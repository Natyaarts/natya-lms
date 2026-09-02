import requests
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from django.utils import timezone
from .models import Course, Module, VideoLesson, Enrollment, LessonProgress, LiveClass, TranslatedAudio

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


class LiveClassModelTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="student_live_test", password="password123")
        self.instructor = User.objects.create_user(username="instructor_live_test", password="password123")
        self.course = Course.objects.create(title="Tabla Basics", price=500.00, is_published=True)
        self.now = timezone.now()

    def test_live_class_creation_and_fields(self):
        live_class = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Class 1: Intro to Tabla",
            description="First session description",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/123456789"
        )
        self.assertEqual(live_class.course, self.course)
        self.assertEqual(live_class.instructor, self.instructor)
        self.assertEqual(live_class.title, "Class 1: Intro to Tabla")
        self.assertEqual(live_class.description, "First session description")
        self.assertEqual(live_class.scheduled_start, self.now)
        self.assertEqual(live_class.duration_minutes, 60)
        self.assertEqual(live_class.meeting_provider, "ZOOM")
        self.assertEqual(live_class.meeting_url, "https://zoom.us/j/123456789")
        self.assertEqual(live_class.status, "SCHEDULED")

    def test_validation_rejects_zero_duration(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            LiveClass.objects.create(
                course=self.course,
                instructor=self.instructor,
                title="Class with 0 duration",
                scheduled_start=self.now,
                duration_minutes=0,
                meeting_url="https://zoom.us/j/123456789"
            )

    def test_validation_rejects_negative_duration(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            LiveClass.objects.create(
                course=self.course,
                instructor=self.instructor,
                title="Class with negative duration",
                scheduled_start=self.now,
                duration_minutes=-30,
                meeting_url="https://zoom.us/j/123456789"
            )

    def test___str___representation(self):
        live_class = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Class 1: Intro to Tabla",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_url="https://zoom.us/j/123456789"
        )
        expected_str = f"Class 1: Intro to Tabla - Tabla Basics ({self.now})"
        self.assertEqual(str(live_class), expected_str)

    def test_ordering_by_scheduled_start_ascending(self):
        class1 = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Later Class",
            scheduled_start=self.now + timezone.timedelta(hours=2),
            duration_minutes=60,
            meeting_url="https://zoom.us/j/123456789"
        )
        class2 = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Earlier Class",
            scheduled_start=self.now + timezone.timedelta(hours=1),
            duration_minutes=60,
            meeting_url="https://zoom.us/j/123456789"
        )
        classes = list(LiveClass.objects.filter(course=self.course))
        self.assertEqual(classes, [class2, class1])

    def test_delete_course_cascades_live_class(self):
        live_class = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Class 1",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_url="https://zoom.us/j/123456789"
        )
        self.course.delete()
        self.assertFalse(LiveClass.objects.filter(id=live_class.id).exists())

    def test_delete_instructor_sets_null(self):
        live_class = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Class 1",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_url="https://zoom.us/j/123456789"
        )
        self.instructor.delete()
        live_class.refresh_from_db()
        self.assertIsNone(live_class.instructor)


class LiveClassAPITests(APITestCase):
    def setUp(self):
        self.student_enrolled = User.objects.create_user(username="student_enrolled", password="password123")
        self.student_enrolled.is_student = True
        self.student_enrolled.save()

        self.student_other = User.objects.create_user(username="student_other", password="password123")
        self.student_other.is_student = True
        self.student_other.save()

        self.teacher1 = User.objects.create_user(username="teacher1", password="password123")
        self.teacher1.is_teacher = True
        self.teacher1.save()

        self.teacher2 = User.objects.create_user(username="teacher2", password="password123")
        self.teacher2.is_teacher = True
        self.teacher2.save()

        self.admin = User.objects.create_superuser(username="admin_user", password="adminpassword")

        # Must be LIVE course type for LiveBatch/LiveClass
        self.course1 = Course.objects.create(title="Violin Basics", price=300.00, course_type=Course.CourseType.LIVE, is_published=True)
        self.course2 = Course.objects.create(title="Tabla Advance", price=600.00, course_type=Course.CourseType.LIVE, is_published=True)

        Enrollment.objects.create(user=self.student_enrolled, course=self.course1)

        # Create batches
        self.batch1 = LiveBatch.objects.create(
            course=self.course1,
            instructor=self.teacher1,
            batch_type=LiveBatch.BatchType.GROUP
        )
        self.batch2 = LiveBatch.objects.create(
            course=self.course2,
            instructor=self.teacher2,
            batch_type=LiveBatch.BatchType.GROUP
        )

        # Assign student_enrolled to batch1
        LiveBatchStudent.objects.create(batch=self.batch1, student=self.student_enrolled)

        self.now = timezone.now()

        self.live_class = LiveClass.objects.create(
            batch=self.batch1,
            course=self.course1,
            instructor=self.teacher1,
            title="Sitar 101 Intro",
            scheduled_start=self.now + timezone.timedelta(days=1),
            duration_minutes=45,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/999"
        )

    def test_unauthenticated_user_denied(self):
        url = reverse('live-class-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_enrolled_student_can_list_and_retrieve(self):
        self.client.force_authenticate(user=self.student_enrolled)
        url_list = reverse('live-class-list')
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.live_class.id)

        url_detail = reverse('live-class-detail', kwargs={'pk': self.live_class.pk})
        response = self.client.get(url_detail)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['meeting_url'], "https://zoom.us/j/999")

    def test_non_enrolled_student_cannot_retrieve(self):
        self.client.force_authenticate(user=self.student_other)
        url_detail = reverse('live-class-detail', kwargs={'pk': self.live_class.pk})
        response = self.client.get(url_detail)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_non_enrolled_student_list_is_empty(self):
        self.client.force_authenticate(user=self.student_other)
        url_list = reverse('live-class-list')
        response = self.client.get(url_list)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 0)

    def test_student_cannot_perform_write_actions(self):
        self.client.force_authenticate(user=self.student_enrolled)

        # Create
        url_list = reverse('live-class-list')
        response = self.client.post(url_list, {
            "batch": self.batch1.id,
            "course": self.course1.id,
            "title": "Violent Violin",
            "scheduled_start": self.now,
            "duration_minutes": 60,
            "meeting_url": "https://meet.google.com"
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Update
        url_detail = reverse('live-class-detail', kwargs={'pk': self.live_class.pk})
        response = self.client.patch(url_detail, {"title": "New Title"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Delete
        response = self.client.delete(url_detail)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Start
        response = self.client.post(reverse('live-class-start', kwargs={'pk': self.live_class.pk}))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_has_full_crud_and_status_access(self):
        self.client.force_authenticate(user=self.admin)
        url_list = reverse('live-class-list')
        response = self.client.post(url_list, {
            "batch": self.batch2.id,
            "title": "Admin Created Class",
            "scheduled_start": self.now + timezone.timedelta(hours=1),
            "duration_minutes": 30,
            "meeting_url": "https://zoom.us/j/admin"
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        new_class_id = response.data['id']

        # Update
        url_detail = reverse('live-class-detail', kwargs={'pk': new_class_id})
        response = self.client.patch(url_detail, {"title": "Admin Updated Title"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Start
        response = self.client.post(reverse('live-class-start', kwargs={'pk': new_class_id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], "LIVE")

        # End
        response = self.client.post(reverse('live-class-end', kwargs={'pk': new_class_id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], "COMPLETED")

        # Delete
        response = self.client.delete(url_detail)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_teacher_segmentation(self):
        # teacher1 is instructor of self.live_class
        self.client.force_authenticate(user=self.teacher1)
        url_detail = reverse('live-class-detail', kwargs={'pk': self.live_class.pk})

        response = self.client.patch(url_detail, {"title": "Teacher 1 Updated"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # teacher2 is NOT instructor of self.live_class, so they get 404
        self.client.force_authenticate(user=self.teacher2)
        response = self.client.patch(url_detail, {"title": "Teacher 2 Attempt"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_teacher_cannot_assign_another_teacher(self):
        self.client.force_authenticate(user=self.teacher1)
        url_list = reverse('live-class-list')

        response = self.client.post(url_list, {
            "batch": self.batch1.id,
            "course": self.course1.id,
            "instructor": self.teacher2.id,
            "title": "Teacher trying to assign teacher2",
            "scheduled_start": self.now,
            "duration_minutes": 60,
            "meeting_url": "https://meet.google.com"
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("instructor", response.data)

    def test_validation_constraints(self):
        self.client.force_authenticate(user=self.admin)
        url_list = reverse('live-class-list')

        # Zero duration
        response = self.client.post(url_list, {
            "batch": self.batch1.id,
            "course": self.course1.id,
            "title": "Class 0",
            "scheduled_start": self.now,
            "duration_minutes": 0,
            "meeting_url": "https://zoom.us"
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Invalid meeting URL
        response = self.client.post(url_list, {
            "batch": self.batch1.id,
            "course": self.course1.id,
            "title": "Class Invalid URL",
            "scheduled_start": self.now,
            "duration_minutes": 30,
            "meeting_url": "invalid-url-format"
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_state_transitions(self):
        self.client.force_authenticate(user=self.admin)

        # Test transition SCHEDULED -> LIVE
        url_start = reverse('live-class-start', kwargs={'pk': self.live_class.pk})
        response = self.client.post(url_start)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Test transition LIVE -> COMPLETED
        url_end = reverse('live-class-end', kwargs={'pk': self.live_class.pk})
        response = self.client.post(url_end)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Test invalid transition completed -> live
        response = self.client.post(url_start)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upcoming_and_history_endpoints(self):
        # Clean current classes
        LiveClass.objects.all().delete()

        # Create one upcoming class
        upcoming_class = LiveClass.objects.create(
            batch=self.batch1,
            course=self.course1,
            instructor=self.teacher1,
            title="Upcoming Sitar",
            scheduled_start=self.now + timezone.timedelta(days=1),
            duration_minutes=45,
            meeting_url="https://zoom.us/j/999"
        )
        # Create one live class (should be included in upcoming)
        live_class = LiveClass.objects.create(
            batch=self.batch1,
            course=self.course1,
            instructor=self.teacher1,
            title="Live Sitar",
            scheduled_start=self.now - timezone.timedelta(hours=1),
            duration_minutes=45,
            status=LiveClass.ClassStatus.LIVE,
            meeting_url="https://zoom.us/j/111"
        )
        # Create one completed class
        completed_class = LiveClass.objects.create(
            batch=self.batch1,
            course=self.course1,
            instructor=self.teacher1,
            title="Past Completed Sitar",
            scheduled_start=self.now - timezone.timedelta(days=2),
            duration_minutes=45,
            status=LiveClass.ClassStatus.COMPLETED,
            meeting_url="https://zoom.us/j/222"
        )

        self.client.force_authenticate(user=self.student_enrolled)

        # Test upcoming
        response = self.client.get(reverse('live-class-upcoming'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]['id'], live_class.id)
        self.assertEqual(results[1]['id'], upcoming_class.id)

        # Test history
        response = self.client.get(reverse('live-class-history'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], completed_class.id)


from .serializers import CourseSerializer, LiveBatchSerializer, LiveBatchStudentSerializer
from .models import LiveBatch, LiveBatchStudent
from django.contrib import admin

class CourseTypeTests(APITestCase):
    def setUp(self):
        self.admin_user = User.objects.create_user(username="admin_course_type", password="password123")
        self.admin_user.is_superuser = True
        self.admin_user.is_staff = True
        self.admin_user.save()

    def test_new_course_defaults_to_recorded(self):
        course = Course.objects.create(title="Default Course", description="Test default")
        self.assertEqual(course.course_type, Course.CourseType.RECORDED)

    def test_live_course_can_be_created(self):
        course = Course.objects.create(
            title="Live Course",
            description="Test Live",
            course_type=Course.CourseType.LIVE
        )
        self.assertEqual(course.course_type, "LIVE")

    def test_recorded_course_can_be_created(self):
        course = Course.objects.create(
            title="Recorded Course",
            description="Test Recorded",
            course_type=Course.CourseType.RECORDED
        )
        self.assertEqual(course.course_type, "RECORDED")

    def test_invalid_course_type_rejected(self):
        from django.core.exceptions import ValidationError
        course = Course(
            title="Invalid Type",
            description="Test invalid",
            course_type="INVALID_TYPE"
        )
        with self.assertRaises(ValidationError):
            course.full_clean()

    def test_serializer_exposes_course_type(self):
        course = Course.objects.create(
            title="Serializer Course",
            description="Serializer desc",
            course_type=Course.CourseType.LIVE
        )
        serializer = CourseSerializer(course)
        self.assertEqual(serializer.data['course_type'], "LIVE")

    def test_serializer_creation_and_update(self):
        self.client.force_authenticate(user=self.admin_user)
        url = reverse('course-list')

        # Test creation with LIVE
        response = self.client.post(url, {
            "title": "Violin Course API",
            "description": "API description",
            "price": "100.00",
            "course_type": "LIVE"
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['course_type'], "LIVE")
        course_id = response.data['id']

        # Test update with RECORDED
        detail_url = reverse('course-detail', kwargs={'pk': course_id})
        response = self.client.patch(detail_url, {"course_type": "RECORDED"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['course_type'], "RECORDED")

        # Test invalid choice rejected
        response = self.client.patch(detail_url, {"course_type": "INVALID_VALUE"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_configuration_recognizes_course_type(self):
        from . import admin as courses_admin
        model_admin = admin.site._registry[Course]
        self.assertIn('course_type', model_admin.list_display)
        self.assertIn('course_type', model_admin.list_filter)


class LiveBatchTests(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher_batch", password="password123")
        self.teacher.is_teacher = True
        self.teacher.save()

        self.student = User.objects.create_user(username="student_batch", password="password123")
        self.student.is_student = True
        self.student.save()

        self.live_course = Course.objects.create(
            title="Live Tabla",
            description="Live class course",
            course_type=Course.CourseType.LIVE
        )
        self.recorded_course = Course.objects.create(
            title="Recorded Sitar",
            description="Recorded course",
            course_type=Course.CourseType.RECORDED
        )

    def test_live_course_can_have_live_batch(self):
        batch = LiveBatch.objects.create(
            course=self.live_course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )
        self.assertEqual(batch.course, self.live_course)
        self.assertEqual(batch.instructor, self.teacher)
        self.assertEqual(batch.batch_type, "GROUP")

    def test_recorded_course_cannot_have_live_batch(self):
        from django.core.exceptions import ValidationError
        batch = LiveBatch(
            course=self.recorded_course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )
        with self.assertRaises(ValidationError):
            batch.full_clean()

    def test_one_to_one_batch_can_be_created(self):
        batch = LiveBatch.objects.create(
            course=self.live_course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.ONE_TO_ONE
        )
        self.assertEqual(batch.batch_type, "ONE_TO_ONE")

    def test_group_batch_can_be_created(self):
        batch = LiveBatch.objects.create(
            course=self.live_course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )
        self.assertEqual(batch.batch_type, "GROUP")

    def test_instructor_relationship_validation(self):
        from django.core.exceptions import ValidationError
        batch = LiveBatch(
            course=self.live_course,
            instructor=self.student,
            batch_type=LiveBatch.BatchType.GROUP
        )
        with self.assertRaises(ValidationError):
            batch.full_clean()

    def test_course_deletion_cascades_to_live_batch(self):
        batch = LiveBatch.objects.create(
            course=self.live_course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )
        batch_id = batch.id
        self.live_course.delete()
        self.assertFalse(LiveBatch.objects.filter(id=batch_id).exists())


class LiveBatchStudentTests(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher_batch_stud", password="password123")
        self.teacher.is_teacher = True
        self.teacher.save()

        self.student1 = User.objects.create_user(username="student1_batch_stud", password="password123")
        self.student1.is_student = True
        self.student1.save()

        self.student2 = User.objects.create_user(username="student2_batch_stud", password="password123")
        self.student2.is_student = True
        self.student2.save()

        self.live_course = Course.objects.create(
            title="Live Tabla",
            description="Live class course",
            course_type=Course.CourseType.LIVE
        )

        self.one_to_one_batch = LiveBatch.objects.create(
            course=self.live_course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.ONE_TO_ONE
        )

        self.group_batch = LiveBatch.objects.create(
            course=self.live_course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )

    def test_student_can_be_assigned_to_batch(self):
        assignment = LiveBatchStudent.objects.create(
            batch=self.group_batch,
            student=self.student1
        )
        self.assertEqual(assignment.batch, self.group_batch)
        self.assertEqual(assignment.student, self.student1)
        self.assertIsNone(assignment.purchase)

    def test_same_student_cannot_be_assigned_twice_to_same_batch(self):
        from django.db import IntegrityError
        from django.core.exceptions import ValidationError
        LiveBatchStudent.objects.create(
            batch=self.group_batch,
            student=self.student1
        )
        with self.assertRaises((IntegrityError, ValidationError)):
            LiveBatchStudent.objects.create(
                batch=self.group_batch,
                student=self.student1
            )

    def test_one_to_one_batch_allows_only_one_student(self):
        from django.core.exceptions import ValidationError
        LiveBatchStudent.objects.create(
            batch=self.one_to_one_batch,
            student=self.student1
        )
        assignment2 = LiveBatchStudent(
            batch=self.one_to_one_batch,
            student=self.student2
        )
        with self.assertRaises(ValidationError):
            assignment2.full_clean()

    def test_group_batch_allows_multiple_students(self):
        LiveBatchStudent.objects.create(
            batch=self.group_batch,
            student=self.student1
        )
        LiveBatchStudent.objects.create(
            batch=self.group_batch,
            student=self.student2
        )
        self.assertEqual(LiveBatchStudent.objects.filter(batch=self.group_batch).count(), 2)

    def test_deleting_batch_removes_assignments(self):
        assignment = LiveBatchStudent.objects.create(
            batch=self.group_batch,
            student=self.student1
        )
        assignment_id = assignment.id
        self.group_batch.delete()
        self.assertFalse(LiveBatchStudent.objects.filter(id=assignment_id).exists())

    def test_purchase_relationship_is_optional_and_on_delete_set_null(self):
        from orders.models import Purchase
        purchase = Purchase.objects.create(
            user=self.student1,
            course=self.live_course,
            amount=500.00,
            status="SUCCESS",
            razorpay_order_id="order_123"
        )
        assignment = LiveBatchStudent.objects.create(
            batch=self.group_batch,
            student=self.student1,
            purchase=purchase
        )
        self.assertEqual(assignment.purchase, purchase)
        purchase.delete()
        assignment.refresh_from_db()
        self.assertIsNone(assignment.purchase)


class LiveClassMigrationCompatibilityTests(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher_migration", password="password123")
        self.teacher.is_teacher = True
        self.teacher.save()

        self.live_course = Course.objects.create(
            title="Live Course Migration",
            description="Live course description",
            course_type=Course.CourseType.LIVE
        )

        self.batch = LiveBatch.objects.create(
            course=self.live_course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )

    def test_live_class_legacy_fields_synchronized_on_save(self):
        live_class = LiveClass.objects.create(
            title="Class Session 1",
            scheduled_start=timezone.now(),
            duration_minutes=60,
            meeting_url="https://zoom.us/j/12345",
            batch=self.batch
        )
        self.assertEqual(live_class.course, self.live_course)
        self.assertEqual(live_class.instructor, self.teacher)

    def test_live_class_validation_prevents_mismatched_legacy_fields(self):
        from django.core.exceptions import ValidationError
        other_course = Course.objects.create(
            title="Other Course",
            course_type=Course.CourseType.LIVE
        )
        live_class = LiveClass(
            title="Mismatched Session",
            scheduled_start=timezone.now(),
            duration_minutes=60,
            meeting_url="https://zoom.us/j/12345",
            batch=self.batch,
            course=other_course
        )
        with self.assertRaises(ValidationError):
            live_class.full_clean()

    def test_live_class_batch_on_delete_set_null(self):
        live_class = LiveClass.objects.create(
            title="Session to check delete",
            scheduled_start=timezone.now(),
            duration_minutes=60,
            meeting_url="https://zoom.us/j/12345",
            batch=self.batch
        )
        live_class_id = live_class.id
        self.batch.delete()
        live_class.refresh_from_db()
        self.assertIsNone(live_class.batch)
        self.assertTrue(LiveClass.objects.filter(id=live_class_id).exists())


class LiveBatchAPIViewTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="admin_api_test", password="password123")

        self.teacher1 = User.objects.create_user(username="teacher1_api_test", password="password123")
        self.teacher1.is_teacher = True
        self.teacher1.save()

        self.teacher2 = User.objects.create_user(username="teacher2_api_test", password="password123")
        self.teacher2.is_teacher = True
        self.teacher2.save()

        self.student1 = User.objects.create_user(username="student1_api_test", password="password123")
        self.student1.is_student = True
        self.student1.save()

        self.student2 = User.objects.create_user(username="student2_api_test", password="password123")
        self.student2.is_student = True
        self.student2.save()

        self.live_course = Course.objects.create(
            title="Live Course API Test",
            description="Live course description",
            price=150.00,
            course_type=Course.CourseType.LIVE,
            is_published=True
        )

        self.free_live_course = Course.objects.create(
            title="Free Live Course",
            description="Free live course description",
            price=0.00,
            course_type=Course.CourseType.LIVE,
            is_published=True
        )

        self.recorded_course = Course.objects.create(
            title="Recorded Course API Test",
            description="Recorded course description",
            price=100.00,
            course_type=Course.CourseType.RECORDED,
            is_published=True
        )

        self.batch1 = LiveBatch.objects.create(
            course=self.live_course,
            instructor=self.teacher1,
            batch_type=LiveBatch.BatchType.GROUP
        )

        self.batch2 = LiveBatch.objects.create(
            course=self.live_course,
            instructor=self.teacher2,
            batch_type=LiveBatch.BatchType.ONE_TO_ONE
        )

    def test_admin_full_crud_batches(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('live-batch-list')

        # Create
        response = self.client.post(url, {
            "course": self.live_course.id,
            "instructor": self.teacher1.id,
            "batch_type": "GROUP"
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        batch_id = response.data['id']

        # List
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(any(b['id'] == batch_id for b in response.data))

        # Retrieve
        detail_url = reverse('live-batch-detail', kwargs={'pk': batch_id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['batch_type'], "GROUP")

        # Update
        response = self.client.patch(detail_url, {"batch_type": "ONE_TO_ONE"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['batch_type'], "ONE_TO_ONE")

        # Delete
        response = self.client.delete(detail_url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

    def test_recorded_course_rejected_for_live_batch(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('live-batch-list')
        response = self.client.post(url, {
            "course": self.recorded_course.id,
            "instructor": self.teacher1.id,
            "batch_type": "GROUP"
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_teacher_permission_boundaries(self):
        self.client.force_authenticate(user=self.teacher1)
        url_list = reverse('live-batch-list')

        # 1. Teachers cannot create batches
        response = self.client.post(url_list, {
            "course": self.live_course.id,
            "instructor": self.teacher1.id,
            "batch_type": "GROUP"
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # 2. Teachers can view their own batches, but not others
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data
        # self.batch1 is teacher1's batch, self.batch2 is teacher2's batch
        self.assertTrue(any(b['id'] == self.batch1.id for b in results))
        self.assertFalse(any(b['id'] == self.batch2.id for b in results))

        # 3. Retrieve detail of own batch succeeds
        response = self.client.get(reverse('live-batch-detail', kwargs={'pk': self.batch1.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # 4. Retrieve detail of other teacher's batch returns 404/403
        response = self.client.get(reverse('live-batch-detail', kwargs={'pk': self.batch2.id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # 5. Teacher cannot update or delete own batch
        response = self.client.patch(reverse('live-batch-detail', kwargs={'pk': self.batch1.id}), {"batch_type": "ONE_TO_ONE"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_permission_boundaries(self):
        self.client.force_authenticate(user=self.student1)
        url_list = reverse('live-batch-list')

        # Students cannot list all batches (only return batches they are assigned to)
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

        # Assign student1 to batch1
        LiveBatchStudent.objects.create(batch=self.batch1, student=self.student1)

        # Now student1 can see batch1 in list
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], self.batch1.id)

        # Students cannot create/update/delete
        response = self.client.post(url_list, {
            "course": self.live_course.id,
            "batch_type": "GROUP"
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_assign_student_group_batch(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('live-batch-students', kwargs={'pk': self.batch1.id})

        # Non-admin cannot assign student
        self.client.force_authenticate(user=self.student1)
        response = self.client.post(url, {"student_id": self.student2.id})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.admin)

        # Paid course requires purchase if assigned by non-admin or if normal rules apply.
        # Admin manual override: admin assigns without purchase succeeds
        response = self.client.post(url, {
            "student_id": self.student1.id
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['student_username'], self.student1.username)

        # Verify enrollment sync occurred
        self.assertTrue(Enrollment.objects.filter(user=self.student1, course=self.live_course).exists())

        # Test duplicate assignment: idempotent, returns 200 OK
        response = self.client.post(url, {
            "student_id": self.student1.id
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_assign_student_purchase_validation(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('live-batch-students', kwargs={'pk': self.batch1.id})

        from orders.models import Purchase
        # 1. Invalid/non-success purchase rejected
        p_failed = Purchase.objects.create(
            user=self.student1,
            course=self.live_course,
            amount=150.00,
            status="FAILED"
        )
        response = self.client.post(url, {
            "student_id": self.student1.id,
            "purchase_id": p_failed.id
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # 2. Purchase for wrong course rejected
        other_course = Course.objects.create(title="Other", price=10.00, course_type=Course.CourseType.LIVE)
        p_wrong_course = Purchase.objects.create(
            user=self.student1,
            course=other_course,
            amount=10.00,
            status="SUCCESS"
        )
        response = self.client.post(url, {
            "student_id": self.student1.id,
            "purchase_id": p_wrong_course.id
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # 3. Purchase for wrong user rejected
        p_wrong_user = Purchase.objects.create(
            user=self.student2,
            course=self.live_course,
            amount=150.00,
            status="SUCCESS"
        )
        response = self.client.post(url, {
            "student_id": self.student1.id,
            "purchase_id": p_wrong_user.id
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # 4. Valid purchase succeeds
        p_valid = Purchase.objects.create(
            user=self.student1,
            course=self.live_course,
            amount=150.00,
            status="SUCCESS"
        )
        response = self.client.post(url, {
            "student_id": self.student1.id,
            "purchase_id": p_valid.id
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['purchase_status'], "SUCCESS")

    def test_assign_student_free_course_omitted_purchase(self):
        self.client.force_authenticate(user=self.admin)
        free_batch = LiveBatch.objects.create(
            course=self.free_live_course,
            instructor=self.teacher1,
            batch_type=LiveBatch.BatchType.GROUP
        )
        url = reverse('live-batch-students', kwargs={'pk': free_batch.id})

        # Student assignment without purchase succeeds on free course
        response = self.client.post(url, {
            "student_id": self.student1.id
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_one_to_one_batch_capacity_api(self):
        self.client.force_authenticate(user=self.admin)
        url = reverse('live-batch-students', kwargs={'pk': self.batch2.id})

        # First student assigned succeeds
        response = self.client.post(url, {"student_id": self.student1.id})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Second student assignment fails due to capacity constraint
        response = self.client.post(url, {"student_id": self.student2.id})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_student_removal_api_and_data_integrity(self):
        # Setup assignment with a purchase
        from orders.models import Purchase
        purchase = Purchase.objects.create(
            user=self.student1,
            course=self.live_course,
            amount=150.00,
            status="SUCCESS"
        )
        assignment = LiveBatchStudent.objects.create(
            batch=self.batch1,
            student=self.student1,
            purchase=purchase
        )
        # Ensure enrollment exists
        enrollment = Enrollment.objects.create(user=self.student1, course=self.live_course)

        # Non-admin removal fails
        self.client.force_authenticate(user=self.student1)
        url = reverse('live-batch-remove-student', kwargs={'pk': self.batch1.id, 'student_id': self.student1.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Admin removal succeeds
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        # Check assignment is deleted
        self.assertFalse(LiveBatchStudent.objects.filter(id=assignment.id).exists())

        # Verify that purchase record and enrollment remain untouched in database
        self.assertTrue(Purchase.objects.filter(id=purchase.id).exists())
        self.assertTrue(Enrollment.objects.filter(id=enrollment.id).exists())


class LiveClassPhaseCTestSuite(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="admin_c", password="password123")

        self.teacher_a = User.objects.create_user(username="teacher_a", password="password123")
        self.teacher_a.is_teacher = True
        self.teacher_a.save()

        self.teacher_b = User.objects.create_user(username="teacher_b", password="password123")
        self.teacher_b.is_teacher = True
        self.teacher_b.save()

        self.student_a = User.objects.create_user(username="student_a", password="password123")
        self.student_a.is_student = True
        self.student_a.save()

        self.student_b = User.objects.create_user(username="student_b", password="password123")
        self.student_b.is_student = True
        self.student_b.save()

        self.student_unassigned = User.objects.create_user(username="student_unassigned", password="password123")
        self.student_unassigned.is_student = True
        self.student_unassigned.save()

        self.course_live = Course.objects.create(
            title="Live Course Phase C",
            price=100.00,
            course_type=Course.CourseType.LIVE,
            is_published=True
        )

        # Enroll all students in the course (representing course-level access)
        Enrollment.objects.create(user=self.student_a, course=self.course_live)
        Enrollment.objects.create(user=self.student_b, course=self.course_live)
        Enrollment.objects.create(user=self.student_unassigned, course=self.course_live)

        # Create batches
        self.batch_a = LiveBatch.objects.create(
            course=self.course_live,
            instructor=self.teacher_a,
            batch_type=LiveBatch.BatchType.ONE_TO_ONE
        )
        self.batch_b = LiveBatch.objects.create(
            course=self.course_live,
            instructor=self.teacher_b,
            batch_type=LiveBatch.BatchType.GROUP
        )

        # Assign students to batches
        LiveBatchStudent.objects.create(batch=self.batch_a, student=self.student_a)
        LiveBatchStudent.objects.create(batch=self.batch_b, student=self.student_b)

        self.now = timezone.now()

        # Create classes
        self.class_a = LiveClass.objects.create(
            batch=self.batch_a,
            course=self.course_live,
            instructor=self.teacher_a,
            title="Session for Batch A",
            scheduled_start=self.now + timezone.timedelta(days=1),
            duration_minutes=60,
            meeting_url="https://zoom.us/batch-a"
        )
        self.class_b = LiveClass.objects.create(
            batch=self.batch_b,
            course=self.course_live,
            instructor=self.teacher_b,
            title="Session for Batch B",
            scheduled_start=self.now + timezone.timedelta(days=2),
            duration_minutes=60,
            meeting_url="https://zoom.us/batch-b"
        )

    def test_unauthenticated_blocked(self):
        url = reverse('live-class-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_admin_has_full_access(self):
        self.client.force_authenticate(user=self.admin)
        url_list = reverse('live-class-list')
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Admins should see both classes
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 2)

        # Admin can retrieve any class
        detail_url = reverse('live-class-detail', kwargs={'pk': self.class_a.id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['meeting_url'], "https://zoom.us/batch-a")

    def test_teacher_isolation(self):
        # Teacher A can see only Batch A classes
        self.client.force_authenticate(user=self.teacher_a)
        url_list = reverse('live-class-list')
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.class_a.id)

        # Teacher A cannot retrieve Batch B class (returns 404)
        detail_url = reverse('live-class-detail', kwargs={'pk': self.class_b.id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Teacher A cannot update Teacher B class (returns 404/403)
        response = self.client.patch(detail_url, {"title": "Hacked"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Teacher A cannot create class for Batch B
        response = self.client.post(url_list, {
            "batch": self.batch_b.id,
            "title": "Teacher A trying to create class on batch B",
            "scheduled_start": self.now + timezone.timedelta(days=1),
            "duration_minutes": 30
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # Teacher A can create class for Batch A
        response = self.client.post(url_list, {
            "batch": self.batch_a.id,
            "title": "Teacher A creates class on batch A",
            "scheduled_start": self.now + timezone.timedelta(days=3),
            "duration_minutes": 30,
            "meeting_url": "https://zoom.us/teacher-a-class"
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_student_isolation_and_meeting_url(self):
        # Student A sees only Batch A classes
        self.client.force_authenticate(user=self.student_a)
        url_list = reverse('live-class-list')
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], self.class_a.id)

        # Student A can retrieve Batch A class and see meeting_url
        detail_url_a = reverse('live-class-detail', kwargs={'pk': self.class_a.id})
        response = self.client.get(detail_url_a)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['meeting_url'], "https://zoom.us/batch-a")

        # Student A cannot retrieve Batch B class
        detail_url_b = reverse('live-class-detail', kwargs={'pk': self.class_b.id})
        response = self.client.get(detail_url_b)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Student A cannot perform writes
        response = self.client.patch(detail_url_a, {"title": "Student A Hacked"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_unassigned_student_denied(self):
        # Student is enrolled in course, but has no batch assignment
        self.client.force_authenticate(user=self.student_unassigned)
        url_list = reverse('live-class-list')
        response = self.client.get(url_list)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 0)

        # Cannot retrieve class detail
        detail_url = reverse('live-class-detail', kwargs={'pk': self.class_a.id})
        response = self.client.get(detail_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_serializer_derivations(self):
        self.client.force_authenticate(user=self.admin)
        url_list = reverse('live-class-list')

        # 1. POST with batch ID derives course and instructor
        response = self.client.post(url_list, {
            "batch": self.batch_a.id,
            "title": "Derived Class",
            "scheduled_start": self.now + timezone.timedelta(days=4),
            "duration_minutes": 45,
            "meeting_url": "https://zoom.us/derived-class"
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['course'], self.course_live.id)
        self.assertEqual(response.data['instructor'], self.teacher_a.id)

        # 2. POST with conflicting course is rejected
        other_course = Course.objects.create(title="Other", course_type=Course.CourseType.LIVE)
        response = self.client.post(url_list, {
            "batch": self.batch_a.id,
            "course": other_course.id,
            "title": "Conflicting Course",
            "scheduled_start": self.now + timezone.timedelta(days=4),
            "duration_minutes": 45,
            "meeting_url": "https://zoom.us/conflicting-course"
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("course", response.data)

        # 3. POST with conflicting instructor is rejected
        response = self.client.post(url_list, {
            "batch": self.batch_a.id,
            "instructor": self.teacher_b.id,
            "title": "Conflicting Instructor",
            "scheduled_start": self.now + timezone.timedelta(days=4),
            "duration_minutes": 45,
            "meeting_url": "https://zoom.us/conflicting-instructor"
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("instructor", response.data)

    def test_status_lifecycle_and_duplicate_calls(self):
        self.client.force_authenticate(user=self.teacher_a)
        url_start = reverse('live-class-start', kwargs={'pk': self.class_a.id})
        url_end = reverse('live-class-end', kwargs={'pk': self.class_a.id})
        url_cancel = reverse('live-class-cancel', kwargs={'pk': self.class_a.id})

        # 1. Start class (SCHEDULED -> LIVE)
        response = self.client.post(url_start)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], "LIVE")

        # 2. Duplicate start returns 400 Bad Request
        response = self.client.post(url_start)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # 3. Cancel returns 400 because status is LIVE
        response = self.client.post(url_cancel)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        # 4. End class (LIVE -> COMPLETED)
        response = self.client.post(url_end)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], "COMPLETED")

        # 5. End completed returns 400
        response = self.client.post(url_end)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upcoming_and_history_filters(self):
        # Clean current classes
        LiveClass.objects.all().delete()

        # Create upcoming
        up_class = LiveClass.objects.create(
            batch=self.batch_a,
            course=self.course_live,
            instructor=self.teacher_a,
            title="Upcoming",
            scheduled_start=self.now + timezone.timedelta(days=5),
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED,
            meeting_url="https://zoom.us/up"
        )
        # Create completed
        past_class = LiveClass.objects.create(
            batch=self.batch_a,
            course=self.course_live,
            instructor=self.teacher_a,
            title="Completed",
            scheduled_start=self.now - timezone.timedelta(days=5),
            duration_minutes=60,
            status=LiveClass.ClassStatus.COMPLETED,
            meeting_url="https://zoom.us/past"
        )

        # Student A (assigned to batch A) queries
        self.client.force_authenticate(user=self.student_a)

        # Upcoming: should contain up_class
        response = self.client.get(reverse('live-class-upcoming'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], up_class.id)

        # History: should contain past_class
        response = self.client.get(reverse('live-class-history'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['id'], past_class.id)

        # Student B (assigned to batch B) queries: should get empty lists
        self.client.force_authenticate(user=self.student_b)
        response = self.client.get(reverse('live-class-upcoming'))
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 0)

        response = self.client.get(reverse('live-class-history'))
        results = response.data.get('results', response.data)
        self.assertEqual(len(results), 0)

    def test_batch_deletion_preserves_historical_class(self):
        class_id = self.class_a.id
        self.batch_a.delete()

        # Check LiveClass is not deleted but batch is NULL
        self.class_a.refresh_from_db()
        self.assertIsNone(self.class_a.batch)
        self.assertEqual(self.class_a.course, self.course_live)
        self.assertEqual(self.class_a.instructor, self.teacher_a)

        # Students cannot view it
        self.client.force_authenticate(user=self.student_a)
        response = self.client.get(reverse('live-class-detail', kwargs={'pk': class_id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Admins can view it
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(reverse('live-class-detail', kwargs={'pk': class_id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_student_removal_revokes_access(self):
        # Remove student A from Batch A
        LiveBatchStudent.objects.filter(batch=self.batch_a, student=self.student_a).delete()

        # Student A tries to retrieve details: 404
        self.client.force_authenticate(user=self.student_a)
        response = self.client.get(reverse('live-class-detail', kwargs={'pk': self.class_a.id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        # Enrollment is intact
        self.assertTrue(Enrollment.objects.filter(user=self.student_a, course=self.course_live).exists())


from unittest.mock import patch, MagicMock

class RecordedClassTranslationPhase1Tests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="admin_translation", password="password123")
        self.teacher = User.objects.create_user(username="teacher_translation", password="password123")
        self.teacher.is_teacher = True
        self.teacher.save()
        self.student = User.objects.create_user(username="student_translation", password="password123")

        self.course = Course.objects.create(
            title="Recorded Course Test",
            price=150.00,
            course_type=Course.CourseType.RECORDED,
            is_published=True
        )
        self.module = Module.objects.create(course=self.course, title="Module Test", order=1)
        self.lesson = VideoLesson.objects.create(
            module=self.module,
            title="Lesson Test",
            description="Test Description",
            transcript="Hello world transcript.",
            video_file="videos/lessons/test_video.mp4"
        )

    def test_celery_task_registration(self):
        from core.celery import app as celery_app
        self.assertIn('courses.tasks.generate_dubbed_audio_task', celery_app.tasks)

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_api_enqueues_task_successfully(self, mock_delay):
        self.client.force_authenticate(user=self.admin)
        url = reverse('lesson-generate-ai-audio', kwargs={'pk': self.lesson.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        mock_delay.assert_called_once_with(self.lesson.id, target_languages=['hi', 'ta', 'ml'])

        # Verify TranslatedAudio records were created with status 'processing'
        for lang in ['hi', 'ta', 'ml']:
            self.assertTrue(TranslatedAudio.objects.filter(lesson=self.lesson, language_code=lang, status='processing').exists())

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_admin_action_enqueues_task(self, mock_delay):
        from courses.admin import generate_ai_audio
        mock_modeladmin = MagicMock()
        mock_request = MagicMock()
        mock_request.user = self.admin

        queryset = VideoLesson.objects.filter(id=self.lesson.id)

        # Call the action
        generate_ai_audio(mock_modeladmin, mock_request, queryset)

        mock_delay.assert_called_once_with(self.lesson.id, target_languages=['hi', 'ta', 'ml'])
        for lang in ['hi', 'ta', 'ml']:
            self.assertTrue(TranslatedAudio.objects.filter(lesson=self.lesson, language_code=lang, status='processing').exists())

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_duplicate_processing_is_prevented(self, mock_delay):
        # Create an existing track in 'processing' status
        TranslatedAudio.objects.create(lesson=self.lesson, language_code='hi', status='processing')

        self.client.force_authenticate(user=self.admin)
        url = reverse('lesson-generate-ai-audio', kwargs={'pk': self.lesson.id})
        response = self.client.post(url)

        # Duplicate should be rejected
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_delay.assert_not_called()

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_completed_translation_not_duplicated(self, mock_delay):
        # Create all tracks in 'completed' status
        for lang in ['hi', 'ta', 'ml']:
            TranslatedAudio.objects.create(lesson=self.lesson, language_code=lang, status='completed')

        self.client.force_authenticate(user=self.admin)
        url = reverse('lesson-generate-ai-audio', kwargs={'pk': self.lesson.id})
        response = self.client.post(url)

        # Should inform that they are already completed, and not enqueue
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("already generated", response.data['message'])
        mock_delay.assert_not_called()

    @patch('courses.tasks.generate_dubbed_audio_task.delay')
    def test_failed_translation_can_be_retried(self, mock_delay):
        # Create a failed track
        TranslatedAudio.objects.create(lesson=self.lesson, language_code='hi', status='failed')

        self.client.force_authenticate(user=self.admin)
        url = reverse('lesson-generate-ai-audio', kwargs={'pk': self.lesson.id})
        response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_delay.assert_called_once_with(self.lesson.id, target_languages=['hi', 'ta', 'ml'])

    @patch('courses.tasks.generate_dubbed_audio_task.retry')
    @patch('courses.tasks.generate_dubbed_audio')
    def test_celery_transient_failures_retry(self, mock_generate, mock_retry):
        from courses.tasks import generate_dubbed_audio_task
        mock_generate.side_effect = requests.RequestException("Transient Network Error")

        # Set settings credentials to avoid ValueError
        with self.settings(GOOGLE_API_KEY='test_google', OPENAI_API_KEY='test_openai'):
            generate_dubbed_audio_task(self.lesson.id)

        mock_retry.assert_called_once()

    @patch('courses.tasks.generate_dubbed_audio_task.retry')
    @patch('courses.tasks.generate_dubbed_audio')
    def test_permanent_failures_do_not_retry(self, mock_generate, mock_retry):
        from courses.tasks import generate_dubbed_audio_task
        # Setup a 'processing' track to test failure transition
        TranslatedAudio.objects.create(lesson=self.lesson, language_code='hi', status='processing')

        mock_generate.side_effect = ValueError("Permanent Config Error")

        with self.settings(GOOGLE_API_KEY='test_google', OPENAI_API_KEY='test_openai'):
            with self.assertRaises(ValueError):
                generate_dubbed_audio_task(self.lesson.id, target_languages=['hi'])

        mock_retry.assert_not_called()
        self.assertEqual(TranslatedAudio.objects.get(lesson=self.lesson, language_code='hi').status, 'failed')

    @patch('courses.services.ai_translator.translate_text')
    @patch('courses.services.ai_translator.text_to_speech')
    @patch('courses.services.ai_translator.AudioSegment')
    @patch('openai.resources.audio.transcriptions.Transcriptions.create')
    @patch('os.path.getsize')
    @patch('subprocess.run')
    def test_whisper_large_file_chunking(self, mock_run, mock_getsize, mock_whisper, mock_audio_segment, mock_tts, mock_translate):
        from courses.services.ai_translator import generate_dubbed_audio

        # 1. Mock file size to be 30MB (exceeds 24MB threshold)
        mock_getsize.return_value = 30 * 1024 * 1024

        # 2. Mock pydub AudioSegment
        mock_audio = MagicMock()
        mock_audio_segment.from_file.return_value = mock_audio
        # Total duration = 20 minutes (1200000 ms)
        mock_audio.__len__.return_value = 20 * 60 * 1000

        mock_chunk = MagicMock()
        def create_dummy_chunk(filepath, *args, **kwargs):
            with open(filepath, 'wb') as f:
                f.write(b"dummy_mp3_data")
        mock_chunk.export.side_effect = create_dummy_chunk
        mock_audio.__getitem__.return_value = mock_chunk

        # 3. Mock Whisper transcription response
        mock_whisper.return_value = "1\n00:00:01,000 --> 00:00:05,000\nHello Chunk"

        # Mock TTS and translation
        mock_translate.return_value = "Hola Chunk"
        mock_tts.return_value = b"mp3bytes"

        with self.settings(GOOGLE_API_KEY='test_google', OPENAI_API_KEY='test_openai'):
            generate_dubbed_audio(self.lesson.id, target_languages=['hi'])

        # Verify Whisper was called twice (for 2 chunks of 15m and 5m)
        self.assertEqual(mock_whisper.call_count, 2)
        # Verify TranslatedAudio for 'hi' was saved as 'completed'
        self.assertTrue(TranslatedAudio.objects.filter(lesson=self.lesson, language_code='hi', status='completed').exists())

from rest_framework.test import APITransactionTestCase
from unittest.mock import patch

class LiveClassNotificationTests(APITransactionTestCase):
    def setUp(self):
        from django.utils import timezone
        import datetime
        from users.models import User
        from courses.models import Course, LiveBatch, LiveBatchStudent, LiveClass
        from django.contrib.auth import get_user_model

        User = get_user_model()
        self.instructor = User.objects.create_user(username='inst1', password='pw', is_teacher=True, is_student=False, email='inst1@test.com')
        self.student1 = User.objects.create_user(username='stu1', password='pw', is_student=True, email='stu1@test.com')
        self.student2 = User.objects.create_user(username='stu2', password='pw', is_student=True, email='stu2@test.com')

        self.course = Course.objects.create(title="Test Course", course_type='LIVE', price=100)
        self.batch = LiveBatch.objects.create(course=self.course, batch_type='GROUP', instructor=self.instructor)

        LiveBatchStudent.objects.create(batch=self.batch, student=self.student1)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student2)

        self.client.force_authenticate(user=self.instructor)
        self.future_time = timezone.now() + datetime.timedelta(days=2)

    @patch('courses.tasks.send_class_reminder.apply_async')
    def test_scheduled_notification(self, mock_apply_async):
        from notifications.models import Notification, NotificationType

        url = reverse('live-class-list')
        data = {
            "title": "New Session",
            "batch": self.batch.id,
            "scheduled_start": self.future_time.isoformat(),
            "duration_minutes": 60,
            "meeting_url": "http://zoom.us/test"
        }

        response = self.client.post(url, data)
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        class_id = response.data['id']

        # Students should receive notification
        stu1_notifs = Notification.objects.filter(recipient=self.student1, notification_type=NotificationType.LIVE_CLASS)
        self.assertEqual(stu1_notifs.count(), 1)
        self.assertIn("Scheduled", stu1_notifs.first().title)
        self.assertEqual(stu1_notifs.first().idempotency_key, f"liveclass:{class_id}:scheduled:{self.student1.id}")

        # Instructor should not receive notification
        inst_notifs = Notification.objects.filter(recipient=self.instructor, notification_type=NotificationType.LIVE_CLASS)
        self.assertEqual(inst_notifs.count(), 0)

    @patch('courses.tasks.send_class_reminder.apply_async')
    def test_reschedule_notification(self, mock_apply_async):
        from notifications.models import Notification, NotificationType
        from courses.models import LiveClass
        from django.utils import timezone
        import datetime

        live_class = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            instructor=self.instructor,
            title="Session 1",
            scheduled_start=self.future_time,
            duration_minutes=60,
            status='SCHEDULED',
            meeting_url="http://zoom.us/test"
        )

        url = reverse('live-class-reschedule', args=[live_class.id])
        new_time = self.future_time + datetime.timedelta(hours=2)

        response = self.client.post(url, {"scheduled_start": new_time.isoformat()})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        new_timestamp = int(new_time.timestamp())
        stu1_notifs = Notification.objects.filter(recipient=self.student1, notification_type=NotificationType.LIVE_CLASS)
        self.assertEqual(stu1_notifs.count(), 1)
        self.assertIn("Rescheduled", stu1_notifs.first().title)
        self.assertEqual(stu1_notifs.first().idempotency_key, f"liveclass:{live_class.id}:rescheduled:{new_timestamp}:{self.student1.id}")

        # Repeat reschedule to same time should not create duplicate
        response = self.client.post(url, {"scheduled_start": new_time.isoformat()})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Notification.objects.filter(recipient=self.student1).count(), 1)

    def test_cancel_notification(self):
        from notifications.models import Notification, NotificationType
        from courses.models import LiveClass

        live_class = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            instructor=self.instructor,
            title="Session 1",
            scheduled_start=self.future_time,
            duration_minutes=60,
            status='SCHEDULED',
            meeting_url="http://zoom.us/test"
        )

        url = reverse('live-class-cancel', args=[live_class.id])

        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        stu1_notifs = Notification.objects.filter(recipient=self.student1, notification_type=NotificationType.LIVE_CLASS)
        self.assertEqual(stu1_notifs.count(), 1)
        self.assertIn("Cancelled", stu1_notifs.first().title)
        self.assertEqual(stu1_notifs.first().idempotency_key, f"liveclass:{live_class.id}:cancelled:{self.student1.id}")

    def test_reminder_celery_task(self):
        from notifications.models import Notification, NotificationType
        from courses.models import LiveClass
        from courses.tasks import send_class_reminder

        live_class = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            instructor=self.instructor,
            title="Session 1",
            scheduled_start=self.future_time,
            duration_minutes=60,
            status='SCHEDULED',
            meeting_url="http://zoom.us/test"
        )

        # Execute the task simulating ETA trigger
        expected_timestamp = int(self.future_time.timestamp())
        send_class_reminder(live_class.id, expected_timestamp)

        stu1_notifs = Notification.objects.filter(recipient=self.student1, notification_type=NotificationType.LIVE_CLASS)
        self.assertEqual(stu1_notifs.count(), 1)
        self.assertIn("Reminder", stu1_notifs.first().title)
        self.assertEqual(stu1_notifs.first().idempotency_key, f"liveclass:{live_class.id}:reminder:{self.student1.id}")

        # Test reminder aborts if cancelled
        live_class.status = 'CANCELLED'
        live_class.save()

        send_class_reminder(live_class.id, expected_timestamp)
        # Count should remain 1
        self.assertEqual(Notification.objects.filter(recipient=self.student1, notification_type=NotificationType.LIVE_CLASS).count(), 1)


class ManualAudioUploadTests(APITestCase):
    """
    Tests for the manual (non-AI) translated-audio upload workflow:
    POST/DELETE /api/courses/lessons/{id}/audio/[...]
    """

    def setUp(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.course = Course.objects.create(title="Dance Basics", price=100.00, is_published=True)
        self.module = Module.objects.create(course=self.course, title="Module 1", order=1)
        self.lesson = VideoLesson.objects.create(
            module=self.module,
            title="Lesson 1",
            video_file=SimpleUploadedFile("lesson.mp4", b"fake-video-bytes", content_type="video/mp4"),
        )

        self.admin = User.objects.create_superuser(username="admin", password="pw")
        self.teacher = User.objects.create_user(username="teacher", password="pw", is_teacher=True, is_student=False)
        self.student = User.objects.create_user(username="student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)

        self.upload_url = f"/api/courses/lessons/{self.lesson.id}/audio/"
        self.audio_file = lambda name="malayalam.mp3": SimpleUploadedFile(name, b"fake-audio-bytes", content_type="audio/mpeg")

    def _delete_url(self, audio_id):
        return f"/api/courses/lessons/{self.lesson.id}/audio/{audio_id}/"

    def test_admin_can_upload_audio_track(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.upload_url, {
            "language_code": "ml",
            "language_name": "Malayalam",
            "audio_file": self.audio_file(),
        }, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["language_code"], "ml")
        self.assertEqual(response.data["language_name"], "Malayalam")
        self.assertEqual(response.data["status"], "completed")
        self.assertTrue(TranslatedAudio.objects.filter(lesson=self.lesson, language_code="ml", status="completed").exists())

    def test_teacher_can_upload_audio_track(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(self.upload_url, {
            "language_code": "hi",
            "audio_file": self.audio_file("hindi.mp3"),
        }, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        # language_name auto-derived when not supplied
        self.assertEqual(response.data["language_name"], "Hindi")

    def test_student_cannot_upload_audio_track(self):
        self.client.force_authenticate(user=self.student)
        response = self.client.post(self.upload_url, {
            "language_code": "ml",
            "audio_file": self.audio_file(),
        }, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(TranslatedAudio.objects.filter(lesson=self.lesson, language_code="ml").exists())

    def test_anonymous_cannot_upload_audio_track(self):
        response = self.client.post(self.upload_url, {
            "language_code": "ml",
            "audio_file": self.audio_file(),
        }, format="multipart")
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_upload_rejects_english_as_translated_language(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.upload_url, {
            "language_code": "en",
            "audio_file": self.audio_file(),
        }, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_rejects_missing_audio_file(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.upload_url, {
            "language_code": "ml",
        }, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_rejects_missing_language_code(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.upload_url, {
            "audio_file": self.audio_file(),
        }, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_rejects_unsupported_file_type(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(self.upload_url, {
            "language_code": "ml",
            "audio_file": SimpleUploadedFile("notaudio.txt", b"not audio", content_type="text/plain"),
        }, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_language_replaces_existing_track_without_duplicating(self):
        self.client.force_authenticate(user=self.admin)
        first = self.client.post(self.upload_url, {
            "language_code": "ta",
            "audio_file": self.audio_file("tamil_v1.mp3"),
        }, format="multipart")
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        first_id = first.data["id"]

        second = self.client.post(self.upload_url, {
            "language_code": "ta",
            "audio_file": self.audio_file("tamil_v2.mp3"),
        }, format="multipart")
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["id"], first_id)  # same row updated, not a new one

        self.assertEqual(TranslatedAudio.objects.filter(lesson=self.lesson, language_code="ta").count(), 1)
        updated = TranslatedAudio.objects.get(lesson=self.lesson, language_code="ta")
        self.assertIn("tamil_v2", updated.audio_file.name)

    def test_legacy_regional_language_codes_are_unaffected(self):
        # Pre-existing AI-generated rows using regional codes must keep working.
        legacy = TranslatedAudio.objects.create(lesson=self.lesson, language_code="hi-IN", status="completed")
        self.client.force_authenticate(user=self.admin)

        # Uploading a new base-code 'hi' track must NOT touch the legacy 'hi-IN' row.
        response = self.client.post(self.upload_url, {
            "language_code": "hi",
            "audio_file": self.audio_file(),
        }, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        legacy.refresh_from_db()
        self.assertEqual(legacy.status, "completed")
        self.assertEqual(TranslatedAudio.objects.filter(lesson=self.lesson).count(), 2)

    def test_admin_can_delete_audio_track(self):
        track = TranslatedAudio.objects.create(lesson=self.lesson, language_code="ml", status="completed")
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(self._delete_url(track.id))
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(TranslatedAudio.objects.filter(id=track.id).exists())

    def test_student_cannot_delete_audio_track(self):
        track = TranslatedAudio.objects.create(lesson=self.lesson, language_code="ml", status="completed")
        self.client.force_authenticate(user=self.student)
        response = self.client.delete(self._delete_url(track.id))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(TranslatedAudio.objects.filter(id=track.id).exists())

    def test_delete_nonexistent_audio_track_returns_404(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(self._delete_url(999999))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_delete_audio_track_from_wrong_lesson_returns_404(self):
        other_lesson = VideoLesson.objects.create(module=self.module, title="Other Lesson")
        track = TranslatedAudio.objects.create(lesson=other_lesson, language_code="ml", status="completed")
        self.client.force_authenticate(user=self.admin)
        response = self.client.delete(self._delete_url(track.id))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(TranslatedAudio.objects.filter(id=track.id).exists())

    def test_lesson_serializer_response_includes_translated_audios_with_new_fields(self):
        TranslatedAudio.objects.create(lesson=self.lesson, language_code="ml", language_name="Malayalam", status="completed")
        self.client.force_authenticate(user=self.student)
        response = self.client.get(f"/api/courses/{self.course.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        lesson_data = response.data["modules"][0]["lessons"][0]
        self.assertIn("translated_audios", lesson_data)
        audio_data = lesson_data["translated_audios"][0]
        for field in ("id", "lesson", "language_code", "language_name", "audio_file", "status", "created_at", "updated_at"):
            self.assertIn(field, audio_data)

    def test_manual_upload_does_not_trigger_ai_dubbing_task(self):
        from unittest.mock import patch
        self.client.force_authenticate(user=self.admin)
        with patch('courses.tasks.generate_dubbed_audio_task.delay') as mock_delay:
            response = self.client.post(self.upload_url, {
                "language_code": "ml",
                "audio_file": self.audio_file(),
            }, format="multipart")
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)
            mock_delay.assert_not_called()
