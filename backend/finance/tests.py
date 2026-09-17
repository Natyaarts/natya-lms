"""
Phase 3.5.2/3.5.3/3.5.4/3.5.5. Tests for the finance ledger/payout/
invoice foundation and its APIs: LedgerEntry/Payout model constraints,
commission/net calculation, the fulfillment hooks (fulfill_purchase/
fulfill_order/_record_subscription_charge via the real webhook
endpoint), idempotency, the "no eligible instructor" safe no-op, admin
read-only protection, the my-earnings API, the admin ledger-inspection
API, payout eligibility/batching/approval, and customer invoice/receipt
generation + APIs.

Deliberately NOT tested here (out of scope, no code exists for any of
these): coupons, tax, gateway fees, multi-instructor revenue splitting,
historical backfill, real payout processing/money transfer, invoice PDF
generation.

Refunds and clawback processing (Phase 3.5.6) are tested in the sibling
finance/test_refunds.py, not here -- mirroring how orders/ already splits
subscription-specific tests into their own files rather than growing one
single tests.py indefinitely.
"""
import hmac
import hashlib
from datetime import date
from decimal import Decimal

from django.contrib.admin.sites import AdminSite
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, CourseInstructor, Enrollment
from orders.models import Bundle, Order, OrderItem, Purchase, Subscription, SubscriptionPlan
from orders.services import fulfill_order, fulfill_purchase
from orders.test_subscription_webhooks import payment_entity, subscription_entity
from orders.tests import WEBHOOK_TEST_SECRET, sign_webhook_payload

from .admin import LedgerEntryAdmin, PayoutAdmin
from .models import Invoice, LedgerEntry, Payout
from .reconciliation import get_instructor_balance
from .services import approve_payout, create_payout_batch, get_eligible_ledger_entries_queryset, mark_payout_paid

User = get_user_model()


def _make_course_instructor(course, user, *, is_primary=True, commission_rate=Decimal('30.00')):
    return CourseInstructor.objects.create(
        course=course, user=user, role=CourseInstructor.InstructorRole.TEACHER,
        is_primary=is_primary, commission_rate=commission_rate,
    )


class LedgerEntryConstraintTests(TestCase):
    """Model-level constraints: exactly-one-source, idempotency uniqueness."""

    def setUp(self):
        self.student = User.objects.create_user(username='ledger_student', password='password123')
        self.teacher = User.objects.create_user(username='ledger_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Bharatanatyam Basics', description='x', price=Decimal('499.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher)
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS,
        )

    def _entry_kwargs(self, **overrides):
        kwargs = dict(
            course_instructor=self.instructor, entry_type=LedgerEntry.EntryType.EARNING,
            gross_amount=Decimal('499.00'), commission_rate=Decimal('30.00'),
            commission_amount=Decimal('149.70'), net_amount=Decimal('349.30'),
        )
        kwargs.update(overrides)
        return kwargs

    def test_zero_sources_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerEntry.objects.create(**self._entry_kwargs())

    def test_two_sources_rejected(self):
        order = Order.objects.create(user=self.student, subtotal=Decimal('499'), total_amount=Decimal('499'))
        item = OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.COURSE, course=self.course,
            title_snapshot=self.course.title, unit_price=Decimal('499'), total_price=Decimal('499'),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerEntry.objects.create(**self._entry_kwargs(purchase=self.purchase, order_item=item))

    def test_exactly_one_source_purchase_accepted(self):
        entry = LedgerEntry.objects.create(**self._entry_kwargs(purchase=self.purchase))
        self.assertIsNotNone(entry.pk)

    def test_duplicate_earning_entry_for_same_purchase_rejected(self):
        LedgerEntry.objects.create(**self._entry_kwargs(purchase=self.purchase))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LedgerEntry.objects.create(**self._entry_kwargs(purchase=self.purchase))

    def test_course_instructor_protected_from_deletion(self):
        LedgerEntry.objects.create(**self._entry_kwargs(purchase=self.purchase))
        from django.db.models import ProtectedError
        with self.assertRaises(ProtectedError):
            self.instructor.delete()


class LedgerCalculationTests(TestCase):
    """Commission/net calculation correctness and commission-rate snapshot immutability."""

    def setUp(self):
        self.student = User.objects.create_user(username='calc_student', password='password123')
        self.teacher = User.objects.create_user(username='calc_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Kathak Foundations', description='x', price=Decimal('499.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS,
        )

    def test_gross_commission_net_calculation(self):
        fulfill_purchase(self.purchase, previous_status='PENDING')
        entry = LedgerEntry.objects.get(purchase=self.purchase)
        self.assertEqual(entry.gross_amount, Decimal('499.00'))
        self.assertEqual(entry.commission_rate, Decimal('30.00'))
        self.assertEqual(entry.commission_amount, Decimal('149.70'))
        self.assertEqual(entry.net_amount, Decimal('349.30'))
        self.assertEqual(entry.currency, 'INR')
        self.assertEqual(entry.course_instructor_id, self.instructor.id)
        self.assertEqual(entry.entry_type, LedgerEntry.EntryType.EARNING)

    def test_commission_rate_snapshot_survives_later_rate_change(self):
        fulfill_purchase(self.purchase, previous_status='PENDING')
        entry = LedgerEntry.objects.get(purchase=self.purchase)
        self.instructor.commission_rate = Decimal('50.00')
        self.instructor.save()
        entry.refresh_from_db()
        self.assertEqual(entry.commission_rate, Decimal('30.00'))
        self.assertEqual(entry.commission_amount, Decimal('149.70'))


class PurchaseFulfillmentLedgerTests(TestCase):
    """fulfill_purchase() -- the Flow A hook."""

    def setUp(self):
        self.student = User.objects.create_user(username='purchase_student', password='password123')
        self.teacher = User.objects.create_user(username='purchase_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Odissi Essentials', description='x', price=Decimal('499.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher)
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS,
        )

    def test_creates_exactly_one_ledger_entry(self):
        fulfill_purchase(self.purchase, previous_status='PENDING')
        self.assertEqual(LedgerEntry.objects.filter(purchase=self.purchase).count(), 1)

    def test_enrollment_still_created_alongside_ledger_entry(self):
        # Confirms the pre-existing fulfillment contract is unchanged --
        # ledger creation is a pure addition, never a replacement.
        fulfill_purchase(self.purchase, previous_status='PENDING')
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course).exists())

    def test_repeated_fulfillment_does_not_duplicate_ledger_entry(self):
        fulfill_purchase(self.purchase, previous_status='PENDING')
        fulfill_purchase(self.purchase, previous_status='SUCCESS')
        fulfill_purchase(self.purchase, previous_status='SUCCESS')
        self.assertEqual(LedgerEntry.objects.filter(purchase=self.purchase).count(), 1)

    def test_no_ledger_entry_when_purchase_not_success(self):
        pending_purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.PENDING,
        )
        fulfill_purchase(pending_purchase, previous_status='PENDING')
        self.assertEqual(LedgerEntry.objects.filter(purchase=pending_purchase).count(), 0)

    def test_no_eligible_instructor_is_safe_noop(self):
        unowned_course = Course.objects.create(title='No Instructor Course', description='x', price=Decimal('100.00'))
        purchase = Purchase.objects.create(
            user=self.student, course=unowned_course, amount=Decimal('100.00'), status=Purchase.Status.SUCCESS,
        )
        # Must not raise, and must still grant access.
        fulfill_purchase(purchase, previous_status='PENDING')
        self.assertEqual(LedgerEntry.objects.filter(purchase=purchase).count(), 0)
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=unowned_course).exists())

    def test_instructor_without_commission_rate_is_safe_noop(self):
        course2 = Course.objects.create(title='No Rate Course', description='x', price=Decimal('100.00'))
        teacher2 = User.objects.create_user(username='no_rate_teacher', password='password123', is_teacher=True)
        _make_course_instructor(course2, teacher2, commission_rate=None)
        purchase = Purchase.objects.create(
            user=self.student, course=course2, amount=Decimal('100.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(purchase, previous_status='PENDING')
        self.assertEqual(LedgerEntry.objects.filter(purchase=purchase).count(), 0)


class OrderFulfillmentLedgerTests(TestCase):
    """fulfill_order() -- the Flow B hook, both COURSE and BUNDLE items."""

    def setUp(self):
        self.student = User.objects.create_user(username='order_student', password='password123')
        self.teacher = User.objects.create_user(username='order_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Kuchipudi Basics', description='x', price=Decimal('599.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher)

    def _paid_order(self):
        return Order.objects.create(
            user=self.student, status=Order.Status.PAID,
            subtotal=Decimal('599.00'), total_amount=Decimal('599.00'), currency='INR',
        )

    def test_course_item_creates_exactly_one_ledger_entry(self):
        order = self._paid_order()
        item = OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.COURSE, course=self.course,
            title_snapshot=self.course.title, unit_price=Decimal('599.00'), total_price=Decimal('599.00'),
        )
        fulfill_order(order, previous_status='PENDING')
        self.assertEqual(LedgerEntry.objects.filter(order_item=item).count(), 1)
        entry = LedgerEntry.objects.get(order_item=item)
        self.assertEqual(entry.gross_amount, Decimal('599.00'))
        self.assertEqual(entry.currency, 'INR')

    def test_single_course_bundle_item_creates_exactly_one_ledger_entry(self):
        bundle = Bundle.objects.create(name='Solo Bundle', price=Decimal('599.00'))
        bundle.courses.add(self.course)
        order = self._paid_order()
        item = OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.BUNDLE, bundle=bundle,
            title_snapshot=bundle.name, unit_price=Decimal('599.00'), total_price=Decimal('599.00'),
        )
        fulfill_order(order, previous_status='PENDING')
        self.assertEqual(LedgerEntry.objects.filter(order_item=item).count(), 1)

    def test_multi_course_bundle_item_is_safe_noop(self):
        # Multi-instructor/bundle revenue splitting is explicitly deferred
        # (Phase 3.5.1 audit) -- a bundle spanning more than one course
        # must not guess an attribution.
        course2 = Course.objects.create(title='Second Bundle Course', description='x', price=Decimal('300.00'))
        _make_course_instructor(course2, self.teacher)
        bundle = Bundle.objects.create(name='Multi Bundle', price=Decimal('899.00'))
        bundle.courses.add(self.course, course2)
        order = self._paid_order()
        item = OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.BUNDLE, bundle=bundle,
            title_snapshot=bundle.name, unit_price=Decimal('899.00'), total_price=Decimal('899.00'),
        )
        fulfill_order(order, previous_status='PENDING')
        self.assertEqual(LedgerEntry.objects.filter(order_item=item).count(), 0)
        # Enrollment for BOTH bundle courses must still happen regardless.
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course).exists())
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=course2).exists())

    def test_repeated_fulfillment_does_not_duplicate_ledger_entry(self):
        order = self._paid_order()
        item = OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.COURSE, course=self.course,
            title_snapshot=self.course.title, unit_price=Decimal('599.00'), total_price=Decimal('599.00'),
        )
        fulfill_order(order, previous_status='PENDING')
        fulfill_order(order, previous_status='PAID')
        self.assertEqual(LedgerEntry.objects.filter(order_item=item).count(), 1)

    def test_no_ledger_entry_when_order_not_paid(self):
        order = Order.objects.create(
            user=self.student, status=Order.Status.PENDING,
            subtotal=Decimal('599.00'), total_amount=Decimal('599.00'), currency='INR',
        )
        item = OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.COURSE, course=self.course,
            title_snapshot=self.course.title, unit_price=Decimal('599.00'), total_price=Decimal('599.00'),
        )
        fulfill_order(order, previous_status='PENDING')
        self.assertEqual(LedgerEntry.objects.filter(order_item=item).count(), 0)


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
class SubscriptionChargeLedgerTests(APITestCase):
    """_record_subscription_charge() -- the Flow C hook, driven through the
    real, signature-verified webhook endpoint (matches this codebase's
    established subscription-webhook test convention exactly, not a
    private-method call)."""

    def setUp(self):
        from unittest.mock import patch
        self._grace_patcher = patch('orders.tasks.notify_subscription_grace_period_expired.apply_async')
        self._grace_patcher.start()
        self.addCleanup(self._grace_patcher.stop)

        self.student = User.objects.create_user(username='sub_ledger_student', password='password123')
        self.teacher = User.objects.create_user(username='sub_ledger_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Mohiniyattam Intro', description='x', price=Decimal('999.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher)

        self.plan = SubscriptionPlan.objects.create(
            name='Ledger Test Plan', billing_interval='MONTHLY', price='999.00', razorpay_plan_id='plan_ledger_test',
        )
        self.plan.courses.add(self.course)
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id='sub_ledger_test_1', razorpay_plan_id='plan_ledger_test',
        )
        self.url = reverse('razorpay-webhook')

    def _post_charged(self, event_id, payment_status='captured', amount=99900):
        payload = {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": subscription_entity(sub_id='sub_ledger_test_1', status_value='active', plan_id='plan_ledger_test')},
                "payment": {"entity": payment_entity(payment_id=f"pay_{event_id}", amount=amount, status_value=payment_status)},
            },
        }
        body, signature = sign_webhook_payload(payload)
        return self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )

    def test_successful_charge_creates_exactly_one_ledger_entry(self):
        response = self._post_charged('evt_ledger_charged_1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(LedgerEntry.objects.filter(subscription_payment__subscription=self.subscription).count(), 1)
        entry = LedgerEntry.objects.get(subscription_payment__subscription=self.subscription)
        self.assertEqual(entry.gross_amount, Decimal('999.00'))
        self.assertEqual(entry.currency, 'INR')

    def test_failed_charge_creates_no_ledger_entry(self):
        response = self._post_charged('evt_ledger_charged_failed', payment_status='failed')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(LedgerEntry.objects.filter(subscription_payment__subscription=self.subscription).count(), 0)

    def test_duplicate_webhook_event_id_does_not_duplicate_ledger_entry(self):
        self._post_charged('evt_ledger_charged_dup')
        response2 = self._post_charged('evt_ledger_charged_dup')  # same event_id -- WebhookEvent-level dedup
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertEqual(LedgerEntry.objects.filter(subscription_payment__subscription=self.subscription).count(), 1)

    def test_multi_course_plan_is_safe_noop(self):
        course2 = Course.objects.create(title='Second Plan Course', description='x', price=Decimal('0'))
        self.plan.courses.add(course2)
        response = self._post_charged('evt_ledger_multi_course')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(LedgerEntry.objects.filter(subscription_payment__subscription=self.subscription).count(), 0)


class AdminReadOnlyTests(TestCase):
    """LedgerEntry/Payout admin: no add, no delete, all fields read-only."""

    def setUp(self):
        self.site = AdminSite()
        self.ledger_admin = LedgerEntryAdmin(LedgerEntry, self.site)
        self.payout_admin = PayoutAdmin(Payout, self.site)

        self.student = User.objects.create_user(username='admin_test_student', password='password123')
        self.teacher = User.objects.create_user(username='admin_test_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Admin Test Course', description='x', price=Decimal('499.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher)
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(self.purchase, previous_status='PENDING')
        self.entry = LedgerEntry.objects.get(purchase=self.purchase)

    def test_ledger_entry_admin_add_disabled(self):
        self.assertFalse(self.ledger_admin.has_add_permission(request=None))

    def test_ledger_entry_admin_delete_disabled(self):
        self.assertFalse(self.ledger_admin.has_delete_permission(request=None, obj=self.entry))

    def test_ledger_entry_admin_all_fields_readonly(self):
        model_fields = {f.name for f in LedgerEntry._meta.get_fields() if f.concrete}
        model_fields.discard('id')
        self.assertTrue(model_fields.issubset(set(self.ledger_admin.readonly_fields)))

    def test_payout_admin_add_disabled(self):
        self.assertFalse(self.payout_admin.has_add_permission(request=None))

    def test_payout_admin_delete_disabled(self):
        self.assertFalse(self.payout_admin.has_delete_permission(request=None, obj=None))

    def test_payout_admin_all_fields_readonly(self):
        model_fields = {f.name for f in Payout._meta.get_fields() if f.concrete}
        model_fields.discard('id')
        self.assertTrue(model_fields.issubset(set(self.payout_admin.readonly_fields)))


class MyEarningsAPITests(APITestCase):
    """Phase 3.5.3. GET /api/finance/my-earnings/."""

    def setUp(self):
        self.url = reverse('finance-my-earnings')

        self.student = User.objects.create_user(username='earn_student', password='password123')
        self.teacher = User.objects.create_user(username='earn_teacher', password='password123', is_teacher=True)
        self.other_teacher = User.objects.create_user(username='earn_other_teacher', password='password123', is_teacher=True)
        self.mentor = User.objects.create_user(username='earn_mentor', password='password123', is_mentor=True)

        self.course = Course.objects.create(title='Earnings Course', description='x', price=Decimal('499.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

        self.other_course = Course.objects.create(title='Other Teacher Course', description='x', price=Decimal('799.00'))
        self.other_instructor = _make_course_instructor(self.other_course, self.other_teacher, commission_rate=Decimal('40.00'))

        self.mentor_course = Course.objects.create(title='Mentor Course', description='x', price=Decimal('299.00'))
        self.mentor_instructor = CourseInstructor.objects.create(
            course=self.mentor_course, user=self.mentor, role=CourseInstructor.InstructorRole.MENTOR,
            is_primary=True, commission_rate=Decimal('20.00'),
        )

        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(purchase, previous_status='PENDING')

        other_purchase = Purchase.objects.create(
            user=self.student, course=self.other_course, amount=Decimal('799.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(other_purchase, previous_status='PENDING')

        mentor_purchase = Purchase.objects.create(
            user=self.student, course=self.mentor_course, amount=Decimal('299.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(mentor_purchase, previous_status='PENDING')

    def test_unauthenticated_denied(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_student_denied(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_sees_own_earnings(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['course']['id'], self.course.id)
        self.assertEqual(Decimal(results[0]['gross_amount']), Decimal('499.00'))
        self.assertEqual(Decimal(results[0]['commission_rate']), Decimal('30.00'))
        self.assertEqual(Decimal(results[0]['commission_amount']), Decimal('149.70'))
        self.assertEqual(Decimal(results[0]['net_amount']), Decimal('349.30'))

    def test_teacher_cannot_see_another_teachers_earnings(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        course_ids = [row['course']['id'] for row in response.data['results']]
        self.assertNotIn(self.other_course.id, course_ids)

    def test_mentor_sees_own_earnings(self):
        self.client.force_authenticate(user=self.mentor)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['course']['id'], self.mentor_course.id)

    def test_empty_earnings_returns_empty_list(self):
        teacher_no_sales = User.objects.create_user(username='no_sales_teacher', password='password123', is_teacher=True)
        course = Course.objects.create(title='No Sales Course', description='x', price=Decimal('100.00'))
        _make_course_instructor(course, teacher_no_sales, commission_rate=Decimal('30.00'))
        self.client.force_authenticate(user=teacher_no_sales)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'], [])
        self.assertEqual(response.data['count'], 0)

    def test_multiple_ledger_entries_returned(self):
        second_course = Course.objects.create(title='Second Earnings Course', description='x', price=Decimal('199.00'))
        _make_course_instructor(second_course, self.teacher, commission_rate=Decimal('25.00'))
        second_purchase = Purchase.objects.create(
            user=self.student, course=second_course, amount=Decimal('199.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(second_purchase, previous_status='PENDING')

        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.data['count'], 2)

    def test_payout_status_null_when_unbatched(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertIsNone(response.data['results'][0]['payout_status'])

    def test_no_razorpay_ids_leaked(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        body = str(response.content)
        self.assertNotIn('razorpay', body.lower())

    def test_pagination_page_size_respected(self):
        for i in range(12):
            course = Course.objects.create(title=f'Pagination Course {i}', description='x', price=Decimal('50.00'))
            _make_course_instructor(course, self.teacher, commission_rate=Decimal('10.00'))
            purchase = Purchase.objects.create(
                user=self.student, course=course, amount=Decimal('50.00'), status=Purchase.Status.SUCCESS,
            )
            fulfill_purchase(purchase, previous_status='PENDING')

        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['results']), 10)  # page_size=10
        self.assertEqual(response.data['count'], 13)  # 1 original + 12 new
        self.assertIsNotNone(response.data['next'])


class MyEarningsSummaryAPITests(APITestCase):
    """Final release audit gap fix. GET /api/finance/my-earnings-summary/."""

    def setUp(self):
        self.url = reverse('finance-my-earnings-summary')

        self.student = User.objects.create_user(username='summary_student', password='password123')
        self.teacher = User.objects.create_user(username='summary_teacher', password='password123', is_teacher=True)
        self.other_teacher = User.objects.create_user(username='summary_other_teacher', password='password123', is_teacher=True)
        self.mentor = User.objects.create_user(username='summary_mentor', password='password123', is_mentor=True)

        self.course = Course.objects.create(title='Summary Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

        self.other_course = Course.objects.create(title='Summary Other Course', description='x', price=Decimal('800.00'))
        self.other_instructor = _make_course_instructor(self.other_course, self.other_teacher, commission_rate=Decimal('40.00'))

        self.mentor_course = Course.objects.create(title='Summary Mentor Course', description='x', price=Decimal('300.00'))
        self.mentor_instructor = CourseInstructor.objects.create(
            course=self.mentor_course, user=self.mentor, role=CourseInstructor.InstructorRole.MENTOR,
            is_primary=True, commission_rate=Decimal('20.00'),
        )

        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(purchase, previous_status='PENDING')

        other_purchase = Purchase.objects.create(
            user=self.student, course=self.other_course, amount=Decimal('800.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(other_purchase, previous_status='PENDING')

        mentor_purchase = Purchase.objects.create(
            user=self.student, course=self.mentor_course, amount=Decimal('300.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(mentor_purchase, previous_status='PENDING')

    def test_unauthenticated_denied(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_student_denied(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_summary_matches_get_instructor_balance(self):
        # This view is a thin wrapper -- its response must always agree
        # exactly with calling the underlying service function directly
        # (the same one admin reconciliation already relies on).
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected = get_instructor_balance(self.teacher).to_dict()
        for key, value in expected.items():
            self.assertEqual(Decimal(str(response.data[key])), Decimal(str(value)), key)

    def test_mentor_summary_matches_get_instructor_balance(self):
        self.client.force_authenticate(user=self.mentor)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        expected = get_instructor_balance(self.mentor).to_dict()
        for key, value in expected.items():
            self.assertEqual(Decimal(str(response.data[key])), Decimal(str(value)), key)

    def test_teacher_earned_reflects_only_own_course(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        # net_amount for a 500 course at 30% commission = 350.00 -- confirms
        # the earned figure is scoped to this teacher's own course only,
        # not summed across every instructor in the system.
        self.assertEqual(Decimal(str(response.data['earned'])), Decimal('350.00'))

    def test_no_earnings_returns_zeroed_balance(self):
        idle_teacher = User.objects.create_user(username='idle_summary_teacher', password='password123', is_teacher=True)
        self.client.force_authenticate(user=idle_teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(str(response.data['earned'])), Decimal('0'))
        self.assertEqual(Decimal(str(response.data['paid'])), Decimal('0'))
        self.assertEqual(Decimal(str(response.data['available'])), Decimal('0'))

    def test_admin_only_payout_list_still_forbidden_for_teacher(self):
        # Sanity check only -- confirms this new teacher-facing view shares
        # no permission/code path with the admin payout endpoints, which
        # must still 403 a plain teacher exactly as before this phase.
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(reverse('finance-payout-list'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class MyPayoutListAPITests(APITestCase):
    """Final release audit gap fix. GET /api/finance/my-payouts/."""

    def setUp(self):
        self.url = reverse('finance-my-payouts')

        self.student = User.objects.create_user(username='payoutlist_student', password='password123')
        self.teacher = User.objects.create_user(username='payoutlist_teacher', password='password123', is_teacher=True)
        self.other_teacher = User.objects.create_user(username='payoutlist_other_teacher', password='password123', is_teacher=True)
        self.admin = User.objects.create_user(username='payoutlist_admin', password='password123', is_staff=True)

        self.course = Course.objects.create(title='Payout List Course', description='x', price=Decimal('1000.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

        self.other_course = Course.objects.create(title='Payout List Other Course', description='x', price=Decimal('1000.00'))
        self.other_instructor = _make_course_instructor(self.other_course, self.other_teacher, commission_rate=Decimal('30.00'))

        purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('1000.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(purchase, previous_status='PENDING')
        self.entry = LedgerEntry.objects.get(course_instructor=self.instructor)

        other_purchase = Purchase.objects.create(
            user=self.student, course=self.other_course, amount=Decimal('1000.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(other_purchase, previous_status='PENDING')
        self.other_entry = LedgerEntry.objects.get(course_instructor=self.other_instructor)

        self.payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[self.entry.id],
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        self.other_payout = create_payout_batch(
            recipient=self.other_teacher, ledger_entry_ids=[self.other_entry.id],
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )

    def test_unauthenticated_denied(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_student_denied(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_sees_own_payout_only(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.payout.id)

    def test_teacher_cannot_see_another_teachers_payout(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        payout_ids = [row['id'] for row in response.data['results']]
        self.assertNotIn(self.other_payout.id, payout_ids)

    def test_other_teacher_sees_own_payout_only(self):
        self.client.force_authenticate(user=self.other_teacher)
        response = self.client.get(self.url)
        results = response.data['results']
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.other_payout.id)

    def test_payout_status_reflects_lifecycle(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.data['results'][0]['status'], Payout.Status.DRAFT)

        approve_payout(self.payout, approved_by=self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.data['results'][0]['status'], Payout.Status.APPROVED)

        mark_payout_paid(self.payout, method='UPI', reference_number='UTR123')
        response = self.client.get(self.url)
        self.assertEqual(response.data['results'][0]['status'], Payout.Status.PAID)
        self.assertEqual(response.data['results'][0]['method'], 'UPI')
        self.assertEqual(response.data['results'][0]['reference_number'], 'UTR123')

    def test_empty_payout_list_for_teacher_with_no_batches(self):
        idle_teacher = User.objects.create_user(username='idle_payout_teacher', password='password123', is_teacher=True)
        self.client.force_authenticate(user=idle_teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['results'], [])
        self.assertEqual(response.data['count'], 0)

    def test_teacher_cannot_approve_via_admin_endpoint(self):
        # Read-only surface confirmation -- a teacher has no path to the
        # admin approve/mark-paid endpoints from this new page; those
        # remain exclusively IsSuperAdminOrAdmin, unchanged.
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(reverse('finance-payout-approve', kwargs={'pk': self.payout.id}))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AdminLedgerInspectionAPITests(APITestCase):
    """Phase 3.5.3. GET /api/finance/admin/ledger-entries/."""

    def setUp(self):
        self.url = reverse('finance-admin-ledger-entries')

        self.admin = User.objects.create_user(username='ledger_admin', password='password123', is_staff=True)
        self.student = User.objects.create_user(username='admin_api_student', password='password123')
        self.teacher = User.objects.create_user(username='admin_api_teacher', password='password123', is_teacher=True)

        self.course = Course.objects.create(title='Admin API Course', description='x', price=Decimal('499.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(self.purchase, previous_status='PENDING')
        self.entry = LedgerEntry.objects.get(purchase=self.purchase)

    def test_admin_can_inspect_ledger(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        row = response.data['results'][0]
        self.assertEqual(row['instructor']['username'], self.teacher.username)
        self.assertEqual(row['course']['id'], self.course.id)
        self.assertEqual(row['source_type'], 'PURCHASE')
        self.assertEqual(Decimal(row['gross_amount']), Decimal('499.00'))
        self.assertEqual(Decimal(row['commission_amount']), Decimal('149.70'))
        self.assertEqual(Decimal(row['net_amount']), Decimal('349.30'))
        self.assertEqual(row['currency'], 'INR')
        self.assertIsNone(row['payout'])

    def test_student_denied(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_denied(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_denied(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_filter_by_instructor_id(self):
        other_teacher = User.objects.create_user(username='admin_api_other_teacher', password='password123', is_teacher=True)
        other_course = Course.objects.create(title='Other Admin Course', description='x', price=Decimal('199.00'))
        _make_course_instructor(other_course, other_teacher, commission_rate=Decimal('20.00'))
        other_purchase = Purchase.objects.create(
            user=self.student, course=other_course, amount=Decimal('199.00'), status=Purchase.Status.SUCCESS,
        )
        fulfill_purchase(other_purchase, previous_status='PENDING')

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url, {'instructor_id': self.teacher.id})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['instructor']['username'], self.teacher.username)

    def test_filter_by_course_id(self):
        response_qs = {'course_id': self.course.id}
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url, response_qs)
        self.assertEqual(response.data['count'], 1)

    def test_filter_by_entry_type(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url, {'entry_type': 'EARNING'})
        self.assertEqual(response.data['count'], 1)
        response_none = self.client.get(self.url, {'entry_type': 'CLAWBACK'})
        self.assertEqual(response_none.data['count'], 0)

    def test_filter_by_source_type(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url, {'source_type': 'PURCHASE'})
        self.assertEqual(response.data['count'], 1)
        response_none = self.client.get(self.url, {'source_type': 'SUBSCRIPTION_PAYMENT'})
        self.assertEqual(response_none.data['count'], 0)

    def test_filter_by_payout_null(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url, {'payout': 'null'})
        self.assertEqual(response.data['count'], 1)

    def test_no_razorpay_ids_leaked(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)
        body = str(response.content)
        self.assertNotIn('razorpay', body.lower())

    def test_pagination_page_size_respected(self):
        for i in range(12):
            course = Course.objects.create(title=f'Admin Pagination Course {i}', description='x', price=Decimal('50.00'))
            _make_course_instructor(course, self.teacher, commission_rate=Decimal('10.00'))
            purchase = Purchase.objects.create(
                user=self.student, course=course, amount=Decimal('50.00'), status=Purchase.Status.SUCCESS,
            )
            fulfill_purchase(purchase, previous_status='PENDING')

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)
        self.assertEqual(len(response.data['results']), 10)
        self.assertEqual(response.data['count'], 13)


def _make_earning_entry(course_instructor, purchase, gross_amount=Decimal('499.00'), commission_rate=Decimal('30.00'), currency='INR'):
    """Manually constructs a LedgerEntry, bypassing finance/services.py's
    normal creation path -- used only to set up eligibility/edge-case
    fixtures (zero/negative net, CLAWBACK type, unsupported currency) that
    the real fulfillment hooks structurally cannot produce."""
    commission_amount = (gross_amount * commission_rate / Decimal('100')).quantize(Decimal('0.01'))
    return LedgerEntry.objects.create(
        course_instructor=course_instructor, purchase=purchase, entry_type=LedgerEntry.EntryType.EARNING,
        gross_amount=gross_amount, currency=currency, commission_rate=commission_rate,
        commission_amount=commission_amount, net_amount=gross_amount - commission_amount,
    )


class PayoutEligibilityTests(TestCase):
    """Phase 3.5.4. get_eligible_ledger_entries_queryset()."""

    def setUp(self):
        self.student = User.objects.create_user(username='elig_student', password='password123')
        self.teacher = User.objects.create_user(username='elig_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Eligibility Course', description='x', price=Decimal('499.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS,
        )

    def test_eligible_entry_included(self):
        fulfill_purchase(self.purchase, previous_status='PENDING')
        entry = LedgerEntry.objects.get(purchase=self.purchase)
        self.assertIn(entry, get_eligible_ledger_entries_queryset())

    def test_empty_eligible_set(self):
        self.assertEqual(get_eligible_ledger_entries_queryset().count(), 0)

    def test_already_batched_entry_excluded(self):
        fulfill_purchase(self.purchase, previous_status='PENDING')
        entry = LedgerEntry.objects.get(purchase=self.purchase)
        create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[entry.id],
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        self.assertEqual(get_eligible_ledger_entries_queryset().count(), 0)

    def test_zero_net_amount_excluded(self):
        entry = _make_earning_entry(self.instructor, self.purchase, gross_amount=Decimal('100.00'), commission_rate=Decimal('100.00'))
        self.assertEqual(entry.net_amount, Decimal('0.00'))
        self.assertNotIn(entry, get_eligible_ledger_entries_queryset())

    def test_negative_net_amount_excluded(self):
        entry = LedgerEntry.objects.create(
            course_instructor=self.instructor, purchase=self.purchase, entry_type=LedgerEntry.EntryType.EARNING,
            gross_amount=Decimal('100.00'), currency='INR', commission_rate=Decimal('30.00'),
            commission_amount=Decimal('150.00'), net_amount=Decimal('-50.00'),
        )
        self.assertNotIn(entry, get_eligible_ledger_entries_queryset())

    def test_malformed_positive_net_clawback_excluded(self):
        # Phase 3.5.8: a real, well-formed CLAWBACK always has a negative
        # net_amount (see _apply_clawback_to_entry) and, since this
        # phase, IS payout-eligible -- see
        # test_unbatched_clawback_is_now_eligible below. This fixture is
        # deliberately malformed (positive net_amount, which no real code
        # path produces) to confirm the eligibility filter's net_amount
        # sign check still defensively excludes it.
        entry = LedgerEntry.objects.create(
            course_instructor=self.instructor, purchase=self.purchase, entry_type=LedgerEntry.EntryType.CLAWBACK,
            gross_amount=Decimal('100.00'), currency='INR', commission_rate=Decimal('30.00'),
            commission_amount=Decimal('30.00'), net_amount=Decimal('70.00'),
        )
        self.assertNotIn(entry, get_eligible_ledger_entries_queryset())

    def test_unbatched_clawback_is_now_eligible(self):
        # Phase 3.5.8: a well-formed, unbatched CLAWBACK (negative
        # net_amount) is now payout-eligible -- this is the mechanism
        # that lets a future payout settle outstanding clawback debt.
        entry = LedgerEntry.objects.create(
            course_instructor=self.instructor, purchase=self.purchase, entry_type=LedgerEntry.EntryType.CLAWBACK,
            gross_amount=Decimal('-100.00'), currency='INR', commission_rate=Decimal('30.00'),
            commission_amount=Decimal('-30.00'), net_amount=Decimal('-70.00'),
        )
        self.assertIn(entry, get_eligible_ledger_entries_queryset())

    def test_batched_clawback_excluded(self):
        # Once a CLAWBACK is settled into a payout, it must stop being
        # "eligible" the same way an EARNING entry already does.
        payout = Payout.objects.create(
            recipient=self.teacher, period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        entry = LedgerEntry.objects.create(
            course_instructor=self.instructor, purchase=self.purchase, entry_type=LedgerEntry.EntryType.CLAWBACK,
            gross_amount=Decimal('-100.00'), currency='INR', commission_rate=Decimal('30.00'),
            commission_amount=Decimal('-30.00'), net_amount=Decimal('-70.00'), payout=payout,
        )
        self.assertNotIn(entry, get_eligible_ledger_entries_queryset())

    def test_unsupported_currency_excluded(self):
        entry = _make_earning_entry(self.instructor, self.purchase, currency='USD')
        self.assertNotIn(entry, get_eligible_ledger_entries_queryset())

    def test_inactive_recipient_excluded(self):
        fulfill_purchase(self.purchase, previous_status='PENDING')
        self.teacher.is_active = False
        self.teacher.save()
        self.assertEqual(get_eligible_ledger_entries_queryset().count(), 0)


class CreatePayoutBatchServiceTests(TestCase):
    """Phase 3.5.4. finance.services.create_payout_batch()."""

    def setUp(self):
        self.student = User.objects.create_user(username='batch_student', password='password123')
        self.teacher = User.objects.create_user(username='batch_teacher', password='password123', is_teacher=True)
        self.other_teacher = User.objects.create_user(username='batch_other_teacher', password='password123', is_teacher=True)

        self.course = Course.objects.create(title='Batch Course A', description='x', price=Decimal('499.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))
        self.course2 = Course.objects.create(title='Batch Course B', description='x', price=Decimal('199.00'))
        _make_course_instructor(self.course2, self.teacher, commission_rate=Decimal('25.00'))

        self.other_course = Course.objects.create(title='Other Instructor Course', description='x', price=Decimal('299.00'))
        self.other_instructor = _make_course_instructor(self.other_course, self.other_teacher, commission_rate=Decimal('20.00'))

        p1 = Purchase.objects.create(user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(p1, previous_status='PENDING')
        self.entry1 = LedgerEntry.objects.get(purchase=p1)

        p2 = Purchase.objects.create(user=self.student, course=self.course2, amount=Decimal('199.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(p2, previous_status='PENDING')
        self.entry2 = LedgerEntry.objects.get(purchase=p2)

        p3 = Purchase.objects.create(user=self.student, course=self.other_course, amount=Decimal('299.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(p3, previous_status='PENDING')
        self.other_entry = LedgerEntry.objects.get(purchase=p3)

    def _batch(self, entry_ids, recipient=None):
        return create_payout_batch(
            recipient=recipient or self.teacher, ledger_entry_ids=entry_ids,
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )

    def test_creates_payout_with_correct_totals(self):
        payout = self._batch([self.entry1.id, self.entry2.id])
        self.assertEqual(payout.gross_amount, Decimal('698.00'))       # 499 + 199
        self.assertEqual(payout.commission_amount, Decimal('199.45'))  # 149.70 + 49.75
        self.assertEqual(payout.net_amount, Decimal('498.55'))         # 349.30 + 149.25

    def test_status_is_draft_not_paid(self):
        payout = self._batch([self.entry1.id])
        self.assertEqual(payout.status, Payout.Status.DRAFT)

    def test_ledger_entries_assigned_to_payout(self):
        payout = self._batch([self.entry1.id, self.entry2.id])
        self.entry1.refresh_from_db()
        self.entry2.refresh_from_db()
        self.assertEqual(self.entry1.payout_id, payout.id)
        self.assertEqual(self.entry2.payout_id, payout.id)

    def test_financial_values_unchanged_after_batching(self):
        original = (self.entry1.gross_amount, self.entry1.commission_rate, self.entry1.commission_amount, self.entry1.net_amount)
        self._batch([self.entry1.id])
        self.entry1.refresh_from_db()
        self.assertEqual(
            (self.entry1.gross_amount, self.entry1.commission_rate, self.entry1.commission_amount, self.entry1.net_amount),
            original,
        )

    def test_empty_selection_raises(self):
        with self.assertRaises(ValueError):
            self._batch([])

    def test_missing_entry_id_raises(self):
        with self.assertRaises(ValueError):
            self._batch([self.entry1.id, 999999])

    def test_already_batched_entry_raises(self):
        self._batch([self.entry1.id])
        with self.assertRaises(ValueError):
            self._batch([self.entry1.id])

    def test_same_ledger_entry_cannot_belong_to_two_payouts(self):
        payout1 = self._batch([self.entry1.id])
        with self.assertRaises(ValueError):
            self._batch([self.entry1.id, self.entry2.id])
        # First payout's assignment must survive the second (failed) attempt untouched.
        self.entry1.refresh_from_db()
        self.assertEqual(self.entry1.payout_id, payout1.id)
        self.entry2.refresh_from_db()
        self.assertIsNone(self.entry2.payout_id)

    def test_wrong_recipient_raises(self):
        with self.assertRaises(ValueError):
            self._batch([self.entry1.id, self.other_entry.id], recipient=self.teacher)

    def test_non_earning_entry_type_raises(self):
        clawback = LedgerEntry.objects.create(
            course_instructor=self.instructor, purchase=Purchase.objects.create(
                user=self.student, course=self.course, amount=Decimal('10.00'), status=Purchase.Status.SUCCESS,
            ),
            entry_type=LedgerEntry.EntryType.CLAWBACK,
            gross_amount=Decimal('10.00'), currency='INR', commission_rate=Decimal('30.00'),
            commission_amount=Decimal('3.00'), net_amount=Decimal('7.00'),
        )
        with self.assertRaises(ValueError):
            self._batch([self.entry1.id, clawback.id])

    def test_multiple_instructors_each_get_independent_batches(self):
        payout_a = self._batch([self.entry1.id, self.entry2.id], recipient=self.teacher)
        payout_b = self._batch([self.other_entry.id], recipient=self.other_teacher)
        self.assertNotEqual(payout_a.id, payout_b.id)
        self.assertEqual(payout_a.recipient_id, self.teacher.id)
        self.assertEqual(payout_b.recipient_id, self.other_teacher.id)


class ApprovePayoutServiceTests(TestCase):
    """Phase 3.5.4. finance.services.approve_payout()."""

    def setUp(self):
        self.student = User.objects.create_user(username='approve_student', password='password123')
        self.teacher = User.objects.create_user(username='approve_teacher', password='password123', is_teacher=True)
        self.admin = User.objects.create_user(username='approve_admin', password='password123', is_staff=True)
        self.course = Course.objects.create(title='Approve Course', description='x', price=Decimal('499.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))
        purchase = Purchase.objects.create(user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(purchase, previous_status='PENDING')
        entry = LedgerEntry.objects.get(purchase=purchase)
        self.payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[entry.id],
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )

    def test_draft_to_approved_transition(self):
        approve_payout(self.payout, approved_by=self.admin)
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.APPROVED)

    def test_approve_sets_approved_by_and_approved_at(self):
        approve_payout(self.payout, approved_by=self.admin)
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.approved_by_id, self.admin.id)
        self.assertIsNotNone(self.payout.approved_at)

    def test_cannot_approve_already_approved_payout(self):
        approve_payout(self.payout, approved_by=self.admin)
        with self.assertRaises(ValueError):
            approve_payout(self.payout, approved_by=self.admin)

    def test_cannot_approve_cancelled_payout(self):
        self.payout.status = Payout.Status.CANCELLED
        self.payout.save()
        with self.assertRaises(ValueError):
            approve_payout(self.payout, approved_by=self.admin)


class EligibleLedgerEntriesAPITests(APITestCase):
    """Phase 3.5.4. GET /api/finance/admin/eligible-ledger-entries/."""

    def setUp(self):
        self.url = reverse('finance-eligible-ledger-entries')
        self.admin = User.objects.create_user(username='elig_api_admin', password='password123', is_staff=True)
        self.student = User.objects.create_user(username='elig_api_student', password='password123')
        self.teacher = User.objects.create_user(username='elig_api_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Eligible API Course', description='x', price=Decimal('499.00'))
        _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))
        purchase = Purchase.objects.create(user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(purchase, previous_status='PENDING')

    def test_admin_can_list_eligible(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)

    def test_student_denied(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_denied(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_denied(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class PayoutAPITests(APITestCase):
    """Phase 3.5.4. Payout create/list/detail/approve endpoints."""

    def setUp(self):
        self.admin = User.objects.create_user(username='payout_api_admin', password='password123', is_staff=True)
        self.student = User.objects.create_user(username='payout_api_student', password='password123')
        self.teacher = User.objects.create_user(username='payout_api_teacher', password='password123', is_teacher=True)

        self.course = Course.objects.create(title='Payout API Course', description='x', price=Decimal('499.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))
        self.course2 = Course.objects.create(title='Payout API Course 2', description='x', price=Decimal('199.00'))
        _make_course_instructor(self.course2, self.teacher, commission_rate=Decimal('25.00'))

        p1 = Purchase.objects.create(user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(p1, previous_status='PENDING')
        self.entry1 = LedgerEntry.objects.get(purchase=p1)

        p2 = Purchase.objects.create(user=self.student, course=self.course2, amount=Decimal('199.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(p2, previous_status='PENDING')
        self.entry2 = LedgerEntry.objects.get(purchase=p2)

        self.create_url = reverse('finance-payout-create')
        self.list_url = reverse('finance-payout-list')

    def _create_payload(self, entry_ids, recipient_id=None):
        return {
            'recipient_id': recipient_id or self.teacher.id,
            'ledger_entry_ids': entry_ids,
            'period_start': '2026-01-01',
            'period_end': '2026-01-31',
        }

    def test_admin_can_create_payout_batch(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.create_url, self._create_payload([self.entry1.id, self.entry2.id]), format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Decimal(response.data['gross_amount']), Decimal('698.00'))
        self.assertEqual(response.data['status'], 'DRAFT')
        self.assertEqual(response.data['entry_count'], 2)
        self.assertEqual(len(response.data['entries']), 2)

    def test_student_denied_create(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_denied_create(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_denied_create(self):
        response = self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_create_payout_already_batched_entry_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        response = self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_duplicate_payout_attempt_does_not_create_second_payout(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        self.assertEqual(Payout.objects.count(), 1)

    def test_admin_can_list_payouts(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['entry_count'], 1)

    def test_non_admin_denied_list(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_view_payout_detail_with_entries(self):
        self.client.force_authenticate(user=self.admin)
        create_response = self.client.post(self.create_url, self._create_payload([self.entry1.id, self.entry2.id]), format='json')
        detail_url = reverse('finance-payout-detail', kwargs={'pk': create_response.data['id']})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data['entries']), 2)

    def test_admin_can_approve_payout(self):
        self.client.force_authenticate(user=self.admin)
        create_response = self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        approve_url = reverse('finance-payout-approve', kwargs={'pk': create_response.data['id']})
        response = self.client.post(approve_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'APPROVED')

    def test_student_denied_approve(self):
        self.client.force_authenticate(user=self.admin)
        create_response = self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        approve_url = reverse('finance-payout-approve', kwargs={'pk': create_response.data['id']})
        self.client.force_authenticate(user=self.student)
        response = self.client.post(approve_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_double_approve_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        create_response = self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        approve_url = reverse('finance-payout-approve', kwargs={'pk': create_response.data['id']})
        self.client.post(approve_url)
        response = self.client.post(approve_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_no_razorpay_ids_or_sensitive_data_leaked(self):
        self.client.force_authenticate(user=self.admin)
        create_response = self.client.post(self.create_url, self._create_payload([self.entry1.id]), format='json')
        body = str(create_response.content).lower()
        self.assertNotIn('razorpay', body)
        self.assertNotIn('bank', body)
        self.assertNotIn('upi', body)


class MarkPayoutPaidServiceTests(TestCase):
    """
    Final release audit gap fix. finance.services.mark_payout_paid() --
    the APPROVED -> PAID transition approve_payout's own docstring
    explicitly deferred. Mirrors ApprovePayoutServiceTests' exact fixture
    shape above.
    """

    def setUp(self):
        self.student = User.objects.create_user(username='paid_student', password='password123')
        self.teacher = User.objects.create_user(username='paid_teacher', password='password123', is_teacher=True)
        self.admin = User.objects.create_user(username='paid_admin', password='password123', is_staff=True)
        self.course = Course.objects.create(title='Paid Course', description='x', price=Decimal('499.00'))
        self.instructor = CourseInstructor.objects.create(
            course=self.course, user=self.teacher, role=CourseInstructor.InstructorRole.TEACHER,
            is_primary=True, commission_rate=Decimal('30.00'),
        )
        purchase = Purchase.objects.create(user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(purchase, previous_status='PENDING')
        self.entry = LedgerEntry.objects.get(purchase=purchase)
        self.payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[self.entry.id],
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        approve_payout(self.payout, approved_by=self.admin)
        self.payout.refresh_from_db()

    def test_approved_to_paid_transition(self):
        mark_payout_paid(self.payout)
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.status, Payout.Status.PAID)

    def test_paid_sets_paid_at(self):
        mark_payout_paid(self.payout)
        self.payout.refresh_from_db()
        self.assertIsNotNone(self.payout.paid_at)

    # 3. PENDING (here: DRAFT, this model's actual pre-approval state)
    # cannot be marked PAID. Built against a second, independent,
    # still-unbatched entry -- self.payout/self.entry (setUp) are already
    # APPROVED and can't be reused to construct a fresh DRAFT.
    def test_cannot_mark_draft_payout_paid(self):
        purchase2 = Purchase.objects.create(user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS, razorpay_order_id='order_draft_1')
        fulfill_purchase(purchase2, previous_status='PENDING')
        entry2 = LedgerEntry.objects.get(purchase=purchase2)
        draft_payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[entry2.id],
            period_start=date(2026, 2, 1), period_end=date(2026, 2, 28),
        )
        with self.assertRaises(ValueError):
            mark_payout_paid(draft_payout)

    # 4/5. Already PAID cannot be paid again -- also proves this is the
    # invalid-transition guard, not merely a duplicate-request special case.
    def test_cannot_mark_already_paid_payout_paid_again(self):
        mark_payout_paid(self.payout)
        with self.assertRaises(ValueError):
            mark_payout_paid(self.payout)

    def test_cannot_mark_cancelled_payout_paid(self):
        self.payout.status = Payout.Status.CANCELLED
        self.payout.save(update_fields=['status'])
        with self.assertRaises(ValueError):
            mark_payout_paid(self.payout)

    def test_blank_method_and_reference_preserve_existing_values(self):
        self.payout.method = Payout.Method.UPI
        self.payout.reference_number = 'already-set-utr'
        self.payout.save(update_fields=['method', 'reference_number'])
        mark_payout_paid(self.payout)
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.method, Payout.Method.UPI)
        self.assertEqual(self.payout.reference_number, 'already-set-utr')

    def test_method_and_reference_can_be_set_at_paid_time(self):
        mark_payout_paid(self.payout, method=Payout.Method.BANK_TRANSFER, reference_number='UTR123456')
        self.payout.refresh_from_db()
        self.assertEqual(self.payout.method, Payout.Method.BANK_TRANSFER)
        self.assertEqual(self.payout.reference_number, 'UTR123456')

    # 7. Existing payout/ledger state remains correct -- no LedgerEntry is
    # touched, and the Payout's own financial totals are unchanged.
    def test_ledger_entry_and_payout_totals_unchanged_by_marking_paid(self):
        original_totals = (self.payout.gross_amount, self.payout.commission_amount, self.payout.net_amount)
        # self.entry was fetched in setUp(), BEFORE create_payout_batch()'s
        # own bulk .update(payout=payout) ran -- that's a queryset-level
        # update, so it never touched this already-loaded Python object's
        # in-memory payout_id. Refresh first to read the real, current
        # value rather than a stale None.
        self.entry.refresh_from_db()
        entry_payout_id_before = self.entry.payout_id
        mark_payout_paid(self.payout)
        self.payout.refresh_from_db()
        self.entry.refresh_from_db()
        self.assertEqual((self.payout.gross_amount, self.payout.commission_amount, self.payout.net_amount), original_totals)
        self.assertEqual(self.entry.payout_id, entry_payout_id_before)
        self.assertEqual(self.entry.net_amount, Decimal('349.30'))  # 499 - 30% commission, unchanged

    def test_no_duplicate_payout_created(self):
        mark_payout_paid(self.payout)
        self.assertEqual(Payout.objects.filter(recipient=self.teacher).count(), 1)

    # Ties the whole fix together: get_instructor_balance()'s own "paid"
    # aggregate (finance/reconciliation.py) is computed by filtering
    # LedgerEntry.payout__status=PAID -- confirms this one status flip is
    # sufficient, with no other write, to make that balance correct.
    def test_instructor_balance_paid_total_reflects_the_transition(self):
        balance_before = get_instructor_balance(self.teacher)
        self.assertEqual(balance_before.paid, Decimal('0'))
        mark_payout_paid(self.payout)
        balance_after = get_instructor_balance(self.teacher)
        self.assertEqual(balance_after.paid, Decimal('349.30'))


class MarkPayoutPaidAPITests(APITestCase):
    """Final release audit gap fix. POST /api/finance/admin/payouts/<id>/mark-paid/."""

    def setUp(self):
        self.admin = User.objects.create_user(username='paid_api_admin', password='password123', is_staff=True)
        self.student = User.objects.create_user(username='paid_api_student', password='password123')
        self.teacher = User.objects.create_user(username='paid_api_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Paid API Course', description='x', price=Decimal('499.00'))
        CourseInstructor.objects.create(
            course=self.course, user=self.teacher, role=CourseInstructor.InstructorRole.TEACHER,
            is_primary=True, commission_rate=Decimal('30.00'),
        )
        purchase = Purchase.objects.create(user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(purchase, previous_status='PENDING')
        entry = LedgerEntry.objects.get(purchase=purchase)
        self.payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[entry.id],
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
        )
        self.mark_paid_url = reverse('finance-payout-mark-paid', kwargs={'pk': self.payout.id})

    def _approve(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(reverse('finance-payout-approve', kwargs={'pk': self.payout.id}))

    # 1. APPROVED -> PAID succeeds.
    def test_admin_can_mark_approved_payout_paid(self):
        self._approve()
        response = self.client.post(self.mark_paid_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'PAID')
        self.assertIsNotNone(response.data['paid_at'])

    # 2. Non-admin cannot mark payout PAID.
    def test_student_denied(self):
        self._approve()
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.mark_paid_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_denied(self):
        self._approve()
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(self.mark_paid_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_denied(self):
        self._approve()
        # _approve() force_authenticate()s as admin, which persists on this
        # client until explicitly cleared -- must reset to a genuinely
        # unauthenticated request, not just "no login call in this test".
        self.client.force_authenticate(user=None)
        response = self.client.post(self.mark_paid_url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    # 3. PENDING (DRAFT, pre-approval) cannot be marked PAID.
    def test_cannot_mark_draft_payout_paid(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.mark_paid_url)  # never approved
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    # 4. Already PAID cannot be paid again.
    def test_cannot_mark_already_paid_payout_paid_again(self):
        self._approve()
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.mark_paid_url)
        response = self.client.post(self.mark_paid_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # 5. Invalid transition (CANCELLED) is rejected.
    def test_cannot_mark_cancelled_payout_paid(self):
        self.payout.status = Payout.Status.CANCELLED
        self.payout.save(update_fields=['status'])
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.mark_paid_url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # 6. Double submission/concurrent transition cannot create duplicate
    # financial effects -- the second call is rejected outright (400), not
    # silently accepted, and no second Payout/LedgerEntry side effect is
    # ever produced.
    def test_double_submission_is_rejected_not_duplicated(self):
        self._approve()
        self.client.force_authenticate(user=self.admin)
        first = self.client.post(self.mark_paid_url)
        second = self.client.post(self.mark_paid_url)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Payout.objects.count(), 1)

    def test_can_set_reference_number_and_method_via_api(self):
        self._approve()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.mark_paid_url, {'method': 'UPI', 'reference_number': 'utr-api-1'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['method'], 'UPI')
        self.assertEqual(response.data['reference_number'], 'utr-api-1')

    # 7. Existing payout/ledger state remains correct after the transition.
    def test_payout_totals_and_entry_count_unchanged_after_marking_paid(self):
        self._approve()
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.mark_paid_url)
        self.assertEqual(Decimal(response.data['gross_amount']), Decimal('499.00'))
        self.assertEqual(response.data['entry_count'], 1)
        self.assertEqual(len(response.data['entries']), 1)


class InvoiceModelTests(TestCase):
    """Phase 3.5.5. Invoice model constraints: numbering, immutability,
    exactly-one-source, idempotency uniqueness."""

    def setUp(self):
        self.student = User.objects.create_user(username='inv_model_student', password='password123')
        self.course = Course.objects.create(title='Invoice Model Course', description='x', price=Decimal('499.00'))
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS,
        )

    def _invoice_kwargs(self, **overrides):
        from django.utils import timezone
        kwargs = dict(customer=self.student, amount=Decimal('499.00'), payment_date=timezone.now())
        kwargs.update(overrides)
        return kwargs

    def test_invoice_number_generated_on_save(self):
        invoice = Invoice.objects.create(**self._invoice_kwargs(purchase=self.purchase))
        self.assertTrue(invoice.invoice_number.startswith('INV-'))

    def test_invoice_numbers_unique_across_multiple_invoices(self):
        course2 = Course.objects.create(title='Invoice Model Course 2', description='x', price=Decimal('199.00'))
        purchase2 = Purchase.objects.create(user=self.student, course=course2, amount=Decimal('199.00'), status=Purchase.Status.SUCCESS)
        invoice1 = Invoice.objects.create(**self._invoice_kwargs(purchase=self.purchase))
        invoice2 = Invoice.objects.create(**self._invoice_kwargs(purchase=purchase2, amount=Decimal('199.00')))
        self.assertNotEqual(invoice1.invoice_number, invoice2.invoice_number)

    def test_invoice_number_immutable_after_issuance(self):
        invoice = Invoice.objects.create(**self._invoice_kwargs(purchase=self.purchase))
        original_number = invoice.invoice_number
        invoice.status = Invoice.Status.CANCELLED
        invoice.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.invoice_number, original_number)

    def test_zero_sources_rejected(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Invoice.objects.create(**self._invoice_kwargs())

    def test_two_sources_rejected(self):
        order = Order.objects.create(user=self.student, subtotal=Decimal('499'), total_amount=Decimal('499'))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Invoice.objects.create(**self._invoice_kwargs(purchase=self.purchase, order=order))

    def test_duplicate_invoice_for_same_purchase_rejected(self):
        Invoice.objects.create(**self._invoice_kwargs(purchase=self.purchase))
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Invoice.objects.create(**self._invoice_kwargs(purchase=self.purchase))


class PurchaseInvoiceGenerationTests(TestCase):
    """Phase 3.5.5. fulfill_purchase() -- Flow A invoice hook."""

    def setUp(self):
        self.student = User.objects.create_user(username='inv_purchase_student', password='password123')
        self.course = Course.objects.create(title='Invoice Purchase Course', description='x', price=Decimal('499.00'))
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS,
        )

    def test_successful_purchase_creates_invoice(self):
        fulfill_purchase(self.purchase, previous_status='PENDING')
        self.assertEqual(Invoice.objects.filter(purchase=self.purchase).count(), 1)
        invoice = Invoice.objects.get(purchase=self.purchase)
        self.assertEqual(invoice.customer_id, self.student.id)
        self.assertEqual(invoice.amount, Decimal('499.00'))
        self.assertEqual(invoice.currency, 'INR')
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)

    def test_failed_purchase_creates_no_invoice(self):
        failed = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.FAILED,
        )
        fulfill_purchase(failed, previous_status='PENDING')
        self.assertEqual(Invoice.objects.filter(purchase=failed).count(), 0)

    def test_pending_purchase_creates_no_invoice(self):
        pending = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.PENDING,
        )
        fulfill_purchase(pending, previous_status='PENDING')
        self.assertEqual(Invoice.objects.filter(purchase=pending).count(), 0)

    def test_repeated_fulfillment_does_not_duplicate_invoice(self):
        # Simulates repeated verification / admin mark-paid / re-delivery.
        fulfill_purchase(self.purchase, previous_status='PENDING')
        fulfill_purchase(self.purchase, previous_status='SUCCESS')
        fulfill_purchase(self.purchase, previous_status='SUCCESS')
        self.assertEqual(Invoice.objects.filter(purchase=self.purchase).count(), 1)

    def test_historical_snapshot_survives_course_price_change(self):
        fulfill_purchase(self.purchase, previous_status='PENDING')
        invoice = Invoice.objects.get(purchase=self.purchase)
        self.course.price = Decimal('999.00')
        self.course.save()
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount, Decimal('499.00'))  # unchanged despite the course price change


class OrderInvoiceGenerationTests(TestCase):
    """Phase 3.5.5. fulfill_order() -- Flow B invoice hook. ONE invoice
    per Order, regardless of how many items it contains."""

    def setUp(self):
        self.student = User.objects.create_user(username='inv_order_student', password='password123')
        self.course = Course.objects.create(title='Invoice Order Course', description='x', price=Decimal('499.00'))
        self.course2 = Course.objects.create(title='Invoice Order Course 2', description='x', price=Decimal('199.00'))

    def _paid_order_with_two_items(self):
        order = Order.objects.create(
            user=self.student, status=Order.Status.PAID,
            subtotal=Decimal('698.00'), total_amount=Decimal('698.00'), currency='INR',
        )
        OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.COURSE, course=self.course,
            title_snapshot=self.course.title, unit_price=Decimal('499.00'), total_price=Decimal('499.00'),
        )
        OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.COURSE, course=self.course2,
            title_snapshot=self.course2.title, unit_price=Decimal('199.00'), total_price=Decimal('199.00'),
        )
        return order

    def test_successful_order_creates_exactly_one_invoice(self):
        order = self._paid_order_with_two_items()
        fulfill_order(order, previous_status='PENDING')
        self.assertEqual(Invoice.objects.filter(order=order).count(), 1)
        invoice = Invoice.objects.get(order=order)
        self.assertEqual(invoice.amount, Decimal('698.00'))
        self.assertEqual(invoice.customer_id, self.student.id)

    def test_order_not_paid_creates_no_invoice(self):
        order = Order.objects.create(
            user=self.student, status=Order.Status.PENDING,
            subtotal=Decimal('499.00'), total_amount=Decimal('499.00'), currency='INR',
        )
        fulfill_order(order, previous_status='PENDING')
        self.assertEqual(Invoice.objects.filter(order=order).count(), 0)

    def test_repeated_fulfillment_does_not_duplicate_invoice(self):
        order = self._paid_order_with_two_items()
        fulfill_order(order, previous_status='PENDING')
        fulfill_order(order, previous_status='PAID')
        self.assertEqual(Invoice.objects.filter(order=order).count(), 1)


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
class SubscriptionPaymentInvoiceGenerationTests(APITestCase):
    """Phase 3.5.5. _record_subscription_charge() -- Flow C invoice hook,
    driven through the real signature-verified webhook endpoint (same
    convention as SubscriptionChargeLedgerTests)."""

    def setUp(self):
        from unittest.mock import patch
        self._grace_patcher = patch('orders.tasks.notify_subscription_grace_period_expired.apply_async')
        self._grace_patcher.start()
        self.addCleanup(self._grace_patcher.stop)

        self.student = User.objects.create_user(username='inv_sub_student', password='password123')
        self.plan = SubscriptionPlan.objects.create(
            name='Invoice Test Plan', billing_interval='MONTHLY', price='999.00', razorpay_plan_id='plan_invoice_test',
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id='sub_invoice_test_1', razorpay_plan_id='plan_invoice_test',
        )
        self.url = reverse('razorpay-webhook')

    def _post_charged(self, event_id, payment_status='captured', amount=99900):
        payload = {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": subscription_entity(sub_id='sub_invoice_test_1', status_value='active', plan_id='plan_invoice_test')},
                "payment": {"entity": payment_entity(payment_id=f"pay_{event_id}", amount=amount, status_value=payment_status)},
            },
        }
        body, signature = sign_webhook_payload(payload)
        return self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )

    def test_successful_charge_creates_invoice(self):
        response = self._post_charged('evt_invoice_charged_1')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Invoice.objects.filter(subscription_payment__subscription=self.subscription).count(), 1)
        invoice = Invoice.objects.get(subscription_payment__subscription=self.subscription)
        self.assertEqual(invoice.amount, Decimal('999.00'))
        self.assertEqual(invoice.customer_id, self.student.id)

    def test_failed_charge_creates_no_invoice(self):
        response = self._post_charged('evt_invoice_charged_failed', payment_status='failed')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Invoice.objects.filter(subscription_payment__subscription=self.subscription).count(), 0)

    def test_duplicate_webhook_event_id_does_not_duplicate_invoice(self):
        self._post_charged('evt_invoice_charged_dup')
        self._post_charged('evt_invoice_charged_dup')  # same event_id -- WebhookEvent-level dedup
        self.assertEqual(Invoice.objects.filter(subscription_payment__subscription=self.subscription).count(), 1)


class MyInvoicesAPITests(APITestCase):
    """Phase 3.5.5. GET /api/finance/my-invoices/[<id>/]."""

    def setUp(self):
        self.list_url = reverse('finance-my-invoices')
        self.student = User.objects.create_user(username='inv_api_student', password='password123')
        self.other_student = User.objects.create_user(username='inv_api_other_student', password='password123')

        course = Course.objects.create(title='Invoice API Course', description='x', price=Decimal('499.00'))
        self.purchase = Purchase.objects.create(user=self.student, course=course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(self.purchase, previous_status='PENDING')
        self.invoice = Invoice.objects.get(purchase=self.purchase)

        other_purchase = Purchase.objects.create(user=self.other_student, course=course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(other_purchase, previous_status='PENDING')
        self.other_invoice = Invoice.objects.get(purchase=other_purchase)

    def test_customer_sees_own_invoice(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['id'], self.invoice.id)

    def test_customer_cannot_see_another_customers_invoice_in_list(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.list_url)
        ids = [row['id'] for row in response.data['results']]
        self.assertNotIn(self.other_invoice.id, ids)

    def test_customer_cannot_view_another_customers_invoice_detail(self):
        self.client.force_authenticate(user=self.student)
        detail_url = reverse('finance-my-invoice-detail', kwargs={'pk': self.other_invoice.id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_customer_can_view_own_invoice_detail(self):
        self.client.force_authenticate(user=self.student)
        detail_url = reverse('finance-my-invoice-detail', kwargs={'pk': self.invoice.id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['invoice_number'], self.invoice.invoice_number)

    def test_unauthenticated_denied(self):
        response = self.client.get(self.list_url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_no_razorpay_ids_leaked(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.list_url)
        self.assertNotIn('razorpay', str(response.content).lower())

    def test_pagination_page_size_respected(self):
        for i in range(12):
            course = Course.objects.create(title=f'Invoice Pagination Course {i}', description='x', price=Decimal('50.00'))
            purchase = Purchase.objects.create(user=self.student, course=course, amount=Decimal('50.00'), status=Purchase.Status.SUCCESS)
            fulfill_purchase(purchase, previous_status='PENDING')

        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.list_url)
        self.assertEqual(len(response.data['results']), 10)
        self.assertEqual(response.data['count'], 13)  # 1 original + 12 new


class AdminInvoiceAPITests(APITestCase):
    """Phase 3.5.5. GET /api/finance/admin/invoices/."""

    def setUp(self):
        self.url = reverse('finance-admin-invoices')
        self.admin = User.objects.create_user(username='inv_admin', password='password123', is_staff=True)
        self.student = User.objects.create_user(username='inv_admin_api_student', password='password123')

        self.course = Course.objects.create(title='Admin Invoice Course', description='x', price=Decimal('499.00'))
        self.purchase = Purchase.objects.create(user=self.student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(self.purchase, previous_status='PENDING')

    def test_admin_can_inspect_invoices(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['customer']['username'], self.student.username)
        self.assertEqual(response.data['results'][0]['source_type'], 'PURCHASE')

    def test_student_denied(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_denied(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_filter_by_customer_id(self):
        other_student = User.objects.create_user(username='inv_admin_api_other', password='password123')
        other_purchase = Purchase.objects.create(user=other_student, course=self.course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(other_purchase, previous_status='PENDING')

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url, {'customer_id': self.student.id})
        self.assertEqual(response.data['count'], 1)
        self.assertEqual(response.data['results'][0]['customer']['username'], self.student.username)

    def test_filter_by_source_type(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url, {'source_type': 'PURCHASE'})
        self.assertEqual(response.data['count'], 1)
        response_none = self.client.get(self.url, {'source_type': 'SUBSCRIPTION_PAYMENT'})
        self.assertEqual(response_none.data['count'], 0)


class InvoiceSourceLabelAPITests(APITestCase):
    """
    Invoice visibility frontend gap fix. source_label on
    MyInvoiceSerializer/AdminInvoiceSerializer -- a plain display string
    (course title / order number / subscription plan name) pulled
    directly from the invoice's already-existing related row. Purely
    observational -- no new calculation, no new business logic.
    """

    def setUp(self):
        self.list_url = reverse('finance-my-invoices')
        self.student = User.objects.create_user(username='label_student', password='password123')

    def test_source_label_for_purchase_invoice_is_course_title(self):
        course = Course.objects.create(title='Source Label Course', description='x', price=Decimal('499.00'))
        purchase = Purchase.objects.create(user=self.student, course=course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(purchase, previous_status='PENDING')

        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.list_url)
        self.assertEqual(response.data['results'][0]['source_label'], 'Source Label Course')

    def test_source_label_for_order_invoice_is_order_number(self):
        order = Order.objects.create(
            user=self.student, status=Order.Status.PAID,
            subtotal=Decimal('499.00'), total_amount=Decimal('499.00'), currency='INR',
        )
        course = Course.objects.create(title='Order Label Course', description='x', price=Decimal('499.00'))
        OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.COURSE, course=course,
            title_snapshot=course.title, unit_price=Decimal('499.00'), total_price=Decimal('499.00'),
        )
        fulfill_order(order, previous_status='PENDING')

        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.list_url)
        self.assertEqual(response.data['results'][0]['source_label'], order.order_number)

    @override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
    def test_source_label_for_subscription_payment_invoice_is_plan_name(self):
        from unittest.mock import patch
        grace_patcher = patch('orders.tasks.notify_subscription_grace_period_expired.apply_async')
        grace_patcher.start()
        self.addCleanup(grace_patcher.stop)

        plan = SubscriptionPlan.objects.create(
            name='Source Label Plan', billing_interval='MONTHLY', price='999.00', razorpay_plan_id='plan_label_test',
        )
        Subscription.objects.create(
            user=self.student, plan=plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id='sub_label_test_1', razorpay_plan_id='plan_label_test',
        )
        payload = {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": subscription_entity(sub_id='sub_label_test_1', status_value='active', plan_id='plan_label_test')},
                "payment": {"entity": payment_entity(payment_id='pay_label_test', amount=99900, status_value='captured')},
            },
        }
        body, signature = sign_webhook_payload(payload)
        self.client.post(
            reverse('razorpay-webhook'), data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID='evt_label_test_1',
        )

        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.list_url)
        self.assertEqual(response.data['results'][0]['source_label'], 'Source Label Plan')

    def test_source_label_included_in_admin_serializer_too(self):
        course = Course.objects.create(title='Admin Label Course', description='x', price=Decimal('499.00'))
        purchase = Purchase.objects.create(user=self.student, course=course, amount=Decimal('499.00'), status=Purchase.Status.SUCCESS)
        fulfill_purchase(purchase, previous_status='PENDING')

        admin = User.objects.create_user(username='label_admin', password='password123', is_staff=True)
        self.client.force_authenticate(user=admin)
        response = self.client.get(reverse('finance-admin-invoices'))
        self.assertEqual(response.data['results'][0]['source_label'], 'Admin Label Course')
