import io
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError
from django.test import TestCase
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, CourseInstructor, Enrollment, LiveBatch, LiveBatchStudent
from notifications.models import DeviceToken
from users.models import AdminAuditLog, Mentorship
from .models import Conversation, ConversationParticipant, ConversationType, Message
from .tasks import send_chat_push_notification
from .validators import (
    MAX_CHAT_ATTACHMENT_SIZE_BYTES,
    validate_chat_attachment,
    validate_chat_attachment_content,
    validate_chat_attachment_size,
)

User = get_user_model()


def generate_test_image(format='PNG'):
    """Generates a genuine in-memory image for validation testing."""
    file = io.BytesIO()
    image = Image.new('RGB', (10, 10), color='red')
    image.save(file, format=format)
    file.seek(0)
    return file.getvalue()


class ChatAttachmentValidatorTests(TestCase):
    """
    Unit tests for attachment size boundary enforcement and deep content inspection.
    """

    def test_attachment_size_below_50mib(self):
        """Boundary test: 52,428,799 bytes (50 MiB - 1 byte) must be accepted."""
        uploaded = SimpleUploadedFile("sample.txt", b"Hello World", content_type="text/plain")
        uploaded.size = MAX_CHAT_ATTACHMENT_SIZE_BYTES - 1  # 52,428,799 bytes
        try:
            validate_chat_attachment_size(uploaded)
        except ValidationError:
            self.fail("validate_chat_attachment_size raised ValidationError on 52,428,799 bytes.")

    def test_attachment_size_exactly_50mib(self):
        """Boundary test: exactly 52,428,800 bytes (50 MiB) must be accepted."""
        uploaded = SimpleUploadedFile("sample.txt", b"Hello World", content_type="text/plain")
        uploaded.size = MAX_CHAT_ATTACHMENT_SIZE_BYTES  # 52,428,800 bytes
        try:
            validate_chat_attachment_size(uploaded)
        except ValidationError:
            self.fail("validate_chat_attachment_size raised ValidationError on exactly 52,428,800 bytes.")

    def test_attachment_size_above_50mib_rejected(self):
        """Boundary test: 52,428,801 bytes (50 MiB + 1 byte) must raise ValidationError."""
        uploaded = SimpleUploadedFile("sample.txt", b"Hello World", content_type="text/plain")
        uploaded.size = MAX_CHAT_ATTACHMENT_SIZE_BYTES + 1  # 52,428,801 bytes
        with self.assertRaises(ValidationError) as ctx:
            validate_chat_attachment_size(uploaded)
        self.assertIn("52,428,800 bytes", str(ctx.exception))

    def test_valid_image_accepted(self):
        """Valid JPEG / PNG images must pass Pillow verification."""
        png_data = generate_test_image(format='PNG')
        uploaded = SimpleUploadedFile("photo.png", png_data, content_type="image/png")
        validate_chat_attachment_content(uploaded)

        jpeg_data = generate_test_image(format='JPEG')
        uploaded_jpg = SimpleUploadedFile("photo.jpg", jpeg_data, content_type="image/jpeg")
        validate_chat_attachment_content(uploaded_jpg)

    def test_fake_image_with_script_rejected(self):
        """Disguised executable or shell script named as an image must fail content verification."""
        malicious_content = b"#!/bin/bash\necho 'malicious script'"
        uploaded = SimpleUploadedFile("avatar.png", malicious_content, content_type="image/png")
        with self.assertRaises(ValidationError) as ctx:
            validate_chat_attachment_content(uploaded)
        self.assertIn("Corrupt or invalid image content", str(ctx.exception))

    def test_valid_pdf_accepted(self):
        """A PDF with valid %PDF- magic bytes must pass."""
        pdf_data = b"%PDF-1.4\n%test pdf content"
        uploaded = SimpleUploadedFile("document.pdf", pdf_data, content_type="application/pdf")
        validate_chat_attachment_content(uploaded)

    def test_fake_pdf_rejected(self):
        """A file with .pdf extension lacking %PDF- magic bytes must be rejected."""
        fake_data = b"NOT_A_PDF_FILE"
        uploaded = SimpleUploadedFile("document.pdf", fake_data, content_type="application/pdf")
        with self.assertRaises(ValidationError) as ctx:
            validate_chat_attachment_content(uploaded)
        self.assertIn("missing PDF header", str(ctx.exception))

    def test_valid_docx_accepted(self):
        """A DOCX with valid Zip magic bytes PK\\x03\\x04 must pass."""
        docx_data = b"PK\x03\x04\x14\x00\x00\x00"
        uploaded = SimpleUploadedFile("notes.docx", docx_data, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        validate_chat_attachment_content(uploaded)

    def test_fake_docx_rejected(self):
        """A file with .docx extension lacking Zip magic bytes must be rejected."""
        fake_data = b"NOT_A_DOCX"
        uploaded = SimpleUploadedFile("notes.docx", fake_data, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        with self.assertRaises(ValidationError) as ctx:
            validate_chat_attachment_content(uploaded)
        self.assertIn("invalid container format", str(ctx.exception))

    def test_valid_txt_accepted(self):
        """Plain text without null bytes must pass."""
        txt_data = b"Hello, this is regular student notes."
        uploaded = SimpleUploadedFile("notes.txt", txt_data, content_type="text/plain")
        validate_chat_attachment_content(uploaded)

    def test_txt_with_null_bytes_rejected(self):
        """Binary file disguised as .txt containing null bytes must be rejected."""
        binary_txt = b"MZ\x90\x00\x03\x00\x00\x00\x04\x00"
        uploaded = SimpleUploadedFile("disguised.txt", binary_txt, content_type="text/plain")
        with self.assertRaises(ValidationError) as ctx:
            validate_chat_attachment_content(uploaded)
        self.assertIn("null bytes", str(ctx.exception))

    def test_disallowed_extension_rejected(self):
        """Disallowed extensions (.exe, .sh, .py, .bat) must raise ValidationError."""
        uploaded = SimpleUploadedFile("installer.exe", b"binary", content_type="application/octet-stream")
        with self.assertRaises(ValidationError) as ctx:
            validate_chat_attachment_content(uploaded)
        self.assertIn("extension '.exe' is not permitted", str(ctx.exception))


class ChatModelTests(TestCase):
    """
    Tests for Conversation, ConversationParticipant, and Message models.
    """

    def setUp(self):
        self.student = User.objects.create_user(username='student_user', password='password123', is_student=True)
        self.teacher = User.objects.create_user(username='teacher_user', password='password123', is_teacher=True)
        self.other_user = User.objects.create_user(username='other_user', password='password123')

    def test_create_conversation_and_participants(self):
        """Creates a direct conversation with 2 participants and verifies ordering and properties."""
        conversation = Conversation.objects.create(
            conversation_type=ConversationType.DIRECT,
            created_by=self.student,
        )
        self.assertIsNotNone(conversation.id)

        p1 = ConversationParticipant.objects.create(conversation=conversation, user=self.student)
        p2 = ConversationParticipant.objects.create(conversation=conversation, user=self.teacher)

        self.assertEqual(conversation.participants.count(), 2)
        self.assertFalse(p1.is_muted)
        self.assertIsNone(p1.last_read_at)

    def test_participant_unique_together(self):
        """A user cannot be added twice to the same conversation."""
        conversation = Conversation.objects.create(conversation_type=ConversationType.DIRECT)
        ConversationParticipant.objects.create(conversation=conversation, user=self.student)

        with self.assertRaises(IntegrityError):
            ConversationParticipant.objects.create(conversation=conversation, user=self.student)

    def test_create_message_with_attachment_metadata(self):
        """Creates a message with text and attachment metadata and validates string repr."""
        conversation = Conversation.objects.create(conversation_type=ConversationType.DIRECT)
        ConversationParticipant.objects.create(conversation=conversation, user=self.student)
        ConversationParticipant.objects.create(conversation=conversation, user=self.teacher)

        message = Message.objects.create(
            conversation=conversation,
            sender=self.student,
            content="Here is my question",
            message_type=Message.MessageType.FILE,
            attachment_name="question.pdf",
            attachment_size=1024,
            attachment_mime_type="application/pdf",
        )

        self.assertIsNotNone(message.id)
        self.assertEqual(message.sender, self.student)
        self.assertFalse(message.is_deleted)
        self.assertEqual(message.attachment_size, 1024)
        self.assertIn("Msg", str(message))


class ChatAPIPermissionAndAuthTests(APITestCase):
    """
    Tests for authentication guards and conversation initiation relationship permissions.
    """

    def setUp(self):
        self.student1 = User.objects.create_user(username='student1', password='password123', is_student=True)
        self.student2 = User.objects.create_user(username='student2', password='password123', is_student=True)
        self.mentor = User.objects.create_user(username='mentor_user', password='password123', is_mentor=True)
        self.teacher = User.objects.create_user(username='teacher_user', password='password123', is_teacher=True)
        self.admin = User.objects.create_user(username='admin_user', password='password123', is_staff=True)

        self.course = Course.objects.create(title="Bharatanatyam 101", price=1000)
        CourseInstructor.objects.create(course=self.course, user=self.teacher, role=CourseInstructor.InstructorRole.TEACHER)

    def test_unauthenticated_request_rejected(self):
        """Unauthenticated requests must receive 401."""
        response = self.client.get('/api/chat/conversations/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_student_cannot_message_random_student(self):
        """Policy check: Student-to-student direct messaging is blocked."""
        self.client.force_authenticate(user=self.student1)
        response = self.client.post('/api/chat/conversations/', {
            'conversation_type': 'DIRECT',
            'recipient_id': self.student2.id,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Direct messaging between students is not permitted", str(response.data))

    def test_student_can_message_assigned_mentor(self):
        """Student can initiate a direct conversation with their active mentor."""
        Mentorship.objects.create(student=self.student1, mentor=self.mentor, status=Mentorship.Status.ACTIVE)

        self.client.force_authenticate(user=self.student1)
        response = self.client.post('/api/chat/conversations/', {
            'conversation_type': 'DIRECT',
            'recipient_id': self.mentor.id,
            'initial_message': 'Hello Mentor!',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['conversation_type'], 'DIRECT')

    def test_student_can_message_course_teacher_when_enrolled(self):
        """Student can message a course instructor if enrolled in their course."""
        Enrollment.objects.create(user=self.student1, course=self.course)

        self.client.force_authenticate(user=self.student1)
        response = self.client.post('/api/chat/conversations/', {
            'conversation_type': 'DIRECT',
            'recipient_id': self.teacher.id,
            'initial_message': 'Question about lesson 1',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_student_cannot_message_teacher_without_enrollment(self):
        """Student cannot message a teacher if not enrolled in any shared course."""
        self.client.force_authenticate(user=self.student1)
        response = self.client.post('/api/chat/conversations/', {
            'conversation_type': 'DIRECT',
            'recipient_id': self.teacher.id,
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_can_message_support_staff(self):
        """Any student can message staff/admin for support."""
        self.client.force_authenticate(user=self.student1)
        response = self.client.post('/api/chat/conversations/', {
            'conversation_type': 'DIRECT',
            'recipient_id': self.admin.id,
            'initial_message': 'Need help with payment',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_existing_conversation_reused(self):
        """Requesting to start a direct chat with a user where one exists reuses the existing conversation."""
        Mentorship.objects.create(student=self.student1, mentor=self.mentor, status=Mentorship.Status.ACTIVE)

        self.client.force_authenticate(user=self.student1)
        res1 = self.client.post('/api/chat/conversations/', {
            'conversation_type': 'DIRECT',
            'recipient_id': self.mentor.id,
        })
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        c_id = res1.data['id']

        # Call again
        res2 = self.client.post('/api/chat/conversations/', {
            'conversation_type': 'DIRECT',
            'recipient_id': self.mentor.id,
        })
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data['id'], c_id)


class ChatMessageAPITests(APITestCase):
    """
    Tests for message sending, reverse cursor pagination, read receipts, and unread counts.
    """

    def setUp(self):
        self.student = User.objects.create_user(username='student_msg', password='password123', is_student=True)
        self.mentor = User.objects.create_user(username='mentor_msg', password='password123', is_mentor=True)
        self.unauthorized_user = User.objects.create_user(username='stranger', password='password123')

        self.conversation = Conversation.objects.create(conversation_type=ConversationType.DIRECT)
        self.p_student = ConversationParticipant.objects.create(conversation=self.conversation, user=self.student)
        self.p_mentor = ConversationParticipant.objects.create(conversation=self.conversation, user=self.mentor)

    def test_send_message_and_update_preview(self):
        """Sending a message updates conversation preview and last_message_at."""
        self.client.force_authenticate(user=self.student)
        response = self.client.post(f'/api/chat/conversations/{self.conversation.id}/messages/', {
            'content': 'Hello Mentor, I have a doubt.',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['content'], 'Hello Mentor, I have a doubt.')

        self.conversation.refresh_from_db()
        self.assertEqual(self.conversation.last_message_preview, 'Hello Mentor, I have a doubt.')
        self.assertIsNotNone(self.conversation.last_message_at)

    def test_unauthorized_user_cannot_read_or_send_messages(self):
        """User who is not in the conversation receives 403 when reading or sending."""
        self.client.force_authenticate(user=self.unauthorized_user)

        # GET messages
        res_get = self.client.get(f'/api/chat/conversations/{self.conversation.id}/messages/')
        self.assertEqual(res_get.status_code, status.HTTP_403_FORBIDDEN)

        # POST message
        res_post = self.client.post(f'/api/chat/conversations/{self.conversation.id}/messages/', {
            'content': 'Intruder message',
        })
        self.assertEqual(res_post.status_code, status.HTTP_403_FORBIDDEN)

    def test_cursor_based_pagination(self):
        """Tests reverse cursor pagination with ?before=<id>&limit=10."""
        # Create 25 messages
        for i in range(1, 26):
            Message.objects.create(
                conversation=self.conversation,
                sender=self.student,
                content=f"Message {i}"
            )

        self.client.force_authenticate(user=self.mentor)

        # First page (limit 10)
        res1 = self.client.get(f'/api/chat/conversations/{self.conversation.id}/messages/?limit=10')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res1.data['results']), 10)
        self.assertTrue(res1.data['has_more'])
        self.assertIsNotNone(res1.data['next_cursor'])

        # Next page with cursor
        cursor = res1.data['next_cursor']
        res2 = self.client.get(f'/api/chat/conversations/{self.conversation.id}/messages/?before={cursor}&limit=10')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res2.data['results']), 10)
        self.assertTrue(res2.data['has_more'])

        # Third page with cursor
        cursor2 = res2.data['next_cursor']
        res3 = self.client.get(f'/api/chat/conversations/{self.conversation.id}/messages/?before={cursor2}&limit=10')
        self.assertEqual(res3.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res3.data['results']), 5)
        self.assertFalse(res3.data['has_more'])
        self.assertIsNone(res3.data['next_cursor'])

    def test_mark_as_read_and_unread_count(self):
        """Tests mark_as_read endpoint and unread-count calculation."""
        # Student sends message
        Message.objects.create(
            conversation=self.conversation,
            sender=self.student,
            content="Unread message for mentor"
        )

        self.client.force_authenticate(user=self.mentor)

        # Check unread count
        res_count = self.client.get('/api/chat/conversations/unread-count/')
        self.assertEqual(res_count.status_code, status.HTTP_200_OK)
        self.assertEqual(res_count.data['total_unread_count'], 1)

        # Mark conversation as read
        res_read = self.client.post(f'/api/chat/conversations/{self.conversation.id}/read/')
        self.assertEqual(res_read.status_code, status.HTTP_200_OK)

        # Check unread count is now 0
        res_count_after = self.client.get('/api/chat/conversations/unread-count/')
        self.assertEqual(res_count_after.data['total_unread_count'], 0)


class ChatAttachmentAPITests(APITestCase):
    """
    Tests for attachment uploads via API, boundary enforcement, and secure IDOR-protected downloads.
    """

    def setUp(self):
        self.student = User.objects.create_user(username='attach_student', password='password123', is_student=True)
        self.mentor = User.objects.create_user(username='attach_mentor', password='password123', is_mentor=True)
        self.stranger = User.objects.create_user(username='stranger_attach', password='password123')

        self.conversation = Conversation.objects.create(conversation_type=ConversationType.DIRECT)
        ConversationParticipant.objects.create(conversation=self.conversation, user=self.student)
        ConversationParticipant.objects.create(conversation=self.conversation, user=self.mentor)

    def test_send_message_with_valid_attachment(self):
        """Sending a message with a valid PDF attachment under 50 MiB succeeds."""
        pdf_file = SimpleUploadedFile("assignment.pdf", b"%PDF-1.4\ncontent", content_type="application/pdf")
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            f'/api/chat/conversations/{self.conversation.id}/messages/',
            {'content': 'Please review this PDF', 'attachment': pdf_file},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data['has_attachment'])
        self.assertEqual(response.data['attachment_name'], 'assignment.pdf')
        self.assertIn('/attachment/', response.data['attachment_url'])

    def test_send_message_with_oversized_attachment_rejected(self):
        """Uploading a file exceeding 52,428,800 bytes via API and Serializer returns 400 Bad Request."""
        from chat.serializers import MessageSerializer
        
        # Test serializer validation with exact boundary 52,428,801 bytes
        oversized_file = SimpleUploadedFile("huge.pdf", b"%PDF-1.4\ncontent", content_type="application/pdf")
        oversized_file.size = MAX_CHAT_ATTACHMENT_SIZE_BYTES + 1
        serializer = MessageSerializer(data={'content': 'Huge', 'attachment': oversized_file})
        self.assertFalse(serializer.is_valid())
        self.assertIn('attachment', serializer.errors)
        self.assertIn('52,428,800 bytes', str(serializer.errors['attachment']))

        # Test API HTTP endpoint error handling when oversized file is detected
        self.client.force_authenticate(user=self.student)
        with patch('chat.serializers.validate_chat_attachment', side_effect=ValidationError(
            f"Chat attachment exceeds maximum allowed size of {MAX_CHAT_ATTACHMENT_SIZE_BYTES:,} bytes (50 MiB)."
        )):
            response = self.client.post(
                f'/api/chat/conversations/{self.conversation.id}/messages/',
                {'content': 'Huge file', 'attachment': SimpleUploadedFile("huge.pdf", b"%PDF-1.4\ncontent")},
                format='multipart'
            )
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("52,428,800 bytes", str(response.data))

    def test_send_message_with_disallowed_extension_rejected(self):
        """Uploading a .exe file via API returns 400 Bad Request."""
        exe_file = SimpleUploadedFile("app.exe", b"binary", content_type="application/octet-stream")
        self.client.force_authenticate(user=self.student)

        response = self.client.post(
            f'/api/chat/conversations/{self.conversation.id}/messages/',
            {'content': 'Executable', 'attachment': exe_file},
            format='multipart'
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("not permitted", str(response.data))

    def test_secure_attachment_download_authorized_vs_unauthorized(self):
        """Only participants in the conversation can download the attachment."""
        pdf_file = SimpleUploadedFile("secure.pdf", b"%PDF-1.4\ncontent", content_type="application/pdf")
        msg = Message.objects.create(
            conversation=self.conversation,
            sender=self.student,
            content="Confidential PDF",
            attachment=pdf_file,
            attachment_name="secure.pdf",
            attachment_size=len(b"%PDF-1.4\ncontent"),
        )

        # 1. Unauthorized non-participant User tries to download -> 403 Forbidden
        self.client.force_authenticate(user=self.stranger)
        res_unauth = self.client.get(f'/api/chat/messages/{msg.id}/attachment/')
        self.assertEqual(res_unauth.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Authorized participant User downloads -> 200 OK (or redirect)
        self.client.force_authenticate(user=self.mentor)
        res_auth = self.client.get(f'/api/chat/messages/{msg.id}/attachment/')
        self.assertIn(res_auth.status_code, [status.HTTP_200_OK, status.HTTP_302_FOUND])


class ChatPushNotificationTaskTests(TestCase):
    """
    Tests for Celery push notification task execution.
    """

    def setUp(self):
        self.sender = User.objects.create_user(username='sender_push', password='password123')
        self.receiver = User.objects.create_user(username='receiver_push', password='password123')
        self.muted_user = User.objects.create_user(username='muted_user', password='password123')

        self.conversation = Conversation.objects.create(conversation_type=ConversationType.DIRECT)
        ConversationParticipant.objects.create(conversation=self.conversation, user=self.sender)
        ConversationParticipant.objects.create(conversation=self.conversation, user=self.receiver)
        ConversationParticipant.objects.create(conversation=self.conversation, user=self.muted_user, is_muted=True)

        DeviceToken.objects.create(user=self.receiver, token='ExponentPushToken[receiver123]', platform='IOS')
        DeviceToken.objects.create(user=self.muted_user, token='ExponentPushToken[muted123]', platform='ANDROID')

    @patch('chat.tasks.send_expo_push_messages')
    def test_push_delivered_to_active_unmuted_receiver_only(self, mock_send):
        """Push is delivered only to unmuted receivers with active tokens, never to sender or muted users."""
        mock_send.return_value = [{"status": "ok", "id": "ticket-1"}]

        msg = Message.objects.create(
            conversation=self.conversation,
            sender=self.sender,
            content="Hello there!"
        )

        send_chat_push_notification(msg.id)

        self.assertTrue(mock_send.called)
        messages_sent = mock_send.call_args[0][0]
        self.assertEqual(len(messages_sent), 1)
        self.assertEqual(messages_sent[0]['to'], 'ExponentPushToken[receiver123]')
        self.assertEqual(messages_sent[0]['data']['conversation_id'], str(self.conversation.id))
        # Verify action_url deep link is included with conversation UUID
        self.assertEqual(messages_sent[0]['data']['action_url'], f"/chat/conversations/{self.conversation.id}")


class ChatFixesRegressionTests(APITestCase):
    """
    Regression tests covering the 4 audit findings:
    1. Push queue error resilience on Celery broker disconnect (no HTTP 500).
    2. Canonical and backward-compatible alias contracts for /read/ and /unread-count/.
    3. Authenticated attachment presigned URL retrieval (/attachment-url/ and ?redirect=false).
    4. S3 presigned URL generation and authorization controls.
    """

    def setUp(self):
        self.student = User.objects.create_user(username='reg_student', password='password123', is_student=True)
        self.mentor = User.objects.create_user(username='reg_mentor', password='password123', is_mentor=True)
        self.stranger = User.objects.create_user(username='reg_stranger', password='password123')

        Mentorship.objects.create(student=self.student, mentor=self.mentor, status=Mentorship.Status.ACTIVE)

        self.conversation = Conversation.objects.create(conversation_type=ConversationType.DIRECT)
        self.student_part = ConversationParticipant.objects.create(conversation=self.conversation, user=self.student)
        self.mentor_part = ConversationParticipant.objects.create(conversation=self.conversation, user=self.mentor)

        # By default mock push notification delay so local tests don't attempt live Redis connections
        patcher = patch('chat.views.send_chat_push_notification.delay')
        self.mock_push_delay = patcher.start()
        self.addCleanup(patcher.stop)

    def test_push_queue_failure_does_not_fail_message_creation(self):
        """
        When Celery/Redis throws an OperationalError during on_commit enqueue,
        the message creation still succeeds with HTTP 201 Created and persists the message.
        """
        self.client.force_authenticate(user=self.student)

        # Patch send_chat_push_notification.delay to simulate Redis connection failure
        with patch('chat.views.send_chat_push_notification.delay', side_effect=Exception("Redis broker offline")):
            response = self.client.post(
                f'/api/chat/conversations/{self.conversation.id}/messages/',
                {'content': 'Message sent during broker outage'},
                format='json'
            )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['content'], 'Message sent during broker outage')
        # Confirm message was actually saved in DB
        self.assertTrue(Message.objects.filter(content='Message sent during broker outage').exists())

    def test_read_status_canonical_and_alias_endpoints(self):
        """
        Both canonical /read/ and backward-compatible /mark_as_read/ succeed and update last_read_at.
        """
        self.client.force_authenticate(user=self.student)

        # 1. Canonical /read/
        res_canonical = self.client.post(f'/api/chat/conversations/{self.conversation.id}/read/')
        self.assertEqual(res_canonical.status_code, status.HTTP_200_OK)
        self.assertEqual(res_canonical.data['status'], 'ok')
        self.assertIn('last_read_at', res_canonical.data)

        # 2. Backward-compatible alias /mark_as_read/
        res_alias = self.client.post(f'/api/chat/conversations/{self.conversation.id}/mark_as_read/')
        self.assertEqual(res_alias.status_code, status.HTTP_200_OK)
        self.assertEqual(res_alias.data['status'], 'ok')

    def test_unread_count_canonical_and_alias_endpoints(self):
        """
        Both canonical /unread-count/ and backward-compatible /unread_count/ succeed
        and return both 'total_unread_count' and 'unread_count' keys.
        """
        # Create an incoming message for student
        Message.objects.create(
            conversation=self.conversation,
            sender=self.mentor,
            content="Hello student!"
        )

        self.client.force_authenticate(user=self.student)

        # 1. Canonical /unread-count/
        res_canonical = self.client.get('/api/chat/conversations/unread-count/')
        self.assertEqual(res_canonical.status_code, status.HTTP_200_OK)
        self.assertEqual(res_canonical.data['total_unread_count'], 1)
        self.assertEqual(res_canonical.data['unread_count'], 1)

        # 2. Backward-compatible alias /unread_count/
        res_alias = self.client.get('/api/chat/conversations/unread_count/')
        self.assertEqual(res_alias.status_code, status.HTTP_200_OK)
        self.assertEqual(res_alias.data['total_unread_count'], 1)
        self.assertEqual(res_alias.data['unread_count'], 1)

    def test_authenticated_attachment_presigned_url_flow(self):
        """
        The dedicated /attachment-url/ endpoint returns JSON containing download_url and metadata
        for authorized participants, and rejects unauthorized/anonymous requests.
        """
        pdf_file = SimpleUploadedFile("study.pdf", b"%PDF-1.4\ncontent", content_type="application/pdf")
        msg = Message.objects.create(
            conversation=self.conversation,
            sender=self.student,
            content="Study notes",
            attachment=pdf_file,
            attachment_name="study.pdf",
            attachment_size=len(b"%PDF-1.4\ncontent"),
            attachment_mime_type="application/pdf"
        )

        # 1. Unauthenticated request -> 401 Unauthorized
        res_anon = self.client.get(f'/api/chat/messages/{msg.id}/attachment-url/')
        self.assertEqual(res_anon.status_code, status.HTTP_401_UNAUTHORIZED)

        # 2. Unauthorized stranger -> 403 Forbidden
        self.client.force_authenticate(user=self.stranger)
        res_forbidden = self.client.get(f'/api/chat/messages/{msg.id}/attachment-url/')
        self.assertEqual(res_forbidden.status_code, status.HTTP_403_FORBIDDEN)

        # 3. Authorized participant (mentor) -> 200 OK with download_url JSON
        self.client.force_authenticate(user=self.mentor)
        res_auth = self.client.get(f'/api/chat/messages/{msg.id}/attachment-url/')
        self.assertEqual(res_auth.status_code, status.HTTP_200_OK)
        self.assertIn('download_url', res_auth.data)
        self.assertEqual(res_auth.data['expires_in'], 900)
        self.assertEqual(res_auth.data['attachment_name'], 'study.pdf')

        # 4. Attachment download with ?redirect=false query param returns same JSON
        res_no_redirect = self.client.get(f'/api/chat/messages/{msg.id}/attachment/?redirect=false')
        self.assertEqual(res_no_redirect.status_code, status.HTTP_200_OK)
        self.assertIn('download_url', res_no_redirect.data)


class AdminChatManagementTests(APITestCase):
    """
    Comprehensive tests for the Admin Chat Management API module:
    1. Admin can list all platform conversations with pagination and message counts.
    2. Admin filtering by type, course, batch, participant user ID, and date range.
    3. Admin search across conversation titles and participant names, phones, emails, and IDs.
    4. Admin can inspect conversation details and full message transcript (audited).
    5. Admin can moderate (soft-delete) and restore messages with mandatory reason (audited).
    6. Non-admins (students, mentors, teachers) cannot access admin chat endpoints (403 Forbidden).
    7. Admin can access attachments via presigned URL / download endpoint.
    8. Existing student/mentor chat permissions and endpoints continue functioning normally.
    """
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username='superadmin', email='super@example.com', password='password123'
        )
        self.staff_admin = User.objects.create_user(
            username='staffadmin', email='staff@example.com', password='password123', is_staff=True
        )
        self.student = User.objects.create_user(
            username='student1', email='student1@example.com', password='password123',
            first_name='Ananya', last_name='Sharma', phone_number='+919876543210', is_student=True
        )
        self.mentor = User.objects.create_user(
            username='mentor1', email='mentor1@example.com', password='password123',
            first_name='Guru', last_name='Prasad', phone_number='+919876543211', is_mentor=True
        )
        self.teacher = User.objects.create_user(
            username='teacher1', email='teacher1@example.com', password='password123',
            first_name='Meera', last_name='Nair', is_teacher=True
        )
        self.course = Course.objects.create(
            title="Bharatanatyam Masterclass",
            description="Test Masterclass",
            price=1999,
            course_type=Course.CourseType.LIVE
        )
        self.batch = LiveBatch.objects.create(
            course=self.course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )

        # Direct conversation between student and mentor
        self.direct_conv = Conversation.objects.create(
            conversation_type=ConversationType.DIRECT,
            title="Direct Chat",
            created_by=self.student,
        )
        ConversationParticipant.objects.create(conversation=self.direct_conv, user=self.student)
        ConversationParticipant.objects.create(conversation=self.direct_conv, user=self.mentor)

        # Batch conversation
        self.batch_conv = Conversation.objects.create(
            conversation_type=ConversationType.BATCH_GROUP,
            title="Morning Raga Discussion",
            batch=self.batch,
            course=self.course,
            created_by=self.teacher,
        )
        ConversationParticipant.objects.create(conversation=self.batch_conv, user=self.teacher, is_admin=True)
        ConversationParticipant.objects.create(conversation=self.batch_conv, user=self.student)

        # Messages
        self.msg1 = Message.objects.create(
            conversation=self.direct_conv,
            sender=self.student,
            content="Namaste Guru ji, I have completed the Adavus practice."
        )
        self.msg2 = Message.objects.create(
            conversation=self.direct_conv,
            sender=self.mentor,
            content="Well done Ananya! Remember to keep your Aramandi posture steady."
        )
        self.batch_msg = Message.objects.create(
            conversation=self.batch_conv,
            sender=self.teacher,
            content="Welcome everyone to the Morning Raga Batch!"
        )

    def test_admin_list_conversations(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/chat/admin/conversations/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 2)
        # Message count annotation
        conv_direct = next(c for c in res.data['results'] if c['id'] == str(self.direct_conv.id))
        self.assertEqual(conv_direct['message_count'], 2)
        self.assertEqual(len(conv_direct['participants']), 2)

    def test_staff_admin_can_also_list(self):
        self.client.force_authenticate(user=self.staff_admin)
        res = self.client.get('/api/chat/admin/conversations/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 2)

    def test_non_admins_forbidden_from_admin_conversation_list(self):
        for user in [self.student, self.mentor, self.teacher]:
            self.client.force_authenticate(user=user)
            res = self.client.get('/api/chat/admin/conversations/')
            self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Unauthenticated
        self.client.logout()
        res_anon = self.client.get('/api/chat/admin/conversations/')
        self.assertEqual(res_anon.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_filter_by_type(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get('/api/chat/admin/conversations/?type=DIRECT')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], str(self.direct_conv.id))

        res_batch = self.client.get('/api/chat/admin/conversations/?type=BATCH_GROUP')
        self.assertEqual(res_batch.status_code, status.HTTP_200_OK)
        self.assertEqual(res_batch.data['count'], 1)
        self.assertEqual(res_batch.data['results'][0]['id'], str(self.batch_conv.id))

    def test_admin_filter_by_course_and_batch(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(f'/api/chat/admin/conversations/?course_id={self.course.id}')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['id'], str(self.batch_conv.id))

        res_batch = self.client.get(f'/api/chat/admin/conversations/?batch_id={self.batch.id}')
        self.assertEqual(res_batch.status_code, status.HTTP_200_OK)
        self.assertEqual(res_batch.data['count'], 1)

    def test_admin_search_by_participant_phone_and_name(self):
        self.client.force_authenticate(user=self.admin)
        # Search by phone number
        res_phone = self.client.get('/api/chat/admin/conversations/?search=9876543210')
        self.assertEqual(res_phone.status_code, status.HTTP_200_OK)
        self.assertEqual(res_phone.data['count'], 2)  # student is in both conversations

        # Search by first name
        res_name = self.client.get('/api/chat/admin/conversations/?search=Ananya')
        self.assertEqual(res_name.status_code, status.HTTP_200_OK)
        self.assertEqual(res_name.data['count'], 2)

        # Search by mentor name
        res_mentor = self.client.get('/api/chat/admin/conversations/?search=Prasad')
        self.assertEqual(res_mentor.status_code, status.HTTP_200_OK)
        self.assertEqual(res_mentor.data['count'], 1)
        self.assertEqual(res_mentor.data['results'][0]['id'], str(self.direct_conv.id))

    def test_admin_inspect_conversation_detail_and_messages(self):
        self.client.force_authenticate(user=self.admin)
        res_detail = self.client.get(f'/api/chat/admin/conversations/{self.direct_conv.id}/')
        self.assertEqual(res_detail.status_code, status.HTTP_200_OK)
        self.assertEqual(res_detail.data['title'], "Direct Chat")

        # Verify audit log was recorded for inspection
        log = AdminAuditLog.objects.filter(
            action='CHAT_CONVERSATION_INSPECTED',
            target_id=str(self.direct_conv.id)
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor, self.admin)

        # Inspect messages
        res_msgs = self.client.get(f'/api/chat/admin/conversations/{self.direct_conv.id}/messages/')
        self.assertEqual(res_msgs.status_code, status.HTTP_200_OK)
        self.assertEqual(res_msgs.data['count'], 2)
        self.assertEqual(res_msgs.data['results'][0]['content'], "Namaste Guru ji, I have completed the Adavus practice.")
        self.assertEqual(res_msgs.data['results'][0]['sender']['username'], "student1")

    def test_admin_soft_delete_and_restore_message(self):
        self.client.force_authenticate(user=self.admin)
        # Moderate message with reason
        res_mod = self.client.post(
            f'/api/chat/admin/messages/{self.msg1.id}/moderate/',
            {'action': 'soft_delete', 'reason': 'Violates community guidelines: inappropriate content.'},
            format='json'
        )
        self.assertEqual(res_mod.status_code, status.HTTP_200_OK)
        self.assertTrue(res_mod.data['is_deleted'])
        self.assertEqual(res_mod.data['moderation_reason'], 'Violates community guidelines: inappropriate content.')
        self.assertEqual(res_mod.data['deleted_by']['username'], self.admin.username)

        # Message is updated in DB
        self.msg1.refresh_from_db()
        self.assertTrue(self.msg1.is_deleted)
        self.assertEqual(self.msg1.deleted_by, self.admin)
        self.assertIsNotNone(self.msg1.deleted_at)

        # Verify audit record
        mod_log = AdminAuditLog.objects.filter(
            action='CHAT_MESSAGE_MODERATED',
            target_id=str(self.msg1.id)
        ).first()
        self.assertIsNotNone(mod_log)
        self.assertEqual(mod_log.actor, self.admin)

        # Normal student query should now NOT see the soft-deleted message
        self.client.force_authenticate(user=self.student)
        res_student = self.client.get(f'/api/chat/conversations/{self.direct_conv.id}/messages/')
        self.assertEqual(res_student.status_code, status.HTTP_200_OK)
        msg_ids = [m['id'] for m in res_student.data['results']]
        self.assertNotIn(self.msg1.id, msg_ids)

        # Admin message view STILL shows it with is_deleted=True
        self.client.force_authenticate(user=self.admin)
        res_admin_msgs = self.client.get(f'/api/chat/admin/conversations/{self.direct_conv.id}/messages/')
        admin_msg1 = next(m for m in res_admin_msgs.data['results'] if m['id'] == self.msg1.id)
        self.assertTrue(admin_msg1['is_deleted'])

        # Restore message
        res_restore = self.client.post(
            f'/api/chat/admin/messages/{self.msg1.id}/moderate/',
            {'action': 'restore'},
            format='json'
        )
        self.assertEqual(res_restore.status_code, status.HTTP_200_OK)
        self.assertFalse(res_restore.data['is_deleted'])

        self.msg1.refresh_from_db()
        self.assertFalse(self.msg1.is_deleted)

        restore_log = AdminAuditLog.objects.filter(
            action='CHAT_MESSAGE_RESTORED',
            target_id=str(self.msg1.id)
        ).first()
        self.assertIsNotNone(restore_log)

    def test_moderate_missing_reason_rejected(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            f'/api/chat/admin/messages/{self.msg1.id}/moderate/',
            {'action': 'soft_delete', 'reason': ''},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_cannot_moderate_message(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.post(
            f'/api/chat/admin/messages/{self.msg1.id}/moderate/',
            {'action': 'soft_delete', 'reason': 'Unauthorized attempt'},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_migration_defaults_and_model_consistency(self):
        """
        Verify Message model defaults for moderation fields remain backward compatible.
        """
        msg = Message.objects.create(
            conversation=self.direct_conv,
            sender=self.student,
            content="Testing default fields without moderation"
        )
        self.assertEqual(msg.moderation_reason, "")
        self.assertIsNone(msg.deleted_at)
        self.assertIsNone(msg.deleted_by)
        self.assertFalse(msg.is_deleted)

    def test_non_admin_blocked_from_moderated_attachment_download_and_presigned_url(self):
        """
        Non-admin participants receive 404 Not Found when trying to download or request
        presigned URLs for attachments belonging to soft-deleted messages.
        """
        pdf_file = SimpleUploadedFile("confidential.pdf", b"%PDF-1.4\ncontent", content_type="application/pdf")
        msg = Message.objects.create(
            conversation=self.direct_conv,
            sender=self.student,
            content="Sensitive document",
            attachment=pdf_file,
            attachment_name="confidential.pdf",
            attachment_size=len(b"%PDF-1.4\ncontent"),
            attachment_mime_type="application/pdf"
        )

        # Before moderation, student can request presigned URL
        self.client.force_authenticate(user=self.student)
        res_before = self.client.get(f'/api/chat/messages/{msg.id}/attachment-url/')
        self.assertEqual(res_before.status_code, status.HTTP_200_OK)

        # Admin moderates the message
        self.client.force_authenticate(user=self.admin)
        res_mod = self.client.post(
            f'/api/chat/admin/messages/{msg.id}/moderate/',
            {'action': 'soft_delete', 'reason': 'Inappropriate document'},
            format='json'
        )
        self.assertEqual(res_mod.status_code, status.HTTP_200_OK)

        # Now student request -> 404 Not Found
        self.client.force_authenticate(user=self.student)
        res_presigned_blocked = self.client.get(f'/api/chat/messages/{msg.id}/attachment-url/')
        self.assertEqual(res_presigned_blocked.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res_presigned_blocked.data['detail'], "This attachment is no longer available.")

        res_download_blocked = self.client.get(f'/api/chat/messages/{msg.id}/attachment/?redirect=false')
        self.assertEqual(res_download_blocked.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(res_download_blocked.data['detail'], "This attachment is no longer available.")

    def test_admin_can_still_inspect_moderated_attachment(self):
        """
        Admins retain authorization to inspect and generate presigned URLs for moderated attachments.
        """
        pdf_file = SimpleUploadedFile("evidence.pdf", b"%PDF-1.4\ncontent", content_type="application/pdf")
        msg = Message.objects.create(
            conversation=self.direct_conv,
            sender=self.student,
            content="Evidence document",
            attachment=pdf_file,
            attachment_name="evidence.pdf",
            attachment_size=len(b"%PDF-1.4\ncontent"),
            attachment_mime_type="application/pdf",
            is_deleted=True,
            moderation_reason="Under investigation"
        )

        self.client.force_authenticate(user=self.admin)
        res_presigned = self.client.get(f'/api/chat/messages/{msg.id}/attachment-url/')
        self.assertEqual(res_presigned.status_code, status.HTTP_200_OK)
        self.assertIn('download_url', res_presigned.data)

        res_download = self.client.get(f'/api/chat/messages/{msg.id}/attachment/?redirect=false')
        self.assertEqual(res_download.status_code, status.HTTP_200_OK)
        self.assertIn('download_url', res_download.data)

    def test_soft_deleted_messages_excluded_from_unread_counts(self):
        """
        Soft-deleted messages are excluded from both /unread-count/ and ConversationListSerializer.
        """
        self.client.force_authenticate(user=self.student)
        initial_unread = self.client.get('/api/chat/conversations/unread-count/').data['total_unread_count']

        res_list_initial = self.client.get('/api/chat/conversations/')
        conv_initial = next(c for c in res_list_initial.data if c['id'] == str(self.direct_conv.id))
        conv_initial_unread = conv_initial['unread_count']

        # Create 2 unread messages for student
        msg_keep = Message.objects.create(
            conversation=self.direct_conv,
            sender=self.mentor,
            content="Valid unread message 1"
        )
        msg_delete = Message.objects.create(
            conversation=self.direct_conv,
            sender=self.mentor,
            content="Inappropriate unread message 2"
        )

        res_unread = self.client.get('/api/chat/conversations/unread-count/')
        self.assertEqual(res_unread.data['total_unread_count'], initial_unread + 2)

        # Admin soft-deletes msg_delete
        self.client.force_authenticate(user=self.admin)
        res_mod = self.client.post(
            f'/api/chat/admin/messages/{msg_delete.id}/moderate/',
            {'action': 'soft_delete', 'reason': 'Spam'},
            format='json'
        )
        self.assertEqual(res_mod.status_code, status.HTTP_200_OK)

        # Student's unread count drops by 1
        self.client.force_authenticate(user=self.student)
        res_unread_after = self.client.get('/api/chat/conversations/unread-count/')
        self.assertEqual(res_unread_after.data['total_unread_count'], initial_unread + 1)

        # ConversationListSerializer unread_count also drops by 1
        res_list = self.client.get('/api/chat/conversations/')
        conv_item = next(c for c in res_list.data if c['id'] == str(self.direct_conv.id))
        self.assertEqual(conv_item['unread_count'], conv_initial_unread + 1)

    @patch('chat.tasks.send_expo_push_messages')
    def test_push_task_skips_soft_deleted_messages(self, mock_send_push):
        """
        send_chat_push_notification skips delivery if message is marked is_deleted.
        """
        # Create device token for mentor
        DeviceToken.objects.create(
            user=self.mentor,
            token="ExponentPushToken[mentor_dummy_token]",
            platform="ios",
            is_active=True
        )

        deleted_msg = Message.objects.create(
            conversation=self.direct_conv,
            sender=self.student,
            content="Deleted message that shouldn't notify",
            is_deleted=True,
            moderation_reason="Flagged content"
        )

        send_chat_push_notification(deleted_msg.id)
        mock_send_push.assert_not_called()

        # Active message DOES call push delivery
        active_msg = Message.objects.create(
            conversation=self.direct_conv,
            sender=self.student,
            content="Active message"
        )
        send_chat_push_notification(active_msg.id)
        mock_send_push.assert_called_once()

    def test_unsupported_moderation_action_returns_400(self):
        """
        Only 'soft_delete' and 'restore' are supported; unknown actions return 400 Bad Request.
        """
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            f'/api/chat/admin/messages/{self.msg1.id}/moderate/',
            {'action': 'permanent_destroy', 'reason': 'Attempt permanent delete'},
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid action", res.data['action'])



