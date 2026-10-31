import os
import logging
import requests
from celery import shared_task
from celery.exceptions import MaxRetriesExceededError
from django.conf import settings
from django.db import transaction
from courses.models import VideoLesson, TranslatedAudio
from courses.services.ai_translator import generate_dubbed_audio

logger = logging.getLogger(__name__)

@shared_task(
    bind=True,
    max_retries=5,
    default_retry_delay=60,
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True
)
def generate_dubbed_audio_task(self, lesson_id, target_languages=None):
    from openai import APIConnectionError, APITimeoutError, RateLimitError

    task_id = self.request.id
    retry_attempt = self.request.retries

    logger.info(f"Task started: task_id={task_id}, lesson_id={lesson_id}, target_languages={target_languages}, stage=init")

    if target_languages is None:
        target_languages = ['hi', 'ta', 'ml']

    # Pre-flight credential check to fail permanently if missing
    google_key = getattr(settings, 'GOOGLE_API_KEY', '')
    openai_key = getattr(settings, 'OPENAI_API_KEY', '')

    try:
        # Check if timed_transcript is NOT provided, in which case OpenAI key is required
        try:
            lesson = VideoLesson.objects.get(id=lesson_id)
            has_timed = bool(lesson.timed_transcript and lesson.timed_transcript.strip())
        except VideoLesson.DoesNotExist:
            has_timed = False

        if not google_key:
            raise ValueError("GOOGLE_API_KEY is not configured in settings.")
        if not has_timed and not openai_key:
            raise ValueError("OPENAI_API_KEY is not configured in settings, required for Whisper transcription.")

        logger.info(f"Triggering processing: task_id={task_id}, lesson_id={lesson_id}, target_languages={target_languages}, stage=processing")
        # Trigger processing
        generate_dubbed_audio(lesson_id, target_languages=target_languages)
        logger.info(f"Task completed successfully: task_id={task_id}, lesson_id={lesson_id}, target_languages={target_languages}, stage=completed")

    except MemoryError as e:
        logger.error(f"MemoryError (OOM): task_id={task_id}, lesson_id={lesson_id}, target_languages={target_languages}, retry_attempt={retry_attempt}, failure_type=MemoryError, error={str(e)}")
        for lang in target_languages:
            try:
                audio_obj = TranslatedAudio.objects.get(lesson_id=lesson_id, language_code=lang)
                if audio_obj.status == 'processing':
                    audio_obj.status = 'failed'
                    audio_obj.save()
            except TranslatedAudio.DoesNotExist:
                pass
        raise e

    except (requests.RequestException, APIConnectionError, APITimeoutError, RateLimitError) as e:
        logger.warning(f"Transient error: task_id={task_id}, lesson_id={lesson_id}, target_languages={target_languages}, retry_attempt={retry_attempt}, failure_type=Transient, error={str(e)}. Retrying task...")
        try:
            raise self.retry(exc=e)
        except Exception as retry_exc:
            # Re-raise retry exception to let Celery handle it
            raise retry_exc

    except requests.HTTPError as e:
        # Retry only transient HTTP error status codes
        if e.response is not None and e.response.status_code in [408, 429, 500, 502, 503, 504]:
            logger.warning(f"Transient HTTP error {e.response.status_code}: task_id={task_id}, lesson_id={lesson_id}, target_languages={target_languages}, retry_attempt={retry_attempt}, failure_type=HTTP_Transient. Retrying task...")
            raise self.retry(exc=e)
        else:
            # Permanent HTTP error
            logger.error(f"Permanent HTTP error: task_id={task_id}, lesson_id={lesson_id}, target_languages={target_languages}, retry_attempt={retry_attempt}, failure_type=HTTP_Permanent, error={str(e)}")
            for lang in target_languages:
                try:
                    audio_obj = TranslatedAudio.objects.get(lesson_id=lesson_id, language_code=lang)
                    if audio_obj.status == 'processing':
                        audio_obj.status = 'failed'
                        audio_obj.save()
                except TranslatedAudio.DoesNotExist:
                    pass
            raise e

    except Exception as e:
        import subprocess
        # Do not retry timeouts
        if isinstance(e, subprocess.TimeoutExpired):
            logger.error(f"Subprocess Timeout: task_id={task_id}, lesson_id={lesson_id}, target_languages={target_languages}, retry_attempt={retry_attempt}, failure_type=TimeoutExpired, error={str(e)}")
        else:
            logger.error(f"Permanent error: task_id={task_id}, lesson_id={lesson_id}, target_languages={target_languages}, retry_attempt={retry_attempt}, failure_type=Exception, error={str(e)}")

        for lang in target_languages:
            try:
                audio_obj = TranslatedAudio.objects.get(lesson_id=lesson_id, language_code=lang)
                if audio_obj.status == 'processing':
                    audio_obj.status = 'failed'
                    audio_obj.save()
            except TranslatedAudio.DoesNotExist:
                pass
        raise e

REMINDER_LABELS = {
    '24h': '24 hours',
    '1h': '1 hour',
    '15m': '15 minutes',
}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_class_reminder(self, class_id, expected_start_timestamp, reminder_type='1h'):
    """
    Phase 2: generalized to send any of the 24h/1h/15m reminders via the
    SAME task and the SAME NotificationService used since Phase 0/1 -- no
    second notification system. `reminder_type` defaults to '1h' so any
    Celery message already queued before this change deployed (with only
    the original 2 positional args) still calls correctly.
    """
    from courses.models import LiveClass
    from notifications.services import NotificationService
    from notifications.models import NotificationType
    from django.utils import timezone
    import datetime

    try:
        live_class = LiveClass.objects.get(id=class_id)
    except LiveClass.DoesNotExist:
        logger.info(f"Reminder skipped: LiveClass {class_id} does not exist.")
        return

    if live_class.status != LiveClass.ClassStatus.SCHEDULED:
        logger.info(f"Reminder skipped: LiveClass {class_id} status is {live_class.status}.")
        return

    # Compare expected start timestamp with current scheduled start
    current_start_timestamp = int(live_class.scheduled_start.timestamp())
    if current_start_timestamp != expected_start_timestamp:
        logger.info(f"Reminder skipped: LiveClass {class_id} was rescheduled from {expected_start_timestamp} to {current_start_timestamp}.")
        return

    if live_class.scheduled_start <= timezone.now():
        logger.info(f"Reminder skipped: LiveClass {class_id} is already in the past.")
        return

    label = REMINDER_LABELS.get(reminder_type, reminder_type)

    # Check batch assignment and dispatch
    from courses.models import LiveBatchStudent
    students = LiveBatchStudent.objects.filter(
        batch=live_class.batch,
        student__is_active=True
    ).select_related('student')

    for lbs in students:
        student = lbs.student
        try:
            NotificationService.create_notification(
                recipient=student,
                title=f"Reminder: {live_class.title} starts in {label}",
                body=f"Your live class for {live_class.course.title} will start at {live_class.scheduled_start.strftime('%H:%M')}.",
                notification_type=NotificationType.LIVE_CLASS,
                action_url=f"/courses/{live_class.course.id}/live",
                idempotency_key=f"liveclass:{live_class.id}:reminder:{reminder_type}:{student.id}"
            )
        except Exception as e:
            logger.error(f"Failed to create reminder for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)


@shared_task(
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=6,
    default_retry_delay=60,
    retry_backoff=True,
    retry_backoff_max=1800,
    retry_jitter=True,
    time_limit=21600,
    soft_time_limit=20700,
)
def process_zoom_recording_completed(self, webhook_event_id):
    """
    Celery task that processes a 'recording.completed' Zoom webhook event.
    - Matches the LiveClass via Zoom meeting UUID and/or meeting ID.
    - Creates or updates a LiveClassRecording session.
    - Streams all recording files (MP4, M4A, VTT, etc.) to AWS S3 using bounded-memory multipart upload.
    - Verifies S3 uploads via head_object and file size.
    - Tracks per-file idempotency (skips already-verified files).
    - Updates LiveClass.recording_url (legacy compatibility) and status upon primary video verification.
    - Dispatches notifications to enrolled students.
    """
    from django.utils import timezone
    from courses.models import (
        LiveClass,
        LiveClassRecording,
        LiveClassRecordingFile,
        ZoomWebhookEvent,
    )
    from courses.services.recording import ZoomRecordingService, WorkerOwnershipLostError

    import uuid
    from django.db import OperationalError
    task_id = self.request.id or str(uuid.uuid4())
    retry_attempt = self.request.retries
    logger.info(f"Task started: task_id={task_id}, webhook_event_id={webhook_event_id}, retry_attempt={retry_attempt}")

    with transaction.atomic():
        locked_event = ZoomWebhookEvent.objects.select_for_update().filter(id=webhook_event_id).first()

        if not locked_event:
            logger.error(f"ZoomWebhookEvent #{webhook_event_id} does not exist. Aborting task.")
            return

        if locked_event.status == ZoomWebhookEvent.Status.PROCESSED:
            logger.info(f"ZoomWebhookEvent #{webhook_event_id} is already PROCESSED. Skipping.")
            return

        import datetime
        now_time = timezone.now()
        ev_payload = dict(locked_event.payload) if isinstance(locked_event.payload, dict) else {}

        # Terminal failure check: abort immediately if marked as non-retryable terminal failure
        if locked_event.status == ZoomWebhookEvent.Status.FAILED and ev_payload.get('_terminal_failure') is True:
            logger.warning(
                f"[ZoomTask] ZoomWebhookEvent #{webhook_event_id} is marked as terminal failure "
                f"({ev_payload.get('_failure_reason', locked_event.error_message)}). Aborting task."
            )
            return

        existing_lease = ev_payload.get('_worker_lease_expires_at')
        existing_task_id = ev_payload.get('_task_id')

        # If another worker has a valid active lease, abort redundant task immediately
        if existing_lease and existing_task_id and existing_task_id != task_id:
            try:
                lease_dt = datetime.datetime.fromisoformat(existing_lease)
                if timezone.is_naive(lease_dt):
                    lease_dt = timezone.make_aware(lease_dt)
                if now_time < lease_dt:
                    logger.warning(
                        f"[ZoomTask] Event #{webhook_event_id} already has active worker {existing_task_id} "
                        f"with valid lease until {lease_dt.isoformat()}. Aborting redundant task {task_id}."
                    )
                    return
            except Exception:
                pass

        lease_expires = (now_time + datetime.timedelta(seconds=900)).isoformat()
        ev_payload['_task_id'] = task_id
        ev_payload['_task_started_at'] = now_time.isoformat()
        ev_payload['_last_heartbeat_at'] = now_time.isoformat()
        ev_payload['_worker_lease_expires_at'] = lease_expires
        ev_payload['_heartbeat_stage'] = 'worker_started'
        locked_event.payload = ev_payload
        locked_event.status = ZoomWebhookEvent.Status.PROCESSING
        locked_event.save(update_fields=['status', 'payload'])
        webhook_event = locked_event

    try:
        payload = webhook_event.payload or {}
        object_data = (
            payload.get('payload', {}).get('object', {})
            or payload.get('object', {})
            or {}
        )
        meeting_uuid = object_data.get('uuid', '')
        meeting_id = str(object_data.get('id') or '')
        download_token = payload.get('download_token') or payload.get('payload', {}).get('download_token')
        recording_files = object_data.get('recording_files', [])

        # Match LiveClass
        live_class = webhook_event.live_class
        if not live_class:
            live_class = ZoomRecordingService.find_live_class_for_zoom_meeting(meeting_id, meeting_uuid)

        if not live_class:
            err_msg = f"Could not find LiveClass matching Zoom meeting ID {meeting_id} or UUID {meeting_uuid}"
            logger.warning(f"[ZoomTask] {err_msg}")
            webhook_event.status = ZoomWebhookEvent.Status.FAILED
            webhook_event.error_message = err_msg
            ev_payload = dict(webhook_event.payload) if isinstance(webhook_event.payload, dict) else {}
            ev_payload['_terminal_failure'] = True
            ev_payload['_retryable'] = False
            ev_payload['_failure_reason'] = 'live_class_not_found'
            webhook_event.payload = ev_payload
            webhook_event.save(update_fields=['status', 'error_message', 'payload'])
            return

        if webhook_event.live_class != live_class:
            webhook_event.live_class = live_class
            webhook_event.save(update_fields=['live_class'])

        lc_updates = []
        if not live_class.zoom_meeting_id and meeting_id:
            live_class.zoom_meeting_id = meeting_id
            lc_updates.append('zoom_meeting_id')
        if not live_class.zoom_uuid and meeting_uuid:
            live_class.zoom_uuid = meeting_uuid
            lc_updates.append('zoom_uuid')
        if live_class.recording_status not in (LiveClass.RecordingStatus.TRANSFERRING, LiveClass.RecordingStatus.COMPLETED):
            live_class.recording_status = LiveClass.RecordingStatus.TRANSFERRING
            lc_updates.append('recording_status')
        if lc_updates:
            live_class.save(update_fields=lc_updates)

        # Recording Session (support multiple sessions if class was restarted)
        session, created = LiveClassRecording.objects.get_or_create(
            live_class=live_class,
            zoom_uuid=meeting_uuid,
            defaults={
                'zoom_meeting_id': meeting_id,
                'session_index': live_class.recording_sessions.count() + 1,
                'status': LiveClassRecording.Status.TRANSFERRING,
                'total_size_bytes': int(object_data.get('total_size') or 0),
                'file_count': len(recording_files),
                'duration_seconds': int(object_data.get('duration', 0)) * 60,
            }
        )
        if not created and session.status != LiveClassRecording.Status.COMPLETED:
            session.status = LiveClassRecording.Status.TRANSFERRING
            session.save(update_fields=['status'])

        s3_bucket = getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or os.environ.get('AWS_STORAGE_BUCKET_NAME', '')

        for f_info in recording_files:
            zoom_file_id = f_info.get('id')
            if not zoom_file_id:
                continue

            file_size = int(f_info.get('file_size') or 0)
            download_url = f_info.get('download_url')
            if not download_url:
                rec_file, _ = LiveClassRecordingFile.objects.get_or_create(
                    zoom_file_id=zoom_file_id,
                    defaults={
                        'recording': session,
                        'live_class': live_class,
                        'file_type': LiveClassRecordingFile.FileType.OTHER,
                        'status': LiveClassRecordingFile.Status.FAILED,
                        'error_message': 'Download URL missing from Zoom webhook payload',
                    }
                )
                rec_file.status = LiveClassRecordingFile.Status.FAILED
                rec_file.error_message = 'Download URL missing from Zoom webhook payload'
                rec_file.save(update_fields=['status', 'error_message'])
                continue

            # Per-file idempotency: skip if already transferred & verified
            verified_file = LiveClassRecordingFile.objects.filter(
                zoom_file_id=zoom_file_id,
                is_verified=True
            ).first()
            if verified_file:
                logger.info(f"[ZoomTask] File {zoom_file_id} already transferred and verified. Skipping.")
                continue

            rec_type = f_info.get('recording_type', '')
            raw_ft = str(f_info.get('file_type', '')).upper()
            ext = str(f_info.get('file_extension', '')).lower() or raw_ft.lower()

            if raw_ft == 'MP4' or ext == 'mp4':
                file_type = LiveClassRecordingFile.FileType.MP4
                content_type = 'video/mp4'
            elif raw_ft == 'M4A' or ext == 'm4a':
                file_type = LiveClassRecordingFile.FileType.M4A
                content_type = 'audio/mp4'
            elif raw_ft in ('CHAT', 'TXT') or ext == 'txt':
                file_type = LiveClassRecordingFile.FileType.TXT
                content_type = 'text/plain'
            elif raw_ft in ('TRANSCRIPT', 'VTT') or ext == 'vtt':
                file_type = LiveClassRecordingFile.FileType.VTT
                content_type = 'text/vtt'
            else:
                file_type = LiveClassRecordingFile.FileType.OTHER
                content_type = 'application/octet-stream'

            s3_key = f"recordings/live-classes/{live_class.id}/sessions/{session.id}/{rec_type or 'part'}_{zoom_file_id[:8]}.{ext}"

            rec_file, _ = LiveClassRecordingFile.objects.get_or_create(
                zoom_file_id=zoom_file_id,
                defaults={
                    'recording': session,
                    'live_class': live_class,
                    'file_type': file_type,
                    'recording_type': rec_type,
                    'file_extension': ext,
                    'file_size_bytes': file_size,
                    's3_bucket': s3_bucket,
                    's3_key': s3_key,
                    'status': LiveClassRecordingFile.Status.TRANSFERRING,
                }
            )
            rec_file.status = LiveClassRecordingFile.Status.TRANSFERRING
            rec_file.s3_bucket = s3_bucket
            rec_file.s3_key = s3_key
            rec_file.save(update_fields=['status', 's3_bucket', 's3_key'])

            def _file_heartbeat(stage='in_progress', part_number=1, total_bytes=0):
                return ZoomRecordingService.update_task_heartbeat(
                    webhook_event_id=webhook_event.id,
                    task_id=task_id,
                    lease_seconds=900,
                    stage=stage,
                    bytes_transferred=total_bytes
                )

            # Pre-check ownership before starting streaming transfer
            if not ZoomRecordingService.is_task_lease_owner(webhook_event.id, task_id):
                raise WorkerOwnershipLostError(f"Task {task_id} lost ownership of event {webhook_event.id} before file {zoom_file_id}")

            # Bounded-memory streaming transfer to S3
            upload_ok, total_bytes = ZoomRecordingService.stream_zoom_recording_to_s3(
                download_url=download_url,
                download_token=download_token,
                s3_bucket=s3_bucket,
                s3_key=s3_key,
                expected_file_size=file_size,
                content_type=content_type,
                heartbeat_callback=_file_heartbeat
            )
            if not upload_ok:
                raise RuntimeError(f"Streaming upload failed for file {zoom_file_id}")

            # Verify S3 upload with head_object and file size
            is_verified, head_meta = ZoomRecordingService.verify_s3_upload(
                s3_bucket=s3_bucket,
                s3_key=s3_key,
                expected_file_size=file_size
            )
            if not is_verified:
                rec_file.status = LiveClassRecordingFile.Status.FAILED
                rec_file.error_message = "S3 head_object verification failed: object missing or size mismatch"
                rec_file.save(update_fields=['status', 'error_message'])
                raise RuntimeError(f"S3 verification failed for file {zoom_file_id}")

            rec_file.is_verified = True
            rec_file.status = LiveClassRecordingFile.Status.VERIFIED
            rec_file.verified_at = timezone.now()
            rec_file.save(update_fields=['is_verified', 'status', 'verified_at'])
            logger.info(f"[ZoomTask] File {zoom_file_id} successfully verified in S3.")

        # Determine session status
        expected_files = max(session.file_count or 0, len(recording_files), session.files.count())
        verified_files = session.files.filter(is_verified=True).count()
        is_completed = (verified_files >= expected_files and expected_files > 0)
        target_session_status = LiveClassRecording.Status.COMPLETED if is_completed else (
            LiveClassRecording.Status.PARTIAL if verified_files > 0 else LiveClassRecording.Status.FAILED
        )

        # Fenced transaction: Atomically verify worker ownership under row lock on ZoomWebhookEvent
        # BEFORE mutating LiveClassRecording, LiveClass, or ZoomWebhookEvent.
        # This prevents superseded workers from modifying session status, completion timestamps,
        # primary recording URLs, or recording status after losing ownership.
        with transaction.atomic():
            locked_event = ZoomWebhookEvent.objects.select_for_update().filter(id=webhook_event.id).first()

            if not locked_event or locked_event.status != ZoomWebhookEvent.Status.PROCESSING:
                logger.warning(
                    f"[ZoomTask] Task {task_id} lost ownership: event #{webhook_event.id} is not PROCESSING. "
                    f"Aborting database writes."
                )
                raise WorkerOwnershipLostError(f"Task {task_id} lost ownership: event #{webhook_event.id} is not PROCESSING")

            curr_payload = dict(locked_event.payload) if isinstance(locked_event.payload, dict) else {}
            if curr_payload.get('_task_id') != task_id:
                logger.warning(
                    f"[ZoomTask] Task {task_id} lost ownership of event #{webhook_event.id} "
                    f"(current owner is {curr_payload.get('_task_id')}). "
                    f"Aborting database writes."
                )
                raise WorkerOwnershipLostError(f"Task {task_id} lost ownership of event #{webhook_event.id}")

            # 1. Update session status under lock
            session.status = target_session_status
            session.completed_at = timezone.now()
            session.save(update_fields=['status', 'completed_at'])

            if is_completed:
                # 2. Update LiveClass primary recording (legacy compatibility) under lock
                primary_file = (
                    session.files.filter(is_verified=True, recording_type='shared_screen_with_speaker_view').first()
                    or session.files.filter(is_verified=True, file_type=LiveClassRecordingFile.FileType.MP4).first()
                )
                if primary_file:
                    region = getattr(settings, 'AWS_S3_REGION_NAME', 'ap-south-1') or 'ap-south-1'
                    bucket = primary_file.s3_bucket or getattr(settings, 'AWS_STORAGE_BUCKET_NAME', '') or os.environ.get('AWS_STORAGE_BUCKET_NAME', '') or 'natya-recordings'
                    canonical_s3_url = f"https://{bucket}.s3.{region}.amazonaws.com/{primary_file.s3_key}"

                    locked_live_class = LiveClass.objects.select_for_update().get(id=live_class.id)

                    current_url = (locked_live_class.recording_url or '').strip()

                    should_set_primary = False
                    if not current_url:
                        should_set_primary = True
                    elif primary_file.s3_key in current_url:
                        should_set_primary = True
                    else:
                        new_duration = int(session.duration_seconds or 0)
                        other_sessions = locked_live_class.recording_sessions.filter(
                            files__is_verified=True
                        ).exclude(id=session.id)

                        matched_existing = None
                        for osess in other_sessions:
                            for ofile in osess.files.all():
                                if ofile.s3_key and ofile.s3_key in current_url:
                                    matched_existing = osess
                                    break
                            if matched_existing:
                                break

                        existing_duration = int((matched_existing or other_sessions.order_by('-duration_seconds').first() or session).duration_seconds or 0)

                        # Rules:
                        # 1. Shorter recordings cannot overwrite longer verified recordings.
                        # 2. If new session has missing duration (new_duration <= 0), preserve existing.
                        # 3. If existing has missing duration (existing_duration <= 0) and new has valid duration (>0), new replaces existing.
                        # 4. If new_duration > existing_duration, new replaces existing.
                        if new_duration > 0 and (existing_duration <= 0 or new_duration > existing_duration):
                            logger.info(
                                f"[ZoomTask] Replacing LiveClass #{locked_live_class.id} recording with longer session "
                                f"(new={new_duration}s > existing={existing_duration}s)."
                            )
                            should_set_primary = True
                        else:
                            logger.info(
                                f"[ZoomTask] Preserving existing LiveClass #{locked_live_class.id} recording. "
                                f"Current session ({new_duration}s) is not longer than existing ({existing_duration}s)."
                            )
                            should_set_primary = False

                    if should_set_primary:
                        locked_live_class.recording_url = canonical_s3_url
                        locked_live_class.recording_uploaded_at = primary_file.verified_at or timezone.now()
                        locked_live_class.recording_status = LiveClass.RecordingStatus.COMPLETED
                        if locked_live_class.status != LiveClass.ClassStatus.COMPLETED:
                            locked_live_class.status = LiveClass.ClassStatus.COMPLETED
                        locked_live_class.save(update_fields=['recording_url', 'recording_uploaded_at', 'recording_status', 'status'])
                        logger.info(f"[ZoomTask] LiveClass #{locked_live_class.id} primary recording set to {canonical_s3_url}")
                    else:
                        if locked_live_class.recording_status != LiveClass.RecordingStatus.COMPLETED:
                            locked_live_class.recording_status = LiveClass.RecordingStatus.COMPLETED
                            locked_live_class.save(update_fields=['recording_status'])

                    # Update local live_class reference for subsequent notification dispatch
                    live_class = locked_live_class

                # 3. Mark webhook event as PROCESSED under strict ownership fencing
                locked_event.status = ZoomWebhookEvent.Status.PROCESSED
                locked_event.processed_at = timezone.now()
                locked_event.error_message = ''
                curr_payload['_worker_lease_expires_at'] = None
                curr_payload['_heartbeat_stage'] = 'completed'
                locked_event.payload = curr_payload
                locked_event.save(update_fields=['status', 'processed_at', 'error_message', 'payload'])

        # Partial session guard: Do NOT mark event PROCESSED if session is PARTIAL or FAILED
        if not is_completed:
            err_msg = (
                f"Recording session #{session.id} for LiveClass #{live_class.id} is not fully completed "
                f"(status={session.status}, verified={verified_files}/{expected_files})."
            )
            logger.warning(f"[ZoomTask] {err_msg} Raising error to trigger Celery retry for missing files.")
            raise RuntimeError(err_msg)

        # Student notifications on recording ready
        try:
            from notifications.services import NotificationService
            from notifications.models import NotificationType
            recipients = set()
            if live_class.batch:
                for lbs in live_class.batch.students.filter(student__is_active=True).select_related('student'):
                    if lbs.student:
                        recipients.add(lbs.student)
            if not recipients and live_class.course:
                for enr in live_class.course.enrollments.select_related('user'):
                    if enr.user:
                        recipients.add(enr.user)

            for student in recipients:
                NotificationService.create_notification(
                    recipient=student,
                    title=f"Recording available: {live_class.title}",
                    body=f"The recording for your live class in {live_class.course.title if live_class.course else 'your course'} is now available to watch.",
                    notification_type=NotificationType.LIVE_CLASS,
                    action_url="/live-classes",
                    idempotency_key=f"liveclass:{live_class.id}:recording:{student.id}"
                )
        except Exception as notif_err:
            logger.warning(f"[ZoomTask] Notification dispatch error for class {live_class.id}: {notif_err}")

        logger.info(f"[ZoomTask] Successfully finished processing recording for LiveClass #{live_class.id}")

    except Exception as exc:
        if isinstance(exc, WorkerOwnershipLostError):
            logger.warning(
                f"[ZoomTask] Task {task_id} lost ownership of webhook event {webhook_event_id}. "
                f"Terminating immediately without modifying database status."
            )
            return

        logger.exception(f"[ZoomTask] Error processing webhook event {webhook_event_id}: {exc}")
        try:
            with transaction.atomic():
                locked_event = ZoomWebhookEvent.objects.select_for_update().filter(id=webhook_event_id).first()

                if locked_event and locked_event.status == ZoomWebhookEvent.Status.PROCESSING:
                    curr_payload = dict(locked_event.payload) if isinstance(locked_event.payload, dict) else {}
                    if curr_payload.get('_task_id') == task_id:
                        if 'session' in locals() and session:
                            try:
                                verified_count = session.files.filter(is_verified=True).count()
                                if verified_count > 0 and session.status != LiveClassRecording.Status.COMPLETED:
                                    session.status = LiveClassRecording.Status.PARTIAL
                                    session.completed_at = timezone.now()
                                    session.save(update_fields=['status', 'completed_at'])
                            except Exception:
                                pass
                        curr_payload['_worker_lease_expires_at'] = None
                        curr_payload['_heartbeat_stage'] = 'failed'
                        curr_payload['_retryable'] = True
                        curr_payload['_terminal_failure'] = False
                        is_partial = False
                        if 'session' in locals() and session:
                            try:
                                is_partial = (session.files.filter(is_verified=True).count() > 0)
                            except Exception:
                                pass
                        curr_payload['_failure_reason'] = (
                            'partial_recording' if (is_partial or 'not fully completed' in str(exc)) else 'task_exception'
                        )
                        locked_event.payload = curr_payload
                        locked_event.status = ZoomWebhookEvent.Status.FAILED
                        locked_event.error_message = str(exc)
                        locked_event.save(update_fields=['status', 'error_message', 'payload'])
        except Exception:
            pass

        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            logger.error(
                f"[ZoomTask] Max retries exceeded for webhook event {webhook_event_id}. Marking as terminal failure."
            )
            try:
                with transaction.atomic():
                    term_event = ZoomWebhookEvent.objects.select_for_update().filter(id=webhook_event_id).first()
                    if term_event:
                        term_payload = dict(term_event.payload) if isinstance(term_event.payload, dict) else {}
                        term_payload['_terminal_failure'] = True
                        term_payload['_retryable'] = False
                        term_payload['_failure_reason'] = 'celery_max_retries_exceeded'
                        term_event.payload = term_payload
                        term_event.status = ZoomWebhookEvent.Status.FAILED
                        term_event.error_message = f"Max retries exceeded: {exc}"
                        term_event.save(update_fields=['status', 'error_message', 'payload'])
            except Exception:
                pass
            raise


@shared_task(
    bind=True,
    name='courses.tasks.recover_stale_zoom_recordings',
    acks_late=True,
    max_retries=0,
)
def recover_stale_zoom_recordings(self, stale_hours=2, max_retries=3, stale_received_minutes=15):
    """
    Watchdog periodic task running on Celery Beat.
    Identifies Zoom webhook events stuck in RECEIVED or PROCESSING where the worker
    is no longer actively processing or has died.
    Recovers uncompleted recording transfers by re-dispatching them to process_zoom_recording_completed.

    Guarantees:
    - Never reprocesses PROCESSED or IGNORED events.
    - Active-task protection: Never redispatches an event while a valid worker lease is active
      (_worker_lease_expires_at > now), even if the event was received 3-5 hours ago.
    - Heartbeat awareness: If an active heartbeat occurred recently, protects the running task.
    - Worker crash recovery: If a worker dies during a transfer, heartbeats cease and the 15-minute
      worker lease expires, allowing the watchdog to safely recover it.
    - Uses database transactions with select_for_update() row locking to prevent concurrent watchdog dispatch.
    - Tracks recovery attempts in payload['_recovery_attempts'] with bounded retries (default max 3).
    - If max retries are exceeded, marks the event as FAILED with an explanatory error_message.
    - Dispatches to Celery only AFTER committing the database transaction (never holds lock across network call).
    - Sanitized logging (no secret tokens or sensitive payloads).
    """
    import datetime
    from django.utils import timezone
    from courses.models import ZoomWebhookEvent

    now = timezone.now()
    threshold_time = now - datetime.timedelta(hours=stale_hours)
    logger.info(f"[Watchdog] Running stale Zoom recording recovery scan (stale_hours={stale_hours}, cutoff={threshold_time.isoformat()}).")

    candidate_event_ids = list(
        ZoomWebhookEvent.objects.filter(
            event_type='recording.completed',
            status__in=[
                ZoomWebhookEvent.Status.RECEIVED,
                ZoomWebhookEvent.Status.PROCESSING,
                ZoomWebhookEvent.Status.FAILED,
            ],
            received_at__lte=threshold_time
        ).values_list('id', flat=True)
    )

    if not candidate_event_ids:
        logger.info("[Watchdog] No stale Zoom recording webhook events found.")
        return 0

    logger.info(f"[Watchdog] Found {len(candidate_event_ids)} candidate event(s). Inspecting under lock.")
    recovered_count = 0

    for ev_id in candidate_event_ids:
        claimed_event_id = None
        event_code = None

        with transaction.atomic():
            locked_event = ZoomWebhookEvent.objects.select_for_update().filter(id=ev_id).first()
            if not locked_event:
                continue

            # Skip if already processed or ignored
            if locked_event.status in (ZoomWebhookEvent.Status.PROCESSED, ZoomWebhookEvent.Status.IGNORED):
                continue

            # Must still have arrived before threshold_time
            if locked_event.received_at > threshold_time:
                continue

            payload = dict(locked_event.payload) if isinstance(locked_event.payload, dict) else {}

            # If FAILED, check if it is a terminal failure (must NOT be recovered)
            if locked_event.status == ZoomWebhookEvent.Status.FAILED:
                if (
                    payload.get('_terminal_failure') is True
                    or payload.get('_retryable') is False
                    or payload.get('_failure_reason') in (
                        'live_class_not_found',
                        'watchdog_max_retries_exceeded',
                        'celery_max_retries_exceeded',
                        'malformed_payload'
                    )
                ):
                    logger.info(
                        f"[Watchdog] Event #{locked_event.id} ({locked_event.event_id}) is marked as terminal failure "
                        f"({payload.get('_failure_reason', locked_event.error_message)}). Skipping recovery."
                    )
                    continue

            # ACTIVE-TASK PROTECTION: Check worker lease
            lease_expires_str = payload.get('_worker_lease_expires_at')
            if lease_expires_str:
                try:
                    lease_expires_dt = datetime.datetime.fromisoformat(lease_expires_str)
                    if timezone.is_naive(lease_expires_dt):
                        lease_expires_dt = timezone.make_aware(lease_expires_dt)
                    if now < lease_expires_dt:
                        logger.info(
                            f"[Watchdog] Event #{locked_event.id} ({locked_event.event_id}) has active worker lease "
                            f"until {lease_expires_dt.isoformat()}. Active task protected; skipping recovery."
                        )
                        continue
                except Exception as parse_err:
                    logger.warning(f"[Watchdog] Error parsing lease timestamp for event #{locked_event.id}: {parse_err}")

            # Also check last heartbeat timestamp
            last_heartbeat_str = payload.get('_last_heartbeat_at')
            if last_heartbeat_str:
                try:
                    last_heartbeat_dt = datetime.datetime.fromisoformat(last_heartbeat_str)
                    if timezone.is_naive(last_heartbeat_dt):
                        last_heartbeat_dt = timezone.make_aware(last_heartbeat_dt)
                    if now - last_heartbeat_dt < datetime.timedelta(minutes=15):
                        logger.info(
                            f"[Watchdog] Event #{locked_event.id} had recent heartbeat at "
                            f"{last_heartbeat_dt.isoformat()}. Active task protected; skipping recovery."
                        )
                        continue
                except Exception as parse_err:
                    logger.warning(f"[Watchdog] Error parsing heartbeat timestamp for event #{locked_event.id}: {parse_err}")

            # Deduplication among watchdog workers: check recent dispatch
            last_dispatch_str = payload.get('_last_watchdog_dispatch')
            if last_dispatch_str:
                try:
                    last_dispatch_dt = datetime.datetime.fromisoformat(last_dispatch_str)
                    if timezone.is_naive(last_dispatch_dt):
                        last_dispatch_dt = timezone.make_aware(last_dispatch_dt)
                    if now - last_dispatch_dt < datetime.timedelta(minutes=30):
                        logger.info(
                            f"[Watchdog] Event #{locked_event.id} was already dispatched by watchdog at "
                            f"{last_dispatch_dt.isoformat()}. Skipping duplicate dispatch."
                        )
                        continue
                except Exception:
                    pass

            attempts = int(payload.get('_recovery_attempts', 0))
            if attempts >= max_retries:
                locked_event.status = ZoomWebhookEvent.Status.FAILED
                locked_event.error_message = (
                    f"Watchdog: abandoned after {attempts} recovery attempts without successful completion."
                )
                payload['_worker_lease_expires_at'] = None
                payload['_terminal_failure'] = True
                payload['_retryable'] = False
                payload['_failure_reason'] = 'watchdog_max_retries_exceeded'
                locked_event.payload = payload
                locked_event.save(update_fields=['status', 'error_message', 'payload'])
                logger.warning(
                    f"[Watchdog] Event #{locked_event.id} ({locked_event.event_id}) exceeded max retries "
                    f"({attempts}/{max_retries}). Marked FAILED (terminal)."
                )
                continue

            # Claim the event for recovery
            payload['_recovery_attempts'] = attempts + 1
            payload['_last_watchdog_dispatch'] = now.isoformat()
            payload['_worker_lease_expires_at'] = (now + datetime.timedelta(seconds=900)).isoformat()
            payload['_heartbeat_stage'] = 'watchdog_redispatched'
            locked_event.payload = payload
            locked_event.status = ZoomWebhookEvent.Status.PROCESSING
            locked_event.error_message = f"Watchdog recovery attempt {attempts + 1}/{max_retries} queued."
            locked_event.save(update_fields=['payload', 'status', 'error_message'])

            claimed_event_id = locked_event.id
            event_code = locked_event.event_id

        # Dispatch Celery task strictly OUTSIDE the database transaction
        if claimed_event_id:
            try:
                process_zoom_recording_completed.delay(claimed_event_id)
                recovered_count += 1
                logger.info(f"[Watchdog] Dispatched recovery task for event #{claimed_event_id} ({event_code}).")
            except Exception as dispatch_err:
                logger.error(
                    f"[Watchdog] Failed to dispatch Celery recovery task for event #{claimed_event_id}: {dispatch_err}",
                    exc_info=True
                )
                # Atomic compensation: revert event to RECEIVED, clear lease, do not burn attempt
                try:
                    with transaction.atomic():
                        revert_event = ZoomWebhookEvent.objects.select_for_update().filter(id=claimed_event_id).first()

                        if revert_event and revert_event.status == ZoomWebhookEvent.Status.PROCESSING:
                            rev_payload = dict(revert_event.payload) if isinstance(revert_event.payload, dict) else {}
                            if rev_payload.get('_heartbeat_stage') == 'watchdog_redispatched':
                                rev_payload['_worker_lease_expires_at'] = None
                                rev_payload['_heartbeat_stage'] = 'watchdog_dispatch_failed'
                                cur_attempts = int(rev_payload.get('_recovery_attempts', 1))
                                rev_payload['_recovery_attempts'] = max(0, cur_attempts - 1)
                                revert_event.payload = rev_payload
                                revert_event.status = (
                                    ZoomWebhookEvent.Status.FAILED
                                    if rev_payload.get('_retryable')
                                    else ZoomWebhookEvent.Status.RECEIVED
                                )
                                revert_event.error_message = f"Watchdog dispatch failed: {str(dispatch_err)}"
                                revert_event.save(update_fields=['status', 'error_message', 'payload'])
                                logger.info(f"[Watchdog] Compensated event #{claimed_event_id}: reverted to RECEIVED.")
                except Exception as comp_err:
                    logger.error(f"[Watchdog] Failed to compensate event #{claimed_event_id}: {comp_err}")

    logger.info(f"[Watchdog] Stale recovery scan finished. Recovered {recovered_count} event(s).")
    return recovered_count

