"""
Phase 4.3: Assessment Learning-Flow Integration.

Assessments now surface inside GET /api/courses/<id>/'s existing
`modules[].assessments` field (ModuleSerializer.get_assessments) rather
than needing a separate discovery endpoint -- see the Phase 4.3 report
for why extending the existing serializer was preferred over a new one.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Assessment, AssessmentAttempt, Course, Enrollment, LessonProgress, Module, VideoLesson
from courses.test_assessment_attempts import build_mixed_assessment, build_single_choice_assessment, full_marks_answers, make_subscription
from orders.models import Subscription, SubscriptionPlan
from django.core.cache import cache

User = get_user_model()


def _get_course_detail(client, course_id):
    return client.get(reverse('course-detail', kwargs={'pk': course_id}))


def _find_module(response_data, module_id):
    return next(m for m in response_data['modules'] if m['id'] == module_id)


def _wrong_but_answered_answers(fx):
    """Every required question answered (so submission isn't rejected as
    'unanswered required question'), but every answer deliberately
    incorrect -- for exercising the FAILED status path."""
    wrong_q1_option = fx["q1"].options.exclude(id=fx["q1_a"].id).first()
    return [
        {"question_id": fx["q1"].id, "option_ids": [wrong_q1_option.id]},
        {"question_id": fx["q2"].id, "option_ids": [fx["q2_a"].id]},
        {"question_id": fx["q3"].id, "option_ids": [fx["q3_c"].id]},
    ]


class AssessmentDiscoveryTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=3)
        self.student = User.objects.create_user(username="discover_student", password="pw")

    def test_published_assessment_appears_in_module_data(self):
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)
        response = _get_course_detail(self.client, self.fx["course"].id)
        module_data = _find_module(response.data, self.fx["module"].id)
        self.assertEqual(len(module_data['assessments']), 1)
        entry = module_data['assessments'][0]
        self.assertEqual(entry['id'], self.fx["assessment"].id)
        self.assertEqual(entry['title'], "Mixed Quiz")
        self.assertEqual(entry['question_count'], 3)

    def test_unpublished_assessment_does_not_appear(self):
        self.fx["assessment"].is_published = False
        self.fx["assessment"].save()
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)
        response = _get_course_detail(self.client, self.fx["course"].id)
        module_data = _find_module(response.data, self.fx["module"].id)
        self.assertEqual(module_data['assessments'], [])

    def test_student_without_course_access_sees_no_assessments(self):
        self.client.force_authenticate(self.student)  # not enrolled, no subscription
        response = _get_course_detail(self.client, self.fx["course"].id)
        module_data = _find_module(response.data, self.fx["module"].id)
        self.assertEqual(module_data['assessments'], [])

    def test_enrolled_student_can_see_assessment(self):
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)
        response = _get_course_detail(self.client, self.fx["course"].id)
        module_data = _find_module(response.data, self.fx["module"].id)
        self.assertEqual(len(module_data['assessments']), 1)

    def test_subscribed_student_can_see_assessment(self):
        plan = SubscriptionPlan.objects.create(name="Discover Plan", billing_interval="MONTHLY", price="99.00", razorpay_plan_id="plan_discover_1")
        plan.courses.add(self.fx["course"])
        from django.utils import timezone
        from datetime import timedelta
        make_subscription(self.student, plan, Subscription.Status.ACTIVE, timezone.now() + timedelta(days=10))
        self.client.force_authenticate(self.student)
        response = _get_course_detail(self.client, self.fx["course"].id)
        module_data = _find_module(response.data, self.fx["module"].id)
        self.assertEqual(len(module_data['assessments']), 1)

    def test_anonymous_sees_no_assessments(self):
        response = _get_course_detail(self.client, self.fx["course"].id)
        module_data = _find_module(response.data, self.fx["module"].id)
        self.assertEqual(module_data['assessments'], [])

    def test_correct_answers_never_exposed_in_module_data(self):
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)
        response = _get_course_detail(self.client, self.fx["course"].id)
        self.assertNotIn("is_correct", str(response.data))


class AssessmentStatusTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=2)
        self.student_a = User.objects.create_user(username="status_student_a", password="pw")
        self.student_b = User.objects.create_user(username="status_student_b", password="pw")
        for s in (self.student_a, self.student_b):
            Enrollment.objects.create(user=s, course=self.fx["course"])

    def _assessment_entry(self, client, course_id, module_id):
        response = _get_course_detail(client, course_id)
        module_data = _find_module(response.data, module_id)
        return module_data['assessments'][0]

    def test_not_started_status(self):
        self.client.force_authenticate(self.student_a)
        entry = self._assessment_entry(self.client, self.fx["course"].id, self.fx["module"].id)
        self.assertEqual(entry['status'], "NOT_STARTED")
        self.assertIsNone(entry['attempt_id'])
        self.assertEqual(entry['attempts_used'], 0)

    def test_in_progress_status_shows_attempt_id_for_continue(self):
        self.client.force_authenticate(self.student_a)
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        entry = self._assessment_entry(self.client, self.fx["course"].id, self.fx["module"].id)
        self.assertEqual(entry['status'], "IN_PROGRESS")
        self.assertEqual(entry['attempt_id'], start.data['attempt_id'])

    def test_passed_status_after_submission(self):
        self.client.force_authenticate(self.student_a)
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
            {"answers": full_marks_answers(self.fx)}, format='json',
        )
        entry = self._assessment_entry(self.client, self.fx["course"].id, self.fx["module"].id)
        self.assertEqual(entry['status'], "PASSED")
        self.assertEqual(entry['attempt_id'], start.data['attempt_id'])

    def test_failed_status_can_retry(self):
        self.client.force_authenticate(self.student_a)
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        submit = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
            {"answers": _wrong_but_answered_answers(self.fx)}, format='json',
        )
        self.assertEqual(submit.status_code, status.HTTP_200_OK, submit.data)
        entry = self._assessment_entry(self.client, self.fx["course"].id, self.fx["module"].id)
        self.assertEqual(entry['status'], "FAILED")
        self.assertEqual(entry['attempts_remaining'], 1)

    def test_max_attempts_reached_status(self):
        self.client.force_authenticate(self.student_a)
        for _ in range(2):  # max_attempts=2
            start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
            submit = self.client.post(
                reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
                {"answers": _wrong_but_answered_answers(self.fx)}, format='json',
            )
            self.assertEqual(submit.status_code, status.HTTP_200_OK, submit.data)
        entry = self._assessment_entry(self.client, self.fx["course"].id, self.fx["module"].id)
        self.assertEqual(entry['status'], "MAX_ATTEMPTS_REACHED")
        self.assertEqual(entry['attempts_remaining'], 0)

    def test_student_a_status_does_not_leak_to_student_b(self):
        self.client.force_authenticate(self.student_a)
        self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))

        self.client.force_authenticate(self.student_b)
        entry = self._assessment_entry(self.client, self.fx["course"].id, self.fx["module"].id)
        self.assertEqual(entry['status'], "NOT_STARTED")
        self.assertIsNone(entry['attempt_id'])


class DirectUrlCompatibilityTests(APITestCase):
    """Phase 4.2's direct endpoints must be completely unaffected by
    Phase 4.3's module-serializer changes."""

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_single_choice_assessment(max_attempts=3)
        self.student = User.objects.create_user(username="direct_url_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)

    def test_full_direct_flow_still_works(self):
        preview = self.client.get(reverse('assessment-detail', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(preview.status_code, status.HTTP_200_OK)

        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(start.status_code, status.HTTP_201_CREATED)

        get_attempt = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': start.data["attempt_id"]}))
        self.assertEqual(get_attempt.status_code, status.HTTP_200_OK)

        submit = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
            {"answers": [{"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]}]}, format='json',
        )
        self.assertEqual(submit.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(str(submit.data["score"])), Decimal("2.00"))

        my_attempts = self.client.get(reverse('assessment-attempt-my'))
        self.assertEqual(my_attempts.status_code, status.HTTP_200_OK)
        self.assertEqual(my_attempts.data["count"], 1)


class ExistingBehaviorUnaffectedTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: see other setUp() comments in this file.

    def test_lesson_progress_unchanged(self):
        student = User.objects.create_user(username="unaffected_student", password="pw")
        course = Course.objects.create(title="Unaffected Course", price=10, is_published=True)
        module = Module.objects.create(course=course, title="M", order=1)
        lesson = VideoLesson.objects.create(module=module, title="L1", order=1)
        Enrollment.objects.create(user=student, course=course)

        self.client.force_authenticate(student)
        response = self.client.post(
            reverse('lesson-progress', kwargs={'pk': lesson.id}),
            {"last_watched_position": 50, "video_duration": 100}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(LessonProgress.objects.get(user=student, lesson=lesson).last_watched_position, 50)

    def test_course_detail_module_shape_unchanged_for_lessons(self):
        course = Course.objects.create(title="Shape Course", price=10, is_published=True)
        module = Module.objects.create(course=course, title="M", order=1)
        VideoLesson.objects.create(module=module, title="L1", order=1)
        response = self.client.get(reverse('course-detail', kwargs={'pk': course.id}))
        module_data = response.data['modules'][0]
        self.assertIn('lessons', module_data)
        self.assertIn('assessments', module_data)
        self.assertEqual(len(module_data['lessons']), 1)
        self.assertEqual(module_data['assessments'], [])


class AssessmentModuleQueryCountTests(TestCase):
    """The added assessments cost must be a FIXED per-request query (the
    student's own attempts, fetched once) plus exactly ONE query per
    module that actually has access -- never one query per assessment."""

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.student = User.objects.create_user(username="qc_student", password="pw")

    def _course_with_n_assessments(self, n):
        course = Course.objects.create(title=f"QC Course {n}", price=10, is_published=True, course_type=Course.CourseType.RECORDED)
        module = Module.objects.create(course=course, title="M", order=1)
        for i in range(n):
            Assessment.objects.create(module=module, title=f"Quiz {i}", order=i, is_published=True)
        Enrollment.objects.create(user=self.student, course=course)
        return course

    def test_query_count_does_not_scale_with_assessment_count(self):
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(self.student)

        one_course = self._course_with_n_assessments(1)
        with self.assertNumQueries(12):  # no VideoLessons in this fixture -> progress_percentage short-circuits its 2nd query; +1 for Phase 4.5's completed_lesson_ids context; +1 for Phase 4.7's student_submissions_by_assignment_id context; +1 for the one module's assignments query (no Assignment rows exist in this fixture, but get_assignments still issues its per-module query, same as get_assessments already does)
            r1 = client.get(reverse('course-detail', kwargs={'pk': one_course.id}))
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(len(r1.data['modules'][0]['assessments']), 1)

        five_course = self._course_with_n_assessments(5)
        with self.assertNumQueries(12):  # same as above; same count -- 5 assessments, not 5 extra queries
            r5 = client.get(reverse('course-detail', kwargs={'pk': five_course.id}))
        self.assertEqual(r5.status_code, 200)
        self.assertEqual(len(r5.data['modules'][0]['assessments']), 5)
