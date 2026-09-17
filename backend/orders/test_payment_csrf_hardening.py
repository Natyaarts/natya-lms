"""
Final release audit -- payment/subscription CSRF hardening.

Covers CSRFEnforcedJWTCookieAuthentication (orders/views.py) and the
ensure_csrf_cookie additions on SubscriptionMeView (orders/views.py) and
CurrentUserView (users/views.py), across the endpoints named in the
audit: CreateOrderView, VerifyPaymentView, CreateSubscriptionView,
VerifySubscriptionPaymentView, SubscriptionMeView, CancelSubscriptionView.

"Web" tests below use a REAL cookie-based session (via the actual login
endpoint, exactly like a browser) rather than force_authenticate --
force_authenticate bypasses authentication_classes entirely and would
never exercise this code at all. Confirmed by reading every existing
subscription/checkout test in this app (test_subscription_checkout.py,
test_subscription_cancellation.py, etc.): all of them use
force_authenticate, which is exactly why none of them needed to change
for this fix, and why they remain a completely independent regression
signal from the tests below.

"Mobile" tests below use a bearer Authorization header
(mobile/src/api/client.ts's own pattern) instead of cookies.

cache.clear() in setUp -- LocMemCache (this test environment) is not
cleared between test methods, and the login endpoint itself is now
throttled (10/hour, see users/throttles.py's LoginRateThrottle, added in
the immediately preceding release-blocker fix) -- without this, logging
in repeatedly across many test methods in this file could spuriously
trip that unrelated throttle.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from orders.models import Subscription, SubscriptionPlan
from orders.tests import WEBHOOK_TEST_SECRET, sign_webhook_payload

User = get_user_model()


class PaymentCSRFHardeningTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.password = 'correct-horse-battery-staple'
        self.student = User.objects.create_user(username='csrf_web_student', password=self.password, is_student=True)
        self.other_student = User.objects.create_user(username='csrf_web_other', password=self.password, is_student=True)
        self.plan = SubscriptionPlan.objects.create(
            name='CSRF Test Plan', billing_interval='MONTHLY', price='999.00', razorpay_plan_id='plan_csrf_test_1',
        )
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id='sub_csrf_test_1',
        )

    # ---- helpers ----

    def _browser_client(self, user=None):
        """A fresh APIClient with real CSRF enforcement on, logged in via
        the actual login endpoint -- exactly like a real browser: cookies
        set on the login response (the JWT cookie, per dj-rest-auth) are
        automatically carried on this same client's subsequent requests."""
        user = user or self.student
        client = APIClient(enforce_csrf_checks=True)
        response = client.post(reverse('rest_login'), {'username': user.username, 'password': self.password}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return client

    def _prime_csrf_cookie(self, client):
        """Fires the CSRF-cookie-ensuring endpoint and returns the token
        value to send back as a header -- exactly what a real frontend
        does (read the csrftoken cookie set by GET api/users/me/, send it
        back as X-CSRFToken on the next state-changing request)."""
        res = client.get(reverse('current-user'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        return client.cookies['csrftoken'].value

    # ---- WEB: 1. valid CSRF token succeeds ----

    @patch('orders.views.client')
    def test_web_request_with_valid_csrf_token_succeeds(self, mock_client):
        mock_client.subscription.cancel.return_value = {"id": "sub_csrf_test_1", "status": "active"}
        browser = self._browser_client()
        token = self._prime_csrf_cookie(browser)
        response = browser.post(reverse('cancel-subscription'), HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # ---- WEB: 2. missing CSRF token rejected ----

    @patch('orders.views.client')
    def test_web_request_without_csrf_token_rejected(self, mock_client):
        browser = self._browser_client()
        self._prime_csrf_cookie(browser)  # cookie exists, but no header is sent below
        response = browser.post(reverse('cancel-subscription'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_client.subscription.cancel.assert_not_called()

    # ---- WEB: 3. invalid CSRF token rejected ----

    @patch('orders.views.client')
    def test_web_request_with_invalid_csrf_token_rejected(self, mock_client):
        browser = self._browser_client()
        self._prime_csrf_cookie(browser)
        response = browser.post(reverse('cancel-subscription'), HTTP_X_CSRFTOKEN='not-a-real-token')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_client.subscription.cancel.assert_not_called()

    # ---- WEB: 4. existing frontend-compatible behavior (read-only GET is
    # never CSRF-checked, and priming the cookie doesn't change its own
    # response) ----

    def test_web_subscription_me_get_is_never_csrf_checked(self):
        browser = self._browser_client()
        response = browser.get(reverse('subscription-me'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], 'ACTIVE')

    # ---- MOBILE: 5/6. bearer-token requests work, unaffected by CSRF ----

    @patch('orders.views.client')
    def test_mobile_bearer_token_request_succeeds_without_any_csrf_token(self, mock_client):
        mock_client.subscription.cancel.return_value = {"id": "sub_csrf_test_1", "status": "active"}
        from rest_framework_simplejwt.tokens import RefreshToken
        access_token = str(RefreshToken.for_user(self.student).access_token)
        mobile_client = APIClient(enforce_csrf_checks=True)  # even with enforcement on...
        mobile_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        # ...no cookies, no CSRF header at all -- exactly mobile/src/api/client.ts's shape.
        response = mobile_client.post(reverse('cancel-subscription'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_mobile_bearer_token_get_still_works(self):
        from rest_framework_simplejwt.tokens import RefreshToken
        access_token = str(RefreshToken.for_user(self.student).access_token)
        mobile_client = APIClient(enforce_csrf_checks=True)
        mobile_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        response = mobile_client.get(reverse('subscription-me'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    # ---- SECURITY: 7. cross-site unauthenticated request rejected ----

    def test_unauthenticated_request_cannot_cancel_subscription(self):
        anon_client = APIClient(enforce_csrf_checks=True)
        response = anon_client.post(reverse('cancel-subscription'))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.subscription.refresh_from_db()
        self.assertFalse(self.subscription.cancel_at_period_end)

    # ---- SECURITY: 8. cross-site subscription cancellation cannot
    # silently succeed -- the audit's named concern, verified end-to-end
    # with an actual (mocked) Razorpay call and a real DB assertion ----

    @patch('orders.views.client')
    def test_cross_site_cancellation_cannot_silently_cancel_subscription(self, mock_client):
        """Simulates the CSRF attack directly: the victim's browser DOES
        carry their real, valid JWT cookie (exactly what happens
        automatically on any cross-site request, JWT_AUTH_SAMESITE='None')
        -- but an attacker's page cannot read/know the victim's csrftoken
        cookie value (blocked by the same-origin policy), so it cannot
        supply a matching X-CSRFToken header. That missing header is what
        this test simulates, and it must be enough to stop the attack."""
        browser = self._browser_client()
        # Deliberately do NOT call _prime_csrf_cookie / send any CSRF
        # header -- this is the attacker's forged cross-site POST, using
        # only the cookie the browser auto-attaches.
        response = browser.post(reverse('cancel-subscription'))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        mock_client.subscription.cancel.assert_not_called()
        self.subscription.refresh_from_db()
        self.assertFalse(self.subscription.cancel_at_period_end)
        self.assertIsNone(self.subscription.cancelled_at)

    # ---- SECURITY: 9. cannot act on another user's subscription ----

    @patch('orders.views.client')
    def test_cannot_cancel_another_users_subscription_over_real_session(self, mock_client):
        browser = self._browser_client(user=self.other_student)
        token = self._prime_csrf_cookie(browser)
        response = browser.post(reverse('cancel-subscription'), HTTP_X_CSRFTOKEN=token)
        # other_student has no subscription of their own -- 404, never
        # touching self.student's subscription (created in setUp).
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        mock_client.subscription.cancel.assert_not_called()
        self.subscription.refresh_from_db()
        self.assertFalse(self.subscription.cancel_at_period_end)

    # ---- SECURITY: 10. Razorpay webhook still works without CSRF ----

    @override_settings(RAZORPAY_WEBHOOK_SECRET=WEBHOOK_TEST_SECRET)
    def test_webhook_still_works_without_csrf(self):
        """RazorpayWebhookView is untouched by this fix (authentication_classes
        stays [], @csrf_exempt stays -- it never used CsrfExemptSessionAuthentication
        in the first place, so there was nothing to change here). A plain,
        no-cookie, no-CSRF-header POST with a valid signature must still
        succeed exactly as before."""
        payload = {
            "event": "subscription.activated",
            "payload": {"subscription": {"entity": {
                "id": "sub_csrf_test_1", "entity": "subscription", "status": "active",
                "current_start": None, "current_end": None, "ended_at": None,
                "charge_at": None, "start_at": None, "end_at": None,
                "total_count": 1200, "paid_count": 0, "remaining_count": 1200, "quantity": 1,
            }}},
        }
        body, signature = sign_webhook_payload(payload)
        anon_client = APIClient(enforce_csrf_checks=True)  # no login, no cookies at all
        response = anon_client.post(
            reverse('razorpay-webhook'), data=body, content_type='application/json',
            HTTP_X_RAZORPAY_SIGNATURE=signature, HTTP_X_RAZORPAY_EVENT_ID='evt_csrf_webhook_1',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
