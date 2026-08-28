from django.test import TestCase, override_settings
from django.utils import timezone
from django.core.files.uploadedfile import SimpleUploadedFile
from unittest.mock import patch, MagicMock
from datetime import timedelta
import subprocess
import os

from courses.models import Course, Module, VideoLesson, TranslatedAudio
from courses.services.ai_translator import generate_dubbed_audio, parse_timed_transcript
from courses.tasks import generate_dubbed_audio_task
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

User = get_user_model()

@override_settings(
    GOOGLE_API_KEY='fake-google-key',
    OPENAI_API_KEY='fake-openai-key'
)
class AITranslationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="teacher", password="password", is_staff=True, is_superuser=True)
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        self.course = Course.objects.create(title="Test Course", course_type=Course.CourseType.RECORDED)
        self.module = Module.objects.create(course=self.course, title="Test Module", order=1)
        
        video_content = b"fake-video-content"
        self.video_file = SimpleUploadedFile("test_video.mp4", video_content, content_type="video/mp4")
        
        self.lesson = VideoLesson.objects.create(
            module=self.module,
            title="Test Lesson",
            timed_transcript="00:00:00 --> Hello world",
            video_file=self.video_file
        )
        
        self.generate_url = f"/api/courses/lessons/{self.lesson.id}/generate_ai_audio/"

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_zombie_recovery_stale(self, mock_delay):
        # Create a stale processing record
        stale_time = timezone.now() - timedelta(hours=7)
        track = TranslatedAudio.objects.create(
            lesson=self.lesson, language_code='hi', status='processing'
        )
        # Force created_at in the past
        TranslatedAudio.objects.filter(id=track.id).update(created_at=stale_time)
        
        # This should succeed and overwrite/retry because it's older than 6 hours
        response = self.client.post(self.generate_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("started in background", response.data['message'])

    def test_zombie_recovery_recent_blocked(self):
        # Create a recent processing record
        TranslatedAudio.objects.create(
            lesson=self.lesson, language_code='hi', status='processing'
        )
        # Should be blocked
        response = self.client.post(self.generate_url)
        self.assertEqual(response.status_code, 400)
        self.assertIn("already in progress", response.data['error'])

    @patch('courses.services.ai_translator.tempfile.TemporaryDirectory')
    @patch('courses.services.ai_translator.subprocess.run')
    def test_ffmpeg_timeout_handling(self, mock_run, mock_tempdir):
        # Ensure temporary directory context manager is used
        mock_tempdir_instance = MagicMock()
        mock_tempdir.return_value.__enter__.return_value = "/fake/tmp"
        
        # Simulate FFmpeg timeout
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=600)
        
        with self.assertRaises(subprocess.TimeoutExpired):
            generate_dubbed_audio(self.lesson.id, target_languages=['hi'])
            
        self.assertTrue(mock_run.called)
        
    @patch('courses.services.ai_translator.subprocess.run')
    def test_memory_safe_processing(self, mock_run):
        # Test that memory error raises exception and marks as failed
        mock_run.side_effect = MemoryError("OOM")
        
        with self.assertRaises(MemoryError):
            generate_dubbed_audio(self.lesson.id, target_languages=['hi'])

    @patch('courses.tasks.generate_dubbed_audio')
    def test_celery_task_memory_error_no_retry(self, mock_generate):
        mock_generate.side_effect = MemoryError("OOM")
        
        # Create initial processing state
        track = TranslatedAudio.objects.create(
            lesson=self.lesson, language_code='hi', status='processing'
        )
        
        with self.assertRaises(MemoryError):
            generate_dubbed_audio_task(self.lesson.id, target_languages=['hi'])
            
        # Verify it was marked as failed
        track.refresh_from_db()
        self.assertEqual(track.status, 'failed')

    @patch('courses.tasks.generate_dubbed_audio')
    def test_celery_task_timeout_no_retry(self, mock_generate):
        mock_generate.side_effect = subprocess.TimeoutExpired(cmd="ffmpeg", timeout=600)
        
        track = TranslatedAudio.objects.create(
            lesson=self.lesson, language_code='hi', status='processing'
        )
        
        with self.assertRaises(subprocess.TimeoutExpired):
            generate_dubbed_audio_task(self.lesson.id, target_languages=['hi'])
            
        track.refresh_from_db()
        self.assertEqual(track.status, 'failed')

    def test_parse_timed_transcript(self):
        transcript = "00:00:01 --> Line 1\n00:00:05 --> Line 2"
        blocks = parse_timed_transcript(transcript)
        self.assertEqual(len(blocks), 2)
        self.assertEqual(blocks[0]['text'], "Line 1")
        self.assertEqual(blocks[0]['start'], 1000)
        self.assertEqual(blocks[0]['end'], 5000)
        
        self.assertEqual(blocks[1]['text'], "Line 2")
        self.assertEqual(blocks[1]['start'], 5000)
        self.assertEqual(blocks[1]['end'], 10000)

    def test_french_configuration_exists(self):
        from courses.services.ai_translator import LANGUAGE_MAP
        self.assertIn('fr', LANGUAGE_MAP)
        self.assertEqual(LANGUAGE_MAP['fr']['translate'], 'fr')
        self.assertEqual(LANGUAGE_MAP['fr']['tts'], 'fr-FR')

    def test_german_configuration_exists(self):
        from courses.services.ai_translator import LANGUAGE_MAP
        self.assertIn('de', LANGUAGE_MAP)
        self.assertEqual(LANGUAGE_MAP['de']['translate'], 'de')
        self.assertEqual(LANGUAGE_MAP['de']['tts'], 'de-DE')

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_french_request_accepted(self, mock_delay):
        response = self.client.post(self.generate_url, {"target_languages": ["fr"]}, format='json')
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(self.lesson.id, target_languages=["fr"])

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_german_request_accepted(self, mock_delay):
        response = self.client.post(self.generate_url, {"target_languages": ["de"]}, format='json')
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(self.lesson.id, target_languages=["de"])

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_both_languages_accepted(self, mock_delay):
        response = self.client.post(self.generate_url, {"target_languages": ["fr", "de"]}, format='json')
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(self.lesson.id, target_languages=["fr", "de"])

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_default_behavior_when_no_body(self, mock_delay):
        response = self.client.post(self.generate_url)
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(self.lesson.id, target_languages=["hi", "ta", "ml"])

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_unsupported_language_returns_400(self, mock_delay):
        response = self.client.post(self.generate_url, {"target_languages": ["xx"]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported language code", response.data['error'])
        mock_delay.assert_not_called()

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_mixed_valid_invalid_languages_returns_400(self, mock_delay):
        response = self.client.post(self.generate_url, {"target_languages": ["fr", "xx"]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("Unsupported language code", response.data['error'])
        mock_delay.assert_not_called()

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_existing_languages_regression(self, mock_delay):
        response = self.client.post(self.generate_url, {"target_languages": ["hi", "ta", "ml"]}, format='json')
        self.assertEqual(response.status_code, 200)
        mock_delay.assert_called_once_with(self.lesson.id, target_languages=["hi", "ta", "ml"])

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_duplicate_processing_for_french_german(self, mock_delay):
        TranslatedAudio.objects.create(lesson=self.lesson, language_code='fr', status='processing')
        response = self.client.post(self.generate_url, {"target_languages": ["fr", "de"]}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn("already in progress", response.data['error'])
        mock_delay.assert_not_called()

    @patch('courses.tasks.generate_dubbed_audio')
    def test_celery_task_multiple_languages(self, mock_generate):
        generate_dubbed_audio_task(self.lesson.id, target_languages=['fr', 'de'])
        mock_generate.assert_called_once_with(self.lesson.id, target_languages=['fr', 'de'])

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_empty_transcript_returns_400_original_workflow(self, mock_delay):
        # Create a lesson with no transcript and no timed_transcript
        lesson_no_transcript = VideoLesson.objects.create(
            module=self.module,
            title="No Transcript Lesson",
            timed_transcript="",
            transcript="",
            video_file=self.video_file
        )
        url = f"/api/courses/lessons/{lesson_no_transcript.id}/generate_ai_audio/"

        response = self.client.post(url, format='json')

        self.assertEqual(response.status_code, 400)
        self.assertIn("Please fill in the 'Timing for Speaking' section (or a transcript)", response.data['error'])
        mock_delay.assert_not_called()
