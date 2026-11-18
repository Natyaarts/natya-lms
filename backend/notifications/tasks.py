import logging
from celery import shared_task
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def send_live_class_whatsapp_task(
    self,
    recipient_id,
    live_class_id,
    event_type,
    template_name,
    body_values,
    button_values=None,
    idempotency_key="",
):
    """
    Asynchronously dispatches an Interakt WhatsApp live-class notification
    with database-backed idempotency, opt-in enforcement, and bounded retries.
    """
    from django.contrib.auth import get_user_model
    from courses.models import LiveClass
    from notifications.models import WhatsAppDeliveryLog
    from notifications.services import (
        InteraktPermanentError,
        InteraktTransientError,
        InteraktWhatsAppService,
    )

    User = get_user_model()
    if not idempotency_key:
        idempotency_key = f"wa:liveclass:{live_class_id}:{event_type.lower()}:{recipient_id}"

    with transaction.atomic():
        log, _ = WhatsAppDeliveryLog.objects.select_for_update().get_or_create(
            idempotency_key=idempotency_key,
            defaults={
                "recipient_id": recipient_id,
                "live_class_id": live_class_id,
                "event_type": event_type,
                "template_name": template_name,
                "status": WhatsAppDeliveryLog.Status.PENDING,
            },
        )
        if log.status == WhatsAppDeliveryLog.Status.SENT:
            return {"status": "duplicate_ignored", "idempotency_key": idempotency_key}

    recipient = User.objects.filter(id=recipient_id, is_active=True).first()
    if not recipient or not getattr(recipient, "whatsapp_opt_in", False) or not (recipient.phone_number or "").strip():
        WhatsAppDeliveryLog.objects.filter(id=log.id).update(
            status=WhatsAppDeliveryLog.Status.SKIPPED,
            last_error="Recipient inactive, missing phone, or not opted in to WhatsApp notifications.",
            updated_at=timezone.now(),
        )
        return {"status": "skipped", "idempotency_key": idempotency_key}

    live_class = LiveClass.objects.filter(id=live_class_id).first()
    if not live_class:
        WhatsAppDeliveryLog.objects.filter(id=log.id).update(
            status=WhatsAppDeliveryLog.Status.SKIPPED,
            last_error="LiveClass not found.",
            updated_at=timezone.now(),
        )
        return {"status": "skipped", "reason": "missing_live_class"}

    if event_type in ("SCHEDULED", "RESCHEDULED") and live_class.meeting_provider == LiveClass.MeetingProvider.ZOOM:
        if not ((live_class.zoom_meeting_id or "").strip() and (live_class.meeting_url or "").strip()):
            WhatsAppDeliveryLog.objects.filter(id=log.id).update(
                status=WhatsAppDeliveryLog.Status.SKIPPED,
                last_error="Zoom meeting URL not persisted.",
                updated_at=timezone.now(),
            )
            return {"status": "skipped", "reason": "missing_zoom_join_url"}

    try:
        res = InteraktWhatsAppService.send_template(
            phone_number=recipient.phone_number,
            template_name=template_name,
            body_values=body_values,
            button_values=button_values,
        )
        WhatsAppDeliveryLog.objects.filter(id=log.id).update(
            status=WhatsAppDeliveryLog.Status.SENT,
            attempts=log.attempts + 1,
            provider_message_id=res.get("message_id", ""),
            last_error="",
            sent_at=timezone.now(),
            updated_at=timezone.now(),
        )
        return {"status": res.get("status", "SENT"), "idempotency_key": idempotency_key}
    except InteraktTransientError as exc:
        next_attempts = log.attempts + 1
        if self.request.retries >= self.max_retries:
            WhatsAppDeliveryLog.objects.filter(id=log.id).update(
                status=WhatsAppDeliveryLog.Status.FAILED,
                attempts=next_attempts,
                last_error=str(exc),
                updated_at=timezone.now(),
            )
            return {"status": "failed", "error": str(exc), "idempotency_key": idempotency_key}

        WhatsAppDeliveryLog.objects.filter(id=log.id).update(
            status=WhatsAppDeliveryLog.Status.PENDING,
            attempts=next_attempts,
            last_error=str(exc),
            updated_at=timezone.now(),
        )
        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))
    except InteraktPermanentError as exc:
        WhatsAppDeliveryLog.objects.filter(id=log.id).update(
            status=WhatsAppDeliveryLog.Status.FAILED,
            attempts=log.attempts + 1,
            last_error=str(exc),
            updated_at=timezone.now(),
        )
        return {"status": "failed", "error": str(exc), "idempotency_key": idempotency_key}
