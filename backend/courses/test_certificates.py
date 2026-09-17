"""
Phase 4.6: Course Completion Certificates.

Reuses the Phase 4.5 fixture helpers (courses/test_completion.py) rather
than duplicating them. Certificate eligibility itself is never
re-implemented here -- every test exercises the real
services.certificates functions and/or the real API endpoints.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import (
    Assessment, Certificate, Course, Enrollment, LessonProgress, Module, Question, QuestionOption, VideoLesson,
)
from courses.services.certificates import get_or_create_certificate, is_course_completed_for_user
from courses.test_assessment_attempts import make_subscription
from courses.test_completion import _add_lessons, _add_single_choice_assessment, _fail_assessment, _make_course, _pass_assessment
from orders.models import Subscription, SubscriptionPlan

User = get_user_model()


def _complete_lesson_only_course(student):
    course = _make_course()
    module = Module.objects.create(course=course, title="M1", order=1)
    lesson = _add_lessons(module, 1)[0]
    Enrollment.objects.create(user=student, course=course)
    LessonProgress.objects.create(user=student, lesson=lesson, completed=True, video_duration=10, last_watched_position=10)
    return course


class CertificateEligibilityTests(APITestCase):
    def setUp(self):
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.lesson = _add_lessons(self.module, 1)[0]
        self.assessment, self.question, self.correct, self.wrong = _add_single_choice_assessment(self.module, max_attempts=3)
        self.student = User.objects.create_user(username="cert_eligibility_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def _by_course(self):
        return self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))

    def test_incomplete_course_no_certificate(self):
        response = self._by_course()
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Certificate.objects.filter(student=self.student, course=self.course).exists())

    def test_completed_lessons_failed_assessment_no_certificate(self):
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)
        _fail_assessment(self.client, self.assessment, self.wrong, self.question)
        response = self._by_course()
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_completed_lessons_passed_assessment_is_eligible(self):
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)
        _pass_assessment(self.client, self.assessment, self.correct, self.question)
        response = self._by_course()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Certificate.objects.filter(student=self.student, course=self.course).exists())

    def test_all_modules_complete_is_eligible(self):
        module2 = Module.objects.create(course=self.course, title="M2", order=2)
        lesson2 = _add_lessons(module2, 1)[0]
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)
        LessonProgress.objects.create(user=self.student, lesson=lesson2, completed=True, video_duration=10, last_watched_position=10)
        _pass_assessment(self.client, self.assessment, self.correct, self.question)
        self.assertTrue(is_course_completed_for_user(self.course, self.student))

    def test_unpublished_assessment_does_not_block_eligibility(self):
        Assessment.objects.create(module=self.module, title="Draft", order=2, is_published=False)
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)
        _pass_assessment(self.client, self.assessment, self.correct, self.question)
        response = self._by_course()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_empty_module_follows_phase_45_vacuous_completion(self):
        Module.objects.create(course=self.course, title="Empty", order=2)
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)
        _pass_assessment(self.client, self.assessment, self.correct, self.question)
        response = self._by_course()
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_exhausted_failed_attempts_no_certificate(self):
        """A single-module course whose only assessment allows exactly
        one attempt: failing that one attempt exhausts it, and the
        course must remain permanently ineligible (no way to retry)."""
        course = _make_course()
        module = Module.objects.create(course=course, title="M1", order=1)
        lesson = _add_lessons(module, 1)[0]
        assessment, question, correct, wrong = _add_single_choice_assessment(module, max_attempts=1)
        Enrollment.objects.create(user=self.student, course=course)
        LessonProgress.objects.create(user=self.student, lesson=lesson, completed=True, video_duration=10, last_watched_position=10)

        _fail_assessment(self.client, assessment, wrong, question)  # max_attempts=1 -> now exhausted
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': course.id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(Certificate.objects.filter(student=self.student, course=course).exists())


class CertificateOwnershipTests(APITestCase):
    def setUp(self):
        self.student_a = User.objects.create_user(username="cert_owner_a", password="pw")
        self.student_b = User.objects.create_user(username="cert_owner_b", password="pw")
        self.course = _complete_lesson_only_course(self.student_a)

    def _generate_for_a(self):
        self.client.force_authenticate(self.student_a)
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def test_student_b_cannot_retrieve_student_a_certificate(self):
        cert_data = self._generate_for_a()
        self.client.force_authenticate(self.student_b)
        response = self.client.get(reverse('certificate-detail', kwargs={'pk': cert_data['id']}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_student_b_cannot_generate_certificate_for_student_a_course(self):
        """Student B has no access/progress on this course at all --
        hitting by_course as B must not create a certificate for A, and
        must not disturb A's own already-issued certificate."""
        self._generate_for_a()
        self.client.force_authenticate(self.student_b)
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Certificate.objects.filter(student=self.student_b).count(), 0)
        self.assertEqual(Certificate.objects.filter(student=self.student_a).count(), 1)

    def test_student_b_list_does_not_include_student_a_certificate(self):
        self._generate_for_a()
        self.client.force_authenticate(self.student_b)
        response = self.client.get(reverse('certificate-list'))
        self.assertEqual(response.data['count'] if isinstance(response.data, dict) else len(response.data), 0)

    def test_verification_does_not_expose_unrelated_certificates(self):
        """Verifying certificate X must never leak or list any other
        certificate."""
        cert_data = self._generate_for_a()
        other_course = _complete_lesson_only_course(self.student_b)
        self.client.force_authenticate(self.student_b)
        self.client.get(reverse('certificate-by-course', kwargs={'course_id': other_course.id}))
        other_cert = Certificate.objects.get(student=self.student_b)

        response = self.client.get(reverse('certificate-verify', kwargs={'verification_id': cert_data['verification_id']}))
        self.assertEqual(response.data['learner_name_snapshot'], cert_data['learner_name_snapshot'])
        self.assertNotEqual(response.data['verification_id'], other_cert.verification_id)


class CertificateIdempotencyTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="cert_idempotent_student", password="pw")
        self.course = _complete_lesson_only_course(self.student)
        self.client.force_authenticate(self.student)

    def test_repeated_generation_returns_same_certificate(self):
        r1 = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        r2 = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        r3 = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        self.assertEqual(r1.data['id'], r2.data['id'])
        self.assertEqual(r2.data['id'], r3.data['id'])
        self.assertEqual(r1.data['verification_id'], r3.data['verification_id'])
        self.assertEqual(Certificate.objects.filter(student=self.student, course=self.course).count(), 1)

    def test_duplicate_creation_prevented_at_db_level(self):
        cert1, created1 = get_or_create_certificate(self.course, self.student)
        cert2, created2 = get_or_create_certificate(self.course, self.student)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(cert1.id, cert2.id)
        self.assertEqual(Certificate.objects.filter(student=self.student, course=self.course).count(), 1)

    def test_direct_duplicate_create_violates_unique_constraint(self):
        from django.db import IntegrityError
        Certificate.objects.create(student=self.student, course=self.course, learner_name_snapshot="X", course_title_snapshot="Y")
        with self.assertRaises(IntegrityError):
            Certificate.objects.create(student=self.student, course=self.course, learner_name_snapshot="X2", course_title_snapshot="Y2")


class CertificateHistoricalIntegrityTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="cert_history_student", password="pw", first_name="Original", last_name="Name")
        self.course = _make_course()
        self.course.title = "Original Course Title"
        self.course.save()
        module = Module.objects.create(course=self.course, title="M1", order=1)
        lesson = _add_lessons(module, 1)[0]
        Enrollment.objects.create(user=self.student, course=self.course)
        LessonProgress.objects.create(user=self.student, lesson=lesson, completed=True, video_duration=10, last_watched_position=10)
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        self.certificate_id = response.data['id']

    def test_course_title_change_does_not_change_snapshot(self):
        self.course.title = "Renamed Course Title"
        self.course.save()
        response = self.client.get(reverse('certificate-detail', kwargs={'pk': self.certificate_id}))
        self.assertEqual(response.data['course_title_snapshot'], "Original Course Title")

    def test_learner_name_change_does_not_change_snapshot(self):
        self.student.first_name = "Changed"
        self.student.last_name = "Person"
        self.student.save()
        response = self.client.get(reverse('certificate-detail', kwargs={'pk': self.certificate_id}))
        self.assertEqual(response.data['learner_name_snapshot'], "Original Name")

    def test_no_assessment_data_ever_stored_on_certificate(self):
        """Certificates store no score/percentage/marks at all -- so
        changing assessment content can never affect certificate
        history, because there is nothing assessment-derived to change."""
        certificate = Certificate.objects.get(pk=self.certificate_id)
        field_names = {f.name for f in Certificate._meta.get_fields()}
        for forbidden in ('score', 'percentage', 'marks', 'passed', 'grade'):
            self.assertNotIn(forbidden, field_names)


class CertificateVerificationTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="cert_verify_student", password="pw")
        self.course = _complete_lesson_only_course(self.student)
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        self.verification_id = response.data['verification_id']

    def test_valid_verification_id_returns_valid_certificate(self):
        anon_client = self.client_class()
        response = anon_client.get(reverse('certificate-verify', kwargs={'verification_id': self.verification_id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data['valid'])
        self.assertEqual(response.data['course_title_snapshot'], self.course.title)

    def test_random_nonexistent_id_is_invalid(self):
        anon_client = self.client_class()
        response = anon_client.get(reverse('certificate-verify', kwargs={'verification_id': 'CERT-DOESNOTEXIST'}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(response.data['valid'])

    def test_verification_does_not_expose_sensitive_fields(self):
        anon_client = self.client_class()
        response = anon_client.get(reverse('certificate-verify', kwargs={'verification_id': self.verification_id}))
        for forbidden in ('id', 'student', 'course', 'course_id', 'email', 'phone_number'):
            self.assertNotIn(forbidden, response.data)

    def test_verification_works_without_authentication(self):
        anon_client = self.client_class()
        response = anon_client.get(reverse('certificate-verify', kwargs={'verification_id': self.verification_id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CertificateAccessTests(APITestCase):
    def setUp(self):
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.lesson = _add_lessons(self.module, 1)[0]

    def test_no_access_student_gets_no_certificate(self):
        student = User.objects.create_user(username="cert_no_access_student", password="pw")
        self.client.force_authenticate(student)
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_anonymous_cannot_generate_certificate(self):
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_admin_bypass_does_not_fabricate_eligibility(self):
        """A staff/superuser has bypass_content_lock for VIEWING, but has
        no LessonProgress/AssessmentAttempt of their own -- must not
        receive a certificate just because they can see full content."""
        admin = User.objects.create_superuser(username="cert_admin", password="pw")
        self.client.force_authenticate(admin)
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(Certificate.objects.filter(student=admin).count(), 0)

    def test_instructor_access_does_not_grant_certificate(self):
        from courses.models import CourseInstructor
        teacher = User.objects.create_user(username="cert_instructor", password="pw", is_teacher=True)
        CourseInstructor.objects.create(course=self.course, user=teacher, role='TEACHER')
        self.client.force_authenticate(teacher)
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_valid_subscription_student_can_earn_certificate(self):
        plan = SubscriptionPlan.objects.create(name="Cert Plan", billing_interval="MONTHLY", price="99.00", razorpay_plan_id="plan_cert_1")
        plan.courses.add(self.course)
        student = User.objects.create_user(username="cert_sub_student", password="pw")
        make_subscription(student, plan, Subscription.Status.ACTIVE, timezone.now() + timedelta(days=10))
        LessonProgress.objects.create(user=student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)
        self.client.force_authenticate(student)
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class CertificateEagerTriggerTests(APITestCase):
    """The two automatic trigger points -- final lesson completion, and
    a passing assessment submission -- must issue the certificate and
    notification without any explicit certificate-endpoint call."""

    def test_final_lesson_completion_triggers_certificate(self):
        student = User.objects.create_user(username="cert_eager_lesson_student", password="pw")
        course = _make_course()
        module = Module.objects.create(course=course, title="M1", order=1)
        lesson = VideoLesson.objects.create(module=module, title="L1", order=1)
        Enrollment.objects.create(user=student, course=course)
        self.client.force_authenticate(student)

        response = self.client.post(
            reverse('lesson-progress', kwargs={'pk': lesson.id}),
            {"last_watched_position": 10, "video_duration": 10, "completed": True}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(Certificate.objects.filter(student=student, course=course).exists())

        from notifications.models import Notification, NotificationType
        self.assertTrue(Notification.objects.filter(recipient=student, notification_type=NotificationType.CERTIFICATE).exists())

    def test_passing_assessment_triggers_certificate(self):
        student = User.objects.create_user(username="cert_eager_assessment_student", password="pw")
        course = _make_course()
        module = Module.objects.create(course=course, title="M1", order=1)
        assessment, question, correct, wrong = _add_single_choice_assessment(module, max_attempts=3)
        Enrollment.objects.create(user=student, course=course)
        self.client.force_authenticate(student)

        _pass_assessment(self.client, assessment, correct, question)
        self.assertTrue(Certificate.objects.filter(student=student, course=course).exists())

    def test_failing_assessment_does_not_trigger_certificate(self):
        student = User.objects.create_user(username="cert_eager_fail_student", password="pw")
        course = _make_course()
        module = Module.objects.create(course=course, title="M1", order=1)
        assessment, question, correct, wrong = _add_single_choice_assessment(module, max_attempts=3)
        Enrollment.objects.create(user=student, course=course)
        self.client.force_authenticate(student)

        _fail_assessment(self.client, assessment, wrong, question)
        self.assertFalse(Certificate.objects.filter(student=student, course=course).exists())


class CertificateAPITests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username="cert_api_student", password="pw")
        self.course = _complete_lesson_only_course(self.student)
        self.client.force_authenticate(self.student)

    def test_list_endpoint_returns_own_certificates(self):
        self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        response = self.client.get(reverse('certificate-list'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        results = response.data['results'] if isinstance(response.data, dict) and 'results' in response.data else response.data
        self.assertEqual(len(results), 1)

    def test_retrieve_endpoint_returns_certificate(self):
        by_course = self.client.get(reverse('certificate-by-course', kwargs={'course_id': self.course.id}))
        response = self.client.get(reverse('certificate-detail', kwargs={'pk': by_course.data['id']}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['verification_id'], by_course.data['verification_id'])

    def test_by_course_nonexistent_course_404s(self):
        response = self.client.get(reverse('certificate-by-course', kwargs={'course_id': 999999}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class CertificateQueryCountTests(TestCase):
    """Section 20: certificate generation/listing must not repeatedly
    query every course module/lesson/assessment -- query count for
    is_course_completed_for_user must stay fixed regardless of module
    count."""

    def _course_with_n_modules(self, n):
        course = _make_course()
        for i in range(n):
            module = Module.objects.create(course=course, title=f"M{i}", order=i)
            _add_lessons(module, 2)
            _add_single_choice_assessment(module, order=100 + i)
        return course

    def test_eligibility_check_query_count_bounded_by_module_count(self):
        from rest_framework.test import APIClient
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        student = User.objects.create_user(username="cert_perf_student", password="pw")

        small_course = self._course_with_n_modules(1)
        Enrollment.objects.create(user=student, course=small_course)
        with CaptureQueriesContext(connection) as ctx_small:
            is_course_completed_for_user(small_course, student)
        small_count = len(ctx_small.captured_queries)

        large_course = self._course_with_n_modules(6)
        Enrollment.objects.create(user=student, course=large_course)
        with CaptureQueriesContext(connection) as ctx_large:
            is_course_completed_for_user(large_course, student)
        large_count = len(ctx_large.captured_queries)

        # 6x the modules, 12x the lessons, 6x the assessments -- must NOT
        # scale anywhere near that; the whole point is 5 fixed queries
        # regardless of module count.
        self.assertEqual(small_count, large_count)

    def test_certificate_list_query_count_does_not_scale_with_certificate_count(self):
        from rest_framework.test import APIClient

        student = User.objects.create_user(username="cert_list_perf_student", password="pw")
        client = APIClient()
        client.force_authenticate(student)

        for i in range(5):
            course = _complete_lesson_only_course_for(student, i)
            get_or_create_certificate(course, student)

        from django.test.utils import CaptureQueriesContext
        from django.db import connection
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(reverse('certificate-list'))
        self.assertEqual(response.status_code, 200)
        # select_related('course') keeps this to a small, fixed number of
        # queries regardless of how many certificates this student has.
        self.assertLessEqual(len(ctx.captured_queries), 5)


def _complete_lesson_only_course_for(student, suffix):
    course = Course.objects.create(title=f"Perf List Course {suffix}", price=1, is_published=True)
    module = Module.objects.create(course=course, title="M1", order=1)
    lesson = VideoLesson.objects.create(module=module, title="L1", order=1)
    Enrollment.objects.create(user=student, course=course)
    LessonProgress.objects.create(user=student, lesson=lesson, completed=True, video_duration=10, last_watched_position=10)
    return course
