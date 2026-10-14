from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import serializers

from courses.models import Course, CourseInstructor, Enrollment, LiveBatch, LiveBatchStudent
from .models import Conversation, ConversationParticipant, ConversationType, Message
from .permissions import can_initiate_direct_conversation
from .validators import (
    MAX_CHAT_ATTACHMENT_SIZE_BYTES,
    validate_chat_attachment,
)

User = get_user_model()


class UserMinimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = [
            'id', 'username', 'first_name', 'last_name',
            'is_teacher', 'is_mentor', 'is_student',
        ]


class ConversationParticipantSerializer(serializers.ModelSerializer):
    user = UserMinimalSerializer(read_only=True)

    class Meta:
        model = ConversationParticipant
        fields = [
            'id', 'user', 'is_admin', 'is_muted',
            'last_read_at', 'joined_at',
        ]


class MessageSerializer(serializers.ModelSerializer):
    sender = UserMinimalSerializer(read_only=True)
    has_attachment = serializers.SerializerMethodField()
    attachment_url = serializers.SerializerMethodField()
    attachment = serializers.FileField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = Message
        fields = [
            'id', 'conversation', 'sender', 'content', 'message_type',
            'attachment', 'attachment_name', 'attachment_size',
            'attachment_mime_type', 'has_attachment', 'attachment_url',
            'is_deleted', 'created_at',
        ]
        read_only_fields = [
            'id', 'conversation', 'sender', 'attachment_name',
            'attachment_size', 'attachment_mime_type', 'is_deleted',
            'created_at',
        ]

    def get_has_attachment(self, obj):
        return bool(obj.attachment)

    def get_attachment_url(self, obj):
        if not obj.attachment:
            return None
        request = self.context.get('request')
        relative_url = f"/api/chat/messages/{obj.id}/attachment/"
        return request.build_absolute_uri(relative_url) if request else relative_url

    def validate_attachment(self, file):
        if not file:
            return file
        validate_chat_attachment(file)
        return file

    def validate(self, attrs):
        content = attrs.get('content', '').strip()
        attachment = attrs.get('attachment')

        if not content and not attachment:
            raise serializers.ValidationError(
                "A message must include either text content or an attachment."
            )

        return attrs

    def create(self, validated_data):
        attachment = validated_data.pop('attachment', None)
        if attachment:
            validated_data['attachment'] = attachment
            validated_data['attachment_name'] = getattr(attachment, 'name', '') or 'attachment'
            validated_data['attachment_size'] = getattr(attachment, 'size', None)
            validated_data['attachment_mime_type'] = getattr(attachment, 'content_type', '') or ''
            
            # Auto-infer message_type if not explicitly set
            if validated_data.get('message_type') == Message.MessageType.TEXT:
                ext = validated_data['attachment_name'].split('.')[-1].lower()
                if ext in ['jpg', 'jpeg', 'png', 'webp']:
                    validated_data['message_type'] = Message.MessageType.IMAGE
                else:
                    validated_data['message_type'] = Message.MessageType.FILE

        return super().create(validated_data)


class ConversationListSerializer(serializers.ModelSerializer):
    participants = ConversationParticipantSerializer(many=True, read_only=True)
    unread_count = serializers.SerializerMethodField()
    other_participant = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            'id', 'conversation_type', 'title', 'course', 'batch',
            'created_by', 'last_message_at', 'last_message_preview',
            'created_at', 'updated_at', 'participants', 'unread_count',
            'other_participant',
        ]

    def get_unread_count(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return 0

        # Find user's participant record
        participant = next(
            (p for p in obj.participants.all() if p.user_id == request.user.id),
            None
        )
        if not participant:
            return 0

        qs = obj.messages.exclude(sender=request.user)
        if participant.last_read_at:
            qs = qs.filter(created_at__gt=participant.last_read_at)
        return qs.count()

    def get_other_participant(self, obj):
        """For direct 1-on-1 chats, returns the partner participant."""
        if obj.conversation_type != ConversationType.DIRECT:
            return None
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return None

        other = next(
            (p.user for p in obj.participants.all() if p.user_id != request.user.id),
            None
        )
        return UserMinimalSerializer(other).data if other else None


class CreateConversationSerializer(serializers.Serializer):
    conversation_type = serializers.ChoiceField(
        choices=ConversationType.choices,
        default=ConversationType.DIRECT
    )
    recipient_id = serializers.IntegerField(required=False, allow_null=True)
    batch_id = serializers.IntegerField(required=False, allow_null=True)
    course_id = serializers.IntegerField(required=False, allow_null=True)
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)
    initial_message = serializers.CharField(required=False, allow_blank=True)
    initial_attachment = serializers.FileField(required=False, allow_null=True)

    def validate_initial_attachment(self, file):
        if file:
            validate_chat_attachment(file)
        return file

    def validate(self, attrs):
        user = self.context['request'].user
        c_type = attrs.get('conversation_type', ConversationType.DIRECT)

        if c_type == ConversationType.DIRECT:
            recipient_id = attrs.get('recipient_id')
            if not recipient_id:
                raise serializers.ValidationError({"recipient_id": "Recipient ID is required for direct conversations."})
            try:
                recipient = User.objects.get(pk=recipient_id)
            except User.DoesNotExist:
                raise serializers.ValidationError({"recipient_id": "Recipient user not found."})

            allowed, reason = can_initiate_direct_conversation(user, recipient)
            if not allowed:
                raise serializers.ValidationError({"detail": reason})
            attrs['recipient_user'] = recipient

        elif c_type == ConversationType.BATCH_GROUP:
            batch_id = attrs.get('batch_id')
            if not batch_id:
                raise serializers.ValidationError({"batch_id": "Batch ID is required for batch conversations."})
            try:
                batch = LiveBatch.objects.select_related('course', 'instructor').get(pk=batch_id)
            except LiveBatch.DoesNotExist:
                raise serializers.ValidationError({"batch_id": "Live batch not found."})

            # Check permissions: must be instructor, assigned student, or admin
            is_instructor = batch.instructor_id == user.id
            is_student_in_batch = LiveBatchStudent.objects.filter(batch=batch, student=user).exists()
            if not (user.is_superuser or user.is_staff or is_instructor or is_student_in_batch):
                raise serializers.ValidationError({"detail": "You are not assigned to this live batch."})
            attrs['batch_instance'] = batch

        elif c_type == ConversationType.COURSE_GROUP:
            course_id = attrs.get('course_id')
            if not course_id:
                raise serializers.ValidationError({"course_id": "Course ID is required for course conversations."})
            try:
                course = Course.objects.get(pk=course_id)
            except Course.DoesNotExist:
                raise serializers.ValidationError({"course_id": "Course not found."})

            is_instructor = CourseInstructor.objects.filter(course=course, user=user).exists()
            is_enrolled = Enrollment.objects.filter(course=course, user=user).exists()
            if not (user.is_superuser or user.is_staff or is_instructor or is_enrolled):
                raise serializers.ValidationError({"detail": "You are not enrolled or instructing this course."})
            attrs['course_instance'] = course

        return attrs
