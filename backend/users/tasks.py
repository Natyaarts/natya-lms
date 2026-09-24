import logging
from celery import shared_task

logger = logging.getLogger('users.tasks')


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def process_account_deletion_task(self, deletion_request_id: int):
    """
    Asynchronous Celery task to execute account deletion lifecycle.
    """
    from users.services.deletion import AccountDeletionService

    try:
        AccountDeletionService.execute_deletion(deletion_request_id)
    except Exception as exc:
        logger.error(
            "process_account_deletion_task failed for request %s: %s",
            deletion_request_id,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=5, default_retry_delay=120)
def retry_razorpay_cancellation_task(self, deletion_request_id: int, subscription_id: int):
    """
    Bounded retry task for subscriptions whose Razorpay cancellation failed or timed out.
    """
    from django.conf import settings
    from django.utils import timezone
    import razorpay
    from orders.models import Subscription
    from users.models import AccountDeletionRequest

    try:
        sub = Subscription.objects.get(id=subscription_id)
        if sub.status in Subscription.TERMINAL_STATUSES:
            logger.info("Subscription %s already in terminal state; retry skipped.", subscription_id)
            return

        key_id = getattr(settings, 'RAZORPAY_KEY_ID', '') or ''
        key_secret = getattr(settings, 'RAZORPAY_KEY_SECRET', '') or ''
        client = razorpay.Client(auth=(key_id, key_secret))

        client.subscription.cancel(sub.razorpay_subscription_id, {'cancel_at_cycle_end': 0})
        sub.status = Subscription.Status.CANCELLED
        sub.cancelled_at = timezone.now()
        sub.save(update_fields=['status', 'cancelled_at'])

        # Update cleanup_log
        try:
            req = AccountDeletionRequest.objects.get(id=deletion_request_id)
            log = req.cleanup_log or {}
            cancellations = log.get('razorpay_cancellations', {}).get('items', [])
            for item in cancellations:
                if item.get('subscription_id') == subscription_id:
                    item['status'] = 'SUCCESS_ON_RETRY'
                    item['resolved_at'] = timezone.now().isoformat()
            req.cleanup_log = log
            req.save(update_fields=['cleanup_log', 'updated_at'])
        except Exception:
            pass

        logger.info("Successfully cancelled subscription %s on retry.", subscription_id)
    except Exception as exc:
        logger.error(
            "retry_razorpay_cancellation_task failed for sub %s: %s",
            subscription_id,
            exc,
            exc_info=True,
        )
        raise self.retry(exc=exc)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def cleanup_deleted_user_media_task(self, deletion_request_id: int, file_paths: list):
    """
    Deletes user-exclusive media files from storage (S3/local).
    Never touches invoice PDFs or course media.
    """
    from django.core.files.storage import default_storage
    from django.utils import timezone
    from users.models import AccountDeletionRequest

    deleted_count = 0
    failed_files = []

    for path in file_paths:
        try:
            if default_storage.exists(path):
                default_storage.delete(path)
                deleted_count += 1
        except Exception as e:
            logger.error("Error deleting file %s: %s", path, e)
            failed_files.append({'path': path, 'error': str(e)})

    try:
        req = AccountDeletionRequest.objects.get(id=deletion_request_id)
        log = req.cleanup_log or {}
        s3_log = log.get('s3_files', {})
        s3_log['status'] = 'COMPLETED' if not failed_files else 'PARTIAL'
        s3_log['deleted_count'] = deleted_count
        s3_log['failed_files'] = failed_files
        s3_log['completed_at'] = timezone.now().isoformat()
        req.cleanup_log = log
        req.save(update_fields=['cleanup_log', 'updated_at'])
    except Exception:
        pass

    if failed_files and self.request.retries < self.max_retries:
        raise self.retry(exc=Exception(f"Failed deleting {len(failed_files)} files"))
