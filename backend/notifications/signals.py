from django.db.models.signals import post_save
from django.dispatch import receiver
from django.db import transaction
import logging
from courses.models import Enrollment
from .services import NotificationService
from .models import NotificationType

logger = logging.getLogger(__name__)

@receiver(post_save, sender=Enrollment)
def enrollment_created_signal(sender, instance, created, **kwargs):
    if created:
        def send_enrollment_notification():
            try:
                NotificationService.create_notification(
                    recipient=instance.user,
                    title="You're enrolled!",
                    body=f"You're enrolled in {instance.course.title}. Start learning today!",
                    notification_type=NotificationType.ENROLLMENT,
                    action_url=f"/courses/{instance.course.id}/learn",
                    idempotency_key=f"enrollment:{instance.id}:created"
                )
            except Exception as e:
                logger.error(
                    f"Failed to create enrollment notification for enrollment {instance.id}: {str(e)}",
                    exc_info=True
                )

        connection = transaction.get_connection()
        if connection.in_atomic_block:
            transaction.on_commit(send_enrollment_notification)
        else:
            send_enrollment_notification()
