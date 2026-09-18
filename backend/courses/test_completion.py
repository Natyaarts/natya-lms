"""
Phase 4.5: Course & Module Completion.

Completion is entirely backend-derived, computed live in
ModuleSerializer/CourseSerializer.to_representation via
courses/services/completion.py -- no stored completion flags exist
anywhere (Course/Module gained no new fields or migration this phase).

Reuses the Phase 4.2 fixture helpers where useful (build_mixed_assessment
etc. -- see courses/test_assessment_attempts.py) rather than duplicating
them.
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
    Assessment, AssessmentAttempt, Course, Enrollment, LessonProgress, Module, Question, QuestionOption, VideoLesson,
)
from courses.test_assessment_attempts import make_subscription
from orders.models import Subscription, SubscriptionPlan
from django.core.cache import cache

User = get_user_model()


def _course_detail(client, course_id):
    return client.get(reverse('course-detail', kwargs={'pk': course_id}))


def _module_data(response, module_id):
    return next(m for m in response.data['modules'] if m['id'] == module_id)


def _make_course(course_type=Course.CourseType.RECORDED):
    return Course.objects.create(title="Completion Test Course", description="x", price=10, is_published=True, course_type=course_type)


def _add_lessons(module, n):
    return [VideoLesson.objects.create(module=module, title=f"L{i}", order=i) for i in range(n)]


def _add_single_choice_assessment(module, order=1, max_attempts=3, passing_percentage=Decimal("50.00")):
    assessment = Assessment.objects.create(
        module=module, title="Quiz", order=order, is_published=True,
        max_attempts=max_attempts, passing_percentage=passing_percentage,
    )
    q = Question.objects.create(assessment=assessment, question_text="Q1", question_type=Question.QuestionType.SINGLE_CHOICE, order=1, marks=Decimal("1.00"))
    correct = QuestionOption.objects.create(question=q, option_text="A", order=1, is_correct=True)
    wrong = QuestionOption.objects.create(question=q, option_text="B", order=2, is_correct=False)
    return assessment, q, correct, wrong


def _pass_assessment(client, assessment, correct_option, question):
    start = client.post(reverse('assessment-start', kwargs={'pk': assessment.id}))
    return client.post(
        reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
        {"answers": [{"question_id": question.id, "option_ids": [correct_option.id]}]}, format='json',
    )


def _fail_assessment(client, assessment, wrong_option, question):
    start = client.post(reverse('assessment-start', kwargs={'pk': assessment.id}))
    return client.post(
        reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
        {"answers": [{"question_id": question.id, "option_ids": [wrong_option.id]}]}, format='json',
    )


class LessonOnlyModuleCompletionTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.l1, self.l2 = _add_lessons(self.module, 2)
        self.student = User.objects.create_user(username="lesson_only_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def test_incomplete_when_no_lessons_done(self):
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertFalse(m['is_completed'])
        self.assertEqual(m['completed_item_count'], 0)
        self.assertEqual(m['total_item_count'], 2)
        self.assertEqual(m['completion_percentage'], 0)

    def test_incomplete_when_partially_done(self):
        LessonProgress.objects.create(user=self.student, lesson=self.l1, completed=True, video_duration=10, last_watched_position=10)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertFalse(m['is_completed'])
        self.assertEqual(m['completion_percentage'], 50)
        lesson_entry = next(l for l in m['lessons'] if l['id'] == self.l1.id)
        self.assertTrue(lesson_entry['is_completed'])
        other_entry = next(l for l in m['lessons'] if l['id'] == self.l2.id)
        self.assertFalse(other_entry['is_completed'])

    def test_module_complete_when_all_lessons_complete(self):
        for lesson in (self.l1, self.l2):
            LessonProgress.objects.create(user=self.student, lesson=lesson, completed=True, video_duration=10, last_watched_position=10)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertTrue(m['is_completed'])
        self.assertEqual(m['completion_percentage'], 100)


class AssessmentOnlyModuleCompletionTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.assessment, self.question, self.correct, self.wrong = _add_single_choice_assessment(self.module, max_attempts=3)
        self.student = User.objects.create_user(username="assessment_only_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def test_no_attempt_is_incomplete(self):
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertFalse(m['is_completed'])
        self.assertEqual(m['completed_item_count'], 0)
        self.assertEqual(m['total_item_count'], 1)

    def test_in_progress_attempt_is_incomplete(self):
        self.client.post(reverse('assessment-start', kwargs={'pk': self.assessment.id}))
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertFalse(m['is_completed'])

    def test_failed_attempt_is_incomplete(self):
        _fail_assessment(self.client, self.assessment, self.wrong, self.question)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertFalse(m['is_completed'])

    def test_passed_attempt_is_complete(self):
        _pass_assessment(self.client, self.assessment, self.correct, self.question)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertTrue(m['is_completed'])
        self.assertEqual(m['completion_percentage'], 100)


class MixedModuleCompletionTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.lesson = _add_lessons(self.module, 1)[0]
        self.assessment, self.question, self.correct, self.wrong = _add_single_choice_assessment(self.module, max_attempts=3)
        self.student = User.objects.create_user(username="mixed_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def _complete_lesson(self):
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)

    def test_lessons_complete_assessment_failed_is_incomplete(self):
        self._complete_lesson()
        _fail_assessment(self.client, self.assessment, self.wrong, self.question)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertFalse(m['is_completed'])
        self.assertEqual(m['completed_item_count'], 1)
        self.assertEqual(m['total_item_count'], 2)

    def test_lessons_incomplete_assessment_passed_is_incomplete(self):
        _pass_assessment(self.client, self.assessment, self.correct, self.question)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertFalse(m['is_completed'])
        self.assertEqual(m['completed_item_count'], 1)

    def test_everything_complete_is_complete(self):
        self._complete_lesson()
        _pass_assessment(self.client, self.assessment, self.correct, self.question)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertTrue(m['is_completed'])
        self.assertEqual(m['completion_percentage'], 100)


class UnpublishedContentTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.lesson = _add_lessons(self.module, 1)[0]
        self.student = User.objects.create_user(username="unpub_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)

    def test_unpublished_assessment_does_not_block_completion(self):
        Assessment.objects.create(module=self.module, title="Draft Quiz", order=2, is_published=False)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        # Only the (completed) lesson counts -- the unpublished assessment
        # never enters get_assessments' output at all.
        self.assertTrue(m['is_completed'])
        self.assertEqual(m['total_item_count'], 1)


class MaxAttemptsCompletionTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.assessment, self.question, self.correct, self.wrong = _add_single_choice_assessment(self.module, max_attempts=2)
        self.student = User.objects.create_user(username="maxattempts_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def test_exhausted_failed_attempts_do_not_complete_module_or_course(self):
        _fail_assessment(self.client, self.assessment, self.wrong, self.question)
        _fail_assessment(self.client, self.assessment, self.wrong, self.question)  # max_attempts=2, now exhausted
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertFalse(m['is_completed'])
        self.assertFalse(response.data['is_completed'])


class CourseCompletionTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module1 = Module.objects.create(course=self.course, title="M1", order=1)
        self.module2 = Module.objects.create(course=self.course, title="M2", order=2)
        self.lesson1 = _add_lessons(self.module1, 1)[0]
        self.lesson2 = _add_lessons(self.module2, 1)[0]
        self.student = User.objects.create_user(username="course_completion_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def test_one_incomplete_module_blocks_course_completion(self):
        LessonProgress.objects.create(user=self.student, lesson=self.lesson1, completed=True, video_duration=10, last_watched_position=10)
        # module2's lesson left incomplete
        response = _course_detail(self.client, self.course.id)
        self.assertFalse(response.data['is_completed'])
        self.assertEqual(response.data['completed_module_count'], 1)
        self.assertEqual(response.data['total_module_count'], 2)

    def test_all_modules_complete_course_complete(self):
        for lesson in (self.lesson1, self.lesson2):
            LessonProgress.objects.create(user=self.student, lesson=lesson, completed=True, video_duration=10, last_watched_position=10)
        response = _course_detail(self.client, self.course.id)
        self.assertTrue(response.data['is_completed'])
        self.assertEqual(response.data['completion_percentage'], 100)


class EmptyModuleTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.empty_module = Module.objects.create(course=self.course, title="Empty", order=1)
        self.real_module = Module.objects.create(course=self.course, title="Real", order=2)
        self.lesson = _add_lessons(self.real_module, 1)[0]
        self.student = User.objects.create_user(username="empty_module_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def test_empty_module_is_vacuously_complete(self):
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.empty_module.id)
        self.assertTrue(m['is_completed'])
        self.assertEqual(m['completion_percentage'], 100)
        self.assertEqual(m['total_item_count'], 0)
        self.assertEqual(m['completed_item_count'], 0)

    def test_empty_module_does_not_block_course_completion(self):
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)
        response = _course_detail(self.client, self.course.id)
        self.assertTrue(response.data['is_completed'])
        self.assertEqual(response.data['completed_module_count'], 2)
        self.assertEqual(response.data['total_module_count'], 2)

    def test_course_with_zero_modules_is_vacuously_complete(self):
        bare_course = Course.objects.create(title="Bare Course", price=1, is_published=True)
        Enrollment.objects.create(user=self.student, course=bare_course)
        response = _course_detail(self.client, bare_course.id)
        self.assertTrue(response.data['is_completed'])
        self.assertEqual(response.data['completion_percentage'], 100)
        self.assertEqual(response.data['total_module_count'], 0)


class PercentageTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.lessons = _add_lessons(self.module, 3)
        self.student = User.objects.create_user(username="percentage_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def test_module_percentage_rounds_correctly(self):
        # 1 of 3 complete = 33.33...% -> rounds to 33
        LessonProgress.objects.create(user=self.student, lesson=self.lessons[0], completed=True, video_duration=10, last_watched_position=10)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertEqual(m['completion_percentage'], 33)

    def test_two_of_three_rounds_correctly(self):
        # 2 of 3 = 66.66...% -> rounds to 67
        for lesson in self.lessons[:2]:
            LessonProgress.objects.create(user=self.student, lesson=lesson, completed=True, video_duration=10, last_watched_position=10)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertEqual(m['completion_percentage'], 67)

    def test_percentage_is_an_integer_not_float_noise(self):
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertIsInstance(m['completion_percentage'], int)


class OwnershipTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.lesson = _add_lessons(self.module, 1)[0]
        self.student_a = User.objects.create_user(username="ownership_student_a", password="pw")
        self.student_b = User.objects.create_user(username="ownership_student_b", password="pw")
        Enrollment.objects.create(user=self.student_a, course=self.course)
        Enrollment.objects.create(user=self.student_b, course=self.course)

    def test_student_b_does_not_see_student_a_progress(self):
        LessonProgress.objects.create(user=self.student_a, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)

        self.client.force_authenticate(self.student_b)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertFalse(m['is_completed'])
        lesson_entry = m['lessons'][0]
        self.assertFalse(lesson_entry['is_completed'])

        self.client.force_authenticate(self.student_a)
        response_a = _course_detail(self.client, self.course.id)
        m_a = _module_data(response_a, self.module.id)
        self.assertTrue(m_a['is_completed'])


class AccessTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        _add_lessons(self.module, 1)

    def test_no_access_student_gets_none_completion(self):
        student = User.objects.create_user(username="no_access_student", password="pw")
        self.client.force_authenticate(student)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertIsNone(m['is_completed'])
        self.assertIsNone(m['completion_percentage'])
        self.assertIsNone(m['completed_item_count'])
        self.assertIsNone(m['total_item_count'])
        self.assertIsNone(response.data['is_completed'])
        self.assertIsNone(response.data['completion_percentage'])

    def test_anonymous_gets_none_completion(self):
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertIsNone(m['is_completed'])
        self.assertIsNone(response.data['is_completed'])

    def test_valid_subscription_student_gets_real_completion(self):
        plan = SubscriptionPlan.objects.create(name="Completion Plan", billing_interval="MONTHLY", price="99.00", razorpay_plan_id="plan_completion_1")
        plan.courses.add(self.course)
        student = User.objects.create_user(username="sub_completion_student", password="pw")
        make_subscription(student, plan, Subscription.Status.ACTIVE, timezone.now() + timedelta(days=10))
        self.client.force_authenticate(student)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertIsNotNone(m['is_completed'])
        self.assertFalse(m['is_completed'])  # real, computed value -- not yet complete

    def test_admin_access_does_not_fabricate_completion(self):
        """An admin/staff viewer has bypass_content_lock (sees full
        content), but completion is still THEIR OWN (empty) progress --
        never a student's, and never fabricated as 100%."""
        admin = User.objects.create_superuser(username="completion_admin", password="pw")
        self.client.force_authenticate(admin)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        self.assertIsNotNone(m['is_completed'])
        self.assertFalse(m['is_completed'])  # admin has no LessonProgress of their own


class RegressionTests(APITestCase):
    """Spot checks that Phase 4.2/4.3/4.4 behavior is untouched by this
    phase's additions -- the full suites are also run separately."""

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.assessment, self.question, self.correct, self.wrong = _add_single_choice_assessment(self.module, max_attempts=3)
        self.student = User.objects.create_user(username="regression_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def test_scoring_result_fields_unaffected(self):
        response = _pass_assessment(self.client, self.assessment, self.correct, self.question)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(str(response.data['score'])), Decimal("1.00"))
        self.assertTrue(response.data['passed'])

    def test_assessment_summary_status_field_still_correct(self):
        _pass_assessment(self.client, self.assessment, self.correct, self.question)
        response = _course_detail(self.client, self.course.id)
        m = _module_data(response, self.module.id)
        assessment_entry = m['assessments'][0]
        self.assertEqual(assessment_entry['status'], "PASSED")

    def test_review_data_unaffected_by_completion_fields(self):
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.assessment.id}))
        submit = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
            {"answers": [{"question_id": self.question.id, "option_ids": [self.correct.id]}]}, format='json',
        )
        self.assertIn("questions", submit.data)
        self.assertEqual(submit.data["questions"][0]["answered_correctly"], True)


class CompletionQueryCountTests(TestCase):
    """Section 9/17: query count for the main course-learning response
    must stay bounded (a small fixed number of context queries + one
    query per model already tolerated by this endpoint), never scaling
    with the number of modules, lessons, or assessments."""

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: see other setUp() comments in this file.

    def _course_with_shape(self, n_modules, n_lessons_per_module, n_assessments_per_module):
        course = Course.objects.create(title="Perf Completion Course", price=1, is_published=True, course_type=Course.CourseType.RECORDED)
        for m_idx in range(n_modules):
            module = Module.objects.create(course=course, title=f"M{m_idx}", order=m_idx)
            _add_lessons(module, n_lessons_per_module)
            for a_idx in range(n_assessments_per_module):
                _add_single_choice_assessment(module, order=a_idx)
        return course

    def test_query_count_bounded_as_modules_and_items_increase(self):
        from rest_framework.test import APIClient
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        student = User.objects.create_user(username="completion_perf_student", password="pw")
        client = APIClient()
        client.force_authenticate(student)

        small_course = self._course_with_shape(n_modules=1, n_lessons_per_module=1, n_assessments_per_module=1)
        Enrollment.objects.create(user=student, course=small_course)
        with CaptureQueriesContext(connection) as ctx_small:
            r_small = client.get(reverse('course-detail', kwargs={'pk': small_course.id}))
        self.assertEqual(r_small.status_code, 200)
        small_count = len(ctx_small.captured_queries)

        large_course = self._course_with_shape(n_modules=4, n_lessons_per_module=3, n_assessments_per_module=2)
        Enrollment.objects.create(user=student, course=large_course)
        with CaptureQueriesContext(connection) as ctx_large:
            r_large = client.get(reverse('course-detail', kwargs={'pk': large_course.id}))
        self.assertEqual(r_large.status_code, 200)
        large_count = len(ctx_large.captured_queries)

        # The large course has 4x the modules, 12x the lessons, 8x the
        # assessments -- query count must grow with MODULE count only
        # (one query per module for its own lessons/assessments, already
        # an accepted, pre-existing cost -- see ModuleSerializer's own
        # docstring), never per-lesson or per-assessment. 4 modules
        # should cost at most a small, bounded multiple of 1 module's
        # cost, not anything close to 12x or 8x.
        self.assertLessEqual(large_count, small_count * 4 + 4)


class DashboardAuthoritativeProgressTests(APITestCase):
    """
    Phase 4.5 CORRECTION. GET /api/courses/my_courses/ -- the exact
    endpoint the dashboard fetches -- must expose an accurate,
    assessment-aware `completion_percentage`/`is_completed`, proving the
    dashboard (which now reads these fields instead of the legacy
    lesson-only `progress_percentage`) reflects real module/assessment
    completion, not just lesson-watching. The legacy `progress_percentage`
    is asserted to still be present and unchanged -- the correction reads
    a different field, it does not remove or break this one.
    """

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.lesson = _add_lessons(self.module, 1)[0]
        self.assessment, self.question, self.correct, self.wrong = _add_single_choice_assessment(self.module, max_attempts=3)
        self.student = User.objects.create_user(username="dashboard_progress_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def _my_course_entry(self):
        response = self.client.get(reverse('course-my-courses'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return next(c for c in response.data if c['id'] == self.course.id)

    def test_all_lessons_done_but_assessment_unpassed_is_not_100_percent(self):
        """The exact scenario the correction exists for: before this fix,
        the dashboard's displayed percentage (progress_percentage) would
        have read 100% here (it only ever counted lessons) even though a
        required assessment was never passed. The authoritative field
        must not -- this course has a single module that is only
        half-done (lesson complete, assessment not), so course-level
        completion_percentage is 0 (that module isn't complete yet), a
        stark, unmistakable contrast with the legacy field's 100."""
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)
        entry = self._my_course_entry()

        # The legacy field IS still 100 (lesson-only, untouched, still served).
        self.assertEqual(entry['progress_percentage'], 100)
        # The authoritative field -- what the dashboard now actually
        # displays -- correctly reflects the unpassed assessment: the
        # course's one module isn't complete, so the course isn't either.
        self.assertEqual(entry['completion_percentage'], 0)
        self.assertFalse(entry['is_completed'])

        # The module-level breakdown (visible on the learning page) shows
        # exactly the partial credit the course-level figure rolls up:
        # 1 of 2 applicable items (lesson done, assessment not) = 50%.
        course_response = _course_detail(self.client, self.course.id)
        module_data = _module_data(course_response, self.module.id)
        self.assertEqual(module_data['completion_percentage'], 50)
        self.assertFalse(module_data['is_completed'])

    def test_passing_the_assessment_brings_completion_to_100(self):
        LessonProgress.objects.create(user=self.student, lesson=self.lesson, completed=True, video_duration=10, last_watched_position=10)
        _pass_assessment(self.client, self.assessment, self.correct, self.question)
        entry = self._my_course_entry()
        self.assertEqual(entry['completion_percentage'], 100)
        self.assertTrue(entry['is_completed'])

    def test_legacy_field_still_present_and_unaffected(self):
        """Requirement: do not remove or break the legacy field."""
        entry = self._my_course_entry()
        self.assertIn('progress_percentage', entry)
        self.assertEqual(entry['progress_percentage'], 0)  # lesson not yet watched
        self.assertIn('completion_percentage', entry)
