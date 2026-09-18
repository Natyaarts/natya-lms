"""
Phase 3.9: Critical Defect & Security Remediation -- the `courses`-app
pieces: real course-progress calculation (replacing the frontend's
previously-hardcoded "0% Completed") and audit logging for
course-instructor assignment/removal.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import AdminAuditLog

from .models import Course, CourseInstructor, LessonProgress, Module, VideoLesson

User = get_user_model()


class CourseProgressPercentageTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='progress_student', password='pw')
        self.course = Course.objects.create(title='Progress Course', price=100, is_published=True)
        module = Module.objects.create(course=self.course, title='Module 1', order=1)
        self.lesson1 = VideoLesson.objects.create(module=module, title='L1', order=1)
        self.lesson2 = VideoLesson.objects.create(module=module, title='L2', order=2)
        self.lesson3 = VideoLesson.objects.create(module=module, title='L3', order=3)
        self.lesson4 = VideoLesson.objects.create(module=module, title='L4', order=4)
        from .models import Enrollment
        Enrollment.objects.create(user=self.student, course=self.course)

    def _my_courses(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('course-my-courses'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return next(c for c in response.data if c['id'] == self.course.id)

    def test_zero_percent_when_nothing_completed(self):
        course_data = self._my_courses()
        self.assertEqual(course_data['progress_percentage'], 0)

    def test_partial_completion_computed_correctly(self):
        LessonProgress.objects.create(user=self.student, lesson=self.lesson1, completed=True, video_duration=100, last_watched_position=100)
        LessonProgress.objects.create(user=self.student, lesson=self.lesson2, completed=True, video_duration=100, last_watched_position=100)
        # lesson3/lesson4 not completed -- 2 of 4 lessons = 50%
        course_data = self._my_courses()
        self.assertEqual(course_data['progress_percentage'], 50)

    def test_full_completion_is_100_percent(self):
        for lesson in (self.lesson1, self.lesson2, self.lesson3, self.lesson4):
            LessonProgress.objects.create(user=self.student, lesson=lesson, completed=True, video_duration=100, last_watched_position=100)
        course_data = self._my_courses()
        self.assertEqual(course_data['progress_percentage'], 100)

    def test_in_progress_but_not_completed_lesson_does_not_count(self):
        """A lesson merely watched partway (completed=False) must not
        inflate the percentage -- only `completed=True` counts, matching
        VideoLessonViewSet.progress's own existing completion rule
        exactly, never a separately-invented one."""
        LessonProgress.objects.create(user=self.student, lesson=self.lesson1, completed=True, video_duration=100, last_watched_position=100)
        LessonProgress.objects.create(user=self.student, lesson=self.lesson2, completed=False, video_duration=100, last_watched_position=80)
        course_data = self._my_courses()
        self.assertEqual(course_data['progress_percentage'], 25)  # 1 of 4, not 1.8 of 4

    def test_course_with_no_lessons_is_zero_not_error(self):
        empty_course = Course.objects.create(title='Empty Course', price=0, is_published=True)
        from .models import Enrollment
        Enrollment.objects.create(user=self.student, course=empty_course)
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('course-my-courses'))
        empty_data = next(c for c in response.data if c['id'] == empty_course.id)
        self.assertEqual(empty_data['progress_percentage'], 0)

    def test_one_students_progress_does_not_leak_into_another(self):
        other_student = User.objects.create_user(username='progress_other_student', password='pw')
        from .models import Enrollment
        Enrollment.objects.create(user=other_student, course=self.course)
        LessonProgress.objects.create(user=self.student, lesson=self.lesson1, completed=True, video_duration=100, last_watched_position=100)

        self.client.force_authenticate(other_student)
        response = self.client.get(reverse('course-my-courses'))
        other_data = next(c for c in response.data if c['id'] == self.course.id)
        self.assertEqual(other_data['progress_percentage'], 0)


class MyCoursesAuthenticationSecurityTests(APITestCase):
    """
    RBAC audit fix: CourseViewSet.my_courses previously allowed AllowAny
    and, for an anonymous caller, silently substituted
    get_user_model().objects.first() as the acting user -- disclosing a
    real (usually the earliest-created) account's accessible courses and
    per-course progress to anyone with no authentication at all. The
    endpoint is now IsAuthenticated with no anonymous fallback.
    """

    def setUp(self):
        self.student_a = User.objects.create_user(username='mycourses_student_a', password='pw')
        self.student_b = User.objects.create_user(username='mycourses_student_b', password='pw')
        self.course_a = Course.objects.create(title='Student A Course', price=100, is_published=True)
        self.course_b = Course.objects.create(title='Student B Course', price=100, is_published=True)
        from .models import Enrollment
        Enrollment.objects.create(user=self.student_a, course=self.course_a)
        Enrollment.objects.create(user=self.student_b, course=self.course_b)

    def test_unauthenticated_request_is_rejected(self):
        response = self.client.get(reverse('course-my-courses'))
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_student_cannot_see_another_students_courses(self):
        self.client.force_authenticate(self.student_a)
        response = self.client.get(reverse('course-my-courses'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {c['id'] for c in response.data}
        self.assertIn(self.course_a.id, returned_ids)
        self.assertNotIn(self.course_b.id, returned_ids)


class CourseInstructorAuditLogTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username='ci_audit_admin', password='pw')
        self.teacher = User.objects.create_user(username='ci_audit_teacher', password='pw', is_teacher=True)
        self.course = Course.objects.create(title='Audit Course', price=100, is_published=True)

    def test_assignment_creates_audit_log(self):
        self.client.force_authenticate(self.admin)
        url = reverse('course-instructors', kwargs={'pk': self.course.id})
        response = self.client.post(url, {'user': self.teacher.id, 'role': 'TEACHER', 'is_primary': True}, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        log = AdminAuditLog.objects.filter(action='COURSE_INSTRUCTOR_ASSIGNED', target_type='CourseInstructor').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.actor_id, self.admin.id)
        self.assertEqual(log.metadata['course_id'], self.course.id)
        self.assertEqual(log.metadata['user_id'], self.teacher.id)

    def test_removal_creates_audit_log(self):
        ci = CourseInstructor.objects.create(course=self.course, user=self.teacher, role='TEACHER')
        self.client.force_authenticate(self.admin)
        url = reverse('course-remove-instructor', kwargs={'pk': self.course.id, 'instructor_id': ci.id})
        response = self.client.delete(url)
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        log = AdminAuditLog.objects.filter(action='COURSE_INSTRUCTOR_REMOVED', target_type='CourseInstructor').first()
        self.assertIsNotNone(log)
        self.assertEqual(log.metadata['user_id'], self.teacher.id)

    def test_non_admin_cannot_assign_and_no_log_created(self):
        student = User.objects.create_user(username='ci_audit_student', password='pw')
        self.client.force_authenticate(student)
        url = reverse('course-instructors', kwargs={'pk': self.course.id})
        response = self.client.post(url, {'user': self.teacher.id, 'role': 'TEACHER'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(AdminAuditLog.objects.filter(action='COURSE_INSTRUCTOR_ASSIGNED').exists())
