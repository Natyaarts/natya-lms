"""
Phase 3.4.3.1 -- Razorpay subscription webhook foundation. Extends the
EXISTING RazorpayWebhookView/WebhookEvent mechanism (Phase 3.2) to also
understand the 9 recurring-subscription lifecycle events, exactly the way
Phase 3.3 extended it to understand Order alongside Purchase -- there is
still only one webhook endpoint, one signature-verification path, one
WebhookEvent/idempotency mechanism.

Every test posts a genuinely HMAC-signed body (reusing sign_webhook_payload/
WEBHOOK_TEST_SECRET from orders.tests, the same helper the existing
RazorpayWebhookTests/OrderWebhookMappingTests use) rather than mocking
signature verification away. No live Razorpay credentials are used or
required anywhere in this file.

Deliberately NOT tested here (out of scope for this phase, per the brief):
access control, course entitlement, grace period, cancellation API,
invoices, refunds, ledger, payouts, coupons, tax, mobile, Celery Beat,
automatic cancellation, access revocation, Enrollment deletion -- several
tests below explicitly assert these do NOT happen as a result of a webhook.
"""
import hashlib
import hmac
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from orders.models import Subscription, SubscriptionPayment, SubscriptionPlan, WebhookEvent
from orders.tests import WEBHOOK_TEST_SECRET, sign_webhook_payload

User = get_user_model()


def subscription_entity(sub_id="sub_webhook_test_1", status_value="active", current_start=None,
                         current_end=None, ended_at=None, plan_id="plan_fake_abc"):
    """Shape confirmed against Razorpay's current webhook payload
    documentation: every subscription.* event carries payload.subscription.entity
    with (among others) these fields."""
    return {
        "id": sub_id, "entity": "subscription", "plan_id": plan_id, "customer_id": "cust_fake_1",
        "status": status_value, "current_start": current_start, "current_end": current_end,
        "ended_at": ended_at, "charge_at": None, "start_at": None, "end_at": None,
        "total_count": 1200, "paid_count": 1, "remaining_count": 1199, "quantity": 1,
    }


def payment_entity(payment_id="pay_webhook_charge_1", amount=99900, currency="INR",
                    status_value="captured", created_at=1735689600):
    """Shape confirmed against Razorpay's current webhook payload
    documentation: subscription.charged (and .completed) additionally carry
    payload.payment.entity with these fields."""
    return {
        "id": payment_id, "entity": "payment", "amount": amount, "currency": currency,
        "status": status_value, "method": "card", "created_at": created_at,
    }


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
class SubscriptionWebhookTests(APITestCase):
    def setUp(self):
        # Phase 3.4.5: entering PENDING/HALTED now schedules a grace-period
        # notification task via apply_async(eta=...) -- mocked here exactly
        # like courses/tests.py mocks send_class_reminder.apply_async
        # everywhere a LiveClass reminder could be scheduled, so tests never
        # attempt a real broker connection.
        self._grace_apply_async_patcher = patch('orders.tasks.notify_subscription_grace_period_expired.apply_async')
        self._grace_apply_async_patcher.start()
        self.addCleanup(self._grace_apply_async_patcher.stop)

        self.student = User.objects.create_user(username="sub_webhook_student", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Webhook Test Plan", billing_interval="MONTHLY", price="999.00",
            razorpay_plan_id="plan_fake_abc",
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.AUTHENTICATED,
            razorpay_subscription_id="sub_webhook_test_1", razorpay_plan_id="plan_fake_abc",
        )
        self.url = reverse('razorpay-webhook')

    def _post(self, payload_dict=None, raw_body=None, event_id="evt_sub_test_1", signature=None,
              secret=WEBHOOK_TEST_SECRET, include_event_id_header=True):
        if raw_body is None:
            body, real_signature = sign_webhook_payload(payload_dict, secret=secret)
        else:
            body = raw_body
            real_signature = hmac.new(key=secret.encode('utf-8'), msg=body.encode('utf-8'), digestmod=hashlib.sha256).hexdigest()
        sig = signature if signature is not None else real_signature
        headers = {'HTTP_X_RAZORPAY_SIGNATURE': sig}
        if include_event_id_header:
            headers['HTTP_X_RAZORPAY_EVENT_ID'] = event_id
        return self.client.post(self.url, data=body, content_type='application/json', **headers)

    def _lifecycle_payload(self, event, sub_status, **entity_kwargs):
        return {"event": event, "payload": {"subscription": {"entity": subscription_entity(status_value=sub_status, **entity_kwargs)}}}

    def _charged_payload(self, sub_status="active", **payment_kwargs):
        return {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": subscription_entity(status_value=sub_status)},
                "payment": {"entity": payment_entity(**payment_kwargs)},
            },
        }

    # ---- 1-4: signature/JSON/event-id error handling ----

    def test_invalid_signature_rejected(self):
        response = self._post(self._lifecycle_payload("subscription.activated", "active"), signature="0" * 64)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(WebhookEvent.objects.exists())
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.AUTHENTICATED)  # untouched

    def test_missing_signature_rejected(self):
        body, _ = sign_webhook_payload(self._lifecycle_payload("subscription.activated", "active"))
        response = self.client.post(self.url, data=body, content_type='application/json', HTTP_X_RAZORPAY_EVENT_ID="evt_no_sig")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_malformed_json_rejected(self):
        response = self._post(raw_body="{not valid json", event_id="evt_malformed_sub")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(WebhookEvent.objects.exists())

    def test_missing_event_id_handled_safely(self):
        response = self._post(self._lifecycle_payload("subscription.activated", "active"), include_event_id_header=False)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(WebhookEvent.objects.exists())

    # ---- 5: duplicate event id idempotency ----

    def test_duplicate_event_id_is_idempotent(self):
        payload = self._lifecycle_payload("subscription.activated", "active")
        r1 = self._post(payload, event_id="evt_sub_dup_1")
        r2 = self._post(payload, event_id="evt_sub_dup_1")
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(WebhookEvent.objects.filter(razorpay_event_id="evt_sub_dup_1").count(), 1)

    # ---- 6-15: each lifecycle event synchronizes local status ----

    def test_subscription_authenticated(self):
        response = self._post(self._lifecycle_payload("subscription.authenticated", "authenticated"), event_id="evt_authenticated_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.AUTHENTICATED)
        self.assertEqual(SubscriptionPayment.objects.count(), 0)  # no payment from this event
        event = WebhookEvent.objects.get(razorpay_event_id="evt_authenticated_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)
        self.assertEqual(event.event_type, "subscription.authenticated")

    def test_subscription_activated_updates_status_and_period(self):
        payload = self._lifecycle_payload(
            "subscription.activated", "active", current_start=1735689600, current_end=1738368000
        )
        response = self._post(payload, event_id="evt_activated_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        self.assertIsNotNone(self.subscription.current_period_start)
        self.assertIsNotNone(self.subscription.current_period_end)
        self.assertEqual(SubscriptionPayment.objects.count(), 0)  # no payment entity on .activated in this test

    def test_subscription_charged_creates_payment(self):
        response = self._post(self._charged_payload(), event_id="evt_charged_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)

        payment = SubscriptionPayment.objects.get(razorpay_payment_id="pay_webhook_charge_1")
        self.assertEqual(payment.subscription, self.subscription)
        self.assertEqual(payment.status, SubscriptionPayment.Status.SUCCESS)
        self.assertIsNotNone(payment.paid_at)

        event = WebhookEvent.objects.get(razorpay_event_id="evt_charged_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)

    def test_subscription_charged_does_not_duplicate_payment(self):
        payload = self._charged_payload()
        r1 = self._post(payload, event_id="evt_charged_dup_1")
        # Razorpay-style retry: identical body, identical event id.
        r2 = self._post(payload, event_id="evt_charged_dup_1")
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(SubscriptionPayment.objects.filter(razorpay_payment_id="pay_webhook_charge_1").count(), 1)
        self.assertEqual(WebhookEvent.objects.filter(razorpay_event_id="evt_charged_dup_1").count(), 1)

    def test_subscription_charged_with_different_event_id_same_payment_still_single_payment(self):
        # Simulates two genuinely different Razorpay event ids somehow
        # describing the same underlying charge (WebhookEvent-level dedup
        # can't catch this -- only the razorpay_payment_id uniqueness
        # check inside _record_subscription_charge does).
        r1 = self._post(self._charged_payload(), event_id="evt_charged_multi_1")
        r2 = self._post(self._charged_payload(), event_id="evt_charged_multi_2")
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(WebhookEvent.objects.count(), 2)  # both events recorded...
        self.assertEqual(SubscriptionPayment.objects.filter(razorpay_payment_id="pay_webhook_charge_1").count(), 1)  # ...but one payment

    def test_subscription_pending(self):
        response = self._post(self._lifecycle_payload("subscription.pending", "pending"), event_id="evt_pending_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.PENDING)
        # Phase 3.4.5: entering PENDING now starts Natya's own 3-day grace
        # period (access_until) -- see test_subscription_webhooks_grace_period.py
        # for the dedicated grace-period test coverage; this test only
        # confirms the status sync itself still works.
        self.assertIsNotNone(self.subscription.access_until)

    def test_subscription_halted(self):
        response = self._post(self._lifecycle_payload("subscription.halted", "halted"), event_id="evt_halted_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.HALTED)

    def test_subscription_cancelled_sets_cancelled_at_from_razorpay_timestamp(self):
        payload = self._lifecycle_payload("subscription.cancelled", "cancelled", ended_at=1735689600)
        response = self._post(payload, event_id="evt_cancelled_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.CANCELLED)
        self.assertIsNotNone(self.subscription.cancelled_at)

    def test_subscription_completed_does_not_delete_history(self):
        SubscriptionPayment.objects.create(
            subscription=self.subscription, razorpay_payment_id="pay_history_1",
            amount="999.00", status=SubscriptionPayment.Status.SUCCESS,
        )
        response = self._post(self._lifecycle_payload("subscription.completed", "completed"), event_id="evt_completed_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.COMPLETED)
        self.assertTrue(SubscriptionPayment.objects.filter(razorpay_payment_id="pay_history_1").exists())
        self.assertTrue(Subscription.objects.filter(pk=self.subscription.pk).exists())

    def test_subscription_completed_does_not_create_payment_even_with_payment_entity(self):
        payload = {
            "event": "subscription.completed",
            "payload": {
                "subscription": {"entity": subscription_entity(status_value="completed")},
                "payment": {"entity": payment_entity(payment_id="pay_final_charge_1")},
            },
        }
        response = self._post(payload, event_id="evt_completed_with_payment_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(SubscriptionPayment.objects.filter(razorpay_payment_id="pay_final_charge_1").exists())

    def test_subscription_paused(self):
        response = self._post(self._lifecycle_payload("subscription.paused", "paused"), event_id="evt_paused_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.PAUSED)

    def test_subscription_resumed(self):
        self.subscription.status = Subscription.Status.PAUSED
        self.subscription.save()
        response = self._post(self._lifecycle_payload("subscription.resumed", "active"), event_id="evt_resumed_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)

    # ---- 16-18: safe handling of unmatched/malformed subscription data ----

    def test_unknown_subscription_id_is_recorded_failed_no_fake_subscription_created(self):
        payload = self._lifecycle_payload("subscription.activated", "active", sub_id="sub_does_not_exist")
        response = self._post(payload, event_id="evt_unknown_sub_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # acked, not retried
        event = WebhookEvent.objects.get(razorpay_event_id="evt_unknown_sub_1")
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertIn("sub_does_not_exist", event.error_message)
        self.assertFalse(Subscription.objects.filter(razorpay_subscription_id="sub_does_not_exist").exists())
        self.assertEqual(Subscription.objects.count(), 1)  # only the one from setUp

    def test_missing_subscription_entity_recorded_failed(self):
        payload = {"event": "subscription.activated", "payload": {}}
        response = self._post(payload, event_id="evt_missing_entity_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_missing_entity_1")
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.AUTHENTICATED)  # untouched

    def test_missing_payment_entity_on_charged_event_recorded_failed(self):
        payload = {"event": "subscription.charged", "payload": {"subscription": {"entity": subscription_entity()}}}
        response = self._post(payload, event_id="evt_missing_payment_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_missing_payment_1")
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertEqual(SubscriptionPayment.objects.count(), 0)
        # The subscription status update itself is part of the same atomic
        # block as the (failing) payment recording -- a payload this
        # malformed rolls the whole reconciliation back rather than
        # applying a partial state change.
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.AUTHENTICATED)

    # ---- 19-20: actual Razorpay values are trusted, never invented ----

    def test_actual_razorpay_amount_and_currency_are_stored(self):
        # Deliberately different from the plan's own price (999.00) --
        # proves the webhook uses the payment entity's real amount, not
        # subscription.plan.price.
        response = self._post(self._charged_payload(amount=150000, currency="INR"), event_id="evt_amount_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment = SubscriptionPayment.objects.get(razorpay_payment_id="pay_webhook_charge_1")
        self.assertEqual(payment.amount, Decimal("1500.00"))
        self.assertEqual(payment.currency, "INR")

    def test_actual_razorpay_payment_id_is_stored(self):
        response = self._post(self._charged_payload(payment_id="pay_specific_id_999"), event_id="evt_payment_id_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment = SubscriptionPayment.objects.get(subscription=self.subscription)
        self.assertEqual(payment.razorpay_payment_id, "pay_specific_id_999")
        self.assertEqual(payment.razorpay_subscription_id, "sub_webhook_test_1")

    def test_failed_charge_is_stored_as_failed_not_success(self):
        response = self._post(self._charged_payload(status_value="failed"), event_id="evt_charge_failed_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment = SubscriptionPayment.objects.get(razorpay_payment_id="pay_webhook_charge_1")
        self.assertEqual(payment.status, SubscriptionPayment.Status.FAILED)
        self.assertIsNone(payment.paid_at)

    # ---- 22: concurrent/duplicate delivery does not create duplicate payment ----

    def test_concurrent_style_duplicate_charged_delivery_creates_exactly_one_payment(self):
        # True DB-level concurrency can only be meaningfully exercised
        # against Postgres (select_for_update() is a documented no-op on
        # SQLite, this project's test backend) -- matches the existing,
        # established precedent in PaymentHardeningPhase3Tests for the same
        # limitation. What IS verified here, backend-independently, is the
        # observable idempotency property the lock exists to guarantee:
        # two deliveries of the same charge, under two different Razorpay
        # event ids (the realistic "same underlying event redelivered with
        # a new envelope id" case), still produce exactly one
        # SubscriptionPayment row.
        payload = self._charged_payload(payment_id="pay_race_1")
        r1 = self._post(payload, event_id="evt_race_1")
        r2 = self._post(payload, event_id="evt_race_2")
        self.assertEqual(r1.status_code, status.HTTP_200_OK)
        self.assertEqual(r2.status_code, status.HTTP_200_OK)
        self.assertEqual(SubscriptionPayment.objects.filter(razorpay_payment_id="pay_race_1").count(), 1)

    # ---- 23: unauthorized/malformed webhook cannot create a payment ----

    def test_unauthorized_webhook_cannot_create_payment(self):
        response = self._post(self._charged_payload(payment_id="pay_should_never_exist"), signature="f" * 64)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(SubscriptionPayment.objects.filter(razorpay_payment_id="pay_should_never_exist").exists())
        self.assertFalse(WebhookEvent.objects.exists())

    # ---- Explicit non-goals: this phase never touches access/enrollment ----

    def test_no_enrollment_is_ever_created_by_subscription_webhooks(self):
        from courses.models import Course, Enrollment
        course = Course.objects.create(title="Webhook Access Course", description="x", price=100, is_published=True)
        self.plan.courses.add(course)
        self._post(self._charged_payload(), event_id="evt_no_access_1")
        self.assertFalse(Enrollment.objects.filter(user=self.student, course=course).exists())

    def test_access_until_only_set_by_pending_or_halted_never_by_other_events(self):
        # Phase 3.4.5: PENDING/HALTED now legitimately start the grace-period
        # clock (access_until) -- every OTHER lifecycle event must still
        # leave it untouched (starting fresh at None each time below).
        GRACE_PERIOD_EVENTS = {"subscription.pending", "subscription.halted"}
        for event, sub_status in [
            ("subscription.authenticated", "authenticated"), ("subscription.activated", "active"),
            ("subscription.pending", "pending"), ("subscription.halted", "halted"),
            ("subscription.cancelled", "cancelled"), ("subscription.completed", "completed"),
            ("subscription.paused", "paused"), ("subscription.resumed", "active"),
        ]:
            self.subscription.status = Subscription.Status.ACTIVE
            self.subscription.access_until = None
            self.subscription.save()
            self._post(self._lifecycle_payload(event, sub_status), event_id=f"evt_no_access_until_{event}")
            self.subscription.refresh_from_db()
            if event in GRACE_PERIOD_EVENTS:
                self.assertIsNotNone(self.subscription.access_until, f"{event} must start the grace period")
            else:
                self.assertIsNone(self.subscription.access_until, f"{event} must not set access_until")


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
class SubscriptionWebhookStatusFallbackAndStalenessTests(APITestCase):
    """
    Phase 3.4.3.1 audit fix -- Finding A (an unknown/missing Razorpay status
    in a webhook must never silently downgrade an existing subscription to
    CREATED) and Finding B (an earlier-generated webhook event arriving
    after a later one must never regress status/period fields -- Razorpay's
    delivery is at-least-once and explicitly NOT guaranteed to be ordered).

    Payloads here set a top-level `created_at` (the webhook ENVELOPE's own
    timestamp, confirmed against Razorpay's current webhook documentation --
    distinct from payload.subscription.entity.created_at) to exercise the
    staleness check directly; the existing SubscriptionWebhookTests class
    above never sets this field, so none of those 27 tests are affected by
    this fix (an event with no envelope timestamp is treated as
    "can't determine staleness, apply it" -- identical to pre-fix behavior).
    """

    def setUp(self):
        self._grace_apply_async_patcher = patch('orders.tasks.notify_subscription_grace_period_expired.apply_async')
        self._grace_apply_async_patcher.start()
        self.addCleanup(self._grace_apply_async_patcher.stop)

        self.student = User.objects.create_user(username="staleness_student", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Staleness Test Plan", billing_interval="MONTHLY", price="999.00",
            razorpay_plan_id="plan_staleness_abc",
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_staleness_test_1", razorpay_plan_id="plan_staleness_abc",
        )
        self.url = reverse('razorpay-webhook')

    def _post(self, payload_dict, event_id):
        body, signature = sign_webhook_payload(payload_dict)
        return self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )

    def _lifecycle_payload(self, event, sub_status, envelope_created_at=None, **entity_kwargs):
        payload = {
            "event": event,
            "payload": {"subscription": {
                "entity": subscription_entity(sub_id="sub_staleness_test_1", status_value=sub_status, **entity_kwargs)
            }},
        }
        if envelope_created_at is not None:
            payload["created_at"] = envelope_created_at
        return payload

    # ---- Finding A: unknown/missing status never downgrades an existing subscription ----

    def test_active_with_missing_status_remains_active(self):
        payload = self._lifecycle_payload("subscription.pending", None)  # status key present, value None
        response = self._post(payload, event_id="evt_missing_status_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)
        # Handled safely, not recorded as an error.
        event = WebhookEvent.objects.get(razorpay_event_id="evt_missing_status_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)

    def test_active_with_unknown_status_remains_active(self):
        payload = self._lifecycle_payload("subscription.pending", "some_future_status_razorpay_might_add")
        response = self._post(payload, event_id="evt_unknown_status_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)  # NOT downgraded to CREATED

    def test_halted_with_unknown_status_remains_halted(self):
        self.subscription.status = Subscription.Status.HALTED
        self.subscription.save()
        payload = self._lifecycle_payload("subscription.pending", "some_future_status_razorpay_might_add")
        response = self._post(payload, event_id="evt_unknown_status_halted_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.HALTED)

    def test_recognized_status_still_updates_correctly(self):
        payload = self._lifecycle_payload("subscription.halted", "halted")
        response = self._post(payload, event_id="evt_recognized_status_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.HALTED)

    def test_no_access_until_modification_from_unknown_status_event(self):
        payload = self._lifecycle_payload("subscription.pending", "totally_unrecognized")
        self._post(payload, event_id="evt_no_access_until_unknown_status_1")
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.access_until)

    # ---- Finding B: out-of-order delivery cannot regress status/period ----

    def test_stale_authenticated_event_cannot_downgrade_active(self):
        # The newer event (activated, envelope created_at=2000) is applied
        # first; a delayed, earlier-generated authenticated event
        # (created_at=1000) arrives afterward.
        newer = self._lifecycle_payload("subscription.activated", "active", envelope_created_at=2000)
        self._post(newer, event_id="evt_newer_activated_1")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)

        stale = self._lifecycle_payload("subscription.authenticated", "authenticated", envelope_created_at=1000)
        response = self._post(stale, event_id="evt_stale_authenticated_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)  # still acked
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)  # NOT downgraded

        event = WebhookEvent.objects.get(razorpay_event_id="evt_stale_authenticated_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)  # acked, not treated as an error

    def test_stale_pending_event_cannot_downgrade_active(self):
        newer = self._lifecycle_payload("subscription.activated", "active", envelope_created_at=2000)
        self._post(newer, event_id="evt_newer_activated_2")

        stale = self._lifecycle_payload("subscription.pending", "pending", envelope_created_at=1000)
        self._post(stale, event_id="evt_stale_pending_1")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)  # NOT downgraded

    def test_stale_event_cannot_overwrite_newer_period_timestamps(self):
        newer = self._lifecycle_payload(
            "subscription.activated", "active", envelope_created_at=2000,
            current_start=5000000, current_end=6000000,
        )
        self._post(newer, event_id="evt_newer_period_1")
        self.subscription.refresh_from_db()
        newer_start, newer_end = self.subscription.current_period_start, self.subscription.current_period_end

        stale = self._lifecycle_payload(
            "subscription.pending", "pending", envelope_created_at=1000,
            current_start=1000000, current_end=2000000,
        )
        self._post(stale, event_id="evt_stale_period_1")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.current_period_start, newer_start)  # unchanged
        self.assertEqual(self.subscription.current_period_end, newer_end)  # unchanged

    def test_genuinely_newer_event_still_updates_after_an_older_one(self):
        older = self._lifecycle_payload("subscription.authenticated", "authenticated", envelope_created_at=1000)
        self._post(older, event_id="evt_order_older_1")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.AUTHENTICATED)

        newer = self._lifecycle_payload("subscription.activated", "active", envelope_created_at=2000)
        self._post(newer, event_id="evt_order_newer_1")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)  # correctly updates forward

    def test_first_event_ever_for_a_subscription_always_applies(self):
        # No prior PROCESSED subscription event exists yet -- even with an
        # envelope created_at present, there's nothing to compare against.
        payload = self._lifecycle_payload("subscription.halted", "halted", envelope_created_at=999)
        response = self._post(payload, event_id="evt_first_ever_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.HALTED)

    def test_cancelled_subscription_cannot_be_reopened_by_any_later_event(self):
        cancelled = self._lifecycle_payload("subscription.cancelled", "cancelled", envelope_created_at=1000, ended_at=1000)
        self._post(cancelled, event_id="evt_terminal_cancel_1")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.CANCELLED)

        # Even a LATER-timestamped event must not revive a terminal subscription.
        later = self._lifecycle_payload("subscription.activated", "active", envelope_created_at=2000)
        response = self._post(later, event_id="evt_terminal_revive_attempt_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.CANCELLED)  # still cancelled

    def test_stale_event_does_not_block_subscription_charged_payment_recording(self):
        # A charge is real regardless of whether the bundled status
        # snapshot happens to be stale -- staleness must never suppress
        # payment recording.
        newer = self._lifecycle_payload("subscription.activated", "active", envelope_created_at=2000)
        self._post(newer, event_id="evt_charge_newer_first_1")

        charged_payload = {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": subscription_entity(sub_id="sub_staleness_test_1", status_value="pending")},
                "payment": {"entity": payment_entity(payment_id="pay_stale_but_real_1")},
            },
            "created_at": 1000,  # deliberately older than the already-applied event above
        }
        response = self._post(charged_payload, event_id="evt_stale_charge_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Payment recorded despite the stale status snapshot...
        self.assertTrue(SubscriptionPayment.objects.filter(razorpay_payment_id="pay_stale_but_real_1").exists())
        # ...but status was NOT regressed to "pending".
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)


@override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
class SubscriptionWebhookPaymentStatusAndPeriodDataTests(APITestCase):
    """
    Phase 3.4.3.2 -- payment-status mapping (Razorpay's Payment entity has
    5 real status values: created, authorized, captured, refunded, failed
    -- only an explicit 'captured'/'failed' maps to SUCCESS/FAILED; every
    other value maps to CREATED, never silently downgraded to FAILED) and
    period-timestamp handling (missing/malformed current_start/current_end
    must never blank out already-synced, valid period data).
    """

    def setUp(self):
        self._grace_apply_async_patcher = patch('orders.tasks.notify_subscription_grace_period_expired.apply_async')
        self._grace_apply_async_patcher.start()
        self.addCleanup(self._grace_apply_async_patcher.stop)

        self.student = User.objects.create_user(username="payment_status_student", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Payment Status Test Plan", billing_interval="MONTHLY", price="999.00",
            razorpay_plan_id="plan_paystatus_abc",
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_paystatus_test_1", razorpay_plan_id="plan_paystatus_abc",
        )
        self.url = reverse('razorpay-webhook')

    def _post(self, payload_dict, event_id):
        body, signature = sign_webhook_payload(payload_dict)
        return self.client.post(
            self.url, data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID=event_id,
        )

    def _charged_payload(self, sub_status="active", **payment_kwargs):
        return {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": subscription_entity(sub_id="sub_paystatus_test_1", status_value=sub_status)},
                "payment": {"entity": payment_entity(**payment_kwargs)},
            },
        }

    def _lifecycle_payload(self, event, sub_status, current_start=None, current_end=None):
        return {
            "event": event,
            "payload": {"subscription": {"entity": subscription_entity(
                sub_id="sub_paystatus_test_1", status_value=sub_status,
                current_start=current_start, current_end=current_end,
            )}},
        }

    # ---- payment status: only captured/failed are definitive ----

    def test_unknown_payment_status_recorded_as_created_not_failed(self):
        response = self._post(self._charged_payload(status_value="authorized"), event_id="evt_authorized_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment = SubscriptionPayment.objects.get(razorpay_payment_id="pay_webhook_charge_1")
        self.assertEqual(payment.status, SubscriptionPayment.Status.CREATED)
        self.assertNotEqual(payment.status, SubscriptionPayment.Status.FAILED)
        self.assertIsNone(payment.paid_at)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_authorized_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)  # not treated as an error

    def test_created_payment_status_recorded_as_created(self):
        response = self._post(self._charged_payload(status_value="created"), event_id="evt_pay_created_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment = SubscriptionPayment.objects.get(razorpay_payment_id="pay_webhook_charge_1")
        self.assertEqual(payment.status, SubscriptionPayment.Status.CREATED)

    def test_refunded_payment_status_not_treated_as_success_or_failure(self):
        response = self._post(self._charged_payload(status_value="refunded"), event_id="evt_pay_refunded_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment = SubscriptionPayment.objects.get(razorpay_payment_id="pay_webhook_charge_1")
        self.assertEqual(payment.status, SubscriptionPayment.Status.CREATED)
        self.assertNotEqual(payment.status, SubscriptionPayment.Status.REFUNDED)  # not this phase's job
        self.assertNotEqual(payment.status, SubscriptionPayment.Status.SUCCESS)

    def test_captured_status_still_maps_to_success_with_paid_at(self):
        response = self._post(self._charged_payload(status_value="captured"), event_id="evt_pay_captured_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment = SubscriptionPayment.objects.get(razorpay_payment_id="pay_webhook_charge_1")
        self.assertEqual(payment.status, SubscriptionPayment.Status.SUCCESS)
        self.assertIsNotNone(payment.paid_at)

    def test_failed_status_still_maps_to_failed(self):
        response = self._post(self._charged_payload(status_value="failed"), event_id="evt_pay_failed_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment = SubscriptionPayment.objects.get(razorpay_payment_id="pay_webhook_charge_1")
        self.assertEqual(payment.status, SubscriptionPayment.Status.FAILED)
        self.assertIsNone(payment.paid_at)

    # ---- missing payment id / amount specifically (payment entity present, one field absent) ----

    def test_payment_entity_present_but_missing_id_recorded_failed(self):
        payload = {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": subscription_entity(sub_id="sub_paystatus_test_1")},
                "payment": {"entity": {"amount": 99900, "currency": "INR", "status": "captured"}},  # no "id"
            },
        }
        response = self._post(payload, event_id="evt_missing_pay_id_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_missing_pay_id_1")
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertEqual(SubscriptionPayment.objects.count(), 0)

    def test_payment_entity_present_but_missing_amount_recorded_failed(self):
        payload = {
            "event": "subscription.charged",
            "payload": {
                "subscription": {"entity": subscription_entity(sub_id="sub_paystatus_test_1")},
                "payment": {"entity": {"id": "pay_no_amount_1", "currency": "INR", "status": "captured"}},  # no "amount"
            },
        }
        response = self._post(payload, event_id="evt_missing_amount_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_missing_amount_1")
        self.assertEqual(event.status, WebhookEvent.Status.FAILED)
        self.assertFalse(SubscriptionPayment.objects.filter(razorpay_payment_id="pay_no_amount_1").exists())

    # ---- malformed / missing timestamps never crash and never blank valid data ----

    def test_malformed_current_start_does_not_crash_and_is_not_applied(self):
        payload = self._lifecycle_payload("subscription.activated", "active", current_start="not-a-timestamp", current_end=1738368000)
        response = self._post(payload, event_id="evt_malformed_start_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        event = WebhookEvent.objects.get(razorpay_event_id="evt_malformed_start_1")
        self.assertEqual(event.status, WebhookEvent.Status.PROCESSED)  # doesn't fail the whole event
        self.subscription.refresh_from_db()
        self.assertIsNone(self.subscription.current_period_start)  # malformed -> not applied (no prior value either)
        self.assertIsNotNone(self.subscription.current_period_end)  # the valid sibling field still applies

    def test_malformed_timestamp_does_not_overwrite_existing_valid_period(self):
        valid = self._lifecycle_payload("subscription.activated", "active", current_start=1735689600, current_end=1738368000)
        self._post(valid, event_id="evt_valid_period_1")
        self.subscription.refresh_from_db()
        original_start = self.subscription.current_period_start
        self.assertIsNotNone(original_start)

        malformed = self._lifecycle_payload("subscription.pending", "pending", current_start="garbage", current_end=1738368000)
        self._post(malformed, event_id="evt_malformed_period_2")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.current_period_start, original_start)  # untouched

    def test_missing_period_fields_do_not_blank_out_existing_valid_period(self):
        valid = self._lifecycle_payload("subscription.activated", "active", current_start=1735689600, current_end=1738368000)
        self._post(valid, event_id="evt_valid_period_2")
        self.subscription.refresh_from_db()
        original_start, original_end = self.subscription.current_period_start, self.subscription.current_period_end

        # subscription.pending with no period info at all (null/absent, as
        # Razorpay legitimately sends for some lifecycle states).
        no_period = self._lifecycle_payload("subscription.pending", "pending", current_start=None, current_end=None)
        self._post(no_period, event_id="evt_no_period_1")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.current_period_start, original_start)  # NOT blanked to None
        self.assertEqual(self.subscription.current_period_end, original_end)

    def test_valid_period_data_still_updates_normally(self):
        payload = self._lifecycle_payload("subscription.activated", "active", current_start=1735689600, current_end=1738368000)
        response = self._post(payload, event_id="evt_normal_period_1")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.subscription.refresh_from_db()
        self.assertIsNotNone(self.subscription.current_period_start)
        self.assertIsNotNone(self.subscription.current_period_end)
