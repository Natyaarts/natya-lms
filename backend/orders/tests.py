import hmac
import hashlib
import json
from django.test import TestCase, override_settings
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from unittest.mock import patch
from courses.models import Course, Enrollment, Bundle
from orders.models import Purchase, WebhookEvent, Order, OrderItem
from notifications.models import Notification

User = get_user_model()

WEBHOOK_TEST_SECRET = 'test_webhook_secret_do_not_use_in_prod'


def sign_webhook_payload(payload_dict, secret=WEBHOOK_TEST_SECRET):
    """
    Computes a REAL HMAC-SHA256 signature over the exact serialized body,
    matching razorpay.Utility.verify_signature's own implementation
    (confirmed by reading the installed SDK source: hmac.new(key=bytes(secret),
    msg=bytes(body), digestmod=sha256).hexdigest()) -- so webhook tests
    exercise the genuine verification path rather than mocking it away.
    Returns (raw_body_str, signature_hex).
    """
    body = json.dumps(payload_dict)
    signature = hmac.new(
        key=secret.encode('utf-8'), msg=body.encode('utf-8'), digestmod=hashlib.sha256
    ).hexdigest()
    return body, signature

class PaymentNotificationTests(APITestCase):
    def setUp(self):
        # Create users
        self.student = User.objects.create_user(username="student_pay_test", password="password123")
        self.student.is_student = True
        self.student.save()

        self.staff_user = User.objects.create_user(username="staff_pay_test", password="password123")
        self.staff_user.is_superuser = True
        self.staff_user.save()

        # Create course
        self.course = Course.objects.create(
            title="Sitar Masterclass",
            description="Learn Sitar",
            price=1500.00,
            is_published=True
        )

        # Create a pending purchase
        self.purchase = Purchase.objects.create(
            user=self.student,
            course=self.course,
            amount=1500.00,
            status="PENDING",
            razorpay_order_id="order_dummy_123"
        )

    @patch('orders.views.client')
    def test_verify_payment_success_triggers_notifications(self, mock_client):
        self.client.force_authenticate(user=self.student)
        mock_client.utility.verify_payment_signature.return_value = True

        url = reverse('verify-payment')
        payload = {
            "razorpay_payment_id": "pay_dummy_123",
            "razorpay_order_id": "order_dummy_123",
            "razorpay_signature": "sig_dummy_123"
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "SUCCESS")

        # Payment Notification should exist
        pay_notif = Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").first()
        self.assertIsNotNone(pay_notif)
        self.assertEqual(pay_notif.title, "Payment successful")

        # Enrollment Notification should exist
        enroll_notif = Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").first()
        self.assertIsNotNone(enroll_notif)

    @patch('orders.views.client')
    def test_verify_payment_failure_creates_no_notifications(self, mock_client):
        self.client.force_authenticate(user=self.student)

        import razorpay.errors
        mock_client.utility.verify_payment_signature.side_effect = razorpay.errors.SignatureVerificationError("Invalid Signature")

        url = reverse('verify-payment')
        payload = {
            "razorpay_payment_id": "pay_dummy_123",
            "razorpay_order_id": "order_dummy_123",
            "razorpay_signature": "sig_dummy_invalid"
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "FAILED")
        self.assertEqual(Notification.objects.filter(recipient=self.student).count(), 0)

    def test_admin_mark_paid_triggers_notifications(self):
        self.client.force_authenticate(user=self.staff_user)

        url = reverse('purchases-admin-mark-paid', kwargs={'pk': self.purchase.pk})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "SUCCESS")

        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").count(), 1)

    def test_user_mark_purchase_paid_triggers_notification(self):
        self.client.force_authenticate(user=self.staff_user)

        url = reverse('admin-user-mark-purchase-paid', kwargs={'pk': self.student.pk})
        payload = {
            "purchase_id": self.purchase.id
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "SUCCESS")
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)

    def test_admin_mark_paid_repeated_calls_do_not_duplicate_notifications(self):
        self.client.force_authenticate(user=self.staff_user)

        url = reverse('purchases-admin-mark-paid', kwargs={'pk': self.purchase.pk})

        with self.captureOnCommitCallbacks(execute=True):
            response1 = self.client.post(url)
        self.assertEqual(response1.status_code, status.HTTP_200_OK)

        with self.captureOnCommitCallbacks(execute=True):
            response2 = self.client.post(url)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)

        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").count(), 1)


class PaymentHardeningPhase3Tests(APITestCase):
    """
    Phase 3.1: concurrency/idempotency/duplicate-prevention hardening on
    top of the existing payment flow. True row-level locking can only be
    meaningfully exercised against Postgres -- select_for_update() is a
    documented no-op on SQLite, the backend this test suite runs against --
    so "concurrent verification" here is tested via the *observable
    idempotency property* the lock exists to guarantee (never fulfilling
    twice, never re-verifying an already-SUCCESS purchase), which holds
    regardless of backend, rather than via literal thread-based races.
    """

    def setUp(self):
        self.student = User.objects.create_user(username="student_hardening_test", password="password123")
        self.student.is_student = True
        self.student.save()

        self.staff_user = User.objects.create_user(username="staff_hardening_test", password="password123")
        self.staff_user.is_superuser = True
        self.staff_user.save()

        self.course = Course.objects.create(
            title="Tabla Masterclass",
            description="Learn Tabla",
            price=2000.00,
            is_published=True
        )

        self.purchase = Purchase.objects.create(
            user=self.student,
            course=self.course,
            amount=2000.00,
            status="PENDING",
            razorpay_order_id="order_hardening_123"
        )

    # ---- Duplicate / concurrent verification ----

    @patch('orders.views.client')
    def test_duplicate_verification_requests_do_not_double_fulfill(self, mock_client):
        self.client.force_authenticate(user=self.student)
        mock_client.utility.verify_payment_signature.return_value = True

        url = reverse('verify-payment')
        payload = {
            "razorpay_payment_id": "pay_hardening_123",
            "razorpay_order_id": "order_hardening_123",
            "razorpay_signature": "sig_hardening_123"
        }

        with self.captureOnCommitCallbacks(execute=True):
            response1 = self.client.post(url, payload)
        self.assertEqual(response1.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_client.utility.verify_payment_signature.call_count, 1)

        # Duplicate call for the same order (client retry / double submit) --
        # must short-circuit on the already-SUCCESS check and NOT re-verify.
        with self.captureOnCommitCallbacks(execute=True):
            response2 = self.client.post(url, payload)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_client.utility.verify_payment_signature.call_count, 1)

        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").count(), 1)
        self.assertEqual(Enrollment.objects.filter(user=self.student, course=self.course).count(), 1)

    @patch('orders.views.client')
    def test_verification_after_admin_already_marked_paid_is_idempotent(self, mock_client):
        # Simulates a race between a client's verify-payment call and an
        # admin's mark-paid action landing first for the same purchase.
        self.client.force_authenticate(user=self.staff_user)
        admin_url = reverse('purchases-admin-mark-paid', kwargs={'pk': self.purchase.pk})
        with self.captureOnCommitCallbacks(execute=True):
            admin_response = self.client.post(admin_url)
        self.assertEqual(admin_response.status_code, status.HTTP_200_OK)

        self.client.force_authenticate(user=self.student)
        mock_client.utility.verify_payment_signature.return_value = True
        url = reverse('verify-payment')
        payload = {
            "razorpay_payment_id": "pay_hardening_123",
            "razorpay_order_id": "order_hardening_123",
            "razorpay_signature": "sig_hardening_123"
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Signature verification must be skipped entirely -- the purchase
        # was already SUCCESS by the time this request's lock was acquired.
        mock_client.utility.verify_payment_signature.assert_not_called()

        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").count(), 1)

    def test_mark_paid_on_already_success_purchase_is_idempotent(self):
        self.purchase.status = "SUCCESS"
        self.purchase.save()
        self.client.force_authenticate(user=self.staff_user)
        url = reverse('purchases-admin-mark-paid', kwargs={'pk': self.purchase.pk})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # No new PAYMENT notification -- previous_status was already SUCCESS
        # when fulfill_purchase/trigger_payment_success ran (pre-existing
        # idempotency guarantee, now also race-safe under the new lock).
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 0)

    def test_fulfill_purchase_called_twice_is_idempotent(self):
        from orders.services import fulfill_purchase
        self.purchase.status = "SUCCESS"
        self.purchase.save()
        with self.captureOnCommitCallbacks(execute=True):
            fulfill_purchase(self.purchase, previous_status="PENDING")
        with self.captureOnCommitCallbacks(execute=True):
            fulfill_purchase(self.purchase, previous_status="SUCCESS")
        self.assertEqual(Enrollment.objects.filter(user=self.student, course=self.course).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)

    # ---- Existing fulfill_purchase() callers still work under the new locking ----

    def test_assign_course_still_enrolls_and_notifies(self):
        self.client.force_authenticate(user=self.staff_user)
        new_course = Course.objects.create(title="Flute Basics", price=500.00, is_published=True)
        url = reverse('admin-user-assign-course', kwargs={'pk': self.student.pk})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {"course_id": new_course.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=new_course).exists())
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)

    def test_assign_course_still_rejects_duplicate_success_purchase(self):
        self.purchase.status = "SUCCESS"
        self.purchase.save()
        self.client.force_authenticate(user=self.staff_user)
        url = reverse('admin-user-assign-course', kwargs={'pk': self.student.pk})
        response = self.client.post(url, {"course_id": self.course.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_user_mark_purchase_paid_still_idempotent_on_repeat(self):
        self.client.force_authenticate(user=self.staff_user)
        url = reverse('admin-user-mark-purchase-paid', kwargs={'pk': self.student.pk})
        payload = {"purchase_id": self.purchase.id}

        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(url, payload)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)

    # ---- Purchase.status choices ----

    def test_invalid_purchase_status_rejected_by_full_clean(self):
        from django.core.exceptions import ValidationError
        bad_purchase = Purchase(user=self.student, course=self.course, amount=100, status="BOGUS_STATUS")
        with self.assertRaises(ValidationError):
            bad_purchase.full_clean()

    def test_purchase_status_choices_cover_exactly_the_values_in_use(self):
        # Confirmed via a repo-wide search before this change that these
        # three strings are the only ones ever read/written anywhere.
        self.assertEqual(set(Purchase.Status.values), {"PENDING", "SUCCESS", "FAILED"})

    # ---- Duplicate purchase / order creation prevention ----

    def test_create_order_rejects_when_already_purchased(self):
        self.purchase.status = "SUCCESS"
        self.purchase.save()
        self.client.force_authenticate(user=self.student)
        url = reverse('create-order')
        response = self.client.post(url, {"course_id": self.course.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already purchased", response.data['error'])

    @patch('orders.views.client')
    def test_create_order_still_allows_retry_after_pending(self, mock_client):
        # A PENDING purchase (e.g. an earlier abandoned checkout) must NOT
        # block a fresh order-creation attempt -- deliberate, see the
        # comment in CreateOrderView.post().
        mock_client.order.create.return_value = {"id": "order_retry_456"}
        self.client.force_authenticate(user=self.student)
        url = reverse('create-order')
        response = self.client.post(url, {"course_id": self.course.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Purchase.objects.filter(user=self.student, course=self.course).count(), 2)


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
class RazorpayWebhookTests(APITestCase):
    """
    Phase 3.2. Every test here posts a genuinely HMAC-signed body (see
    sign_webhook_payload) rather than mocking signature verification away --
    the signature check itself is exactly what's being tested in several of
    these. captureOnCommitCallbacks(execute=True) is used identically to
    every other payment test in this file, since fulfill_purchase's
    notification side effects are deferred via transaction.on_commit.
    """

    def setUp(self):
        self.student = User.objects.create_user(username="student_webhook_test", password="password123")
        self.student.is_student = True
        self.student.save()

        self.course = Course.objects.create(
            title="Webhook Course", description="x", price=999.00, is_published=True
        )

        self.purchase = Purchase.objects.create(
            user=self.student,
            course=self.course,
            amount=999.00,
            status="PENDING",
            razorpay_order_id="order_webhook_123",
        )

        self.url = reverse('razorpay-webhook')

    def _captured_payload(self, order_id="order_webhook_123", payment_id="pay_webhook_1"):
        return {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {
                "id": payment_id, "order_id": order_id, "amount": 99900, "currency": "INR", "status": "captured"
            }}},
        }

    def _order_paid_payload(self, order_id="order_webhook_123", payment_id="pay_webhook_1"):
        return {
            "event": "order.paid",
            "payload": {
                "payment": {"entity": {"id": payment_id, "order_id": order_id, "amount": 99900, "status": "captured"}},
                "order": {"entity": {"id": order_id, "amount": 99900, "status": "paid"}},
            },
        }

    def _failed_payload(self, order_id="order_webhook_123", payment_id="pay_webhook_fail_1"):
        return {
            "event": "payment.failed",
            "payload": {"payment": {"entity": {
                "id": payment_id, "order_id": order_id, "status": "failed",
                "error_code": "BAD_REQUEST_ERROR", "error_description": "Payment failed"
            }}},
        }

    def _post(self, payload_dict=None, raw_body=None, event_id="evt_test_1", signature=None, secret=WEBHOOK_TEST_SECRET):
        if raw_body is None:
            body, real_signature = sign_webhook_payload(payload_dict, secret=secret)
        else:
            # For malformed-payload tests: sign the exact (possibly
            # non-JSON) bytes so the request passes signature verification
            # and reaches the JSON-parsing step.
            body = raw_body
            real_signature = hmac.new(
                key=secret.encode('utf-8'), msg=body.encode('utf-8'), digestmod=hashlib.sha256
            ).hexdigest()
        sig = signature if signature is not None else real_signature
        return self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=sig, **{'HTTP_X_RAZORPAY_EVENT_ID': event_id} if event_id is not None else {}
        )

    # ---- Valid webhook / pending -> successful ----

    def test_valid_captured_webhook_fulfills_pending_purchase(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self._post(self._captured_payload(), event_id="evt_captured_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "SUCCESS")
        self.assertEqual(self.purchase.razorpay_payment_id, "pay_webhook_1")
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course).exists())
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").count(), 1)

        event = WebhookEvent.objects.get(razorpay_event_id="evt_captured_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)
        self.assertEqual(event.purchase_id, self.purchase.id)
        self.assertEqual(event.event_type, "payment.captured")
        self.assertIsNotNone(event.processed_at)

    def test_order_paid_event_also_fulfills(self):
        with self.captureOnCommitCallbacks(execute=True):
            response = self._post(self._order_paid_payload(), event_id="evt_order_paid_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "SUCCESS")

    # ---- Invalid signature ----

    def test_invalid_signature_rejected_and_not_persisted(self):
        response = self._post(self._captured_payload(), event_id="evt_bad_sig_1", signature="0" * 64)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "PENDING")
        # Nothing persisted for an unverified request -- prevents an
        # attacker from polluting the WebhookEvent table with fake ids.
        self.assertFalse(WebhookEvent.objects.filter(razorpay_event_id="evt_bad_sig_1").exists())

    def test_missing_signature_header_rejected(self):
        body, _ = sign_webhook_payload(self._captured_payload())
        response = self.client.post(
            self.url, data=body, content_type='application/json', HTTP_X_RAZORPAY_EVENT_ID="evt_no_sig_1"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(WebhookEvent.objects.filter(razorpay_event_id="evt_no_sig_1").exists())

    # ---- Duplicate / concurrent-style webhook handling ----

    def test_duplicate_webhook_same_event_id_does_not_double_fulfill(self):
        payload = self._captured_payload()
        with self.captureOnCommitCallbacks(execute=True):
            response1 = self._post(payload, event_id="evt_dup_1")
        self.assertEqual(response1.status_code, status.HTTP_200_OK)

        # Razorpay-style retry: identical body, identical event id.
        with self.captureOnCommitCallbacks(execute=True):
            response2 = self._post(payload, event_id="evt_dup_1")
        self.assertEqual(response2.status_code, status.HTTP_200_OK)

        self.assertEqual(WebhookEvent.objects.filter(razorpay_event_id="evt_dup_1").count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").count(), 1)
        self.assertEqual(Enrollment.objects.filter(user=self.student, course=self.course).count(), 1)

    def test_different_event_ids_for_same_order_do_not_double_fulfill(self):
        # Simulates Razorpay sending BOTH payment.captured and order.paid
        # for the same underlying transaction (two distinct, genuinely
        # different event ids -- WebhookEvent-level dedup can't catch this,
        # only fulfill_purchase's own status-transition guard can, and does).
        with self.captureOnCommitCallbacks(execute=True):
            r1 = self._post(self._captured_payload(), event_id="evt_multi_1")
        with self.captureOnCommitCallbacks(execute=True):
            r2 = self._post(self._order_paid_payload(), event_id="evt_multi_2")

        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(WebhookEvent.objects.count(), 2)  # both events recorded...
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)  # ...but fulfilled once
        self.assertEqual(Enrollment.objects.filter(user=self.student, course=self.course).count(), 1)

    def test_webhook_after_client_side_verify_payment_is_idempotent(self):
        # Realistic race: the client's own /verify-payment/ call completes
        # first (e.g. the browser was still open), then Razorpay's webhook
        # for the same payment arrives afterward.
        self.purchase.status = "SUCCESS"
        self.purchase.razorpay_payment_id = "pay_already_verified"
        self.purchase.save()

        with self.captureOnCommitCallbacks(execute=True):
            response = self._post(self._captured_payload(payment_id="pay_already_verified"), event_id="evt_after_verify_1")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 0)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_after_verify_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)

    # ---- Unknown event type ----

    def test_unknown_event_type_is_ignored_gracefully(self):
        payload = {"event": "refund.created", "payload": {"refund": {"entity": {"id": "rfnd_1"}}}}
        response = self._post(payload, event_id="evt_unknown_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "PENDING")  # untouched
        event = WebhookEvent.objects.get(razorpay_event_id="evt_unknown_1")
        self.assertEqual(event.status, WebhookEvent.Status.IGNORED)
        self.assertEqual(event.event_type, "refund.created")

    # ---- Malformed payload ----

    def test_malformed_json_payload_rejected(self):
        response = self._post(raw_body="{not valid json", event_id="evt_malformed_1")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(WebhookEvent.objects.filter(razorpay_event_id="evt_malformed_1").exists())

    # ---- Already-successful Purchase ----

    def test_captured_webhook_on_already_success_purchase_is_idempotent(self):
        self.purchase.status = "SUCCESS"
        self.purchase.save()
        with self.captureOnCommitCallbacks(execute=True):
            response = self._post(self._captured_payload(), event_id="evt_already_success_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 0)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_already_success_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)
        self.assertEqual(event.purchase_id, self.purchase.id)

    # ---- Failed payment webhook ----

    def test_failed_payment_webhook_moves_pending_purchase_to_failed(self):
        response = self._post(self._failed_payload(), event_id="evt_failed_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "FAILED")
        self.assertEqual(Notification.objects.filter(recipient=self.student).count(), 0)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_failed_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)

    def test_late_failed_webhook_does_not_downgrade_already_successful_purchase(self):
        # The student retried and succeeded before this FAILED webhook for
        # their first, failed attempt arrived (out-of-order delivery).
        self.purchase.status = "SUCCESS"
        self.purchase.save()
        response = self._post(self._failed_payload(), event_id="evt_late_failed_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "SUCCESS")  # unchanged, not downgraded

    # ---- Webhook for a nonexistent Purchase ----

    def test_webhook_for_nonexistent_purchase_is_acked_but_flagged(self):
        response = self._post(self._captured_payload(order_id="order_does_not_exist"), event_id="evt_no_purchase_1")
        # Still 200 -- acknowledged so Razorpay doesn't retry an event we've
        # durably recorded and flagged for manual review.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_no_purchase_1")
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertIn("order_does_not_exist", event.error_message)
        self.assertIsNone(event.purchase)

    # ---- Transaction rollback / error handling ----

    def test_missing_order_id_in_payload_is_recorded_as_failed_not_silently_dropped(self):
        payload = {"event": "payment.captured", "payload": {"payment": {"entity": {"id": "pay_no_order"}}}}
        response = self._post(payload, event_id="evt_no_order_id_1")
        # Still 200 at the HTTP layer (event durably recorded), but the
        # WebhookEvent itself is marked FAILED with a clear reason -- this
        # is the "transaction rollback/error handling" contract: a
        # processing exception never crashes the endpoint or silently
        # vanishes, it's captured and surfaced for admin review.
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "PENDING")  # untouched
        event = WebhookEvent.objects.get(razorpay_event_id="evt_no_order_id_1")
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertIn("order_id", event.error_message)

    # ---- Logging must never leak the signature or webhook secret ----

    def test_invalid_signature_log_does_not_contain_secret_or_signature(self):
        import logging
        logger = logging.getLogger('orders.views')
        with self.assertLogs(logger, level='WARNING') as captured:
            self._post(self._captured_payload(), event_id="evt_log_test_1", signature="deadbeef" * 8)
        log_output = " ".join(captured.output)
        self.assertNotIn(WEBHOOK_TEST_SECRET, log_output)
        self.assertNotIn("deadbeef", log_output)


# ===========================================================================
# PHASE 3.3: ORDERS & BUNDLES
# ===========================================================================

class BundleAdminAPITests(APITestCase):
    """Bundle CRUD + permissions, items 1-8 of the Phase 3.3 test list."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username="bundle_admin", password="password123")
        self.student = User.objects.create_user(username="bundle_student", password="password123")
        self.course1 = Course.objects.create(title="Bharatanatyam Basics", description="x", price=1000, is_published=True)
        self.course2 = Course.objects.create(title="Kathak Fundamentals", description="x", price=1200, is_published=True)

    def test_admin_can_create_bundle(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(reverse('bundle-list'), {
            "name": "Classical Dance Starter Pack",
            "price": "1800.00",
            "course_ids": [self.course1.id, self.course2.id],
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        bundle = Bundle.objects.get(pk=res.data['id'])
        self.assertEqual(bundle.courses.count(), 2)
        self.assertTrue(bundle.slug)  # auto-generated

    def test_admin_can_update_bundle(self):
        bundle = Bundle.objects.create(name="Original Name", price=500)
        bundle.courses.add(self.course1)
        self.client.force_authenticate(user=self.admin)
        res = self.client.patch(reverse('bundle-detail', kwargs={'pk': bundle.pk}), {"price": "750.00"}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        bundle.refresh_from_db()
        self.assertEqual(str(bundle.price), "750.00")

    def test_admin_can_activate_deactivate_bundle(self):
        bundle = Bundle.objects.create(name="Toggle Bundle", price=500, is_active=True)
        bundle.courses.add(self.course1)
        self.client.force_authenticate(user=self.admin)
        res = self.client.patch(reverse('bundle-detail', kwargs={'pk': bundle.pk}), {"is_active": False}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        bundle.refresh_from_db()
        self.assertFalse(bundle.is_active)

    def test_bundle_contains_courses(self):
        bundle = Bundle.objects.create(name="Multi Course Bundle", price=2000)
        bundle.courses.add(self.course1, self.course2)
        self.assertEqual(set(bundle.courses.values_list('id', flat=True)), {self.course1.id, self.course2.id})

    def test_duplicate_course_cannot_be_added_twice(self):
        bundle = Bundle.objects.create(name="Dup Bundle", price=1000)
        bundle.courses.add(self.course1)
        bundle.courses.add(self.course1)  # Django M2M .add() is idempotent
        self.assertEqual(bundle.courses.count(), 1)

    def test_student_cannot_manage_bundles(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.post(reverse('bundle-list'), {"name": "Should Fail", "price": "100.00"}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        bundle = Bundle.objects.create(name="Existing", price=500)
        res = self.client.patch(reverse('bundle-detail', kwargs={'pk': bundle.pk}), {"price": "1.00"}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_anyone_authenticated_or_not_can_read_bundles(self):
        bundle = Bundle.objects.create(name="Public Bundle", price=500, is_active=True)
        bundle.courses.add(self.course1)
        # Anonymous
        res = self.client.get(reverse('bundle-list'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        # Authenticated student
        self.client.force_authenticate(user=self.student)
        res = self.client.get(reverse('bundle-detail', kwargs={'pk': bundle.pk}))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_non_purchasable_bundle_does_not_hide_unpublished_course_but_flags_it(self):
        unpublished = Course.objects.create(title="Draft Course", description="x", price=100, is_published=False)
        bundle = Bundle.objects.create(name="Draft Bundle", price=100)
        bundle.courses.add(unpublished)
        self.assertFalse(bundle.is_purchasable)


class OrderCreationTests(APITestCase):
    """Price-security + creation-transactionality, items 9-14 and price/
    status-write-protection items 32-35."""

    def setUp(self):
        self.student = User.objects.create_user(username="order_student", password="password123")
        self.student.is_student = True
        self.student.save()

        self.other_student = User.objects.create_user(username="order_other_student", password="password123")

        self.course1 = Course.objects.create(title="Odissi Essentials", description="x", price=1500.00, is_published=True)
        self.course2 = Course.objects.create(title="Mohiniyattam Intro", description="x", price=2000.00, is_published=True)
        self.unpublished_course = Course.objects.create(title="Unpublished", description="x", price=999, is_published=False)

        self.bundle = Bundle.objects.create(name="Two Course Bundle", price=3000.00)
        self.bundle.courses.add(self.course1, self.course2)

        self.client.force_authenticate(user=self.student)

    @patch('orders.views.client')
    def test_course_order_calculates_price_server_side(self, mock_client):
        mock_client.order.create.return_value = {"id": "order_srv_price_1"}
        res = self.client.post(reverse('order-list'), {"items": [{"course_id": self.course1.id}]}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(res.data['subtotal']), "1500.00")
        self.assertEqual(str(res.data['total_amount']), "1500.00")
        mock_client.order.create.assert_called_once()
        call_kwargs = mock_client.order.create.call_args[0][0]
        self.assertEqual(call_kwargs['amount'], 150000)  # paise

    @patch('orders.views.client')
    def test_bundle_order_calculates_price_server_side(self, mock_client):
        mock_client.order.create.return_value = {"id": "order_srv_price_2"}
        res = self.client.post(reverse('order-list'), {"items": [{"bundle_id": self.bundle.id}]}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(res.data['total_amount']), "3000.00")

    @patch('orders.views.client')
    def test_client_submitted_price_is_ignored(self, mock_client):
        mock_client.order.create.return_value = {"id": "order_srv_price_3"}
        # Client tries to sneak in a bogus price/total -- must be ignored entirely.
        res = self.client.post(reverse('order-list'), {
            "items": [{"course_id": self.course1.id, "unit_price": "1.00", "price": "1.00"}],
            "total_amount": "1.00",
            "subtotal": "1.00",
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(res.data['total_amount']), "1500.00")  # real Course.price, not client's "1.00"

    def test_invalid_course_bundle_combination_rejected(self):
        # Neither course_id nor bundle_id
        res = self.client.post(reverse('order-list'), {"items": [{}]}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Both course_id AND bundle_id on the same item
        res = self.client.post(reverse('order-list'), {
            "items": [{"course_id": self.course1.id, "bundle_id": self.bundle.id}]
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Unpublished course
        res = self.client.post(reverse('order-list'), {"items": [{"course_id": self.unpublished_course.id}]}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Empty items list
        res = self.client.post(reverse('order-list'), {"items": []}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Duplicate course in the same order
        res = self.client.post(reverse('order-list'), {
            "items": [{"course_id": self.course1.id}, {"course_id": self.course1.id}]
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('orders.views.client')
    def test_order_total_calculated_correctly_for_multiple_items(self, mock_client):
        mock_client.order.create.return_value = {"id": "order_multi_1"}
        course3 = Course.objects.create(title="Third Course", description="x", price=500.00, is_published=True)
        res = self.client.post(reverse('order-list'), {
            "items": [{"course_id": course3.id}, {"bundle_id": self.bundle.id}]
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(str(res.data['total_amount']), "3500.00")  # 500 + 3000
        self.assertEqual(len(res.data['items']), 2)

    @patch('orders.views.client')
    def test_order_creation_is_transactional(self, mock_client):
        # Razorpay's API call fails -- the whole Order/OrderItem creation
        # must roll back, not leave an orphaned local Order.
        mock_client.order.create.side_effect = Exception("Razorpay is down")
        res = self.client.post(reverse('order-list'), {"items": [{"course_id": self.course1.id}]}, format='json')
        self.assertEqual(res.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(OrderItem.objects.count(), 0)

    def test_already_owned_course_order_rejected(self):
        Enrollment.objects.create(user=self.student, course=self.course1)
        res = self.client.post(reverse('order-list'), {"items": [{"course_id": self.course1.id}]}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    @patch('orders.views.client')
    def test_partial_bundle_ownership_does_not_block_bundle_order(self, mock_client):
        # Student already owns course1 -- must NOT block buying the bundle
        # (bundle ownership rules are deliberately looser than single-course).
        mock_client.order.create.return_value = {"id": "order_partial_1"}
        Enrollment.objects.create(user=self.student, course=self.course1)
        res = self.client.post(reverse('order-list'), {"items": [{"bundle_id": self.bundle.id}]}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    # ---- Security: student cannot manipulate order price/status, cannot see others' orders ----

    def test_user_cannot_access_another_users_order(self):
        order = Order.objects.create(user=self.other_student, subtotal=100, total_amount=100, status=Order.Status.PENDING)
        res = self.client.get(reverse('order-detail', kwargs={'pk': order.pk}))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_user_can_only_list_own_orders(self):
        own_order = Order.objects.create(user=self.student, subtotal=100, total_amount=100, status=Order.Status.PENDING)
        Order.objects.create(user=self.other_student, subtotal=200, total_amount=200, status=Order.Status.PENDING)
        res = self.client.get(reverse('order-list'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        ids = [o['id'] for o in res.data] if isinstance(res.data, list) else [o['id'] for o in res.data['results']]
        self.assertIn(own_order.id, ids)
        self.assertEqual(len(ids), 1)

    def test_user_cannot_modify_order_status_or_price_via_patch(self):
        order = Order.objects.create(user=self.student, subtotal=100, total_amount=100, status=Order.Status.PENDING)
        # PATCH isn't even a supported method on OrderViewSet.
        res = self.client.patch(reverse('order-detail', kwargs={'pk': order.pk}), {"status": "PAID", "total_amount": "1.00"}, format='json')
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.PENDING)
        self.assertEqual(str(order.total_amount), "100.00")

    def test_user_cannot_delete_an_order(self):
        order = Order.objects.create(user=self.student, subtotal=100, total_amount=100, status=Order.Status.PENDING)
        res = self.client.delete(reverse('order-detail', kwargs={'pk': order.pk}))
        self.assertEqual(res.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_order_response_fields_are_all_read_only_shaped(self):
        # Every field OrderSerializer exposes is read_only -- confirms there
        # is no client-writable path to status/price via create() either
        # (create() is a custom method that ignores anything except `items`).
        from orders.serializers import OrderSerializer
        self.assertEqual(set(OrderSerializer.Meta.read_only_fields), set(OrderSerializer.Meta.fields))


class OrderPaymentAndFulfillmentTests(APITestCase):
    """Items 15-26: Razorpay integration, fulfillment, idempotency."""

    def setUp(self):
        self.student = User.objects.create_user(username="fulfillment_student", password="password123")
        self.course1 = Course.objects.create(title="Fulfillment Course 1", description="x", price=1000.00, is_published=True)
        self.course2 = Course.objects.create(title="Fulfillment Course 2", description="x", price=1200.00, is_published=True)
        self.bundle = Bundle.objects.create(name="Fulfillment Bundle", price=2000.00)
        self.bundle.courses.add(self.course1, self.course2)
        self.client.force_authenticate(user=self.student)

    def _create_order(self, items, mock_client, razorpay_id="order_fulfill_1"):
        mock_client.order.create.return_value = {"id": razorpay_id}
        res = self.client.post(reverse('order-list'), {"items": items}, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return Order.objects.get(pk=res.data['id'])

    @patch('orders.views.client')
    def test_razorpay_order_uses_server_calculated_amount(self, mock_client):
        mock_client.order.create.return_value = {"id": "order_amt_check"}
        self.client.post(reverse('order-list'), {"items": [{"course_id": self.course1.id}]}, format='json')
        call_kwargs = mock_client.order.create.call_args[0][0]
        self.assertEqual(call_kwargs['amount'], 100000)  # 1000.00 * 100, never client-supplied

    def test_existing_purchase_checkout_still_works(self):
        # Legacy single-course flow, completely untouched by Phase 3.3.
        with patch('orders.views.client') as mock_client:
            mock_client.order.create.return_value = {"id": "order_legacy_1"}
            res = self.client.post(reverse('create-order'), {"course_id": self.course1.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(Purchase.objects.filter(user=self.student, course=self.course1, razorpay_order_id="order_legacy_1").exists())

    def test_existing_purchase_verification_still_works(self):
        purchase = Purchase.objects.create(user=self.student, course=self.course1, amount=1000, status="PENDING", razorpay_order_id="order_legacy_verify_1")
        with patch('orders.views.client') as mock_client:
            mock_client.utility.verify_payment_signature.return_value = True
            with self.captureOnCommitCallbacks(execute=True):
                res = self.client.post(reverse('verify-payment'), {
                    "razorpay_payment_id": "pay_legacy_1", "razorpay_order_id": "order_legacy_verify_1", "razorpay_signature": "sig_legacy_1"
                })
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        purchase.refresh_from_db()
        self.assertEqual(purchase.status, "SUCCESS")
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course1).exists())

    @patch('orders.views.client')
    def test_new_order_payment_can_be_reconciled_via_verify(self, mock_client):
        order = self._create_order([{"course_id": self.course1.id}], mock_client, "order_verify_1")
        mock_client.utility.verify_payment_signature.return_value = True
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(reverse('order-verify', kwargs={'pk': order.pk}), {
                "razorpay_payment_id": "pay_order_1", "razorpay_order_id": "order_verify_1", "razorpay_signature": "sig_order_1"
            })
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        order.refresh_from_db()
        self.assertEqual(order.status, "PAID")

    @patch('orders.views.client')
    def test_paid_course_order_grants_access(self, mock_client):
        order = self._create_order([{"course_id": self.course1.id}], mock_client, "order_grant_1")
        mock_client.utility.verify_payment_signature.return_value = True
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('order-verify', kwargs={'pk': order.pk}), {
                "razorpay_payment_id": "pay_grant_1", "razorpay_order_id": "order_grant_1", "razorpay_signature": "sig_grant_1"
            })
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course1).exists())
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)

    @patch('orders.views.client')
    def test_paid_bundle_grants_access_to_all_bundle_courses(self, mock_client):
        order = self._create_order([{"bundle_id": self.bundle.id}], mock_client, "order_bundle_grant_1")
        mock_client.utility.verify_payment_signature.return_value = True
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('order-verify', kwargs={'pk': order.pk}), {
                "razorpay_payment_id": "pay_bundle_1", "razorpay_order_id": "order_bundle_grant_1", "razorpay_signature": "sig_bundle_1"
            })
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course1).exists())
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course2).exists())
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)
        # Enrollment signal fires per-course, not per-order.
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").count(), 2)

    @patch('orders.views.client')
    def test_already_owned_bundle_course_does_not_duplicate_enrollment(self, mock_client):
        Enrollment.objects.create(user=self.student, course=self.course1)  # pre-existing access
        order = self._create_order([{"bundle_id": self.bundle.id}], mock_client, "order_partial_own_1")
        mock_client.utility.verify_payment_signature.return_value = True
        with self.captureOnCommitCallbacks(execute=True):
            res = self.client.post(reverse('order-verify', kwargs={'pk': order.pk}), {
                "razorpay_payment_id": "pay_partial_1", "razorpay_order_id": "order_partial_own_1", "razorpay_signature": "sig_partial_1"
            })
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(Enrollment.objects.filter(user=self.student, course=self.course1).count(), 1)  # not duplicated
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course2).exists())  # newly granted

    def test_repeated_fulfillment_is_idempotent(self):
        from orders.services import fulfill_order
        order = Order.objects.create(user=self.student, subtotal=1000, total_amount=1000, status=Order.Status.PAID)
        OrderItem.objects.create(order=order, item_type='COURSE', course=self.course1, title_snapshot=self.course1.title, unit_price=1000, total_price=1000)
        with self.captureOnCommitCallbacks(execute=True):
            fulfill_order(order, previous_status='PENDING')
        with self.captureOnCommitCallbacks(execute=True):
            fulfill_order(order, previous_status='PAID')
        self.assertEqual(Enrollment.objects.filter(user=self.student, course=self.course1).count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)

    @patch('orders.views.client')
    def test_duplicate_verify_call_does_not_duplicate_fulfillment(self, mock_client):
        order = self._create_order([{"course_id": self.course1.id}], mock_client, "order_dup_verify_1")
        mock_client.utility.verify_payment_signature.return_value = True
        payload = {"razorpay_payment_id": "pay_dup_1", "razorpay_order_id": "order_dup_verify_1", "razorpay_signature": "sig_dup_1"}
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse('order-verify', kwargs={'pk': order.pk}), payload)
        with self.captureOnCommitCallbacks(execute=True):
            res2 = self.client.post(reverse('order-verify', kwargs={'pk': order.pk}), payload)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_client.utility.verify_payment_signature.call_count, 1)  # second call short-circuited
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)

    @patch('orders.views.client')
    def test_failed_payment_does_not_grant_access(self, mock_client):
        order = self._create_order([{"course_id": self.course1.id}], mock_client, "order_failed_1")
        import razorpay.errors
        mock_client.utility.verify_payment_signature.side_effect = razorpay.errors.SignatureVerificationError("bad sig")
        res = self.client.post(reverse('order-verify', kwargs={'pk': order.pk}), {
            "razorpay_payment_id": "pay_failed_1", "razorpay_order_id": "order_failed_1", "razorpay_signature": "sig_failed_1"
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        order.refresh_from_db()
        self.assertEqual(order.status, "FAILED")
        self.assertFalse(Enrollment.objects.filter(user=self.student, course=self.course1).exists())

    def test_invalid_nonexistent_order_cannot_be_fulfilled(self):
        res = self.client.post(reverse('order-verify', kwargs={'pk': 999999}), {
            "razorpay_payment_id": "x", "razorpay_order_id": "y", "razorpay_signature": "z"
        })
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    @patch('orders.views.client')
    def test_verify_with_mismatched_razorpay_order_id_rejected(self, mock_client):
        # The pk resolves to a real order, but the razorpay_order_id in the
        # body doesn't match it -- must not verify against the wrong order.
        order = self._create_order([{"course_id": self.course1.id}], mock_client, "order_real_id_1")
        res = self.client.post(reverse('order-verify', kwargs={'pk': order.pk}), {
            "razorpay_payment_id": "pay_x", "razorpay_order_id": "order_totally_different", "razorpay_signature": "sig_x"
        })
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)


class OrderWebhookMappingTests(APITestCase):
    """Items 27-31: webhook must distinguish Purchase-mapped and Order-
    mapped events, and remain safe for unknown/malformed/error cases."""

    def setUp(self):
        self.student = User.objects.create_user(username="webhook_order_student", password="password123")
        self.course = Course.objects.create(title="Webhook Order Course", description="x", price=1000.00, is_published=True)
        self.bundle = Bundle.objects.create(name="Webhook Bundle", price=1000.00)
        self.bundle.courses.add(self.course)

        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=1000.00, status="PENDING", razorpay_order_id="order_legacy_webhook_1"
        )
        self.order = Order.objects.create(
            user=self.student, subtotal=1000, total_amount=1000, status=Order.Status.PENDING,
            razorpay_order_id="order_new_webhook_1"
        )
        OrderItem.objects.create(
            order=self.order, item_type='BUNDLE', bundle=self.bundle, title_snapshot=self.bundle.name,
            unit_price=1000, total_price=1000
        )

        self.url = reverse('razorpay-webhook')

    def _captured_payload(self, order_id, payment_id="pay_1"):
        return {
            "event": "payment.captured",
            "payload": {"payment": {"entity": {"id": payment_id, "order_id": order_id, "amount": 100000, "status": "captured"}}},
        }

    def _post(self, payload_dict, event_id):
        body, signature = sign_webhook_payload(payload_dict)
        return self.client.post(self.url, data=body, content_type='application/json', HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id)

    @override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
    def test_existing_purchase_webhook_behavior_remains_intact(self):
        with self.captureOnCommitCallbacks(execute=True):
            res = self._post(self._captured_payload("order_legacy_webhook_1"), "evt_order_regression_1")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "SUCCESS")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "PENDING")  # untouched
        event = WebhookEvent.objects.get(razorpay_event_id="evt_order_regression_1")
        self.assertEqual(event.purchase_id, self.purchase.id)

    @override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
    def test_new_order_webhook_maps_correctly(self):
        with self.captureOnCommitCallbacks(execute=True):
            res = self._post(self._captured_payload("order_new_webhook_1"), "evt_order_mapping_1")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "PAID")
        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "PENDING")  # untouched -- the legacy Purchase was never involved
        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course).exists())
        event = WebhookEvent.objects.get(razorpay_event_id="evt_order_mapping_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)
        self.assertIsNone(event.purchase)  # Order-mapped, not Purchase-mapped

    @override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
    def test_unknown_webhook_remains_safe_with_order_data_present(self):
        payload = {"event": "refund.created", "payload": {"refund": {"entity": {"id": "rfnd_1"}}}}
        res = self._post(payload, "evt_order_unknown_1")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "PENDING")

    @override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
    def test_malformed_webhook_remains_safe_with_order_data_present(self):
        body = "{not valid json"
        signature = hmac.new(key=WEBHOOK_TEST_SECRET.encode(), msg=body.encode(), digestmod=hashlib.sha256).hexdigest()
        res = self.client.post(self.url, data=body, content_type='application/json', HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID="evt_order_malformed_1")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "PENDING")

    @override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
    def test_webhook_for_order_id_matching_neither_purchase_nor_order_is_recorded_failed(self):
        res = self._post(self._captured_payload("order_matches_nothing"), "evt_order_no_match_1")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_order_no_match_1")
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertIn("order_matches_nothing", event.error_message)

    @override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
    def test_different_webhook_events_for_same_order_do_not_double_fulfill(self):
        order_paid_payload = {
            "event": "order.paid",
            "payload": {
                "payment": {"entity": {"id": "pay_order_paid_1", "order_id": "order_new_webhook_1", "amount": 100000, "status": "captured"}},
                "order": {"entity": {"id": "order_new_webhook_1", "amount": 100000, "status": "paid"}},
            },
        }
        with self.captureOnCommitCallbacks(execute=True):
            r1 = self._post(self._captured_payload("order_new_webhook_1"), "evt_order_multi_1")
        with self.captureOnCommitCallbacks(execute=True):
            r2 = self._post(order_paid_payload, "evt_order_multi_2")
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)
        self.assertEqual(Enrollment.objects.filter(user=self.student, course=self.course).count(), 1)
