"""
Phase 4.2: Assessment Attempts & Scoring.

Fixture shape reused by most test classes -- a mixed SINGLE_CHOICE +
MULTIPLE_CHOICE assessment worth 10 marks total, passing at 50%:

    Q1 SINGLE_CHOICE,   2 marks -- correct: optA1
    Q2 SINGLE_CHOICE,   3 marks -- correct: optB2
    Q3 MULTIPLE_CHOICE, 5 marks -- correct: {optA3, optB3}
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import (
    Assessment, AssessmentAttempt, Course, Enrollment, Module, Question, QuestionOption,
)
from orders.models import Subscription, SubscriptionPlan
from django.core.cache import cache

User = get_user_model()


def make_subscription(user, plan, sub_status, current_period_end):
    return Subscription.objects.create(
        user=user, plan=plan, status=sub_status,
        razorpay_subscription_id=f"sub_{user.id}_{plan.id}",
        current_period_start=timezone.now() - timedelta(days=15),
        current_period_end=current_period_end,
    )


def build_mixed_assessment(course=None, is_published=True, max_attempts=1, time_limit_minutes=None, passing_percentage=Decimal("50.00")):
    """Course -> Module -> Assessment (10 marks total) -> 3 questions."""
    if course is None:
        course = Course.objects.create(
            title="Attempt Test Course", description="x", price=100,
            is_published=True, course_type=Course.CourseType.RECORDED,
        )
    module = Module.objects.create(course=course, title="M1", order=1)
    assessment = Assessment.objects.create(
        module=module, title="Mixed Quiz", order=1, is_published=is_published,
        passing_percentage=passing_percentage, max_attempts=max_attempts,
        time_limit_minutes=time_limit_minutes,
    )

    q1 = Question.objects.create(assessment=assessment, question_text="Q1", question_type=Question.QuestionType.SINGLE_CHOICE, order=1, marks=Decimal("2.00"))
    q1_a = QuestionOption.objects.create(question=q1, option_text="A", order=1, is_correct=True)
    QuestionOption.objects.create(question=q1, option_text="B", order=2, is_correct=False)

    q2 = Question.objects.create(assessment=assessment, question_text="Q2", question_type=Question.QuestionType.SINGLE_CHOICE, order=2, marks=Decimal("3.00"))
    q2_a = QuestionOption.objects.create(question=q2, option_text="A", order=1, is_correct=False)
    q2_b = QuestionOption.objects.create(question=q2, option_text="B", order=2, is_correct=True)

    q3 = Question.objects.create(assessment=assessment, question_text="Q3", question_type=Question.QuestionType.MULTIPLE_CHOICE, order=3, marks=Decimal("5.00"))
    q3_a = QuestionOption.objects.create(question=q3, option_text="A", order=1, is_correct=True)
    q3_b = QuestionOption.objects.create(question=q3, option_text="B", order=2, is_correct=True)
    q3_c = QuestionOption.objects.create(question=q3, option_text="C", order=3, is_correct=False)

    return {
        "course": course, "module": module, "assessment": assessment,
        "q1": q1, "q1_a": q1_a, "q2": q2, "q2_a": q2_a, "q2_b": q2_b,
        "q3": q3, "q3_a": q3_a, "q3_b": q3_b, "q3_c": q3_c,
    }


def build_single_choice_assessment(max_attempts=5):
    """A single required SINGLE_CHOICE question, 2 marks -- isolates
    SINGLE_CHOICE scoring without other required questions interfering."""
    course = Course.objects.create(title="SC Course", description="x", price=1, is_published=True, course_type=Course.CourseType.RECORDED)
    module = Module.objects.create(course=course, title="M", order=1)
    assessment = Assessment.objects.create(module=module, title="SC Quiz", order=1, is_published=True, passing_percentage=Decimal("50.00"), max_attempts=max_attempts)
    q1 = Question.objects.create(assessment=assessment, question_text="Q1", question_type=Question.QuestionType.SINGLE_CHOICE, order=1, marks=Decimal("2.00"))
    q1_a = QuestionOption.objects.create(question=q1, option_text="A", order=1, is_correct=True)
    q1_b = QuestionOption.objects.create(question=q1, option_text="B", order=2, is_correct=False)
    return {"course": course, "module": module, "assessment": assessment, "q1": q1, "q1_a": q1_a, "q1_b": q1_b}


def build_multiple_choice_assessment(max_attempts=5):
    """A single required MULTIPLE_CHOICE question, 5 marks, correct set {A, B}."""
    course = Course.objects.create(title="MC Course", description="x", price=1, is_published=True, course_type=Course.CourseType.RECORDED)
    module = Module.objects.create(course=course, title="M", order=1)
    assessment = Assessment.objects.create(module=module, title="MC Quiz", order=1, is_published=True, passing_percentage=Decimal("50.00"), max_attempts=max_attempts)
    q3 = Question.objects.create(assessment=assessment, question_text="Q3", question_type=Question.QuestionType.MULTIPLE_CHOICE, order=1, marks=Decimal("5.00"))
    q3_a = QuestionOption.objects.create(question=q3, option_text="A", order=1, is_correct=True)
    q3_b = QuestionOption.objects.create(question=q3, option_text="B", order=2, is_correct=True)
    q3_c = QuestionOption.objects.create(question=q3, option_text="C", order=3, is_correct=False)
    return {"course": course, "module": module, "assessment": assessment, "q3": q3, "q3_a": q3_a, "q3_b": q3_b, "q3_c": q3_c}


def full_marks_answers(fx):
    return [
        {"question_id": fx["q1"].id, "option_ids": [fx["q1_a"].id]},
        {"question_id": fx["q2"].id, "option_ids": [fx["q2_b"].id]},
        {"question_id": fx["q3"].id, "option_ids": [fx["q3_a"].id, fx["q3_b"].id]},
    ]


class AssessmentAttemptAccessTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment()
        self.student = User.objects.create_user(username="access_student", password="pw")

    def _start_url(self):
        return reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id})

    def test_anonymous_cannot_start(self):
        response = self.client.post(self._start_url())
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
        self.assertFalse(AssessmentAttempt.objects.exists())

    def test_user_without_course_access_cannot_start(self):
        self.client.force_authenticate(self.student)
        response = self.client.post(self._start_url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertFalse(AssessmentAttempt.objects.exists())

    def test_student_with_enrollment_can_start(self):
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)
        response = self.client.post(self._start_url())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "IN_PROGRESS")
        self.assertEqual(len(response.data["questions"]), 3)
        # No correct-answer leakage.
        for q in response.data["questions"]:
            self.assertNotIn("is_correct", q)
            for opt in q["options"]:
                self.assertNotIn("is_correct", opt)

    def test_valid_subscription_student_can_start(self):
        plan = SubscriptionPlan.objects.create(name="Access Plan", billing_interval="MONTHLY", price="199.00", razorpay_plan_id="plan_attempt_1")
        plan.courses.add(self.fx["course"])
        make_subscription(self.student, plan, Subscription.Status.ACTIVE, timezone.now() + timedelta(days=10))
        self.client.force_authenticate(self.student)
        response = self.client.post(self._start_url())
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_unpublished_assessment_cannot_be_started(self):
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.fx["assessment"].is_published = False
        self.fx["assessment"].save()
        self.client.force_authenticate(self.student)
        response = self.client.post(self._start_url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_assessment_with_no_questions_cannot_be_started(self):
        course = Course.objects.create(title="Empty Q Course", description="x", price=1, is_published=True, course_type=Course.CourseType.RECORDED)
        module = Module.objects.create(course=course, title="M", order=1)
        empty_assessment = Assessment.objects.create(module=module, title="Empty", order=1, is_published=True)
        Enrollment.objects.create(user=self.student, course=course)
        self.client.force_authenticate(self.student)
        response = self.client.post(reverse('assessment-start', kwargs={'pk': empty_assessment.id}))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class AssessmentAttemptOwnershipTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=5)
        self.student_a = User.objects.create_user(username="owner_a", password="pw")
        self.student_b = User.objects.create_user(username="owner_b", password="pw")
        for s in (self.student_a, self.student_b):
            Enrollment.objects.create(user=s, course=self.fx["course"])

        self.client.force_authenticate(self.student_a)
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.attempt_id = start.data["attempt_id"]

    def test_student_b_cannot_get_student_a_attempt(self):
        self.client.force_authenticate(self.student_b)
        response = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': self.attempt_id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_student_b_cannot_submit_student_a_attempt(self):
        self.client.force_authenticate(self.student_b)
        response = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': self.attempt_id}),
            {"answers": full_marks_answers(self.fx)}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.fx["assessment"].refresh_from_db()
        attempt = AssessmentAttempt.objects.get(pk=self.attempt_id)
        self.assertEqual(attempt.status, AssessmentAttempt.Status.IN_PROGRESS)


class MaxAttemptsTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=3)
        self.student = User.objects.create_user(username="max_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)

    def _start_and_submit(self):
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        attempt_id = start.data["attempt_id"]
        submit = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': attempt_id}),
            {"answers": full_marks_answers(self.fx)}, format='json',
        )
        return start, submit

    def test_three_attempts_allowed_fourth_rejected(self):
        for n in (1, 2, 3):
            start, submit = self._start_and_submit()
            self.assertEqual(start.status_code, status.HTTP_201_CREATED, f"attempt {n} start failed")
            self.assertEqual(start.data["attempt_number"], n)
            self.assertEqual(submit.status_code, status.HTTP_200_OK, f"attempt {n} submit failed")

        fourth = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(fourth.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(AssessmentAttempt.objects.filter(student=self.student).count(), 3)


class ActiveAttemptTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=5)
        self.student = User.objects.create_user(username="active_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)

    def test_duplicate_start_reuses_same_attempt(self):
        first = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        second = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(first.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["attempt_id"], second.data["attempt_id"])
        self.assertEqual(AssessmentAttempt.objects.filter(student=self.student).count(), 1)

    def test_new_attempt_allowed_after_previous_submitted(self):
        first = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': first.data["attempt_id"]}),
            {"answers": full_marks_answers(self.fx)}, format='json',
        )
        second = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second.data["attempt_number"], 2)


class TimeLimitTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.student = User.objects.create_user(username="time_student", password="pw")

    def test_no_time_limit_assessment_never_expires(self):
        fx = build_mixed_assessment(time_limit_minutes=None)
        Enrollment.objects.create(user=self.student, course=fx["course"])
        self.client.force_authenticate(self.student)
        start = self.client.post(reverse('assessment-start', kwargs={'pk': fx["assessment"].id}))
        self.assertIsNone(start.data["deadline"])

        attempt = AssessmentAttempt.objects.get(pk=start.data["attempt_id"])
        attempt.started_at = timezone.now() - timedelta(days=365)
        attempt.save(update_fields=['started_at'])

        submit = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': attempt.id}),
            {"answers": full_marks_answers(fx)}, format='json',
        )
        self.assertEqual(submit.status_code, status.HTTP_200_OK)
        self.assertEqual(submit.data["status"], "SUBMITTED")

    def test_valid_timed_attempt_within_window_submits_normally(self):
        fx = build_mixed_assessment(time_limit_minutes=30)
        Enrollment.objects.create(user=self.student, course=fx["course"])
        self.client.force_authenticate(self.student)
        start = self.client.post(reverse('assessment-start', kwargs={'pk': fx["assessment"].id}))
        self.assertIsNotNone(start.data["deadline"])

        submit = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
            {"answers": full_marks_answers(fx)}, format='json',
        )
        self.assertEqual(submit.status_code, status.HTTP_200_OK)

    def test_expired_attempt_marked_timed_out_and_rejected(self):
        fx = build_mixed_assessment(time_limit_minutes=10)
        Enrollment.objects.create(user=self.student, course=fx["course"])
        self.client.force_authenticate(self.student)
        start = self.client.post(reverse('assessment-start', kwargs={'pk': fx["assessment"].id}))
        attempt = AssessmentAttempt.objects.get(pk=start.data["attempt_id"])
        attempt.started_at = timezone.now() - timedelta(minutes=11)
        attempt.save(update_fields=['started_at'])

        submit = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': attempt.id}),
            {"answers": full_marks_answers(fx)}, format='json',
        )
        self.assertEqual(submit.status_code, status.HTTP_400_BAD_REQUEST)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, AssessmentAttempt.Status.TIMED_OUT)
        self.assertIsNone(attempt.score)

    def test_client_cannot_extend_server_side_deadline(self):
        """The submit payload has no field for started_at/deadline at all --
        confirms there is no client-controlled way to influence the
        server-computed expiry."""
        fx = build_mixed_assessment(time_limit_minutes=10)
        Enrollment.objects.create(user=self.student, course=fx["course"])
        self.client.force_authenticate(self.student)
        start = self.client.post(reverse('assessment-start', kwargs={'pk': fx["assessment"].id}))
        attempt = AssessmentAttempt.objects.get(pk=start.data["attempt_id"])
        attempt.started_at = timezone.now() - timedelta(minutes=11)
        attempt.save(update_fields=['started_at'])

        payload = {"answers": full_marks_answers(fx), "started_at": timezone.now().isoformat(), "deadline": timezone.now().isoformat()}
        submit = self.client.post(reverse('assessment-attempt-submit', kwargs={'pk': attempt.id}), payload, format='json')
        self.assertEqual(submit.status_code, status.HTTP_400_BAD_REQUEST)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, AssessmentAttempt.Status.TIMED_OUT)


class ScoringSingleChoiceTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.student = User.objects.create_user(username="sc_student", password="pw")
        self.client.force_authenticate(self.student)

    def _submit(self, fx, answers):
        Enrollment.objects.create(user=self.student, course=fx["course"])
        start = self.client.post(reverse('assessment-start', kwargs={'pk': fx["assessment"].id}))
        return self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
            {"answers": answers}, format='json',
        )

    def test_correct_single_choice_awards_full_marks(self):
        fx = build_single_choice_assessment()
        response = self._submit(fx, [{"question_id": fx["q1"].id, "option_ids": [fx["q1_a"].id]}])
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("2.00"))

    def test_incorrect_single_choice_awards_zero(self):
        fx = build_single_choice_assessment()
        response = self._submit(fx, [{"question_id": fx["q1"].id, "option_ids": [fx["q1_b"].id]}])
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("0.00"))


class ScoringMultipleChoiceTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.student = User.objects.create_user(username="mc_student", password="pw")
        self.client.force_authenticate(self.student)

    def _submit_q3_only(self, fx, option_ids):
        Enrollment.objects.create(user=self.student, course=fx["course"])
        start = self.client.post(reverse('assessment-start', kwargs={'pk': fx["assessment"].id}))
        return self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
            {"answers": [{"question_id": fx["q3"].id, "option_ids": option_ids}]}, format='json',
        )

    def test_exact_correct_set_awards_full_marks(self):
        fx = build_multiple_choice_assessment()
        response = self._submit_q3_only(fx, [fx["q3_a"].id, fx["q3_b"].id])
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("5.00"))

    def test_missing_correct_option_awards_zero(self):
        fx = build_multiple_choice_assessment()
        response = self._submit_q3_only(fx, [fx["q3_a"].id])
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("0.00"))

    def test_extra_incorrect_option_awards_zero(self):
        fx = build_multiple_choice_assessment()
        response = self._submit_q3_only(fx, [fx["q3_a"].id, fx["q3_b"].id, fx["q3_c"].id])
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("0.00"))


class ScoringGeneralTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=5, passing_percentage=Decimal("50.00"))
        self.student = User.objects.create_user(username="general_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)

    def _start(self):
        return self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))

    def _submit(self, attempt_id, answers):
        return self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': attempt_id}), {"answers": answers}, format='json',
        )

    def test_mixed_question_assessment_full_marks_and_passes(self):
        start = self._start()
        response = self._submit(start.data["attempt_id"], full_marks_answers(self.fx))
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("10.00"))
        self.assertEqual(Decimal(str(response.data["percentage"])), Decimal("100.00"))
        self.assertTrue(response.data["passed"])

    def test_decimal_percentage_correctness_for_partial_score(self):
        # Only Q1 (2 marks) correct out of 10 -> 20.00%. Q2/Q3 are
        # required, so they're still answered here (deliberately wrong)
        # -- required only means "must answer", not "must answer Q1 only".
        start = self._start()
        response = self._submit(start.data["attempt_id"], [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]},
            {"question_id": self.fx["q2"].id, "option_ids": [self.fx["q2_a"].id]},  # wrong on purpose
            {"question_id": self.fx["q3"].id, "option_ids": [self.fx["q3_c"].id]},  # wrong on purpose
        ])
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("2.00"))
        self.assertEqual(Decimal(str(response.data["percentage"])), Decimal("20.00"))
        self.assertFalse(response.data["passed"])

    def test_pass_threshold_exactly_met(self):
        # Q1(2) + Q2(3) correct, Q3 answered wrong = 5/10 = 50% exactly -> passes (>=).
        start = self._start()
        response = self._submit(start.data["attempt_id"], [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]},
            {"question_id": self.fx["q2"].id, "option_ids": [self.fx["q2_b"].id]},
            {"question_id": self.fx["q3"].id, "option_ids": [self.fx["q3_c"].id]},
        ])
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(Decimal(str(response.data["percentage"])), Decimal("50.00"))
        self.assertTrue(response.data["passed"])

    def test_fail_threshold_just_below(self):
        # Only Q1 (2/10 = 20%) -- well below 50%. Q2/Q3 answered wrong
        # (still required to be answered, not omitted).
        start = self._start()
        response = self._submit(start.data["attempt_id"], [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]},
            {"question_id": self.fx["q2"].id, "option_ids": [self.fx["q2_a"].id]},  # wrong on purpose
            {"question_id": self.fx["q3"].id, "option_ids": [self.fx["q3_c"].id]},  # wrong on purpose
        ])
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertFalse(response.data["passed"])

    def test_unanswered_required_question_rejected(self):
        start = self._start()
        response = self._submit(start.data["attempt_id"], [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]},
            # q2, q3 left out entirely -- both default is_required=True.
        ])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        attempt = AssessmentAttempt.objects.get(pk=start.data["attempt_id"])
        self.assertEqual(attempt.status, AssessmentAttempt.Status.IN_PROGRESS)

    def test_non_required_question_may_be_left_unanswered(self):
        self.fx["q3"].is_required = False
        self.fx["q3"].save()
        start = self._start()
        response = self._submit(start.data["attempt_id"], [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]},
            {"question_id": self.fx["q2"].id, "option_ids": [self.fx["q2_b"].id]},
        ])
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("5.00"))

    def test_invalid_question_id_rejected(self):
        start = self._start()
        response = self._submit(start.data["attempt_id"], [{"question_id": 999999, "option_ids": []}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_option_from_another_question_rejected(self):
        start = self._start()
        response = self._submit(start.data["attempt_id"], [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q2_b"].id]},
        ])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_option_from_another_assessment_rejected(self):
        other_fx = build_mixed_assessment()
        start = self._start()
        response = self._submit(start.data["attempt_id"], [
            {"question_id": self.fx["q1"].id, "option_ids": [other_fx["q1_a"].id]},
        ])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_options_in_same_answer_handled_safely(self):
        start = self._start()
        response = self._submit(start.data["attempt_id"], [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id, self.fx["q1_a"].id]},
            {"question_id": self.fx["q2"].id, "option_ids": [self.fx["q2_b"].id]},
            {"question_id": self.fx["q3"].id, "option_ids": [self.fx["q3_a"].id, self.fx["q3_b"].id]},
        ])
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(Decimal(str(response.data["score"])), Decimal("10.00"))

    def test_duplicate_question_in_payload_rejected(self):
        start = self._start()
        response = self._submit(start.data["attempt_id"], [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]},
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]},
        ])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_submission_handled_safely(self):
        start = self._start()
        first = self._submit(start.data["attempt_id"], full_marks_answers(self.fx))
        second = self._submit(start.data["attempt_id"], full_marks_answers(self.fx))
        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)

    def test_submitted_attempt_cannot_be_modified_by_resubmission(self):
        start = self._start()
        self._submit(start.data["attempt_id"], full_marks_answers(self.fx))
        # Try to "resubmit" with all-wrong answers -- must not change the stored result.
        wrong = [{"question_id": self.fx["q1"].id, "option_ids": []}]
        self._submit(start.data["attempt_id"], wrong)
        attempt = AssessmentAttempt.objects.get(pk=start.data["attempt_id"])
        self.assertEqual(attempt.score, Decimal("10.00"))

    def test_single_choice_with_two_selected_options_rejected(self):
        q1_options = list(self.fx["q1"].options.all())
        start = self._start()
        response = self._submit(start.data["attempt_id"], [
            {"question_id": self.fx["q1"].id, "option_ids": [o.id for o in q1_options]},
        ])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class MyAttemptsListTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx1 = build_mixed_assessment(max_attempts=5)
        self.fx2 = build_mixed_assessment(max_attempts=5)
        self.student = User.objects.create_user(username="my_attempts_student", password="pw")
        self.other = User.objects.create_user(username="my_attempts_other", password="pw")
        for c in (self.fx1["course"], self.fx2["course"]):
            Enrollment.objects.create(user=self.student, course=c)
        Enrollment.objects.create(user=self.other, course=self.fx1["course"])

    def test_returns_only_own_attempts(self):
        self.client.force_authenticate(self.student)
        self.client.post(reverse('assessment-start', kwargs={'pk': self.fx1["assessment"].id}))
        self.client.post(reverse('assessment-start', kwargs={'pk': self.fx2["assessment"].id}))

        self.client.force_authenticate(self.other)
        self.client.post(reverse('assessment-start', kwargs={'pk': self.fx1["assessment"].id}))

        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('assessment-attempt-my'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 2)
        returned_students = {AssessmentAttempt.objects.get(pk=r["id"]).student_id for r in response.data["results"]}
        self.assertEqual(returned_students, {self.student.id})

    def test_filter_by_assessment_id(self):
        self.client.force_authenticate(self.student)
        self.client.post(reverse('assessment-start', kwargs={'pk': self.fx1["assessment"].id}))
        self.client.post(reverse('assessment-start', kwargs={'pk': self.fx2["assessment"].id}))

        response = self.client.get(reverse('assessment-attempt-my'), {"assessment_id": self.fx1["assessment"].id})
        self.assertEqual(response.data["count"], 1)

    def test_invalid_assessment_id_filter_rejected_not_500(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('assessment-attempt-my'), {"assessment_id": "not-a-number"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_anonymous_cannot_list_attempts(self):
        response = self.client.get(reverse('assessment-attempt-my'))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class DataIsolationAndSafeSerializationTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=5)
        self.student = User.objects.create_user(username="iso_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)

    def test_get_in_progress_attempt_never_leaks_correctness(self):
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        response = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': start.data["attempt_id"]}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body_str = str(response.data)
        self.assertNotIn("is_correct", body_str)

    def test_get_submitted_attempt_returns_result_and_safe_review(self):
        """
        Phase 4.2 originally asserted NO 'questions' key on a submitted
        attempt's GET response -- Phase 4.4 deliberately supersedes that
        (post-submission review is now an explicit, in-scope feature; see
        the Phase 4.4 report). What must still hold, unconditionally, is
        that no raw `is_correct` field is ever serialized -- only the
        explicitly-derived `is_correct_answer` (options) / marks_awarded
        (answer), scoped to this attempt's own frozen result.
        """
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
            {"answers": full_marks_answers(self.fx)}, format='json',
        )
        response = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': start.data["attempt_id"]}))
        self.assertEqual(response.data["status"], "SUBMITTED")
        self.assertIn("score", response.data)
        self.assertIn("questions", response.data)
        for q in response.data["questions"]:
            for opt in q["options"]:
                self.assertIn("is_correct_answer", opt)
                self.assertNotIn("is_correct", opt)

    def test_unpublished_assessment_start_returns_404_not_403(self):
        """404, not 403 -- never confirms an unpublished assessment exists."""
        self.fx["assessment"].is_published = False
        self.fx["assessment"].save()
        response = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


class AssessmentAttemptAdminAccessTests(TestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment()
        self.student = User.objects.create_user(username="admin_test_student", password="pw", is_staff=False)
        self.superuser = User.objects.create_superuser(username="admin_test_super", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.attempt = AssessmentAttempt.objects.create(assessment=self.fx["assessment"], student=self.student, attempt_number=1)

    def test_non_staff_cannot_access_attempt_admin(self):
        self.client.force_login(self.student)
        response = self.client.get(f"/admin/courses/assessmentattempt/{self.attempt.id}/change/")
        self.assertNotEqual(response.status_code, 200)

    def test_superuser_change_view_has_no_save_capability(self):
        from courses.admin import AssessmentAttemptAdmin
        from django.contrib import admin as django_admin
        admin_instance = AssessmentAttemptAdmin(AssessmentAttempt, django_admin.site)
        self.assertFalse(admin_instance.has_change_permission(None))
        self.assertFalse(admin_instance.has_add_permission(None))
        self.assertFalse(admin_instance.has_delete_permission(None))


class ExistingCourseFunctionalityUnaffectedByPhase42Tests(APITestCase):
    """Smoke test -- Phase 4.1's own foundation and Course/Module basics
    still work after Phase 4.2's model/view/admin additions."""

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: see other setUp() comments in this file.

    def test_course_module_assessment_creation_still_works(self):
        fx = build_mixed_assessment()
        self.assertEqual(fx["course"].modules.count(), 1)
        self.assertEqual(fx["module"].assessments.count(), 1)
        self.assertEqual(fx["assessment"].questions.count(), 3)

    def test_course_serializer_progress_percentage_unaffected(self):
        from courses.models import Enrollment, LessonProgress, VideoLesson
        student = User.objects.create_user(username="regress_student", password="pw")
        course = Course.objects.create(title="Regress Course", price=10, is_published=True)
        module = Module.objects.create(course=course, title="M", order=1)
        lesson = VideoLesson.objects.create(module=module, title="L1", order=1)
        Enrollment.objects.create(user=student, course=course)
        LessonProgress.objects.create(user=student, lesson=lesson, completed=True, video_duration=100, last_watched_position=100)

        self.client.force_authenticate(student)
        response = self.client.get(reverse('course-my-courses'))
        data = next(c for c in response.data if c['id'] == course.id)
        self.assertEqual(data['progress_percentage'], 100)


class AssessmentPreviewRetrieveTests(APITestCase):
    """The read-only, side-effect-free GET /assessments/<id>/ added so the
    frontend's start screen can show instructions/passing criteria before
    committing to start (which would otherwise begin the clock)."""

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=2)
        self.student = User.objects.create_user(username="preview_student", password="pw")

    def _url(self):
        return reverse('assessment-detail', kwargs={'pk': self.fx["assessment"].id})

    def test_preview_never_creates_an_attempt(self):
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["title"], "Mixed Quiz")
        self.assertEqual(response.data["question_count"], 3)
        self.assertEqual(response.data["attempts_used"], 0)
        self.assertTrue(response.data["can_start"])
        self.assertIsNone(response.data["active_attempt_id"])
        self.assertFalse(AssessmentAttempt.objects.exists())

    def test_preview_shows_no_is_correct_data(self):
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)
        response = self.client.get(self._url())
        self.assertNotIn("is_correct", str(response.data))
        self.assertNotIn("questions", response.data)

    def test_preview_reflects_active_attempt(self):
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        response = self.client.get(self._url())
        self.assertEqual(response.data["active_attempt_id"], start.data["attempt_id"])
        self.assertEqual(response.data["attempts_used"], 1)

    def test_preview_can_start_false_once_max_attempts_reached(self):
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)
        for _ in range(2):  # max_attempts=2
            start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
            self.client.post(
                reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
                {"answers": full_marks_answers(self.fx)}, format='json',
            )
        response = self.client.get(self._url())
        self.assertFalse(response.data["can_start"])
        self.assertEqual(response.data["attempts_remaining"], 0)

    def test_unpublished_preview_returns_404(self):
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.fx["assessment"].is_published = False
        self.fx["assessment"].save()
        self.client.force_authenticate(self.student)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_preview_without_course_access_returns_403(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(self._url())
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_preview_rejected(self):
        response = self.client.get(self._url())
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
