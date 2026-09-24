import logging
import uuid
from typing import Any, Dict

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)

from users.models import AccountDeletionRequest, AdminAuditLog, Mentorship, User

logger = logging.getLogger('users.deletion')


class AccountDeletionService:
    """
    Stage 2D Step 2B, Phase 2: Orchestrates the secure account deletion lifecycle.
    - Idempotent: safe to run multiple times without duplicating or corrupting state.
    - Non-destructive to protected financial records (Invoice, Payout, Refund, LedgerEntry, Order).
    - Preserves Certificate records and frozen snapshots for ongoing verification.
    - Immediately revokes outstanding refresh tokens and sets is_active=False.
    - Anonymizes personal user identity (phone, email, username, names).
    - Deactivates profiles (TeacherProfile, MentorProfile) and removes device tokens.
    - Resiliently cancels Razorpay subscriptions without blocking user deactivation.
    - Safely queues S3 cleanup for user-exclusive files.
    """

    @classmethod
    def execute_deletion(cls, deletion_request_id: int) -> AccountDeletionRequest:
        """
        Executes the deletion lifecycle for the specified AccountDeletionRequest.
        """
        with transaction.atomic():
            deletion_request = (
                AccountDeletionRequest.objects.select_for_update()
                .select_related('user')
                .get(id=deletion_request_id)
            )

            # Idempotency check: if already completed, do nothing
            if deletion_request.status == AccountDeletionRequest.Status.COMPLETED:
                logger.info(
                    "AccountDeletionRequest #%s already COMPLETED; skipping re-execution.",
                    deletion_request_id,
                )
                return deletion_request

            deletion_request.status = AccountDeletionRequest.Status.PROCESSING
            if not deletion_request.confirmed_at:
                deletion_request.confirmed_at = timezone.now()
            deletion_request.save(update_fields=['status', 'confirmed_at', 'updated_at'])

            user = deletion_request.user
            cleanup_log: Dict[str, Any] = deletion_request.cleanup_log or {}

            # Step 1: Invalidate all active tokens and block account access immediately
            cls._revoke_user_tokens(user, cleanup_log)

            # Step 2: Anonymize personal identifying information and mark user inactive
            cls._anonymize_user(user, cleanup_log)

            # Step 3: Clean up non-essential learner records and profiles
            cls._cleanup_non_essential_associations(user, cleanup_log)

        # Step 4: External integrations (Razorpay & S3) performed outside atomic block
        # to ensure database transactions are committed and external network latency
        # or failures never hold database locks.
        cls._cancel_user_subscriptions(user, deletion_request, cleanup_log)
        cls._queue_media_cleanup(user, deletion_request, cleanup_log)

        # Step 5: Mark request COMPLETED and record audit log
        with transaction.atomic():
            deletion_request.refresh_from_db()
            deletion_request.status = AccountDeletionRequest.Status.COMPLETED
            deletion_request.completed_at = timezone.now()
            deletion_request.cleanup_log = cleanup_log
            deletion_request.save(
                update_fields=['status', 'completed_at', 'cleanup_log', 'updated_at']
            )

            AdminAuditLog.record(
                actor=None,
                action="ACCOUNT_DELETION_COMPLETED",
                target_type="User",
                target_id=str(user.id),
                description=f"Account deletion completed for user ID {user.id}. Personal data anonymized.",
                metadata={"deletion_request_id": deletion_request.id},
            )

        logger.info(
            "Account deletion lifecycle completed successfully for user ID %s (Request #%s)",
            user.id,
            deletion_request.id,
        )
        return deletion_request

    @classmethod
    def _revoke_user_tokens(cls, user: User, cleanup_log: Dict[str, Any]) -> None:
        """
        Revoke and blacklist all outstanding SimpleJWT refresh tokens for the user.
        """
        blacklisted_count = 0
        try:
            tokens = OutstandingToken.objects.filter(user=user)
            for token in tokens:
                _, created = BlacklistedToken.objects.get_or_create(token=token)
                if created:
                    blacklisted_count += 1
            cleanup_log['jwt_blacklisting'] = {
                'status': 'SUCCESS',
                'blacklisted_count': blacklisted_count,
                'timestamp': timezone.now().isoformat(),
            }
        except Exception as e:
            logger.error(
                "Error blacklisting JWT tokens for user %s: %s", user.id, e, exc_info=True
            )
            cleanup_log['jwt_blacklisting'] = {
                'status': 'ERROR',
                'error': str(e),
                'timestamp': timezone.now().isoformat(),
            }

    @classmethod
    def _anonymize_user(cls, user: User, cleanup_log: Dict[str, Any]) -> None:
        """
        Anonymizes user PII while preserving the User row to prevent breaking
        PROTECT-constrained financial records (Invoices, Payouts, Refunds).
        """
        orig_id = user.id
        anon_suffix = uuid.uuid4().hex[:8]

        user.is_active = False
        user.phone_number = None
        user.email = f"deleted_{orig_id}_{anon_suffix}@deleted.local"
        user.username = f"deleted_{orig_id}_{anon_suffix}"
        user.first_name = ""
        user.last_name = ""
        user.parent_name = None
        user.parent_phone = None
        user.onboarding_data = {}
        user.set_unusable_password()
        user.save()

        cleanup_log['user_anonymization'] = {
            'status': 'SUCCESS',
            'is_active': False,
            'timestamp': timezone.now().isoformat(),
        }

    @classmethod
    def _cleanup_non_essential_associations(cls, user: User, cleanup_log: Dict[str, Any]) -> None:
        """
        Clean up push tokens, deactivate public profiles, and set mentorships inactive.
        """
        deleted_device_tokens = 0
        try:
            from notifications.models import DeviceToken
            deleted_count, _ = DeviceToken.objects.filter(user=user).delete()
            deleted_device_tokens = deleted_count
        except Exception as e:
            logger.warning("Could not delete DeviceToken for user %s: %s", user.id, e)

        updated_mentorships = 0
        try:
            updated_mentorships = Mentorship.objects.filter(
                student=user, status=Mentorship.Status.ACTIVE
            ).update(status=Mentorship.Status.INACTIVE)
        except Exception as e:
            logger.warning("Could not deactivate mentorships for user %s: %s", user.id, e)

        try:
            if hasattr(user, 'teacher_profile') and user.teacher_profile:
                user.teacher_profile.is_active = False
                user.teacher_profile.is_public = False
                user.teacher_profile.save(update_fields=['is_active', 'is_public'])
        except Exception as e:
            logger.warning("Could not deactivate teacher_profile for user %s: %s", user.id, e)

        try:
            if hasattr(user, 'mentor_profile') and user.mentor_profile:
                user.mentor_profile.is_active = False
                user.mentor_profile.is_public = False
                user.mentor_profile.save(update_fields=['is_active', 'is_public'])
        except Exception as e:
            logger.warning("Could not deactivate mentor_profile for user %s: %s", user.id, e)

        cleanup_log['associations_cleanup'] = {
            'status': 'SUCCESS',
            'deleted_device_tokens': deleted_device_tokens,
            'deactivated_mentorships': updated_mentorships,
            'timestamp': timezone.now().isoformat(),
        }

    @classmethod
    def _cancel_user_subscriptions(
        cls, user: User, deletion_request: AccountDeletionRequest, cleanup_log: Dict[str, Any]
    ) -> None:
        """
        Cancel active recurring Razorpay subscriptions.
        Resilient: API timeouts or gateway errors are logged, persisted in cleanup_log,
        and queued for async retry without blocking user deactivation.
        """
        from orders.models import Subscription

        active_subs = Subscription.objects.filter(
            user=user
        ).exclude(status__in=Subscription.TERMINAL_STATUSES)

        cancellation_results = []
        if not active_subs.exists():
            cleanup_log['razorpay_cancellations'] = {
                'status': 'NONE_ACTIVE',
                'items': [],
            }
            return

        import razorpay
        key_id = getattr(settings, 'RAZORPAY_KEY_ID', '') or ''
        key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '') or ''
        client = razorpay.Client(auth=(key_id, key_secret))

        for sub in active_subs:
            sub_id = sub.razorpay_subscription_id
            if not sub_id:
                sub.status = Subscription.Status.CANCELLED
                sub.cancelled_at = timezone.now()
                sub.save(update_fields=['status', 'cancelled_at'])
                cancellation_results.append({
                    'subscription_id': sub.id,
                    'razorpay_subscription_id': None,
                    'status': 'LOCAL_ONLY_CANCELLED',
                })
                continue

            try:
                # Cancel immediately with Razorpay
                client.subscription.cancel(sub_id, {'cancel_at_cycle_end': 0})
                sub.status = Subscription.Status.CANCELLED
                sub.cancelled_at = timezone.now()
                sub.save(update_fields=['status', 'cancelled_at'])
                cancellation_results.append({
                    'subscription_id': sub.id,
                    'razorpay_subscription_id': sub_id,
                    'status': 'SUCCESS',
                })
                logger.info(
                    "Cancelled Razorpay subscription %s for deleting user %s",
                    sub_id,
                    user.id,
                )
            except Exception as e:
                # Do NOT crash or rollback. Record retryable state.
                logger.error(
                    "Razorpay cancellation failed for sub %s (user %s): %s",
                    sub_id,
                    user.id,
                    e,
                    exc_info=True,
                )
                cancellation_results.append({
                    'subscription_id': sub.id,
                    'razorpay_subscription_id': sub_id,
                    'status': 'FAILED',
                    'error': str(e),
                })
                # Queue retry task via Celery
                try:
                    from users.tasks import retry_razorpay_cancellation_task
                    retry_razorpay_cancellation_task.delay(deletion_request.id, sub.id)
                except Exception as task_err:
                    logger.warning("Could not dispatch retry_razorpay_cancellation_task: %s", task_err)

        cleanup_log['razorpay_cancellations'] = {
            'status': 'COMPLETED' if all(r['status'] == 'SUCCESS' for r in cancellation_results) else 'PARTIAL_OR_FAILED',
            'items': cancellation_results,
            'timestamp': timezone.now().isoformat(),
        }

    @classmethod
    def _queue_media_cleanup(
        cls, user: User, deletion_request: AccountDeletionRequest, cleanup_log: Dict[str, Any]
    ) -> None:
        """
        Identify files confirmed to belong exclusively to the deleted user and queue
        their deletion via Celery background task.
        """
        files_to_delete = []

        try:
            if hasattr(user, 'teacher_profile') and user.teacher_profile and user.teacher_profile.profile_image:
                files_to_delete.append(user.teacher_profile.profile_image.name)
        except Exception:
            pass

        try:
            if hasattr(user, 'mentor_profile') and user.mentor_profile and user.mentor_profile.profile_image:
                files_to_delete.append(user.mentor_profile.profile_image.name)
        except Exception:
            pass

        try:
            from courses.models import AssignmentSubmission
            submissions = AssignmentSubmission.objects.filter(student=user).exclude(submitted_file='')
            for s in submissions:
                if s.submitted_file:
                    files_to_delete.append(s.submitted_file.name)
        except Exception:
            pass

        cleanup_log['s3_files'] = {
            'status': 'QUEUED' if files_to_delete else 'NONE',
            'files': files_to_delete,
            'timestamp': timezone.now().isoformat(),
        }

        if files_to_delete:
            try:
                from users.tasks import cleanup_deleted_user_media_task
                cleanup_deleted_user_media_task.delay(deletion_request.id, files_to_delete)
            except Exception as e:
                logger.warning("Could not dispatch cleanup_deleted_user_media_task: %s", e)
                cleanup_log['s3_files']['dispatch_error'] = str(e)
