from django.test import TestCase
from django.utils import timezone
from django.db import IntegrityError
from django.contrib.admin.sites import site
from datetime import timedelta

from users.models import User
from courses.models import (
    Course,
    LiveClass,
    LiveClassRecording,
    LiveClassRecordingFile,
    ZoomWebhookEvent,
)


class ZoomRecordingModelsTestCase(TestCase):
    def setUp(self):
        self.instructor = User.objects.create_user(
            username='zoom_test_teacher',
            email='zoom_teacher@example.com',
            password='testpassword123',
            is_teacher=True
        )
        self.course = Course.objects.create(
            title='Bharatanatyam Masterclass',
            description='Masterclass with live interactive Zoom sessions',
            price=1999.00,
            course_type=Course.CourseType.LIVE,
            is_published=True
        )
        self.live_class = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title='Adavus Deep Dive',
            description='Detailed footwork study',
            scheduled_start=timezone.now() + timedelta(days=1),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url='https://zoom.us/j/85712345678',
            host_url='https://zoom.us/s/85712345678?zak=dummy_token',
            status=LiveClass.ClassStatus.SCHEDULED,
            recording_url='https://example-bucket.s3.amazonaws.com/legacy_recording.mp4',
            recording_uploaded_at=timezone.now(),
            zoom_meeting_id='85712345678',
            zoom_uuid='4444AAAnEntryUUID==',
            recording_status=LiveClass.RecordingStatus.NONE
        )

    def test_live_class_recording_metadata_fields(self):
        """Verify new metadata fields and legacy field preservation on LiveClass."""
        lc = LiveClass.objects.get(id=self.live_class.id)
        self.assertEqual(lc.zoom_meeting_id, '85712345678')
        self.assertEqual(lc.zoom_uuid, '4444AAAnEntryUUID==')
        self.assertEqual(lc.recording_status, LiveClass.RecordingStatus.NONE)
        # Legacy compatibility check
        self.assertEqual(lc.recording_url, 'https://example-bucket.s3.amazonaws.com/legacy_recording.mp4')
        self.assertIsNotNone(lc.recording_uploaded_at)

    def test_multiple_recording_sessions_for_one_live_class(self):
        """Verify that a LiveClass can have multiple recording sessions if restarted."""
        session1 = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='UUID_SESSION_PART_1',
            zoom_meeting_id='85712345678',
            session_index=1,
            status=LiveClassRecording.Status.COMPLETED,
            start_time=timezone.now() - timedelta(hours=2),
            end_time=timezone.now() - timedelta(hours=1),
            duration_seconds=3600,
            total_size_bytes=1024 * 1024 * 500,
            file_count=2
        )

        session2 = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='UUID_SESSION_PART_2',
            zoom_meeting_id='85712345678',
            session_index=2,
            status=LiveClassRecording.Status.PROCESSING,
            start_time=timezone.now() - timedelta(minutes=50),
            end_time=timezone.now(),
            duration_seconds=3000,
            total_size_bytes=1024 * 1024 * 400,
            file_count=2
        )

        sessions = self.live_class.recording_sessions.all()
        self.assertEqual(sessions.count(), 2)
        self.assertEqual(list(sessions), [session1, session2])

    def test_recording_session_unique_constraint(self):
        """Verify that (live_class, zoom_uuid) is unique to prevent duplicate sessions."""
        LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='UNIQUE_SESSION_UUID',
            zoom_meeting_id='85712345678',
            session_index=1
        )
        with self.assertRaises(IntegrityError):
            LiveClassRecording.objects.create(
                live_class=self.live_class,
                zoom_uuid='UNIQUE_SESSION_UUID',
                zoom_meeting_id='85712345678',
                session_index=2
            )

    def test_live_class_recording_files(self):
        """Verify recording file creation, multi-type storage and S3 keys."""
        session = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='UUID_SESSION_MAIN',
            zoom_meeting_id='85712345678',
            session_index=1
        )

        # 1. MP4 video file
        mp4_file = LiveClassRecordingFile.objects.create(
            recording=session,
            live_class=self.live_class,
            zoom_file_id='zoom_file_mp4_guid_001',
            file_type=LiveClassRecordingFile.FileType.MP4,
            recording_type='shared_screen_with_speaker_view',
            file_extension='mp4',
            file_size_bytes=1024 * 1024 * 600,
            s3_bucket='natya-lms-media',
            s3_key=f'recordings/live-classes/{self.live_class.id}/sessions/{session.id}/video_part1.mp4',
            is_verified=True,
            verification_method='HEAD_OBJECT_SIZE',
            verified_at=timezone.now(),
            status=LiveClassRecordingFile.Status.VERIFIED
        )

        # 2. M4A audio file
        m4a_file = LiveClassRecordingFile.objects.create(
            recording=session,
            live_class=self.live_class,
            zoom_file_id='zoom_file_m4a_guid_002',
            file_type=LiveClassRecordingFile.FileType.M4A,
            recording_type='audio_only',
            file_extension='m4a',
            file_size_bytes=1024 * 1024 * 50,
            s3_bucket='natya-lms-media',
            s3_key=f'recordings/live-classes/{self.live_class.id}/sessions/{session.id}/audio_only.m4a',
            is_verified=True,
            verification_method='HEAD_OBJECT_SIZE',
            verified_at=timezone.now(),
            status=LiveClassRecordingFile.Status.VERIFIED
        )

        # 3. VTT transcript file
        vtt_file = LiveClassRecordingFile.objects.create(
            recording=session,
            live_class=self.live_class,
            zoom_file_id='zoom_file_vtt_guid_003',
            file_type=LiveClassRecordingFile.FileType.VTT,
            recording_type='audio_transcript',
            file_extension='vtt',
            file_size_bytes=1024 * 25,
            s3_bucket='natya-lms-media',
            s3_key=f'recordings/live-classes/{self.live_class.id}/sessions/{session.id}/transcript.vtt',
            is_verified=False,
            status=LiveClassRecordingFile.Status.PENDING
        )

        self.assertEqual(session.files.count(), 3)
        self.assertEqual(self.live_class.recording_files.count(), 3)
        self.assertEqual(self.live_class.recording_files.filter(is_verified=True).count(), 2)

        # Duplicate zoom_file_id must raise IntegrityError
        with self.assertRaises(IntegrityError):
            LiveClassRecordingFile.objects.create(
                recording=session,
                live_class=self.live_class,
                zoom_file_id='zoom_file_mp4_guid_001',
                file_type=LiveClassRecordingFile.FileType.MP4
            )

    def test_zoom_webhook_event_idempotency_and_tracking(self):
        """Verify ZoomWebhookEvent stores raw payload and enforces unique event_id."""
        raw_payload = {
            'event': 'recording.completed',
            'event_ts': 1730282400123,
            'payload': {
                'account_id': 'zoom_acc_123',
                'object': {
                    'id': 85712345678,
                    'uuid': '4444AAAnEntryUUID==',
                    'recording_files': [{'id': 'file_001', 'file_type': 'MP4'}]
                }
            }
        }
        event_id = 'zoom:recording.completed:4444AAAnEntryUUID==:1730282400123'

        event = ZoomWebhookEvent.objects.create(
            event_id=event_id,
            event_type='recording.completed',
            payload=raw_payload,
            live_class=self.live_class,
            status=ZoomWebhookEvent.Status.RECEIVED
        )

        self.assertEqual(event.event_id, event_id)
        self.assertEqual(event.status, ZoomWebhookEvent.Status.RECEIVED)
        self.assertEqual(event.payload['event_ts'], 1730282400123)

        # Replay / duplicate webhook with the same event_id must fail on unique constraint
        with self.assertRaises(IntegrityError):
            ZoomWebhookEvent.objects.create(
                event_id=event_id,
                event_type='recording.completed',
                payload=raw_payload,
                live_class=self.live_class,
                status=ZoomWebhookEvent.Status.RECEIVED
            )

    def test_admin_registration(self):
        """Verify that all models are properly registered in Django admin."""
        self.assertIn(LiveClass, site._registry)
        self.assertIn(LiveClassRecording, site._registry)
        self.assertIn(LiveClassRecordingFile, site._registry)
        self.assertIn(ZoomWebhookEvent, site._registry)
