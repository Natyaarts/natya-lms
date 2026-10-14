import logging
from celery import shared_task

from notifications.models import DeviceToken
from notifications.push import ExpoPushTransientError, send_expo_push_messages
from .models import Message

logger = logging.getLogger(__name__)

_PERMANENTLY_INVALID_ERROR_CODES = {"DeviceNotRegistered"}


@shared_task(bind=True, max_retries=3, default_retry_delay=15)
def send_chat_push_notification(self, message_id):
    """
    Asynchronously delivers an Expo push notification to all conversation participants
    (excluding the sender) who have active DeviceTokens and have not muted the conversation.
    """
    try:
        message = Message.objects.select_related('conversation', 'sender').get(pk=message_id)
    except Message.DoesNotExist:
        logger.warning(f"Chat push delivery: Message {message_id} no longer exists; skipping.")
        return

    conversation = message.conversation

    # Find recipient users in this conversation who have not muted it
    recipient_user_ids = list(
        conversation.participants
        .exclude(user=message.sender)
        .filter(is_muted=False)
        .values_list('user_id', flat=True)
    )

    if not recipient_user_ids:
        return

    device_tokens = list(
        DeviceToken.objects.filter(user_id__in=recipient_user_ids, is_active=True)
    )
    if not device_tokens:
        return

    # Notification text construction
    sender_name = message.sender.get_full_name() or message.sender.username
    if conversation.conversation_type != 'DIRECT' and conversation.title:
        title = f"{sender_name} ({conversation.title})"
    else:
        title = sender_name

    if message.content:
        body = message.content[:120]
    elif message.attachment_name:
        body = f"📎 Sent attachment: {message.attachment_name}"
    else:
        body = "Sent a new message"

    action_url = f"/chat/conversations/{conversation.id}"
    messages = [
        {
            "to": device_token.token,
            "title": title,
            "body": body,
            "data": {
                "type": "CHAT_MESSAGE",
                "conversation_id": str(conversation.id),
                "message_id": message.id,
                "action_url": action_url,
            },
        }
        for device_token in device_tokens
    ]

    try:
        tickets = send_expo_push_messages(messages)
    except ExpoPushTransientError as exc:
        logger.warning(f"Chat push delivery: transient failure for message {message_id}, retrying: {exc}")
        raise self.retry(exc=exc)
    except Exception:
        logger.exception(f"Chat push delivery: unexpected error sending message {message_id}")
        return

    invalid_token_ids = []
    for device_token, ticket in zip(device_tokens, tickets):
        if ticket.get("status") == "error":
            error_code = (ticket.get("details") or {}).get("error")
            if error_code in _PERMANENTLY_INVALID_ERROR_CODES:
                invalid_token_ids.append(device_token.id)

    if invalid_token_ids:
        DeviceToken.objects.filter(id__in=invalid_token_ids).update(is_active=False)
