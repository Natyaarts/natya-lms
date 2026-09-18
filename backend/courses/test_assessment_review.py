"""
Phase 4.4: Assessment Results, Review & Attempt History.

Reuses the Phase 4.2 fixture helpers (build_mixed_assessment etc.) rather
than duplicating them -- see courses/test_assessment_attempts.py.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import (
    Assessment, AssessmentAttempt, Course, Enrollment, Module, Question, QuestionOption,
)
from courses.test_assessment_attempts import build_mixed_assessment, build_single_choice_assessment, full_marks_answers
from django.core.cache import cache

User = get_user_model()


def _start_and_submit(client, fx, answers):
    start = client.post(reverse('assessment-start', kwargs={'pk': fx["assessment"].id}))
    submit = client.post(
        reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
        {"answers": answers}, format='json',
    )
    return start, submit


def _wrong_but_answered_answers(fx):
    wrong_q1_option = fx["q1"].options.exclude(id=fx["q1_a"].id).first()
    return [
        {"question_id": fx["q1"].id, "option_ids": [wrong_q1_option.id]},
        {"question_id": fx["q2"].id, "option_ids": [fx["q2_a"].id]},
        {"question_id": fx["q3"].id, "option_ids": [fx["q3_c"].id]},
    ]


class ResultDisplayTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=3, passing_percentage=Decimal("50.00"))
        self.student = User.objects.create_user(username="result_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)

    def test_submitted_result_visible_with_all_fields(self):
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        self.assertEqual(submit.status_code, status.HTTP_200_OK)
        for key in ("attempt_id", "attempt_number", "status", "started_at", "submitted_at",
                    "assessment", "score", "percentage", "passed", "total_marks",
                    "questions", "question_count", "correct_count"):
            self.assertIn(key, submit.data, f"missing key: {key}")
        self.assertEqual(submit.data["assessment"]["title"], "Mixed Quiz")
        self.assertEqual(submit.data["assessment"]["passing_percentage"], "50.00")

    def test_score_and_total_marks_correct(self):
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        self.assertEqual(Decimal(str(submit.data["score"])), Decimal("10.00"))
        self.assertEqual(Decimal(str(submit.data["total_marks"])), Decimal("10.00"))

    def test_percentage_correct(self):
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        self.assertEqual(Decimal(str(submit.data["percentage"])), Decimal("100.00"))

    def test_pass_state_correct(self):
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        self.assertTrue(submit.data["passed"])

    def test_fail_state_correct(self):
        start, submit = _start_and_submit(self.client, self.fx, _wrong_but_answered_answers(self.fx))
        self.assertFalse(submit.data["passed"])
        self.assertEqual(Decimal(str(submit.data["score"])), Decimal("0.00"))

    def test_attempt_number_correct_across_retries(self):
        _start_and_submit(self.client, self.fx, _wrong_but_answered_answers(self.fx))
        start2, submit2 = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        self.assertEqual(submit2.data["attempt_number"], 2)

    def test_question_count_and_correct_count(self):
        start, submit = _start_and_submit(self.client, self.fx, [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]},  # correct
            {"question_id": self.fx["q2"].id, "option_ids": [self.fx["q2_a"].id]},  # wrong
            {"question_id": self.fx["q3"].id, "option_ids": [self.fx["q3_a"].id, self.fx["q3_b"].id]},  # correct
        ])
        self.assertEqual(submit.data["question_count"], 3)
        self.assertEqual(submit.data["correct_count"], 2)


class AnswerReviewTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=3)
        self.fx["q1"].explanation = "Option A is correct because..."
        self.fx["q1"].save()
        self.student = User.objects.create_user(username="review_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)

    def test_submitted_attempt_can_be_reviewed(self):
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        review = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': start.data["attempt_id"]}))
        self.assertEqual(review.status_code, status.HTTP_200_OK)
        self.assertEqual(len(review.data["questions"]), 3)

    def test_correct_single_choice_answer_derived_correctly(self):
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        q1_review = next(q for q in submit.data["questions"] if q["id"] == self.fx["q1"].id)
        self.assertTrue(q1_review["answered_correctly"])
        self.assertEqual(Decimal(str(q1_review["marks_awarded"])), Decimal("2.00"))
        self.assertEqual(q1_review["selected_option_ids"], [self.fx["q1_a"].id])

    def test_incorrect_single_choice_answer_derived_correctly(self):
        wrong_q1 = self.fx["q1"].options.exclude(id=self.fx["q1_a"].id).first()
        start, submit = _start_and_submit(self.client, self.fx, [
            {"question_id": self.fx["q1"].id, "option_ids": [wrong_q1.id]},
            {"question_id": self.fx["q2"].id, "option_ids": [self.fx["q2_b"].id]},
            {"question_id": self.fx["q3"].id, "option_ids": [self.fx["q3_a"].id, self.fx["q3_b"].id]},
        ])
        q1_review = next(q for q in submit.data["questions"] if q["id"] == self.fx["q1"].id)
        self.assertFalse(q1_review["answered_correctly"])
        self.assertEqual(Decimal(str(q1_review["marks_awarded"])), Decimal("0.00"))

    def test_multiple_choice_review_exact_set_correct(self):
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        q3_review = next(q for q in submit.data["questions"] if q["id"] == self.fx["q3"].id)
        self.assertTrue(q3_review["answered_correctly"])
        self.assertEqual(set(q3_review["selected_option_ids"]), {self.fx["q3_a"].id, self.fx["q3_b"].id})
        correct_option_ids = {o["id"] for o in q3_review["options"] if o["is_correct_answer"]}
        self.assertEqual(correct_option_ids, {self.fx["q3_a"].id, self.fx["q3_b"].id})

    def test_multiple_choice_review_partial_set_incorrect(self):
        start, submit = _start_and_submit(self.client, self.fx, [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]},
            {"question_id": self.fx["q2"].id, "option_ids": [self.fx["q2_b"].id]},
            {"question_id": self.fx["q3"].id, "option_ids": [self.fx["q3_a"].id]},  # missing q3_b
        ])
        q3_review = next(q for q in submit.data["questions"] if q["id"] == self.fx["q3"].id)
        self.assertFalse(q3_review["answered_correctly"])
        self.assertEqual(Decimal(str(q3_review["marks_awarded"])), Decimal("0.00"))

    def test_explanation_returned_when_configured(self):
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        q1_review = next(q for q in submit.data["questions"] if q["id"] == self.fx["q1"].id)
        self.assertEqual(q1_review["explanation"], "Option A is correct because...")

    def test_explanation_blank_when_not_configured(self):
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        q2_review = next(q for q in submit.data["questions"] if q["id"] == self.fx["q2"].id)
        self.assertEqual(q2_review["explanation"], "")

    def test_marks_earned_correct_for_mixed_result(self):
        start, submit = _start_and_submit(self.client, self.fx, [
            {"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]},  # correct, 2 marks
            {"question_id": self.fx["q2"].id, "option_ids": [self.fx["q2_a"].id]},  # wrong, 0 marks
            {"question_id": self.fx["q3"].id, "option_ids": [self.fx["q3_a"].id, self.fx["q3_b"].id]},  # correct, 5 marks
        ])
        marks_by_qid = {q["id"]: Decimal(str(q["marks_awarded"])) for q in submit.data["questions"]}
        self.assertEqual(marks_by_qid[self.fx["q1"].id], Decimal("2.00"))
        self.assertEqual(marks_by_qid[self.fx["q2"].id], Decimal("0.00"))
        self.assertEqual(marks_by_qid[self.fx["q3"].id], Decimal("5.00"))

    def test_no_answer_key_leak_before_submission(self):
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        in_progress = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': start.data["attempt_id"]}))
        self.assertEqual(in_progress.data["status"], "IN_PROGRESS")
        for q in in_progress.data["questions"]:
            self.assertNotIn("answered_correctly", q)
            self.assertNotIn("marks_awarded", q)
            for opt in q["options"]:
                self.assertNotIn("is_correct_answer", opt)
                self.assertNotIn("is_correct", opt)

    def test_no_raw_is_correct_field_leak_after_submission(self):
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        for q in submit.data["questions"]:
            for opt in q["options"]:
                self.assertNotIn("is_correct", opt)  # only is_correct_answer, exact key check
                self.assertIn("is_correct_answer", opt)


class ReviewSecurityTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=3)
        self.student_a = User.objects.create_user(username="sec_student_a", password="pw")
        self.student_b = User.objects.create_user(username="sec_student_b", password="pw")
        Enrollment.objects.create(user=self.student_a, course=self.fx["course"])

        self.client.force_authenticate(self.student_a)
        self.start, self.submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))

    def test_student_b_cannot_review_student_a_attempt(self):
        self.client.force_authenticate(self.student_b)
        response = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': self.start.data["attempt_id"]}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cannot_resubmit_submitted_attempt(self):
        self.client.force_authenticate(self.student_a)
        second = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': self.start.data["attempt_id"]}),
            {"answers": _wrong_but_answered_answers(self.fx)}, format='json',
        )
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        attempt = AssessmentAttempt.objects.get(pk=self.start.data["attempt_id"])
        self.assertEqual(attempt.score, Decimal("10.00"))  # unchanged -- original result stands

    def test_result_fields_cannot_be_altered_via_review_get(self):
        """GET is read-only -- confirms no side effect on the stored result."""
        self.client.force_authenticate(self.student_a)
        before = AssessmentAttempt.objects.get(pk=self.start.data["attempt_id"])
        self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': self.start.data["attempt_id"]}))
        after = AssessmentAttempt.objects.get(pk=self.start.data["attempt_id"])
        self.assertEqual(before.score, after.score)
        self.assertEqual(before.percentage, after.percentage)
        self.assertEqual(before.passed, after.passed)

    def test_unpublished_assessment_still_inaccessible_for_review_flow(self):
        self.fx["assessment"].is_published = False
        self.fx["assessment"].save()
        other_student = User.objects.create_user(username="sec_student_c", password="pw")
        Enrollment.objects.create(user=other_student, course=self.fx["course"])
        self.client.force_authenticate(other_student)
        response = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_no_course_access_remains_blocked(self):
        no_access_student = User.objects.create_user(username="sec_student_d", password="pw")
        self.client.force_authenticate(no_access_student)
        response = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class AttemptHistoryTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=3)
        self.student_a = User.objects.create_user(username="hist_student_a", password="pw")
        self.student_b = User.objects.create_user(username="hist_student_b", password="pw")
        Enrollment.objects.create(user=self.student_a, course=self.fx["course"])
        Enrollment.objects.create(user=self.student_b, course=self.fx["course"])

    def test_own_attempts_returned_with_can_review(self):
        self.client.force_authenticate(self.student_a)
        _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        response = self.client.get(reverse('assessment-attempt-my'))
        self.assertEqual(response.data["count"], 1)
        self.assertTrue(response.data["results"][0]["can_review"])

    def test_in_progress_attempt_cannot_be_reviewed_yet(self):
        self.client.force_authenticate(self.student_a)
        self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        response = self.client.get(reverse('assessment-attempt-my'))
        self.assertFalse(response.data["results"][0]["can_review"])

    def test_other_students_attempts_excluded(self):
        self.client.force_authenticate(self.student_a)
        _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))

        self.client.force_authenticate(self.student_b)
        _start_and_submit(self.client, self.fx, _wrong_but_answered_answers(self.fx))

        response = self.client.get(reverse('assessment-attempt-my'))
        self.assertEqual(response.data["count"], 1)
        attempt = AssessmentAttempt.objects.get(pk=response.data["results"][0]["id"])
        self.assertEqual(attempt.student_id, self.student_b.id)

    def test_ordering_most_recent_first(self):
        self.client.force_authenticate(self.student_a)
        _start_and_submit(self.client, self.fx, _wrong_but_answered_answers(self.fx))
        _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        response = self.client.get(reverse('assessment-attempt-my'))
        numbers = [r["attempt_number"] for r in response.data["results"]]
        self.assertEqual(numbers, [2, 1])

    def test_historical_scores_frozen_after_question_marks_change(self):
        """The single most important Phase 4.4 test: editing Question.marks
        and QuestionOption.is_correct AFTER submission must not silently
        change an already-graded attempt's stored result OR its review
        breakdown."""
        self.client.force_authenticate(self.student_a)
        start, submit = _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        original_score = Decimal(str(submit.data["score"]))
        original_percentage = Decimal(str(submit.data["percentage"]))
        original_total_marks = Decimal(str(submit.data["total_marks"]))
        original_marks_awarded = {q["id"]: Decimal(str(q["marks_awarded"])) for q in submit.data["questions"]}
        original_correctness = {q["id"]: q["answered_correctly"] for q in submit.data["questions"]}

        # Admin edits the assessment AFTER the student has already been graded.
        self.fx["q1"].marks = Decimal("99.00")
        self.fx["q1"].save()
        self.fx["q1_a"].is_correct = False  # flip what used to be correct
        self.fx["q1_a"].save()
        wrong_option = self.fx["q1"].options.exclude(id=self.fx["q1_a"].id).first()
        wrong_option.is_correct = True
        wrong_option.save()

        attempt = AssessmentAttempt.objects.get(pk=start.data["attempt_id"])
        self.assertEqual(attempt.score, original_score)
        self.assertEqual(attempt.percentage, original_percentage)
        self.assertEqual(attempt.total_marks, original_total_marks)

        response = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': start.data["attempt_id"]}))
        self.assertEqual(Decimal(str(response.data["score"])), original_score)
        self.assertEqual(Decimal(str(response.data["percentage"])), original_percentage)
        self.assertEqual(Decimal(str(response.data["total_marks"])), original_total_marks)
        for q in response.data["questions"]:
            self.assertEqual(Decimal(str(q["marks_awarded"])), original_marks_awarded[q["id"]])
            self.assertEqual(q["answered_correctly"], original_correctness[q["id"]])


class RetryTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=2)
        self.student = User.objects.create_user(username="retry_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)

    def test_failed_with_attempts_remaining_allows_retry(self):
        _start_and_submit(self.client, self.fx, _wrong_but_answered_answers(self.fx))
        retry = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(retry.status_code, status.HTTP_201_CREATED)
        self.assertEqual(retry.data["attempt_number"], 2)

    def test_max_attempts_blocks_further_retry(self):
        _start_and_submit(self.client, self.fx, _wrong_but_answered_answers(self.fx))
        _start_and_submit(self.client, self.fx, _wrong_but_answered_answers(self.fx))
        blocked = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(blocked.status_code, status.HTTP_403_FORBIDDEN)

    def test_passed_assessment_preview_shows_correct_remaining_count(self):
        """Not a backend block (Phase 4.2's max-attempt rule is unchanged
        -- see the Phase 4.4 report) but the preview data the frontend
        uses to decide whether to show a retry button must be accurate."""
        _start_and_submit(self.client, self.fx, full_marks_answers(self.fx))
        preview = self.client.get(reverse('assessment-detail', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(preview.data["attempts_used"], 1)
        self.assertEqual(preview.data["attempts_remaining"], 1)


class TimeoutReviewTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_mixed_assessment(max_attempts=3, time_limit_minutes=10)
        self.student = User.objects.create_user(username="timeout_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)

    def _expire(self, attempt_id):
        from django.utils import timezone
        from datetime import timedelta
        attempt = AssessmentAttempt.objects.get(pk=attempt_id)
        attempt.started_at = timezone.now() - timedelta(minutes=11)
        attempt.save(update_fields=['started_at'])
        return attempt

    def test_timed_out_attempt_review_works_with_no_score(self):
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self._expire(start.data["attempt_id"])
        response = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': start.data["attempt_id"]}))
        self.assertEqual(response.data["status"], "TIMED_OUT")
        self.assertIsNone(response.data["score"])
        self.assertIsNone(response.data["percentage"])
        self.assertIsNone(response.data["passed"])
        self.assertEqual(len(response.data["questions"]), 3)
        for q in response.data["questions"]:
            self.assertEqual(q["selected_option_ids"], [])
            self.assertFalse(q["answered_correctly"])
            # CORRECTION: a TIMED_OUT attempt has no frozen snapshot (it
            # was never scored), so -- unlike a SUBMITTED attempt's
            # review -- no correctness is revealed for it at all.
            for opt in q["options"]:
                self.assertNotIn("is_correct_answer", opt)

    def test_timed_out_attempt_cannot_be_submitted(self):
        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self._expire(start.data["attempt_id"])
        submit = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
            {"answers": full_marks_answers(self.fx)}, format='json',
        )
        self.assertEqual(submit.status_code, status.HTTP_400_BAD_REQUEST)
        attempt = AssessmentAttempt.objects.get(pk=start.data["attempt_id"])
        self.assertIsNone(attempt.score)
        self.assertEqual(attempt.answers.count(), 0)


class ReviewQueryCountTests(TestCase):
    """
    Section 17. The READ path (GET .../assessment-attempts/<id>/, i.e.
    the review payload) must cost the SAME fixed number of queries
    regardless of question count -- it's built entirely from
    prefetch_related/select_related batch fetches, never one query per
    question (see _build_review_questions's own docstring).

    The SUBMIT path is deliberately NOT asserted to be constant here: it
    does one INSERT per question (AssessmentAnswer) plus one per selected
    option (AssessmentAnswerOption) -- that scales with N by necessity,
    since N rows are being created, per Phase 4.1's explicit "relational,
    not JSON" mandate. That is normal, expected write cost, not an N+1
    SELECT regression, so pinning it to a fixed count would be the wrong
    test to write.
    """

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: see other setUp() comments in this file.

    def _assessment_with_n_questions(self, n):
        course = Course.objects.create(title=f"Perf Course {n}", price=1, is_published=True, course_type=Course.CourseType.RECORDED)
        module = Module.objects.create(course=course, title="M", order=1)
        assessment = Assessment.objects.create(module=module, title=f"Perf Quiz {n}", order=1, is_published=True, max_attempts=3)
        answers = []
        for i in range(n):
            q = Question.objects.create(assessment=assessment, question_text=f"Q{i}", question_type=Question.QuestionType.SINGLE_CHOICE, order=i, marks=Decimal("1.00"))
            correct = QuestionOption.objects.create(question=q, option_text="A", order=1, is_correct=True)
            QuestionOption.objects.create(question=q, option_text="B", order=2, is_correct=False)
            answers.append({"question_id": q.id, "option_ids": [correct.id]})
        return course, assessment, answers

    def test_review_get_query_count_constant_across_question_counts(self):
        from rest_framework.test import APIClient
        student = User.objects.create_user(username="perf_student", password="pw")
        client = APIClient()
        client.force_authenticate(student)

        query_counts = {}
        question_counts = {}
        for n in (5, 20, 50):
            course, assessment, answers = self._assessment_with_n_questions(n)
            Enrollment.objects.create(user=student, course=course)
            start = client.post(reverse('assessment-start', kwargs={'pk': assessment.id}))
            self.assertEqual(start.status_code, 201)
            submit = client.post(
                reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
                {"answers": answers}, format='json',
            )
            self.assertEqual(submit.status_code, 200)

            from django.test.utils import CaptureQueriesContext
            from django.db import connection
            with CaptureQueriesContext(connection) as ctx:
                review = client.get(reverse('assessment-attempt-detail', kwargs={'pk': start.data["attempt_id"]}))
            self.assertEqual(review.status_code, 200)
            query_counts[n] = len(ctx.captured_queries)
            question_counts[n] = len(review.data["questions"])

        self.assertEqual(question_counts, {5: 5, 20: 20, 50: 50})
        # The actual assertion: GET's query count for 50 questions is the
        # SAME as for 5 -- not "small", not "reasonable", identical.
        self.assertEqual(query_counts[5], query_counts[20])
        self.assertEqual(query_counts[5], query_counts[50])


class HistoricalReviewCorrectionTests(APITestCase):
    """
    Phase 4.4 CORRECTION. The exact scenario the correction directive
    describes: option correctness (and text) must be reconstructible from
    what existed AT SUBMISSION TIME, never from live QuestionOption data,
    and the per-question marks denominator must be equally immune to a
    later Question.marks edit.
    """

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.fx = build_single_choice_assessment(max_attempts=3)
        self.student = User.objects.create_user(username="correction_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.fx["course"])
        self.client.force_authenticate(self.student)

        start = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.submit = self.client.post(
            reverse('assessment-attempt-submit', kwargs={'pk': start.data["attempt_id"]}),
            {"answers": [{"question_id": self.fx["q1"].id, "option_ids": [self.fx["q1_a"].id]}]}, format='json',
        )
        self.attempt_id = start.data["attempt_id"]

    def _review(self):
        response = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': self.attempt_id}))
        return response.data["questions"][0]

    def test_a_b_review_shows_option_a_as_correct(self):
        # A: submitted above with Option A (correct) selected.
        q = self._review()
        self.assertTrue(q["answered_correctly"])
        a_option = next(o for o in q["options"] if o["id"] == self.fx["q1_a"].id)
        self.assertTrue(a_option["is_correct_answer"])
        self.assertEqual(Decimal(str(q["marks_awarded"])), Decimal("2.00"))

    def test_c_d_correctness_flip_does_not_change_historical_review(self):
        before = self._review()

        # C: admin flips correctness AFTER submission -- exactly the
        # directive's example (A becomes incorrect, B becomes correct).
        self.fx["q1_a"].is_correct = False
        self.fx["q1_a"].save()
        self.fx["q1_b"].is_correct = True
        self.fx["q1_b"].save()

        # D: historical review is unchanged.
        after = self._review()
        self.assertEqual(before, after)
        a_option = next(o for o in after["options"] if o["id"] == self.fx["q1_a"].id)
        b_option = next(o for o in after["options"] if o["id"] == self.fx["q1_b"].id)
        self.assertTrue(a_option["is_correct_answer"])   # still shown correct, as it was
        self.assertFalse(b_option["is_correct_answer"])  # still shown incorrect, as it was
        self.assertTrue(after["answered_correctly"])
        self.assertEqual(Decimal(str(after["marks_awarded"])), Decimal("2.00"))

        # The attempt's own frozen aggregate is untouched too.
        attempt = AssessmentAttempt.objects.get(pk=self.attempt_id)
        self.assertEqual(attempt.passed, True)

    def test_e_f_marks_change_does_not_change_historical_result(self):
        original_marks_awarded = Decimal(str(self._review()["marks_awarded"]))
        original_marks_shown = Decimal(str(self._review()["marks"]))
        original_score = AssessmentAttempt.objects.get(pk=self.attempt_id).score
        original_total_marks = AssessmentAttempt.objects.get(pk=self.attempt_id).total_marks

        # E: admin edits the question's marks after submission.
        self.fx["q1"].marks = Decimal("99.00")
        self.fx["q1"].save()

        # F: historical marks/result remain unchanged -- both the frozen
        # aggregate AND the per-question denominator shown in review.
        q = self._review()
        self.assertEqual(Decimal(str(q["marks_awarded"])), original_marks_awarded)
        self.assertEqual(Decimal(str(q["marks"])), original_marks_shown)
        self.assertEqual(Decimal(str(q["marks"])), Decimal("2.00"))  # never 99.00

        attempt = AssessmentAttempt.objects.get(pk=self.attempt_id)
        self.assertEqual(attempt.score, original_score)
        self.assertEqual(attempt.total_marks, original_total_marks)

    def test_g_h_option_text_change_does_not_change_historical_review(self):
        before = self._review()
        original_text = next(o for o in before["options"] if o["id"] == self.fx["q1_a"].id)["option_text"]

        # G: admin edits the option's text after submission.
        self.fx["q1_a"].option_text = "Completely different wording"
        self.fx["q1_a"].save()

        # H: historical review still shows the ORIGINAL text.
        after = self._review()
        a_option = next(o for o in after["options"] if o["id"] == self.fx["q1_a"].id)
        self.assertEqual(a_option["option_text"], original_text)
        self.assertNotEqual(a_option["option_text"], "Completely different wording")

    def test_i_in_progress_attempts_unaffected(self):
        """A fresh, still-active attempt must behave exactly as before --
        the safe live representation, no snapshot involved at all."""
        second = self.client.post(reverse('assessment-start', kwargs={'pk': self.fx["assessment"].id}))
        self.assertEqual(second.status_code, status.HTTP_201_CREATED)
        get_response = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': second.data["attempt_id"]}))
        self.assertEqual(get_response.data["status"], "IN_PROGRESS")
        for q in get_response.data["questions"]:
            self.assertNotIn("answered_correctly", q)
            for opt in q["options"]:
                self.assertNotIn("is_correct_answer", opt)

    def test_j_no_raw_is_correct_field_leaks(self):
        q = self._review()
        for opt in q["options"]:
            self.assertNotIn("is_correct", opt)  # exact key check -- only is_correct_answer
            self.assertIn("is_correct_answer", opt)

    def test_k_cross_student_isolation_after_correction(self):
        other_student = User.objects.create_user(username="correction_student_b", password="pw")
        self.client.force_authenticate(other_student)
        response = self.client.get(reverse('assessment-attempt-detail', kwargs={'pk': self.attempt_id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
