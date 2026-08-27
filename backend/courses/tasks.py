import logging
import requests
from celery import shared_task
from django.conf import settings
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

@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def send_class_reminder(self, class_id, expected_start_timestamp):
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
                title=f"Reminder: {live_class.title} starts soon",
                body=f"Your live class for {live_class.course.title} will start at {live_class.scheduled_start.strftime('%H:%M')}.",
                notification_type=NotificationType.LIVE_CLASS,
                action_url=f"/courses/{live_class.course.id}/live",
                idempotency_key=f"liveclass:{live_class.id}:reminder:{student.id}"
            )
        except Exception as e:
            logger.error(f"Failed to create reminder for student {student.id}, class {live_class.id}: {str(e)}", exc_info=True)
