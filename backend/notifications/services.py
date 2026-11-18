import logging

from django.db import IntegrityError, transaction
from .models import Notification

logger = logging.getLogger(__name__)

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
                NotificationService._schedule_push_delivery(notification)
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
            NotificationService._schedule_push_delivery(notification)
            return notification, True

    @staticmethod
    def _schedule_push_delivery(notification):
        """
        Push delivery gap fix. Enqueues the Celery push-delivery task for
        this notification -- ONLY called for a genuinely newly-created
        row (never for an idempotent duplicate hit, which represents an
        already-notified event and must never re-push).

        Deferred via transaction.on_commit so the Celery worker never
        races ahead of the Notification row actually being committed and
        visible (works correctly whether or not the caller wrapped this
        in its own transaction.atomic() -- if there is none, on_commit
        fires immediately, which is exactly Django's documented
        behavior). Both the registration of the callback AND the body of
        the callback itself are wrapped in try/except: creating a
        Notification must never fail, and never raise back into the
        caller's request/task, merely because push delivery (or even
        enqueueing it) fails -- Celery/Redis being unreachable must be no
        different from Expo itself being unreachable, from the point of
        view of "did the Notification get created."
        """
        def _enqueue():
            try:
                from .tasks import send_push_notification_for_notification
                send_push_notification_for_notification.delay(notification.id)
            except Exception:
                logger.exception(f"Push delivery: failed to enqueue Celery task for notification {notification.id}")

        try:
            transaction.on_commit(_enqueue)
        except Exception:
            logger.exception(f"Push delivery: failed to register on_commit callback for notification {notification.id}")

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

class InteraktTransientError(Exception):
    """Raised on transient HTTP 429/5xx/network errors from Interakt."""
    pass


class InteraktPermanentError(Exception):
    """Raised on non-retryable 4xx errors or unsafe payload errors from Interakt."""
    pass


class InteraktWhatsAppService:
    INTERAKT_API_URL = "https://api.interakt.ai/v1/public/message/"

    @staticmethod
    def _contains_forbidden_zoom_url(values) -> bool:
        if not values:
            return False
        items = values.values() if isinstance(values, dict) else values
        for v in items:
            if isinstance(v, (list, tuple)):
                if InteraktWhatsAppService._contains_forbidden_zoom_url(v):
                    return True
            else:
                text = str(v or "").lower()
                if "zoom.us" in text or "zak=" in text:
                    return True
        return False

    @classmethod
    def send_template(cls, phone_number: str, template_name: str, body_values: list,
                      button_values: dict = None, language_code: str = "en"):
        import os
        import requests
        import logging
        from django.conf import settings

        logger = logging.getLogger(__name__)
        if cls._contains_forbidden_zoom_url(body_values) or cls._contains_forbidden_zoom_url(button_values):
            raise InteraktPermanentError("Refusing to send raw Zoom URL or ZAK token in WhatsApp template.")

        clean_phone = (phone_number or "").replace(" ", "").replace("-", "").strip().lstrip("+")
        if not clean_phone:
            raise InteraktPermanentError("Missing recipient phone number.")

        is_staging = getattr(settings, "IS_STAGING", False)
        allow_staging_whatsapp = getattr(settings, "ENABLE_STAGING_WHATSAPP", False) or (
            os.environ.get("ENABLE_STAGING_WHATSAPP", "False").lower() in ("true", "1", "yes")
        )
        if is_staging and not allow_staging_whatsapp:
            logger.info("Staging mode: suppressing outbound Interakt template=%s to %s", template_name, clean_phone[-4:])
            return {"status": "STAGING_SUPPRESSED", "message_id": ""}

        interakt_key = getattr(settings, "INTERAKT_SECRET_KEY", "") or ""
        if getattr(settings, "DEBUG", False) and not interakt_key:
            logger.info("DEBUG mode without INTERAKT_SECRET_KEY: suppressing template=%s to %s", template_name, clean_phone[-4:])
            return {"status": "DEV_SUPPRESSED", "message_id": ""}
        if not interakt_key:
            raise InteraktPermanentError("INTERAKT_SECRET_KEY is not configured.")

        payload = {
            "fullPhoneNumber": clean_phone,
            "type": "Template",
            "template": {
                "name": template_name,
                "languageCode": language_code,
                "bodyValues": [str(v) for v in (body_values or [])],
            },
        }
        if button_values:
            payload["template"]["buttonValues"] = button_values

        headers = {
            "Authorization": f"Basic {interakt_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(cls.INTERAKT_API_URL, json=payload, headers=headers, timeout=10)
        except requests.RequestException as exc:
            raise InteraktTransientError(f"Interakt network error: {exc}") from exc

        if resp.status_code in (200, 201, 202):
            data = {}
            try:
                data = resp.json() if hasattr(resp, "json") else {}
            except Exception:
                data = {}
            msg_id = str(data.get("id") or data.get("messageId") or "")
            return {"status": "SENT", "message_id": msg_id}

        if resp.status_code == 429 or 500 <= resp.status_code < 600:
            raise InteraktTransientError(f"Interakt transient HTTP {resp.status_code}")
        raise InteraktPermanentError(f"Interakt rejected HTTP {resp.status_code}")


class LiveClassNotificationService:
    @staticmethod
    def build_student_join_url(live_class_id: int) -> str:
        from django.conf import settings
        base = getattr(settings, "FRONTEND_BASE_URL", "https://academy.natyaarts.com").rstrip("/")
        return f"{base}/live-classes/join/{live_class_id}"

    @staticmethod
    def _has_ready_join_link(live_class) -> bool:
        from courses.models import LiveClass
        if live_class.meeting_provider == LiveClass.MeetingProvider.ZOOM:
            return bool((live_class.zoom_meeting_id or "").strip() and (live_class.meeting_url or "").strip())
        return True

    @staticmethod
    def _enqueue_whatsapp(student, live_class, event_type: str, template_name: str,
                          body_values: list, button_values: dict, idempotency_key: str):
        if not getattr(student, "whatsapp_opt_in", False):
            return
        if not (getattr(student, "phone_number", None) or "").strip():
            return
        from notifications.tasks import send_live_class_whatsapp_task
        send_live_class_whatsapp_task.delay(
            recipient_id=student.id,
            live_class_id=live_class.id,
            event_type=event_type,
            template_name=template_name,
            body_values=body_values,
            button_values=button_values,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _get_assigned_students(live_class):
        from courses.models import LiveBatchStudent, Enrollment
        if live_class.batch_id:
            return [
                lbs.student for lbs in LiveBatchStudent.objects.filter(
                    batch=live_class.batch,
                    student__is_active=True
                ).select_related('student')
            ]
        if live_class.course_id:
            return [
                enr.user for enr in Enrollment.objects.filter(
                    course=live_class.course,
                    user__is_active=True,
                ).select_related('user')
            ]
        return []

    @staticmethod
    def notify_scheduled(live_class):
        from .models import NotificationType
        from django.conf import settings
        import logging
        logger = logging.getLogger(__name__)

        if not LiveClassNotificationService._has_ready_join_link(live_class):
            logger.info(
                "Skipping scheduled notifications for LiveClass #%s: Zoom meeting URL is not yet provisioned.",
                live_class.id,
            )
            return

        students = LiveClassNotificationService._get_assigned_students(live_class)
        when_str = live_class.scheduled_start.strftime('%b %d, %Y %H:%M')
        template_name = getattr(settings, "INTERAKT_LIVE_SCHEDULED_TEMPLATE", "live_class_scheduled_v1")

        for student in students:
            try:
                NotificationService.create_notification(
                    recipient=student,
                    title=f"Live Class Scheduled: {live_class.title}",
                    body=f"A new live class has been scheduled for {live_class.course.title} on {when_str}.",
                    notification_type=NotificationType.LIVE_CLASS,
                    action_url=f"/live-classes/join/{live_class.id}",
                    idempotency_key=f"liveclass:{live_class.id}:scheduled:{student.id}"
                )
            except Exception as e:
                logger.error(f"Failed to create scheduled notification for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)
            try:
                student_name = f"{student.first_name} {student.last_name}".strip() or student.username
                LiveClassNotificationService._enqueue_whatsapp(
                    student=student,
                    live_class=live_class,
                    event_type="SCHEDULED",
                    template_name=template_name,
                    body_values=[student_name, live_class.course.title, live_class.title, when_str],
                    button_values={"0": [str(live_class.id)]},
                    idempotency_key=f"wa:liveclass:{live_class.id}:scheduled:{student.id}",
                )
            except Exception as e:
                logger.error(f"Failed to enqueue scheduled WhatsApp for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)

    @staticmethod
    def notify_rescheduled(live_class, old_scheduled_start):
        from .models import NotificationType
        from django.conf import settings
        import logging
        logger = logging.getLogger(__name__)

        if live_class.scheduled_start == old_scheduled_start:
            return
        if not LiveClassNotificationService._has_ready_join_link(live_class):
            logger.info(
                "Skipping rescheduled WhatsApp notifications for LiveClass #%s: Zoom meeting URL is not yet provisioned.",
                live_class.id,
            )
            return

        students = LiveClassNotificationService._get_assigned_students(live_class)
        new_timestamp = int(live_class.scheduled_start.timestamp())
        old_str = old_scheduled_start.strftime('%b %d, %Y %H:%M')
        new_str = live_class.scheduled_start.strftime('%b %d, %Y %H:%M')
        join_url = LiveClassNotificationService.build_student_join_url(live_class.id)
        template_name = getattr(settings, "INTERAKT_LIVE_RESCHEDULED_TEMPLATE", "live_class_rescheduled_v1")

        for student in students:
            try:
                NotificationService.create_notification(
                    recipient=student,
                    title=f"Live Class Rescheduled: {live_class.title}",
                    body=f"The live class for {live_class.course.title} has been rescheduled from {old_str} to {new_str}.",
                    notification_type=NotificationType.LIVE_CLASS,
                    action_url=f"/live-classes/join/{live_class.id}",
                    idempotency_key=f"liveclass:{live_class.id}:rescheduled:{new_timestamp}:{student.id}"
                )
            except Exception as e:
                logger.error(f"Failed to create rescheduled notification for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)
            try:
                student_name = f"{student.first_name} {student.last_name}".strip() or student.username
                LiveClassNotificationService._enqueue_whatsapp(
                    student=student,
                    live_class=live_class,
                    event_type="RESCHEDULED",
                    template_name=template_name,
                    body_values=[student_name, live_class.course.title, live_class.title, old_str, new_str, join_url],
                    button_values={"0": [f"live-classes/join/{live_class.id}"]},
                    idempotency_key=f"wa:liveclass:{live_class.id}:rescheduled:{new_timestamp}:{student.id}",
                )
            except Exception as e:
                logger.error(f"Failed to enqueue rescheduled WhatsApp for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)

    @staticmethod
    def notify_cancelled(live_class):
        from .models import NotificationType
        from django.conf import settings
        import logging
        logger = logging.getLogger(__name__)

        students = LiveClassNotificationService._get_assigned_students(live_class)
        when_str = live_class.scheduled_start.strftime('%b %d, %Y %H:%M')
        template_name = getattr(settings, "INTERAKT_LIVE_CANCELLED_TEMPLATE", "live_class_cancelled_v1")

        for student in students:
            try:
                NotificationService.create_notification(
                    recipient=student,
                    title=f"Live Class Cancelled: {live_class.title}",
                    body=f"The live class for {live_class.course.title} scheduled on {when_str} has been cancelled.",
                    notification_type=NotificationType.LIVE_CLASS,
                    action_url="/live-classes",
                    idempotency_key=f"liveclass:{live_class.id}:cancelled:{student.id}"
                )
            except Exception as e:
                logger.error(f"Failed to create cancelled notification for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)
            try:
                student_name = f"{student.first_name} {student.last_name}".strip() or student.username
                LiveClassNotificationService._enqueue_whatsapp(
                    student=student,
                    live_class=live_class,
                    event_type="CANCELLED",
                    template_name=template_name,
                    body_values=[student_name, live_class.course.title, live_class.title, when_str],
                    button_values=None,
                    idempotency_key=f"wa:liveclass:{live_class.id}:cancelled:{student.id}",
                )
            except Exception as e:
                logger.error(f"Failed to enqueue cancelled WhatsApp for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)
