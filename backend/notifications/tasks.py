"""
Push delivery gap fix. Extends this project's EXISTING Celery pattern
(courses/tasks.py's send_class_reminder, orders/tasks.py's
notify_subscription_grace_period_expired -- both @shared_task, both
already use NotificationService) rather than introducing a second/
parallel task-queue architecture.

Exactly one task: deliver an already-created, already-persisted
Notification row to every active DeviceToken its recipient owns, via
Expo. This never creates/modifies/deletes the Notification row itself --
the in-app notification (list, unread-count, mark-read) is entirely
unaffected by whether push delivery succeeds, partially succeeds, or
fails outright.
"""
import logging

from celery import shared_task

from .push import ExpoPushTransientError, send_expo_push_messages

logger = logging.getLogger(__name__)

# DeviceNotRegistered is Expo's own documented terminal error code -- the
# token was for an app install that no longer exists (uninstalled, or the
# OS revoked it). It will never succeed again, so the corresponding
# DeviceToken is deactivated rather than left to fail forever on every
# future notification. Every OTHER Expo error (MessageTooBig,
# MessageRateExceeded, InvalidCredentials, etc.) is logged but leaves the
# token active -- those are about this specific message, not proof the
# token itself is dead.
_PERMANENTLY_INVALID_ERROR_CODES = {"DeviceNotRegistered"}


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_push_notification_for_notification(self, notification_id):
    """
    Best-effort, asynchronous push delivery for one Notification. Safe
    against:
      - the Notification no longer existing (race with a concurrent
        delete, or a test calling this directly) -- logs and returns.
      - the recipient having no active DeviceToken at all -- returns
        immediately, no error, nothing to send.
      - a transient Expo/network failure -- retried up to max_retries
        times with a backoff delay (Celery's own retry mechanism);
        NEVER retried endlessly (max_retries=3, not unbounded).
      - a permanently invalid (DeviceNotRegistered) token -- deactivated
        on that ticket alone, no retry, every other token in the same
        batch is still processed normally.
    """
    from .models import DeviceToken, Notification

    try:
        notification = Notification.objects.select_related('recipient').get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.warning(f"Push delivery: Notification {notification_id} no longer exists; skipping.")
        return

    device_tokens = list(DeviceToken.objects.filter(user=notification.recipient, is_active=True))
    if not device_tokens:
        return

    messages = [
        {
            "to": device_token.token,
            "title": notification.title,
            "body": notification.body,
            "data": {
                "notification_id": notification.id,
                "notification_type": notification.notification_type,
                "action_url": notification.action_url,
            },
        }
        for device_token in device_tokens
    ]

    try:
        tickets = send_expo_push_messages(messages)
    except ExpoPushTransientError as exc:
        logger.warning(f"Push delivery: transient failure for notification {notification_id}, retrying: {exc}")
        raise self.retry(exc=exc)
    except Exception:
        # An unexpected (non-transient-classified) error -- logged, not
        # retried (retrying a bug indefinitely helps no one), and never
        # allowed to propagate into a task-failure state that could ever
        # be mistaken for "the Notification itself failed."
        logger.exception(f"Push delivery: unexpected error sending notification {notification_id}")
        return

    invalid_token_ids = []
    for device_token, ticket in zip(device_tokens, tickets):
        status_value = ticket.get("status")
        if status_value == "error":
            error_code = (ticket.get("details") or {}).get("error")
            if error_code in _PERMANENTLY_INVALID_ERROR_CODES:
                invalid_token_ids.append(device_token.id)
            else:
                logger.warning(
                    f"Push delivery: Expo reported an error for DeviceToken {device_token.id} "
                    f"(notification {notification_id}): {ticket.get('message')}"
                )

    if invalid_token_ids:
        DeviceToken.objects.filter(id__in=invalid_token_ids).update(is_active=False)
