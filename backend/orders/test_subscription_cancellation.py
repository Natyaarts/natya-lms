"""
Phase 3.4.5 -- student subscription cancellation + 3-day grace period.

Covers CancelSubscriptionView/SubscriptionMeView (API-level) and the grace-
period tracking added to RazorpayWebhookView._sync_subscription_state
(webhook-level, reusing test_subscription_webhooks.py's exact signing/
payload-building conventions). No second access-control implementation --
every access assertion here goes through courses/services/access.py's
existing, unmodified user_has_course_access(), exactly as production code
does.

Deliberately NOT tested here (out of scope, per the brief): refunds,
invoices, ledger, payouts, coupons, tax, mobile payments, pause/resume,
live-class entitlements, a full subscription dashboard.
"""
import hashlib
import hmac
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, Enrollment
from courses.services.access import user_has_course_access
from orders.models import Subscription, SubscriptionPlan, SubscriptionPayment, WebhookEvent
from orders.tests import WEBHOOK_TEST_SECRET, sign_webhook_payload
from orders.test_subscription_webhooks import subscription_entity, payment_entity

User = get_user_model()


class CancelSubscriptionAPITests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="cancel_student", password="password123")
        self.other_student = User.objects.create_user(username="cancel_other_student", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Cancel Test Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_cancel_1",
        )
        self.future_period_end = timezone.now() + timedelta(days=20)
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_cancel_test_1",
            current_period_start=timezone.now() - timedelta(days=10),
            current_period_end=self.future_period_end,
        )
        self.cancel_url = reverse('cancel-subscription')
        self.me_url = reverse('subscription-me')

    # 1. Active subscription can be cancelled by its owner.
    @patch('orders.views.client')
    def test_owner_can_cancel_active_subscription(self, mock_client):
        mock_client.subscription.cancel.return_value = {"id": "sub_cancel_test_1", "status": "active"}
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.cancel_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # 2. Cancellation calls the correct Razorpay operation.
    @patch('orders.views.client')
    def test_cancellation_calls_razorpay_cancel_at_cycle_end(self, mock_client):
        mock_client.subscription.cancel.return_value = {"id": "sub_cancel_test_1", "status": "active"}
        self.client.force_authenticate(user=self.student)
        self.client.post(self.cancel_url)
        mock_client.subscription.cancel.assert_called_once_with("sub_cancel_test_1", {"cancel_at_cycle_end": 1})

    # 3. Cancellation marks the local subscription correctly.
    @patch('orders.views.client')
    def test_cancellation_sets_local_fields(self, mock_client):
        mock_client.subscription.cancel.return_value = {"id": "sub_cancel_test_1", "status": "active"}
        self.client.force_authenticate(user=self.student)
        self.client.post(self.cancel_url)
        self.subscription.refresh_from_db()
        self.assertTrue(self.subscription.cancel_at_period_end)
        self.assertIsNotNone(self.subscription.cancelled_at)
        # Deliberately NOT immediately CANCELLED -- that's the existing,
        # unmodified subscription.cancelled webhook's job, confirmed once
        # Razorpay actually processes it at cycle end.
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)

    # 4/5. Cancellation does not immediately remove access; access remains
    # valid until paid period end.
    @patch('orders.views.client')
    def test_cancellation_does_not_immediately_remove_access(self, mock_client):
        mock_client.subscription.cancel.return_value = {"id": "sub_cancel_test_1", "status": "active"}
        course = Course.objects.create(title="Cancel Access Course", description="x", price=1, is_published=True)
        self.plan.courses.add(course)
        self.client.force_authenticate(user=self.student)
        self.client.post(self.cancel_url)
        self.subscription.refresh_from_db()
        self.assertTrue(user_has_course_access(self.student, course))

    # 6. Access stops after paid period end.
    def test_access_stops_after_paid_period_end(self):
        course = Course.objects.create(title="Cancel Expiry Course", description="x", price=1, is_published=True)
        self.plan.courses.add(course)
        self.subscription.cancel_at_period_end = True
        self.subscription.cancelled_at = timezone.now()
        self.subscription.current_period_end = timezone.now() - timedelta(days=1)
        self.subscription.status = Subscription.Status.CANCELLED
        self.subscription.save()
        self.assertFalse(user_has_course_access(self.student, course))

    # 7. Permanent Enrollment remains after subscription cancellation.
    @patch('orders.views.client')
    def test_permanent_enrollment_remains_after_cancellation(self, mock_client):
        mock_client.subscription.cancel.return_value = {"id": "sub_cancel_test_1", "status": "active"}
        course = Course.objects.create(title="Cancel Enrollment Course", description="x", price=1, is_published=True)
        Enrollment.objects.create(user=self.student, course=course)
        self.client.force_authenticate(user=self.student)
        self.client.post(self.cancel_url)
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=course).exists())
        self.assertTrue(user_has_course_access(self.student, course))

    # 8. User cannot cancel another user's subscription.
    @patch('orders.views.client')
    def test_user_cannot_cancel_another_users_subscription(self, mock_client):
        self.client.force_authenticate(user=self.other_student)
        response = self.client.post(self.cancel_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_client.subscription.cancel.assert_not_called()
        self.subscription.refresh_from_db()
        self.assertFalse(self.subscription.cancel_at_period_end)

    def test_unauthenticated_cannot_cancel(self):
        response = self.client.post(self.cancel_url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # 9. User cannot cancel a terminal subscription incorrectly.
    @patch('orders.views.client')
    def test_cannot_cancel_already_terminal_subscription(self, mock_client):
        self.subscription.status = Subscription.Status.CANCELLED
        self.subscription.save()
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.cancel_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_client.subscription.cancel.assert_not_called()

    # 16. Duplicate cancellation is handled safely/idempotently.
    @patch('orders.views.client')
    def test_duplicate_cancellation_is_idempotent(self, mock_client):
        mock_client.subscription.cancel.return_value = {"id": "sub_cancel_test_1", "status": "active"}
        self.client.force_authenticate(user=self.student)
        first = self.client.post(self.cancel_url)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        mock_client.subscription.cancel.reset_mock()

        second = self.client.post(self.cancel_url)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        mock_client.subscription.cancel.assert_not_called()  # no second Razorpay call

    @patch('orders.views.client')
    def test_razorpay_failure_does_not_mark_local_subscription_cancelled(self, mock_client):
        mock_client.subscription.cancel.side_effect = Exception("Razorpay unavailable")
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.cancel_url)
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.subscription.refresh_from_db()
        self.assertFalse(self.subscription.cancel_at_period_end)
        self.assertIsNone(self.subscription.cancelled_at)

    # 22. Unauthorized API manipulation cannot extend access/grace.
    @patch('orders.views.client')
    def test_client_supplied_fields_are_ignored(self, mock_client):
        mock_client.subscription.cancel.return_value = {"id": "sub_cancel_test_1", "status": "active"}
        forged_access_until = timezone.now() + timedelta(days=3650)
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.cancel_url, {
            "subscription_id": "sub_someone_elses",
            "access_until": forged_access_until.isoformat(),
            "current_period_end": forged_access_until.isoformat(),
            "status": "ACTIVE",
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.current_period_end, self.future_period_end)  # unchanged, not forged value
        self.assertIsNone(self.subscription.access_until)  # not set by the client-supplied value
        mock_client.subscription.cancel.assert_called_once_with("sub_cancel_test_1", {"cancel_at_cycle_end": 1})  # not the forged id

    # SubscriptionMeView coverage (needed by the frontend to show state).
    def test_me_endpoint_returns_current_subscription(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.me_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ACTIVE')
        self.assertNotIn('razorpay_subscription_id', response.data)
        self.assertNotIn('razorpay_plan_id', response.data)

    def test_me_endpoint_404_when_no_subscription(self):
        self.client.force_authenticate(user=self.other_student)
        response = self.client.get(self.me_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
class GracePeriodWebhookTests(APITestCase):
    def setUp(self):
        self._grace_apply_async_patcher = patch('orders.tasks.notify_subscription_grace_period_expired.apply_async')
        self.mock_apply_async = self._grace_apply_async_patcher.start()
        self.addCleanup(self._grace_apply_async_patcher.stop)

        self.student = User.objects.create_user(username="grace_student", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Grace Test Plan", billing_interval="MONTHLY", price="999.00", razorpay_plan_id="plan_grace_1",
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_grace_test_1",
            current_period_start=timezone.now() - timedelta(days=10),
            current_period_end=timezone.now() + timedelta(days=20),
        )
        self.course = Course.objects.create(title="Grace Access Course", description="x", price=1, is_published=True)
        self.plan.courses.add(self.course)
        self.url = reverse('razorpay-webhook')

    def _post(self, payload_dict, event_id):
        body, signature = sign_webhook_payload(payload_dict)
        return self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )

    def _lifecycle_payload(self, event, sub_status):
        return {
            "event": event,
            "payload": {"subscription": {"entity": subscription_entity(sub_id="sub_grace_test_1", status_value=sub_status)}},
        }

    # 10. Payment failure enters the intended grace-period behavior.
    def test_pending_status_starts_grace_period(self):
        response = self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_pending_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.PENDING)
        self.assertIsNotNone(self.subscription.access_until)

    # 11. Grace period lasts exactly 3 days according to the chosen
    # source-of-truth timestamp (access_until).
    def test_grace_period_is_exactly_three_days(self):
        before = timezone.now()
        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_timing_1")
        after = timezone.now()
        self.subscription.refresh_from_db()
        self.assertGreaterEqual(self.subscription.access_until, before + timedelta(days=3))
        self.assertLessEqual(self.subscription.access_until, after + timedelta(days=3))

    def test_grace_period_notification_scheduled_via_apply_async(self):
        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_schedule_1")
        self.mock_apply_async.assert_called_once()
        self.subscription.refresh_from_db()
        call = self.mock_apply_async.call_args
        self.assertEqual(call.kwargs['args'][0], self.subscription.id)
        self.assertEqual(call.kwargs['eta'], self.subscription.access_until)

    def test_repeated_pending_events_do_not_reset_grace_clock(self):
        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_repeat_1")
        self.subscription.refresh_from_db()
        first_deadline = self.subscription.access_until

        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_repeat_2")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.access_until, first_deadline)  # unchanged

    def test_pending_to_halted_does_not_reset_grace_clock(self):
        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_escalate_1")
        self.subscription.refresh_from_db()
        first_deadline = self.subscription.access_until

        self._post(self._lifecycle_payload("subscription.halted", "halted"), event_id="evt_grace_escalate_2")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.HALTED)
        self.assertEqual(self.subscription.access_until, first_deadline)  # unchanged

    # 12. User retains access during grace period.
    def test_access_retained_during_grace_period(self):
        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_access_1")
        self.assertTrue(user_has_course_access(self.student, self.course))

    # 13. User loses subscription access after grace period expires.
    def test_access_lost_after_grace_period_expires(self):
        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_expire_1")
        self.subscription.refresh_from_db()
        self.subscription.access_until = timezone.now() - timedelta(seconds=1)
        self.subscription.save()
        self.assertFalse(user_has_course_access(self.student, self.course))

    # 14. Recovery before grace expiry restores/continues normal access.
    def test_recovery_before_grace_expiry_clears_grace_and_continues_access(self):
        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_recover_1")
        self.subscription.refresh_from_db()
        self.assertIsNotNone(self.subscription.access_until)

        self._post(self._lifecycle_payload("subscription.activated", "active"), event_id="evt_grace_recover_2")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertIsNone(self.subscription.access_until)  # cleared -- current_period_end governs again
        self.assertTrue(user_has_course_access(self.student, self.course))

    def test_recovery_does_not_create_duplicate_subscription(self):
        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_dup_1")
        self._post(self._lifecycle_payload("subscription.activated", "active"), event_id="evt_grace_dup_2")
        self.assertEqual(Subscription.objects.filter(user=self.student).count(), 1)

    def test_recovery_via_charged_event_keeps_payment_records_consistent(self):
        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_charge_1")
        charged_payload = {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": subscription_entity(sub_id="sub_grace_test_1", status_value="active")},
                "payment": {"entity": payment_entity(payment_id="pay_grace_recovery_1")},
            },
        }
        body, signature = sign_webhook_payload(charged_payload)
        self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID="evt_grace_charge_2",
        )
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.access_until)
        self.assertEqual(SubscriptionPayment.objects.filter(subscription=self.subscription).count(), 1)
        self.assertTrue(SubscriptionPayment.objects.filter(razorpay_payment_id="pay_grace_recovery_1").exists())

    # 15. Grace expiry does not delete Enrollment.
    def test_grace_expiry_does_not_delete_enrollment(self):
        Enrollment.objects.create(user=self.student, course=self.course)
        self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_grace_enroll_1")
        self.subscription.refresh_from_db()
        self.subscription.access_until = timezone.now() - timedelta(seconds=1)
        self.subscription.save()
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course).exists())
        self.assertTrue(user_has_course_access(self.student, self.course))  # via Enrollment, independent of subscription
