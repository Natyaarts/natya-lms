"""
Phase 4.6. The single place certificate eligibility/generation logic
lives (mirrors access.py's/assessment_scoring.py's/completion.py's own
"one authoritative place" precedent).

Deliberately contains ZERO completion RULES of its own -- module/course
completion is computed by calling the actual Phase 4.5 functions
(compute_module_completion/compute_course_completion) and the actual
Phase 4.3 per-assessment status function
(_serialize_assessment_summary_for_student), the exact same functions
ModuleSerializer/CourseSerializer call. Certificates can never drift from
the authoritative rule, because there is no second copy of it here.

This does NOT go through CourseSerializer/ModuleSerializer themselves,
though -- that would mean fully serializing every lesson (with
TranslatedAudioSerializer nesting) and every assessment (with its full
SafeQuestionSerializer question list) just to read one boolean, which is
exactly the "repeatedly query every course module, lesson, or assessment"
Phase 4.6 Section 20 warns against. Instead: a handful of FIXED-COUNT
queries (never one per module) build the same lightweight
{'is_completed': ...}/{'status': ...} dicts those functions expect.
"""
from collections import defaultdict

from django.db import IntegrityError, transaction

from ..models import Assessment, AssessmentAttempt, Certificate, LessonProgress, VideoLesson


def is_course_completed_for_user(course, user):
    """
    True iff every module of `course` is complete for `user`, per the
    Phase 4.5/4.3 rules (reused, not re-derived). Returns False for an
    anonymous/unauthenticated user (there is no "their own completion" to
    check) rather than raising.

    Five fixed-count queries total, regardless of how many modules the
    course has: modules, all-lessons-for-course, all-published-
    assessments-for-course, this user's completed-lesson ids, this user's
    attempts for this course's assessments.
    """
    if not user or not user.is_authenticated:
        return False

    from .completion import compute_course_completion, compute_module_completion
    from ..serializers import _serialize_assessment_summary_for_student

    modules = list(course.modules.all())
    module_ids = [m.id for m in modules]

    lessons_by_module = defaultdict(list)
    for lesson_id, module_id in VideoLesson.objects.filter(module_id__in=module_ids).values_list('id', 'module_id'):
        lessons_by_module[module_id].append(lesson_id)

    # question_count is annotated because _serialize_assessment_summary_for_student
    # (Phase 4.3) reads it directly off the assessment -- exactly matching
    # ModuleSerializer.get_assessments' own query, so this can never drift
    # from what that function expects.
    from django.db.models import Count
    assessments_by_module = defaultdict(list)
    for assessment in Assessment.objects.filter(module_id__in=module_ids, is_published=True).annotate(question_count=Count('questions')):
        assessments_by_module[assessment.module_id].append(assessment)

    completed_lesson_ids = set(
        LessonProgress.objects.filter(user=user, completed=True, lesson__module_id__in=module_ids)
        .values_list('lesson_id', flat=True)
    )

    all_assessment_ids = [a.id for assessments in assessments_by_module.values() for a in assessments]
    attempts_by_assessment = defaultdict(list)
    for attempt in AssessmentAttempt.objects.filter(student=user, assessment_id__in=all_assessment_ids):
        attempts_by_assessment[attempt.assessment_id].append(attempt)

    modules_completion = []
    for module in modules:
        lessons_data = [{'is_completed': lid in completed_lesson_ids} for lid in lessons_by_module.get(module.id, [])]
        assessments_data = [
            _serialize_assessment_summary_for_student(a, attempts_by_assessment.get(a.id, []))
            for a in assessments_by_module.get(module.id, [])
        ]
        modules_completion.append(compute_module_completion(lessons_data, assessments_data))

    return bool(compute_course_completion(modules_completion)['is_completed'])


def get_or_create_certificate(course, user):
    """
    Idempotent: returns (certificate_or_None, created_bool). Eligibility
    is checked FRESH here, immediately before any write -- never trusts a
    stale or client-supplied completion flag.

    Concurrency: the UniqueConstraint on (student, course) is the actual
    safety guarantee (two simultaneous requests can't both create a row);
    the try/except IntegrityError-then-refetch idiom mirrors
    NotificationService.create_notification's own established
    idempotency-key handling exactly, rather than inventing a new pattern.
    """
    if not is_course_completed_for_user(course, user):
        return None, False

    existing = Certificate.objects.filter(student=user, course=course).first()
    if existing:
        return existing, False

    display_name = f"{user.first_name} {user.last_name}".strip() or user.username
    try:
        with transaction.atomic():
            certificate = Certificate.objects.create(
                student=user,
                course=course,
                learner_name_snapshot=display_name,
                course_title_snapshot=course.title,
            )
        return certificate, True
    except IntegrityError:
        return Certificate.objects.get(student=user, course=course), False


def maybe_issue_certificate_and_notify(course, user):
    """
    The eager-trigger entry point, called right after a lesson is marked
    complete or a passing assessment attempt is submitted -- see
    VideoLessonViewSet.progress / AssessmentAttemptViewSet.submit. Never
    raises: certificate issuance is a side effect of learning activity,
    not the main flow, and must never break the request that triggered
    it (same principle NotificationService/AdminAuditLog.record already
    establish elsewhere in this codebase). A lazy fallback still exists
    (CertificateViewSet.by_course), so a swallowed failure here is never
    a permanently stuck state for the learner.
    """
    import logging
    logger = logging.getLogger('courses.certificates')
    try:
        certificate, created = get_or_create_certificate(course, user)
        if created:
            from notifications.models import NotificationType
            from notifications.services import NotificationService
            NotificationService.create_notification(
                recipient=user,
                title="Certificate earned!",
                body=f"You've completed \"{course.title}\" and earned a certificate.",
                notification_type=NotificationType.CERTIFICATE,
                idempotency_key=f"certificate_issued:{certificate.id}",
            )
    except Exception:
        logger.error("Certificate issuance failed for user=%s course=%s", getattr(user, 'id', None), getattr(course, 'id', None), exc_info=True)
