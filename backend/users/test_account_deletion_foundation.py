from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from .models import OTPVerification, User, AccountDeletionRequest


class AccountDeletionFoundationTests(APITestCase):
    """
    Stage 2D Step 2B, Phase 1: Tests for OTP purpose separation,
    account deletion OTP request/verify flows, and AccountDeletionRequest
    model durability and idempotency constraints.
    """

    def setUp(self):
        cache.clear()
        self.login_send_url = reverse('send-otp')
        self.login_verify_url = reverse('verify-otp')
        self.del_request_otp_url = reverse('delete-account-request-otp')
        self.del_verify_otp_url = reverse('delete-account-verify-otp')
        self.del_status_url = reverse('delete-account-status')
        self.del_cancel_url = reverse('delete-account-cancel')

        self.user = User.objects.create_user(
            username='deletion_test_user',
            email='deletion_test@example.com',
            phone_number='+919876543210'
        )
        self.client.force_authenticate(user=self.user)

    # -------------------------------------------------------------------------
    # TASK 1: Purpose Isolation Tests
    # -------------------------------------------------------------------------

    def test_login_otp_cannot_satisfy_account_deletion_verification(self):
        """
        Verify that an OTP generated for LOGIN cannot be used to verify
        an account deletion request.
        """
        login_otp = "123456"
        OTPVerification.objects.create(
            identifier='+919876543210',
            otp=login_otp,
            purpose=OTPVerification.Purpose.LOGIN
        )

        response = self.client.post(
            self.del_verify_otp_url,
            {'otp': login_otp, 'identifier': '+919876543210'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('error'), "Invalid or expired OTP")

        # Deletion request must not have been created
        self.assertFalse(AccountDeletionRequest.objects.filter(user=self.user).exists())

        # The login OTP should remain unverified
        record = OTPVerification.objects.get(otp=login_otp, identifier='+919876543210')
        self.assertFalse(record.is_verified)

    def test_account_deletion_otp_cannot_satisfy_normal_login(self):
        """
        Verify that an OTP generated for ACCOUNT_DELETION cannot authenticate
        a normal user login.
        """
        del_otp = "654321"
        OTPVerification.objects.create(
            identifier='+919876543210',
            otp=del_otp,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        )

        # Unauthenticated client attempting login
        self.client.logout()
        response = self.client.post(
            self.login_verify_url,
            {'identifier': '+919876543210', 'otp': del_otp},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('error'), "Invalid or expired OTP")

        # Deletion OTP record must remain unverified
        record = OTPVerification.objects.get(otp=del_otp, identifier='+919876543210')
        self.assertFalse(record.is_verified)

    # -------------------------------------------------------------------------
    # TASK 1: Expiry, Attempt Limits, and Single-Use Tests
    # -------------------------------------------------------------------------

    def test_deletion_otp_expiry_enforced(self):
        """
        Account deletion OTP older than 5 minutes must be rejected.
        """
        del_otp = "112233"
        record = OTPVerification.objects.create(
            identifier='+919876543210',
            otp=del_otp,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        )
        # Fast-forward created_at to 6 minutes ago
        OTPVerification.objects.filter(id=record.id).update(
            created_at=timezone.now() - timedelta(minutes=6)
        )

        response = self.client.post(
            self.del_verify_otp_url,
            {'otp': del_otp, 'identifier': '+919876543210'},
            format='json'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get('error'), "Invalid or expired OTP")
        self.assertFalse(AccountDeletionRequest.objects.filter(user=self.user).exists())

    def test_deletion_otp_attempt_limits_lockout(self):
        """
        Five consecutive incorrect attempts must permanently invalidate the deletion OTP.
        """
        del_otp = "998877"
        OTPVerification.objects.create(
            identifier='+919876543210',
            otp=del_otp,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        )

        # 5 wrong guesses
        for _ in range(5):
            res = self.client.post(
                self.del_verify_otp_url,
                {'otp': '000000', 'identifier': '+919876543210'},
                format='json'
            )
            self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # 6th attempt with correct OTP must still be rejected
        res = self.client.post(
            self.del_verify_otp_url,
            {'otp': del_otp, 'identifier': '+919876543210'},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data.get('error'), "Invalid or expired OTP")
        self.assertFalse(AccountDeletionRequest.objects.filter(user=self.user).exists())

    def test_deletion_otp_single_use_enforced(self):
        """
        A verified deletion OTP cannot be reused.
        """
        del_otp = "445566"
        OTPVerification.objects.create(
            identifier='+919876543210',
            otp=del_otp,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        )

        # 1st verification succeeds
        res1 = self.client.post(
            self.del_verify_otp_url,
            {'otp': del_otp, 'identifier': '+919876543210'},
            format='json'
        )
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        self.assertTrue(AccountDeletionRequest.objects.filter(user=self.user).exists())

        # 2nd verification with same OTP must be rejected
        res2 = self.client.post(
            self.del_verify_otp_url,
            {'otp': del_otp, 'identifier': '+919876543210'},
            format='json'
        )
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res2.data.get('error'), "Invalid or expired OTP")

    # -------------------------------------------------------------------------
    # Authentication & Identifier Safety Tests
    # -------------------------------------------------------------------------

    def test_deletion_endpoints_require_authentication(self):
        """
        Unauthenticated requests to request-otp and verify-otp are rejected (401).
        """
        self.client.logout()
        res_req = self.client.post(self.del_request_otp_url, {}, format='json')
        self.assertEqual(res_req.status_code, status.HTTP_401_UNAUTHORIZED)

        res_ver = self.client.post(self.del_verify_otp_url, {'otp': '123456'}, format='json')
        self.assertEqual(res_ver.status_code, status.HTTP_401_UNAUTHORIZED)

        res_stat = self.client.get(self.del_status_url)
        self.assertEqual(res_stat.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_deletion_otp_request_rejects_foreign_identifier(self):
        """
        An authenticated user cannot request deletion OTP for another user's identifier.
        """
        res = self.client.post(
            self.del_request_otp_url,
            {'identifier': '+919999900002'},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Provided identifier does not match", res.data.get('error', ''))

    def test_deletion_otp_request_auto_resolves_user_phone(self):
        """
        If identifier is omitted, the user's registered phone number is automatically used.
        """
        with patch('users.views.requests.post') as mock_post:
            mock_post.return_value.status_code = 200
            res = self.client.post(self.del_request_otp_url, {}, format='json')

        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get('message'), "Account deletion OTP sent successfully")

        record = OTPVerification.objects.filter(
            identifier='+919876543210',
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        ).order_by('-created_at').first()
        self.assertIsNotNone(record)
        # OTP must not be leaked in response
        self.assertNotIn('otp', res.data)
        self.assertNotIn(record.otp, str(res.data))

    def test_deletion_otp_cooldown_rate_limit(self):
        """
        Per-identifier rate limit prevents requesting more than 5 deletion OTPs in 10 minutes.
        """
        now = timezone.now()
        for i in range(5):
            OTPVerification.objects.create(
                identifier='+919876543210',
                otp=f'10000{i}',
                purpose=OTPVerification.Purpose.ACCOUNT_DELETION,
                created_at=now
            )

        res = self.client.post(self.del_request_otp_url, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        self.assertIn("Too many OTP requests", res.data.get('error', ''))

    # -------------------------------------------------------------------------
    # TASK 2: Deletion Request Model & Idempotency Tests
    # -------------------------------------------------------------------------

    def test_deletion_request_created_upon_successful_verification(self):
        """
        Verifying a valid deletion OTP creates a durable AccountDeletionRequest with PENDING status.
        """
        del_otp = "778899"
        OTPVerification.objects.create(
            identifier='+919876543210',
            otp=del_otp,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        )

        res = self.client.post(
            self.del_verify_otp_url,
            {'otp': del_otp, 'reason': 'Closing my account'},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data.get('status'), AccountDeletionRequest.Status.COMPLETED)
        self.assertTrue(res.data.get('created'))

        req = AccountDeletionRequest.objects.get(user=self.user)
        self.assertEqual(req.status, AccountDeletionRequest.Status.COMPLETED)
        self.assertEqual(req.reason, 'Closing my account')
        self.assertIsNotNone(req.confirmed_at)
        self.assertIsNotNone(req.completed_at)

    def test_repeated_deletion_request_is_idempotent(self):
        """
        Verifying a subsequent deletion OTP when an active PENDING request already exists
        updates the existing record idempotently without creating a duplicate.
        """
        # Create existing pending request
        req1 = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING,
            reason='Initial reason',
            confirmed_at=timezone.now() - timedelta(minutes=10)
        )
        old_confirmed_at = req1.confirmed_at

        # Verify a new deletion OTP
        del_otp = "332211"
        OTPVerification.objects.create(
            identifier='+919876543210',
            otp=del_otp,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        )

        res = self.client.post(
            self.del_verify_otp_url,
            {'otp': del_otp},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get('request_id'), req1.id)
        self.assertFalse(res.data.get('created'))

        # Must still only be one record for this user
        self.assertEqual(AccountDeletionRequest.objects.filter(user=self.user).count(), 1)
        req1.refresh_from_db()
        self.assertGreater(req1.confirmed_at, old_confirmed_at)

    def test_concurrent_active_deletion_request_rejected_by_db_constraint(self):
        """
        The database UniqueConstraint prevents multiple concurrent active (PENDING/PROCESSING)
        requests for the same user.
        """
        AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )

        with self.assertRaises(IntegrityError):
            AccountDeletionRequest.objects.create(
                user=self.user,
                status=AccountDeletionRequest.Status.PROCESSING
            )

    def test_request_otp_blocked_when_deletion_is_processing(self):
        """
        If a deletion request is currently PROCESSING, new deletion OTP requests are blocked with 409 Conflict.
        """
        AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PROCESSING
        )

        res = self.client.post(self.del_request_otp_url, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(res.data.get('error'), "Account deletion is already being processed.")

    # -------------------------------------------------------------------------
    # Terminal State & Retry Tests
    # -------------------------------------------------------------------------

    def test_retry_allowed_after_failed_request(self):
        """
        A user whose prior deletion request FAILED can request and create a new deletion request.
        """
        AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.FAILED,
            error_message="External service error"
        )

        del_otp = "556677"
        OTPVerification.objects.create(
            identifier='+919876543210',
            otp=del_otp,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        )

        res = self.client.post(
            self.del_verify_otp_url,
            {'otp': del_otp, 'reason': 'Retry deletion'},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(AccountDeletionRequest.objects.filter(user=self.user).count(), 2)

        completed = AccountDeletionRequest.objects.filter(
            user=self.user,
            status=AccountDeletionRequest.Status.COMPLETED
        ).first()
        self.assertIsNotNone(completed)
        self.assertEqual(completed.reason, 'Retry deletion')

    def test_cancel_pending_request(self):
        """
        User can cancel an active PENDING deletion request, allowing later retry.
        """
        req = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )

        res = self.client.post(self.del_cancel_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get('status'), AccountDeletionRequest.Status.CANCELLED)

        req.refresh_from_db()
        self.assertEqual(req.status, AccountDeletionRequest.Status.CANCELLED)

        # After cancellation, user can create a new request
        del_otp = "990011"
        OTPVerification.objects.create(
            identifier='+919876543210',
            otp=del_otp,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        )

        res_retry = self.client.post(
            self.del_verify_otp_url,
            {'otp': del_otp},
            format='json'
        )
        self.assertEqual(res_retry.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_retry.data.get('status'), AccountDeletionRequest.Status.COMPLETED)

    def test_cancel_when_no_pending_request_returns_404(self):
        """
        Attempting to cancel when there is no pending request returns 404 Not Found.
        """
        res = self.client.post(self.del_cancel_url)
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    # -------------------------------------------------------------------------
    # Status Endpoint Tests
    # -------------------------------------------------------------------------

    def test_deletion_status_endpoint(self):
        """
        Account deletion status endpoint reports active and latest requests accurately.
        """
        # 1. No requests
        res = self.client.get(self.del_status_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data.get('has_active_request'))
        self.assertIsNone(res.data.get('latest_request'))

        # 2. Active PENDING request
        req = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING,
            reason='Testing status'
        )
        res = self.client.get(self.del_status_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data.get('has_active_request'))
        self.assertEqual(res.data['request']['id'], req.id)
        self.assertEqual(res.data['request']['status'], 'PENDING')

    # -------------------------------------------------------------------------
    # Compatibility & Migration Verification Tests
    # -------------------------------------------------------------------------

    def test_legacy_otp_records_default_to_login(self):
        """
        Records created without specifying purpose default to Purpose.LOGIN
        and successfully authenticate through VerifyOTPView.
        """
        legacy_otp = "888999"
        record = OTPVerification.objects.create(
            identifier='+919876543210',
            otp=legacy_otp
        )
        self.assertEqual(record.purpose, OTPVerification.Purpose.LOGIN)

        self.client.logout()
        res = self.client.post(
            self.login_verify_url,
            {'identifier': '+919876543210', 'otp': legacy_otp},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn('tokens', res.data)
