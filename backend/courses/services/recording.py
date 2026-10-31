import os
import io
import time
import re
import uuid
import hmac
import hashlib
import logging
import requests
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from django.conf import settings
from django.utils import timezone
from courses.models import LiveClass
from courses.services.zoom import ZoomService

logger = logging.getLogger(__name__)


class WorkerOwnershipLostError(Exception):
    """Raised when a Celery worker detects that it has lost ownership of the event lease."""
    pass


class ZoomRecordingService:
    """
    Handles:
    1. Zoom cloud recording webhook validation and processing.
    2. Downloading Zoom recording MP4 and uploading to AWS S3.
    3. Manual recording synchronization and direct S3 recording uploads.
    """

    @classmethod
    def get_s3_client(cls):
        client_kwargs = {
            'region_name': getattr(settings, 'AWS_S3_REGION_NAME', 'ap-south-1') or 'ap-south-1',
            'config': Config(signature_version='s3v4')
        }
        if getattr(settings, 'AWS_ACCESS_KEY_ID', None) and getattr(settings, 'AWS_SECRET_ACCESS_KEY', None):
            client_kwargs['aws_access_key_id'] = settings.AWS_ACCESS_KEY_ID
            client_kwargs['aws_secret_access_key'] = settings.AWS_SECRET_ACCESS_KEY
        return boto3.client('s3', **client_kwargs)

    @classmethod
    def is_s3_active(cls):
        bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or os.environ.get('AWS_STORAGE_BUCKET_NAME', '')
        return bool(
            bucket_name and (
                bool(getattr(settings, 'AWS_ACCESS_KEY_ID', None) and getattr(settings, 'AWS_SECRET_ACCESS_KEY', None))
                or getattr(settings, 'IS_STAGING', False)
                or getattr(settings, 'AWS_S3_ENABLED', False)
            )
        )

    @classmethod
    def verify_webhook_signature(cls, raw_body_bytes, signature_header, timestamp_header):
        """
        Verifies Zoom's HMAC-SHA256 webhook signature.
        Official Zoom format:
          message = f"v0:{timestamp}:{raw_body_utf8}"
          hash = hmac_sha256(secret, message).hexdigest()
          expected_signature = f"v0={hash}"
        Also validates that timestamp is within 300 seconds (5 minutes) of current time.
        """
        if not signature_header or not timestamp_header:
            logger.warning("[ZoomWebhook] Missing signature or timestamp header.")
            return False

        try:
            ts = float(timestamp_header)
            if ts > 1e11:
                ts = ts / 1000.0
            now = timezone.now().timestamp()
            if abs(now - ts) > 300:
                logger.warning(f"[ZoomWebhook] Timestamp skew rejected: received={ts}, now={now}")
                return False
        except (ValueError, TypeError):
            logger.warning(f"[ZoomWebhook] Invalid timestamp header: {timestamp_header}")
            return False

        webhook_secret = getattr(settings, 'ZOOM_WEBHOOK_SECRET_TOKEN', '') or os.environ.get('ZOOM_WEBHOOK_SECRET_TOKEN', '')
        if not webhook_secret:
            if getattr(settings, 'DEBUG', False):
                logger.warning("[ZoomWebhook] ZOOM_WEBHOOK_SECRET_TOKEN not set; allowing in DEBUG.")
                return True
            logger.error("[ZoomWebhook] ZOOM_WEBHOOK_SECRET_TOKEN is not configured; rejecting request.")
            return False

        if isinstance(raw_body_bytes, str):
            raw_body_bytes = raw_body_bytes.encode('utf-8')

        try:
            raw_body_str = raw_body_bytes.decode('utf-8')
        except UnicodeDecodeError:
            raw_body_str = raw_body_bytes.decode('latin-1')

        message = f"v0:{timestamp_header}:{raw_body_str}".encode('utf-8')
        computed_hash = hmac.new(webhook_secret.encode('utf-8'), message, hashlib.sha256).hexdigest()
        expected_signature = f"v0={computed_hash}"

        return hmac.compare_digest(expected_signature, signature_header)

    @classmethod
    def find_live_class_for_zoom_meeting(cls, meeting_id, meeting_uuid=''):
        """
        Finds a LiveClass by Zoom meeting UUID, numeric meeting ID, or URL substring.
        """
        from courses.models import LiveClass
        if meeting_uuid:
            lc = LiveClass.objects.filter(zoom_uuid=meeting_uuid).order_by('-scheduled_start').first()
            if lc:
                return lc
        if meeting_id:
            lc = LiveClass.objects.filter(zoom_meeting_id=meeting_id).order_by('-scheduled_start').first()
            if lc:
                return lc
            lc = LiveClass.objects.filter(meeting_url__icontains=meeting_id).order_by('-scheduled_start').first()
            if lc:
                return lc
            lc = LiveClass.objects.filter(host_url__icontains=meeting_id).order_by('-scheduled_start').first()
            if lc:
                return lc
        return None

    @classmethod
    def stream_zoom_recording_to_s3(
        cls,
        download_url,
        download_token=None,
        s3_bucket=None,
        s3_key=None,
        expected_file_size=None,
        content_type='video/mp4',
        part_size=10 * 1024 * 1024,
        heartbeat_callback=None
    ):
        """
        Streams a large Zoom recording to S3 using bounded-memory multipart upload.
        - Memory consumption is strictly bounded (fixed 10MB chunk buffer).
        - Retries individual part uploads up to 3 times before failing.
        - Automatically cleans up incomplete multipart uploads on abort/failure.
          NOTE on SIGKILL: If the host/systemd terminates the process via SIGKILL
          (e.g. systemd TimeoutStopSec during deploy), Python cleanup handlers cannot execute.
          An S3 Lifecycle rule (abort incomplete multipart uploads after 1-2 days) must be
          configured on the S3 bucket as an infrastructure safeguard.
        - Falls back to local media directory in dev if S3 is not active.
        """
        s3_bucket = s3_bucket or getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or os.environ.get('AWS_STORAGE_BUCKET_NAME', '')
        use_s3 = cls.is_s3_active()

        def _make_request(token_val):
            headers = {}
            target_url = download_url
            if token_val:
                sep = '&' if '?' in target_url else '?'
                target_url = f"{target_url}{sep}access_token={token_val}"
                headers["Authorization"] = f"Bearer {token_val}"
            return requests.get(target_url, headers=headers, stream=True, timeout=300)

        token = download_token
        if not token:
            try:
                token = ZoomService.get_access_token()
            except Exception as e:
                logger.warning(f"[ZoomRecording] Could not get OAuth token for download: {e}")

        res = _make_request(token)

        # If HTTP 401 occurs and a token was used, attempt fresh OAuth token refresh once
        if res.status_code == 401 and token:
            logger.warning("[ZoomRecording] Received HTTP 401 from Zoom download stream. Attempting fresh OAuth token refresh.")
            res.close()
            try:
                fresh_token = ZoomService.get_access_token()
            except Exception as token_err:
                logger.error(f"[ZoomRecording] Failed to acquire fresh Zoom OAuth token after 401: {token_err}")
                return False, 0

            if not fresh_token:
                logger.error("[ZoomRecording] Fresh Zoom OAuth token is empty after 401.")
                return False, 0

            token = fresh_token
            res = _make_request(token)
            if res.status_code == 401:
                logger.error("[ZoomRecording] Zoom download failed with HTTP 401 even after fresh OAuth token refresh.")
                res.close()
                return False, 0

        def _check_heartbeat(stage, part_num, bytes_count):
            if heartbeat_callback:
                try:
                    res_val = heartbeat_callback(stage=stage, part_number=part_num, total_bytes=bytes_count)
                except TypeError:
                    res_val = heartbeat_callback(part_number=part_num, total_bytes=bytes_count)
                if res_val is False:
                    logger.warning(f"[ZoomRecording] Heartbeat check failed at stage '{stage}'. Task lost ownership.")
                    raise WorkerOwnershipLostError(f"Task lost ownership during stage: {stage}")

        if not use_s3:
            local_path = os.path.join(settings.MEDIA_ROOT, s3_key)
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with res:
                if res.status_code != 200:
                    logger.error(f"[ZoomRecording] Download failed with status {res.status_code}")
                    return False, 0
                total_bytes = 0
                last_hb_time = time.time()
                last_hb_bytes = 0
                with open(local_path, 'wb') as f:
                    for chunk in res.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            f.write(chunk)
                            total_bytes += len(chunk)
                            now_t = time.time()
                            if (now_t - last_hb_time >= 30) or (total_bytes - last_hb_bytes >= 5 * 1024 * 1024):
                                _check_heartbeat(stage='downloading', part_num=1, bytes_count=total_bytes)
                                last_hb_time = now_t
                                last_hb_bytes = total_bytes
                _check_heartbeat(stage='completed_download', part_num=1, bytes_count=total_bytes)
            return True, total_bytes

        s3_client = cls.get_s3_client()
        upload_id = None

        try:
            with res:
                if res.status_code != 200:
                    logger.error(f"[ZoomRecording] Download failed from Zoom with status {res.status_code}")
                    return False, 0

                res_content_type = res.headers.get('content-type', '').lower()
                if 'html' in res_content_type:
                    logger.error("[ZoomRecording] Received HTML response instead of media stream from Zoom.")
                    return False, 0

                # Pre-check ownership before creating multipart upload
                _check_heartbeat(stage='init_multipart_upload', part_num=1, bytes_count=0)

                mp_resp = s3_client.create_multipart_upload(
                    Bucket=s3_bucket,
                    Key=s3_key,
                    ContentType=content_type
                )
                upload_id = mp_resp['UploadId']

                parts = []
                part_number = 1
                buffer = io.BytesIO()
                total_bytes = 0
                last_hb_time = time.time()
                last_hb_bytes = 0

                for chunk in res.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        buffer.write(chunk)
                        total_bytes += len(chunk)

                        # Bounded-time and bounded-bytes download heartbeat
                        now_t = time.time()
                        if (now_t - last_hb_time >= 30) or (total_bytes - last_hb_bytes >= 5 * 1024 * 1024):
                            _check_heartbeat(stage='downloading', part_num=part_number, bytes_count=total_bytes)
                            last_hb_time = now_t
                            last_hb_bytes = total_bytes

                        if buffer.tell() >= part_size:
                            buf_bytes = buffer.getvalue()
                            for attempt in range(3):
                                try:
                                    part_resp = s3_client.upload_part(
                                        Bucket=s3_bucket,
                                        Key=s3_key,
                                        PartNumber=part_number,
                                        UploadId=upload_id,
                                        Body=buf_bytes
                                    )
                                    parts.append({
                                        'PartNumber': part_number,
                                        'ETag': part_resp['ETag']
                                    })
                                    _check_heartbeat(stage=f'uploaded_part_{part_number}', part_num=part_number, bytes_count=total_bytes)
                                    last_hb_time = time.time()
                                    last_hb_bytes = total_bytes
                                    break
                                except Exception as part_err:
                                    if isinstance(part_err, WorkerOwnershipLostError):
                                        raise part_err
                                    if attempt == 2:
                                        raise part_err
                                    time.sleep(1 * (2 ** attempt))

                            buffer.close()
                            buffer = io.BytesIO()
                            part_number += 1

                # Final partial upload
                if buffer.tell() > 0:
                    buf_bytes = buffer.getvalue()
                    for attempt in range(3):
                        try:
                            part_resp = s3_client.upload_part(
                                Bucket=s3_bucket,
                                Key=s3_key,
                                PartNumber=part_number,
                                UploadId=upload_id,
                                Body=buf_bytes
                            )
                            parts.append({
                                'PartNumber': part_number,
                                'ETag': part_resp['ETag']
                            })
                            _check_heartbeat(stage=f'uploaded_part_{part_number}', part_num=part_number, bytes_count=total_bytes)
                            last_hb_time = time.time()
                            last_hb_bytes = total_bytes
                            break
                        except Exception as part_err:
                            if isinstance(part_err, WorkerOwnershipLostError):
                                raise part_err
                            if attempt == 2:
                                raise part_err
                            time.sleep(1 * (2 ** attempt))
                    buffer.close()

                if not parts:
                    raise ValueError(f"No parts uploaded for {s3_key}; stream was empty.")

                # Pre-check before completing multipart upload (FENCING)
                _check_heartbeat(stage='completing_multipart_upload', part_num=part_number, bytes_count=total_bytes)

                s3_client.complete_multipart_upload(
                    Bucket=s3_bucket,
                    Key=s3_key,
                    UploadId=upload_id,
                    MultipartUpload={'Parts': parts}
                )
                logger.info(f"[ZoomRecording] Successfully completed multipart upload {upload_id} for {s3_key} ({total_bytes} bytes).")
                return True, total_bytes

        except Exception as e:
            logger.exception(f"[ZoomRecording] Multipart upload error for {s3_key}: {e}")
            if upload_id and s3_client:
                try:
                    s3_client.abort_multipart_upload(
                        Bucket=s3_bucket,
                        Key=s3_key,
                        UploadId=upload_id
                    )
                    logger.info(f"[ZoomRecording] Aborted incomplete multipart upload {upload_id} for {s3_key}")
                except Exception as abort_err:
                    logger.error(f"[ZoomRecording] Failed to abort multipart upload {upload_id}: {abort_err}")
            raise e

    @classmethod
    def verify_s3_upload(cls, s3_bucket, s3_key, expected_file_size=None):
        """
        Verifies that an object exists in S3 and its size matches expected_file_size.
        Falls back to local filesystem check in dev when S3 is not active.
        """
        use_s3 = cls.is_s3_active()
        if use_s3:
            try:
                s3_client = cls.get_s3_client()
                head = s3_client.head_object(Bucket=s3_bucket, Key=s3_key)
                actual_size = head.get('ContentLength', 0)
                if expected_file_size is not None and expected_file_size > 0:
                    if actual_size != expected_file_size:
                        logger.error(
                            f"[ZoomRecording] S3 size mismatch for {s3_key}: "
                            f"actual={actual_size}, expected={expected_file_size}"
                        )
                        return False, head
                return True, head
            except Exception as e:
                logger.error(f"[ZoomRecording] S3 head_object verification failed for {s3_key}: {e}")
                return False, {}
        else:
            local_path = os.path.join(settings.MEDIA_ROOT, s3_key)
            if not os.path.exists(local_path):
                return False, {}
            actual_size = os.path.getsize(local_path)
            if expected_file_size is not None and expected_file_size > 0:
                if actual_size != expected_file_size:
                    return False, {'ContentLength': actual_size}
            return True, {'ContentLength': actual_size}

    @classmethod
    def is_task_lease_owner(cls, webhook_event_id, task_id):
        """
        Atomically checks whether task_id currently owns the lease for webhook_event_id.
        """
        from courses.models import ZoomWebhookEvent
        event = ZoomWebhookEvent.objects.filter(id=webhook_event_id).first()
        if not event or event.status != ZoomWebhookEvent.Status.PROCESSING:
            return False
        payload = event.payload or {}
        return payload.get('_task_id') == task_id

    @classmethod
    def update_task_heartbeat(
        cls,
        webhook_event_id,
        task_id=None,
        lease_seconds=900,
        stage='in_progress',
        bytes_transferred=None
    ):
        """
        Extends the worker lease and logs heartbeat progress for an active recording task.
        FENCING: Verifies that task_id matches the current payload['_task_id'].
        Returns True if the lease was successfully extended, or False if ownership was lost.
        """
        import datetime
        from django.utils import timezone
        from courses.models import ZoomWebhookEvent
        from django.db import transaction, OperationalError

        now = timezone.now()
        expires_at = now + datetime.timedelta(seconds=lease_seconds)

        try:
            with transaction.atomic():
                event = ZoomWebhookEvent.objects.select_for_update().filter(id=webhook_event_id).first()

                if not event or event.status != ZoomWebhookEvent.Status.PROCESSING:
                    return False

                payload = dict(event.payload) if isinstance(event.payload, dict) else {}
                current_task_id = payload.get('_task_id')
                if current_task_id and task_id and current_task_id != task_id:
                    logger.warning(
                        f"[ZoomRecording] Heartbeat rejected: task {task_id} is not the current lease owner "
                        f"(current owner is {current_task_id})."
                    )
                    return False

                if task_id:
                    payload['_task_id'] = task_id
                payload['_last_heartbeat_at'] = now.isoformat()
                payload['_worker_lease_expires_at'] = expires_at.isoformat()
                if stage:
                    payload['_heartbeat_stage'] = stage
                if bytes_transferred is not None:
                    payload['_bytes_transferred'] = bytes_transferred

                event.payload = payload
                event.save(update_fields=['payload'])
                return True
        except Exception as e:
            logger.warning(f"[ZoomRecording] Heartbeat lock acquisition failed: {e}")
            return False

    @classmethod
    def upload_file_to_s3(cls, file_stream, s3_key, content_type='video/mp4'):
        """
        Uploads an opened file-like object or stream to AWS S3.
        Falls back to local media directory in dev if S3 is not configured.
        """
        bucket_name = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or os.environ.get('AWS_STORAGE_BUCKET_NAME', '')
        region = getattr(settings, 'AWS_S3_REGION_NAME', '') or os.environ.get('AWS_S3_REGION_NAME', 'ap-south-1') or 'ap-south-1'
        use_s3 = bool(bucket_name and (bool(getattr(settings, 'AWS_ACCESS_KEY_ID', None) and getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)) or getattr(settings, 'IS_STAGING', False) or getattr(settings, 'AWS_S3_ENABLED', False)))

        if use_s3:
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
        use_s3 = bool(bucket_name and (bool(getattr(settings, 'AWS_ACCESS_KEY_ID', None) and getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)) or getattr(settings, 'IS_STAGING', False) or getattr(settings, 'AWS_S3_ENABLED', False)))
        if not use_s3:
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
            from django.core.exceptions import ImproperlyConfigured
            logger.error('[ZoomWebhook] ZOOM_WEBHOOK_SECRET_TOKEN is not configured; cannot validate endpoint challenge.')
            raise ImproperlyConfigured('ZOOM_WEBHOOK_SECRET_TOKEN is not configured.')

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
                    for enr in Enrollment.objects.filter(course=live_class.course).select_related('user'):
                        if enr.user:
                            students.add(enr.user)
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
    def auto_sync_live_class_recording(cls, live_class_id, max_retries=8, delay_seconds=20):
        """
        Asynchronously polls Zoom Cloud Recordings API in a background daemon thread
        and transfers the completed MP4 to AWS S3 once Zoom encoding is ready.
        Retries up to max_retries with backoff delays, allowing seamless hands-off auto-sync.
        """
        import threading
        import time

        def _worker():
            for attempt in range(1, max_retries + 1):
                try:
                    from courses.models import LiveClass
                    live_class = LiveClass.objects.filter(pk=live_class_id).first()
                    if not live_class:
                        logger.warning(f"[AutoSyncRecording] LiveClass #{live_class_id} not found.")
                        return

                    # If already synced, stop
                    if live_class.recording_url:
                        logger.info(f"[AutoSyncRecording] LiveClass #{live_class_id} already has recording_url.")
                        return

                    logger.info(f"[AutoSyncRecording] Attempt {attempt}/{max_retries} for LiveClass #{live_class_id}...")
                    success, result = cls.sync_meeting_recordings(live_class)
                    if success:
                        logger.info(f"[AutoSyncRecording] Successfully synced LiveClass #{live_class_id} to S3 on attempt {attempt}: {result}")
                        return
                    else:
                        logger.info(f"[AutoSyncRecording] Attempt {attempt} for LiveClass #{live_class_id}: {result}. Retrying in {delay_seconds}s...")
                except Exception as e:
                    logger.warning(f"[AutoSyncRecording] Exception on attempt {attempt} for LiveClass #{live_class_id}: {e}")

                if attempt < max_retries:
                    time.sleep(delay_seconds)

            logger.info(f"[AutoSyncRecording] Finished all {max_retries} attempts for LiveClass #{live_class_id}.")

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        return thread


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
