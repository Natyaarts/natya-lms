"""
Phase 4.2. Server-side answer validation and scoring for AssessmentAttempt
submission -- the single place this logic lives (mirrors access.py's own
"one place, not duplicated per call site" precedent). Never trust a
client-provided score/percentage/passed; this module is the only code
path that computes them.
"""
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError

from ..models import AssessmentAnswer, AssessmentAnswerOption, AssessmentAnswerOptionSnapshot, Question


class AnswerValidationError(ValidationError):
    """Raised when a submitted answer payload is malformed or violates a
    relational rule (option belongs to a different question, unknown
    question id, etc). Caught by the view and turned into a 400."""
    pass


def validate_and_normalize_answers(assessment, raw_answers):
    """
    Validates a submit payload's `answers` list against `assessment`'s
    actual questions/options, and returns a normalized
    {question_id: [option_id, ...]} dict (options de-duplicated, order
    irrelevant -- scoring only cares about set membership).

    Raises AnswerValidationError on anything a well-behaved client
    should never send (unknown question, option from another question,
    duplicate question entries, more than one option for a SINGLE_CHOICE
    question) -- these are client-error 400s, not silently-scored-as-wrong
    submissions.

    Does NOT check is_required here (that needs the full set of
    questions, done by the caller after normalizing) and does NOT score
    anything -- purely relational validation.

    Returns (normalized, questions_by_id) -- the caller (submit(), via
    score_attempt) reuses `questions_by_id` instead of this module
    re-querying the exact same questions+options a second time; the
    query cost is still O(1) either way (a single request-scoped fetch),
    this just halves the fixed constant.
    """
    if not isinstance(raw_answers, list):
        raise AnswerValidationError("'answers' must be a list.")

    questions_by_id = {q.id: q for q in assessment.questions.prefetch_related('options').all()}
    normalized = {}

    for entry in raw_answers:
        if not isinstance(entry, dict) or 'question_id' not in entry:
            raise AnswerValidationError("Each answer must be an object with a question_id.")

        try:
            question_id = int(entry['question_id'])
        except (TypeError, ValueError):
            raise AnswerValidationError("question_id must be an integer.")

        if question_id in normalized:
            raise AnswerValidationError(f"Duplicate answer submitted for question {question_id}.")

        question = questions_by_id.get(question_id)
        if question is None:
            raise AnswerValidationError(f"Question {question_id} does not belong to this assessment.")

        raw_option_ids = entry.get('option_ids', [])
        if not isinstance(raw_option_ids, list):
            raise AnswerValidationError(f"option_ids for question {question_id} must be a list.")

        try:
            option_ids = {int(oid) for oid in raw_option_ids}
        except (TypeError, ValueError):
            raise AnswerValidationError(f"option_ids for question {question_id} must be integers.")

        valid_option_ids = {o.id for o in question.options.all()}
        if not option_ids.issubset(valid_option_ids):
            raise AnswerValidationError(f"An option in question {question_id} does not belong to that question.")

        if question.question_type == Question.QuestionType.SINGLE_CHOICE and len(option_ids) > 1:
            raise AnswerValidationError(f"Question {question_id} is single-choice and cannot have more than one selected option.")

        normalized[question_id] = option_ids

    # is_required enforcement -- needs the full question set, not just
    # what the client happened to send.
    for question in questions_by_id.values():
        if question.is_required and not normalized.get(question.id):
            raise AnswerValidationError(f"Required question {question.id} was not answered.")

    return normalized, questions_by_id


def score_attempt(attempt, normalized_answers, questions=None):
    """
    Persists AssessmentAnswer/AssessmentAnswerOption/
    AssessmentAnswerOptionSnapshot rows (each answer frozen with its own
    is_correct/marks_awarded/marks_possible, and a full snapshot of every
    option's text/correctness at this exact moment -- Phase 4.4 +
    CORRECTION) for `normalized_answers` ({question_id: {option_id, ...}})
    and computes the final score/percentage/passed/total_marks for
    `attempt`. Does NOT save `attempt` itself -- the caller (inside its
    own transaction/lock) sets status/submitted_at alongside these and
    saves once.

    `questions` lets the caller pass the same questions_by_id.values()
    validate_and_normalize_answers already fetched, avoiding a second,
    identical query; falls back to fetching them itself if omitted (e.g.
    a direct/test call site that didn't go through validation first).

    SINGLE_CHOICE: full marks iff the one selected option is_correct.
    MULTIPLE_CHOICE: full marks iff the selected set exactly equals the
    correct-option set (all-or-nothing, no partial credit -- Phase 4.2's
    explicit, deliberate scope).
    Unanswered (or non-required, left blank) questions score zero and
    still count toward the total available marks -- see the Phase 4.2
    report for why the denominator isn't reduced for skipped optional
    questions.
    """
    total_score = Decimal("0.00")
    total_available = Decimal("0.00")

    if questions is None:
        questions = attempt.assessment.questions.prefetch_related('options').all()
    for question in questions:
        total_available += question.marks
        selected_ids = normalized_answers.get(question.id, set())
        options = list(question.options.all())

        correct_ids = {o.id for o in options if o.is_correct}
        is_correct = bool(selected_ids) and selected_ids == correct_ids
        marks_awarded = question.marks if is_correct else Decimal("0.00")
        total_score += marks_awarded

        # Phase 4.4 + CORRECTION: is_correct/marks_awarded/marks_possible
        # are frozen here, at scoring time, from the SAME data used to
        # compute the aggregate score above -- never re-derived later
        # from potentially-changed Question.marks/QuestionOption.is_correct
        # (see AssessmentAnswer's own docstring).
        answer = AssessmentAnswer.objects.create(
            attempt=attempt, question=question, is_correct=is_correct,
            marks_awarded=marks_awarded, marks_possible=question.marks,
        )
        if selected_ids:
            AssessmentAnswerOption.objects.bulk_create([
                AssessmentAnswerOption(answer=answer, option_id=option_id) for option_id in selected_ids
            ])

        # CORRECTION: a full snapshot of EVERY option for this question
        # (not just the selected ones) -- see
        # AssessmentAnswerOptionSnapshot's own docstring for why this
        # exists as a separate, complementary record from
        # AssessmentAnswerOption above.
        AssessmentAnswerOptionSnapshot.objects.bulk_create([
            AssessmentAnswerOptionSnapshot(
                answer=answer, option=o, option_text=o.option_text, was_correct=o.is_correct, order=o.order,
            )
            for o in options
        ])

    if total_available > 0:
        percentage = (total_score / total_available * Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    else:
        percentage = Decimal("0.00")

    passed = percentage >= attempt.assessment.passing_percentage

    attempt.score = total_score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    attempt.percentage = percentage
    attempt.passed = passed
    attempt.total_marks = total_available.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return attempt
