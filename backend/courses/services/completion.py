"""
Phase 4.5. Backend-authoritative module/course completion -- the single
place this is computed (mirrors access.py's/assessment_scoring.py's own
"one place, not duplicated per call site" precedent), so a future phase
(e.g. certificates) has exactly one function to call rather than
re-deriving completion itself.

Deliberately pure / DB-free: both functions take already-serialized data
(lesson dicts, assessment summary dicts, per-module completion dicts) and
do no querying of their own -- callers (ModuleSerializer/CourseSerializer)
are responsible for fetching everything ONCE per request (see
CourseViewSet.get_serializer_context's `completed_lesson_ids` and
`student_attempts_by_assessment_id`), so this module can never itself
introduce an N+1.

Completion rule (see the Phase 4.5 report for the full reasoning):

    A lesson is complete iff LessonProgress.completed is True for this
    user -- the existing, unmodified Phase-0-era source of truth.

    An assessment is complete iff its Phase 4.3 per-student `status` is
    exactly "PASSED" (in-progress/failed/timed-out/max-attempts-reached/
    never-attempted are all NOT complete -- one attempt existing is never
    enough on its own).

    A module is complete iff EVERY applicable item (its lessons + its
    PUBLISHED assessments) is complete. An empty module (zero applicable
    items) is vacuously complete -- the only deterministic rule that
    guarantees an empty module can never permanently block course
    completion (an incomplete module that can NEVER become complete
    would be a product bug, not a safe default).

    A course is complete iff EVERY module is complete (same vacuous-truth
    rule for a course with zero modules).
"""


def compute_module_completion(lessons_data, assessments_data):
    """
    lessons_data: the module's already-serialized lesson dicts, each with
    an `is_completed` key (injected by ModuleSerializer.to_representation
    from the request-scoped `completed_lesson_ids` set).
    assessments_data: the module's already-serialized assessment summary
    dicts (Phase 4.3's `_serialize_assessment_summary_for_student`
    output), each with a `status` key -- already filtered to
    is_published=True assessments only by get_assessments, so an
    unpublished assessment never reaches this function at all and can
    never block completion.

    Returns {is_completed, completion_percentage, completed_item_count,
    total_item_count}.
    """
    total = len(lessons_data) + len(assessments_data)
    if total == 0:
        return {
            "is_completed": True,
            "completion_percentage": 100,
            "completed_item_count": 0,
            "total_item_count": 0,
        }

    completed = (
        sum(1 for lesson in lessons_data if lesson.get("is_completed"))
        + sum(1 for assessment in assessments_data if assessment.get("status") == "PASSED")
    )
    return {
        "is_completed": completed == total,
        "completion_percentage": round(completed / total * 100),
        "completed_item_count": completed,
        "total_item_count": total,
    }


def compute_course_completion(modules_completion):
    """
    modules_completion: the course's already-computed per-module
    completion dicts (compute_module_completion's own return shape),
    one per module, in any order.

    Returns {is_completed, completion_percentage, completed_module_count,
    total_module_count}.
    """
    total = len(modules_completion)
    if total == 0:
        return {
            "is_completed": True,
            "completion_percentage": 100,
            "completed_module_count": 0,
            "total_module_count": 0,
        }

    completed = sum(1 for module in modules_completion if module.get("is_completed"))
    return {
        "is_completed": completed == total,
        "completion_percentage": round(completed / total * 100),
        "completed_module_count": completed,
        "total_module_count": total,
    }


LOCKED_COMPLETION = {
    "is_completed": None,
    "completion_percentage": None,
    "completed_item_count": None,
    "total_item_count": None,
}

LOCKED_COURSE_COMPLETION = {
    "is_completed": None,
    "completion_percentage": None,
    "completed_module_count": None,
    "total_module_count": None,
}
