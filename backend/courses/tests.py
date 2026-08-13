from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.utils import timezone
from .models import Course, Module, VideoLesson, Enrollment, LessonProgress

User = get_user_model()

class LessonProgressTests(APITestCase):
    def setUp(self):
        # Create courses, modules, lessons
        self.course1 = Course.objects.create(title="Course 1", price=100.00, is_published=True)
        self.course2 = Course.objects.create(title="Course 2", price=200.00, is_published=True)

        self.module1 = Module.objects.create(course=self.course1, title="Module 1", order=1)
        self.module2 = Module.objects.create(course=self.course2, title="Module 2", order=1)

        self.lesson1 = VideoLesson.objects.create(module=self.module1, title="Lesson 1", order=1)
        self.lesson2 = VideoLesson.objects.create(module=self.module2, title="Lesson 2", order=1)

        # Create users
        self.student1 = User.objects.create_user(username="student1", password="password123")
        self.student2 = User.objects.create_user(username="student2", password="password123")
        self.superuser = User.objects.create_superuser(username="admin", password="adminpassword")

        # Enroll student1 in course1
        Enrollment.objects.create(user=self.student1, course=self.course1)
        # student2 is NOT enrolled in course1, but enrolled in course2
        Enrollment.objects.create(user=self.student2, course=self.course2)

    def test_authenticated_student_gets_sensible_default_if_no_progress_exists(self):
        self.client.force_authenticate(user=self.student1)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], None)
        self.assertEqual(response.data['lesson'], self.lesson1.id)
        self.assertEqual(response.data['last_watched_position'], 0.0)
        self.assertEqual(response.data['video_duration'], 0.0)
        self.assertEqual(response.data['progress_percentage'], 0.0)
        self.assertEqual(response.data['completed'], False)
        self.assertEqual(response.data['updated_at'], None)
        self.assertEqual(response.data['completed_at'], None)

    def test_student_without_course_access_is_denied_progress_get(self):
        self.client.force_authenticate(user=self.student2)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_without_course_access_is_denied_progress_post(self):
        self.client.force_authenticate(user=self.student2)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})
        response = self.client.post(url, {
            'last_watched_position': 10.0,
            'video_duration': 100.0
        })

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_superuser_can_access_progress_without_enrollment(self):
        self.client.force_authenticate(user=self.superuser)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_student_can_create_progress(self):
        self.client.force_authenticate(user=self.student1)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})
        response = self.client.post(url, {
            'last_watched_position': 15.5,
            'video_duration': 120.0
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(response.data['id'], None)
        self.assertEqual(response.data['lesson'], self.lesson1.id)
        self.assertEqual(response.data['last_watched_position'], 15.5)
        self.assertEqual(response.data['video_duration'], 120.0)
        self.assertEqual(response.data['progress_percentage'], (15.5 / 120.0) * 100)
        self.assertEqual(response.data['completed'], False)

        # Verify db entry was created
        self.assertTrue(LessonProgress.objects.filter(user=self.student1, lesson=self.lesson1).exists())

    def test_student_can_update_progress(self):
        # Setup existing progress
        progress = LessonProgress.objects.create(
            user=self.student1,
            lesson=self.lesson1,
            last_watched_position=5.0,
            video_duration=120.0
        )

        self.client.force_authenticate(user=self.student1)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})
        response = self.client.post(url, {
            'last_watched_position': 25.0
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], progress.id)
        self.assertEqual(response.data['last_watched_position'], 25.0)
        self.assertEqual(response.data['video_duration'], 120.0)

    def test_negative_values_are_rejected(self):
        self.client.force_authenticate(user=self.student1)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})

        response = self.client.post(url, {
            'last_watched_position': -5.0,
            'video_duration': 100.0
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        response = self.client.post(url, {
            'last_watched_position': 5.0,
            'video_duration': -100.0
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_position_greater_than_duration_is_clamped(self):
        self.client.force_authenticate(user=self.student1)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})

        response = self.client.post(url, {
            'last_watched_position': 105.0,
            'video_duration': 100.0
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['last_watched_position'], 100.0)
        self.assertEqual(response.data['progress_percentage'], 100.0)

    def test_student_cannot_access_or_modify_another_students_progress(self):
        # Create progress for student1
        progress = LessonProgress.objects.create(
            user=self.student1,
            lesson=self.lesson1,
            last_watched_position=50.0,
            video_duration=100.0
        )

        # student2 requests the endpoint for lesson1 (which student2 has NO access to anyway)
        self.client.force_authenticate(user=self.student2)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Now let's enroll student2 in course1 to check if they can access student1's progress
        Enrollment.objects.create(user=self.student2, course=self.course1)

        # student2 now requests progress for lesson1.
        # It should return a default/empty progress record for student2, NOT student1's progress!
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['id'], None)
        self.assertEqual(response.data['last_watched_position'], 0.0)

        # student2 posts progress for lesson1. It should create a new record for student2, NOT modify student1's.
        response = self.client.post(url, {
            'last_watched_position': 10.0,
            'video_duration': 100.0
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(response.data['id'], progress.id)

        # student1's record remains untouched
        progress.refresh_from_db()
        self.assertEqual(progress.last_watched_position, 50.0)

    def test_completed_lesson_sets_completed_and_completed_at(self):
        self.client.force_authenticate(user=self.student1)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})

        response = self.client.post(url, {
            'last_watched_position': 100.0,
            'video_duration': 100.0,
            'completed': True
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['completed'], True)
        self.assertIsNotNone(response.data['completed_at'])

    def test_already_completed_lesson_cannot_be_reset_to_incomplete(self):
        progress = LessonProgress.objects.create(
            user=self.student1,
            lesson=self.lesson1,
            last_watched_position=100.0,
            video_duration=100.0,
            completed=True,
            completed_at=timezone.now() - timezone.timedelta(days=1)
        )
        old_completed_at = progress.completed_at

        self.client.force_authenticate(user=self.student1)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})

        # Post request trying to set completed=False or not specifying completed
        response = self.client.post(url, {
            'last_watched_position': 10.0,
            'completed': False
        })

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['completed'], True)

        # Verify db is not changed to incomplete
        progress.refresh_from_db()
        self.assertEqual(progress.completed, True)
        self.assertEqual(progress.completed_at, old_completed_at)

    def test_uniqueness_constraint_prevents_duplicate_progress_records(self):
        # Directly create a record in DB
        progress = LessonProgress.objects.create(
            user=self.student1,
            lesson=self.lesson1,
            last_watched_position=10.0,
            video_duration=100.0
        )

        # Attempt to POST/create again. The code should update the existing record
        # rather than creating a new one, keeping count of records at 1.
        self.client.force_authenticate(user=self.student1)
        url = reverse('lesson-progress', kwargs={'pk': self.lesson1.id})

        response = self.client.post(url, {
            'last_watched_position': 20.0
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(LessonProgress.objects.filter(user=self.student1, lesson=self.lesson1).count(), 1)
