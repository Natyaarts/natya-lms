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

    @staticmethod
    def trigger_order_payment_success(order, previous_status):
        """
        Phase 3.3 sibling of trigger_payment_success() for the new Order
        model -- same transition-guard/on_commit/idempotency-key shape,
        same reused "PAYMENT" notification_type (no new type added, per
        the Phase 3.3 brief). Per-course "You're enrolled!" notifications
        are NOT duplicated here -- those already fire automatically from
        the existing Enrollment post_save signal (notifications/signals.py)
        for every Enrollment created during order fulfillment, whether it
        came from a single course item or every course inside a bundle.
        """
        if previous_status != 'PAID':
            import logging
            logger = logging.getLogger(__name__)

            def send_notification():
                try:
                    NotificationService.create_notification(
                        recipient=order.user,
                        title="Payment successful",
                        body=f"Your payment of ₹{order.total_amount} for order {order.order_number} was completed successfully.",
                        notification_type="PAYMENT",
                        action_url="/dashboard",
                        idempotency_key=f"order:{order.id}:success"
                    )
                except Exception as e:
                    logger.error(f"Failed to create payment success notification for order {order.id}: {str(e)}", exc_info=True)

            connection = transaction.get_connection()
            if connection.in_atomic_block:
                transaction.on_commit(send_notification)
            else:
                send_notification()

class LiveClassNotificationService:
    @staticmethod
    def _get_assigned_students(live_class):
        from courses.models import LiveBatchStudent
        return [
            lbs.student for lbs in LiveBatchStudent.objects.filter(
                batch=live_class.batch,
                student__is_active=True
            ).select_related('student')
        ]

    @staticmethod
    def notify_scheduled(live_class):
        from .models import NotificationType
        import logging
        logger = logging.getLogger(__name__)

        students = LiveClassNotificationService._get_assigned_students(live_class)
        for student in students:
            try:
                NotificationService.create_notification(
                    recipient=student,
                    title=f"Live Class Scheduled: {live_class.title}",
                    body=f"A new live class has been scheduled for {live_class.course.title} on {live_class.scheduled_start.strftime('%b %d, %Y %H:%M')}.",
                    notification_type=NotificationType.LIVE_CLASS,
                    action_url=f"/courses/{live_class.course.id}/live",
                    idempotency_key=f"liveclass:{live_class.id}:scheduled:{student.id}"
                )
            except Exception as e:
                logger.error(f"Failed to create scheduled notification for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)

    @staticmethod
    def notify_rescheduled(live_class, old_scheduled_start):
        from .models import NotificationType
        import logging
        logger = logging.getLogger(__name__)

        if live_class.scheduled_start == old_scheduled_start:
            return

        students = LiveClassNotificationService._get_assigned_students(live_class)
        new_timestamp = int(live_class.scheduled_start.timestamp())
        for student in students:
            try:
                NotificationService.create_notification(
                    recipient=student,
                    title=f"Live Class Rescheduled: {live_class.title}",
                    body=f"The live class for {live_class.course.title} has been rescheduled from {old_scheduled_start.strftime('%b %d, %Y %H:%M')} to {live_class.scheduled_start.strftime('%b %d, %Y %H:%M')}.",
                    notification_type=NotificationType.LIVE_CLASS,
                    action_url=f"/courses/{live_class.course.id}/live",
                    idempotency_key=f"liveclass:{live_class.id}:rescheduled:{new_timestamp}:{student.id}"
                )
            except Exception as e:
                logger.error(f"Failed to create rescheduled notification for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)

    @staticmethod
    def notify_cancelled(live_class):
        from .models import NotificationType
        import logging
        logger = logging.getLogger(__name__)

        students = LiveClassNotificationService._get_assigned_students(live_class)
        for student in students:
            try:
                NotificationService.create_notification(
                    recipient=student,
                    title=f"Live Class Cancelled: {live_class.title}",
                    body=f"The live class for {live_class.course.title} scheduled on {live_class.scheduled_start.strftime('%b %d, %Y %H:%M')} has been cancelled.",
                    notification_type=NotificationType.LIVE_CLASS,
                    action_url=f"/courses/{live_class.course.id}/live",
                    idempotency_key=f"liveclass:{live_class.id}:cancelled:{student.id}"
                )
            except Exception as e:
                logger.error(f"Failed to create cancelled notification for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)
