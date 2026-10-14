import uuid
from django.conf import settings
from django.core.validators import FileExtensionValidator
from django.db import models

from .validators import (
    ALLOWED_CHAT_ATTACHMENT_EXTENSIONS,
    validate_chat_attachment,
)


class ConversationType(models.TextChoices):
    DIRECT = "DIRECT", "Direct (1-on-1)"
    BATCH_GROUP = "BATCH_GROUP", "Live Batch Group"
    COURSE_GROUP = "COURSE_GROUP", "Course Discussion Group"
    SUPPORT = "SUPPORT", "Platform Support"


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation_type = models.CharField(
        max_length=20,
        choices=ConversationType.choices,
        default=ConversationType.DIRECT,
        db_index=True,
    )
    title = models.CharField(max_length=255, blank=True)
    course = models.ForeignKey(
        'courses.Course',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chat_conversations',
    )
    batch = models.ForeignKey(
        'courses.LiveBatch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='chat_conversations',
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_conversations',
    )
    last_message_at = models.DateTimeField(null=True, blank=True, db_index=True)
    last_message_preview = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_message_at', '-created_at']
        indexes = [
            models.Index(fields=['conversation_type', 'last_message_at']),
        ]

    def __str__(self):
        return f"{self.get_conversation_type_display()} ({self.id})"


class ConversationParticipant(models.Model):
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='participants',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='conversation_participations',
    )
    is_admin = models.BooleanField(default=False)
    last_read_at = models.DateTimeField(null=True, blank=True, db_index=True)
    is_muted = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('conversation', 'user')
        ordering = ['joined_at']
        indexes = [
            models.Index(fields=['user', 'last_read_at']),
        ]

    def __str__(self):
        return f"{self.user.username} in {self.conversation_id}"


class Message(models.Model):
    class MessageType(models.TextChoices):
        TEXT = "TEXT", "Text"
        IMAGE = "IMAGE", "Image"
        FILE = "FILE", "File"
        SYSTEM = "SYSTEM", "System Notice"

    id = models.BigAutoField(primary_key=True)
    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name='messages',
        db_index=True,
    )
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_messages',
    )
    content = models.TextField(blank=True)
    message_type = models.CharField(
        max_length=10,
        choices=MessageType.choices,
        default=MessageType.TEXT,
    )
    attachment = models.FileField(
        upload_to='chat/attachments/%Y/%m/',
        blank=True,
        null=True,
        validators=[
            FileExtensionValidator(allowed_extensions=ALLOWED_CHAT_ATTACHMENT_EXTENSIONS),
            validate_chat_attachment,
        ],
    )
    attachment_name = models.CharField(max_length=255, blank=True)
    attachment_size = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Attachment size in bytes (max 52,428,800)",
    )
    attachment_mime_type = models.CharField(max_length=100, blank=True)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['conversation', 'created_at']),
        ]

    def __str__(self):
        return f"Msg {self.id} by {self.sender.username} in {self.conversation_id}"
