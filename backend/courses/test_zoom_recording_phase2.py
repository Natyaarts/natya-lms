import hmac
import hashlib
import json
import io
from unittest.mock import patch, MagicMock
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework import status

from users.models import User
from courses.models import (
    Course,
    LiveClass,
    LiveClassRecording,
    LiveClassRecordingFile,
    ZoomWebhookEvent,
    Enrollment,
)
from courses.services.recording import ZoomRecordingService
from courses.tasks import process_zoom_recording_completed


@override_settings(
    ZOOM_WEBHOOK_SECRET_TOKEN='test_zoom_secret_token_123',
    AWS_STORAGE_BUCKET_NAME='test-natya-recordings-bucket',
    AWS_S3_REGION_NAME='ap-south-1',
    AWS_ACCESS_KEY_ID='mock_key',
    AWS_SECRET_ACCESS_KEY='mock_secret',
    DEBUG=False,
)
class ZoomRecordingPhase2TestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.secret_token = 'test_zoom_secret_token_123'
        self.webhook_url = '/api/courses/zoom/webhook/'

        self.instructor = User.objects.create_user(
            username='zoom_instructor_p2',
            email='instructor_p2@example.com',
            password='testpassword123',
            is_teacher=True
        )
        self.student = User.objects.create_user(
            username='zoom_student_p2',
            email='student_p2@example.com',
            password='testpassword123',
            is_student=True
        )
        self.course = Course.objects.create(
            title='Carnatic Vocal Masterclass',
            description='Live class course',
            price=2499.00,
            course_type=Course.CourseType.LIVE,
            is_published=True
        )
        self.enrollment = Enrollment.objects.create(
            user=self.student,
            course=self.course
        )
        self.live_class = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title='Vocal Exercises Session 1',
            description='Warmups and scales',
            scheduled_start=timezone.now() - timedelta(hours=2),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url='https://zoom.us/j/98765432100',
            host_url='https://zoom.us/s/98765432100',
            status=LiveClass.ClassStatus.SCHEDULED,
            zoom_meeting_id='98765432100',
            zoom_uuid='ZoomMeetingUUID_Alpha==',
            recording_status=LiveClass.RecordingStatus.NONE
        )

    def _generate_zoom_signature(self, body_bytes, timestamp_str):
        message = f"v0:{timestamp_str}:{body_bytes.decode('utf-8')}".encode('utf-8')
        computed_hash = hmac.new(self.secret_token.encode('utf-8'), message, hashlib.sha256).hexdigest()
        return f"v0={computed_hash}"

    # --------------------------------------------------------------------------
    # 1. Webhook Signature & Timestamp Validation Tests
    # --------------------------------------------------------------------------
    def test_webhook_url_validation_challenge(self):
        """endpoint.url_validation CRC challenge succeeds without requiring signature header."""
        payload = {
            'event': 'endpoint.url_validation',
            'payload': {
                'plainToken': 'test_plain_token_xyz'
            }
        }
        response = self.client.post(self.webhook_url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['plainToken'], 'test_plain_token_xyz')
        self.assertTrue(len(response.data['encryptedToken']) > 20)

    def test_webhook_valid_signature_accepted(self):
        """Webhook with valid signature and current timestamp is accepted."""
        payload = {
            'event': 'meeting.participant_joined',
            'event_ts': int(timezone.now().timestamp() * 1000),
            'payload': {
                'object': {
                    'id': 98765432100,
                    'uuid': 'ZoomMeetingUUID_Alpha=='
                }
            }
        }
        raw_body = json.dumps(payload).encode('utf-8')
        timestamp = str(int(timezone.now().timestamp()))
        signature = self._generate_zoom_signature(raw_body, timestamp)

        response = self.client.post(
            self.webhook_url,
            raw_body,
            content_type='application/json',
            HTTP_X_ZM_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_ZM_SIGNATURE=signature
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_webhook_invalid_signature_rejected(self):
        """Webhook with forged/invalid signature returns 401 Unauthorized."""
        payload = {'event': 'recording.completed', 'event_ts': 1234567890}
        raw_body = json.dumps(payload).encode('utf-8')
        timestamp = str(int(timezone.now().timestamp()))

        response = self.client.post(
            self.webhook_url,
            raw_body,
            content_type='application/json',
            HTTP_X_ZM_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_ZM_SIGNATURE='v0=forged_invalid_signature_hex'
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_webhook_timestamp_skew_rejected(self):
        """Webhook older than 300 seconds is rejected for replay prevention."""
        payload = {'event': 'recording.completed', 'event_ts': 1234567890}
        raw_body = json.dumps(payload).encode('utf-8')
        old_timestamp = str(int(timezone.now().timestamp()) - 350)  # 350 seconds ago
        signature = self._generate_zoom_signature(raw_body, old_timestamp)

        response = self.client.post(
            self.webhook_url,
            raw_body,
            content_type='application/json',
            HTTP_X_ZM_REQUEST_TIMESTAMP=old_timestamp,
            HTTP_X_ZM_SIGNATURE=signature
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    # --------------------------------------------------------------------------
    # 2. Duplicate Event & Webhook Idempotency Tests
    # --------------------------------------------------------------------------
    @patch('courses.tasks.process_zoom_recording_completed.delay')
    def test_duplicate_webhook_deliveries_ignored(self, mock_task_delay):
        """Duplicate webhook deliveries with the same event_id return 200 and do not re-dispatch task."""
        event_ts = 1730282400555
        payload = {
            'event': 'recording.completed',
            'event_ts': event_ts,
            'payload': {
                'object': {
                    'id': 98765432100,
                    'uuid': 'ZoomMeetingUUID_Alpha==',
                    'recording_files': [{'id': 'file_part_1', 'file_type': 'MP4'}]
                }
            }
        }
        raw_body = json.dumps(payload).encode('utf-8')
        timestamp = str(int(timezone.now().timestamp()))
        signature = self._generate_zoom_signature(raw_body, timestamp)

        # First delivery
        resp1 = self.client.post(
            self.webhook_url,
            raw_body,
            content_type='application/json',
            HTTP_X_ZM_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_ZM_SIGNATURE=signature
        )
        self.assertEqual(resp1.status_code, status.HTTP_200_OK)
        self.assertEqual(resp1.data['status'], 'queued')
        self.assertEqual(mock_task_delay.call_count, 1)

        # Second delivery (identical retry from Zoom)
        resp2 = self.client.post(
            self.webhook_url,
            raw_body,
            content_type='application/json',
            HTTP_X_ZM_REQUEST_TIMESTAMP=timestamp,
            HTTP_X_ZM_SIGNATURE=signature
        )
        self.assertEqual(resp2.status_code, status.HTTP_200_OK)
        self.assertEqual(resp2.data['status'], 'duplicate_ignored')
        # Task must NOT be called a second time
        self.assertEqual(mock_task_delay.call_count, 1)

        # Database must only have 1 event row
        self.assertEqual(ZoomWebhookEvent.objects.count(), 1)

    # --------------------------------------------------------------------------
    # 3. Multipart Upload & Bounded-Memory Streaming Tests
    # --------------------------------------------------------------------------
    @patch('courses.services.recording.ZoomRecordingService.get_s3_client')
    @patch('requests.get')
    def test_bounded_memory_multipart_upload_success(self, mock_requests_get, mock_get_s3):
        """Tests that large files are chunked into 10MB parts and multipart upload completes."""
        mock_s3 = MagicMock()
        mock_get_s3.return_value = mock_s3
        mock_s3.create_multipart_upload.return_value = {'UploadId': 'test_upload_id_777'}
        mock_s3.upload_part.side_effect = [
            {'ETag': '"etag_part_1"'},
            {'ETag': '"etag_part_2"'}
        ]
        mock_s3.complete_multipart_upload.return_value = {}

        # Mock streaming response with 2 chunks of 10MB
        chunk_10mb = b'A' * (10 * 1024 * 1024)
        chunk_2mb = b'B' * (2 * 1024 * 1024)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'video/mp4'}
        mock_response.iter_content.return_value = [chunk_10mb, chunk_2mb]
        mock_response.__enter__.return_value = mock_response
        mock_requests_get.return_value = mock_response

        success, total_bytes = ZoomRecordingService.stream_zoom_recording_to_s3(
            download_url='https://zoom.us/rec/download/sample.mp4',
            download_token='test_token',
            s3_bucket='test-natya-recordings-bucket',
            s3_key='recordings/sample.mp4',
            expected_file_size=12 * 1024 * 1024
        )

        self.assertTrue(success)
        self.assertEqual(total_bytes, 12 * 1024 * 1024)
        mock_s3.create_multipart_upload.assert_called_once()
        self.assertEqual(mock_s3.upload_part.call_count, 2)
        mock_s3.complete_multipart_upload.assert_called_once()

    # --------------------------------------------------------------------------
    # 4. Individual Part Retry Tests
    # --------------------------------------------------------------------------
    @patch('courses.services.recording.ZoomRecordingService.get_s3_client')
    @patch('requests.get')
    def test_multipart_upload_part_retry(self, mock_requests_get, mock_get_s3):
        """Tests that a transient error during upload_part is retried and succeeds."""
        mock_s3 = MagicMock()
        mock_get_s3.return_value = mock_s3
        mock_s3.create_multipart_upload.return_value = {'UploadId': 'test_upload_retry_id'}
        # Fail once, then succeed
        mock_s3.upload_part.side_effect = [
            Exception("Transient AWS S3 500 error"),
            {'ETag': '"etag_part_retry_ok"'}
        ]
        mock_s3.complete_multipart_upload.return_value = {}

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'video/mp4'}
        mock_response.iter_content.return_value = [b'C' * (1024 * 1024)]
        mock_response.__enter__.return_value = mock_response
        mock_requests_get.return_value = mock_response

        success, total_bytes = ZoomRecordingService.stream_zoom_recording_to_s3(
            download_url='https://zoom.us/rec/download/retry.mp4',
            download_token='test_token',
            s3_bucket='test-natya-recordings-bucket',
            s3_key='recordings/retry.mp4'
        )

        self.assertTrue(success)
        self.assertEqual(mock_s3.upload_part.call_count, 2)
        mock_s3.complete_multipart_upload.assert_called_once()

    # --------------------------------------------------------------------------
    # 5. Interrupted Transfer & Multipart Cleanup Tests
    # --------------------------------------------------------------------------
    @patch('courses.services.recording.ZoomRecordingService.get_s3_client')
    @patch('requests.get')
    def test_interrupted_transfer_aborts_multipart_upload(self, mock_requests_get, mock_get_s3):
        """When an upload permanently fails, abort_multipart_upload is called to clean up S3 parts."""
        mock_s3 = MagicMock()
        mock_get_s3.return_value = mock_s3
        mock_s3.create_multipart_upload.return_value = {'UploadId': 'abandoned_upload_id'}
        mock_s3.upload_part.side_effect = Exception("Permanent connection reset")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {'content-type': 'video/mp4'}
        mock_response.iter_content.return_value = [b'X' * (1024 * 1024)]
        mock_response.__enter__.return_value = mock_response
        mock_requests_get.return_value = mock_response

        with self.assertRaises(Exception):
            ZoomRecordingService.stream_zoom_recording_to_s3(
                download_url='https://zoom.us/rec/download/broken.mp4',
                download_token='test_token',
                s3_bucket='test-natya-recordings-bucket',
                s3_key='recordings/broken.mp4'
            )

        mock_s3.abort_multipart_upload.assert_called_once_with(
            Bucket='test-natya-recordings-bucket',
            Key='recordings/broken.mp4',
            UploadId='abandoned_upload_id'
        )

    # --------------------------------------------------------------------------
    # 6. S3 Verification Tests
    # --------------------------------------------------------------------------
    @patch('courses.services.recording.ZoomRecordingService.get_s3_client')
    def test_s3_verification_size_match_and_mismatch(self, mock_get_s3):
        """Verify S3 head_object confirms matching file size, rejects mismatch."""
        mock_s3 = MagicMock()
        mock_get_s3.return_value = mock_s3
        mock_s3.head_object.return_value = {'ContentLength': 5000000, 'ETag': '"sample_etag"'}

        # Match
        ok, meta = ZoomRecordingService.verify_s3_upload(
            'test-bucket', 'recordings/test.mp4', expected_file_size=5000000
        )
        self.assertTrue(ok)

        # Mismatch
        bad, _ = ZoomRecordingService.verify_s3_upload(
            'test-bucket', 'recordings/test.mp4', expected_file_size=9999999
        )
        self.assertFalse(bad)

    # --------------------------------------------------------------------------
    # 7. Celery Task End-to-End Processing & Recovery Tests
    # --------------------------------------------------------------------------
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload')
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3')
    def test_process_zoom_recording_task_e2e(self, mock_stream, mock_verify):
        """Full task execution: creates session, files, updates LiveClass and marks event PROCESSED."""
        mock_stream.return_value = (True, 1024 * 1024 * 50)
        mock_verify.return_value = (True, {'ContentLength': 1024 * 1024 * 50})

        webhook_event = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:ZoomMeetingUUID_Alpha==:1730282400999',
            event_type='recording.completed',
            payload={
                'event': 'recording.completed',
                'download_token': 'test_dl_token',
                'object': {
                    'id': 98765432100,
                    'uuid': 'ZoomMeetingUUID_Alpha==',
                    'total_size': 1024 * 1024 * 50,
                    'duration': 60,
                    'recording_files': [
                        {
                            'id': 'file_guid_mp4_main',
                            'file_type': 'MP4',
                            'recording_type': 'shared_screen_with_speaker_view',
                            'file_size': 1024 * 1024 * 50,
                            'download_url': 'https://zoom.us/rec/dl/mp4_main',
                        }
                    ]
                }
            },
            status=ZoomWebhookEvent.Status.RECEIVED
        )

        # Execute task directly
        process_zoom_recording_completed.apply(args=(webhook_event.id,)).get()

        webhook_event.refresh_from_db()
        self.assertEqual(webhook_event.status, ZoomWebhookEvent.Status.PROCESSED)

        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.recording_status, LiveClass.RecordingStatus.COMPLETED)
        self.assertEqual(self.live_class.status, LiveClass.ClassStatus.COMPLETED)
        self.assertTrue(self.live_class.recording_url.startswith('https://test-natya-recordings-bucket.s3.'))

        # Check session and file
        session = self.live_class.recording_sessions.first()
        self.assertIsNotNone(session)
        self.assertEqual(session.status, LiveClassRecording.Status.COMPLETED)

        rec_file = session.files.first()
        self.assertIsNotNone(rec_file)
        self.assertTrue(rec_file.is_verified)
        self.assertEqual(rec_file.status, LiveClassRecordingFile.Status.VERIFIED)

    # --------------------------------------------------------------------------
    # 8. Per-File Idempotency Tests
    # --------------------------------------------------------------------------
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload')
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3')
    def test_per_file_idempotency_skips_verified_files(self, mock_stream, mock_verify):
        """When a file is already verified in a prior run, it is not re-streamed to S3."""
        mock_stream.return_value = (True, 1024 * 1024 * 10)
        mock_verify.return_value = (True, {'ContentLength': 1024 * 1024 * 10})

        session = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='ZoomMeetingUUID_Alpha==',
            zoom_meeting_id='98765432100',
            session_index=1,
            status=LiveClassRecording.Status.TRANSFERRING
        )

        # Pre-create verified file 1
        LiveClassRecordingFile.objects.create(
            recording=session,
            live_class=self.live_class,
            zoom_file_id='already_verified_file_001',
            file_type=LiveClassRecordingFile.FileType.MP4,
            file_size_bytes=1024 * 1024 * 10,
            s3_bucket='test-natya-recordings-bucket',
            s3_key='recordings/part1.mp4',
            is_verified=True,
            status=LiveClassRecordingFile.Status.VERIFIED
        )

        webhook_event = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:ZoomMeetingUUID_Alpha==:1730282400888',
            event_type='recording.completed',
            payload={
                'event': 'recording.completed',
                'object': {
                    'id': 98765432100,
                    'uuid': 'ZoomMeetingUUID_Alpha==',
                    'recording_files': [
                        {
                            'id': 'already_verified_file_001',
                            'file_type': 'MP4',
                            'recording_type': 'shared_screen_with_speaker_view',
                            'file_size': 1024 * 1024 * 10,
                            'download_url': 'https://zoom.us/rec/dl/part1',
                        },
                        {
                            'id': 'new_unverified_file_002',
                            'file_type': 'M4A',
                            'recording_type': 'audio_only',
                            'file_size': 1024 * 1024 * 5,
                            'download_url': 'https://zoom.us/rec/dl/part2',
                        }
                    ]
                }
            },
            status=ZoomWebhookEvent.Status.RECEIVED
        )

        process_zoom_recording_completed.apply(args=(webhook_event.id,)).get()

        # stream_zoom_recording_to_s3 should only be called once (for file 2), NOT for file 1!
        self.assertEqual(mock_stream.call_count, 1)
        self.assertEqual(session.files.count(), 2)
        self.assertEqual(session.files.filter(is_verified=True).count(), 2)
