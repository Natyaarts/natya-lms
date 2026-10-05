import os
import re
import uuid
import hmac
import hashlib
import logging
import requests
import boto3
from botocore.config import Config
from django.conf import settings
from django.utils import timezone
from courses.models import LiveClass
from courses.services.zoom import ZoomService

logger = logging.getLogger(__name__)


class ZoomRecordingService:
    """
    Handles:
    1. Zoom cloud recording webhook validation and processing.
    2. Downloading Zoom recording MP4 and uploading to AWS S3.
    3. Manual recording synchronization and direct S3 recording uploads.
    """

    @classmethod
    def get_s3_client(cls):
        return boto3.client(
            's3',
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME or 'ap-south-1',
            config=Config(signature_version='s3v4')
        )

    @classmethod
    def upload_file_to_s3(cls, file_stream, s3_key, content_type='video/mp4'):
        """
        Uploads an opened file-like object or stream to AWS S3.
        Falls back to local media directory in dev if S3 is not configured.
        """
        bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or os.environ.get('AWS_STORAGE_BUCKET_NAME', '')
        region = getattr(settings, 'AWS_S3_REGION_NAME', '') or os.environ.get('AWS_S3_REGION_NAME', 'ap-south-1') or 'ap-south-1'

        if bucket_name and getattr(settings, 'AWS_ACCESS_KEY_ID', ''):
            s3_client = cls.get_s3_client()
            s3_client.upload_fileobj(
                file_stream,
                bucket_name,
                s3_key,
                ExtraArgs={
                    'ContentType': content_type
                }
            )
            return f"https://{bucket_name}.s3.{region}.amazonaws.com/{s3_key}"
        else:
            # Local dev fallback
            local_path = os.path.join(settings.MEDIA_ROOT, s3_key)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, 'wb') as f:
                if hasattr(file_stream, 'read'):
                    for chunk in iter(lambda: file_stream.read(1024 * 1024), b''):
                        f.write(chunk)
                else:
                    f.write(file_stream)
            media_url = getattr(settings, 'MEDIA_URL', '/media/')
            return f"{media_url.rstrip('/')}/{s3_key}"

    @classmethod
    def generate_presigned_download_url(cls, s3_url, expires_in=86400):
        """
        Takes an S3 URL like https://bucket.s3.region.amazonaws.com/recordings/...
        and generates a presigned GET URL so browser video players can stream it
        even if the S3 bucket is private.
        """
        import re
        bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or os.environ.get('AWS_STORAGE_BUCKET_NAME', '')
        if not bucket_name or not getattr(settings, 'AWS_ACCESS_KEY_ID', ''):
            return s3_url
        match = re.search(r'amazonaws\.com/(.+)$', s3_url)
        if not match:
            return s3_url
        s3_key = match.group(1)
        try:
            s3_client = cls.get_s3_client()
            return s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': s3_key},
                ExpiresIn=expires_in
            )
        except Exception as e:
            logger.warning(f"[ZoomRecording] Failed to generate presigned download URL for {s3_key}: {e}")
            return s3_url

    @classmethod
    def validate_zoom_webhook_challenge(cls, plain_token):
        """
        Responds to Zoom's endpoint.url_validation challenge event using HMAC-SHA256.
        """
        webhook_secret = getattr(settings, 'ZOOM_WEBHOOK_SECRET_TOKEN', '') or os.environ.get('ZOOM_WEBHOOK_SECRET_TOKEN', '')
        if not webhook_secret:
            # Fallback to client secret if specific webhook secret token is not configured
            _, _, webhook_secret = ZoomService.get_credentials()

        message = plain_token.encode('utf-8')
        secret = webhook_secret.encode('utf-8')
        encrypted_token = hmac.new(secret, message, hashlib.sha256).hexdigest()

        return {
            "plainToken": plain_token,
            "encryptedToken": encrypted_token
        }

    @classmethod
    def download_and_save_to_s3(cls, live_class_id, download_url, download_token=None):
        """
        Downloads the MP4 recording from Zoom and streams directly into AWS S3.
        Updates LiveClass.recording_url, recording_uploaded_at, and status.
        """
        try:
            live_class = LiveClass.objects.get(pk=live_class_id)
        except LiveClass.DoesNotExist:
            logger.error(f"[ZoomRecording] LiveClass #{live_class_id} not found.")
            return None

        logger.info(f"[ZoomRecording] Starting download for LiveClass #{live_class_id} from {download_url[:60]}...")

        headers = {}
        target_url = download_url

        token = download_token
        if not token:
            try:
                token = ZoomService.get_access_token()
            except Exception as e:
                logger.warning(f"[ZoomRecording] Could not get OAuth token for download: {e}")

        if token:
            separator = '&' if '?' in target_url else '?'
            target_url = f"{target_url}{separator}access_token={token}"
            headers["Authorization"] = f"Bearer {token}"

        import tempfile
        tmp_path = None
        s3_url = None

        try:
            # Stream download into a temporary file to avoid partial/corrupt uploads
            with requests.get(target_url, headers=headers, stream=True, timeout=300) as res:
                if res.status_code != 200:
                    logger.error(f"[ZoomRecording] Download failed with status {res.status_code}: {res.text[:200]}")
                    return None

                content_type = res.headers.get('content-type', '').lower()
                if 'html' in content_type:
                    logger.error(f"[ZoomRecording] Received HTML response instead of video stream from Zoom.")
                    return None

                with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tmp_file:
                    tmp_path = tmp_file.name
                    for chunk in res.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            tmp_file.write(chunk)

            file_size = os.path.getsize(tmp_path)
            if file_size < 1024:
                logger.error(f"[ZoomRecording] Downloaded file is too small ({file_size} bytes). Not a valid video.")
                return None

            s3_key = f"recordings/live-classes/{live_class.id}/recording_{uuid.uuid4().hex[:8]}.mp4"
            with open(tmp_path, 'rb') as f:
                s3_url = cls.upload_file_to_s3(f, s3_key, content_type='video/mp4')

            if not s3_url:
                logger.error(f"[ZoomRecording] Failed to upload recording to S3 for LiveClass #{live_class_id}.")
                return None

            live_class.recording_url = s3_url
            live_class.recording_uploaded_at = timezone.now()
            if live_class.status != LiveClass.ClassStatus.COMPLETED:
                live_class.status = LiveClass.ClassStatus.COMPLETED
            live_class.save(update_fields=['recording_url', 'recording_uploaded_at', 'status'])

            # Automatically populate attendance for enrolled students who did not attend as ABSENT (0 min)
            try:
                from courses.models import Attendance, Enrollment
                students = set()
                if live_class.batch:
                    for lbs in live_class.batch.students.select_related('student'):
                        if lbs.student:
                            students.add(lbs.student)
                if not students and live_class.course:
                    for enr in Enrollment.objects.filter(course=live_class.course).select_related('student'):
                        if enr.student:
                            students.add(enr.student)
                instructor = live_class.instructor or (live_class.batch.instructor if live_class.batch else None)
                for st in students:
                    Attendance.objects.get_or_create(
                        live_class=live_class,
                        student=st,
                        defaults={
                            'status': Attendance.Status.ABSENT,
                            'duration_minutes': 0,
                            'notes': 'Automatically marked ABSENT on session completion',
                            'marked_by': instructor
                        }
                    )
            except Exception as att_err:
                logger.warning(f"[ZoomRecording] Auto-attendance recording failed: {att_err}")

            try:
                from notifications.services import NotificationService
                from notifications.models import NotificationType
                if live_class.batch:
                    for lbs in live_class.batch.students.filter(student__is_active=True).select_related('student'):
                        try:
                            NotificationService.create_notification(
                                recipient=lbs.student,
                                title=f"Recording available: {live_class.title}",
                                body=f"The recording for your live class in {live_class.course.title if live_class.course else 'your course'} is now available to watch.",
                                notification_type=NotificationType.LIVE_CLASS,
                                action_url="/live-classes",
                                idempotency_key=f"liveclass:{live_class.id}:recording:{lbs.student.id}"
                            )
                        except Exception:
                            pass
            except Exception as notif_err:
                logger.warning(f"[ZoomRecording] Notification dispatch failed: {notif_err}")

            logger.info(f"[ZoomRecording] Successfully saved recording for LiveClass #{live_class_id} to S3: {s3_url}")
            return s3_url

        except Exception as e:
            logger.exception(f"[ZoomRecording] Error streaming recording to S3 for LiveClass #{live_class_id}: {e}")
            return None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    @classmethod
    def handle_recording_completed_webhook(cls, payload):
        """
        Processes 'recording.completed' webhook event from Zoom.
        Finds matching LiveClass and triggers background download to S3.
        """
        meeting_obj = payload.get('object', {})
        meeting_id = str(meeting_obj.get('id') or '')
        meeting_uuid = meeting_obj.get('uuid', '')
        download_token = payload.get('download_token')

        recording_files = meeting_obj.get('recording_files', [])
        logger.info(f"[ZoomWebhook] Received recording.completed for meeting {meeting_id}, {len(recording_files)} files.")

        # Find MP4 video file
        mp4_file = None
        for f in recording_files:
            file_type = str(f.get('file_type', '')).upper()
            file_extension = str(f.get('file_extension', '')).upper()
            if file_type == 'MP4' or file_extension == 'MP4':
                mp4_file = f
                # Prefer shared_screen_with_speaker_view if multiple
                if f.get('recording_type') == 'shared_screen_with_speaker_view':
                    break

        if not mp4_file or not mp4_file.get('download_url'):
            logger.warning(f"[ZoomWebhook] No MP4 download URL in recording_files for meeting {meeting_id}")
            return False

        # Match LiveClass in DB
        live_class = None
        if meeting_id:
            live_class = LiveClass.objects.filter(meeting_url__icontains=meeting_id).order_by('-scheduled_start').first()
            if not live_class:
                live_class = LiveClass.objects.filter(host_url__icontains=meeting_id).order_by('-scheduled_start').first()

        if not live_class:
            logger.warning(f"[ZoomWebhook] Could not find LiveClass matching Zoom meeting ID {meeting_id}")
            return False

        download_url = mp4_file.get('download_url')

        # Trigger download in background thread
        import threading
        t = threading.Thread(
            target=cls.download_and_save_to_s3,
            args=(live_class.id, download_url, download_token),
            daemon=True
        )
        t.start()
        return True

    @classmethod
    def sync_meeting_recordings(cls, live_class):
        """
        Manually checks Zoom Cloud Recordings API for this LiveClass and transfers to S3.
        """
        try:
            import re
            mid_match = re.search(r'/(?:j|s)/(\d+)', f"{live_class.meeting_url or ''} {live_class.host_url or ''}")
            if not mid_match:
                return False, "Could not extract Zoom meeting ID from meeting URL."

            meeting_id = mid_match.group(1)
            token = ZoomService.get_access_token()
            headers = {"Authorization": f"Bearer {token}"}

            url = f"https://api.zoom.us/v2/meetings/{meeting_id}/recordings"
            res = requests.get(url, headers=headers, timeout=15)

            if res.status_code == 404:
                return False, "No cloud recording found on Zoom for this meeting yet. If the meeting just ended, please wait 1–2 minutes for Zoom to finish encoding."
            elif res.status_code in (400, 401, 403):
                err_data = {}
                try:
                    err_data = res.json()
                except Exception:
                    pass
                if err_data.get('code') == 4711 or 'scope' in err_data.get('message', '').lower():
                    return False, "Zoom API token is missing 'recording:read:admin' scope. Please add 'Recording -> View all user recordings' in your Zoom App Marketplace app."
                if err_data.get('code') == 3301 or 'in progress' in err_data.get('message', '').lower():
                    return False, "This meeting is currently still in progress. Please click 'End Meeting for All' in Zoom, then wait 1–2 minutes for Zoom to finish encoding the video."
                return False, err_data.get('message', f'Zoom API error (HTTP {res.status_code}).')
            elif res.status_code != 200:
                return False, f"Zoom API returned status {res.status_code}"

            data = res.json()
            recording_files = data.get('recording_files', [])

            # Check if files are still processing on Zoom side
            processing = any(f.get('status') == 'processing' or not f.get('download_url') for f in recording_files if str(f.get('file_type', '')).upper() == 'MP4')
            if processing:
                return False, "Zoom is currently encoding the cloud recording. Please wait 1–2 minutes and click 'Sync to S3' again."

            mp4_file = next((f for f in recording_files if str(f.get('file_type', '')).upper() == 'MP4' and f.get('download_url')), None)

            if not mp4_file:
                return False, "Zoom returned recording metadata, but no MP4 video file is ready yet. Please wait 1–2 minutes."

            s3_url = cls.download_and_save_to_s3(live_class.id, mp4_file.get('download_url'))
            if s3_url:
                return True, s3_url
            return False, "Failed to download recording from Zoom and upload to AWS S3. Please verify AWS S3 bucket permissions."
        except Exception as e:
            logger.exception(f"Exception during sync_meeting_recordings for LiveClass #{getattr(live_class, 'id', 'unknown')}: {e}")
            return False, f"Error syncing Zoom recording: {str(e)}"

    @classmethod
    def handle_participant_webhook(cls, payload, event_type):
        """
        Processes 'meeting.participant_joined' and 'meeting.participant_left' webhooks from Zoom.
        Finds matching LiveClass and student, marking them PRESENT and recording duration.
        """
        from courses.models import LiveClass, Attendance
        from django.contrib.auth import get_user_model
        User = get_user_model()

        meeting_obj = payload.get('object', {})
        meeting_id = str(meeting_obj.get('id') or '')
        participant = meeting_obj.get('participant', {})
        email = participant.get('email') or participant.get('user_email', '')
        user_name = participant.get('user_name', '')
        duration_sec = participant.get('duration', 0)

        if not meeting_id:
            return False

        live_class = LiveClass.objects.filter(meeting_url__icontains=meeting_id).first()
        if not live_class:
            live_class = LiveClass.objects.filter(host_url__icontains=meeting_id).first()
        if not live_class:
            return False

        student = None
        if email:
            student = User.objects.filter(email__iexact=email).first()
        if not student and user_name:
            student = User.objects.filter(username__iexact=user_name).first()
        if not student:
            return False

        if student.is_staff or student.is_superuser or (live_class.batch and live_class.batch.instructor == student):
            return True

        instructor = live_class.instructor or (live_class.batch.instructor if live_class.batch else None)
        duration_min = max(int(duration_sec) // 60, 1) if duration_sec else 1
        now = timezone.now()

        att, created = Attendance.objects.get_or_create(
            live_class=live_class,
            student=student,
            defaults={
                'status': Attendance.Status.PRESENT,
                'duration_minutes': duration_min,
                'joined_at': now,
                'marked_by': instructor,
                'notes': f"Recorded via Zoom {event_type}",
            }
        )
        if not created:
            att.status = Attendance.Status.PRESENT
            if duration_min > att.duration_minutes:
                att.duration_minutes = duration_min
            if event_type == 'meeting.participant_left':
                att.left_at = now
            att.save(update_fields=['status', 'duration_minutes', 'left_at'])
        return True
