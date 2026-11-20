"""
Who may see which historical recordings (sessions imported from the previous LMS).

Production stores each HistoricalRecordingSession against a course plus the old
platform's class id. Course enrollment is far too broad (one course holds many
batches and teachers), and names are not identities, so access comes only from
explicit, reviewed mappings written by `map_historical_recordings`:

  * Administrators (is_staff / is_superuser): every session.
  * Students: sessions whose source class has a CONFIRMED
    HistoricalRecordingClassMapping to a LiveBatch they belong to through
    LiveBatchStudent, and only while that batch is in the session's course.
  * Teachers / mentors: only sessions explicitly listed for their account in
    HistoricalRecordingTeacher, and only while the class mapping is CONFIRMED.
  * Everyone else, and every AMBIGUOUS / UNRESOLVED / unmapped session: nothing.

Course membership alone NEVER grants access, and names are never matched.
Files are only ever exposed when they are verified and point at the expected
private bucket and prefix.
"""
from django.db.models import Exists, OuterRef, Q

from .models import (
    HistoricalRecordingClassMapping,
    HistoricalRecordingFile,
    HistoricalRecordingSession,
    HistoricalRecordingTeacher,
)

RECORDING_BUCKET = "natya-wise-recordings"
RECORDING_PREFIX = "wise-recordings/"

# Accounts that must never be treated as teachers of historical recordings
# (1, 25, 96 are also in sync_zoom_host_accounts.EXCLUDED_TEACHER_IDS; 39 is a test account).
PROTECTED_USER_IDS = frozenset({1, 25, 39, 96})


def is_recording_admin(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def _confirmed_class_mappings():
    """CONFIRMED mappings for the outer session's class whose batch is in the session's course."""
    return HistoricalRecordingClassMapping.objects.filter(
        status=HistoricalRecordingClassMapping.Status.CONFIRMED,
        source_class_id=OuterRef("source_class_id"),
        batch__course_id=OuterRef("course_id"),
    )


def visible_sessions(user):
    """Sessions this user may list and play."""
    qs = HistoricalRecordingSession.objects.select_related("course")
    if is_recording_admin(user):
        return qs
    if not (user and user.is_authenticated and user.is_active):
        return qs.none()

    confirmed = _confirmed_class_mappings()
    as_student = Exists(confirmed.filter(batch__students__student_id=user.pk))
    condition = Q(as_student)

    if user.pk not in PROTECTED_USER_IDS and (
        getattr(user, "is_teacher", False) or getattr(user, "is_mentor", False)
    ):
        condition |= Q(
            Exists(HistoricalRecordingTeacher.objects.filter(session_id=OuterRef("pk"), teacher_id=user.pk)),
            Exists(confirmed),
        )

    return qs.filter(condition).exclude(source_class_id="")


def mapping_pending_for(user):
    """True when a non-admin currently has no historical recordings mapped to their account."""
    if is_recording_admin(user):
        return False
    return not visible_sessions(user).exists()


def playable_files():
    """Verified files in the expected private bucket/prefix (never unverified objects)."""
    return HistoricalRecordingFile.objects.filter(
        is_verified=True,
        s3_bucket=RECORDING_BUCKET,
        s3_key__startswith=RECORDING_PREFIX,
    )
