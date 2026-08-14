from django.db import IntegrityError, transaction
from .models import Notification

class NotificationService:
    @staticmethod
    def create_notification(recipient, title, body, notification_type, action_url="", idempotency_key=None):
        """
        Creates a notification safely, preventing duplicate creations using the idempotency_key.
        Guarantees concurrency-safety by wrapping the database write in an atomic block and
        handling IntegrityErrors safely.

        Returns:
            (notification_instance, created_boolean)
        """
        if idempotency_key:
            try:
                with transaction.atomic():
                    notification = Notification.objects.create(
                        recipient=recipient,
                        title=title,
                        body=body,
                        notification_type=notification_type,
                        action_url=action_url,
                        idempotency_key=idempotency_key
                    )
                return notification, True
            except IntegrityError as e:
                # IntegrityError was raised. Let's check if the idempotency_key already exists.
                try:
                    notification = Notification.objects.get(idempotency_key=idempotency_key)
                    return notification, False
                except Notification.DoesNotExist:
                    # The IntegrityError was caused by a different constraint. Raise it.
                    raise e
        else:
            notification = Notification.objects.create(
                recipient=recipient,
                title=title,
                body=body,
                notification_type=notification_type,
                action_url=action_url
            )
            return notification, True

    @staticmethod
    def trigger_payment_success(purchase, previous_status):
        """
        Triggers payment success notification if the purchase status transitions to SUCCESS.
        """
        if previous_status != 'SUCCESS':
            import logging
            logger = logging.getLogger(__name__)

            def send_notification():
                try:
                    NotificationService.create_notification(
                        recipient=purchase.user,
                        title="Payment successful",
                        body=f"Your payment of ₹{purchase.amount} for {purchase.course.title} was completed successfully.",
                        notification_type="PAYMENT",
                        action_url="/dashboard",
                        idempotency_key=f"payment:{purchase.id}:success"
                    )
                except Exception as e:
                    logger.error(f"Failed to create payment success notification for purchase {purchase.id}: {str(e)}", exc_info=True)

            connection = transaction.get_connection()
            if connection.in_atomic_block:
                transaction.on_commit(send_notification)
            else:
                send_notification()
