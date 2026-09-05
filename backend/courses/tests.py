import requests
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import TestCase, Client as DjangoClient
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
from .models import RecurrenceRule, TeacherAvailability, Attendance
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

        # 1. Phase 2: a teacher CAN now create their own batch (self-service
        # scheduling -- "Teacher can create/manage classes assigned to
        # them"), but still cannot create one on another teacher's behalf.
        response = self.client.post(url_list, {
            "course": self.live_course.id,
            "instructor": self.teacher1.id,
            "batch_type": "GROUP"
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        response = self.client.post(url_list, {
            "course": self.live_course.id,
            "instructor": self.teacher2.id,
            "batch_type": "GROUP"
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

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

        # 5. Phase 2: a teacher CAN now update their own batch, but still
        # cannot touch another teacher's.
        response = self.client.patch(reverse('live-batch-detail', kwargs={'pk': self.batch1.id}), {"batch_type": "ONE_TO_ONE"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        response = self.client.patch(reverse('live-batch-detail', kwargs={'pk': self.batch2.id}), {"batch_type": "ONE_TO_ONE"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

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
        # Phase 2: idempotency key now includes reminder_type (default '1h') so
        # the 24h/1h/15m reminders for the same class+student don't collide.
        self.assertEqual(stu1_notifs.first().idempotency_key, f"liveclass:{live_class.id}:reminder:1h:{self.student1.id}")

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

        # Phase 1: course write access (including lesson/audio management)
        # is scoped to the course's actual assigned instructor via
        # CourseInstructor -- this teacher must be genuinely assigned to
        # this course, matching real usage, not just any is_teacher account.
        from .models import CourseInstructor
        CourseInstructor.objects.create(course=self.course, user=self.teacher, role=CourseInstructor.InstructorRole.TEACHER)

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


class CourseInstructorTests(APITestCase):
    """
    Phase 1: CourseInstructor integration into course permissions/queries.
    See ARCHITECTURE_PROPOSAL.md Phase 1.
    """

    def setUp(self):
        from .models import CourseInstructor
        self.CourseInstructor = CourseInstructor

        self.admin = User.objects.create_superuser(username="p1_ci_admin", password="pw")
        self.teacher_a = User.objects.create_user(username="p1_ci_teacher_a", password="pw", is_teacher=True, is_student=False)
        self.teacher_b = User.objects.create_user(username="p1_ci_teacher_b", password="pw", is_teacher=True, is_student=False)
        self.mentor = User.objects.create_user(username="p1_ci_mentor", password="pw", is_mentor=True, is_student=False)
        self.student = User.objects.create_user(username="p1_ci_student", password="pw")

        self.course_a = Course.objects.create(title="Course A", price=100, is_published=True)
        self.course_b = Course.objects.create(title="Course B", price=100, is_published=True)

        CourseInstructor.objects.create(course=self.course_a, user=self.teacher_a, role='TEACHER', is_primary=True)

        self.instructors_url = reverse('course-instructors', kwargs={'pk': self.course_a.id})

    def _remove_url(self, course_id, instructor_id):
        return reverse('course-remove-instructor', kwargs={'pk': course_id, 'instructor_id': instructor_id})

    # --- Item 1: Student cannot create CourseInstructor ---
    def test_student_cannot_create_course_instructor(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.post(self.instructors_url, {"user": self.teacher_b.id, "role": "TEACHER"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # --- Item 2: Student cannot assign instructors (alias of above via a
    # different course, confirming it's not course-specific) ---
    def test_student_cannot_assign_instructor_on_any_course(self):
        self.client.force_authenticate(user=self.student)
        url = reverse('course-instructors', kwargs={'pk': self.course_b.id})
        res = self.client.post(url, {"user": self.mentor.id, "role": "MENTOR"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(self.CourseInstructor.objects.filter(course=self.course_b).exists())

    # --- Item 3: Teacher can access (write to) their assigned course ---
    def test_teacher_can_edit_assigned_course(self):
        self.client.force_authenticate(user=self.teacher_a)
        url = reverse('course-detail', kwargs={'pk': self.course_a.id})
        res = self.client.patch(url, {"description": "Updated by assigned teacher"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.course_a.refresh_from_db()
        self.assertEqual(self.course_a.description, "Updated by assigned teacher")

    # --- Item 4: Teacher cannot manage an unrelated course ---
    def test_teacher_cannot_edit_unrelated_course(self):
        self.client.force_authenticate(user=self.teacher_b)
        url = reverse('course-detail', kwargs={'pk': self.course_a.id})
        res = self.client.patch(url, {"description": "Should not be allowed"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.course_a.refresh_from_db()
        self.assertNotEqual(self.course_a.description, "Should not be allowed")

    def test_mentor_cannot_edit_course_even_if_assigned(self):
        # Mentor role must NOT automatically receive full teacher permissions.
        self.CourseInstructor.objects.create(course=self.course_b, user=self.mentor, role='MENTOR')
        self.client.force_authenticate(user=self.mentor)
        url = reverse('course-detail', kwargs={'pk': self.course_b.id})
        res = self.client.patch(url, {"description": "Mentor should not be able to do this"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # --- Item 7: Admin can manage instructors ---
    def test_admin_can_add_and_view_instructor(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.instructors_url, {"user": self.mentor.id, "role": "MENTOR"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        res_get = self.client.get(self.instructors_url)
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        roles = {row['user']: row['role'] for row in res_get.data}
        self.assertEqual(roles.get(self.teacher_a.id), 'TEACHER')
        self.assertEqual(roles.get(self.mentor.id), 'MENTOR')

    def test_admin_can_remove_instructor(self):
        self.client.force_authenticate(user=self.admin)
        ci = self.CourseInstructor.objects.get(course=self.course_a, user=self.teacher_a)
        res = self.client.delete(self._remove_url(self.course_a.id, ci.id))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(self.CourseInstructor.objects.filter(pk=ci.pk).exists())

    # --- Item 8: Duplicate CourseInstructor prevented ---
    def test_duplicate_course_instructor_prevented(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.instructors_url, {"user": self.teacher_a.id, "role": "TEACHER"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(self.CourseInstructor.objects.filter(course=self.course_a, user=self.teacher_a, role='TEACHER').count(), 1)

    def test_cannot_assign_plain_student_as_instructor(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.instructors_url, {"user": self.student.id, "role": "TEACHER"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_setting_primary_unsets_previous_primary(self):
        self.client.force_authenticate(user=self.admin)
        self.client.post(self.instructors_url, {"user": self.mentor.id, "role": "MENTOR", "is_primary": True})
        self.CourseInstructor.objects.get(course=self.course_a, user=self.teacher_a).refresh_from_db()
        primaries = self.CourseInstructor.objects.filter(course=self.course_a, is_primary=True)
        self.assertEqual(primaries.count(), 1)
        self.assertEqual(primaries.first().user, self.mentor)

    # --- Item 10: Student remains enrolled after instructor changes ---
    def test_student_enrollment_unaffected_by_instructor_changes(self):
        Enrollment.objects.create(user=self.student, course=self.course_a)
        self.client.force_authenticate(user=self.admin)

        ci = self.CourseInstructor.objects.get(course=self.course_a, user=self.teacher_a)
        self.client.delete(self._remove_url(self.course_a.id, ci.id))
        self.client.post(self.instructors_url, {"user": self.teacher_b.id, "role": "TEACHER"})

        self.assertTrue(Enrollment.objects.filter(user=self.student, course=self.course_a).exists())

    # --- Item 15: CourseInstructor does not interfere with Enrollment ---
    def test_course_instructor_and_enrollment_are_independent(self):
        Enrollment.objects.create(user=self.student, course=self.course_a)
        # Student is enrolled but has no instructor role -- must not appear
        # as an instructor, and instructor assignment must not create/imply
        # an Enrollment for the instructor.
        self.assertFalse(self.CourseInstructor.objects.filter(course=self.course_a, user=self.student).exists())
        self.assertFalse(Enrollment.objects.filter(user=self.teacher_a, course=self.course_a).exists())

    # --- Item 11: Existing (legacy) teacher accounts continue working ---
    def test_legacy_teacher_without_courseinstructor_row_still_has_access(self):
        # Simulates a teacher who predates Phase 0's backfill: no
        # CourseInstructor row, only the old self-enrollment relationship.
        legacy_teacher = User.objects.create_user(username="p1_legacy_teacher", password="pw", is_teacher=True, is_student=False)
        Enrollment.objects.create(user=legacy_teacher, course=self.course_b)

        self.client.force_authenticate(user=legacy_teacher)
        url = reverse('course-detail', kwargs={'pk': self.course_b.id})
        res = self.client.patch(url, {"description": "Legacy teacher fallback works"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_legacy_teacher_sees_their_course_in_list(self):
        legacy_teacher = User.objects.create_user(username="p1_legacy_teacher2", password="pw", is_teacher=True, is_student=False)
        Enrollment.objects.create(user=legacy_teacher, course=self.course_b)
        self.course_b.is_published = False
        self.course_b.save()

        self.client.force_authenticate(user=legacy_teacher)
        res = self.client.get(reverse('course-list'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        course_ids = [c['id'] for c in res.data]
        self.assertIn(self.course_b.id, course_ids)

    # --- Item 12: Existing courses continue working (public/student read access) ---
    def test_published_course_still_publicly_readable(self):
        res = self.client.get(reverse('course-list'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        course_ids = [c['id'] for c in res.data]
        self.assertIn(self.course_a.id, course_ids)
        self.assertIn(self.course_b.id, course_ids)


class AssistantPermissionSplitTests(APITestCase):
    """
    Phase 1 (second pass): Assistant may manage lesson/module/audio content
    for a course it's assigned to, but must NOT be able to edit the course's
    own metadata (title/description/price/publish/delete) -- only TEACHER
    role (or admin) can. See IsSuperAdminOrCourseTeacherOrReadOnly.
    """

    def setUp(self):
        from .models import CourseInstructor
        self.CourseInstructor = CourseInstructor

        self.admin = User.objects.create_superuser(username="p1b_admin", password="pw")
        self.teacher = User.objects.create_user(username="p1b_teacher", password="pw", is_teacher=True, is_student=False)
        self.assistant_user = User.objects.create_user(username="p1b_assistant", password="pw", is_teacher=True, is_student=False)

        self.course = Course.objects.create(title="Assistant Split Course", price=100, is_published=True)
        self.module = Module.objects.create(course=self.course, title="Module 1", order=1)

        CourseInstructor.objects.create(course=self.course, user=self.teacher, role='TEACHER', is_primary=True)
        CourseInstructor.objects.create(course=self.course, user=self.assistant_user, role='ASSISTANT')

    def test_assistant_can_create_module(self):
        self.client.force_authenticate(user=self.assistant_user)
        res = self.client.post(reverse('module-list'), {"course": self.course.id, "title": "New Module", "order": 2})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_assistant_can_edit_existing_module(self):
        self.client.force_authenticate(user=self.assistant_user)
        res = self.client.patch(reverse('module-detail', kwargs={'pk': self.module.id}), {"title": "Renamed by assistant"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_assistant_cannot_edit_course_metadata(self):
        self.client.force_authenticate(user=self.assistant_user)
        res = self.client.patch(reverse('course-detail', kwargs={'pk': self.course.id}), {"description": "Assistant should not be able to do this"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.course.refresh_from_db()
        self.assertNotEqual(self.course.description, "Assistant should not be able to do this")

    def test_assistant_cannot_publish_course(self):
        self.client.force_authenticate(user=self.assistant_user)
        res = self.client.patch(reverse('course-detail', kwargs={'pk': self.course.id}), {"is_published": False})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_can_still_edit_course_metadata(self):
        # Confirms the split didn't accidentally narrow TEACHER's own access.
        self.client.force_authenticate(user=self.teacher)
        res = self.client.patch(reverse('course-detail', kwargs={'pk': self.course.id}), {"description": "Teacher update"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_teacher_can_still_edit_modules(self):
        self.client.force_authenticate(user=self.teacher)
        res = self.client.patch(reverse('module-detail', kwargs={'pk': self.module.id}), {"title": "Renamed by teacher"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_mentor_gets_no_course_editing_at_all(self):
        mentor = User.objects.create_user(username="p1b_mentor", password="pw", is_mentor=True, is_student=False)
        self.CourseInstructor.objects.create(course=self.course, user=mentor, role='MENTOR')
        self.client.force_authenticate(user=mentor)

        res_course = self.client.patch(reverse('course-detail', kwargs={'pk': self.course.id}), {"description": "no"})
        self.assertEqual(res_course.status_code, status.HTTP_403_FORBIDDEN)

        res_module = self.client.patch(reverse('module-detail', kwargs={'pk': self.module.id}), {"title": "no"})
        self.assertEqual(res_module.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_gets_no_course_editing(self):
        student = User.objects.create_user(username="p1b_student", password="pw")
        self.client.force_authenticate(user=student)
        res = self.client.patch(reverse('course-detail', kwargs={'pk': self.course.id}), {"description": "no"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


# ============================================================================
# PHASE 2: LIVE CLASS SYSTEM
# ============================================================================
# Uses plain APITestCase (DB-transaction-per-test, never actually committed)
# for pure permission/status-code/validation checks -- transaction.on_commit
# callbacks (notification + reminder dispatch) simply never fire in that
# case, so no Celery/Redis mocking is needed there. Any test that asserts on
# the *content* of a dispatched notification/reminder uses
# APITransactionTestCase (real commits) plus the established
# @patch('courses.tasks.send_class_reminder.apply_async') convention from
# LiveClassNotificationTests, since a live broker is not available in this
# environment.

class Phase2BatchSelfServiceTests(APITestCase):
    """Requirement: 'Teacher can create/manage classes assigned to them' /
    'Mentor can create/manage their own mentor sessions' -- via LiveBatch,
    since a LiveClass requires a batch."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username="p2_admin", password="pw")
        self.teacher1 = User.objects.create_user(username="p2_teacher1", password="pw", is_teacher=True, is_student=False)
        self.teacher2 = User.objects.create_user(username="p2_teacher2", password="pw", is_teacher=True, is_student=False)
        self.mentor = User.objects.create_user(username="p2_mentor", password="pw", is_mentor=True, is_student=False)
        self.student = User.objects.create_user(username="p2_student", password="pw")
        self.course = Course.objects.create(title="P2 Live Course", price=0, course_type='LIVE', is_published=True)

    def test_teacher_self_service_batch_create_defaults_to_self(self):
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.post(reverse('live-batch-list'), {"course": self.course.id, "batch_type": "GROUP"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['instructor'], self.teacher1.id)

    def test_mentor_self_service_batch_create_defaults_to_self(self):
        self.client.force_authenticate(user=self.mentor)
        res = self.client.post(reverse('live-batch-list'), {"course": self.course.id, "batch_type": "ONE_TO_ONE"})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['instructor'], self.mentor.id)

    def test_teacher_cannot_create_batch_on_behalf_of_another_teacher(self):
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.post(reverse('live-batch-list'), {"course": self.course.id, "batch_type": "GROUP", "instructor": self.teacher2.id})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("instructor", res.data)

    def test_admin_can_create_batch_for_any_instructor(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(reverse('live-batch-list'), {"course": self.course.id, "batch_type": "GROUP", "instructor": self.teacher1.id})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['instructor'], self.teacher1.id)

    def test_student_cannot_create_batch(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.post(reverse('live-batch-list'), {"course": self.course.id, "batch_type": "GROUP"})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_cannot_manage_another_teachers_batch(self):
        other_batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher2, batch_type='GROUP')
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.patch(reverse('live-batch-detail', kwargs={'pk': other_batch.id}), {"batch_type": "ONE_TO_ONE"})
        # get_queryset() already scopes a non-admin to their own batches, so
        # another teacher's batch isn't visible at all -> 404, not 403 (same
        # isolation pattern as IsSuperAdminOrAuthorizedTeacherOrReadOnly).
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_teacher_can_assign_and_remove_students_on_own_batch(self):
        batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher1, batch_type='GROUP')
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.post(reverse('live-batch-students', kwargs={'pk': batch.id}), {"student_id": self.student.id})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        res = self.client.delete(reverse('live-batch-remove-student', kwargs={'pk': batch.id, 'student_id': self.student.id}))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_teacher_cannot_assign_students_on_another_teachers_batch(self):
        other_batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher2, batch_type='GROUP')
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.post(reverse('live-batch-students', kwargs={'pk': other_batch.id}), {"student_id": self.student.id})
        # Same queryset-scoping isolation as above -- 404, not 403.
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_group_batch_capacity_enforced(self):
        batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher1, batch_type='GROUP', max_participants=1)
        s2 = User.objects.create_user(username="p2_student2", password="pw")
        self.client.force_authenticate(user=self.teacher1)
        res1 = self.client.post(reverse('live-batch-students', kwargs={'pk': batch.id}), {"student_id": self.student.id})
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)
        res2 = self.client.post(reverse('live-batch-students', kwargs={'pk': batch.id}), {"student_id": s2.id})
        self.assertEqual(res2.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("full", res2.data['error'])


class Phase2LiveClassSchedulingTests(APITestCase):
    """Scheduling + ownership enforcement for a single (non-recurring) LiveClass."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username="p2s_admin", password="pw")
        self.teacher1 = User.objects.create_user(username="p2s_teacher1", password="pw", is_teacher=True, is_student=False)
        self.teacher2 = User.objects.create_user(username="p2s_teacher2", password="pw", is_teacher=True, is_student=False)
        self.mentor = User.objects.create_user(username="p2s_mentor", password="pw", is_mentor=True, is_student=False)
        self.student = User.objects.create_user(username="p2s_student", password="pw")
        self.outsider_student = User.objects.create_user(username="p2s_outsider", password="pw")
        self.course = Course.objects.create(title="P2S Live Course", price=0, course_type='LIVE', is_published=True)

        self.teacher1_batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher1, batch_type='GROUP')
        self.mentor_batch = LiveBatch.objects.create(course=self.course, instructor=self.mentor, batch_type='ONE_TO_ONE')
        LiveBatchStudent.objects.create(batch=self.teacher1_batch, student=self.student)
        LiveBatchStudent.objects.create(batch=self.mentor_batch, student=self.student)

        self.future_time = timezone.now() + timezone.timedelta(days=3)

    def _payload(self, batch):
        return {
            "title": "P2 Session",
            "batch": batch.id,
            "scheduled_start": self.future_time.isoformat(),
            "duration_minutes": 60,
            "meeting_url": "http://zoom.us/p2test",
        }

    def test_teacher_can_schedule_on_own_batch_and_sees_meeting_url(self):
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.post(reverse('live-class-list'), self._payload(self.teacher1_batch))
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['meeting_url'], "http://zoom.us/p2test")

    def test_teacher_cannot_schedule_on_another_teachers_batch(self):
        other_batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher2, batch_type='GROUP')
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.post(reverse('live-class-list'), self._payload(other_batch))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("batch", res.data)

    def test_mentor_can_schedule_own_session_and_sees_meeting_url(self):
        """Regression check for the meeting_url-visibility bug fixed in Phase 2:
        a mentor conducting their own batch must see their own meeting_url."""
        self.client.force_authenticate(user=self.mentor)
        res = self.client.post(reverse('live-class-list'), self._payload(self.mentor_batch))
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['meeting_url'], "http://zoom.us/p2test")

    def test_mentor_cannot_schedule_on_teachers_batch(self):
        """Regression check for the ownership-check bug fixed in Phase 2: a
        mentor could previously create a LiveClass under ANY batch because
        only is_teacher was checked in LiveClassSerializer.validate()."""
        self.client.force_authenticate(user=self.mentor)
        res = self.client.post(reverse('live-class-list'), self._payload(self.teacher1_batch))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("batch", res.data)

    def test_student_cannot_schedule_a_class(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.post(reverse('live-class-list'), self._payload(self.teacher1_batch))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_does_not_see_meeting_url_unless_assigned(self):
        live_class = LiveClass.objects.create(
            course=self.course, batch=self.teacher1_batch, instructor=self.teacher1,
            title="Hidden", scheduled_start=self.future_time, duration_minutes=60,
            status='SCHEDULED', meeting_url="http://zoom.us/hidden"
        )
        self.client.force_authenticate(user=self.outsider_student)
        res = self.client.get(reverse('live-class-detail', kwargs={'pk': live_class.id}))
        # Outsider isn't assigned to the batch, so has_object_permission denies entirely.
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

        self.client.force_authenticate(user=self.student)
        res = self.client.get(reverse('live-class-detail', kwargs={'pk': live_class.id}))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['meeting_url'], "http://zoom.us/hidden")

    def test_overlapping_classes_for_same_instructor_rejected(self):
        LiveClass.objects.create(
            course=self.course, batch=self.teacher1_batch, instructor=self.teacher1,
            title="Existing", scheduled_start=self.future_time, duration_minutes=60,
            status='SCHEDULED', meeting_url="http://zoom.us/existing"
        )
        self.client.force_authenticate(user=self.teacher1)
        overlapping_payload = self._payload(self.teacher1_batch)
        overlapping_payload['scheduled_start'] = (self.future_time + timezone.timedelta(minutes=30)).isoformat()
        res = self.client.post(reverse('live-class-list'), overlapping_payload)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("scheduled_start", res.data)

    def test_cancel_with_reason_notifies_and_persists_reason(self):
        live_class = LiveClass.objects.create(
            course=self.course, batch=self.teacher1_batch, instructor=self.teacher1,
            title="To Cancel", scheduled_start=self.future_time, duration_minutes=60,
            status='SCHEDULED', meeting_url="http://zoom.us/cancel"
        )
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.post(reverse('live-class-cancel', args=[live_class.id]), {"reason": "Instructor unavailable"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        live_class.refresh_from_db()
        self.assertEqual(live_class.status, 'CANCELLED')
        self.assertEqual(live_class.cancellation_reason, "Instructor unavailable")

    def test_today_action_scopes_to_todays_classes_only(self):
        today_start = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        if today_start < timezone.now():
            today_start = timezone.now() + timezone.timedelta(minutes=5)
        LiveClass.objects.create(
            course=self.course, batch=self.teacher1_batch, instructor=self.teacher1,
            title="Today Session", scheduled_start=today_start, duration_minutes=60,
            status='SCHEDULED', meeting_url="http://zoom.us/today"
        )
        LiveClass.objects.create(
            course=self.course, batch=self.teacher1_batch, instructor=self.teacher1,
            title="Future Session", scheduled_start=self.future_time, duration_minutes=60,
            status='SCHEDULED', meeting_url="http://zoom.us/future"
        )
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.get(reverse('live-class-today'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        titles = [c['title'] for c in res.data['results']] if isinstance(res.data, dict) and 'results' in res.data else [c['title'] for c in res.data]
        self.assertIn("Today Session", titles)
        self.assertNotIn("Future Session", titles)


class Phase2AvailabilityTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="p2a_admin", password="pw")
        self.teacher = User.objects.create_user(username="p2a_teacher", password="pw", is_teacher=True, is_student=False)
        self.other_teacher = User.objects.create_user(username="p2a_teacher2", password="pw", is_teacher=True, is_student=False)
        self.course = Course.objects.create(title="P2A Live Course", price=0, course_type='LIVE', is_published=True)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type='GROUP')

    def test_teacher_self_service_create_omitting_user(self):
        self.client.force_authenticate(user=self.teacher)
        res = self.client.post(reverse('availability-list'), {
            "day_of_week": 0, "start_time": "09:00:00", "end_time": "12:00:00"
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['user'], self.teacher.id)

    def test_teacher_cannot_set_availability_for_another_user(self):
        self.client.force_authenticate(user=self.teacher)
        res = self.client.post(reverse('availability-list'), {
            "user": self.other_teacher.id, "day_of_week": 0, "start_time": "09:00:00", "end_time": "12:00:00"
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_can_create_on_behalf_of_teacher(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(reverse('availability-list'), {
            "user": self.teacher.id, "day_of_week": 1, "start_time": "09:00:00", "end_time": "12:00:00"
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data['user'], self.teacher.id)

    def test_end_time_before_start_time_rejected(self):
        self.client.force_authenticate(user=self.teacher)
        res = self.client.post(reverse('availability-list'), {
            "day_of_week": 0, "start_time": "12:00:00", "end_time": "09:00:00"
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_owner_cannot_delete_others_availability(self):
        window = TeacherAvailability.objects.create(user=self.teacher, day_of_week=0, start_time="09:00", end_time="12:00")
        self.client.force_authenticate(user=self.other_teacher)
        res = self.client.delete(reverse('availability-detail', kwargs={'pk': window.id}))
        # get_queryset() already scopes a non-admin to only their own rows,
        # so another teacher's window isn't visible at all -> 404. The
        # perform_destroy() ownership guard is defense-in-depth behind that.
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)
        self.assertTrue(TeacherAvailability.objects.filter(pk=window.id).exists())

    def test_owner_can_delete_own_availability(self):
        window = TeacherAvailability.objects.create(user=self.teacher, day_of_week=0, start_time="09:00", end_time="12:00")
        self.client.force_authenticate(user=self.teacher)
        res = self.client.delete(reverse('availability-detail', kwargs={'pk': window.id}))
        self.assertEqual(res.status_code, status.HTTP_204_NO_CONTENT)

    def test_scheduling_outside_configured_availability_rejected(self):
        # Pick a concrete future Monday and give the teacher a 09:00-12:00
        # window on Mondays only.
        import datetime
        base = timezone.now() + timezone.timedelta(days=10)
        days_ahead = (0 - base.weekday()) % 7  # next Monday
        monday = base + timezone.timedelta(days=days_ahead)
        TeacherAvailability.objects.create(user=self.teacher, day_of_week=0, start_time=datetime.time(9, 0), end_time=datetime.time(12, 0))

        self.client.force_authenticate(user=self.teacher)

        outside_window = timezone.make_aware(datetime.datetime.combine(monday.date(), datetime.time(14, 0)))
        res = self.client.post(reverse('live-class-list'), {
            "title": "Outside window", "batch": self.batch.id,
            "scheduled_start": outside_window.isoformat(), "duration_minutes": 60,
            "meeting_url": "http://zoom.us/x"
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("scheduled_start", res.data)

        inside_window = timezone.make_aware(datetime.datetime.combine(monday.date(), datetime.time(10, 0)))
        res = self.client.post(reverse('live-class-list'), {
            "title": "Inside window", "batch": self.batch.id,
            "scheduled_start": inside_window.isoformat(), "duration_minutes": 60,
            "meeting_url": "http://zoom.us/y"
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

    def test_no_availability_rows_means_unrestricted_backward_compat(self):
        # Teacher has zero TeacherAvailability rows -- any future time is fine.
        self.client.force_authenticate(user=self.teacher)
        future = timezone.now() + timezone.timedelta(days=5)
        res = self.client.post(reverse('live-class-list'), {
            "title": "Any time", "batch": self.batch.id,
            "scheduled_start": future.isoformat(), "duration_minutes": 60,
            "meeting_url": "http://zoom.us/z"
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)


class Phase2RecurringClassTests(APITransactionTestCase):
    """Recurring series creation, per-occurrence validation, and cancel-series.
    Uses APITransactionTestCase + mocked apply_async since class creation
    dispatches notifications/reminders via transaction.on_commit."""

    def setUp(self):
        self.teacher = User.objects.create_user(username="p2r_teacher", password="pw", is_teacher=True, is_student=False)
        self.student = User.objects.create_user(username="p2r_student", password="pw")
        self.course = Course.objects.create(title="P2R Live Course", price=0, course_type='LIVE', is_published=True)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type='GROUP')
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student)
        self.client.force_authenticate(user=self.teacher)
        self.start = timezone.now() + timezone.timedelta(days=7)

    @patch('courses.tasks.send_class_reminder.apply_async')
    def test_weekly_recurring_series_creates_linked_occurrences(self, mock_apply_async):
        payload = {
            "title": "Weekly Session",
            "batch": self.batch.id,
            "scheduled_start": self.start.isoformat(),
            "duration_minutes": 60,
            "meeting_url": "http://zoom.us/weekly",
            "recurrence": {"frequency": "WEEKLY", "weekdays": [self.start.weekday()], "occurrence_count": 3},
        }
        res = self.client.post(reverse('live-class-list'), payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data), 3)

        rule_ids = {c['recurrence_rule']['id'] for c in res.data}
        self.assertEqual(len(rule_ids), 1)  # all occurrences share one RecurrenceRule
        self.assertEqual(RecurrenceRule.objects.count(), 1)
        self.assertEqual(LiveClass.objects.filter(recurrence_rule_id=rule_ids.pop()).count(), 3)

    @patch('courses.tasks.send_class_reminder.apply_async')
    def test_recurring_series_conflict_rolls_back_entire_series(self, mock_apply_async):
        # Pre-existing class 14 days out collides with the 3rd weekly occurrence.
        LiveClass.objects.create(
            course=self.course, batch=self.batch, instructor=self.teacher,
            title="Blocker", scheduled_start=self.start + timezone.timedelta(days=14),
            duration_minutes=60, status='SCHEDULED', meeting_url="http://zoom.us/blocker"
        )
        payload = {
            "title": "Weekly Session",
            "batch": self.batch.id,
            "scheduled_start": self.start.isoformat(),
            "duration_minutes": 60,
            "meeting_url": "http://zoom.us/weekly",
            "recurrence": {"frequency": "WEEKLY", "weekdays": [self.start.weekday()], "occurrence_count": 3},
        }
        res = self.client.post(reverse('live-class-list'), payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        # Nothing from the failed series should have been persisted (atomic rollback).
        self.assertEqual(LiveClass.objects.filter(title="Weekly Session").count(), 0)
        self.assertEqual(RecurrenceRule.objects.count(), 0)

    @patch('courses.tasks.send_class_reminder.apply_async')
    def test_cancel_series_cancels_all_future_scheduled_occurrences(self, mock_apply_async):
        payload = {
            "title": "Weekly Session",
            "batch": self.batch.id,
            "scheduled_start": self.start.isoformat(),
            "duration_minutes": 60,
            "meeting_url": "http://zoom.us/weekly",
            "recurrence": {"frequency": "WEEKLY", "weekdays": [self.start.weekday()], "occurrence_count": 3},
        }
        res = self.client.post(reverse('live-class-list'), payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        occurrence_ids = [c['id'] for c in res.data]

        # Manually complete the first occurrence -- cancel-series must leave it alone.
        first = LiveClass.objects.get(pk=occurrence_ids[0])
        first.status = 'COMPLETED'
        first.save()

        res = self.client.post(reverse('live-class-cancel-series', args=[occurrence_ids[1]]), {"reason": "Series cancelled"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        first.refresh_from_db()
        self.assertEqual(first.status, 'COMPLETED')  # untouched
        second = LiveClass.objects.get(pk=occurrence_ids[1])
        third = LiveClass.objects.get(pk=occurrence_ids[2])
        self.assertEqual(second.status, 'CANCELLED')
        self.assertEqual(third.status, 'CANCELLED')
        self.assertEqual(second.cancellation_reason, "Series cancelled")

    def test_non_recurring_create_unaffected(self):
        payload = {
            "title": "One-off",
            "batch": self.batch.id,
            "scheduled_start": self.start.isoformat(),
            "duration_minutes": 60,
            "meeting_url": "http://zoom.us/oneoff",
        }
        with patch('courses.tasks.send_class_reminder.apply_async'):
            res = self.client.post(reverse('live-class-list'), payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(res.data.get('recurrence_rule'))


class Phase2AttendanceTests(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="p2at_teacher", password="pw", is_teacher=True, is_student=False)
        self.other_teacher = User.objects.create_user(username="p2at_teacher2", password="pw", is_teacher=True, is_student=False)
        self.student1 = User.objects.create_user(username="p2at_student1", password="pw")
        self.student2 = User.objects.create_user(username="p2at_student2", password="pw")
        self.outsider = User.objects.create_user(username="p2at_outsider", password="pw")
        self.course = Course.objects.create(title="P2AT Live Course", price=0, course_type='LIVE', is_published=True)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type='GROUP')
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student1)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student2)
        self.live_class = LiveClass.objects.create(
            course=self.course, batch=self.batch, instructor=self.teacher,
            title="Attendance Session", scheduled_start=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=60, status='SCHEDULED', meeting_url="http://zoom.us/att"
        )

    def test_instructor_can_mark_single_and_bulk_attendance(self):
        self.client.force_authenticate(user=self.teacher)
        url = reverse('live-class-attendance', args=[self.live_class.id])

        res = self.client.post(url, {"student": self.student1.id, "status": "PRESENT"}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        res = self.client.post(url, [
            {"student": self.student1.id, "status": "LATE"},
            {"student": self.student2.id, "status": "ABSENT"},
        ], format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(Attendance.objects.filter(live_class=self.live_class).count(), 2)
        self.assertEqual(Attendance.objects.get(live_class=self.live_class, student=self.student1).status, "LATE")

    def test_instructor_cannot_mark_attendance_for_non_batch_student(self):
        self.client.force_authenticate(user=self.teacher)
        url = reverse('live-class-attendance', args=[self.live_class.id])
        res = self.client.post(url, {"student": self.outsider.id, "status": "PRESENT"}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_cannot_mark_attendance(self):
        self.client.force_authenticate(user=self.student1)
        url = reverse('live-class-attendance', args=[self.live_class.id])
        res = self.client.post(url, {"student": self.student1.id, "status": "PRESENT"}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_other_teacher_cannot_mark_attendance(self):
        self.client.force_authenticate(user=self.other_teacher)
        url = reverse('live-class-attendance', args=[self.live_class.id])
        res = self.client.get(url)
        # Not the batch instructor and not assigned as a student -> object permission denies.
        self.assertIn(res.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

    def test_student_sees_only_own_attendance_row(self):
        Attendance.objects.create(live_class=self.live_class, student=self.student1, status='PRESENT', marked_by=self.teacher)
        Attendance.objects.create(live_class=self.live_class, student=self.student2, status='ABSENT', marked_by=self.teacher)

        self.client.force_authenticate(user=self.student1)
        url = reverse('live-class-attendance', args=[self.live_class.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]['student'], self.student1.id)

    def test_instructor_sees_all_attendance_rows(self):
        Attendance.objects.create(live_class=self.live_class, student=self.student1, status='PRESENT', marked_by=self.teacher)
        Attendance.objects.create(live_class=self.live_class, student=self.student2, status='ABSENT', marked_by=self.teacher)

        self.client.force_authenticate(user=self.teacher)
        url = reverse('live-class-attendance', args=[self.live_class.id])
        res = self.client.get(url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 2)


class Phase2RecordingTests(APITransactionTestCase):
    """Recording attach dispatches a notification per assigned student --
    needs a real commit, hence APITransactionTestCase (no apply_async
    involved here, so no mocking needed, but NotificationService writes are
    checked post-commit like the existing LiveClassNotificationTests do)."""

    def setUp(self):
        self.teacher = User.objects.create_user(username="p2rec_teacher", password="pw", is_teacher=True, is_student=False)
        self.other_teacher = User.objects.create_user(username="p2rec_teacher2", password="pw", is_teacher=True, is_student=False)
        self.student1 = User.objects.create_user(username="p2rec_student1", password="pw")
        self.course = Course.objects.create(title="P2REC Live Course", price=0, course_type='LIVE', is_published=True)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type='GROUP')
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student1)
        self.live_class = LiveClass.objects.create(
            course=self.course, batch=self.batch, instructor=self.teacher,
            title="Recorded Session", scheduled_start=timezone.now() - timezone.timedelta(days=1),
            duration_minutes=60, status='COMPLETED', meeting_url="http://zoom.us/rec"
        )

    def test_instructor_can_attach_recording_and_students_are_notified(self):
        from notifications.models import Notification, NotificationType

        self.client.force_authenticate(user=self.teacher)
        url = reverse('live-class-recording', args=[self.live_class.id])
        res = self.client.post(url, {"recording_url": "https://cdn.example.com/rec1.mp4"}, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.recording_url, "https://cdn.example.com/rec1.mp4")
        self.assertIsNotNone(self.live_class.recording_uploaded_at)

        notifs = Notification.objects.filter(recipient=self.student1, notification_type=NotificationType.LIVE_CLASS)
        self.assertEqual(notifs.filter(idempotency_key=f"liveclass:{self.live_class.id}:recording:{self.student1.id}").count(), 1)

    def test_recording_url_required(self):
        self.client.force_authenticate(user=self.teacher)
        url = reverse('live-class-recording', args=[self.live_class.id])
        res = self.client.post(url, {}, format='json')
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_instructor_cannot_attach_recording(self):
        self.client.force_authenticate(user=self.other_teacher)
        url = reverse('live-class-recording', args=[self.live_class.id])
        res = self.client.post(url, {"recording_url": "https://cdn.example.com/rec2.mp4"}, format='json')
        self.assertIn(res.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

    def test_student_cannot_attach_recording(self):
        self.client.force_authenticate(user=self.student1)
        url = reverse('live-class-recording', args=[self.live_class.id])
        res = self.client.post(url, {"recording_url": "https://cdn.example.com/rec3.mp4"}, format='json')
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)


class Phase2ReminderSchedulingTests(APITransactionTestCase):
    """Verifies _schedule_class_reminders (24h/1h/15m, reusing the single
    existing Celery task) is called with the right args, and skips any
    reminder whose ETA has already passed."""

    def setUp(self):
        self.teacher = User.objects.create_user(username="p2rem_teacher", password="pw", is_teacher=True, is_student=False)
        self.student = User.objects.create_user(username="p2rem_student", password="pw")
        self.course = Course.objects.create(title="P2REM Live Course", price=0, course_type='LIVE', is_published=True)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type='GROUP')
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student)
        self.client.force_authenticate(user=self.teacher)

    @patch('courses.tasks.send_class_reminder.apply_async')
    def test_all_three_reminders_scheduled_for_a_far_future_class(self, mock_apply_async):
        far_future = timezone.now() + timezone.timedelta(days=5)
        res = self.client.post(reverse('live-class-list'), {
            "title": "Far Future", "batch": self.batch.id,
            "scheduled_start": far_future.isoformat(), "duration_minutes": 60,
            "meeting_url": "http://zoom.us/far"
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        self.assertEqual(mock_apply_async.call_count, 3)
        reminder_types = {call.kwargs['args'][2] for call in mock_apply_async.call_args_list}
        self.assertEqual(reminder_types, {'24h', '1h', '15m'})

    @patch('courses.tasks.send_class_reminder.apply_async')
    def test_only_still_future_reminders_are_scheduled_for_a_near_class(self, mock_apply_async):
        near_future = timezone.now() + timezone.timedelta(minutes=30)
        res = self.client.post(reverse('live-class-list'), {
            "title": "Near Future", "batch": self.batch.id,
            "scheduled_start": near_future.isoformat(), "duration_minutes": 60,
            "meeting_url": "http://zoom.us/near"
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # Only the 15-minutes-before reminder still has a future ETA.
        self.assertEqual(mock_apply_async.call_count, 1)
        self.assertEqual(mock_apply_async.call_args_list[0].kwargs['args'][2], '15m')

    @patch('courses.tasks.send_class_reminder.apply_async')
    def test_reschedule_reschedules_reminders(self, mock_apply_async):
        live_class = LiveClass.objects.create(
            course=self.course, batch=self.batch, instructor=self.teacher,
            title="To Reschedule", scheduled_start=timezone.now() + timezone.timedelta(days=5),
            duration_minutes=60, status='SCHEDULED', meeting_url="http://zoom.us/resched"
        )
        mock_apply_async.reset_mock()
        new_time = timezone.now() + timezone.timedelta(days=6)
        res = self.client.post(reverse('live-class-reschedule', args=[live_class.id]), {"scheduled_start": new_time.isoformat()})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(mock_apply_async.call_count, 3)


class Phase2RealJWTPermissionTests(TestCase):
    """Explicit real-JWT-cookie verification (not force_authenticate) for the
    two most security-critical Phase 2 boundaries, mirroring the pattern
    used for the equivalent checks in Phase 0/1."""

    def _jwt_client(self, user):
        from rest_framework_simplejwt.tokens import RefreshToken
        client = DjangoClient()
        access = str(RefreshToken.for_user(user).access_token)
        client.cookies['natya-auth'] = access
        return client

    def setUp(self):
        self.teacher1 = User.objects.create_user(username="p2jwt_teacher1", password="pw", is_teacher=True, is_student=False)
        self.teacher2 = User.objects.create_user(username="p2jwt_teacher2", password="pw", is_teacher=True, is_student=False)
        self.student = User.objects.create_user(username="p2jwt_student", password="pw")
        self.course = Course.objects.create(title="P2JWT Live Course", price=0, course_type='LIVE', is_published=True)
        self.teacher1_batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher1, batch_type='GROUP')

    def test_real_jwt_teacher_blocked_from_another_teachers_batch(self):
        client = self._jwt_client(self.teacher2)
        future = timezone.now() + timezone.timedelta(days=3)
        res = client.post(
            '/api/courses/live-classes/',
            data={
                "title": "Cross-teacher attempt", "batch": self.teacher1_batch.id,
                "scheduled_start": future.isoformat(), "duration_minutes": 60,
                "meeting_url": "http://zoom.us/cross",
            },
        )
        self.assertEqual(res.status_code, 400)

    def test_real_jwt_student_cannot_create_live_class(self):
        client = self._jwt_client(self.student)
        future = timezone.now() + timezone.timedelta(days=3)
        res = client.post(
            '/api/courses/live-classes/',
            data={
                "title": "Student attempt", "batch": self.teacher1_batch.id,
                "scheduled_start": future.isoformat(), "duration_minutes": 60,
                "meeting_url": "http://zoom.us/studentattempt",
            },
        )
        self.assertEqual(res.status_code, 403)

    def test_real_jwt_teacher_can_create_on_own_batch(self):
        client = self._jwt_client(self.teacher1)
        future = timezone.now() + timezone.timedelta(days=3)
        res = client.post(
            '/api/courses/live-classes/',
            data={
                "title": "Own batch", "batch": self.teacher1_batch.id,
                "scheduled_start": future.isoformat(), "duration_minutes": 60,
                "meeting_url": "http://zoom.us/ownbatch",
            },
        )
        self.assertEqual(res.status_code, 201)
