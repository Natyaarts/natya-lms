"""
Phase 3.4.5 -- subscription grace-period notification. Mirrors
courses/tasks.py::send_class_reminder's exact shape: scheduled via
apply_async(eta=...) (see orders/views.py::_sync_subscription_state, where
the task is queued the moment a subscription first enters a payment-trouble
state and Subscription.access_until is set), and guarded by comparing an
"expected state at schedule time" snapshot against the current DB row
before doing anything -- so a stale firing (the subscription recovered, was
cancelled, or entered a NEW trouble episode with a different deadline since
this was scheduled) is a safe no-op, exactly like a rescheduled LiveClass
makes its pending reminder tasks no-op.

This task does NOT enforce access cutoff -- read-time evaluation
(courses/services/access.py) already does that the instant
Subscription.access_until passes, with no Celery involvement at all. Its
only job is the grace-period-expired notification.
"""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def notify_subscription_grace_period_expired(self, subscription_id, expected_access_until_timestamp):
    from django.utils import timezone
    from notifications.models import NotificationType
    from notifications.services import NotificationService
    from .models import Subscription

    try:
        subscription = Subscription.objects.select_related('user', 'plan').get(id=subscription_id)
    except Subscription.DoesNotExist:
        logger.info(f"Grace-period notification skipped: Subscription {subscription_id} does not exist.")
        return

    if subscription.access_until is None:
        logger.info(f"Grace-period notification skipped: Subscription {subscription_id} recovered (access_until cleared).")
        return

    current_timestamp = int(subscription.access_until.timestamp())
    if current_timestamp != expected_access_until_timestamp:
        logger.info(
            f"Grace-period notification skipped: Subscription {subscription_id} access_until changed "
            f"since this was scheduled (expected={expected_access_until_timestamp}, current={current_timestamp})."
        )
        return

    if subscription.status not in (Subscription.Status.PENDING, Subscription.Status.HALTED):
        logger.info(
            f"Grace-period notification skipped: Subscription {subscription_id} is no longer in a "
            f"payment-trouble state (status={subscription.status})."
        )
        return

    if subscription.access_until > timezone.now():
        # Shouldn't happen given eta-based scheduling, but never notify
        # early if it somehow fires before the deadline actually passes.
        logger.info(f"Grace-period notification skipped: Subscription {subscription_id} access_until has not passed yet.")
        return

    try:
        NotificationService.create_notification(
            recipient=subscription.user,
            title="Your subscription access has ended",
            body=(
                f"We were unable to process your payment for {subscription.plan.name}, and the grace "
                f"period has now ended. Your subscription-based access has ended -- any courses you "
                f"purchased individually remain fully accessible."
            ),
            notification_type=NotificationType.PAYMENT,
            idempotency_key=f"subscription:{subscription.id}:grace_expired:{current_timestamp}",
        )
    except Exception as e:
        logger.error(
            f"Failed to create grace-period-expired notification for subscription {subscription.id}: {e}",
            exc_info=True,
        )
