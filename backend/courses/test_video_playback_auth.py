from datetime import timedelta
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course, Module, VideoLesson, TranslatedAudio, Enrollment, CourseInstructor, VideoProtectionSetting
from orders.models import SubscriptionPlan, Subscription

User = get_user_model()


class VideoPlaybackAuthTests(APITestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="pb_super", password="password123", email="super@example.com"
        )
        self.staff_user = User.objects.create_user(
            username="pb_staff", password="password123", is_staff=True
        )
        self.teacher_instructor = User.objects.create_user(
            username="pb_teacher", password="password123", is_teacher=True
        )
        self.enrolled_student = User.objects.create_user(
            username="pb_enrolled", password="password123"
        )
        self.subscribed_student = User.objects.create_user(
            username="pb_subscribed", password="password123"
        )
        self.unauthorized_student = User.objects.create_user(
            username="pb_unauth", password="password123"
        )

        self.course = Course.objects.create(
            title="Playback Auth Test Course",
            description="Testing playback authorization",
            price=499,
            is_published=True,
            course_type=Course.CourseType.RECORDED,
        )
        self.module = Module.objects.create(course=self.course, title="Module 1")
        self.lesson = VideoLesson.objects.create(
            module=self.module,
            title="Protected Lesson 1",
            description="A protected video lesson",
            video_file="videos/lessons/sample_protected.mp4",
        )
        self.audio_hi = TranslatedAudio.objects.create(
            lesson=self.lesson,
            language_code="hi-IN",
            language_name="Hindi",
            audio_file="videos/audios/sample_hi.mp3",
            status="completed",
        )
        self.audio_ml_pending = TranslatedAudio.objects.create(
            lesson=self.lesson,
            language_code="ml-IN",
            language_name="Malayalam",
            audio_file="videos/audios/sample_ml.mp3",
            status="processing",
        )

        # Grants
        CourseInstructor.objects.create(
            course=self.course, user=self.teacher_instructor, role=CourseInstructor.InstructorRole.TEACHER
        )
        Enrollment.objects.create(user=self.enrolled_student, course=self.course)

        # Subscription grant
        self.plan = SubscriptionPlan.objects.create(
            name="Playback Test Plan",
            billing_interval="MONTHLY",
            price="499.00",
            razorpay_plan_id="plan_playback_1",
        )
        self.plan.courses.add(self.course)
        Subscription.objects.create(
            user=self.subscribed_student,
            plan=self.plan,
            status=Subscription.Status.ACTIVE,
            razorpay_subscription_id="sub_playback_1",
            current_period_start=timezone.now() - timedelta(days=5),
            current_period_end=timezone.now() + timedelta(days=25),
        )

        self.url = reverse('lesson-playback-auth', kwargs={'pk': self.lesson.id})

    def test_unauthenticated_request_rejected(self):
        """Unauthenticated user receives 401."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_unauthorized_student_rejected(self):
        """Learner without enrollment or active subscription receives 403."""
        self.client.force_authenticate(user=self.unauthorized_student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("detail", response.data)

    def test_enrolled_student_authorized(self):
        """Student with permanent enrollment receives 200 with signed playback payload."""
        self.client.force_authenticate(user=self.enrolled_student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data

        self.assertEqual(data['lesson_id'], self.lesson.id)
        self.assertEqual(data['title'], self.lesson.title)
        self.assertTrue(data['video_url'])
        self.assertEqual(data['expires_in'], 900)
        self.assertEqual(data['student_id'], self.enrolled_student.student_id)

        # Watermark payload check
        self.assertTrue(data['watermark']['enabled'])
        self.assertEqual(data['watermark']['text'], self.enrolled_student.student_id)
        self.assertTrue(data['watermark']['moving'])
        self.assertEqual(data['watermark']['interval_seconds'], 15)

        # Protection settings check
        self.assertFalse(data['protection']['download_enabled'])
        self.assertTrue(data['protection']['screen_capture_protection_enabled'])

        # Translated audio check: completed track included, pending omitted
        self.assertEqual(len(data['translated_audios']), 1)
        self.assertEqual(data['translated_audios'][0]['language_code'], 'hi-IN')
        self.assertTrue(data['translated_audios'][0]['audio_url'])

    def test_subscribed_student_authorized(self):
        """Student with active subscription plan covering course receives 200."""
        self.client.force_authenticate(user=self.subscribed_student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['student_id'], self.subscribed_student.student_id)

    def test_course_instructor_authorized(self):
        """Assigned CourseInstructor receives 200 without needing student enrollment."""
        self.client.force_authenticate(user=self.teacher_instructor)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_staff_admin_authorized(self):
        """Staff administrator receives 200."""
        self.client.force_authenticate(user=self.staff_user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superuser_authorized(self):
        """Superuser receives 200."""
        self.client.force_authenticate(user=self.superuser)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_settings_propagation(self):
        """Updating VideoProtectionSetting is immediately reflected in the playback response."""
        settings_obj = VideoProtectionSetting.get_settings()
        settings_obj.watermark_position_interval = 45
        settings_obj.moving_watermark_enabled = False
        settings_obj.save()

        self.client.force_authenticate(user=self.enrolled_student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['watermark']['interval_seconds'], 45)
        self.assertFalse(response.data['watermark']['moving'])

    def test_lesson_without_video_returns_404(self):
        """Requesting playback auth for a text/empty lesson returns 404."""
        empty_lesson = VideoLesson.objects.create(
            module=self.module, title="Text only lesson", video_file=None
        )
        url = reverse('lesson-playback-auth', kwargs={'pk': empty_lesson.id})
        self.client.force_authenticate(user=self.enrolled_student)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_s3_presigned_url_expiry_argument_is_900_seconds(self):
        """When S3 storage is enabled, generate_presigned_url is called with ExpiresIn=900."""
        from unittest import mock
        mock_s3 = mock.MagicMock()
        mock_s3.generate_presigned_url.return_value = "https://s3.example.com/signed-test-video.mp4"

        with mock.patch('boto3.client', return_value=mock_s3):
            with self.settings(
                AWS_STORAGE_BUCKET_NAME="test-secure-bucket",
                AWS_ACCESS_KEY_ID="test-key",
                AWS_SECRET_ACCESS_KEY="test-secret",
                AWS_S3_ENABLED=True,
            ):
                self.client.force_authenticate(user=self.enrolled_student)
                response = self.client.get(self.url)
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data['expires_in'], 900)

                # Verify generate_presigned_url was called with 900 seconds
                self.assertTrue(mock_s3.generate_presigned_url.called)
                for call_args in mock_s3.generate_presigned_url.call_args_list:
                    self.assertEqual(call_args.kwargs.get('ExpiresIn'), 900)
                    self.assertEqual(
                        call_args.kwargs.get('Params', {}).get('ResponseContentDisposition'),
                        'inline'
                    )
