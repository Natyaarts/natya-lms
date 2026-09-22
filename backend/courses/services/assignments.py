"""
Phase 4.7. Submission/grading state-transition logic -- the single place
this lives (mirrors assessment_scoring.py's own "one authoritative place"
precedent). Deliberately contains NO authorization checks (that's the
view/permission layer's job, matching how assessment_scoring.py never
checks course access either) and NO calls into
courses/services/completion.py (assignments do not count toward Phase
4.5 completion -- explicit product decision, Phase 4.7 directive).
"""
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from ..models import AssignmentSubmission


class SubmissionNotAllowedError(Exception):
    """Raised when a new submission attempt isn't currently permitted --
    caught by the view and turned into a 400/409."""
    pass


class GradingValidationError(Exception):
    """Raised when a grading request's marks_awarded is invalid (negative
    or exceeds the assignment's max_marks) -- caught by the view and
    turned into a 400."""
    pass


def create_submission(assignment, student, content, submitted_file):
    """
    Race-safe creation of the next submission attempt. Mirrors
    AssessmentViewSet.start's exact select_for_update-before-deciding
    pattern: locks this (student, assignment) pair's existing rows before
    checking whether a new attempt is allowed, so two concurrent submit
    requests can't both slip past the check -- the partial unique
    constraint on AssignmentSubmission (student, assignment,
    status='SUBMITTED') is the final DB-level backstop either way.

    Allowed only when:
    - there is no existing submission at all (the first submission), or
    - the most recent submission's status is RETURNED_FOR_REVISION.
    Raises SubmissionNotAllowedError otherwise (a pending SUBMITTED
    attempt already exists, or the most recent attempt is already GRADED
    and this phase has no "resubmit after grading" path).
    """
    with transaction.atomic():
        existing = list(
            AssignmentSubmission.objects.select_for_update()
            .filter(student=student, assignment=assignment)
            .order_by('attempt_number')
        )
        latest = existing[-1] if existing else None

        if latest is not None and latest.status != AssignmentSubmission.Status.RETURNED_FOR_REVISION:
            if latest.status == AssignmentSubmission.Status.SUBMITTED:
                raise SubmissionNotAllowedError("You already have a submission awaiting grading for this assignment.")
            raise SubmissionNotAllowedError("This assignment has already been graded and cannot be resubmitted.")

        submission = AssignmentSubmission(
            assignment=assignment,
            student=student,
            attempt_number=len(existing) + 1,
            content=content or '',
            submitted_file=submitted_file,
        )
        # Plain .objects.create()/.save() never runs a model field's
        # `validators=[...]` -- those only fire via full_clean() (e.g. a
        # ModelForm). full_clean() is what actually enforces
        # ALLOWED_SUBMISSION_FILE_EXTENSIONS and
        # MAX_SUBMISSION_FILE_SIZE_BYTES on submitted_file; it raises
        # django.core.exceptions.ValidationError, which
        # AssignmentViewSet.submit already catches and turns into a 400.
        submission.full_clean()
        submission.save()
    return submission


def grade_submission(submission, grader, marks_awarded, feedback):
    """
    Grades a SUBMITTED submission (final for this attempt -- Phase 4.7
    has no path back from GRADED to RETURNED_FOR_REVISION; see the
    Phase 4.7 report). Validates marks_awarded against the assignment's
    max_marks here (not a plain model field validator, which can't see
    the related Assignment).
    """
    if marks_awarded is not None:
        marks_awarded = Decimal(str(marks_awarded))
        if marks_awarded < 0:
            raise GradingValidationError("marks_awarded cannot be negative.")
        if marks_awarded > submission.assignment.max_marks:
            raise GradingValidationError(f"marks_awarded cannot exceed this assignment's max_marks ({submission.assignment.max_marks}).")

    submission.status = AssignmentSubmission.Status.GRADED
    submission.marks_awarded = marks_awarded
    submission.feedback = feedback or ''
    submission.graded_by = grader
    submission.graded_at = timezone.now()
    submission.save()
    _notify_grading_result(submission, returned=False)
    return submission


def return_for_revision(submission, grader, feedback):
    """Marks a SUBMITTED submission RETURNED_FOR_REVISION -- the one
    transition that unblocks a new submission attempt (see
    create_submission)."""
    submission.status = AssignmentSubmission.Status.RETURNED_FOR_REVISION
    submission.feedback = feedback or ''
    submission.graded_by = grader
    submission.graded_at = timezone.now()
    submission.save()
    _notify_grading_result(submission, returned=True)
    return submission


def _notify_grading_result(submission, returned):
    """
    Never breaks the grading request on failure (same principle
    NotificationService/AdminAuditLog.record already establish
    elsewhere in this codebase).
    """
    import logging
    logger = logging.getLogger('courses.assignments')
    try:
        from notifications.models import NotificationType
        from notifications.services import NotificationService
        assignment = submission.assignment
        if returned:
            title = "Assignment returned for revision"
            body = f"Your submission for \"{assignment.title}\" was returned for revision. Check the feedback and resubmit."
        else:
            title = "Assignment graded"
            body = f"Your submission for \"{assignment.title}\" has been graded."
        NotificationService.create_notification(
            recipient=submission.student,
            title=title,
            body=body,
            notification_type=NotificationType.ASSIGNMENT,
            action_url="/dashboard",
            idempotency_key=f"assignment_submission_graded:{submission.id}:{submission.status}",
        )
    except Exception:
        logger.error("Assignment grading notification failed for submission=%s", getattr(submission, 'id', None), exc_info=True)
