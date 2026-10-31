import os
import json
import time
import hmac
import hashlib
from datetime import timedelta
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.conf import settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from core.celery import app as celery_app
from courses.models import (
    Course,
    LiveClass,
    LiveClassRecording,
    LiveClassRecordingFile,
    ZoomWebhookEvent,
)
from courses.services.recording import ZoomRecordingService
from courses.tasks import process_zoom_recording_completed

User = get_user_model()


@override_settings(
    AWS_STORAGE_BUCKET_NAME='test-natya-recordings-bucket',
    AWS_S3_REGION_NAME='ap-south-1',
)
class ZoomRemediationTests(TestCase):
    """
    Automated regression and remediation tests covering Fixes 1, 2, 3, 5, and 6.
    """

    def setUp(self):
        self.client = APIClient()
        self.instructor = User.objects.create_user(
            username='remed_instructor',
            email='remed_instructor@example.com',
            password='Password123!',
            is_teacher=True
        )
        self.course = Course.objects.create(
            title='Remediation Course',
            description='Live class course',
            price=2499.00,
            course_type=Course.CourseType.LIVE,
            is_published=True
        )
        self.live_class = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title='Remediation Class',
            description='Live class description',
            scheduled_start=timezone.now() + timedelta(days=1),
            duration_minutes=60,
            meeting_url='https://zoom.us/j/9988776655',
            zoom_meeting_id='9988776655',
            zoom_uuid='ZoomUUID_Remed_Main=='
        )

    # =========================================================================
    # Fix 1: Redis Visibility Timeout Assertion
    # =========================================================================
    def test_fix1_redis_visibility_timeout_exceeds_task_time_limit(self):
        """
        Verify that Celery's effective Redis broker visibility timeout is 25,200s (7h),
        safely exceeding the task's 21,600s (6h) hard time limit.
        """
        broker_transport_opts = celery_app.conf.broker_transport_options or {}
        visibility_timeout = broker_transport_opts.get('visibility_timeout')
        task_time_limit = getattr(process_zoom_recording_completed, 'time_limit', 0)

        self.assertIsNotNone(visibility_timeout, "visibility_timeout must be configured in broker_transport_options")
        self.assertEqual(visibility_timeout, 25200, "Visibility timeout must be exactly 25,200 seconds (7 hours)")
        self.assertEqual(task_time_limit, 21600, "Task time_limit must be 21,600 seconds (6 hours)")
        self.assertGreater(
            visibility_timeout,
            task_time_limit,
            "Broker visibility timeout must strictly exceed task hard time limit"
        )

    # =========================================================================
    # Fix 2: Webhook Dispatch Recovery & Deduplication
    # =========================================================================
    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN='test_remed_token_xyz')
    def test_fix2_first_delay_raises_broker_error_then_redelivery_succeeds(self):
        """
        1. When .delay() raises an exception, view logs error, saves event as RECEIVED, and returns 503.
        2. When Zoom redelivers the same event, existing event is reused and task is successfully dispatched with 200.
        """
        secret = 'test_remed_token_xyz'
        ts = str(int(time.time()))
        payload_data = {
            "event": "recording.completed",
            "event_ts": 1730999001,
            "payload": {
                "object": {
                    "id": "9988776655",
                    "uuid": "ZoomUUID_Remed_Main==",
                    "recording_files": []
                }
            }
        }
        raw_body = json.dumps(payload_data)
        message = f"v0:{ts}:{raw_body}".encode('utf-8')
        sig = 'v0=' + hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()

        # Step 1: Simulate broker outage on first delivery
        with patch('courses.tasks.process_zoom_recording_completed.delay', side_effect=Exception("Redis connection refused")):
            resp1 = self.client.post(
                '/api/courses/zoom/webhook/',
                data=raw_body,
                content_type='application/json',
                HTTP_X_ZM_SIGNATURE=sig,
                HTTP_X_ZM_REQUEST_TIMESTAMP=ts
            )
            self.assertEqual(resp1.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

        # Confirm event was persisted with status RECEIVED
        event_obj = ZoomWebhookEvent.objects.get(event_id="zoom:recording.completed:ZoomUUID_Remed_Main==:1730999001")
        self.assertEqual(event_obj.status, ZoomWebhookEvent.Status.RECEIVED)
        self.assertIn("Task dispatch failed", event_obj.error_message)

        # Step 2: Zoom redelivers same event (broker is restored)
        with patch('courses.tasks.process_zoom_recording_completed.delay') as mock_delay:
            resp2 = self.client.post(
                '/api/courses/zoom/webhook/',
                data=raw_body,
                content_type='application/json',
                HTTP_X_ZM_SIGNATURE=sig,
                HTTP_X_ZM_REQUEST_TIMESTAMP=ts
            )
            self.assertEqual(resp2.status_code, status.HTTP_200_OK)
            self.assertEqual(resp2.data.get('status'), 'queued')
            self.assertTrue(resp2.data.get('is_redelivery'))
            mock_delay.assert_called_once_with(event_obj.id)

        # Confirm no duplicate record was created
        self.assertEqual(
            ZoomWebhookEvent.objects.filter(event_id="zoom:recording.completed:ZoomUUID_Remed_Main==:1730999001").count(),
            1
        )

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN='test_remed_token_xyz')
    def test_fix2_already_processed_duplicate_is_not_requeued(self):
        """
        If an event is already marked PROCESSED, duplicate redelivery returns 200 duplicate_ignored without calling .delay().
        """
        event_id = "zoom:recording.completed:ZoomUUID_Remed_Main==:1730999002"
        ZoomWebhookEvent.objects.create(
            event_id=event_id,
            event_type="recording.completed",
            payload={},
            status=ZoomWebhookEvent.Status.PROCESSED
        )

        secret = 'test_remed_token_xyz'
        ts = str(int(time.time()))
        payload_data = {
            "event": "recording.completed",
            "event_ts": 1730999002,
            "payload": {
                "object": {
                    "id": "9988776655",
                    "uuid": "ZoomUUID_Remed_Main==",
                }
            }
        }
        raw_body = json.dumps(payload_data)
        message = f"v0:{ts}:{raw_body}".encode('utf-8')
        sig = 'v0=' + hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()

        with patch('courses.tasks.process_zoom_recording_completed.delay') as mock_delay:
            resp = self.client.post(
                '/api/courses/zoom/webhook/',
                data=raw_body,
                content_type='application/json',
                HTTP_X_ZM_SIGNATURE=sig,
                HTTP_X_ZM_REQUEST_TIMESTAMP=ts
            )
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(resp.data.get('status'), 'duplicate_ignored')
            mock_delay.assert_not_called()

    # =========================================================================
    # Fix 3: Expired Zoom Download Token & 401 Refresh
    # =========================================================================
    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.get_s3_client')
    @patch('courses.services.zoom.ZoomService.get_access_token')
    @patch('requests.get')
    def test_fix3_initial_token_401_fresh_token_succeeds(self, mock_requests_get, mock_get_token, mock_get_s3, mock_s3_active):
        """
        When the initial download_token returns HTTP 401, stream_zoom_recording_to_s3 acquires
        a fresh token via ZoomService.get_access_token() and retries once successfully.
        """
        mock_get_token.return_value = 'fresh_oauth_token_123'

        # Response 1: 401 Unauthorized
        res_401 = MagicMock()
        res_401.status_code = 401

        # Response 2: 200 OK with video stream
        res_200 = MagicMock()
        res_200.status_code = 200
        res_200.headers = {'content-type': 'video/mp4'}
        res_200.iter_content.return_value = [b"A" * 1024 * 1024]

        mock_requests_get.side_effect = [res_401, res_200]

        mock_s3 = MagicMock()
        mock_s3.create_multipart_upload.return_value = {'UploadId': 'test_upload_id'}
        mock_s3.upload_part.return_value = {'ETag': '"etag-1"'}
        mock_get_s3.return_value = mock_s3

        success, total_bytes = ZoomRecordingService.stream_zoom_recording_to_s3(
            download_url="https://api.zoom.us/rec/download/sample.mp4",
            download_token="expired_jwt_token_abc",
            s3_bucket="my-bucket",
            s3_key="recordings/sample.mp4"
        )

        self.assertTrue(success)
        self.assertEqual(total_bytes, 1024 * 1024)
        self.assertEqual(mock_requests_get.call_count, 2)
        mock_get_token.assert_called_once()
        res_401.close.assert_called_once()

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.zoom.ZoomService.get_access_token')
    @patch('requests.get')
    def test_fix3_both_attempts_receive_401_returns_failure(self, mock_requests_get, mock_get_token, mock_s3_active):
        """
        When both initial and refreshed tokens receive 401, fails cleanly without infinite loops.
        """
        mock_get_token.return_value = 'fresh_oauth_token_123'
        res_401_a = MagicMock(status_code=401)
        res_401_b = MagicMock(status_code=401)
        mock_requests_get.side_effect = [res_401_a, res_401_b]

        success, total_bytes = ZoomRecordingService.stream_zoom_recording_to_s3(
            download_url="https://api.zoom.us/rec/download/sample.mp4",
            download_token="expired_token",
            s3_bucket="my-bucket",
            s3_key="recordings/sample.mp4"
        )

        self.assertFalse(success)
        self.assertEqual(total_bytes, 0)
        self.assertEqual(mock_requests_get.call_count, 2)
        res_401_a.close.assert_called_once()
        res_401_b.close.assert_called_once()

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.zoom.ZoomService.get_access_token')
    @patch('requests.get')
    def test_fix3_fresh_token_acquisition_fails(self, mock_requests_get, mock_get_token, mock_s3_active):
        """
        When token refresh raises an exception (e.g. network/credentials failure), fails safely.
        """
        mock_get_token.side_effect = Exception("OAuth service unavailable")
        res_401 = MagicMock(status_code=401)
        mock_requests_get.return_value = res_401

        success, total_bytes = ZoomRecordingService.stream_zoom_recording_to_s3(
            download_url="https://api.zoom.us/rec/download/sample.mp4",
            download_token="expired_token",
            s3_bucket="my-bucket",
            s3_key="recordings/sample.mp4"
        )

        self.assertFalse(success)
        self.assertEqual(total_bytes, 0)
        self.assertEqual(mock_requests_get.call_count, 1)

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.zoom.ZoomService.get_access_token')
    @patch('requests.get')
    def test_fix3_non_401_error_does_not_trigger_token_refresh(self, mock_requests_get, mock_get_token, mock_s3_active):
        """
        Errors like 404 or 500 do not trigger unnecessary token refresh calls.
        """
        res_500 = MagicMock(status_code=500)
        mock_requests_get.return_value = res_500

        success, total_bytes = ZoomRecordingService.stream_zoom_recording_to_s3(
            download_url="https://api.zoom.us/rec/download/sample.mp4",
            download_token="valid_token",
            s3_bucket="my-bucket",
            s3_key="recordings/sample.mp4"
        )

        self.assertFalse(success)
        self.assertEqual(total_bytes, 0)
        mock_get_token.assert_not_called()

    # =========================================================================
    # Fix 5: Multiple-Session Recording URL Overwrite Protection
    # =========================================================================
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', return_value=(True, 5000000))
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 5000000}))
    def test_fix5_long_first_session_followed_by_short_second_session(self, mock_verify, mock_stream):
        """
        A 2-hour first session (7200s) sets recording_url. A 5-minute restart session (300s)
        preserves the 2-hour recording_url.
        """
        # Session 1: 120 minutes (7200s)
        event1 = ZoomWebhookEvent.objects.create(
            event_id="zoom:recording.completed:uuid_sess_1:1",
            event_type="recording.completed",
            live_class=self.live_class,
            payload={
                "object": {
                    "uuid": "uuid_sess_1",
                    "id": "9988776655",
                    "duration": 120,
                    "recording_files": [{
                        "id": "sess1_file_mp4",
                        "file_type": "MP4",
                        "recording_type": "shared_screen_with_speaker_view",
                        "download_url": "https://api.zoom.us/rec/download/file1.mp4",
                        "file_size": 5000000
                    }]
                }
            }
        )
        process_zoom_recording_completed(event1.id)
        self.live_class.refresh_from_db()
        sess1_url = self.live_class.recording_url
        self.assertIn("sess1_fi", sess1_url)

        # Session 2: 5 minutes (300s)
        event2 = ZoomWebhookEvent.objects.create(
            event_id="zoom:recording.completed:uuid_sess_2:2",
            event_type="recording.completed",
            live_class=self.live_class,
            payload={
                "object": {
                    "uuid": "uuid_sess_2",
                    "id": "9988776655",
                    "duration": 5,
                    "recording_files": [{
                        "id": "sess2_file_mp4",
                        "file_type": "MP4",
                        "recording_type": "shared_screen_with_speaker_view",
                        "download_url": "https://api.zoom.us/rec/download/file2.mp4",
                        "file_size": 5000000
                    }]
                }
            }
        )
        process_zoom_recording_completed(event2.id)
        self.live_class.refresh_from_db()

        # Confirm sess1_url was preserved and NOT overwritten
        self.assertEqual(self.live_class.recording_url, sess1_url)
        # But session 2 records exist intact
        self.assertEqual(self.live_class.recording_sessions.count(), 2)
        self.assertEqual(self.live_class.recording_files.count(), 2)

    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', return_value=(True, 5000000))
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 5000000}))
    def test_fix5_longer_later_session_replaces_shorter_first_session(self, mock_verify, mock_stream):
        """
        A 5-minute accidental first session (300s) followed by a 2-hour lecture session (7200s).
        The longer later session successfully replaces recording_url.
        """
        # Session 1: 5 minutes
        event1 = ZoomWebhookEvent.objects.create(
            event_id="zoom:recording.completed:uuid_sess_short:1",
            event_type="recording.completed",
            live_class=self.live_class,
            payload={
                "object": {
                    "uuid": "uuid_sess_short",
                    "id": "9988776655",
                    "duration": 5,
                    "recording_files": [{
                        "id": "short_part_mp4",
                        "file_type": "MP4",
                        "recording_type": "shared_screen_with_speaker_view",
                        "download_url": "https://api.zoom.us/rec/download/fileshort.mp4",
                        "file_size": 5000000
                    }]
                }
            }
        )
        process_zoom_recording_completed(event1.id)
        self.live_class.refresh_from_db()
        self.assertIn("short_pa", self.live_class.recording_url)

        # Session 2: 120 minutes (7200s)
        event2 = ZoomWebhookEvent.objects.create(
            event_id="zoom:recording.completed:uuid_sess_long:2",
            event_type="recording.completed",
            live_class=self.live_class,
            payload={
                "object": {
                    "uuid": "uuid_sess_long",
                    "id": "9988776655",
                    "duration": 120,
                    "recording_files": [{
                        "id": "long_part_mp4",
                        "file_type": "MP4",
                        "recording_type": "shared_screen_with_speaker_view",
                        "download_url": "https://api.zoom.us/rec/download/filelong.mp4",
                        "file_size": 5000000
                    }]
                }
            }
        )
        process_zoom_recording_completed(event2.id)
        self.live_class.refresh_from_db()

        # Confirm recording_url was updated to the longer session
        self.assertIn("long_par", self.live_class.recording_url)

    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', return_value=(True, 5000000))
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 5000000}))
    def test_fix5_missing_duration_preserves_existing_primary_recording(self, mock_verify, mock_stream):
        """
        When duration is missing/0 on second session, existing primary recording is preserved.
        """
        event1 = ZoomWebhookEvent.objects.create(
            event_id="zoom:recording.completed:uuid_sess_primary:1",
            event_type="recording.completed",
            live_class=self.live_class,
            payload={
                "object": {
                    "uuid": "uuid_sess_primary",
                    "id": "9988776655",
                    "duration": 60,
                    "recording_files": [{
                        "id": "file_sess_primary_mp4",
                        "file_type": "MP4",
                        "recording_type": "shared_screen_with_speaker_view",
                        "download_url": "https://api.zoom.us/rec/download/fileprim.mp4",
                        "file_size": 5000000
                    }]
                }
            }
        )
        process_zoom_recording_completed(event1.id)
        self.live_class.refresh_from_db()
        orig_url = self.live_class.recording_url

        # Session 2: 0 duration
        event2 = ZoomWebhookEvent.objects.create(
            event_id="zoom:recording.completed:uuid_sess_nodur:2",
            event_type="recording.completed",
            live_class=self.live_class,
            payload={
                "object": {
                    "uuid": "uuid_sess_nodur",
                    "id": "9988776655",
                    "duration": 0,
                    "recording_files": [{
                        "id": "file_sess_nodur_mp4",
                        "file_type": "MP4",
                        "recording_type": "shared_screen_with_speaker_view",
                        "download_url": "https://api.zoom.us/rec/download/filenodur.mp4",
                        "file_size": 5000000
                    }]
                }
            }
        )
        process_zoom_recording_completed(event2.id)
        self.live_class.refresh_from_db()

        self.assertEqual(self.live_class.recording_url, orig_url)

    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', return_value=(True, 5000000))
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 5000000}))
    def test_fix5_tasks_completing_out_of_order(self, mock_verify, mock_stream):
        """
        Short session (300s) completes first. Long session (7200s) completes second.
        Long session deterministically wins and becomes recording_url.
        """
        event_short = ZoomWebhookEvent.objects.create(
            event_id="zoom:recording.completed:uuid_ooo_short:1",
            event_type="recording.completed",
            live_class=self.live_class,
            payload={
                "object": {
                    "uuid": "uuid_ooo_short",
                    "id": "9988776655",
                    "duration": 5,
                    "recording_files": [{
                        "id": "short_oo_mp4",
                        "file_type": "MP4",
                        "recording_type": "shared_screen_with_speaker_view",
                        "download_url": "https://api.zoom.us/rec/download/fileshort.mp4",
                        "file_size": 5000000
                    }]
                }
            }
        )
        event_long = ZoomWebhookEvent.objects.create(
            event_id="zoom:recording.completed:uuid_ooo_long:2",
            event_type="recording.completed",
            live_class=self.live_class,
            payload={
                "object": {
                    "uuid": "uuid_ooo_long",
                    "id": "9988776655",
                    "duration": 120,
                    "recording_files": [{
                        "id": "long_ooo_mp4",
                        "file_type": "MP4",
                        "recording_type": "shared_screen_with_speaker_view",
                        "download_url": "https://api.zoom.us/rec/download/filelong.mp4",
                        "file_size": 5000000
                    }]
                }
            }
        )

        # Out-of-order execution: short finishes first, then long finishes
        process_zoom_recording_completed(event_short.id)
        self.live_class.refresh_from_db()
        self.assertIn("short_oo", self.live_class.recording_url)

        process_zoom_recording_completed(event_long.id)
        self.live_class.refresh_from_db()
        self.assertIn("long_ooo", self.live_class.recording_url)

    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', return_value=(True, 5000000))
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 5000000}))
    def test_fix5_duplicate_task_execution_is_idempotent(self, mock_verify, mock_stream):
        """
        Running process_zoom_recording_completed twice for the same event preserves the recording_url.
        """
        event = ZoomWebhookEvent.objects.create(
            event_id="zoom:recording.completed:uuid_dup_sess:1",
            event_type="recording.completed",
            live_class=self.live_class,
            payload={
                "object": {
                    "uuid": "uuid_dup_sess",
                    "id": "9988776655",
                    "duration": 60,
                    "recording_files": [{
                        "id": "file_dup_sess_mp4",
                        "file_type": "MP4",
                        "recording_type": "shared_screen_with_speaker_view",
                        "download_url": "https://api.zoom.us/rec/download/filedup.mp4",
                        "file_size": 5000000
                    }]
                }
            }
        )
        process_zoom_recording_completed(event.id)
        self.live_class.refresh_from_db()
        url1 = self.live_class.recording_url

        # Run task again (simulating duplicate delivery)
        event.status = ZoomWebhookEvent.Status.RECEIVED
        event.save()
        process_zoom_recording_completed(event.id)
        self.live_class.refresh_from_db()
        url2 = self.live_class.recording_url

        self.assertEqual(url1, url2)

    # =========================================================================
    # Fix 6: Webhook Secret Configuration
    # =========================================================================
    @override_settings(DEBUG=False, ZOOM_WEBHOOK_SECRET_TOKEN='')
    @patch('courses.services.zoom.ZoomService.get_credentials', return_value=('acc_id', 'cli_id', 'oauth_client_secret'))
    def test_fix6_missing_secret_rejects_and_does_not_fallback_to_client_secret(self, mock_creds):
        """
        When ZOOM_WEBHOOK_SECRET_TOKEN is empty and DEBUG=False, verification fails and DOES NOT
        treat client_secret as the webhook secret token.
        """
        is_valid = ZoomRecordingService.verify_webhook_signature(
            raw_body_bytes=b'{"test": 1}',
            signature_header='v0=fake_signature',
            timestamp_header=str(int(time.time()))
        )
        self.assertFalse(is_valid)

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN='')
    def test_fix6_challenge_raises_improperly_configured_when_secret_missing(self):
        """
        validate_zoom_webhook_challenge raises ImproperlyConfigured if secret token is missing.
        """
        from django.core.exceptions import ImproperlyConfigured
        with self.assertRaises(ImproperlyConfigured):
            ZoomRecordingService.validate_zoom_webhook_challenge('sample_plain_token')


# =============================================================================
# Phase 2.1 Concurrency & Recovery Hardening Tests
# =============================================================================
import concurrent.futures
from django.test import TransactionTestCase
from django.db import connections
from courses.tasks import recover_stale_zoom_recordings


@override_settings(
    AWS_STORAGE_BUCKET_NAME='test-natya-recordings-bucket',
    AWS_S3_REGION_NAME='ap-south-1',
    ZOOM_WEBHOOK_SECRET_TOKEN='test_webhook_secret_token_phase2_1',
)
class ZoomConcurrencyAndWatchdogTests(TransactionTestCase):
    """
    Validates Phase 2.1 concurrency hardening and background recovery:
    1. Multithreaded concurrent duplicate webhook dispatch.
    2. Concurrent LiveClass primary recording updates under row lock.
    3. Stale webhook event watchdog recovery and bounded retries.
    """

    def setUp(self):
        self.instructor = User.objects.create_user(
            username='concurr_instructor',
            email='concurr_instructor@example.com',
            password='Password123!',
            is_teacher=True
        )
        self.course = Course.objects.create(
            title='Concurrency Course',
            description='Live class course',
            price=2499.00,
            course_type=Course.CourseType.LIVE,
            is_published=True
        )
        self.live_class = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title='Concurrency Class',
            description='Live class description',
            scheduled_start=timezone.now() + timedelta(days=1),
            duration_minutes=60,
            meeting_url='https://zoom.us/j/8877665544',
            zoom_meeting_id='8877665544',
            zoom_uuid='ZoomUUID_Concurrency_Test=='
        )

    def tearDown(self):
        connections.close_all()

    def _generate_zoom_signature(self, body_bytes, timestamp_str, secret='test_webhook_secret_token_phase2_1'):
        message = f"v0:{timestamp_str}:{body_bytes.decode('utf-8')}"
        sig = hmac.new(secret.encode('utf-8'), message.encode('utf-8'), hashlib.sha256).hexdigest()
        return f"v0={sig}"

    @patch('courses.tasks.process_zoom_recording_completed.delay')
    def test_multithreaded_duplicate_webhook_dispatch_only_queues_once(self, mock_delay):
        """
        Scenario 1: Two identical webhook requests arrive concurrently in separate threads.
        Assert that exactly ONE request successfully claims the event and calls .delay(),
        while the concurrent duplicate receives 'duplicate_ignored'.
        """
        event_payload = {
            'event': 'recording.completed',
            'event_ts': 1730999888,
            'payload': {
                'object': {
                    'id': '8877665544',
                    'uuid': 'ZoomUUID_Concurrency_Test==',
                    'duration': 60,
                    'total_size': 1000000,
                    'recording_files': [
                        {
                            'id': 'file_concurr_001',
                            'recording_type': 'shared_screen_with_speaker_view',
                            'file_type': 'MP4',
                            'file_size': 1000000,
                            'download_url': 'https://zoom.us/rec/play/file_concurr_001'
                        }
                    ]
                }
            }
        }
        body_bytes = json.dumps(event_payload).encode('utf-8')
        ts_str = str(int(time.time()))
        sig_header = self._generate_zoom_signature(body_bytes, ts_str)

        results = []

        def send_webhook():
            try:
                client = APIClient()
                resp = client.post(
                    '/api/courses/zoom/webhook/',
                    data=body_bytes,
                    content_type='application/json',
                    HTTP_X_ZM_SIGNATURE=sig_header,
                    HTTP_X_ZM_REQUEST_TIMESTAMP=ts_str
                )
                return resp.status_code, resp.json()
            finally:
                connections.close_all()

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            f1 = executor.submit(send_webhook)
            f2 = executor.submit(send_webhook)
            results = [f1.result(), f2.result()]

        status_codes = [r[0] for r in results]
        statuses = [r[1].get('status') for r in results]

        # Both return HTTP 200 to Zoom
        self.assertEqual(status_codes, [200, 200])

        # Exactly one is 'queued', exactly one is 'duplicate_ignored'
        self.assertIn('queued', statuses)
        self.assertIn('duplicate_ignored', statuses)

        # Celery task delay was invoked exactly ONCE
        self.assertEqual(mock_delay.call_count, 1)

        # Database has exactly ONE ZoomWebhookEvent record
        canonical_event_id = 'zoom:recording.completed:ZoomUUID_Concurrency_Test==:1730999888'
        events = ZoomWebhookEvent.objects.filter(event_id=canonical_event_id)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.first().status, ZoomWebhookEvent.Status.PROCESSING)

    @patch('courses.tasks.process_zoom_recording_completed.delay', side_effect=RuntimeError("Redis connection refused"))
    def test_dispatch_failure_reverts_status_to_received_and_returns_503(self, mock_delay):
        """
        When broker dispatch fails (.delay() raises exception), the atomic claim reverts
        the event status to RECEIVED and returns HTTP 503 so Zoom can retry later.
        """
        event_payload = {
            'event': 'recording.completed',
            'event_ts': 1730999999,
            'payload': {
                'object': {
                    'id': '8877665544',
                    'uuid': 'ZoomUUID_Concurrency_Test==',
                    'duration': 60,
                    'recording_files': []
                }
            }
        }
        body_bytes = json.dumps(event_payload).encode('utf-8')
        ts_str = str(int(time.time()))
        sig_header = self._generate_zoom_signature(body_bytes, ts_str)

        client = APIClient()
        resp = client.post(
            '/api/courses/zoom/webhook/',
            data=body_bytes,
            content_type='application/json',
            HTTP_X_ZM_SIGNATURE=sig_header,
            HTTP_X_ZM_REQUEST_TIMESTAMP=ts_str
        )

        self.assertEqual(resp.status_code, 503)
        self.assertIn('error', resp.json())

        canonical_event_id = 'zoom:recording.completed:ZoomUUID_Concurrency_Test==:1730999999'
        event_obj = ZoomWebhookEvent.objects.get(event_id=canonical_event_id)
        self.assertEqual(event_obj.status, ZoomWebhookEvent.Status.RECEIVED)
        self.assertIn('Task dispatch failed', event_obj.error_message)

    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 5000000}))
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', return_value=(True, 5000000))
    def test_concurrent_sessions_longer_recording_remains_primary(self, mock_stream, mock_verify):
        """
        Scenario 2: Two tasks complete for different sessions of the same LiveClass.
        Session 1 has duration 7200s (2 hrs).
        Session 2 has duration 300s (5 mins).
        Regardless of the order or concurrency, the 7200s session remains primary.
        """
        # Session 1: Long session (7200s)
        ev1 = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:sess_long:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '8877665544',
                    'uuid': 'sess_long_uuid',
                    'duration': 120,  # 120 mins = 7200s
                    'recording_files': [{
                        'id': 'long_sess_file_mp4',
                        'recording_type': 'shared_screen_with_speaker_view',
                        'file_type': 'MP4',
                        'file_size': 5000000,
                        'download_url': 'https://zoom.us/sess_long'
                    }]
                }
            },
            status=ZoomWebhookEvent.Status.RECEIVED
        )

        # Session 2: Short session (300s)
        ev2 = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:sess_short:2',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '8877665544',
                    'uuid': 'sess_short_uuid',
                    'duration': 5,  # 5 mins = 300s
                    'recording_files': [{
                        'id': 'shrt_sess_file_mp4',
                        'recording_type': 'shared_screen_with_speaker_view',
                        'file_type': 'MP4',
                        'file_size': 5000000,
                        'download_url': 'https://zoom.us/sess_short'
                    }]
                }
            },
            status=ZoomWebhookEvent.Status.RECEIVED
        )

        # Run process task for long session first, then short session
        process_zoom_recording_completed(ev1.id)
        self.live_class.refresh_from_db()
        self.assertIn('long_sess_file_mp4'[:8], self.live_class.recording_url)

        # Run process task for short session
        process_zoom_recording_completed(ev2.id)
        self.live_class.refresh_from_db()

        # Long session must STILL be primary
        self.assertIn('long_sess_file_mp4'[:8], self.live_class.recording_url)
        self.assertNotIn('shrt_sess_file_mp4'[:8], self.live_class.recording_url)

        # Both sessions and files exist
        self.assertEqual(self.live_class.recording_sessions.count(), 2)
        self.assertEqual(LiveClassRecordingFile.objects.filter(live_class=self.live_class, is_verified=True).count(), 2)

    @patch('courses.tasks.process_zoom_recording_completed.delay')
    def test_watchdog_recovers_stale_received_and_processing_events(self, mock_delay):
        """
        Scenario 3: Watchdog identifies events stuck in RECEIVED or PROCESSING
        beyond stale threshold (2 hours) and re-dispatches them to Celery.
        PROCESSED events and recent events are left untouched.
        """
        now = timezone.now()

        # 1. Stale RECEIVED event (3 hours ago) -> SHOULD BE RECOVERED
        ev_stale_rcv = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:stale_rcv:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={'object': {'id': '111'}},
            status=ZoomWebhookEvent.Status.RECEIVED,
        )
        ZoomWebhookEvent.objects.filter(id=ev_stale_rcv.id).update(received_at=now - timedelta(hours=3))

        # 2. Stale PROCESSING event (4 hours ago) -> SHOULD BE RECOVERED
        ev_stale_proc = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:stale_proc:2',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={'object': {'id': '222'}},
            status=ZoomWebhookEvent.Status.PROCESSING,
        )
        ZoomWebhookEvent.objects.filter(id=ev_stale_proc.id).update(received_at=now - timedelta(hours=4))

        # 3. Already PROCESSED event (5 hours ago) -> MUST NOT BE TOUCHED
        ev_processed = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:already_proc:3',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={'object': {'id': '333'}},
            status=ZoomWebhookEvent.Status.PROCESSED,
        )
        ZoomWebhookEvent.objects.filter(id=ev_processed.id).update(received_at=now - timedelta(hours=5))

        # 4. Recent RECEIVED event (30 minutes ago) -> MUST NOT BE TOUCHED
        ev_recent = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:recent:4',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={'object': {'id': '444'}},
            status=ZoomWebhookEvent.Status.RECEIVED,
        )
        ZoomWebhookEvent.objects.filter(id=ev_recent.id).update(received_at=now - timedelta(minutes=30))

        # Run watchdog recovery scan
        recovered = recover_stale_zoom_recordings(stale_hours=2, max_retries=3)

        self.assertEqual(recovered, 2)
        self.assertEqual(mock_delay.call_count, 2)

        # Inspect ev_stale_rcv
        ev_stale_rcv.refresh_from_db()
        self.assertEqual(ev_stale_rcv.status, ZoomWebhookEvent.Status.PROCESSING)
        self.assertEqual(ev_stale_rcv.payload.get('_recovery_attempts'), 1)

        # Inspect ev_stale_proc
        ev_stale_proc.refresh_from_db()
        self.assertEqual(ev_stale_proc.status, ZoomWebhookEvent.Status.PROCESSING)
        self.assertEqual(ev_stale_proc.payload.get('_recovery_attempts'), 1)

        # Inspect ev_processed
        ev_processed.refresh_from_db()
        self.assertEqual(ev_processed.status, ZoomWebhookEvent.Status.PROCESSED)

        # Inspect ev_recent
        ev_recent.refresh_from_db()
        self.assertEqual(ev_recent.status, ZoomWebhookEvent.Status.RECEIVED)
        self.assertIsNone(ev_recent.payload.get('_recovery_attempts'))

    @patch('courses.tasks.process_zoom_recording_completed.delay')
    def test_watchdog_bounded_retries_marks_as_failed_after_max(self, mock_delay):
        """
        When an event has reached max recovery attempts (e.g. 3), the watchdog
        marks it FAILED with an informative error message and stops retrying.
        """
        now = timezone.now()
        ev_max = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:max_retries:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={'object': {'id': '555'}, '_recovery_attempts': 3},
            status=ZoomWebhookEvent.Status.PROCESSING,
        )
        ZoomWebhookEvent.objects.filter(id=ev_max.id).update(received_at=now - timedelta(hours=3))

        recovered = recover_stale_zoom_recordings(stale_hours=2, max_retries=3)

        self.assertEqual(recovered, 0)
        mock_delay.assert_not_called()

        ev_max.refresh_from_db()
        self.assertEqual(ev_max.status, ZoomWebhookEvent.Status.FAILED)
        self.assertIn('abandoned after 3 recovery attempts', ev_max.error_message)

    @patch('courses.tasks.process_zoom_recording_completed.delay')
    def test_watchdog_recent_dispatch_skips_duplicate(self, mock_delay):
        """
        If a watchdog worker just dispatched an event moments ago (< 30 mins),
        a second watchdog worker does not double-dispatch.
        """
        now = timezone.now()
        ev_recent_disp = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:recent_disp:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {'id': '666'},
                '_recovery_attempts': 1,
                '_last_watchdog_dispatch': now.isoformat()
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )
        ZoomWebhookEvent.objects.filter(id=ev_recent_disp.id).update(received_at=now - timedelta(hours=3))

        recovered = recover_stale_zoom_recordings(stale_hours=2, max_retries=3)

        self.assertEqual(recovered, 0)
        mock_delay.assert_not_called()

    def test_celery_beat_schedule_configuration(self):
        """
        Confirms recover_stale_zoom_recordings is properly registered in CELERY_BEAT_SCHEDULE.
        """
        beat_sched = getattr(settings, 'CELERY_BEAT_SCHEDULE', {})
        self.assertIn('recover-stale-zoom-recordings', beat_sched)
        task_conf = beat_sched['recover-stale-zoom-recordings']
        self.assertEqual(task_conf['task'], 'courses.tasks.recover_stale_zoom_recordings')



    # =========================================================================
    # Phase 2.2: Watchdog Active-Task Protection & Heartbeat Tests
    # =========================================================================
    @patch('courses.tasks.process_zoom_recording_completed.delay')
    def test_watchdog_active_processing_task_with_valid_lease_is_not_redispatched(self, mock_delay):
        """
        Requirement 1 & 4: An actively running PROCESSING task (e.g. 3 hours into
        a 5-hour class transfer) with a valid worker lease is NOT redispatched by
        the watchdog, even though received_at is older than stale_hours.
        """
        now = timezone.now()
        active_lease_expires = (now + timedelta(minutes=12)).isoformat()
        recent_heartbeat = (now - timedelta(seconds=45)).isoformat()

        ev_active = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:active_task:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {'id': '777'},
                '_task_id': 'active-worker-task-1',
                '_worker_lease_expires_at': active_lease_expires,
                '_last_heartbeat_at': recent_heartbeat,
                '_heartbeat_stage': 'uploading_part_42',
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )
        ZoomWebhookEvent.objects.filter(id=ev_active.id).update(received_at=now - timedelta(hours=3))

        recovered = recover_stale_zoom_recordings(stale_hours=2, max_retries=3)

        self.assertEqual(recovered, 0, "Active task with valid worker lease must not be redispatched")
        mock_delay.assert_not_called()

        ev_active.refresh_from_db()
        self.assertEqual(ev_active.status, ZoomWebhookEvent.Status.PROCESSING)
        self.assertEqual(ev_active.payload.get('_heartbeat_stage'), 'uploading_part_42')
        self.assertIsNone(ev_active.payload.get('_recovery_attempts'))

    @patch('courses.tasks.process_zoom_recording_completed.delay')
    def test_watchdog_recovers_genuinely_abandoned_task(self, mock_delay):
        """
        Requirement 5: A worker died halfway through upload. Heartbeats stopped
        and the 15-minute lease expired. The watchdog discovers it and recovers it.
        """
        now = timezone.now()
        expired_lease = (now - timedelta(minutes=20)).isoformat()
        stopped_heartbeat = (now - timedelta(minutes=35)).isoformat()

        ev_abandoned = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:abandoned_task:2',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {'id': '888'},
                '_task_id': 'dead-worker-task-2',
                '_worker_lease_expires_at': expired_lease,
                '_last_heartbeat_at': stopped_heartbeat,
                '_heartbeat_stage': 'uploading_part_15',
                '_recovery_attempts': 0,
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )
        ZoomWebhookEvent.objects.filter(id=ev_abandoned.id).update(received_at=now - timedelta(hours=3))

        recovered = recover_stale_zoom_recordings(stale_hours=2, max_retries=3)

        self.assertEqual(recovered, 1, "Abandoned task with expired lease must be recovered")
        mock_delay.assert_called_once_with(ev_abandoned.id)

        ev_abandoned.refresh_from_db()
        self.assertEqual(ev_abandoned.status, ZoomWebhookEvent.Status.PROCESSING)
        self.assertEqual(ev_abandoned.payload.get('_recovery_attempts'), 1)
        self.assertEqual(ev_abandoned.payload.get('_heartbeat_stage'), 'watchdog_redispatched')

    @patch('courses.tasks.process_zoom_recording_completed.delay')
    def test_watchdog_recovers_expired_worker_lease(self, mock_delay):
        """
        Requirement 4 & 5: When worker crashes or host is lost, lease expires.
        Watchdog verifies expired lease and re-dispatches.
        """
        now = timezone.now()
        expired_lease = (now - timedelta(minutes=5)).isoformat()
        last_hb = (now - timedelta(minutes=20)).isoformat()

        ev_crashed = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:crashed_lease:3',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {'id': '999'},
                '_worker_lease_expires_at': expired_lease,
                '_last_heartbeat_at': last_hb,
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )
        ZoomWebhookEvent.objects.filter(id=ev_crashed.id).update(received_at=now - timedelta(hours=2, minutes=30))

        recovered = recover_stale_zoom_recordings(stale_hours=2, max_retries=3)

        self.assertEqual(recovered, 1)
        mock_delay.assert_called_once_with(ev_crashed.id)

    @patch('courses.tasks.process_zoom_recording_completed.delay')
    def test_watchdog_never_redispatches_processed_event(self, mock_delay):
        """
        Requirement 7: A PROCESSED event is NEVER redispatched under any circumstances,
        even if received_at is in the past and lease is empty or expired.
        """
        now = timezone.now()
        ev_done = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:already_done:4',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {'id': '1010'},
                '_worker_lease_expires_at': None,
                '_heartbeat_stage': 'completed',
            },
            status=ZoomWebhookEvent.Status.PROCESSED,
        )
        ZoomWebhookEvent.objects.filter(id=ev_done.id).update(received_at=now - timedelta(hours=5))

        recovered = recover_stale_zoom_recordings(stale_hours=2, max_retries=3)

        self.assertEqual(recovered, 0)
        mock_delay.assert_not_called()
        ev_done.refresh_from_db()
        self.assertEqual(ev_done.status, ZoomWebhookEvent.Status.PROCESSED)

    def test_worker_heartbeat_callback_renews_lease_during_streaming(self):
        """
        Tests that ZoomRecordingService.update_task_heartbeat atomically updates
        _last_heartbeat_at, _worker_lease_expires_at, and stage without altering status.
        """
        now = timezone.now()
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:hb_test:5',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={'object': {'id': '2020'}},
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        ZoomRecordingService.update_task_heartbeat(
            webhook_event_id=ev.id,
            task_id='test-worker-task-hb',
            lease_seconds=900,
            stage='uploading_part_7',
            bytes_transferred=73400320
        )

        ev.refresh_from_db()
        payload = ev.payload
        self.assertEqual(payload.get('_task_id'), 'test-worker-task-hb')
        self.assertEqual(payload.get('_heartbeat_stage'), 'uploading_part_7')
        self.assertEqual(payload.get('_bytes_transferred'), 73400320)
        self.assertIsNotNone(payload.get('_last_heartbeat_at'))
        self.assertIsNotNone(payload.get('_worker_lease_expires_at'))

        import datetime
        lease_dt = datetime.datetime.fromisoformat(payload['_worker_lease_expires_at'])
        if timezone.is_naive(lease_dt):
            lease_dt = timezone.make_aware(lease_dt)
        self.assertGreater(lease_dt, now + timedelta(minutes=14))


    # =========================================================================
    # Phase 2.3: Worker Ownership Fencing & Remediation Tests
    # =========================================================================
    def test_worker1_loses_lease_worker2_takes_ownership_worker1_heartbeat_rejected(self):
        """
        1. Worker 1 loses lease, Worker 2 takes ownership.
        Worker 1 attempts a heartbeat: update_task_heartbeat returns False and
        the database task_id remains Worker 2.
        """
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:fencing:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {'id': '101'},
                '_task_id': 'worker-task-2',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=15)).isoformat(),
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        # Worker 1 tries to send heartbeat
        ok = ZoomRecordingService.update_task_heartbeat(
            webhook_event_id=ev.id,
            task_id='worker-task-1',
            lease_seconds=900,
            stage='uploading_part_2'
        )
        self.assertFalse(ok, "Heartbeat from superseded Worker 1 must be rejected")

        ev.refresh_from_db()
        self.assertEqual(ev.payload.get('_task_id'), 'worker-task-2', "Task ID must remain Worker 2")

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 1000000}))
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3')
    def test_worker1_attempts_to_mark_processed_after_worker2_takes_ownership(self, mock_stream, mock_verify, mock_s3_active):
        """
        2. Worker 1 attempts to mark the event PROCESSED after Worker 2 takes ownership.
        Fencing rejects the terminal transition: status remains PROCESSING, owned by Worker 2.
        """
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:fencing:2',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '102',
                    'uuid': 'uuid_102',
                    'duration': 60,
                    'recording_files': [{
                        'id': 'file_102_mp4',
                        'recording_type': 'shared_screen_with_speaker_view',
                        'file_type': 'MP4',
                        'file_size': 1000000,
                        'download_url': 'https://zoom.us/f102'
                    }]
                },
                '_task_id': 'worker-task-1',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=15)).isoformat(),
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        def steal_ownership_during_stream(*args, **kwargs):
            ev.payload['_task_id'] = 'worker-task-2'
            ev.save(update_fields=['payload'])
            return (True, 1000000)

        mock_stream.side_effect = steal_ownership_during_stream

        # Worker 1 finishes stream, but before marking PROCESSED, Worker 2 became owner.
        process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-task-1')

        ev.refresh_from_db()
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.PROCESSING, "Event must not be marked PROCESSED by superseded Worker 1")
        self.assertEqual(ev.payload.get('_task_id'), 'worker-task-2', "Owner must remain Worker 2")

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    def test_worker1_attempts_to_mark_failed_after_worker2_takes_ownership(self, mock_s3_active):
        """
        3. Worker 1 encounters an exception and attempts to mark the event FAILED.
        Because Worker 2 is now the owner, Worker 1 cannot overwrite status with FAILED.
        """
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:fencing:3',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '103',
                    'uuid': 'uuid_103',
                    'recording_files': [{
                        'id': 'file_103',
                        'file_type': 'MP4',
                        'file_size': 1000,
                        'download_url': 'https://zoom.us/f103'
                    }]
                },
                '_task_id': 'worker-task-1',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=15)).isoformat(),
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        def steal_ownership_and_fail(*args, **kwargs):
            ev.payload['_task_id'] = 'worker-task-2'
            ev.save(update_fields=['payload'])
            raise RuntimeError("Network timeout during stream")

        with patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', side_effect=steal_ownership_and_fail):
            with patch.object(process_zoom_recording_completed, 'retry', side_effect=RuntimeError("Worker retry")):
                with self.assertRaises(RuntimeError):
                    process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-task-1', throw=True)

        ev.refresh_from_db()
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.PROCESSING)
        self.assertEqual(ev.payload.get('_task_id'), 'worker-task-2')

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.get_s3_client')
    @patch('requests.get')
    def test_slow_zoom_download_continues_beyond_heartbeat_interval(self, mock_requests_get, mock_s3_client, mock_s3_active):
        """
        4. A slow Zoom download chunk stream emits heartbeats with stage='downloading'
        so the lease remains refreshed during slow data transfer.
        """
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.headers = {'content-type': 'video/mp4'}
        # Simulate 10 chunks of 1 MB each (10 MB total)
        chunk_1mb = b'X' * (1024 * 1024)
        mock_res.iter_content.return_value = [chunk_1mb] * 10
        mock_requests_get.return_value = mock_res

        mock_s3 = MagicMock()
        mock_s3.create_multipart_upload.return_value = {'UploadId': 'slow_up_id'}
        mock_s3.upload_part.return_value = {'ETag': 'etag_slow'}
        mock_s3_client.return_value = mock_s3

        heartbeat_stages = []
        def test_hb(stage='in_progress', part_number=1, total_bytes=0):
            heartbeat_stages.append(stage)
            return True

        ok, total = ZoomRecordingService.stream_zoom_recording_to_s3(
            download_url='https://zoom.us/slow_file',
            s3_bucket='test-bucket',
            s3_key='recordings/slow.mp4',
            part_size=10 * 1024 * 1024,
            heartbeat_callback=test_hb
        )

        self.assertTrue(ok)
        self.assertIn('downloading', heartbeat_stages, "Download phase must emit heartbeats")
        self.assertIn('completing_multipart_upload', heartbeat_stages)

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.get_s3_client')
    @patch('requests.get')
    def test_file_smaller_than_10mb_produces_heartbeat_updates(self, mock_requests_get, mock_s3_client, mock_s3_active):
        """
        5. A file smaller than 10 MB (e.g. 2 MB) produces appropriate heartbeats
        during final partial upload and before completing.
        """
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.headers = {'content-type': 'video/mp4'}
        mock_res.iter_content.return_value = [b'A' * (1024 * 1024 * 2)]  # 2 MB
        mock_requests_get.return_value = mock_res

        mock_s3 = MagicMock()
        mock_s3.create_multipart_upload.return_value = {'UploadId': 'small_up_id'}
        mock_s3.upload_part.return_value = {'ETag': 'etag_small'}
        mock_s3_client.return_value = mock_s3

        stages = []
        def test_hb(stage='in_progress', part_number=1, total_bytes=0):
            stages.append(stage)
            return True

        ok, total = ZoomRecordingService.stream_zoom_recording_to_s3(
            download_url='https://zoom.us/small_file',
            s3_bucket='test-bucket',
            s3_key='recordings/small.mp4',
            part_size=10 * 1024 * 1024,
            heartbeat_callback=test_hb
        )

        self.assertTrue(ok)
        self.assertEqual(total, 2 * 1024 * 1024)
        self.assertIn('uploaded_part_1', stages, "Small file must send heartbeat on final partial upload")
        self.assertIn('completing_multipart_upload', stages)

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.get_s3_client')
    @patch('requests.get')
    def test_final_partial_upload_sends_heartbeat(self, mock_requests_get, mock_s3_client, mock_s3_active):
        """
        6. A 15 MB file (10 MB full part + 5 MB remainder) emits heartbeats for both part 1
        and the final remainder part 2.
        """
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.headers = {'content-type': 'video/mp4'}
        chunk_5mb = b'B' * (5 * 1024 * 1024)
        mock_res.iter_content.return_value = [chunk_5mb, chunk_5mb, chunk_5mb]  # 15 MB total
        mock_requests_get.return_value = mock_res

        mock_s3 = MagicMock()
        mock_s3.create_multipart_upload.return_value = {'UploadId': 'part15_up_id'}
        mock_s3.upload_part.return_value = {'ETag': 'etag_15'}
        mock_s3_client.return_value = mock_s3

        stages = []
        def test_hb(stage='in_progress', part_number=1, total_bytes=0):
            stages.append(stage)
            return True

        ok, total = ZoomRecordingService.stream_zoom_recording_to_s3(
            download_url='https://zoom.us/15mb_file',
            s3_bucket='test-bucket',
            s3_key='recordings/15mb.mp4',
            part_size=10 * 1024 * 1024,
            heartbeat_callback=test_hb
        )

        self.assertTrue(ok)
        self.assertIn('uploaded_part_1', stages)
        self.assertIn('uploaded_part_2', stages, "Final partial part 2 must emit heartbeat")

    @patch('courses.tasks.process_zoom_recording_completed.delay')
    def test_watchdog_delay_failure_restores_event_and_does_not_consume_recovery_attempt(self, mock_delay):
        """
        7. Watchdog .delay() failure restores the event to RECEIVED, clears its lease,
        sets _heartbeat_stage = 'watchdog_dispatch_failed', and does not consume a recovery attempt.
        """
        now = timezone.now()
        mock_delay.side_effect = Exception("Redis broker unavailable")

        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:watchdog_fail:7',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={'object': {'id': '707'}, '_recovery_attempts': 1},
            status=ZoomWebhookEvent.Status.RECEIVED,
        )
        ZoomWebhookEvent.objects.filter(id=ev.id).update(received_at=now - timedelta(hours=3))

        recovered = recover_stale_zoom_recordings(stale_hours=2, max_retries=3)

        self.assertEqual(recovered, 0)
        ev.refresh_from_db()
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.RECEIVED)
        self.assertIsNone(ev.payload.get('_worker_lease_expires_at'))
        self.assertEqual(ev.payload.get('_heartbeat_stage'), 'watchdog_dispatch_failed')
        self.assertEqual(ev.payload.get('_recovery_attempts'), 1, "Recovery attempts must not be consumed on broker failure")

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload')
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3')
    def test_partial_session_does_not_become_permanently_processed_webhook_event(self, mock_stream, mock_verify, mock_s3_active):
        """
        8. A PARTIAL session (e.g. secondary file missing/failed) raises an error so Celery
        can retry, and does NOT mark the webhook event PROCESSED.
        """
        mock_stream.return_value = (True, 5000000)
        mock_verify.return_value = (True, {'ContentLength': 5000000})

        # Webhook event containing 2 files
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:partial_sess:8',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '808',
                    'uuid': 'uuid_808',
                    'duration': 60,
                    'recording_files': [
                        {
                            'id': 'file_808_video',
                            'recording_type': 'shared_screen_with_speaker_view',
                            'file_type': 'MP4',
                            'file_size': 5000000,
                            'download_url': 'https://zoom.us/f808_vid'
                        },
                        {
                            'id': 'file_808_audio',
                            'recording_type': 'audio_only',
                            'file_type': 'M4A',
                            'file_size': 1000000,
                            'download_url': ''  # Missing download URL causes file to be skipped, making session PARTIAL
                        }
                    ]
                }
            },
            status=ZoomWebhookEvent.Status.RECEIVED
        )

        # Run task with mock retry to catch exception
        with patch.object(process_zoom_recording_completed, 'retry', side_effect=RuntimeError("Retry triggered")):
            with self.assertRaises(RuntimeError):
                process_zoom_recording_completed.apply(args=[ev.id], throw=True)

        ev.refresh_from_db()
        self.assertNotEqual(ev.status, ZoomWebhookEvent.Status.PROCESSED, "PARTIAL session must not mark webhook PROCESSED")
        session = LiveClassRecording.objects.get(zoom_uuid='uuid_808')
        self.assertEqual(session.status, LiveClassRecording.Status.PARTIAL)

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload')
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3')
    def test_previously_completed_files_are_not_uploaded_again_during_retry(self, mock_stream, mock_verify, mock_s3_active):
        """
        9. During retry, previously completed and verified files are skipped
        and not streamed to S3 again.
        """
        mock_stream.return_value = (True, 1000000)
        mock_verify.return_value = (True, {'ContentLength': 1000000})

        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:retry_skip:9',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '909',
                    'uuid': 'uuid_909',
                    'duration': 60,
                    'recording_files': [
                        {
                            'id': 'file_909_already_done',
                            'recording_type': 'shared_screen_with_speaker_view',
                            'file_type': 'MP4',
                            'file_size': 5000000,
                            'download_url': 'https://zoom.us/f909_done'
                        },
                        {
                            'id': 'file_909_new_retry',
                            'recording_type': 'audio_only',
                            'file_type': 'M4A',
                            'file_size': 1000000,
                            'download_url': 'https://zoom.us/f909_new'
                        }
                    ]
                }
            },
            status=ZoomWebhookEvent.Status.RECEIVED
        )

        session = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='uuid_909',
            zoom_meeting_id='909',
            session_index=1,
            status=LiveClassRecording.Status.PARTIAL,
            file_count=2,
            duration_seconds=3600
        )

        # Pre-create file 1 as verified
        LiveClassRecordingFile.objects.create(
            recording=session,
            live_class=self.live_class,
            zoom_file_id='file_909_already_done',
            file_type=LiveClassRecordingFile.FileType.MP4,
            is_verified=True,
            status=LiveClassRecordingFile.Status.VERIFIED,
            s3_bucket='test-bucket',
            s3_key='recordings/part1.mp4'
        )

        process_zoom_recording_completed.apply(args=[ev.id])

        # stream_zoom_recording_to_s3 must have been called ONLY for file_909_new_retry
        self.assertEqual(mock_stream.call_count, 1)
        call_kwargs = mock_stream.call_args.kwargs
        self.assertIn('f909_new', call_kwargs.get('download_url'))

    # =========================================================================
    # Phase 2.4: Final Database Write Fencing Tests
    # =========================================================================
    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 2000000}))
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3')
    def test_worker1_loses_ownership_cannot_update_liveclass_recording_status(self, mock_stream, mock_verify, mock_s3_active):
        """
        Phase 2.4 - Test 1: Worker 1 loses ownership to Worker 2 and cannot update
        LiveClassRecording.status or completed_at.
        """
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p24_test1:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '2401',
                    'uuid': 'uuid_2401',
                    'duration': 60,
                    'recording_files': [{
                        'id': 'file_2401_mp4',
                        'recording_type': 'shared_screen_with_speaker_view',
                        'file_type': 'MP4',
                        'file_size': 2000000,
                        'download_url': 'https://zoom.us/f2401'
                    }]
                },
                '_task_id': 'worker-task-1',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=15)).isoformat(),
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        session = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='uuid_2401',
            zoom_meeting_id='2401',
            session_index=1,
            status=LiveClassRecording.Status.TRANSFERRING,
            file_count=1,
            duration_seconds=3600
        )

        def steal_ownership_during_stream(*args, **kwargs):
            ev.payload['_task_id'] = 'worker-task-2'
            ev.save(update_fields=['payload'])
            return (True, 2000000)

        mock_stream.side_effect = steal_ownership_during_stream

        # Worker 1 runs
        process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-task-1')

        # Assert: session status MUST NOT be updated to COMPLETED by superseded Worker 1
        session.refresh_from_db()
        self.assertEqual(session.status, LiveClassRecording.Status.TRANSFERRING, "Superseded worker must not update session status")
        self.assertIsNone(session.completed_at, "Superseded worker must not set completed_at timestamp")

        ev.refresh_from_db()
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.PROCESSING, "Event must not be marked PROCESSED")
        self.assertEqual(ev.payload.get('_task_id'), 'worker-task-2', "Lease owner must remain Worker 2")

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 2000000}))
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3')
    def test_worker1_loses_ownership_cannot_update_liveclass_recording_url(self, mock_stream, mock_verify, mock_s3_active):
        """
        Phase 2.4 - Test 2: Worker 1 cannot update LiveClass.recording_url after
        losing ownership.
        """
        original_url = "https://original-domain.s3.amazonaws.com/legacy_rec.mp4"
        self.live_class.recording_url = original_url
        self.live_class.save(update_fields=['recording_url'])

        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p24_test2:2',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '2402',
                    'uuid': 'uuid_2402',
                    'duration': 60,
                    'recording_files': [{
                        'id': 'file_2402_mp4',
                        'recording_type': 'shared_screen_with_speaker_view',
                        'file_type': 'MP4',
                        'file_size': 2000000,
                        'download_url': 'https://zoom.us/f2402'
                    }]
                },
                '_task_id': 'worker-task-1',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=15)).isoformat(),
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        def steal_ownership_during_stream(*args, **kwargs):
            ev.payload['_task_id'] = 'worker-task-2'
            ev.save(update_fields=['payload'])
            return (True, 2000000)

        mock_stream.side_effect = steal_ownership_during_stream

        # Worker 1 runs
        process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-task-1')

        # Assert: LiveClass.recording_url MUST NOT be overwritten by superseded Worker 1
        self.live_class.refresh_from_db()
        self.assertEqual(
            self.live_class.recording_url,
            original_url,
            "Superseded worker must not modify LiveClass.recording_url"
        )

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 2000000}))
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3')
    def test_worker1_loses_ownership_cannot_change_liveclass_recording_status(self, mock_stream, mock_verify, mock_s3_active):
        """
        Phase 2.4 - Test 3: Worker 1 cannot change LiveClass.recording_status after
        losing ownership.
        """
        self.live_class.recording_status = LiveClass.RecordingStatus.TRANSFERRING
        self.live_class.save(update_fields=['recording_status'])

        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p24_test3:3',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '2403',
                    'uuid': 'uuid_2403',
                    'duration': 60,
                    'recording_files': [{
                        'id': 'file_2403_mp4',
                        'recording_type': 'shared_screen_with_speaker_view',
                        'file_type': 'MP4',
                        'file_size': 2000000,
                        'download_url': 'https://zoom.us/f2403'
                    }]
                },
                '_task_id': 'worker-task-1',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=15)).isoformat(),
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        def steal_ownership_during_stream(*args, **kwargs):
            ev.payload['_task_id'] = 'worker-task-2'
            ev.save(update_fields=['payload'])
            return (True, 2000000)

        mock_stream.side_effect = steal_ownership_during_stream

        # Worker 1 runs
        process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-task-1')

        # Assert: LiveClass.recording_status MUST NOT be changed to COMPLETED by superseded Worker 1
        self.live_class.refresh_from_db()
        self.assertEqual(
            self.live_class.recording_status,
            LiveClass.RecordingStatus.TRANSFERRING,
            "Superseded worker must not modify LiveClass.recording_status"
        )

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 2000000}))
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3')
    def test_worker2_as_current_owner_completes_session_and_updates_primary_recording(self, mock_stream, mock_verify, mock_s3_active):
        """
        Phase 2.4 - Test 4: Worker 2 (current owner) can still complete the session
        and update the primary recording after Worker 1 loses ownership.
        """
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p24_test4:4',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '2404',
                    'uuid': 'uuid_2404',
                    'duration': 60,
                    'recording_files': [{
                        'id': 'file_2404_mp4',
                        'recording_type': 'shared_screen_with_speaker_view',
                        'file_type': 'MP4',
                        'file_size': 2000000,
                        'download_url': 'https://zoom.us/f2404'
                    }]
                },
                '_task_id': 'worker-task-1',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=15)).isoformat(),
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        session = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='uuid_2404',
            zoom_meeting_id='2404',
            session_index=1,
            status=LiveClassRecording.Status.TRANSFERRING,
            file_count=1,
            duration_seconds=3600
        )

        def steal_ownership_during_stream(*args, **kwargs):
            ev.payload['_task_id'] = 'worker-task-2'
            ev.save(update_fields=['payload'])
            return (True, 2000000)

        mock_stream.side_effect = steal_ownership_during_stream

        # Worker 1 runs and cleanly terminates due to lost ownership
        process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-task-1')

        # Session is still TRANSFERRING and event is still PROCESSING
        session.refresh_from_db()
        self.assertEqual(session.status, LiveClassRecording.Status.TRANSFERRING)

        # Now Worker 2 (legitimate owner) runs
        mock_stream.side_effect = None
        mock_stream.return_value = (True, 2000000)
        process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-task-2')

        # Worker 2 successfully completes session and updates primary recording
        session.refresh_from_db()
        self.assertEqual(session.status, LiveClassRecording.Status.COMPLETED)
        self.assertIsNotNone(session.completed_at)

        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.recording_status, LiveClass.RecordingStatus.COMPLETED)
        self.assertIn('file_2404'[:8], self.live_class.recording_url)

        ev.refresh_from_db()
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.PROCESSED)

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 2000000}))
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', return_value=(True, 2000000))
    def test_concurrent_ownership_change_prevented_between_validation_and_write(self, mock_stream, mock_verify, mock_s3_active):
        """
        Phase 2.4 - Test 5: A concurrent ownership change cannot occur between
        validation and the protected database write.
        Verifies that select_for_update() row locking is acquired on ZoomWebhookEvent
        and that if ownership changes right before lock acquisition, the write is
        cleanly aborted and no mutations to session or LiveClass occur.
        """
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p24_test5:5',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '2405',
                    'uuid': 'uuid_2405',
                    'duration': 60,
                    'recording_files': [{
                        'id': 'file_2405_mp4',
                        'recording_type': 'shared_screen_with_speaker_view',
                        'file_type': 'MP4',
                        'file_size': 2000000,
                        'download_url': 'https://zoom.us/f2405'
                    }]
                },
                '_task_id': 'worker-task-1',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=15)).isoformat(),
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        session = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='uuid_2405',
            zoom_meeting_id='2405',
            session_index=1,
            status=LiveClassRecording.Status.TRANSFERRING,
            file_count=1,
            duration_seconds=3600
        )

        select_for_update_called = []
        original_sfu = ZoomWebhookEvent.objects.select_for_update

        def tracked_sfu(*args, **kwargs):
            select_for_update_called.append(True)
            return original_sfu(*args, **kwargs)

        # Hook to simulate concurrent ownership reassignment right when the lock is requested
        with patch.object(ZoomWebhookEvent.objects, 'select_for_update', side_effect=tracked_sfu):
            # Worker 1 runs
            process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-task-1')

        self.assertTrue(len(select_for_update_called) >= 1, "select_for_update must be invoked during execution")

        # Now test that if ownership is changed immediately before acquiring the final write lock,
        # the atomic check detects it and prevents write:
        ev2 = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p24_test5_race:6',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '2406',
                    'uuid': 'uuid_2406',
                    'duration': 60,
                    'recording_files': [{
                        'id': 'file_2406_mp4',
                        'recording_type': 'shared_screen_with_speaker_view',
                        'file_type': 'MP4',
                        'file_size': 2000000,
                        'download_url': 'https://zoom.us/f2406'
                    }]
                },
                '_task_id': 'worker-task-1',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=15)).isoformat(),
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        session2 = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='uuid_2406',
            zoom_meeting_id='2406',
            session_index=1,
            status=LiveClassRecording.Status.TRANSFERRING,
            file_count=1,
            duration_seconds=3600
        )

        # Right before the write transaction locks, another worker updates the event's _task_id
        def intercept_sfu_and_race(*args, **kwargs):
            qs = original_sfu(*args, **kwargs)
            # Simulate concurrent transaction committing _task_id = 'worker-task-2'
            ZoomWebhookEvent.objects.filter(id=ev2.id).update(
                payload={
                    **ev2.payload,
                    '_task_id': 'worker-task-2'
                }
            )
            return qs

        with patch.object(ZoomWebhookEvent.objects, 'select_for_update', side_effect=intercept_sfu_and_race):
            process_zoom_recording_completed.apply(args=[ev2.id], task_id='worker-task-1')

        # Assert: Even though Worker 1 streamed the file, the write was blocked and aborted
        session2.refresh_from_db()
        self.assertEqual(session2.status, LiveClassRecording.Status.TRANSFERRING, "Concurrent race must prevent status update")
        self.assertIsNone(session2.completed_at, "completed_at must remain None")

    # =========================================================================
    # Phase 2.4.1: Fail-Closed Locking Correction & Concurrency Tests
    # =========================================================================
    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 2000000}))
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', return_value=(True, 2000000))
    def test_lock_acquisition_failure_causes_zero_final_database_mutations(self, mock_stream, mock_verify, mock_s3_active):
        """
        Phase 2.4.1 - Test A: Lock acquisition failure (e.g. OperationalError)
        causes zero final database mutations (fails closed).
        Proves that an unlocked query fallback is NOT used.
        """
        from django.db import OperationalError

        initial_url = "https://legacy-domain.s3.amazonaws.com/initial.mp4"
        self.live_class.recording_url = initial_url
        self.live_class.recording_status = LiveClass.RecordingStatus.TRANSFERRING
        self.live_class.save(update_fields=['recording_url', 'recording_status'])

        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p241_lock_fail:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '2411',
                    'uuid': 'uuid_2411',
                    'duration': 60,
                    'recording_files': [{
                        'id': 'file_2411_mp4',
                        'recording_type': 'shared_screen_with_speaker_view',
                        'file_type': 'MP4',
                        'file_size': 2000000,
                        'download_url': 'https://zoom.us/f2411'
                    }]
                },
                '_task_id': 'worker-task-1',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=15)).isoformat(),
            },
            status=ZoomWebhookEvent.Status.PROCESSING,
        )

        session = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='uuid_2411',
            zoom_meeting_id='2411',
            session_index=1,
            status=LiveClassRecording.Status.TRANSFERRING,
            file_count=1,
            duration_seconds=3600
        )

        original_sfu = ZoomWebhookEvent.objects.select_for_update
        call_count = {'val': 0}

        def fail_finalization_sfu(*args, **kwargs):
            call_count['val'] += 1
            # First call is during task startup (allow it to proceed)
            if call_count['val'] > 1:
                raise OperationalError("could not obtain lock on row in relation courses_zoomwebhookevent")
            return original_sfu(*args, **kwargs)

        with patch.object(ZoomWebhookEvent.objects, 'select_for_update', side_effect=fail_finalization_sfu):
            with patch.object(process_zoom_recording_completed, 'retry', side_effect=RuntimeError("Celery retry triggered")):
                with self.assertRaises(RuntimeError):
                    process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-task-1', throw=True)

        # Assert: FAIL-CLOSED: Zero final mutations occurred!
        session.refresh_from_db()
        self.assertEqual(session.status, LiveClassRecording.Status.TRANSFERRING, "Status must remain TRANSFERRING on lock failure")
        self.assertIsNone(session.completed_at, "completed_at must remain None on lock failure")

        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.recording_url, initial_url, "recording_url must NOT be updated on lock failure")
        self.assertEqual(self.live_class.recording_status, LiveClass.RecordingStatus.TRANSFERRING, "recording_status must NOT be COMPLETED")

        ev.refresh_from_db()
        self.assertNotEqual(ev.status, ZoomWebhookEvent.Status.PROCESSED, "Event must NOT be marked PROCESSED on lock failure")

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    @patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', return_value=(True, {'ContentLength': 5000000}))
    @patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', return_value=(True, 5000000))
    def test_partial_status_is_committed_before_retry_and_event_is_recoverable(self, mock_stream, mock_verify, mock_s3_active):
        """
        Phase 2.4.1 - Test B: Partial session status is committed successfully
        under lock BEFORE the retry exception is raised outside the transaction,
        and the event remains recoverable (not marked PROCESSED).
        """
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p241_partial:2',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '2412',
                    'uuid': 'uuid_2412',
                    'duration': 60,
                    'recording_files': [
                        {
                            'id': 'file_2412_vid',
                            'recording_type': 'shared_screen_with_speaker_view',
                            'file_type': 'MP4',
                            'file_size': 5000000,
                            'download_url': 'https://zoom.us/f2412_vid'
                        },
                        {
                            'id': 'file_2412_aud',
                            'recording_type': 'audio_only',
                            'file_type': 'M4A',
                            'file_size': 1000000,
                            'download_url': '' # Missing download URL causes file to fail
                        }
                    ]
                }
            },
            status=ZoomWebhookEvent.Status.RECEIVED
        )

        with patch.object(process_zoom_recording_completed, 'retry', side_effect=RuntimeError("Celery retry triggered")):
            with self.assertRaises(RuntimeError):
                process_zoom_recording_completed.apply(args=[ev.id], throw=True)

        # 1. Partial session status was committed to DB
        session = LiveClassRecording.objects.get(zoom_uuid='uuid_2412')
        self.assertEqual(session.status, LiveClassRecording.Status.PARTIAL)
        self.assertIsNotNone(session.completed_at)

        # 2. Event is NOT marked PROCESSED
        ev.refresh_from_db()
        self.assertNotEqual(ev.status, ZoomWebhookEvent.Status.PROCESSED)
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.FAILED)
        self.assertIn("not fully completed", ev.error_message)

        # 3. Verified file exists
        verified_count = session.files.filter(is_verified=True).count()
        self.assertEqual(verified_count, 1)

    # =========================================================================
    # Phase 2.4.2: Partial Recording Retry & Event Recovery Tests
    # =========================================================================
    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    def test_partial_event_is_marked_recoverable_and_successfully_retried(self, mock_s3_active):
        """
        Phase 2.4.2 - Test 1: When a recording session finishes partially,
        it is marked as a recoverable failure (_retryable=True, _terminal_failure=False),
        and a subsequent retry successfully claims ownership, skips verified files,
        transfers missing files, and marks the event PROCESSED.
        """
        # Event with 2 recording files: 1 MP4 and 1 M4A
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p242_partial_retry:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                'object': {
                    'id': '2421',
                    'uuid': 'uuid_2421',
                    'duration': 60,
                    'recording_files': [
                        {
                            'id': 'file_2421_video',
                            'recording_type': 'shared_screen_with_speaker_view',
                            'file_type': 'MP4',
                            'file_size': 3000000,
                            'download_url': 'https://zoom.us/f2421_video'
                        },
                        {
                            'id': 'file_2421_audio',
                            'recording_type': 'audio_only',
                            'file_type': 'M4A',
                            'file_size': 1000000,
                            'download_url': 'https://zoom.us/f2421_audio'
                        }
                    ]
                }
            },
            status=ZoomWebhookEvent.Status.RECEIVED
        )

        streamed_files = []
        def mock_stream_first_run(download_url, **kwargs):
            s3_key = kwargs.get('s3_key', '')
            streamed_files.append(s3_key)
            if 'file_2421_audio' in s3_key or 'audio_only' in s3_key:
                raise RuntimeError("Simulated transient connection reset during audio upload")
            return True, 3000000

        def mock_verify(s3_bucket=None, s3_key=None, expected_file_size=None, **kwargs):
            return True, {'ContentLength': expected_file_size or 3000000}

        # First run: Video succeeds, audio fails -> triggers Celery retry
        with patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', side_effect=mock_stream_first_run):
            with patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', side_effect=mock_verify):
                with patch.object(process_zoom_recording_completed, 'retry', side_effect=RuntimeError("Celery retry triggered")):
                    with self.assertRaises(RuntimeError):
                        process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-initial', throw=True)

        # Assert post-run 1:
        session = LiveClassRecording.objects.get(zoom_uuid='uuid_2421')
        self.assertEqual(session.status, LiveClassRecording.Status.PARTIAL, "Session must be PARTIAL")
        self.assertIsNotNone(session.completed_at)

        ev.refresh_from_db()
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.FAILED)
        payload1 = ev.payload or {}
        self.assertTrue(payload1.get('_retryable'), "Event must be flagged as _retryable=True")
        self.assertFalse(payload1.get('_terminal_failure'), "Event must NOT be flagged as _terminal_failure")
        self.assertEqual(payload1.get('_failure_reason'), 'partial_recording')

        # Video file was verified, audio file was not
        self.assertTrue(session.files.filter(zoom_file_id='file_2421_video', is_verified=True).exists())
        self.assertFalse(session.files.filter(zoom_file_id='file_2421_audio', is_verified=True).exists())

        # Second run (Retry): Audio now succeeds!
        retry_streamed_files = []
        def mock_stream_retry(download_url, **kwargs):
            s3_key = kwargs.get('s3_key', '')
            retry_streamed_files.append(s3_key)
            return True, kwargs.get('expected_file_size') or 1000000

        with patch('courses.services.recording.ZoomRecordingService.stream_zoom_recording_to_s3', side_effect=mock_stream_retry):
            with patch('courses.services.recording.ZoomRecordingService.verify_s3_upload', side_effect=mock_verify):
                process_zoom_recording_completed.apply(args=[ev.id], task_id='worker-retry', throw=True)

        # Assert post-retry:
        # 1. Video file was SKIPPED; only audio file was streamed!
        self.assertEqual(len(retry_streamed_files), 1)
        self.assertTrue(any('file_2421_audio' in f or 'audio_only' in f for f in retry_streamed_files))

        # 2. Both files are verified now
        session.refresh_from_db()
        self.assertEqual(session.status, LiveClassRecording.Status.COMPLETED, "Session must now be COMPLETED")
        self.assertEqual(session.files.filter(is_verified=True).count(), 2)

        # 3. Webhook event reached PROCESSED
        ev.refresh_from_db()
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.PROCESSED)
        self.assertEqual(ev.payload.get('_task_id'), 'worker-retry', "Ownership must be claimed by retry worker")

    @patch('courses.services.recording.ZoomRecordingService.is_s3_active', return_value=True)
    def test_watchdog_recovers_retryable_failure_and_skips_terminal_failure(self, mock_s3_active):
        """
        Phase 2.4.2 - Test 2: Watchdog scans FAILED events, successfully recovering
        retryable events while safely ignoring terminal failures.
        """
        now = timezone.now()
        three_hours_ago = now - timedelta(hours=3)

        # Event A: Retryable failure (partial recording from 3 hours ago)
        ev_retryable = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p242_watchdog_retryable:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                '_retryable': True,
                '_terminal_failure': False,
                '_failure_reason': 'partial_recording',
                '_recovery_attempts': 0,
            },
            status=ZoomWebhookEvent.Status.FAILED
        )
        ZoomWebhookEvent.objects.filter(id=ev_retryable.id).update(received_at=three_hours_ago)

        # Event B: Terminal failure (live_class_not_found from 3 hours ago)
        ev_terminal = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p242_watchdog_terminal:2',
            event_type='recording.completed',
            payload={
                '_retryable': False,
                '_terminal_failure': True,
                '_failure_reason': 'live_class_not_found',
            },
            status=ZoomWebhookEvent.Status.FAILED
        )
        ZoomWebhookEvent.objects.filter(id=ev_terminal.id).update(received_at=three_hours_ago)

        # Event C: Watchdog max retries exhausted
        ev_max_retries = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p242_watchdog_max:3',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                '_retryable': True,
                '_recovery_attempts': 3,
            },
            status=ZoomWebhookEvent.Status.FAILED
        )
        ZoomWebhookEvent.objects.filter(id=ev_max_retries.id).update(received_at=three_hours_ago)

        dispatched_ids = []
        with patch.object(process_zoom_recording_completed, 'delay', side_effect=lambda ev_id: dispatched_ids.append(ev_id)):
            recovered_count = recover_stale_zoom_recordings(stale_hours=2, max_retries=3)

        # Assertions:
        # 1. Exactly 1 event was recovered (ev_retryable)
        self.assertEqual(recovered_count, 1)
        self.assertIn(ev_retryable.id, dispatched_ids)

        # 2. ev_retryable was claimed and transitioned to PROCESSING
        ev_retryable.refresh_from_db()
        self.assertEqual(ev_retryable.status, ZoomWebhookEvent.Status.PROCESSING)
        self.assertEqual(ev_retryable.payload.get('_recovery_attempts'), 1)
        self.assertIsNotNone(ev_retryable.payload.get('_worker_lease_expires_at'))

        # 3. ev_terminal was SKIPPED and remains FAILED
        ev_terminal.refresh_from_db()
        self.assertEqual(ev_terminal.status, ZoomWebhookEvent.Status.FAILED)
        self.assertNotIn(ev_terminal.id, dispatched_ids)

        # 4. ev_max_retries was marked as terminal failure and abandoned
        ev_max_retries.refresh_from_db()
        self.assertEqual(ev_max_retries.status, ZoomWebhookEvent.Status.FAILED)
        self.assertTrue(ev_max_retries.payload.get('_terminal_failure'))
        self.assertFalse(ev_max_retries.payload.get('_retryable'))
        self.assertEqual(ev_max_retries.payload.get('_failure_reason'), 'watchdog_max_retries_exceeded')

    def test_duplicate_retry_dispatch_does_not_create_concurrent_ownership(self):
        """
        Phase 2.4.2 - Test 3: If a duplicate retry task is dispatched while an existing
        worker holds an active unexpired lease, the duplicate task aborts immediately
        without altering _task_id or database status.
        """
        ev = ZoomWebhookEvent.objects.create(
            event_id='zoom:recording.completed:p242_dup_fencing:1',
            event_type='recording.completed',
            live_class=self.live_class,
            payload={
                '_task_id': 'active-worker-task-1',
                '_worker_lease_expires_at': (timezone.now() + timedelta(minutes=10)).isoformat(),
                '_heartbeat_stage': 'worker_started',
            },
            status=ZoomWebhookEvent.Status.PROCESSING
        )

        session = LiveClassRecording.objects.create(
            live_class=self.live_class,
            zoom_uuid='uuid_dup_fencing',
            zoom_meeting_id='dup123',
            session_index=1,
            status=LiveClassRecording.Status.TRANSFERRING,
            file_count=1
        )

        # Duplicate task arrives with task_id = 'duplicate-worker-task-2'
        process_zoom_recording_completed.apply(args=[ev.id], task_id='duplicate-worker-task-2')

        # Assert: Duplicate task did NOT steal ownership
        ev.refresh_from_db()
        self.assertEqual(ev.payload.get('_task_id'), 'active-worker-task-1', "Ownership must remain with active worker")
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.PROCESSING)

        session.refresh_from_db()
        self.assertEqual(session.status, LiveClassRecording.Status.TRANSFERRING, "Session status must be unchanged")

    # =========================================================================
    # Phase 2.4.4: Prevent Terminal Failure Reopening on Webhook Redelivery
    # =========================================================================
    def test_terminal_failed_event_remains_terminal_after_duplicate_webhook_redelivery(self):
        """
        Phase 2.4.4 - Test 1: A terminal FAILED event (e.g. live_class_not_found)
        must NOT be reopened or re-dispatched when Zoom redelivers the webhook.
        Its terminal flags and failure reason must be completely preserved.
        """
        uuid_str = "ZoomUUID_Terminal_P244=="
        event_ts = "1731999111"
        event_id = f"zoom:recording.completed:{uuid_str}:{event_ts}"

        raw_payload = {
            "event": "recording.completed",
            "event_ts": int(event_ts),
            "payload": {
                "object": {
                    "id": 999999,
                    "uuid": uuid_str,
                    "recording_files": []
                }
            }
        }
        raw_body = json.dumps(raw_payload)
        secret = getattr(settings, 'ZOOM_WEBHOOK_SECRET_TOKEN', 'test_zoom_webhook_secret_12345')
        ts = str(int(timezone.now().timestamp()))
        message = f"v0:{ts}:{raw_body}".encode('utf-8')
        sig = 'v0=' + hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()

        # Create existing terminal failure
        ev = ZoomWebhookEvent.objects.create(
            event_id=event_id,
            event_type='recording.completed',
            payload={
                **raw_payload,
                '_terminal_failure': True,
                '_retryable': False,
                '_failure_reason': 'live_class_not_found',
            },
            status=ZoomWebhookEvent.Status.FAILED,
            error_message="Could not find LiveClass matching Zoom meeting"
        )

        with patch('courses.tasks.process_zoom_recording_completed.delay') as mock_delay:
            resp = self.client.post(
                '/api/courses/zoom/webhook/',
                data=raw_body,
                content_type='application/json',
                HTTP_X_ZM_SIGNATURE=sig,
                HTTP_X_ZM_REQUEST_TIMESTAMP=ts
            )

        # Assert: Safe duplicate response, NO Celery task dispatched
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data.get('status'), 'terminal_failure_ignored')
        self.assertEqual(resp.data.get('reason'), 'live_class_not_found')
        mock_delay.assert_not_called()

        # Assert: Database event remains terminal
        ev.refresh_from_db()
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.FAILED)
        self.assertTrue(ev.payload.get('_terminal_failure'))
        self.assertFalse(ev.payload.get('_retryable'))
        self.assertEqual(ev.payload.get('_failure_reason'), 'live_class_not_found')
        self.assertEqual(ev.error_message, "Could not find LiveClass matching Zoom meeting")

    def test_retryable_failed_event_is_successfully_redispatched_on_webhook_redelivery(self):
        """
        Phase 2.4.4 - Test 2: A retryable FAILED event (_retryable=True, _terminal_failure=False)
        IS claimed and redispatched to Celery when Zoom redelivers the webhook.
        """
        uuid_str = "ZoomUUID_Retryable_P244=="
        event_ts = "1731999222"
        event_id = f"zoom:recording.completed:{uuid_str}:{event_ts}"

        raw_payload = {
            "event": "recording.completed",
            "event_ts": int(event_ts),
            "payload": {
                "object": {
                    "id": 888888,
                    "uuid": uuid_str,
                    "recording_files": []
                }
            }
        }
        raw_body = json.dumps(raw_payload)
        secret = getattr(settings, 'ZOOM_WEBHOOK_SECRET_TOKEN', 'test_zoom_webhook_secret_12345')
        ts = str(int(timezone.now().timestamp()))
        message = f"v0:{ts}:{raw_body}".encode('utf-8')
        sig = 'v0=' + hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()

        # Create existing retryable failure
        ev = ZoomWebhookEvent.objects.create(
            event_id=event_id,
            event_type='recording.completed',
            payload={
                **raw_payload,
                '_terminal_failure': False,
                '_retryable': True,
                '_failure_reason': 'partial_recording',
            },
            status=ZoomWebhookEvent.Status.FAILED,
            error_message="Partial recording not complete"
        )

        with patch('courses.tasks.process_zoom_recording_completed.delay') as mock_delay:
            resp = self.client.post(
                '/api/courses/zoom/webhook/',
                data=raw_body,
                content_type='application/json',
                HTTP_X_ZM_SIGNATURE=sig,
                HTTP_X_ZM_REQUEST_TIMESTAMP=ts
            )

        # Assert: Claimed and queued
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data.get('status'), 'queued')
        self.assertTrue(resp.data.get('is_redelivery'))
        mock_delay.assert_called_once_with(ev.id)

        # Assert: Database event transitioned to PROCESSING
        ev.refresh_from_db()
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.PROCESSING)
        self.assertIsNotNone(ev.payload.get('_worker_lease_expires_at'))

    def test_processed_event_remains_immutable_on_webhook_redelivery(self):
        """
        Phase 2.4.4 - Test 3: A PROCESSED event is completely ignored on duplicate redelivery,
        with zero Celery dispatches and zero status mutations.
        """
        uuid_str = "ZoomUUID_Processed_P244=="
        event_ts = "1731999333"
        event_id = f"zoom:recording.completed:{uuid_str}:{event_ts}"

        raw_payload = {
            "event": "recording.completed",
            "event_ts": int(event_ts),
            "payload": {
                "object": {
                    "id": 777777,
                    "uuid": uuid_str,
                    "recording_files": []
                }
            }
        }
        raw_body = json.dumps(raw_payload)
        secret = getattr(settings, 'ZOOM_WEBHOOK_SECRET_TOKEN', 'test_zoom_webhook_secret_12345')
        ts = str(int(timezone.now().timestamp()))
        message = f"v0:{ts}:{raw_body}".encode('utf-8')
        sig = 'v0=' + hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()

        # Create existing PROCESSED event
        ev = ZoomWebhookEvent.objects.create(
            event_id=event_id,
            event_type='recording.completed',
            payload=raw_payload,
            status=ZoomWebhookEvent.Status.PROCESSED,
            processed_at=timezone.now()
        )

        with patch('courses.tasks.process_zoom_recording_completed.delay') as mock_delay:
            resp = self.client.post(
                '/api/courses/zoom/webhook/',
                data=raw_body,
                content_type='application/json',
                HTTP_X_ZM_SIGNATURE=sig,
                HTTP_X_ZM_REQUEST_TIMESTAMP=ts
            )

        # Assert: duplicate_ignored
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data.get('status'), 'duplicate_ignored')
        mock_delay.assert_not_called()

        # Assert: Status remains PROCESSED
        ev.refresh_from_db()
        self.assertEqual(ev.status, ZoomWebhookEvent.Status.PROCESSED)
