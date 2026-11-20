"""
Who may see which historical recordings (sessions imported from the previous LMS).

Production stores each HistoricalRecordingSession against a course only. It has
no batch and no teacher-account link: just the old platform's class id/name and
the teacher's name as free text. Course enrollment is far too broad (one course
holds many batches and teachers), and names are not identities, so the policy is
deliberately fail-closed:

  * Administrators (is_staff / is_superuser): every session.
  * Students and teachers: only sessions with a confirmed mapping to their own
    batch / teacher account. No such mapping is stored yet, so they currently
    see none. Course membership alone NEVER grants access, and names are never
    matched.

Files are only ever exposed when they are verified and point at the expected
private bucket and prefix.
"""
from .models import HistoricalRecordingFile, HistoricalRecordingSession

RECORDING_BUCKET = "natya-wise-recordings"
RECORDING_PREFIX = "wise-recordings/"


def is_recording_admin(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.is_superuser))


def visible_sessions(user):
    """Sessions this user may list and play."""
    qs = HistoricalRecordingSession.objects.select_related("course")
    if is_recording_admin(user):
        return qs
    # Batch / teacher mappings are not stored yet -> nothing for non-admins.
    return qs.none()


def mapping_pending_for(user):
    """True when the user is denied only because confirmed mappings are not available yet."""
    return not is_recording_admin(user)


def playable_files():
    """Verified files in the expected private bucket/prefix (never unverified objects)."""
    return HistoricalRecordingFile.objects.filter(
        is_verified=True,
        s3_bucket=RECORDING_BUCKET,
        s3_key__startswith=RECORDING_PREFIX,
    )
