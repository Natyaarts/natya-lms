from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from courses.models import Certificate, Course
from finance.models import Invoice
from notifications.models import DeviceToken
from orders.models import Subscription, SubscriptionPlan
from users.models import AccountDeletionRequest, OTPVerification, TeacherProfile, User
from users.services.deletion import AccountDeletionService


class AccountDeletionLifecycleTests(APITestCase):
    """
    Stage 2D Step 2B, Phase 2: Tests for the full account deletion lifecycle.
    Verifies immediate access cutoff, JWT blacklisting, PII anonymization,
    financial record preservation, certificate preservation, Razorpay resilience,
    S3 cleanup queueing, and cancellation safety.
    """

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            username='lifecycle_student',
            email='student_lifecycle@example.com',
            phone_number='+919876543210',
            first_name='Ananya',
            last_name='Nair',
            parent_name='Ramesh Nair',
            parent_phone='+919876543219',
            onboarding_data={'experience': 'beginner'}
        )
        self.del_verify_otp_url = reverse('delete-account-verify-otp')
        self.del_cancel_url = reverse('delete-account-cancel')
        self.del_status_url = reverse('delete-account-status')
        self.me_url = reverse('current-user')
        self.token_refresh_url = reverse('mobile-token-refresh')

    # -------------------------------------------------------------------------
    # 1. Immediate Access Cutoff & Token Revocation
    # -------------------------------------------------------------------------

    def test_immediate_access_cutoff_and_jwt_blacklisting(self):
        """
        When deletion completes:
        - user.is_active is set to False
        - Existing access tokens are rejected with 401 on authenticated endpoints
        - Refresh tokens cannot be refreshed (401)
        """
        refresh = RefreshToken.for_user(self.user)
        access_token = str(refresh.access_token)
        refresh_token = str(refresh)

        # Confirm access token works initially
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {access_token}')
        res_before = self.client.get(self.me_url)
        self.assertEqual(res_before.status_code, status.HTTP_200_OK)

        # Create deletion OTP and verify it to trigger lifecycle
        del_otp = "123456"
        OTPVerification.objects.create(
            identifier='+919876543210',
            otp=del_otp,
            purpose=OTPVerification.Purpose.ACCOUNT_DELETION
        )

        res_del = self.client.post(
            self.del_verify_otp_url,
            {'otp': del_otp, 'identifier': '+919876543210'},
            format='json'
        )
        self.assertEqual(res_del.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res_del.data.get('status'), AccountDeletionRequest.Status.COMPLETED)

        # 1. User is inactive
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)

        # 2. Existing access token is rejected
        res_after = self.client.get(self.me_url)
        self.assertEqual(res_after.status_code, status.HTTP_401_UNAUTHORIZED)

        # 3. Refreshing the refresh token fails
        self.client.credentials()  # clear bearer header
        res_refresh = self.client.post(
            self.token_refresh_url,
            {'refresh': refresh_token},
            format='json'
        )
        self.assertEqual(res_refresh.status_code, status.HTTP_401_UNAUTHORIZED)

    # -------------------------------------------------------------------------
    # 2. PII Anonymization
    # -------------------------------------------------------------------------

    def test_user_personal_data_anonymization(self):
        """
        Personal identifying information is anonymized, password unusable,
        and onboarding data cleared.
        """
        del_request = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )

        AccountDeletionService.execute_deletion(del_request.id)

        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertIsNone(self.user.phone_number)
        self.assertTrue(self.user.email.startswith(f"deleted_{self.user.id}_"))
        self.assertTrue(self.user.email.endswith("@deleted.local"))
        self.assertTrue(self.user.username.startswith(f"deleted_{self.user.id}_"))
        self.assertEqual(self.user.first_name, "")
        self.assertEqual(self.user.last_name, "")
        self.assertIsNone(self.user.parent_name)
        self.assertIsNone(self.user.parent_phone)
        self.assertEqual(self.user.onboarding_data, {})
        self.assertFalse(self.user.has_usable_password())

    # -------------------------------------------------------------------------
    # 3. Preservation of Protected Financial Records
    # -------------------------------------------------------------------------

    def test_protected_financial_records_preserved_without_error(self):
        """
        Invoices and Payouts referencing User with on_delete=PROTECT
        remain intact and linked to the anonymized user. No ProtectedError is raised.
        """
        from finance.models import Payout

        course = Course.objects.create(
            title="Accounting Protected Course",
            price=Decimal('1000.00'),
            is_published=True
        )
        from orders.models import Purchase
        purchase = Purchase.objects.create(
            user=self.user,
            course=course,
            status=Purchase.Status.SUCCESS,
            amount=Decimal('1000.00'),
            razorpay_order_id='order_prot_123',
            razorpay_payment_id='pay_prot_123'
        )
        invoice = Invoice.objects.create(
            customer=self.user,
            invoice_number='INV-TEST-001',
            purchase=purchase,
            amount=Decimal('1000.00'),
            payment_date=timezone.now()
        )
        payout = Payout.objects.create(
            recipient=self.user,
            period_start=timezone.now().date(),
            period_end=timezone.now().date(),
            gross_amount=Decimal('500.00'),
            net_amount=Decimal('500.00')
        )

        del_request = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )

        # Execution must succeed without ProtectedError
        completed_request = AccountDeletionService.execute_deletion(del_request.id)
        self.assertEqual(completed_request.status, AccountDeletionRequest.Status.COMPLETED)

        # Invoice and Payout still exist and point to the same (anonymized) user PK
        invoice.refresh_from_db()
        self.assertEqual(invoice.customer_id, self.user.id)
        self.assertEqual(invoice.invoice_number, 'INV-TEST-001')

        payout.refresh_from_db()
        self.assertEqual(payout.recipient_id, self.user.id)

    # -------------------------------------------------------------------------
    # 4. Certificate Preservation & Snapshot Verification
    # -------------------------------------------------------------------------

    def test_certificate_records_and_snapshots_preserved(self):
        """
        Issued certificates survive account deletion and maintain their frozen
        snapshots for third-party verification.
        """
        course = Course.objects.create(
            title="Bharatanatyam Foundations",
            price=Decimal('2999.00'),
            is_published=True
        )

        cert = Certificate.objects.create(
            student=self.user,
            course=course,
            learner_name_snapshot="Ananya Nair",
            course_title_snapshot="Bharatanatyam Foundations"
        )
        verification_id = cert.verification_id

        del_request = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )
        AccountDeletionService.execute_deletion(del_request.id)

        # Certificate record still exists
        cert.refresh_from_db()
        self.assertEqual(cert.student_id, self.user.id)
        self.assertEqual(cert.verification_id, verification_id)
        self.assertEqual(cert.learner_name_snapshot, "Ananya Nair")
        self.assertEqual(cert.course_title_snapshot, "Bharatanatyam Foundations")

    # -------------------------------------------------------------------------
    # 5. Idempotency of Deletion Service
    # -------------------------------------------------------------------------

    def test_account_deletion_service_is_idempotent(self):
        """
        Calling execute_deletion multiple times on the same request is a safe no-op.
        """
        del_request = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )

        first_exec = AccountDeletionService.execute_deletion(del_request.id)
        self.assertEqual(first_exec.status, AccountDeletionRequest.Status.COMPLETED)
        completed_at = first_exec.completed_at

        # Second execution
        second_exec = AccountDeletionService.execute_deletion(del_request.id)
        self.assertEqual(second_exec.status, AccountDeletionRequest.Status.COMPLETED)
        self.assertEqual(second_exec.completed_at, completed_at)

    # -------------------------------------------------------------------------
    # 6. Razorpay Subscription Cancellation: Success & Failure Resilience
    # -------------------------------------------------------------------------

    @patch('razorpay.Client')
    def test_razorpay_cancellation_success(self, mock_razorpay_client_class):
        """
        Active subscription is cancelled in Razorpay and marked CANCELLED locally.
        """
        mock_client = MagicMock()
        mock_razorpay_client_class.return_value = mock_client
        mock_client.subscription.cancel.return_value = {'id': 'sub_live_123', 'status': 'cancelled'}

        plan = SubscriptionPlan.objects.create(
            name="Monthly Classical",
            billing_interval="MONTHLY",
            price=Decimal('999.00'),
            razorpay_plan_id="plan_live_123"
        )
        sub = Subscription.objects.create(
            user=self.user,
            plan=plan,
            razorpay_subscription_id="sub_live_123",
            status=Subscription.Status.ACTIVE
        )

        del_request = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )

        AccountDeletionService.execute_deletion(del_request.id)

        # Razorpay cancel called
        mock_client.subscription.cancel.assert_called_once_with('sub_live_123', {'cancel_at_cycle_end': 0})
        sub.refresh_from_db()
        self.assertEqual(sub.status, Subscription.Status.CANCELLED)

        del_request.refresh_from_db()
        self.assertEqual(del_request.cleanup_log['razorpay_cancellations']['status'], 'COMPLETED')

    @patch('razorpay.Client')
    def test_razorpay_cancellation_failure_does_not_block_deactivation(self, mock_razorpay_client_class):
        """
        If Razorpay API times out or raises an error, account deactivation and anonymization
        still succeed, and the error is captured for retry in cleanup_log.
        """
        mock_client = MagicMock()
        mock_razorpay_client_class.return_value = mock_client
        mock_client.subscription.cancel.side_effect = Exception("Razorpay 504 Gateway Timeout")

        plan = SubscriptionPlan.objects.create(
            name="Monthly Classical",
            billing_interval="MONTHLY",
            price=Decimal('999.00'),
            razorpay_plan_id="plan_live_123"
        )
        sub = Subscription.objects.create(
            user=self.user,
            plan=plan,
            razorpay_subscription_id="sub_live_timeout",
            status=Subscription.Status.ACTIVE
        )

        del_request = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )

        with patch('users.tasks.retry_razorpay_cancellation_task.delay') as mock_task_delay:
            AccountDeletionService.execute_deletion(del_request.id)

        # User is still deactivated and anonymized
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_active)
        self.assertIsNone(self.user.phone_number)

        # Deletion request reached COMPLETED
        del_request.refresh_from_db()
        self.assertEqual(del_request.status, AccountDeletionRequest.Status.COMPLETED)

        # Error captured in cleanup_log
        razorpay_log = del_request.cleanup_log.get('razorpay_cancellations', {})
        self.assertEqual(razorpay_log.get('status'), 'PARTIAL_OR_FAILED')
        self.assertEqual(razorpay_log['items'][0]['status'], 'FAILED')
        self.assertIn("504 Gateway Timeout", razorpay_log['items'][0]['error'])

        # Retry task queued
        mock_task_delay.assert_called_once_with(del_request.id, sub.id)

    # -------------------------------------------------------------------------
    # 7. S3 Exclusive Media Cleanup Queueing
    # -------------------------------------------------------------------------

    def test_s3_exclusive_media_cleanup_queued(self):
        """
        User profile photo is queued for S3 cleanup.
        """
        TeacherProfile.objects.create(
            user=self.user,
            profile_image='profiles/teachers/ananya_profile.jpg'
        )

        del_request = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )

        with patch('users.tasks.cleanup_deleted_user_media_task.delay') as mock_media_cleanup:
            AccountDeletionService.execute_deletion(del_request.id)

        del_request.refresh_from_db()
        s3_log = del_request.cleanup_log.get('s3_files', {})
        self.assertEqual(s3_log.get('status'), 'QUEUED')
        self.assertIn('profiles/teachers/ananya_profile.jpg', s3_log.get('files', []))
        mock_media_cleanup.assert_called_once_with(del_request.id, ['profiles/teachers/ananya_profile.jpg'])

    # -------------------------------------------------------------------------
    # 8. Device Tokens Cleared
    # -------------------------------------------------------------------------

    def test_device_tokens_cleared_on_deletion(self):
        """
        Push notification DeviceTokens are immediately deleted.
        """
        DeviceToken.objects.create(
            user=self.user,
            token="fcm_token_12345",
            platform="android"
        )
        self.assertTrue(DeviceToken.objects.filter(user=self.user).exists())

        del_request = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )
        AccountDeletionService.execute_deletion(del_request.id)

        self.assertFalse(DeviceToken.objects.filter(user=self.user).exists())

    # -------------------------------------------------------------------------
    # 9. Cancellation Boundary: Rejected Once Deletion Completed
    # -------------------------------------------------------------------------

    def test_cancel_endpoint_rejects_completed_request(self):
        """
        A completed deletion request cannot be cancelled.
        """
        del_request = AccountDeletionRequest.objects.create(
            user=self.user,
            status=AccountDeletionRequest.Status.PENDING
        )
        AccountDeletionService.execute_deletion(del_request.id)

        # Authenticate (even though user is inactive, test client force_authenticate bypasses)
        self.client.force_authenticate(user=self.user)
        res = self.client.post(self.del_cancel_url)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already processing or completed", res.data.get('error', ''))
