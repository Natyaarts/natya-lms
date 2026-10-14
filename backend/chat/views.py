import logging
import boto3
from botocore.config import Config
from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from users.models import AdminAuditLog

from .models import Conversation, ConversationParticipant, ConversationType, Message
from .permissions import IsConversationParticipant
from .serializers import (
    AdminConversationListSerializer,
    AdminMessageSerializer,
    ConversationListSerializer,
    CreateConversationSerializer,
    MessageSerializer,
)
from .tasks import send_chat_push_notification
from .throttles import ChatConversationRateThrottle, ChatMessageRateThrottle

logger = logging.getLogger(__name__)


def safe_enqueue_chat_push(message_id: int):
    """
    Safely enqueues the chat push notification task on database commit.
    Catches any broker/Celery connectivity exceptions so an already-saved
    message never fails with an HTTP 500 error due to push queue issues.
    Logs only message_id, never message content, tokens, or attachment URLs.
    """
    def _enqueue():
        try:
            send_chat_push_notification.delay(message_id)
        except Exception:
            logger.exception(f"Chat push delivery: failed to enqueue Celery task for message {message_id}")

    try:
        transaction.on_commit(_enqueue)
    except Exception:
        logger.exception(f"Chat push delivery: failed to register on_commit callback for message {message_id}")


def get_attachment_presigned_url(message, request=None):
    """
    Generates a 15-minute S3 presigned URL for private attachment access,
    or falls back to an absolute local download URL in development/testing.
    Preserves participant authorization and private S3 storage without
    transmitting bearer tokens in URL query parameters.
    """
    if getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') and getattr(settings, 'AWS_ACCESS_KEY_ID', ''):
        s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=getattr(settings, 'AWS_S3_REGION_NAME', 'ap-south-1') or 'ap-south-1',
            config=Config(signature_version='s3v4')
        )
        return s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                'Key': message.attachment.name,
                'ResponseContentDisposition': f'inline; filename="{message.attachment_name}"',
            },
            ExpiresIn=900  # 15 minutes
        )

    # Local fallback for dev/test
    if request:
        return request.build_absolute_uri(
            reverse('message-attachment-download', kwargs={'pk': message.pk})
        )
    return f"/api/chat/messages/{message.pk}/attachment/"



class ConversationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for listing, creating, and managing conversations.
    - list: Returns conversations for the authenticated user with unread counts.
    - retrieve: Returns single conversation if user is a participant.
    - create: Initiates a direct or group conversation with relationship validation.
    - read: Marks all messages in the conversation as read for the user.
    - unread-count: Returns total unread messages across all conversations.
    """
    permission_classes = [permissions.IsAuthenticated, IsConversationParticipant]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_throttles(self):
        if self.action == 'create':
            return [ChatConversationRateThrottle()]
        return super().get_throttles()

    def get_queryset(self):
        user = self.request.user
        if not user.is_authenticated:
            return Conversation.objects.none()

        qs = Conversation.objects.prefetch_related('participants__user', 'messages')
        if (user.is_superuser or user.is_staff) and self.request.query_params.get('all') == 'true':
            return qs

        return qs.filter(participants__user=user).distinct()

    def get_serializer_class(self):
        if self.action == 'create':
            return CreateConversationSerializer
        return ConversationListSerializer

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        serializer = ConversationListSerializer(queryset, many=True, context={'request': request})
        return Response(serializer.data)

    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = ConversationListSerializer(instance, context={'request': request})
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = CreateConversationSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        user = request.user
        c_type = data.get('conversation_type', ConversationType.DIRECT)
        title = data.get('title', '')
        initial_message = data.get('initial_message', '').strip()
        initial_attachment = data.get('initial_attachment')

        with transaction.atomic():
            if c_type == ConversationType.DIRECT:
                recipient = data['recipient_user']
                # Check for existing 1-on-1 conversation
                existing = Conversation.objects.filter(
                    conversation_type=ConversationType.DIRECT,
                    participants__user=user
                ).filter(
                    participants__user=recipient
                ).first()

                if existing:
                    conversation = existing
                    created = False
                else:
                    conversation = Conversation.objects.create(
                        conversation_type=ConversationType.DIRECT,
                        created_by=user,
                    )
                    ConversationParticipant.objects.create(conversation=conversation, user=user)
                    ConversationParticipant.objects.create(conversation=conversation, user=recipient)
                    created = True

            elif c_type == ConversationType.BATCH_GROUP:
                batch = data['batch_instance']
                title = title or f"Batch: {batch.course.title}"
                conversation, created = Conversation.objects.get_or_create(
                    batch=batch,
                    conversation_type=ConversationType.BATCH_GROUP,
                    defaults={'created_by': user, 'title': title, 'course': batch.course}
                )
                if created:
                    # Add instructor
                    if batch.instructor:
                        ConversationParticipant.objects.get_or_create(conversation=conversation, user=batch.instructor, is_admin=True)
                    # Add creator if not instructor
                    ConversationParticipant.objects.get_or_create(conversation=conversation, user=user)
                    # Add all assigned students
                    for student_rel in batch.students.select_related('student').all():
                        ConversationParticipant.objects.get_or_create(conversation=conversation, user=student_rel.student)

            elif c_type == ConversationType.COURSE_GROUP:
                course = data['course_instance']
                title = title or f"Course: {course.title}"
                conversation, created = Conversation.objects.get_or_create(
                    course=course,
                    conversation_type=ConversationType.COURSE_GROUP,
                    defaults={'created_by': user, 'title': title}
                )
                if created:
                    ConversationParticipant.objects.get_or_create(conversation=conversation, user=user, is_admin=True)
                    # Add enrolled students
                    for enroll in course.enrollments.select_related('user').all():
                        ConversationParticipant.objects.get_or_create(conversation=conversation, user=enroll.user)
                    # Add instructors
                    for instructor in course.instructors.select_related('user').all():
                        ConversationParticipant.objects.get_or_create(conversation=conversation, user=instructor.user, is_admin=True)

            elif c_type == ConversationType.SUPPORT:
                title = title or f"Support: {user.username}"
                conversation = Conversation.objects.create(
                    conversation_type=ConversationType.SUPPORT,
                    created_by=user,
                    title=title,
                )
                ConversationParticipant.objects.create(conversation=conversation, user=user)
                created = True

            # Create initial message if supplied
            if initial_message or initial_attachment:
                msg_serializer = MessageSerializer(
                    data={'content': initial_message, 'attachment': initial_attachment},
                    context={'request': request}
                )
                msg_serializer.is_valid(raise_exception=True)
                msg = msg_serializer.save(sender=user, conversation=conversation)

                conversation.last_message_at = msg.created_at
                conversation.last_message_preview = msg.content[:200] or f"[Attachment: {msg.attachment_name}]"
                conversation.save(update_fields=['last_message_at', 'last_message_preview'])

                ConversationParticipant.objects.filter(
                    conversation=conversation, user=user
                ).update(last_read_at=msg.created_at)

                safe_enqueue_chat_push(msg.id)

        status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        return Response(
            ConversationListSerializer(conversation, context={'request': request}).data,
            status=status_code
        )

    @action(detail=True, methods=['post'], url_path='read')
    def mark_as_read(self, request, pk=None):
        conversation = self.get_object()
        participant = ConversationParticipant.objects.filter(
            conversation=conversation, user=request.user
        ).first()

        if not participant:
            return Response(
                {"detail": "You are not a participant in this conversation."},
                status=status.HTTP_403_FORBIDDEN
            )

        now = timezone.now()
        participant.last_read_at = now
        participant.save(update_fields=['last_read_at'])

        return Response({"status": "ok", "last_read_at": now})

    @action(detail=True, methods=['post'], url_path='mark_as_read')
    def mark_as_read_alias(self, request, pk=None):
        """Backward-compatible alias for clients calling mark_as_read/."""
        return self.mark_as_read(request, pk)

    @action(detail=False, methods=['get'], url_path='unread-count')
    def unread_count(self, request):
        user = request.user
        participations = ConversationParticipant.objects.filter(user=user)

        total_unread = 0
        for p in participations:
            qs = Message.objects.filter(conversation_id=p.conversation_id, is_deleted=False).exclude(sender=user)
            if p.last_read_at:
                qs = qs.filter(created_at__gt=p.last_read_at)
            total_unread += qs.count()

        return Response({
            "total_unread_count": total_unread,
            "unread_count": total_unread,
        })

    @action(detail=False, methods=['get'], url_path='unread_count')
    def unread_count_alias(self, request):
        """Backward-compatible alias for clients requesting unread_count/."""
        return self.unread_count(request)


class MessageListCreateView(APIView):
    """
    API for listing messages (reverse cursor-based pagination) and sending messages.
    - GET: /api/chat/conversations/<uuid:conversation_id>/messages/?before=<id>&limit=30
    - POST: /api/chat/conversations/<uuid:conversation_id>/messages/
    """
    permission_classes = [permissions.IsAuthenticated]

    def get_throttles(self):
        if self.request.method == 'POST':
            return [ChatMessageRateThrottle()]
        return []

    def get_conversation(self, conversation_id, user):
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        if not (user.is_superuser or user.is_staff or conversation.participants.filter(user=user).exists()):
            return None
        return conversation

    def get(self, request, conversation_id):
        conversation = self.get_conversation(conversation_id, request.user)
        if not conversation:
            return Response(
                {"detail": "You do not have permission to view messages in this conversation."},
                status=status.HTTP_403_FORBIDDEN
            )

        limit = min(int(request.query_params.get('limit', 30)), 100)
        before = request.query_params.get('before')

        qs = Message.objects.filter(
            conversation=conversation,
            is_deleted=False
        ).select_related('sender').order_by('-id')

        if before:
            try:
                qs = qs.filter(id__lt=int(before))
            except ValueError:
                pass

        messages = list(qs[:limit + 1])
        has_more = len(messages) > limit
        results = messages[:limit]
        next_cursor = results[-1].id if results and has_more else None

        # Return in ascending chronological order for chat UI rendering
        ordered_results = list(reversed(results))
        serialized = MessageSerializer(ordered_results, many=True, context={'request': request}).data

        return Response({
            "results": serialized,
            "has_more": has_more,
            "next_cursor": next_cursor,
        })

    def post(self, request, conversation_id):
        conversation = self.get_conversation(conversation_id, request.user)
        if not conversation:
            return Response(
                {"detail": "You do not have permission to send messages in this conversation."},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = MessageSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        with transaction.atomic():
            message = serializer.save(sender=request.user, conversation=conversation)

            preview = message.content[:200] if message.content else f"[Attachment: {message.attachment_name}]"
            conversation.last_message_at = message.created_at
            conversation.last_message_preview = preview
            conversation.save(update_fields=['last_message_at', 'last_message_preview'])

            # Automatically mark message as read by the sender
            ConversationParticipant.objects.filter(
                conversation=conversation, user=request.user
            ).update(last_read_at=message.created_at)

            # Schedule push notification after commit with error resilience
            safe_enqueue_chat_push(message.id)

        return Response(MessageSerializer(message, context={'request': request}).data, status=status.HTTP_201_CREATED)


class AttachmentPresignedUrlView(APIView):
    """
    Authenticated endpoint returning a short-lived S3 presigned URL for secure mobile downloads.
    Preserves participant authorization and private S3 storage without transmitting bearer tokens
    in URL query parameters.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        message = get_object_or_404(Message.objects.select_related('conversation'), pk=pk)

        if not message.attachment:
            return Response({"detail": "This message has no attachment."}, status=status.HTTP_404_NOT_FOUND)

        is_admin = bool(request.user.is_superuser or request.user.is_staff)
        if message.is_deleted and not is_admin:
            return Response({"detail": "This attachment is no longer available."}, status=status.HTTP_404_NOT_FOUND)

        conversation = message.conversation
        is_authorized = is_admin or conversation.participants.filter(user=request.user).exists()

        if not is_authorized:
            return Response(
                {"detail": "You do not have permission to access this attachment."},
                status=status.HTTP_403_FORBIDDEN
            )

        download_url = get_attachment_presigned_url(message, request=request)
        return Response({
            "download_url": download_url,
            "expires_in": 900,
            "attachment_name": message.attachment_name,
            "attachment_size": message.attachment_size,
            "attachment_mime_type": message.attachment_mime_type,
        })


class AttachmentDownloadView(APIView):
    """
    Secure download endpoint for chat attachments.
    Verifies that the requesting user is an active participant in the conversation
    (or an administrator) before granting access.
    Supports ?redirect=false to return presigned URL JSON, or redirects to S3 / streams locally.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        message = get_object_or_404(Message.objects.select_related('conversation'), pk=pk)

        if not message.attachment:
            return Response({"detail": "This message has no attachment."}, status=status.HTTP_404_NOT_FOUND)

        is_admin = bool(request.user.is_superuser or request.user.is_staff)
        if message.is_deleted and not is_admin:
            return Response({"detail": "This attachment is no longer available."}, status=status.HTTP_404_NOT_FOUND)

        conversation = message.conversation
        is_authorized = is_admin or conversation.participants.filter(user=request.user).exists()

        if not is_authorized:
            return Response(
                {"detail": "You do not have permission to access this attachment."},
                status=status.HTTP_403_FORBIDDEN
            )

        # Support ?redirect=false query param for API clients requesting JSON presigned URL
        if request.query_params.get('redirect', 'true').lower() in ('false', '0'):
            download_url = get_attachment_presigned_url(message, request=request)
            return Response({
                "download_url": download_url,
                "expires_in": 900,
                "attachment_name": message.attachment_name,
                "attachment_size": message.attachment_size,
                "attachment_mime_type": message.attachment_mime_type,
            })

        # S3 presigned redirect in production
        if getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') and getattr(settings, 'AWS_ACCESS_KEY_ID', ''):
            presigned_url = get_attachment_presigned_url(message, request=request)
            return HttpResponseRedirect(presigned_url)

        # Local fallback (development and automated testing)
        try:
            response = FileResponse(message.attachment.open('rb'))
            response['Content-Disposition'] = f'inline; filename="{message.attachment_name}"'
            return response
        except FileNotFoundError:
            return Response({"detail": "Attachment file not found on disk."}, status=status.HTTP_404_NOT_FOUND)


class IsAdminRole(permissions.BasePermission):
    """
    Permission class granting access strictly to Superusers and Staff members.
    Regular students, teachers, and mentors receive 403 Forbidden.
    """
    def has_permission(self, request, view):
        return bool(
            request.user and
            request.user.is_authenticated and
            (request.user.is_superuser or request.user.is_staff)
        )


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class AdminConversationListView(APIView):
    """
    Admin-only endpoint listing all conversations across the platform.
    Supports server-side filtering by type, course, batch, participant user ID,
    date ranges, and searching across titles and user names/emails/phones.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request):
        qs = Conversation.objects.all().select_related(
            'course', 'batch', 'created_by'
        ).prefetch_related(
            'participants__user'
        ).annotate(
            message_count=Count('messages')
        ).order_by('-last_message_at', '-created_at')

        # Filter by conversation type
        conv_type = request.query_params.get('type') or request.query_params.get('conversation_type')
        if conv_type:
            conv_type = conv_type.upper().strip()
            if conv_type in ConversationType.values:
                qs = qs.filter(conversation_type=conv_type)

        # Filter by course
        course_id = request.query_params.get('course_id')
        if course_id:
            qs = qs.filter(course_id=course_id)

        # Filter by batch
        batch_id = request.query_params.get('batch_id')
        if batch_id:
            qs = qs.filter(batch_id=batch_id)

        # Filter by participant (user ID)
        participant_id = request.query_params.get('participant_id') or request.query_params.get('user_id')
        if participant_id:
            qs = qs.filter(participants__user_id=participant_id)

        # Filter by date range
        date_from = request.query_params.get('date_from')
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)

        date_to = request.query_params.get('date_to')
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        # Full text search across title, participant name/email/phone/ID
        search = request.query_params.get('search', '').strip()
        if search:
            search_filter = (
                Q(title__icontains=search) |
                Q(participants__user__username__icontains=search) |
                Q(participants__user__first_name__icontains=search) |
                Q(participants__user__last_name__icontains=search) |
                Q(participants__user__email__icontains=search) |
                Q(participants__user__phone_number__icontains=search)
            )
            if search.isdigit():
                search_filter |= Q(participants__user__id=int(search))
            qs = qs.filter(search_filter)

        qs = qs.distinct()

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(qs, request)
        serializer = AdminConversationListSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)


class AdminConversationDetailView(APIView):
    """
    Admin-only endpoint returning complete conversation metadata.
    Audits administrative inspection.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request, conversation_id):
        conversation = get_object_or_404(
            Conversation.objects.select_related('course', 'batch', 'created_by')
            .prefetch_related('participants__user')
            .annotate(message_count=Count('messages')),
            id=conversation_id
        )

        # Audit administrative inspection
        AdminAuditLog.record(
            actor=request.user,
            action='CHAT_CONVERSATION_INSPECTED',
            target_type='Conversation',
            target_id=str(conversation.id),
            description=f"Admin {request.user.username} inspected conversation {conversation.id} ({conversation.get_conversation_type_display()})",
            metadata={
                'conversation_type': conversation.conversation_type,
                'participant_count': conversation.participants.count(),
            }
        )

        serializer = AdminConversationListSerializer(conversation, context={'request': request})
        return Response(serializer.data)


class AdminConversationMessagesView(APIView):
    """
    Admin-only endpoint returning paginated messages for a conversation in chronological order.
    Includes both active and moderated messages so administrators can inspect full chat history.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def get(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, id=conversation_id)

        qs = Message.objects.filter(conversation=conversation).select_related(
            'sender', 'deleted_by'
        ).order_by('created_at')

        paginator = StandardResultsSetPagination()
        paginator.page_size = 50
        page = paginator.paginate_queryset(qs, request)
        serializer = AdminMessageSerializer(page, many=True, context={'request': request})
        return paginator.get_paginated_response(serializer.data)


class AdminMessageModerateView(APIView):
    """
    Admin-only endpoint to soft-delete or restore a message with an audited reason.
    Never permanently deletes chat history.
    """
    permission_classes = [permissions.IsAuthenticated, IsAdminRole]

    def post(self, request, pk):
        message = get_object_or_404(
            Message.objects.select_related('conversation', 'sender', 'deleted_by'),
            pk=pk
        )

        action_name = str(request.data.get('action', 'soft_delete')).lower().strip()
        reason = str(request.data.get('reason', '')).strip()

        if action_name not in ('soft_delete', 'restore'):
            return Response(
                {"action": "Invalid action. Supported actions are 'soft_delete' and 'restore'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        if action_name == 'restore':
            message.is_deleted = False
            message.deleted_by = None
            message.deleted_at = None
            message.moderation_reason = ""
            message.save(update_fields=['is_deleted', 'deleted_by', 'deleted_at', 'moderation_reason'])

            AdminAuditLog.record(
                actor=request.user,
                action='CHAT_MESSAGE_RESTORED',
                target_type='Message',
                target_id=str(message.id),
                description=f"Admin {request.user.username} restored message {message.id} in conversation {message.conversation_id}",
                metadata={
                    'conversation_id': str(message.conversation_id),
                    'sender_id': message.sender_id,
                }
            )
        else:
            if not reason:
                return Response(
                    {"reason": "A moderation reason is required when soft-deleting a message."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            message.is_deleted = True
            message.deleted_by = request.user
            message.deleted_at = timezone.now()
            message.moderation_reason = reason
            message.save(update_fields=['is_deleted', 'deleted_by', 'deleted_at', 'moderation_reason'])

            AdminAuditLog.record(
                actor=request.user,
                action='CHAT_MESSAGE_MODERATED',
                target_type='Message',
                target_id=str(message.id),
                description=f"Admin {request.user.username} moderated message {message.id} in conversation {message.conversation_id}: {reason}",
                metadata={
                    'conversation_id': str(message.conversation_id),
                    'sender_id': message.sender_id,
                    'reason': reason,
                }
            )

        serializer = AdminMessageSerializer(message, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class ChatRecipientsView(APIView):
    """
    Returns eligible 1-on-1 direct chat contacts for the authenticated user,
    strictly adhering to the authorization rules in can_initiate_direct_conversation():
    - Students see all active assigned mentors and instructors of their enrolled courses.
    - Mentors see their active assigned students.
    - Teachers see students enrolled in courses they instruct.
    - Dual-role (teacher + mentor) users see the merged set of students without duplicates.
    - Staff / Superusers see platform users.
    - Inactive mentorships and inactive accounts are strictly excluded.
    - Returns minimal user display data required by the recipient UI.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.contrib.auth import get_user_model
        from courses.models import Course, CourseInstructor
        from users.models import Mentorship
        from .permissions import can_initiate_direct_conversation

        user = request.user
        User = get_user_model()
        recipients_map = {}

        def _format_recipient(u, role_override=None):
            if role_override:
                role = role_override
            elif u.is_superuser or u.is_staff:
                role = 'STAFF'
            elif getattr(u, 'is_mentor', False):
                role = 'MENTOR'
            elif getattr(u, 'is_teacher', False):
                role = 'TEACHER'
            else:
                role = 'STUDENT'

            full_name = f"{u.first_name or ''} {u.last_name or ''}".strip()
            display_name = full_name or u.username

            profile_image_url = None
            if hasattr(u, 'mentor_profile') and u.mentor_profile and u.mentor_profile.profile_image:
                try:
                    profile_image_url = u.mentor_profile.profile_image.url
                except Exception:
                    profile_image_url = None
            elif hasattr(u, 'teacher_profile') and u.teacher_profile and u.teacher_profile.profile_image:
                try:
                    profile_image_url = u.teacher_profile.profile_image.url
                except Exception:
                    profile_image_url = None

            return {
                "id": u.id,
                "username": u.username,
                "first_name": u.first_name or "",
                "last_name": u.last_name or "",
                "name": display_name,
                "email": u.email or "",
                "role": role,
                "profile_image_url": profile_image_url,
            }

        # 1. Staff / SuperUser: platform directory
        if user.is_superuser or user.is_staff:
            all_users = User.objects.filter(is_active=True).exclude(pk=user.pk).order_by('first_name', 'last_name', 'username')
            return Response([_format_recipient(u) for u in all_users])

        is_student = getattr(user, 'is_student', False)
        is_mentor = getattr(user, 'is_mentor', False)
        is_teacher = getattr(user, 'is_teacher', False)

        # 2. Mentor -> Assigned Active Students
        if is_mentor:
            active_mentorships = Mentorship.objects.filter(
                mentor=user,
                status=Mentorship.Status.ACTIVE,
                student__is_active=True
            ).select_related('student')
            for m in active_mentorships:
                s = m.student
                if s and s.pk != user.pk and s.pk not in recipients_map:
                    recipients_map[s.pk] = _format_recipient(s, role_override='STUDENT')

        # 3. Teacher -> Enrolled Course Students
        if is_teacher:
            teacher_courses = Course.objects.filter(
                Q(instructors__user=user, instructors__role='TEACHER') |
                Q(enrollments__user=user)
            ).distinct()
            course_students = User.objects.filter(
                is_active=True,
                enrollments__course__in=teacher_courses
            ).exclude(pk=user.pk).distinct().order_by('first_name', 'last_name', 'username')
            for s in course_students:
                if s.pk not in recipients_map:
                    recipients_map[s.pk] = _format_recipient(s, role_override='STUDENT')

        # 4. Student -> Active Mentors and Enrolled Course Instructors
        if is_student:
            # Active mentors (supports multiple active mentors)
            mentorships = Mentorship.objects.filter(
                student=user,
                status=Mentorship.Status.ACTIVE,
                mentor__is_active=True
            ).select_related('mentor')
            for m in mentorships:
                mentor = m.mentor
                if mentor and mentor.pk != user.pk and mentor.pk not in recipients_map:
                    recipients_map[mentor.pk] = _format_recipient(mentor, role_override='MENTOR')

            # Instructors of enrolled courses
            enrolled_instructors = CourseInstructor.objects.filter(
                course__enrollments__user=user,
                user__is_active=True
            ).select_related('user').exclude(user=user)
            for ci in enrolled_instructors:
                inst = ci.user
                if inst and inst.pk not in recipients_map:
                    inst_role = 'MENTOR' if getattr(inst, 'is_mentor', False) else 'TEACHER'
                    recipients_map[inst.pk] = _format_recipient(inst, role_override=inst_role)

            # Legacy teachers for courses where teacher self-enrolled
            legacy_teacher_courses = Course.objects.filter(enrollments__user=user).exclude(instructors__isnull=False)
            legacy_teachers = User.objects.filter(
                is_teacher=True,
                is_active=True,
                enrollments__course__in=legacy_teacher_courses
            ).exclude(pk=user.pk).distinct()
            for lt in legacy_teachers:
                if lt.pk not in recipients_map:
                    recipients_map[lt.pk] = _format_recipient(lt, role_override='TEACHER')

        # Enforce can_initiate_direct_conversation validation on all candidates
        final_list = []
        candidate_users = User.objects.filter(pk__in=recipients_map.keys())
        user_by_id = {u.id: u for u in candidate_users}
        for r_id, r_data in recipients_map.items():
            candidate = user_by_id.get(r_id)
            if candidate and can_initiate_direct_conversation(user, candidate)[0]:
                final_list.append(r_data)

        # Sort: Mentors first, then alphabetically by display name
        final_list.sort(key=lambda x: (x['role'] != 'MENTOR', x['name'].lower()))
        return Response(final_list, status=status.HTTP_200_OK)


