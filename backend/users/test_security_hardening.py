"""
Phase 3.9: Critical Defect & Security Remediation.

Tests for:
- OTP hardening: bypass removal, secure generation, per-identifier and
  per-IP throttling, expiry, wrong-attempt lockout, real email delivery.
- Google mobile login audience verification.
- AdminUserSerializer password validation.
- AdminAuditLog (role-change trigger; course-instructor and finance
  triggers are tested in their own apps' test files).
- Security headers (SSL redirect + exemption, HSTS).
- The new deep health-check endpoint.

Cache note: CACHES uses LocMemCache in DEBUG (this test environment) --
see core/settings.py's own comment for why (no Redis running locally, and
this project's test suite has never previously required one). LocMemCache
is NOT automatically cleared between test methods by Django's test
runner (unlike the DB, which rolls back per-test), so every test that
exercises a throttled endpoint clears the cache in setUp to avoid one
test's requests silently counting against another's throttle budget.
"""
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import AdminAuditLog, OTPVerification
from .serializers import AdminUserSerializer

User = get_user_model()


class OTPGenerationSecurityTests(TestCase):
    def test_otp_is_six_digits_zero_padded(self):
        from .views import _generate_otp
        for _ in range(50):
            otp = _generate_otp()
            self.assertEqual(len(otp), 6)
            self.assertTrue(otp.isdigit())

    def test_otp_generation_uses_cryptographic_secrets_module(self):
        """Confirms _generate_otp is backed by `secrets`, not `random` --
        the exact defect identified in the audit (random.randint is not a
        CSPRNG)."""
        from . import views as users_views
        with patch.object(users_views.secrets, 'randbelow', return_value=42) as mock_randbelow:
            otp = users_views._generate_otp()
        mock_randbelow.assert_called_once_with(1_000_000)
        self.assertEqual(otp, '000042')


class OTPBypassRemovedTests(APITestCase):
    """The hardcoded "+919999999999"/"123456" bypass must no longer exist
    anywhere in the OTP flow."""

    def setUp(self):
        cache.clear()

    def test_special_identifier_no_longer_bypasses_send(self):
        # Previously this identifier silently skipped actual OTP sending.
        # It must now be treated exactly like any other phone number (i.e.
        # attempt real WhatsApp delivery) -- mock the outbound call so the
        # test doesn't hit the real Interakt API.
        with patch('users.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {}
            response = self.client.post(reverse('send-otp'), {'identifier': '+919999999999'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_post.assert_called_once()  # proves it went through the REAL send path, not a silent bypass

    def test_hardcoded_otp_no_longer_verifies(self):
        response = self.client.post(
            reverse('verify-otp'), {'identifier': '+919999999999', 'otp': '123456'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class OTPThrottlingTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_send_otp_throttled_after_limit(self):
        with patch('users.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {}
            responses = [
                self.client.post(reverse('send-otp'), {'identifier': f'+9198765432{i:02d}'}, format='json')
                for i in range(6)  # otp_request scope = 5/hour
            ]
        self.assertTrue(all(r.status_code == status.HTTP_200_OK for r in responses[:5]))
        self.assertEqual(responses[5].status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_per_identifier_cooldown_independent_of_ip_throttle(self):
        """A DIFFERENT simulated client IP on every request -- so the
        per-IP throttle (5/hour) never engages at all -- proves the
        per-identifier cooldown is a genuinely separate mechanism, not
        just the same IP-based limit being hit twice."""
        identifier = '+919812345678'
        with patch('users.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {}
            responses = [
                self.client.post(
                    reverse('send-otp'), {'identifier': identifier}, format='json',
                    REMOTE_ADDR=f'10.0.0.{i}',
                )
                for i in range(6)  # MAX_OTP_REQUESTS_PER_WINDOW (5) + 1
            ]
        self.assertTrue(all(r.status_code == status.HTTP_200_OK for r in responses[:5]))
        self.assertEqual(responses[5].status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_verify_otp_throttled_after_limit(self):
        OTPVerification.objects.create(identifier='+919000000001', otp='111111')
        responses = [
            self.client.post(reverse('verify-otp'), {'identifier': '+919000000001', 'otp': '000000'}, format='json')
            for _ in range(21)  # otp_verify scope = 20/hour
        ]
        self.assertEqual(responses[-1].status_code, status.HTTP_429_TOO_MANY_REQUESTS)


class OTPExpiryTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_expired_otp_rejected(self):
        from django.utils import timezone
        from datetime import timedelta
        record = OTPVerification.objects.create(identifier='+919111111111', otp='555555')
        OTPVerification.objects.filter(pk=record.pk).update(created_at=timezone.now() - timedelta(minutes=6))
        response = self.client.post(reverse('verify-otp'), {'identifier': '+919111111111', 'otp': '555555'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_unexpired_otp_within_five_minutes_accepted(self):
        OTPVerification.objects.create(identifier='+919222222222', otp='444444')
        response = self.client.post(reverse('verify-otp'), {'identifier': '+919222222222', 'otp': '444444'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class OTPWrongAttemptLockoutTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_wrong_attempts_increment_and_lock_after_max(self):
        identifier = '+919333333333'
        OTPVerification.objects.create(identifier=identifier, otp='777777')

        for i in range(OTPVerification.MAX_VERIFY_ATTEMPTS):
            response = self.client.post(reverse('verify-otp'), {'identifier': identifier, 'otp': '000000'}, format='json')
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        record = OTPVerification.objects.get(identifier=identifier)
        self.assertEqual(record.attempts, OTPVerification.MAX_VERIFY_ATTEMPTS)

        # Even the CORRECT code is now rejected -- the record is locked.
        response = self.client.post(reverse('verify-otp'), {'identifier': identifier, 'otp': '777777'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_correct_code_before_lockout_still_works(self):
        identifier = '+919444444444'
        OTPVerification.objects.create(identifier=identifier, otp='888888')
        self.client.post(reverse('verify-otp'), {'identifier': identifier, 'otp': '000000'}, format='json')  # 1 wrong attempt
        response = self.client.post(reverse('verify-otp'), {'identifier': identifier, 'otp': '888888'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_newest_otp_for_identifier_is_the_only_active_one(self):
        """A deliberate tightening: requesting a new OTP supersedes an
        older still-unverified one for the same identifier -- only the
        most recently issued code is ever considered active."""
        identifier = '+919555555555'
        OTPVerification.objects.create(identifier=identifier, otp='111111')
        OTPVerification.objects.create(identifier=identifier, otp='222222')
        response = self.client.post(reverse('verify-otp'), {'identifier': identifier, 'otp': '111111'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        response = self.client.post(reverse('verify-otp'), {'identifier': identifier, 'otp': '222222'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class OTPEmailDeliveryTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_email_identifier_calls_ses_send(self):
        with patch('users.email_utils.boto3') as mock_boto3, \
             override_settings(AWS_ACCESS_KEY_ID='fake', AWS_SECRET_ACCESS_KEY='fake', DEBUG=False):
            mock_client = mock_boto3.client.return_value
            response = self.client.post(reverse('send-otp'), {'identifier': 'student@example.com'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_client.send_email.assert_called_once()
        call_kwargs = mock_client.send_email.call_args.kwargs
        self.assertEqual(call_kwargs['Destination'], {'ToAddresses': ['student@example.com']})

    def test_ses_failure_surfaces_as_error_not_silently_ignored(self):
        """Previously this path only ever printed a mock message and
        always reported success even though nothing was sent. A genuine
        SES failure must now be surfaced to the client, not hidden."""
        from botocore.exceptions import ClientError
        with patch('users.email_utils.boto3') as mock_boto3, \
             override_settings(AWS_ACCESS_KEY_ID='fake', AWS_SECRET_ACCESS_KEY='fake', DEBUG=False):
            mock_boto3.client.return_value.send_email.side_effect = ClientError(
                {'Error': {'Code': 'MessageRejected', 'Message': 'boom'}}, 'SendEmail',
            )
            response = self.client.post(reverse('send-otp'), {'identifier': 'fails@example.com'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertFalse(OTPVerification.objects.filter(identifier='fails@example.com').exists())


class AdminUserPasswordValidationTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(username='pw_superadmin', password='RealStrongPass987!', is_superuser=True, is_staff=True)

    def test_weak_password_rejected_on_create(self):
        self.client.force_authenticate(self.superadmin)
        response = self.client.post(reverse('admin-user-list'), {
            'username': 'weakpwuser', 'email': 'weak@example.com', 'password': '123',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('password', response.data)
        self.assertFalse(User.objects.filter(username='weakpwuser').exists())

    def test_common_password_rejected(self):
        self.client.force_authenticate(self.superadmin)
        response = self.client.post(reverse('admin-user-list'), {
            'username': 'commonpwuser', 'email': 'common@example.com', 'password': 'password',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_strong_password_accepted(self):
        self.client.force_authenticate(self.superadmin)
        response = self.client.post(reverse('admin-user-list'), {
            'username': 'strongpwuser', 'email': 'strong@example.com', 'password': 'Xk7#mQp2vRz9!Lw4',
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_weak_password_rejected_on_update(self):
        target = User.objects.create_user(username='update_target', password='OriginalStrongPass1!')
        self.client.force_authenticate(self.superadmin)
        response = self.client.patch(reverse('admin-user-detail', args=[target.id]), {'password': '1'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class RoleChangeAuditLogTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(username='audit_superadmin', password='RealStrongPass987!', is_superuser=True, is_staff=True)
        self.target = User.objects.create_user(username='audit_target', password='RealStrongPass987!')

    def test_role_change_creates_audit_log_entry(self):
        self.client.force_authenticate(self.superadmin)
        response = self.client.patch(reverse('admin-user-detail', args=[self.target.id]), {'is_teacher': True}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        log = AdminAuditLog.objects.filter(action='ROLE_CHANGE', target_type='User', target_id=str(self.target.id)).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_id, self.superadmin.id)
        self.assertIn('is_teacher', log.metadata['changed_fields'])
        self.assertEqual(log.metadata['changed_fields']['is_teacher'], {'from': False, 'to': True})

    def test_no_role_field_change_creates_no_log(self):
        self.client.force_authenticate(self.superadmin)
        before_count = AdminAuditLog.objects.count()
        self.client.patch(reverse('admin-user-detail', args=[self.target.id]), {'first_name': 'Changed'}, format='json')
        self.assertEqual(AdminAuditLog.objects.count(), before_count)

    def test_audit_log_is_read_only_in_admin(self):
        from django.contrib.admin.sites import AdminSite
        from .admin import AdminAuditLogAdmin
        admin_instance = AdminAuditLogAdmin(AdminAuditLog, AdminSite())
        self.assertFalse(admin_instance.has_add_permission(None))
        self.assertFalse(admin_instance.has_change_permission(None))
        self.assertFalse(admin_instance.has_delete_permission(None))


class GoogleMobileLoginAudienceTests(APITestCase):
    """
    Production Environment Verification follow-up. MobileGoogleLoginView
    now accepts a token whose `aud` claim matches ANY of
    GOOGLE_MOBILE_CLIENT_ID/GOOGLE_OAUTH_CLIENT_ID/GOOGLE_ANDROID_CLIENT_ID
    (users.views.google_oauth_audiences(), deduplicated, empties dropped) --
    not exactly one fixed client id. All three settings are overridden to
    '' in every test's own `with` block (rather than relying on whatever a
    real env happens to leave them at) so this suite is deterministic
    regardless of the environment it runs in.
    """

    def test_missing_client_id_setting_returns_service_unavailable(self):
        with override_settings(GOOGLE_MOBILE_CLIENT_ID='', GOOGLE_OAUTH_CLIENT_ID='', GOOGLE_ANDROID_CLIENT_ID=''):
            response = self.client.post(reverse('mobile-google-login'), {'token': 'anything'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    def test_valid_token_returns_200_and_logs_in(self):
        with override_settings(GOOGLE_MOBILE_CLIENT_ID='real-client-id.apps.googleusercontent.com', GOOGLE_OAUTH_CLIENT_ID='', GOOGLE_ANDROID_CLIENT_ID=''), \
             patch('users.views.id_token.verify_oauth2_token') as mock_verify:
            mock_verify.return_value = {'email': 'mobileuser@example.com', 'given_name': 'A', 'family_name': 'B'}
            response = self.client.post(reverse('mobile-google-login'), {'token': 'sometoken'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('tokens', response.data)
        self.assertTrue(User.objects.filter(email='mobileuser@example.com').exists())

    def test_audience_parameter_passed_to_verification_as_deduped_list(self):
        # Both settings point at the same real client id (the documented,
        # supported "reuse one Web-application client for both web login
        # and mobile's webClientId" pattern) -- the list passed to
        # verify_oauth2_token must be deduplicated, not contain the same
        # id twice.
        with override_settings(GOOGLE_MOBILE_CLIENT_ID='real-client-id.apps.googleusercontent.com', GOOGLE_OAUTH_CLIENT_ID='real-client-id.apps.googleusercontent.com', GOOGLE_ANDROID_CLIENT_ID=''), \
             patch('users.views.id_token.verify_oauth2_token') as mock_verify:
            mock_verify.return_value = {'email': 'mobileuser2@example.com', 'given_name': 'A', 'family_name': 'B'}
            response = self.client.post(reverse('mobile-google-login'), {'token': 'sometoken'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        _, kwargs = mock_verify.call_args
        self.assertEqual(kwargs.get('audience'), ['real-client-id.apps.googleusercontent.com'])

    def test_multiple_distinct_client_ids_all_accepted_as_audience(self):
        with override_settings(GOOGLE_MOBILE_CLIENT_ID='mobile-client.apps.googleusercontent.com', GOOGLE_OAUTH_CLIENT_ID='web-client.apps.googleusercontent.com', GOOGLE_ANDROID_CLIENT_ID='android-client.apps.googleusercontent.com'), \
             patch('users.views.id_token.verify_oauth2_token') as mock_verify:
            mock_verify.return_value = {'email': 'mobileuser3@example.com', 'given_name': 'A', 'family_name': 'B'}
            response = self.client.post(reverse('mobile-google-login'), {'token': 'sometoken'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        _, kwargs = mock_verify.call_args
        self.assertEqual(
            kwargs.get('audience'),
            ['mobile-client.apps.googleusercontent.com', 'web-client.apps.googleusercontent.com', 'android-client.apps.googleusercontent.com'],
        )

    def test_wrong_audience_token_rejected(self):
        # google-auth's real InvalidValue (raised by jwt.decode when `aud`
        # matches none of the configured audiences) IS a ValueError
        # subclass -- confirmed directly against the installed package --
        # so mocking the same exception type here is a faithful stand-in
        # for the real rejection path, not just an arbitrary ValueError.
        with override_settings(GOOGLE_MOBILE_CLIENT_ID='real-client-id.apps.googleusercontent.com', GOOGLE_OAUTH_CLIENT_ID='', GOOGLE_ANDROID_CLIENT_ID=''), \
             patch('users.views.id_token.verify_oauth2_token', side_effect=ValueError("Wrong recipient")):
            response = self.client.post(reverse('mobile-google-login'), {'token': 'sometoken'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_token_returns_400(self):
        with override_settings(GOOGLE_MOBILE_CLIENT_ID='real-client-id.apps.googleusercontent.com', GOOGLE_OAUTH_CLIENT_ID='', GOOGLE_ANDROID_CLIENT_ID=''):
            response = self.client.post(reverse('mobile-google-login'), {}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('error', response.data)

    def test_token_without_email_returns_400(self):
        with override_settings(GOOGLE_MOBILE_CLIENT_ID='real-client-id.apps.googleusercontent.com', GOOGLE_OAUTH_CLIENT_ID='', GOOGLE_ANDROID_CLIENT_ID=''), \
             patch('users.views.id_token.verify_oauth2_token') as mock_verify:
            mock_verify.return_value = {'given_name': 'A', 'family_name': 'B'}  # no 'email' key
            response = self.client.post(reverse('mobile-google-login'), {'token': 'sometoken'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data['error'])


class ProfileImageUploadValidationTests(TestCase):
    def test_oversized_image_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import TeacherProfile
        user = User.objects.create_user(username='upload_teacher', password='x', is_teacher=True)
        oversized = SimpleUploadedFile('big.jpg', b'\x00' * (6 * 1024 * 1024), content_type='image/jpeg')
        profile = TeacherProfile(user=user, profile_image=oversized)
        with self.assertRaises(Exception):
            profile.full_clean()

    def test_disallowed_extension_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        from .models import TeacherProfile
        user = User.objects.create_user(username='upload_teacher2', password='x', is_teacher=True)
        bad_file = SimpleUploadedFile('script.exe', b'not-an-image', content_type='application/octet-stream')
        profile = TeacherProfile(user=user, profile_image=bad_file)
        with self.assertRaises(Exception):
            profile.full_clean()


class SecurityHeadersTests(TestCase):
    """SECURE_SSL_REDIRECT/HSTS are only set at settings-load time when
    DEBUG=False (see core/settings.py) -- this test environment runs with
    DEBUG=True, so those settings are never populated here and the
    middleware has nothing to act on by default. This directly exercises
    SecurityMiddleware's OWN behavior against those settings values via
    override_settings, which is the meaningful, testable claim (the
    settings.py conditional itself is a straightforward, reviewable `if`
    statement, not independently unit-testable once already evaluated at
    import time)."""

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r'^$'])
    def test_health_check_root_path_exempt_from_ssl_redirect(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    @override_settings(SECURE_SSL_REDIRECT=True, SECURE_REDIRECT_EXEMPT=[r'^$'])
    def test_other_path_redirected_to_https(self):
        response = self.client.get('/health/detailed/')
        self.assertEqual(response.status_code, 301)
        self.assertTrue(response.url.startswith('https://'))

    @override_settings(SECURE_SSL_REDIRECT=False, SECURE_HSTS_SECONDS=86400)
    def test_hsts_header_present_on_secure_request(self):
        response = self.client.get('/', secure=True)
        self.assertIn('Strict-Transport-Security', response.headers)
        self.assertIn('max-age=86400', response.headers['Strict-Transport-Security'])


class DetailedHealthCheckTests(TestCase):
    def test_healthy_response_shape(self):
        with patch('redis.Redis.from_url') as mock_redis, \
             patch('core.celery.app.control') as mock_control:
            mock_redis.return_value.ping.return_value = True
            mock_control.inspect.return_value.ping.return_value = {'worker1': {'ok': 'pong'}}
            response = self.client.get('/health/detailed/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['checks']['database'], 'ok')
        self.assertEqual(data['checks']['redis'], 'ok')
        self.assertEqual(data['status'], 'ok')

    def test_degraded_when_redis_unreachable(self):
        with patch('redis.Redis.from_url', side_effect=Exception("connection refused")):
            response = self.client.get('/health/detailed/')
        self.assertEqual(response.status_code, 200)  # never a 5xx -- see health_check_detailed's own docstring
        data = response.json()
        self.assertEqual(data['status'], 'degraded')
        self.assertIn('error', data['checks']['redis'])

    def test_root_health_check_unaffected(self):
        """The bare EB-facing health check must remain untouched --
        trivial, dependency-free, always 200 'OK'."""
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b'OK')

    def test_dependency_failure_does_not_leak_raw_exception_text(self):
        """Staging/production verification pass (post-implementation):
        this endpoint has no authentication -- any anonymous caller can
        hit it -- so a raw exception string (e.g. a connection-refused
        message naming the broker's host:port) must never reach the
        response body. Only a flat 'ok'/'error' per dependency; the real
        detail is logged server-side only."""
        with patch('redis.Redis.from_url', side_effect=Exception("connection refused to redis://internal-host:6379")):
            response = self.client.get('/health/detailed/')
        data = response.json()
        self.assertEqual(data['checks']['redis'], 'error')
        self.assertNotIn('internal-host', str(data))
        self.assertNotIn('6379', str(data))


class LoginThrottlingTests(APITestCase):
    """
    Final release-blocker fix: POST api/auth/login/ (superuser/staff/
    teacher/mentor password login, dj-rest-auth's LoginView) previously had
    no rate limiting at all. Covers ThrottledLoginView (users/views.py) +
    LoginRateThrottle (users/throttles.py), scope 'login' = 10/hour
    (core/settings.py). Same cache.clear()-in-setUp convention as
    OTPThrottlingTests above, for the same LocMemCache-not-cleared-
    between-tests reason.
    """
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username='login_throttle_staff', password='correct-horse-battery-staple', is_staff=True)

    def test_login_succeeds_with_valid_credentials(self):
        response = self.client.post(reverse('rest_login'), {'username': 'login_throttle_staff', 'password': 'correct-horse-battery-staple'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_login_rejects_invalid_credentials(self):
        response = self.client.post(reverse('rest_login'), {'username': 'login_throttle_staff', 'password': 'wrong-password'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_repeated_login_attempts_throttled_returns_429(self):
        responses = [
            self.client.post(reverse('rest_login'), {'username': 'login_throttle_staff', 'password': 'wrong-password'}, format='json')
            for _ in range(11)  # login scope = 10/hour
        ]
        self.assertTrue(all(r.status_code == status.HTTP_400_BAD_REQUEST for r in responses[:10]))
        self.assertEqual(responses[10].status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_login_throttle_is_per_ip_not_global(self):
        """A different simulated client IP still has its own, independent
        budget -- proves this is a per-IP rolling limit (nobody is
        permanently locked out platform-wide), the same technique already
        used by test_per_identifier_cooldown_independent_of_ip_throttle
        above."""
        for _ in range(10):
            self.client.post(
                reverse('rest_login'), {'username': 'login_throttle_staff', 'password': 'wrong-password'}, format='json',
                REMOTE_ADDR='10.1.1.1',
            )
        exhausted = self.client.post(
            reverse('rest_login'), {'username': 'login_throttle_staff', 'password': 'wrong-password'}, format='json',
            REMOTE_ADDR='10.1.1.1',
        )
        self.assertEqual(exhausted.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

        still_works = self.client.post(
            reverse('rest_login'), {'username': 'login_throttle_staff', 'password': 'correct-horse-battery-staple'}, format='json',
            REMOTE_ADDR='10.2.2.2',
        )
        self.assertEqual(still_works.status_code, status.HTTP_200_OK)

    def test_login_throttle_does_not_affect_otp_endpoints(self):
        """Exhausting the login scope must never engage otp_request/
        otp_verify -- confirms 'login' is a genuinely separate throttle
        scope, not accidentally shared/global."""
        for _ in range(11):
            self.client.post(reverse('rest_login'), {'username': 'login_throttle_staff', 'password': 'wrong-password'}, format='json')

        with patch('users.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {}
            otp_response = self.client.post(reverse('send-otp'), {'identifier': '+919876500000'}, format='json')
        self.assertEqual(otp_response.status_code, status.HTTP_200_OK)

    def test_otp_throttle_does_not_affect_login_endpoint(self):
        """The inverse: exhausting otp_request must never engage the
        login scope."""
        with patch('users.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {}
            for i in range(6):  # otp_request scope = 5/hour
                self.client.post(reverse('send-otp'), {'identifier': f'+9198765{i:05d}'}, format='json')

        login_response = self.client.post(reverse('rest_login'), {'username': 'login_throttle_staff', 'password': 'correct-horse-battery-staple'}, format='json')
        self.assertEqual(login_response.status_code, status.HTTP_200_OK)
