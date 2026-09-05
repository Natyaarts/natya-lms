"""
Phase 3.4.2 -- Razorpay Subscription creation + checkout signature
verification. Covers CreateSubscriptionView and VerifySubscriptionPaymentView
only: server-priced/server-planned subscription creation against a
Razorpay-linked SubscriptionPlan, and verification of the first checkout
payment's signature. Deliberately does NOT test course access, webhooks,
cancellation, or recurring-payment reconciliation -- none of that exists
yet (see orders/views.py's Phase 3.4.2 section docstring).

Razorpay is mocked throughout via @patch('orders.views.client') -- the same
module-level client singleton every other orders test suite patches. No
test depends on live Razorpay credentials.
"""
from decimal import Decimal
from unittest.mock import patch

import razorpay.errors
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course
from orders.models import SubscriptionPlan, Subscription, SubscriptionPayment

User = get_user_model()


def fake_razorpay_subscription(sub_id="sub_fake_123", rzp_status="created", current_start=None, current_end=None):
    return {
        "id": sub_id,
        "entity": "subscription",
        "plan_id": "plan_fake_abc",
        "status": rzp_status,
        "current_start": current_start,
        "current_end": current_end,
        "ended_at": None,
        "quantity": 1,
        "total_count": 1200,
    }


class CreateSubscriptionViewTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="sub_checkout_student", password="password123")
        self.other_student = User.objects.create_user(username="sub_checkout_other", password="password123")
        self.course = Course.objects.create(title="Kathak Basics", description="x", price=1000, is_published=True)
        self.plan = SubscriptionPlan.objects.create(
            name="Monthly Kathak", billing_interval=SubscriptionPlan.BillingInterval.MONTHLY,
            price="999.00", razorpay_plan_id="plan_real_xyz",
        )
        self.yearly_plan = SubscriptionPlan.objects.create(
            name="Yearly Kathak", billing_interval=SubscriptionPlan.BillingInterval.YEARLY,
            price="9999.00", razorpay_plan_id="plan_real_yearly",
        )
        self.url = reverse('create-subscription')

    # 1. Unauthenticated create rejected.
    def test_unauthenticated_create_rejected(self):
        response = self.client.post(self.url, {"plan_id": self.plan.id})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(Subscription.objects.count(), 0)

    # 2. Authenticated create succeeds.
    @patch('orders.views.client')
    def test_authenticated_create_succeeds(self, mock_client):
        mock_client.subscription.create.return_value = fake_razorpay_subscription()
        self.client.force_authenticate(user=self.student)

        response = self.client.post(self.url, {"plan_id": self.plan.id})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["subscription_id"], "sub_fake_123")
        self.assertEqual(response.data["plan_id"], self.plan.id)
        self.assertIn("razorpay_key_id", response.data)
        self.assertEqual(Subscription.objects.filter(user=self.student).count(), 1)

    # 3. Invalid plan rejected.
    @patch('orders.views.client')
    def test_invalid_plan_rejected(self, mock_client):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, {"plan_id": 999999})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_client.subscription.create.assert_not_called()

    def test_missing_plan_id_rejected(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.url, {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # 4. Inactive plan rejected.
    @patch('orders.views.client')
    def test_inactive_plan_rejected(self, mock_client):
        self.plan.is_active = False
        self.plan.save()
        self.client.force_authenticate(user=self.student)

        response = self.client.post(self.url, {"plan_id": self.plan.id})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_client.subscription.create.assert_not_called()

    # 5. Plan without razorpay_plan_id rejected.
    @patch('orders.views.client')
    def test_plan_without_razorpay_plan_id_rejected(self, mock_client):
        unlinked_plan = SubscriptionPlan.objects.create(
            name="Unlinked Plan", billing_interval="MONTHLY", price="500.00"
        )
        self.client.force_authenticate(user=self.student)

        response = self.client.post(self.url, {"plan_id": unlinked_plan.id})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_client.subscription.create.assert_not_called()

    # 6. Client cannot override Razorpay plan ID / amount / currency.
    @patch('orders.views.client')
    def test_client_cannot_override_plan_id_amount_currency(self, mock_client):
        mock_client.subscription.create.return_value = fake_razorpay_subscription()
        self.client.force_authenticate(user=self.student)

        self.client.post(self.url, {
            "plan_id": self.plan.id,
            "razorpay_plan_id": "attacker_supplied_plan_id",
            "amount": "1",
            "currency": "USD",
        })

        called_data = mock_client.subscription.create.call_args[0][0]
        self.assertEqual(called_data["plan_id"], "plan_real_xyz")  # the server-side plan's own id, not the client's

    # 7. Existing active subscription blocks duplicate.
    @patch('orders.views.client')
    def test_existing_active_subscription_blocks_duplicate(self, mock_client):
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE)
        self.client.force_authenticate(user=self.student)

        response = self.client.post(self.url, {"plan_id": self.plan.id})

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_client.subscription.create.assert_not_called()
        self.assertEqual(Subscription.objects.filter(user=self.student).count(), 1)

    # A cancelled/expired/completed subscription must NOT block a new one.
    @patch('orders.views.client')
    def test_terminal_subscription_does_not_block_new_one(self, mock_client):
        Subscription.objects.create(user=self.student, plan=self.plan, status=Subscription.Status.CANCELLED)
        mock_client.subscription.create.return_value = fake_razorpay_subscription(sub_id="sub_second")
        self.client.force_authenticate(user=self.student)

        response = self.client.post(self.url, {"plan_id": self.plan.id})

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Subscription.objects.filter(user=self.student).count(), 2)

    # 8. Razorpay API failure handled safely.
    @patch('orders.views.client')
    def test_razorpay_api_failure_handled_safely(self, mock_client):
        mock_client.subscription.create.side_effect = razorpay.errors.BadRequestError("Invalid plan_id")
        self.client.force_authenticate(user=self.student)

        response = self.client.post(self.url, {"plan_id": self.plan.id})

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        # No orphaned local row -- the whole atomic() block rolled back.
        self.assertEqual(Subscription.objects.filter(user=self.student).count(), 0)

    # 9. Local Subscription stores razorpay_subscription_id and razorpay_plan_id.
    @patch('orders.views.client')
    def test_local_subscription_stores_razorpay_ids(self, mock_client):
        mock_client.subscription.create.return_value = fake_razorpay_subscription(sub_id="sub_store_me")
        self.client.force_authenticate(user=self.student)

        self.client.post(self.url, {"plan_id": self.plan.id})

        sub = Subscription.objects.get(user=self.student)
        self.assertEqual(sub.razorpay_subscription_id, "sub_store_me")
        self.assertEqual(sub.razorpay_plan_id, "plan_real_xyz")
        self.assertEqual(sub.plan_id, self.plan.id)

    # 10. Correct billing interval/count sent to Razorpay.
    @patch('orders.views.client')
    def test_monthly_plan_sends_correct_total_count(self, mock_client):
        mock_client.subscription.create.return_value = fake_razorpay_subscription()
        self.client.force_authenticate(user=self.student)

        self.client.post(self.url, {"plan_id": self.plan.id})

        called_data = mock_client.subscription.create.call_args[0][0]
        self.assertEqual(called_data["total_count"], 1200)
        self.assertNotIn("end_at", called_data)

    @patch('orders.views.client')
    def test_yearly_plan_sends_correct_total_count(self, mock_client):
        mock_client.subscription.create.return_value = fake_razorpay_subscription()
        self.client.force_authenticate(user=self.student)

        self.client.post(self.url, {"plan_id": self.yearly_plan.id})

        called_data = mock_client.subscription.create.call_args[0][0]
        self.assertEqual(called_data["total_count"], 100)
        self.assertNotIn("end_at", called_data)

    # Status must be read from Razorpay's actual response, never hardcoded.
    @patch('orders.views.client')
    def test_status_stored_from_actual_razorpay_response_not_hardcoded(self, mock_client):
        mock_client.subscription.create.return_value = fake_razorpay_subscription(rzp_status="created")
        self.client.force_authenticate(user=self.student)

        self.client.post(self.url, {"plan_id": self.plan.id})

        sub = Subscription.objects.get(user=self.student)
        self.assertEqual(sub.status, Subscription.Status.CREATED)
        self.assertNotEqual(sub.status, Subscription.Status.ACTIVE)

    # Best-effort Razorpay cleanup when Razorpay succeeds but the local save fails.
    @patch('orders.views.client')
    def test_razorpay_cleanup_attempted_when_local_save_fails_after_razorpay_success(self, mock_client):
        mock_client.subscription.create.return_value = fake_razorpay_subscription(sub_id="sub_needs_cleanup")
        self.client.force_authenticate(user=self.student)

        # Force the final subscription.save() (inside the atomic block,
        # AFTER Razorpay has already returned successfully) to blow up --
        # simulates a DB error at that exact point.
        with patch('orders.models.Subscription.save', side_effect=[None, Exception("simulated DB failure")]):
            # The first save() call is the initial Subscription.objects.create()
            # (must succeed, matching the "before Razorpay" state); the
            # second is the post-Razorpay update (must fail).
            response = self.client.post(self.url, {"plan_id": self.plan.id})

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        mock_client.subscription.cancel.assert_called_once_with("sub_needs_cleanup")
        # Whole transaction rolled back -- no orphaned local row either.
        self.assertEqual(Subscription.objects.filter(user=self.student).count(), 0)


class VerifySubscriptionPaymentViewTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="verify_sub_student", password="password123")
        self.other_student = User.objects.create_user(username="verify_sub_other", password="password123")
        self.plan = SubscriptionPlan.objects.create(
            name="Verify Plan", billing_interval="MONTHLY", price="500.00", razorpay_plan_id="plan_verify_abc",
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.CREATED,
            razorpay_subscription_id="sub_verify_me",
        )
        self.url = reverse('verify-subscription')
        self.valid_payload = {
            "razorpay_payment_id": "pay_verify_1",
            "razorpay_subscription_id": "sub_verify_me",
            "razorpay_signature": "sig_verify_1",
        }

    # 11. Unauthenticated verify rejected.
    def test_unauthenticated_verify_rejected(self):
        response = self.client.post(self.url, self.valid_payload)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # 12. Ownership enforced -- (also covers #18, same check).
    @patch('orders.views.client')
    def test_ownership_enforced_other_student_rejected(self, mock_client):
        self.client.force_authenticate(user=self.other_student)

        response = self.client.post(self.url, self.valid_payload)

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_client.utility.verify_subscription_payment_signature.assert_not_called()
        self.assertEqual(SubscriptionPayment.objects.count(), 0)

    # 13. Missing signature fields rejected.
    def test_missing_fields_rejected(self):
        self.client.force_authenticate(user=self.student)
        for missing_field in ("razorpay_payment_id", "razorpay_subscription_id", "razorpay_signature"):
            payload = {k: v for k, v in self.valid_payload.items() if k != missing_field}
            response = self.client.post(self.url, payload)
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    # 14. Invalid signature rejected.
    @patch('orders.views.client')
    def test_invalid_signature_rejected(self, mock_client):
        mock_client.utility.verify_subscription_payment_signature.side_effect = (
            razorpay.errors.SignatureVerificationError("bad signature")
        )
        self.client.force_authenticate(user=self.student)

        response = self.client.post(self.url, self.valid_payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(SubscriptionPayment.objects.count(), 0)

    # 19. Invalid/unknown Razorpay subscription ID rejected safely.
    def test_unknown_razorpay_subscription_id_rejected(self):
        self.client.force_authenticate(user=self.student)
        payload = {**self.valid_payload, "razorpay_subscription_id": "sub_does_not_exist"}
        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    # 15. Valid signature accepted / 16. creates SubscriptionPayment.
    @patch('orders.views.client')
    def test_valid_signature_verifies_and_creates_payment(self, mock_client):
        mock_client.utility.verify_subscription_payment_signature.return_value = True
        mock_client.subscription.fetch.return_value = fake_razorpay_subscription(
            sub_id="sub_verify_me", rzp_status="authenticated",
        )
        self.client.force_authenticate(user=self.student)

        response = self.client.post(self.url, self.valid_payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        payment = SubscriptionPayment.objects.get(razorpay_payment_id="pay_verify_1")
        self.assertEqual(payment.subscription, self.subscription)
        self.assertEqual(payment.status, SubscriptionPayment.Status.SUCCESS)
        payment.refresh_from_db()
        self.assertEqual(payment.amount, Decimal("500.00"))  # from the server-side plan, not the client
        self.assertEqual(payment.currency, "INR")

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.AUTHENTICATED)

    # Verified call correctly uses the LOCAL stored subscription id, not
    # whatever the client happened to send.
    @patch('orders.views.client')
    def test_signature_verification_uses_stored_subscription_id(self, mock_client):
        mock_client.utility.verify_subscription_payment_signature.return_value = True
        mock_client.subscription.fetch.return_value = fake_razorpay_subscription(sub_id="sub_verify_me")
        self.client.force_authenticate(user=self.student)

        self.client.post(self.url, self.valid_payload)

        called_params = mock_client.utility.verify_subscription_payment_signature.call_args[0][0]
        self.assertEqual(called_params["razorpay_subscription_id"], "sub_verify_me")
        self.assertEqual(called_params["razorpay_payment_id"], "pay_verify_1")
        self.assertEqual(called_params["razorpay_signature"], "sig_verify_1")

    # 17. Duplicate verification is idempotent.
    @patch('orders.views.client')
    def test_duplicate_verification_is_idempotent(self, mock_client):
        mock_client.utility.verify_subscription_payment_signature.return_value = True
        mock_client.subscription.fetch.return_value = fake_razorpay_subscription(sub_id="sub_verify_me")
        self.client.force_authenticate(user=self.student)

        first = self.client.post(self.url, self.valid_payload)
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(SubscriptionPayment.objects.count(), 1)

        mock_client.utility.verify_subscription_payment_signature.reset_mock()
        second = self.client.post(self.url, self.valid_payload)

        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(SubscriptionPayment.objects.count(), 1)  # no duplicate row
        # The idempotency short-circuit fires before re-verifying.
        mock_client.utility.verify_subscription_payment_signature.assert_not_called()

    # Status update reflects only what Razorpay's fetch actually reports --
    # never guessed as ACTIVE.
    @patch('orders.views.client')
    def test_status_after_verification_comes_from_razorpay_fetch_not_guessed(self, mock_client):
        mock_client.utility.verify_subscription_payment_signature.return_value = True
        mock_client.subscription.fetch.return_value = fake_razorpay_subscription(
            sub_id="sub_verify_me", rzp_status="active",
        )
        self.client.force_authenticate(user=self.student)

        self.client.post(self.url, self.valid_payload)

        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.status, Subscription.Status.ACTIVE)

    # If Razorpay's post-verification fetch fails, fail safely -- no
    # SubscriptionPayment is fabricated, and the caller can safely retry.
    @patch('orders.views.client')
    def test_fetch_failure_after_signature_verification_fails_safely(self, mock_client):
        mock_client.utility.verify_subscription_payment_signature.return_value = True
        mock_client.subscription.fetch.side_effect = Exception("Razorpay unavailable")
        self.client.force_authenticate(user=self.student)

        response = self.client.post(self.url, self.valid_payload)

        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(SubscriptionPayment.objects.count(), 0)
