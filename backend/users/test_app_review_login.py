from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import OTPVerification, User


class AppReviewLoginTests(APITestCase):
    """
    Stage 2D Step 1: Tests for the App Store reviewer login flow.
    Verifies environment-controlled access, Interakt skipping, standard
    verification enforcement, attempt limits, expiry, and throttling.
    """

    def setUp(self):
        cache.clear()
        self.send_url = reverse('send-otp')
        self.verify_url = reverse('verify-otp')
        # Arbitrary test constants for unit testing only -- never production credentials
        self.test_review_phone = "+919999900001"
        self.test_review_otp = "839201"

    @override_settings(
        APP_REVIEW_ENABLED=True,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP="839201"
    )
    def test_reviewer_login_when_enabled_skips_interakt_and_stores_record(self):
        with patch('users.views.requests.post') as mock_post:
            response = self.client.post(
                self.send_url,
                {'identifier': self.test_review_phone},
                format='json'
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, {"message": "OTP sent successfully"})
        # Asserts external WhatsApp API (Interakt) was skipped
        mock_post.assert_not_called()

        # Asserts response never leaks OTP
        self.assertNotIn('otp', response.data)
        self.assertNotIn(self.test_review_otp, str(response.data))

        # Asserts record in database
        record = OTPVerification.objects.filter(identifier=self.test_review_phone).order_by('-created_at').first()
        self.assertIsNotNone(record)
        self.assertEqual(record.otp, self.test_review_otp)
        self.assertFalse(record.is_verified)
        self.assertEqual(record.attempts, 0)

        # Asserts standard VerifyOTPView verifies the record and logs in
        verify_res = self.client.post(
            self.verify_url,
            {'identifier': self.test_review_phone, 'otp': self.test_review_otp},
            format='json'
        )
        self.assertEqual(verify_res.status_code, status.HTTP_200_OK)
        self.assertIn('tokens', verify_res.data)
        self.assertIn('access', verify_res.data['tokens'])
        self.assertIn('refresh', verify_res.data['tokens'])

        # Asserts user is created with the phone number
        self.assertTrue(User.objects.filter(phone_number=self.test_review_phone).exists())

    @override_settings(
        APP_REVIEW_ENABLED=False,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP="839201"
    )
    def test_reviewer_skipped_when_app_review_disabled(self):
        with patch('users.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {}
            response = self.client.post(
                self.send_url,
                {'identifier': self.test_review_phone},
                format='json'
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Asserts Interakt was called because reviewer access is disabled
        mock_post.assert_called_once()

        record = OTPVerification.objects.filter(identifier=self.test_review_phone).order_by('-created_at').first()
        self.assertIsNotNone(record)
        # Asserts the OTP is a dynamic random 6-digit OTP, NOT the static reviewer OTP
        self.assertNotEqual(record.otp, self.test_review_otp)
        self.assertEqual(len(record.otp), 6)

    @override_settings(
        APP_REVIEW_ENABLED=True,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP="839201"
    )
    def test_non_matching_identifier_calls_interakt_even_when_reviewer_enabled(self):
        other_phone = "+919876543210"
        with patch('users.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {}
            response = self.client.post(
                self.send_url,
                {'identifier': other_phone},
                format='json'
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Interakt MUST be called for non-matching identifier
        mock_post.assert_called_once()

        record = OTPVerification.objects.filter(identifier=other_phone).order_by('-created_at').first()
        self.assertIsNotNone(record)
        self.assertNotEqual(record.otp, self.test_review_otp)

    @override_settings(
        APP_REVIEW_ENABLED=True,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP="839201"
    )
    def test_reviewer_otp_expiry_enforced(self):
        with patch('users.views.requests.post'):
            self.client.post(self.send_url, {'identifier': self.test_review_phone}, format='json')

        record = OTPVerification.objects.filter(identifier=self.test_review_phone).order_by('-created_at').first()
        # Age the record past the 5-minute threshold
        record.created_at = timezone.now() - timedelta(minutes=6)
        record.save(update_fields=['created_at'])

        verify_res = self.client.post(
            self.verify_url,
            {'identifier': self.test_review_phone, 'otp': self.test_review_otp},
            format='json'
        )
        self.assertEqual(verify_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(verify_res.data, {"error": "Invalid or expired OTP"})

    @override_settings(
        APP_REVIEW_ENABLED=True,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP="839201"
    )
    def test_reviewer_otp_attempt_limits_enforced(self):
        with patch('users.views.requests.post'):
            self.client.post(self.send_url, {'identifier': self.test_review_phone}, format='json')

        # 5 wrong attempts lock out the OTP
        for _ in range(5):
            res = self.client.post(
                self.verify_url,
                {'identifier': self.test_review_phone, 'otp': '000000'},
                format='json'
            )
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # 6th attempt with correct OTP must fail because max attempts was reached
        final_res = self.client.post(
            self.verify_url,
            {'identifier': self.test_review_phone, 'otp': self.test_review_otp},
            format='json'
        )
        self.assertEqual(final_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(final_res.data, {"error": "Invalid or expired OTP"})

    @override_settings(
        APP_REVIEW_ENABLED=True,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP="839201"
    )
    def test_phone_normalization_matches_formatted_variants(self):
        # Number provided with spaces / without explicit plus
        formatted_variant = " 919999900001 "
        with patch('users.views.requests.post') as mock_post:
            response = self.client.post(
                self.send_url,
                {'identifier': formatted_variant},
                format='json'
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_post.assert_not_called()

        # The record is now stored under the CANONICAL identifier (+919999900001)
        record = OTPVerification.objects.filter(identifier=self.test_review_phone).order_by('-created_at').first()
        self.assertIsNotNone(record)
        self.assertEqual(record.otp, self.test_review_otp)
        # Raw/un-normalized variant is not stored in DB
        self.assertFalse(OTPVerification.objects.filter(identifier=formatted_variant).exists())

    @override_settings(
        APP_REVIEW_ENABLED=True,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP=""  # empty static OTP
    )
    def test_empty_static_otp_fails_safe_to_normal_send(self):
        with patch('users.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {}
            response = self.client.post(
                self.send_url,
                {'identifier': self.test_review_phone},
                format='json'
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # When static OTP is missing/empty, it must not bypass external send
        mock_post.assert_called_once()

    @override_settings(
        APP_REVIEW_ENABLED=True,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP="839201"
    )
    def test_cross_screen_phone_normalization_send_and_verify(self):
        """
        Task 2 Test A: Cross-screen normalization.
        Send OTP using a formatted reviewer phone number (spaces/punctuation/missing +)
        and verify using its canonical equivalent (+91...).
        Confirms successful verification, JWT token issuance, and expected User identity.
        """
        formatted_input = " +91 (999) 990-0001 "
        with patch('users.views.requests.post') as mock_post:
            send_res = self.client.post(
                self.send_url,
                {'identifier': formatted_input},
                format='json'
            )
        self.assertEqual(send_res.status_code, status.HTTP_200_OK)
        mock_post.assert_not_called()

        # OTP record must be stored under the canonical E.164 identifier
        record = OTPVerification.objects.filter(identifier=self.test_review_phone).order_by('-created_at').first()
        self.assertIsNotNone(record)
        self.assertEqual(record.otp, self.test_review_otp)

        # Verify using canonical equivalent
        verify_res = self.client.post(
            self.verify_url,
            {'identifier': self.test_review_phone, 'otp': self.test_review_otp},
            format='json'
        )
        self.assertEqual(verify_res.status_code, status.HTTP_200_OK)
        self.assertIn('tokens', verify_res.data)
        self.assertIn('access', verify_res.data['tokens'])
        self.assertIn('refresh', verify_res.data['tokens'])

        # Confirm expected User identity in database
        user = User.objects.get(id=verify_res.data['user_id'])
        self.assertEqual(user.phone_number, self.test_review_phone)

        # Also test the inverse: send canonical, verify with formatted
        cache.clear()
        with patch('users.views.requests.post') as mock_post:
            send_res2 = self.client.post(
                self.send_url,
                {'identifier': self.test_review_phone},
                format='json'
            )
        self.assertEqual(send_res2.status_code, status.HTTP_200_OK)
        verify_res2 = self.client.post(
            self.verify_url,
            {'identifier': " 919999900001 ", 'otp': self.test_review_otp},
            format='json'
        )
        self.assertEqual(verify_res2.status_code, status.HTTP_200_OK)
        self.assertEqual(verify_res2.data['user_id'], user.id)

    @override_settings(
        APP_REVIEW_ENABLED=True,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP="839201"
    )
    def test_reviewer_ip_throttling_enforced(self):
        """
        Task 2 Test B: Reviewer IP throttling.
        Verify repeated reviewer OTP requests from the same IP are throttled according
        to the existing OTPRequestThrottle policy (5/hour per IP).
        Production throttle limits are not altered.
        """
        client_ip = '198.51.100.77'
        responses = []
        for _ in range(6):  # OTPRequestThrottle policy allows 5/hour per IP
            res = self.client.post(
                self.send_url,
                {'identifier': self.test_review_phone},
                format='json',
                REMOTE_ADDR=client_ip
            )
            responses.append(res)

        # First 5 succeed under standard rate limit
        self.assertTrue(all(r.status_code == status.HTTP_200_OK for r in responses[:5]))
        # 6th request from the same IP is throttled
        self.assertEqual(responses[5].status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    @override_settings(
        APP_REVIEW_ENABLED=True,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP="839201"
    )
    def test_reviewer_otp_cannot_hijack_other_student_account(self):
        """
        Task 2 Test C: Reviewer OTP hijack immunity.
        Request a normal OTP for a different student's phone number,
        then attempt verification using the configured reviewer static OTP.
        Confirm authentication fails and no tokens are issued.
        """
        student_phone = "+919876543210"
        with patch('users.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {}
            send_res = self.client.post(
                self.send_url,
                {'identifier': student_phone},
                format='json'
            )
        self.assertEqual(send_res.status_code, status.HTTP_200_OK)
        mock_post.assert_called_once()

        # Attacker attempts to verify the normal student number using reviewer static OTP
        verify_res = self.client.post(
            self.verify_url,
            {'identifier': student_phone, 'otp': self.test_review_otp},
            format='json'
        )
        self.assertEqual(verify_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(verify_res.data, {"error": "Invalid or expired OTP"})
        self.assertNotIn('tokens', verify_res.data)

        # Confirm student account was not verified and attempt counter incremented
        record = OTPVerification.objects.filter(identifier=student_phone).order_by('-created_at').first()
        self.assertIsNotNone(record)
        self.assertFalse(record.is_verified)
        self.assertEqual(record.attempts, 1)

    @override_settings(
        APP_REVIEW_ENABLED=True,
        APP_REVIEW_PHONE_NUMBER="+919999900001",
        APP_REVIEW_STATIC_OTP="839201"
    )
    def test_malformed_phone_identifier_rejected_safely(self):
        """
        Malformed or malicious phone identifiers (letters, invalid lengths, script injections)
        must be rejected safely and never silently mapped to a valid account.
        """
        malformed_inputs = [
            "alice9999900001",      # letters mixed with digits
            "123",                  # too short (< 7 digits)
            "+",                    # plus sign only
            "09999900001",          # leading zero country code
            "+1234567890123456789", # too long (> 15 digits)
            "invalid_phone",        # pure text
        ]
        for i, malformed in enumerate(malformed_inputs):
            send_res = self.client.post(
                self.send_url,
                {'identifier': malformed},
                format='json',
                REMOTE_ADDR=f'10.1.1.{i}'
            )
            self.assertEqual(
                send_res.status_code,
                status.HTTP_400_BAD_REQUEST,
                f"SendOTPView failed to reject malformed input: {malformed}"
            )
            verify_res = self.client.post(
                self.verify_url,
                {'identifier': malformed, 'otp': '123456'},
                format='json',
                REMOTE_ADDR=f'10.1.1.{i}'
            )
            self.assertEqual(
                verify_res.status_code,
                status.HTTP_400_BAD_REQUEST,
                f"VerifyOTPView failed to reject malformed input: {malformed}"
            )

    def test_email_identifier_behavior_preserved(self):
        """
        Email identifiers must be preserved, stripped, lowercased, and never converted
        to phone numbers. User created must have email set and phone_number null.
        """
        email_input = "  Reviewer_Student@Example.Com  "
        expected_email = "reviewer_student@example.com"
        with patch('users.email_utils.send_otp_email'):
            send_res = self.client.post(
                self.send_url,
                {'identifier': email_input},
                format='json',
                REMOTE_ADDR='10.2.2.1'
            )
        self.assertEqual(send_res.status_code, status.HTTP_200_OK)

        record = OTPVerification.objects.filter(identifier=expected_email).order_by('-created_at').first()
        self.assertIsNotNone(record)

        verify_res = self.client.post(
            self.verify_url,
            {'identifier': email_input, 'otp': record.otp},
            format='json',
            REMOTE_ADDR='10.2.2.1'
        )
        self.assertEqual(verify_res.status_code, status.HTTP_200_OK)
        user = User.objects.get(id=verify_res.data['user_id'])
        self.assertEqual(user.email, expected_email)
        self.assertIsNone(user.phone_number)


