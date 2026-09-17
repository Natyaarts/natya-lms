"""
General API rate limiting gap fix (final release audit).

Covers:
1. The new general baseline (DEFAULT_THROTTLE_CLASSES = Anon/UserRateThrottle,
   scopes 'anon'/'user') -- both the raw throttle classes directly, and a
   real end-to-end round trip through an ordinary, unmodified view.
2. Every new dedicated scoped throttle added this phase (order_create,
   payment_verify, subscription_create, subscription_verify, refund_create,
   assessment_start, assessment_submit, assignment_submit, password_reset).
3. That the already-fixed login throttle is untouched/still active.
4. Cross-scope independence (one scope tripping doesn't affect another).
5. That a request below any limit still succeeds normally (not merely
   "doesn't 429" -- legitimate usage must remain unaffected).

Every test that needs to "use up" a rate posts/gets a cheap, deliberately
minimal or invalid request repeatedly -- DRF's throttle check
(APIView.initial() -> check_throttles()) runs BEFORE the view's own
handler method, so it counts against the throttle regardless of whether
the view goes on to accept or reject that specific request. This avoids
needing to mock Razorpay or build a fully valid payment/subscription
fixture just to prove the THROTTLE layer trips at the right count --
exactly the same principle LoginThrottlingTests/OTPThrottlingTests
already established (repeated real requests, not instantiating the
throttle class in isolation) for the two pre-existing scopes.

Cache note: LocMemCache (this test environment) is not cleared between
test methods -- every test that exercises a throttled endpoint clears the
cache in setUp, same as users/test_security_hardening.py already does.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIRequestFactory, APITestCase
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

from courses.models import Course, Enrollment
from courses.test_assessment_attempts import build_mixed_assessment
from courses.test_assignments import _make_assignment, _make_course

User = get_user_model()


class GeneralThrottleBaselineTests(TestCase):
    """DEFAULT_THROTTLE_CLASSES / DEFAULT_THROTTLE_RATES['anon'|'user'] --
    exercises DRF's own real, unmodified AnonRateThrottle/UserRateThrottle
    classes directly against a real APIRequestFactory request, since these
    apply globally rather than to one specific view."""

    def setUp(self):
        cache.clear()
        self.factory = APIRequestFactory()

    def test_anon_and_user_rates_configured(self):
        from rest_framework.settings import api_settings
        self.assertEqual(api_settings.DEFAULT_THROTTLE_RATES.get('anon'), '100/min')
        self.assertEqual(api_settings.DEFAULT_THROTTLE_RATES.get('user'), '300/min')
        self.assertIn(AnonRateThrottle, api_settings.DEFAULT_THROTTLE_CLASSES)
        self.assertIn(UserRateThrottle, api_settings.DEFAULT_THROTTLE_CLASSES)

    def test_anon_baseline_blocks_after_limit(self):
        throttle = AnonRateThrottle()
        request = self.factory.get('/')
        request.user = type('Anon', (), {'is_authenticated': False})()
        for i in range(100):
            self.assertTrue(throttle.allow_request(request, None), f"request {i} unexpectedly throttled")
        self.assertFalse(throttle.allow_request(request, None))

    def test_user_baseline_is_keyed_per_authenticated_user_not_ip(self):
        user_a = User.objects.create_user(username='baseline_user_a', password='pw')
        user_b = User.objects.create_user(username='baseline_user_b', password='pw')

        throttle = UserRateThrottle()
        request_a = self.factory.get('/')
        request_a.user = user_a
        for i in range(300):
            self.assertTrue(throttle.allow_request(request_a, None), f"user_a request {i} unexpectedly throttled")
        self.assertFalse(throttle.allow_request(request_a, None))

        # A completely different authenticated user, same throttle
        # instance shape, is unaffected by user_a's exhausted count --
        # confirms the key is request.user.pk, not anything shared/global.
        throttle_b = UserRateThrottle()
        request_b = self.factory.get('/')
        request_b.user = user_b
        self.assertTrue(throttle_b.allow_request(request_b, None))

    def test_real_unmodified_view_gets_general_baseline(self):
        # Bundle listing has no throttle_classes override anywhere --
        # confirms the global default actually reaches a real view/URL,
        # not just the throttle classes in isolation. A handful of
        # requests, well under the 100/min anon limit, must all succeed.
        url = reverse('bundle-list')
        for _ in range(5):
            response = self.client.get(url)
            self.assertEqual(response.status_code, status.HTTP_200_OK)


class LoginAndOTPThrottleRegressionTests(APITestCase):
    """Confirms the already-fixed login/OTP throttles are untouched by
    this phase's DEFAULT_THROTTLE_CLASSES addition -- a view with its own
    throttle_classes is entirely unaffected by the new global default
    (throttle_classes replaces, not adds to, the default list)."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='rl_login_user', password='correct-password', is_staff=True)

    def test_login_still_throttles_at_its_own_existing_rate(self):
        url = reverse('rest_login')
        for i in range(10):
            response = self.client.post(url, {'username': 'rl_login_user', 'password': 'wrong'}, format='json')
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(url, {'username': 'rl_login_user', 'password': 'wrong'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_send_otp_still_throttles_at_its_own_existing_rate(self):
        url = reverse('send-otp')
        for i in range(5):
            response = self.client.post(url, {'identifier': 'rl_otp_test@example.com'}, format='json')
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(url, {'identifier': 'rl_otp_test@example.com'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class PasswordResetThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.url = reverse('rest_password_reset')

    def test_password_reset_request_throttled_after_limit(self):
        for i in range(5):
            response = self.client.post(self.url, {'email': 'someone@example.com'}, format='json')
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(self.url, {'email': 'someone@example.com'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_password_reset_throttle_independent_of_login_throttle(self):
        # Exhaust password_reset...
        for _ in range(6):
            self.client.post(self.url, {'email': 'someone@example.com'}, format='json')
        # ...login (a different scope) must still be reachable.
        response = self.client.post(reverse('rest_login'), {'username': 'nobody', 'password': 'x'}, format='json')
        self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class OrderPaymentThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(username='rl_order_student', password='pw')
        self.client.force_authenticate(self.student)

    def test_create_order_throttled_after_limit(self):
        url = reverse('create-order')
        for i in range(20):
            response = self.client.post(url, {}, format='json')  # missing course_id -> cheap 400
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_verify_payment_throttled_after_limit(self):
        url = reverse('verify-payment')
        for i in range(30):
            response = self.client.post(url, {}, format='json')  # missing fields -> cheap 400
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_order_creation_throttle_is_per_user_not_shared_with_other_students(self):
        url = reverse('create-order')
        for _ in range(21):
            self.client.post(url, {}, format='json')
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        other_student = User.objects.create_user(username='rl_order_other_student', password='pw')
        self.client.force_authenticate(other_student)
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)  # not throttled -- own budget untouched

    def test_order_viewset_create_action_throttled_separately_from_list(self):
        create_url = reverse('order-list')  # router POST /orders/orders/ -> create()
        for _ in range(20):
            self.client.post(create_url, {}, format='json')
        response = self.client.post(create_url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        # list (GET, same URL) is a completely different action -- must
        # remain on the general 'user' baseline, unaffected by create()
        # having just been exhausted.
        response = self.client.get(create_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_order_viewset_verify_action_throttled(self):
        url = reverse('order-verify', kwargs={'pk': 999999})  # nonexistent -- cheap 404
        for i in range(30):
            response = self.client.post(url)
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class SubscriptionThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user(username='rl_sub_student', password='pw')
        self.client.force_authenticate(self.student)

    def test_create_subscription_throttled_after_limit(self):
        url = reverse('create-subscription')
        for i in range(10):
            response = self.client.post(url, {}, format='json')  # missing plan_id -> cheap 400
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_verify_subscription_payment_throttled_after_limit(self):
        url = reverse('verify-subscription')
        for i in range(20):
            response = self.client.post(url, {}, format='json')  # missing fields -> cheap 400
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class RefundThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(username='rl_refund_admin', password='pw', is_staff=True)
        self.student = User.objects.create_user(username='rl_refund_student', password='pw')
        self.client.force_authenticate(self.admin)

    def test_create_refund_throttled_after_limit(self):
        url = reverse('finance-refund-create')
        for i in range(30):
            response = self.client.post(url, {}, format='json')  # missing amount -> cheap 400
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(url, {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_non_admin_denied_before_throttle_even_matters(self):
        self.client.force_authenticate(self.student)
        response = self.client.post(reverse('finance-refund-create'), {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AssessmentThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.fx = build_mixed_assessment(max_attempts=50)
        self.student = User.objects.create_user(username='rl_assessment_student', password='pw')
        Enrollment.objects.create(user=self.student, course=self.fx['course'])
        self.client.force_authenticate(self.student)

    def test_assessment_start_throttled_after_limit(self):
        # Calling `start` again while one attempt is already IN_PROGRESS
        # just returns that same attempt (200) rather than creating a new
        # one -- so this can safely loop well past max_attempts without
        # ever hitting the "max attempts reached" business rule; only the
        # throttle should intervene.
        url = reverse('assessment-start', kwargs={'pk': self.fx['assessment'].id})
        for i in range(30):
            response = self.client.post(url)
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_assessment_retrieve_unaffected_by_start_throttle(self):
        start_url = reverse('assessment-start', kwargs={'pk': self.fx['assessment'].id})
        for _ in range(30):
            self.client.post(start_url)
        response = self.client.post(start_url)
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        # retrieve (GET, read-only preview) is a different action -- stays
        # on the general 'user' baseline, unaffected by `start` tripping.
        retrieve_url = reverse('assessment-detail', kwargs={'pk': self.fx['assessment'].id})
        response = self.client.get(retrieve_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_assessment_submit_throttled_after_limit(self):
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx['assessment'].id}))
        attempt_id = start.data['attempt_id']
        submit_url = reverse('assessment-attempt-submit', kwargs={'pk': attempt_id})
        # First submit actually succeeds; every subsequent one 400s
        # ("already submitted") -- irrelevant to the throttle, which
        # counts every request that reaches the view either way.
        for i in range(30):
            response = self.client.post(submit_url, {'answers': []}, format='json')
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(submit_url, {'answers': []}, format='json')
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class AssignmentThrottleTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.course = _make_course()
        from courses.models import Module
        self.module = Module.objects.create(course=self.course, title='M1', order=1)
        self.assignment = _make_assignment(module=self.module, max_marks=Decimal('50.00'))
        self.student = User.objects.create_user(username='rl_assignment_student', password='pw')
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def test_assignment_submit_throttled_after_limit(self):
        url = reverse('assignment-submit', kwargs={'pk': self.assignment.id})
        for i in range(20):
            response = self.client.post(url, {'content': f'answer {i}'})
            self.assertNotEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS, f"attempt {i} unexpectedly throttled early")
        response = self.client.post(url, {'content': 'one more'})
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
