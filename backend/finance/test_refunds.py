"""
Phase 3.5.6. Tests for Refunds + Clawbacks -- the Refund model's own
constraints, finance/services.py's refund validation/Razorpay-integration/
clawback logic, the refund.* webhook extension to RazorpayWebhookView, the
admin-only refund APIs, the customer-facing my-refunds API, and the
payout-interaction/Invoice-immutability/Enrollment-preservation guarantees
the approved Phase 3.5.6 brief required.

Deliberately NOT tested here (out of scope, no code exists for any of
these, per the brief's explicit DO NOT IMPLEMENT list): Razorpay Route,
bank/UPI detail storage, automatic instructor payouts, payout provider
integration, tax/GST engine, credit-note tax engine, automatic access
revocation, multi-instructor revenue splitting, coupons, gateway fee
accounting, historical financial backfill, automated reconciliation job.

True PostgreSQL-grade concurrent row-locking is NOT verified here -- this
project's test backend is SQLite, where select_for_update() is a
documented no-op. Concurrency protection is verified via SEQUENTIAL
double-call tests (matching this codebase's established practice, e.g.
finance/tests.py's CreatePayoutBatchServiceTests), which confirm the
validate-then-reserve logic is correct, not that PostgreSQL's real locking
is exercised.
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, CourseInstructor, Enrollment
from orders.models import Order, OrderItem, Purchase, Subscription, SubscriptionPayment, SubscriptionPlan
from orders.services import fulfill_order, fulfill_purchase
from orders.tests import WEBHOOK_TEST_SECRET, sign_webhook_payload

from .admin import RefundAdmin
from .models import Invoice, LedgerEntry, Payout, Refund
from .services import (
    RefundGatewayError, approve_payout, confirm_refund_by_razorpay_id, create_and_process_refund,
    create_payout_batch, get_refund_eligibility, get_refundable_remaining, get_refunded_amount,
)

User = get_user_model()


def _make_course_instructor(course, user, *, is_primary=True, commission_rate=Decimal('30.00')):
    return CourseInstructor.objects.create(
        course=course, user=user, role=CourseInstructor.InstructorRole.TEACHER,
        is_primary=is_primary, commission_rate=commission_rate,
    )


def _paid_purchase(student, course, amount, *, razorpay_payment_id='pay_refund_test_1'):
    purchase = Purchase.objects.create(
        user=student, course=course, amount=amount, status=Purchase.Status.SUCCESS,
        razorpay_order_id='order_refund_test_1', razorpay_payment_id=razorpay_payment_id,
    )
    fulfill_purchase(purchase, previous_status='PENDING')
    return purchase


class RefundModelConstraintTests(TestCase):
    """Refund model's own DB-level constraints."""

    def setUp(self):
        self.student = User.objects.create_user(username='refund_ct_student', password='password123')
        self.admin = User.objects.create_user(username='refund_ct_admin', password='password123', is_staff=True)
        self.course = Course.objects.create(title='Refund Constraint Course', description='x', price=Decimal('500.00'))
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_ct_1',
        )

    def test_exactly_one_source_required(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Refund.objects.create(
                    customer=self.student, amount=Decimal('100.00'), requested_by=self.admin,
                )  # no source at all

    def test_two_sources_rejected(self):
        order = Order.objects.create(
            user=self.student, status=Order.Status.PAID, subtotal=Decimal('1'), total_amount=Decimal('1'), currency='INR',
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Refund.objects.create(
                    customer=self.student, purchase=self.purchase, order=order,
                    amount=Decimal('100.00'), requested_by=self.admin,
                )

    def test_amount_must_be_positive(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Refund.objects.create(
                    customer=self.student, purchase=self.purchase, amount=Decimal('0.00'), requested_by=self.admin,
                )

    def test_multiple_refunds_allowed_against_same_purchase(self):
        # Unlike LedgerEntry/Invoice, Refund has NO uniqueness constraint
        # on its source field -- multiple partial refunds are legitimate.
        Refund.objects.create(customer=self.student, purchase=self.purchase, amount=Decimal('100.00'), requested_by=self.admin)
        Refund.objects.create(customer=self.student, purchase=self.purchase, amount=Decimal('100.00'), requested_by=self.admin)
        self.assertEqual(Refund.objects.filter(purchase=self.purchase).count(), 2)

    def test_razorpay_refund_id_unique(self):
        Refund.objects.create(
            customer=self.student, purchase=self.purchase, amount=Decimal('100.00'),
            requested_by=self.admin, razorpay_refund_id='rfnd_dup_1',
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Refund.objects.create(
                    customer=self.student, purchase=self.purchase, amount=Decimal('50.00'),
                    requested_by=self.admin, razorpay_refund_id='rfnd_dup_1',
                )


class RefundValidationServiceTests(TestCase):
    """create_and_process_refund's validation logic -- amount limits,
    partial/full refunds, unsupported source states, multi-item order
    partial rejection."""

    def setUp(self):
        self.student = User.objects.create_user(username='refund_val_student', password='password123')
        self.admin = User.objects.create_user(username='refund_val_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='refund_val_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Refund Val Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher)

    def _mock_refund_response(self, mock_client, *, refund_id='rfnd_val_1', resp_status='processed'):
        mock_client.payment.refund.return_value = {"id": refund_id, "status": resp_status, "amount": 0}

    @patch('orders.views.client')
    def test_partial_refund_allowed(self, mock_client):
        self._mock_refund_response(mock_client)
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'))
        refund = create_and_process_refund(purchase=purchase, amount=Decimal('200.00'), reason='partial', requested_by=self.admin)
        self.assertEqual(refund.status, Refund.Status.SUCCESS)
        self.assertEqual(get_refundable_remaining(purchase=purchase), Decimal('300.00'))
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.SUCCESS)  # not yet fully refunded

    @patch('orders.views.client')
    def test_multiple_partial_refunds_sum_correctly(self, mock_client):
        self._mock_refund_response(mock_client, refund_id='rfnd_val_2a')
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'))
        create_and_process_refund(purchase=purchase, amount=Decimal('150.00'), requested_by=self.admin)
        self._mock_refund_response(mock_client, refund_id='rfnd_val_2b')
        create_and_process_refund(purchase=purchase, amount=Decimal('150.00'), requested_by=self.admin)
        self.assertEqual(get_refunded_amount(purchase=purchase), Decimal('300.00'))
        self.assertEqual(get_refundable_remaining(purchase=purchase), Decimal('200.00'))

    @patch('orders.views.client')
    def test_fully_refunded_transaction_sets_status_refunded(self, mock_client):
        self._mock_refund_response(mock_client)
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'))
        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.REFUNDED)

    @patch('orders.views.client')
    def test_amount_exceeding_remainder_rejected(self, mock_client):
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'))
        with self.assertRaises(ValueError):
            create_and_process_refund(purchase=purchase, amount=Decimal('501.00'), requested_by=self.admin)
        mock_client.payment.refund.assert_not_called()

    @patch('orders.views.client')
    def test_second_refund_request_exceeding_remainder_rejected(self, mock_client):
        self._mock_refund_response(mock_client, refund_id='rfnd_val_3a')
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'))
        create_and_process_refund(purchase=purchase, amount=Decimal('400.00'), requested_by=self.admin)
        with self.assertRaises(ValueError):
            create_and_process_refund(purchase=purchase, amount=Decimal('200.00'), requested_by=self.admin)
        self.assertEqual(get_refunded_amount(purchase=purchase), Decimal('400.00'))

    @patch('orders.views.client')
    def test_pending_purchase_rejected(self, mock_client):
        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.PENDING,
            razorpay_payment_id='pay_pending_1',
        )
        with self.assertRaises(ValueError):
            create_and_process_refund(purchase=purchase, amount=Decimal('100.00'), requested_by=self.admin)
        mock_client.payment.refund.assert_not_called()

    @patch('orders.views.client')
    def test_unpaid_order_rejected(self, mock_client):
        order = Order.objects.create(
            user=self.student, status=Order.Status.PENDING, subtotal=Decimal('500.00'),
            total_amount=Decimal('500.00'), currency='INR',
        )
        with self.assertRaises(ValueError):
            create_and_process_refund(order=order, amount=Decimal('100.00'), requested_by=self.admin)

    def test_zero_amount_rejected(self):
        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_zero_1',
        )
        with self.assertRaises(ValueError):
            create_and_process_refund(purchase=purchase, amount=Decimal('0.00'), requested_by=self.admin)

    def test_no_source_rejected(self):
        with self.assertRaises(ValueError):
            create_and_process_refund(amount=Decimal('100.00'), requested_by=self.admin)

    def test_purchase_without_razorpay_payment_id_rejected(self):
        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
        )  # no razorpay_payment_id
        with self.assertRaises(ValueError):
            create_and_process_refund(purchase=purchase, amount=Decimal('100.00'), requested_by=self.admin)

    @patch('orders.views.client')
    def test_multi_item_order_partial_refund_rejected(self, mock_client):
        course2 = Course.objects.create(title='Multi Item Course 2', description='x', price=Decimal('300.00'))
        _make_course_instructor(course2, self.teacher)
        order = Order.objects.create(
            user=self.student, status=Order.Status.PAID, subtotal=Decimal('800.00'),
            total_amount=Decimal('800.00'), currency='INR', razorpay_payment_id='pay_multi_1',
        )
        OrderItem.objects.create(order=order, item_type=OrderItem.ItemType.COURSE, course=self.course, title_snapshot=self.course.title, unit_price=Decimal('500.00'), total_price=Decimal('500.00'))
        OrderItem.objects.create(order=order, item_type=OrderItem.ItemType.COURSE, course=course2, title_snapshot=course2.title, unit_price=Decimal('300.00'), total_price=Decimal('300.00'))
        with self.assertRaises(ValueError):
            create_and_process_refund(order=order, amount=Decimal('300.00'), requested_by=self.admin)
        mock_client.payment.refund.assert_not_called()

    @patch('orders.views.client')
    def test_multi_item_order_full_refund_allowed(self, mock_client):
        self._mock_refund_response(mock_client)
        course2 = Course.objects.create(title='Multi Item Course 3', description='x', price=Decimal('300.00'))
        _make_course_instructor(course2, self.teacher)
        order = Order.objects.create(
            user=self.student, status=Order.Status.PAID, subtotal=Decimal('800.00'),
            total_amount=Decimal('800.00'), currency='INR', razorpay_payment_id='pay_multi_2',
        )
        OrderItem.objects.create(order=order, item_type=OrderItem.ItemType.COURSE, course=self.course, title_snapshot=self.course.title, unit_price=Decimal('500.00'), total_price=Decimal('500.00'))
        OrderItem.objects.create(order=order, item_type=OrderItem.ItemType.COURSE, course=course2, title_snapshot=course2.title, unit_price=Decimal('300.00'), total_price=Decimal('300.00'))
        refund = create_and_process_refund(order=order, amount=Decimal('800.00'), requested_by=self.admin)
        self.assertEqual(refund.status, Refund.Status.SUCCESS)


class RefundConcurrencyTests(TestCase):
    """Sequential double-call verification of the reservation logic --
    real PostgreSQL row-locking is NOT exercised on SQLite (this project's
    test backend); see this module's own header docstring."""

    def setUp(self):
        self.student = User.objects.create_user(username='refund_conc_student', password='password123')
        self.admin = User.objects.create_user(username='refund_conc_admin', password='password123', is_staff=True)
        self.course = Course.objects.create(title='Refund Concurrency Course', description='x', price=Decimal('500.00'))

    @patch('orders.views.client')
    def test_two_sequential_refund_requests_cannot_exceed_total(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_conc_1", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_conc_1')

        create_and_process_refund(purchase=purchase, amount=Decimal('300.00'), requested_by=self.admin)
        with self.assertRaises(ValueError):
            create_and_process_refund(purchase=purchase, amount=Decimal('300.00'), requested_by=self.admin)

        self.assertEqual(get_refunded_amount(purchase=purchase), Decimal('300.00'))


class RefundRazorpayIntegrationTests(TestCase):
    """create_and_process_refund's Razorpay API-call handling: success,
    pending/processing, and failure paths."""

    def setUp(self):
        self.student = User.objects.create_user(username='refund_rzp_student', password='password123')
        self.admin = User.objects.create_user(username='refund_rzp_admin', password='password123', is_staff=True)
        self.course = Course.objects.create(title='Refund Razorpay Course', description='x', price=Decimal('500.00'))

    @patch('orders.views.client')
    def test_processed_response_marks_refund_success_immediately(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_rzp_1", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_rzp_1')
        refund = create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)
        self.assertEqual(refund.status, Refund.Status.SUCCESS)
        self.assertEqual(refund.razorpay_refund_id, 'rfnd_rzp_1')
        self.assertIsNotNone(refund.processed_at)

    @patch('orders.views.client')
    def test_pending_response_leaves_refund_processing(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_rzp_2", "status": "pending"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_rzp_2')
        refund = create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)
        self.assertEqual(refund.status, Refund.Status.PROCESSING)
        self.assertEqual(refund.razorpay_refund_id, 'rfnd_rzp_2')
        self.assertIsNone(refund.processed_at)
        # No clawback and no Purchase status change until refund.processed confirms it.
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.SUCCESS)

    @patch('orders.views.client')
    def test_razorpay_call_failure_marks_refund_failed_and_raises_gateway_error(self, mock_client):
        mock_client.payment.refund.side_effect = Exception("Razorpay is down")
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_rzp_3')
        with self.assertRaises(RefundGatewayError):
            create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)
        refund = Refund.objects.get(purchase=purchase)
        self.assertEqual(refund.status, Refund.Status.FAILED)
        self.assertIn('Razorpay is down', refund.failure_reason)
        # The failed attempt's amount must not stay reserved forever against future refunds.
        self.assertEqual(get_refunded_amount(purchase=purchase), Decimal('0.00'))


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
class RefundWebhookTests(APITestCase):
    """refund.processed/refund.failed/refund.created handling in
    RazorpayWebhookView, driven through the real signature-verified
    endpoint."""

    def setUp(self):
        self.student = User.objects.create_user(username='refund_wh_student', password='password123')
        self.admin = User.objects.create_user(username='refund_wh_admin', password='password123', is_staff=True)
        self.course = Course.objects.create(title='Refund Webhook Course', description='x', price=Decimal('500.00'))
        self.url = reverse('razorpay-webhook')

    def _post_refund_event(self, event_type, event_id, *, refund_id, payment_id, amount=50000, refund_status='processed'):
        payload = {
            "event": event_type,
            "payload": {
                "refund": {"entity": {
                    "id": refund_id, "entity": "refund", "amount": amount, "currency": "INR",
                    "payment_id": payment_id, "status": refund_status,
                }},
            },
        }
        body, signature = sign_webhook_payload(payload)
        return self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )

    @patch('orders.views.client')
    def test_refund_processed_webhook_confirms_processing_refund(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_wh_1", "status": "pending"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_wh_1')
        refund = create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)
        self.assertEqual(refund.status, Refund.Status.PROCESSING)

        response = self._post_refund_event('refund.processed', 'evt_refund_wh_1', refund_id='rfnd_wh_1', payment_id='pay_wh_1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        refund.refresh_from_db()
        self.assertEqual(refund.status, Refund.Status.SUCCESS)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, Purchase.Status.REFUNDED)

    @patch('orders.views.client')
    def test_repeated_refund_processed_webhook_does_not_double_clawback(self, mock_client):
        teacher = User.objects.create_user(username='refund_wh_teacher', password='password123', is_teacher=True)
        instructor = _make_course_instructor(self.course, teacher)
        mock_client.payment.refund.return_value = {"id": "rfnd_wh_2", "status": "pending"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_wh_2')
        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)

        self._post_refund_event('refund.processed', 'evt_refund_wh_2a', refund_id='rfnd_wh_2', payment_id='pay_wh_2')
        # A second, DIFFERENT delivery (different event_id, same underlying
        # refund -- Razorpay's own at-least-once retry behavior) for the
        # SAME razorpay_refund_id.
        response2 = self._post_refund_event('refund.processed', 'evt_refund_wh_2b', refund_id='rfnd_wh_2', payment_id='pay_wh_2')
        self.assertEqual(response2.status_code, status.HTTP_200_OK)

        self.assertEqual(LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.CLAWBACK).count(), 1)

    def test_duplicate_webhook_event_id_is_idempotent(self):
        Refund.objects.create(
            customer=self.student, purchase=Purchase.objects.create(
                user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
                razorpay_payment_id='pay_wh_3',
            ),
            amount=Decimal('500.00'), requested_by=self.admin, status=Refund.Status.PROCESSING,
            razorpay_payment_id='pay_wh_3', razorpay_refund_id='rfnd_wh_3',
        )
        r1 = self._post_refund_event('refund.processed', 'evt_refund_wh_3', refund_id='rfnd_wh_3', payment_id='pay_wh_3')
        r2 = self._post_refund_event('refund.processed', 'evt_refund_wh_3', refund_id='rfnd_wh_3', payment_id='pay_wh_3')
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(Refund.objects.filter(razorpay_refund_id='rfnd_wh_3').count(), 1)

    def test_refund_failed_webhook_marks_refund_failed(self):
        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_wh_4',
        )
        Refund.objects.create(
            customer=self.student, purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin,
            status=Refund.Status.PROCESSING, razorpay_payment_id='pay_wh_4', razorpay_refund_id='rfnd_wh_4',
        )
        response = self._post_refund_event('refund.failed', 'evt_refund_wh_4', refund_id='rfnd_wh_4', payment_id='pay_wh_4')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        refund = Refund.objects.get(razorpay_refund_id='rfnd_wh_4')
        self.assertEqual(refund.status, Refund.Status.FAILED)

    def test_unmatched_refund_id_recorded_as_failed_webhook_event_not_fabricated(self):
        from orders.models import WebhookEvent
        response = self._post_refund_event('refund.processed', 'evt_refund_wh_5', refund_id='rfnd_never_seen', payment_id='pay_never_seen')
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # still 200 -- durably recorded, not retried forever
        event = WebhookEvent.objects.get(razorpay_event_id='evt_refund_wh_5')
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertEqual(Refund.objects.filter(razorpay_refund_id='rfnd_never_seen').count(), 0)  # never fabricated


class ClawbackTests(TestCase):
    """Clawback LedgerEntry creation, amount calculation, and the
    immutability of the original EARNING entry."""

    def setUp(self):
        self.student = User.objects.create_user(username='clawback_student', password='password123')
        self.admin = User.objects.create_user(username='clawback_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='clawback_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Clawback Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    @patch('orders.views.client')
    def test_full_refund_creates_clawback_matching_original_net_amount(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_cb_1", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_cb_1')
        original = LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.EARNING)

        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)

        clawback = LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.CLAWBACK)
        self.assertEqual(clawback.related_entry_id, original.id)
        self.assertEqual(clawback.net_amount, -original.net_amount)
        self.assertEqual(clawback.gross_amount, -original.gross_amount)
        self.assertEqual(clawback.commission_amount, -original.commission_amount)
        self.assertEqual(clawback.course_instructor_id, original.course_instructor_id)

    @patch('orders.views.client')
    def test_partial_refund_creates_proportional_clawback(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_cb_2", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_cb_2')
        original = LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.EARNING)
        self.assertEqual(original.gross_amount, Decimal('500.00'))
        self.assertEqual(original.commission_amount, Decimal('150.00'))  # 30% of 500
        self.assertEqual(original.net_amount, Decimal('350.00'))

        create_and_process_refund(purchase=purchase, amount=Decimal('100.00'), requested_by=self.admin)  # 20% refunded

        clawback = LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.CLAWBACK)
        self.assertEqual(clawback.gross_amount, Decimal('-100.00'))       # 20% of 500
        self.assertEqual(clawback.commission_amount, Decimal('-30.00'))   # 20% of 150
        self.assertEqual(clawback.net_amount, Decimal('-70.00'))          # 20% of 350

    @patch('orders.views.client')
    def test_multiple_partial_refunds_clawback_sums_without_rounding_drift(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_cb_3a", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_cb_3')
        original = LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.EARNING)

        create_and_process_refund(purchase=purchase, amount=Decimal('333.33'), requested_by=self.admin)
        mock_client.payment.refund.return_value = {"id": "rfnd_cb_3b", "status": "processed"}
        create_and_process_refund(purchase=purchase, amount=Decimal('166.67'), requested_by=self.admin)  # brings total to exactly 500.00

        total_clawback_net = LedgerEntry.objects.filter(
            purchase=purchase, entry_type=LedgerEntry.EntryType.CLAWBACK,
        ).count()
        self.assertEqual(total_clawback_net, 2)
        summed_net = sum(
            (e.net_amount for e in LedgerEntry.objects.filter(purchase=purchase, entry_type=LedgerEntry.EntryType.CLAWBACK)),
            Decimal('0'),
        )
        self.assertEqual(summed_net, -original.net_amount)  # exact, no drift

    @patch('orders.views.client')
    def test_original_earning_entry_remains_unchanged(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_cb_4", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_cb_4')
        original = LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.EARNING)
        original_snapshot = (original.gross_amount, original.commission_amount, original.net_amount, original.commission_rate)

        create_and_process_refund(purchase=purchase, amount=Decimal('250.00'), requested_by=self.admin)

        original.refresh_from_db()
        self.assertEqual(
            (original.gross_amount, original.commission_amount, original.net_amount, original.commission_rate),
            original_snapshot,
        )

    @patch('orders.views.client')
    def test_no_eligible_instructor_is_safe_noop_for_clawback(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_cb_5", "status": "processed"}
        unowned_course = Course.objects.create(title='No Instructor Refund Course', description='x', price=Decimal('200.00'))
        purchase = Purchase.objects.create(
            user=self.student, course=unowned_course, amount=Decimal('200.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_cb_5',
        )
        fulfill_purchase(purchase, previous_status='PENDING')
        self.assertEqual(LedgerEntry.objects.filter(purchase=purchase).count(), 0)

        refund = create_and_process_refund(purchase=purchase, amount=Decimal('200.00'), requested_by=self.admin)
        self.assertEqual(refund.status, Refund.Status.SUCCESS)  # refund still succeeds
        self.assertEqual(LedgerEntry.objects.filter(purchase=purchase, entry_type=LedgerEntry.EntryType.CLAWBACK).count(), 0)

    def test_no_double_clawback_from_repeated_confirm_call(self):
        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_cb_6',
        )
        fulfill_purchase(purchase, previous_status='PENDING')
        refund = Refund.objects.create(
            customer=self.student, purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin,
            status=Refund.Status.PROCESSING, razorpay_payment_id='pay_cb_6', razorpay_refund_id='rfnd_cb_6',
        )
        confirm_refund_by_razorpay_id('rfnd_cb_6')
        confirm_refund_by_razorpay_id('rfnd_cb_6')  # repeated confirmation -- must not double-clawback
        self.assertEqual(LedgerEntry.objects.filter(purchase=purchase, entry_type=LedgerEntry.EntryType.CLAWBACK).count(), 1)

    @patch('orders.views.client')
    def test_multi_item_full_order_refund_claws_back_every_item(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_cb_7", "status": "processed"}
        admin = self.admin
        course2 = Course.objects.create(title='Clawback Multi Course 2', description='x', price=Decimal('300.00'))
        teacher2 = User.objects.create_user(username='clawback_teacher2', password='password123', is_teacher=True)
        _make_course_instructor(course2, teacher2, commission_rate=Decimal('20.00'))

        order = Order.objects.create(
            user=self.student, status=Order.Status.PAID, subtotal=Decimal('800.00'),
            total_amount=Decimal('800.00'), currency='INR', razorpay_payment_id='pay_cb_7',
        )
        item1 = OrderItem.objects.create(order=order, item_type=OrderItem.ItemType.COURSE, course=self.course, title_snapshot=self.course.title, unit_price=Decimal('500.00'), total_price=Decimal('500.00'))
        item2 = OrderItem.objects.create(order=order, item_type=OrderItem.ItemType.COURSE, course=course2, title_snapshot=course2.title, unit_price=Decimal('300.00'), total_price=Decimal('300.00'))
        fulfill_order(order, previous_status='PENDING')

        create_and_process_refund(order=order, amount=Decimal('800.00'), requested_by=admin)

        self.assertEqual(LedgerEntry.objects.filter(order_item=item1, entry_type=LedgerEntry.EntryType.CLAWBACK).count(), 1)
        self.assertEqual(LedgerEntry.objects.filter(order_item=item2, entry_type=LedgerEntry.EntryType.CLAWBACK).count(), 1)
        cb1 = LedgerEntry.objects.get(order_item=item1, entry_type=LedgerEntry.EntryType.CLAWBACK)
        cb2 = LedgerEntry.objects.get(order_item=item2, entry_type=LedgerEntry.EntryType.CLAWBACK)
        self.assertEqual(cb1.net_amount, Decimal('-350.00'))  # 500 - 30% commission
        self.assertEqual(cb2.net_amount, Decimal('-240.00'))  # 300 - 20% commission


class PayoutInteractionTests(TestCase):
    """CRITICAL per the approved brief: a refund must never silently claw
    back money from an already-batched/approved/paid Payout."""

    def setUp(self):
        self.student = User.objects.create_user(username='payout_int_student', password='password123')
        self.admin = User.objects.create_user(username='payout_int_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='payout_int_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Payout Interaction Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    def _setup_purchase_and_earning(self, payment_id):
        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id=payment_id,
        )
        fulfill_purchase(purchase, previous_status='PENDING')
        return purchase, LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.EARNING)

    @patch('orders.views.client')
    def test_refund_after_draft_payout_does_not_alter_payout_totals(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_payout_1", "status": "processed"}
        purchase, entry = self._setup_purchase_and_earning('pay_payout_1')
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[entry.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        original_totals = (payout.gross_amount, payout.commission_amount, payout.net_amount)

        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)

        payout.refresh_from_db()
        self.assertEqual((payout.gross_amount, payout.commission_amount, payout.net_amount), original_totals)
        clawback = LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.CLAWBACK)
        self.assertIsNone(clawback.payout_id)  # standalone, not retroactively added to the DRAFT payout

    @patch('orders.views.client')
    def test_refund_after_approved_payout_does_not_alter_payout_totals(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_payout_2", "status": "processed"}
        purchase, entry = self._setup_purchase_and_earning('pay_payout_2')
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[entry.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        payout = approve_payout(payout, approved_by=self.admin)
        self.assertEqual(payout.status, Payout.Status.APPROVED)
        original_totals = (payout.gross_amount, payout.commission_amount, payout.net_amount)

        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.APPROVED)  # not silently reverted
        self.assertEqual((payout.gross_amount, payout.commission_amount, payout.net_amount), original_totals)

    @patch('orders.views.client')
    def test_refund_after_paid_payout_creates_standalone_debt_record_not_silent_clawback(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_payout_3", "status": "processed"}
        purchase, entry = self._setup_purchase_and_earning('pay_payout_3')
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[entry.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        # No code path in this codebase ever moves a Payout to PAID (see
        # approve_payout's own docstring) -- simulated directly here, the
        # same way finance/tests.py's own edge-case fixtures bypass the
        # service layer to construct a state no real code path produces yet.
        payout.status = Payout.Status.PAID
        payout.paid_at = payout.approved_at
        payout.save(update_fields=['status', 'paid_at'])
        original_totals = (payout.gross_amount, payout.commission_amount, payout.net_amount)

        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)

        payout.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.PAID)
        self.assertEqual((payout.gross_amount, payout.commission_amount, payout.net_amount), original_totals)  # never silently clawed back
        clawback = LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.CLAWBACK)
        self.assertIsNone(clawback.payout_id)  # the "outstanding negative debt record" -- unbatched, net_amount negative
        self.assertLess(clawback.net_amount, Decimal('0'))


class InvoiceImmutabilityTests(TestCase):
    """A refund must never mutate the original Invoice."""

    def setUp(self):
        self.student = User.objects.create_user(username='invoice_immut_student', password='password123')
        self.admin = User.objects.create_user(username='invoice_immut_admin', password='password123', is_staff=True)
        self.course = Course.objects.create(title='Invoice Immutability Course', description='x', price=Decimal('500.00'))

    @patch('orders.views.client')
    def test_original_invoice_unchanged_after_refund(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_inv_1", "status": "processed"}
        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_inv_1',
        )
        fulfill_purchase(purchase, previous_status='PENDING')
        invoice = Invoice.objects.get(purchase=purchase)
        original_snapshot = (invoice.amount, invoice.currency, invoice.status, invoice.invoice_number)

        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)

        invoice.refresh_from_db()
        self.assertEqual((invoice.amount, invoice.currency, invoice.status, invoice.invoice_number), original_snapshot)
        self.assertEqual(Invoice.objects.filter(purchase=purchase).count(), 1)  # no second/credit-note invoice created either


class EnrollmentPreservationTests(TestCase):
    """A refund must never automatically revoke course access -- refund
    and access revocation are separate business decisions per the brief."""

    def setUp(self):
        self.student = User.objects.create_user(username='enroll_preserve_student', password='password123')
        self.admin = User.objects.create_user(username='enroll_preserve_admin', password='password123', is_staff=True)
        self.course = Course.objects.create(title='Enrollment Preservation Course', description='x', price=Decimal('500.00'))

    @patch('orders.views.client')
    def test_enrollment_preserved_after_full_refund(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_enroll_1", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_enroll_1')
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course).exists())

        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)

        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course).exists())  # untouched


class SubscriptionPaymentRefundTests(TestCase):
    """SubscriptionPayment refund behavior -- the third supported source
    type."""

    def setUp(self):
        self.student = User.objects.create_user(username='sub_refund_student', password='password123')
        self.admin = User.objects.create_user(username='sub_refund_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='sub_refund_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Sub Refund Course', description='x', price=Decimal('999.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('25.00'))
        self.plan = SubscriptionPlan.objects.create(
            name='Refund Test Plan', billing_interval='MONTHLY', price='999.00', razorpay_plan_id='plan_refund_test',
        )
        self.plan.courses.add(self.course)
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id='sub_refund_test_1', razorpay_plan_id='plan_refund_test',
        )
        self.subscription_payment = SubscriptionPayment.objects.create(
            subscription=self.subscription, razorpay_payment_id='pay_sub_refund_1',
            razorpay_subscription_id='sub_refund_test_1', amount=Decimal('999.00'), currency='INR',
            status=SubscriptionPayment.Status.SUCCESS,
        )
        from .services import create_earning_entry_for_subscription_payment
        create_earning_entry_for_subscription_payment(self.subscription_payment)

    @patch('orders.views.client')
    def test_subscription_payment_full_refund_supported(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_sub_1", "status": "processed"}
        refund = create_and_process_refund(
            subscription_payment=self.subscription_payment, amount=Decimal('999.00'), requested_by=self.admin,
        )
        self.assertEqual(refund.status, Refund.Status.SUCCESS)
        self.subscription_payment.refresh_from_db()
        self.assertEqual(self.subscription_payment.status, SubscriptionPayment.Status.REFUNDED)
        self.assertTrue(LedgerEntry.objects.filter(subscription_payment=self.subscription_payment, entry_type=LedgerEntry.EntryType.CLAWBACK).exists())
        # The parent Subscription itself is untouched by a payment refund.
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)

    def test_created_subscription_payment_not_refundable(self):
        never_charged = SubscriptionPayment.objects.create(
            subscription=self.subscription, razorpay_payment_id='pay_sub_never_charged',
            amount=Decimal('999.00'), currency='INR', status=SubscriptionPayment.Status.CREATED,
        )
        with self.assertRaises(ValueError):
            create_and_process_refund(subscription_payment=never_charged, amount=Decimal('999.00'), requested_by=self.admin)


class RefundEligibilityServiceTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='elig_svc_student', password='password123')
        self.course = Course.objects.create(title='Eligibility Service Course', description='x', price=Decimal('500.00'))

    def test_eligibility_reports_remaining_and_supports_partial(self):
        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_elig_1',
        )
        eligibility = get_refund_eligibility(purchase=purchase)
        self.assertEqual(eligibility['remaining_refundable'], Decimal('500.00'))
        self.assertTrue(eligibility['supports_partial_refund'])

    def test_eligibility_raises_for_unrefundable_source(self):
        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.FAILED,
        )
        with self.assertRaises(ValueError):
            get_refund_eligibility(purchase=purchase)


class AdminReadOnlyRefundTests(TestCase):
    def setUp(self):
        self.site = AdminSite()

    def test_refund_admin_add_disabled(self):
        self.assertFalse(RefundAdmin(Refund, self.site).has_add_permission(None))

    def test_refund_admin_delete_disabled(self):
        self.assertFalse(RefundAdmin(Refund, self.site).has_delete_permission(None))


class RefundAPIPermissionsTests(APITestCase):
    """Admin-only creation/inspection; customer-scoped own-history."""

    def setUp(self):
        self.student = User.objects.create_user(username='refund_perm_student', password='password123')
        self.other_student = User.objects.create_user(username='refund_perm_other_student', password='password123')
        self.teacher = User.objects.create_user(username='refund_perm_teacher', password='password123', is_teacher=True)
        self.admin = User.objects.create_user(username='refund_perm_admin', password='password123', is_staff=True)
        self.course = Course.objects.create(title='Refund Perm Course', description='x', price=Decimal('500.00'))
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_perm_1',
        )
        self.refund = Refund.objects.create(
            customer=self.student, purchase=self.purchase, amount=Decimal('100.00'),
            requested_by=self.admin, status=Refund.Status.SUCCESS,
        )
        self.create_url = reverse('finance-refund-create')
        self.list_url = reverse('finance-admin-refunds')
        self.my_url = reverse('finance-my-refunds')

    def _create_payload(self):
        return {"purchase_id": self.purchase.id, "amount": "50.00", "reason": "test"}

    def test_unauthenticated_denied(self):
        response = self.client.post(self.create_url, self._create_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_student_denied(self):
        self.client.force_authenticate(self.student)
        response = self.client.post(self.create_url, self._create_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_denied(self):
        self.client.force_authenticate(self.teacher)
        response = self.client.post(self.create_url, self._create_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch('orders.views.client')
    def test_admin_allowed(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_api_1", "status": "processed"}
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.create_url, self._create_payload(), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_student_denied_admin_refund_list(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_my_refunds_scoped_to_own_customer(self):
        Refund.objects.create(
            customer=self.other_student, purchase=Purchase.objects.create(
                user=self.other_student, course=self.course, amount=Decimal('200.00'),
                status=Purchase.Status.SUCCESS, razorpay_payment_id='pay_perm_2',
            ),
            amount=Decimal('200.00'), requested_by=self.admin, status=Refund.Status.SUCCESS,
        )
        self.client.force_authenticate(self.student)
        response = self.client.get(self.my_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [r['id'] for r in response.data['results']]
        self.assertIn(self.refund.id, ids)
        self.assertEqual(len(ids), 1)

    def test_my_refund_detail_other_users_refund_is_404(self):
        other_refund = Refund.objects.create(
            customer=self.other_student, purchase=Purchase.objects.create(
                user=self.other_student, course=self.course, amount=Decimal('200.00'),
                status=Purchase.Status.SUCCESS, razorpay_payment_id='pay_perm_3',
            ),
            amount=Decimal('200.00'), requested_by=self.admin, status=Refund.Status.SUCCESS,
        )
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('finance-my-refund-detail', kwargs={'pk': other_refund.pk}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_my_refunds_never_exposes_razorpay_ids(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(self.my_url)
        body = str(response.data)
        self.assertNotIn('razorpay', body.lower())

    def test_admin_refund_detail_exposes_razorpay_ids(self):
        self.refund.razorpay_refund_id = 'rfnd_perm_visible_1'
        self.refund.save(update_fields=['razorpay_refund_id'])
        self.client.force_authenticate(self.admin)
        response = self.client.get(reverse('finance-admin-refund-detail', kwargs={'pk': self.refund.pk}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['razorpay_refund_id'], 'rfnd_perm_visible_1')


class RefundEligibilityAPITests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='elig_api_student', password='password123')
        self.admin = User.objects.create_user(username='elig_api_admin', password='password123', is_staff=True)
        self.course = Course.objects.create(title='Eligibility API Course', description='x', price=Decimal('500.00'))
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_elig_api_1',
        )
        self.url = reverse('finance-refund-eligibility')

    def test_admin_can_check_eligibility(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.url, {'purchase_id': self.purchase.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(response.data['remaining_refundable']), Decimal('500.00'))

    def test_student_denied(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(self.url, {'purchase_id': self.purchase.id})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AdminRefundUIWorkflowTests(APITestCase):
    """
    Phase Next -- admin refund UI (frontend/src/app/admin/payments/page.tsx).
    No new backend code was added for that UI; these tests exist purely to
    lock in the exact eligibility -> refund -> eligibility-again sequence
    that UI performs against the ALREADY-EXISTING API
    (RefundEligibilityView + CreateRefundView), since nothing in this
    file's existing tests exercises that specific call sequence
    end-to-end the way the new frontend actually does.
    """
    def setUp(self):
        self.admin = User.objects.create_user(username='refund_ui_admin', password='password123', is_staff=True)
        self.student = User.objects.create_user(username='refund_ui_student', password='password123')
        self.course = Course.objects.create(title='Refund UI Course', description='x', price=Decimal('1000.00'))
        self.purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_ui_1')
        self.eligibility_url = reverse('finance-refund-eligibility')
        self.create_url = reverse('finance-refund-create')

    # 1. Admin can see refund action for eligible payment (the data the
    # UI's button-visibility decision is gated on).
    def test_eligible_purchase_shows_full_remaining_before_any_refund(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.eligibility_url, {'purchase_id': self.purchase.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(response.data['remaining_refundable']), Decimal('1000.00'))
        self.assertEqual(Decimal(response.data['already_refunded']), Decimal('0'))
        self.assertTrue(response.data['supports_partial_refund'])

    @patch('orders.views.client')
    def test_full_refund_flips_purchase_to_refunded_and_eligibility_then_400s(self, mock_client):
        """The exact sequence the UI performs on a successful full refund:
        create -> re-fetch eligibility. Discovered (not assumed) while
        building the admin refund UI: a full refund immediately flips the
        Purchase to REFUNDED (_update_source_status_if_fully_refunded),
        and _resolve_refund_source then correctly refuses that source
        entirely on the next eligibility call ("not in a refundable
        state") rather than reporting a clean 0-remaining success payload
        -- there is no window where this purchase is both SUCCESS and
        fully refunded. The new frontend's refund-result success display
        is therefore deliberately shown independently of this follow-up
        eligibility call's own outcome (see admin/payments/page.tsx's
        render-priority comment) -- this test locks in the backend half
        of that reasoning so a future change can't silently invalidate it."""
        mock_client.payment.refund.return_value = {"id": "rfnd_ui_1", "status": "processed"}
        self.client.force_authenticate(self.admin)

        create_response = self.client.post(self.create_url, {
            "purchase_id": self.purchase.id, "amount": "1000.00", "reason": "UI test full refund",
        }, format='json')
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_response.data['status'], 'SUCCESS')
        self.assertEqual(create_response.data['razorpay_refund_id'], 'rfnd_ui_1')

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, Purchase.Status.REFUNDED)

        eligibility_response = self.client.get(self.eligibility_url, {'purchase_id': self.purchase.id})
        self.assertEqual(eligibility_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('not in a refundable state', eligibility_response.data['error'])

    # 3. Already refunded payment cannot be refunded again -- exactly the
    # scenario the UI's disabled/hidden refund button protects against,
    # re-verified here at the API layer the UI can never bypass.
    @patch('orders.views.client')
    def test_second_refund_attempt_after_full_refund_is_rejected(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_ui_2", "status": "processed"}
        self.client.force_authenticate(self.admin)
        self.client.post(self.create_url, {"purchase_id": self.purchase.id, "amount": "1000.00"}, format='json')

        second_attempt = self.client.post(self.create_url, {
            "purchase_id": self.purchase.id, "amount": "1000.00", "reason": "should be rejected",
        }, format='json')
        self.assertEqual(second_attempt.status_code, status.HTTP_400_BAD_REQUEST)
        # Rejected because the Purchase is no longer SUCCESS (flipped to
        # REFUNDED by the first refund), not because the amount "exceeds
        # the remainder" -- _resolve_refund_source's status check fires
        # before get_refunded_amount is even consulted a second time.
        self.assertIn('not in a refundable state', second_attempt.data['error'])
        mock_client.payment.refund.assert_called_once()  # never actually called Razorpay a second time

    # A genuine partial refund (some, but not all, of the balance) is the
    # one case where the purchase legitimately stays SUCCESS and a
    # follow-up eligibility call succeeds with a smaller, non-zero
    # remaining balance -- this is what the UI's "partial refund" branch
    # (re-fetch eligibility rather than assume REFUNDED) actually exercises.
    @patch('orders.views.client')
    def test_partial_refund_leaves_purchase_success_with_reduced_remaining(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_ui_partial_1", "status": "processed"}
        self.client.force_authenticate(self.admin)

        create_response = self.client.post(self.create_url, {
            "purchase_id": self.purchase.id, "amount": "400.00", "reason": "partial",
        }, format='json')
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, Purchase.Status.SUCCESS)

        eligibility_response = self.client.get(self.eligibility_url, {'purchase_id': self.purchase.id})
        self.assertEqual(eligibility_response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(eligibility_response.data['remaining_refundable']), Decimal('600.00'))
        self.assertEqual(Decimal(eligibility_response.data['already_refunded']), Decimal('400.00'))

    # 2. Non-admin cannot initiate refund, at the exact endpoint the new UI calls.
    def test_non_admin_cannot_initiate_refund_via_this_exact_endpoint(self):
        self.client.force_authenticate(self.student)
        response = self.client.post(self.create_url, {"purchase_id": self.purchase.id, "amount": "1000.00"}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    # 6. Backend error is displayed correctly -- confirms the {"error": "..."}
    # shape the UI reads verbatim (response.data.error) is really what
    # CreateRefundView returns for a validation failure.
    def test_backend_error_shape_matches_what_the_ui_displays(self):
        self.client.force_authenticate(self.admin)
        response = self.client.post(self.create_url, {
            "purchase_id": self.purchase.id, "amount": "5000.00",
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)
        self.assertIsInstance(response.data['error'], str)
