"""
Who a Zoom participant is, and who must never be counted as a student.

One shared exclusion check is used by both the live webhook path and the
post-meeting report path. Excluded people are neither counted as students nor
sent to the unmatched-participant review queue.

Identity evidence is limited to reliable identifiers: Zoom registrant ID,
customer key (join token), an unambiguous enrolled-student email, and a stable
Zoom account ID an admin explicitly linked before. Display names and device
names are never used to identify anyone.
"""
import re

from django.conf import settings

# A stable Zoom account ID (present only for participants signed in to Zoom):
# an opaque base64-like string. Per-meeting participant IDs are numeric and are
# never treated as an account ID.
_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


def participant_email(participant):
    return str(participant.get("email") or participant.get("user_email") or "").strip()


def stable_zoom_account_id(participant):
    """The signed-in Zoom account ID, or "" for guests / unknown."""
    if not isinstance(participant, dict):
        return ""
    for key in ("participant_user_id", "id"):
        value = str(participant.get(key) or "").strip()
        if value and not value.isdigit() and _ACCOUNT_ID_RE.match(value):
            return value
    return ""


def per_meeting_participant_id(participant):
    """Zoom's per-meeting participant ID (only valid within one meeting run)."""
    return str(participant.get("user_id") or "").strip()


def is_class_member(live_class, user):
    if user is None:
        return False
    if live_class.batch_id and live_class.batch.students.filter(student=user).exists():
        return True
    if live_class.course_id and live_class.course.enrollments.filter(user=user).exists():
        return True
    return False


def teaching_staff_user_ids(live_class):
    """Instructor, substitute, batch instructor/mentor and course teaching staff."""
    cached = getattr(live_class, "_teaching_staff_ids", None)
    if cached is not None:
        return cached
    from courses.models import CourseInstructor

    ids = {live_class.instructor_id, live_class.substitute_instructor_id}
    if live_class.batch_id:
        ids.update({live_class.batch.instructor_id, live_class.batch.mentor_id})
    if live_class.course_id:
        ids.update(CourseInstructor.objects.filter(course_id=live_class.course_id).values_list("user_id", flat=True))
    ids.discard(None)
    live_class._teaching_staff_ids = ids
    return ids


def excluded_user_reason(user, live_class):
    """Why this LMS user must not be counted as a student of this class, or None."""
    if user is None:
        return None
    if user.is_superuser:
        return "superuser"
    if user.is_staff:
        return "staff"
    if user.pk in getattr(settings, "ZOOM_ATTENDANCE_EXCLUDED_USER_IDS", frozenset()):
        return "excluded_account"
    if (user.email or "").strip().lower() in getattr(settings, "ZOOM_ATTENDANCE_EXCLUDED_EMAILS", frozenset()):
        return "test_account"
    if user.pk in teaching_staff_user_ids(live_class):
        return "teaching_staff"
    return None


def _host_pool_identifiers():
    from courses.models import ZoomHostAccount

    emails, ids = set(), set()
    for email, zoom_user_id in ZoomHostAccount.objects.values_list("email", "zoom_user_id"):
        if email:
            emails.add(email.strip().lower())
        if zoom_user_id:
            ids.add(zoom_user_id.strip())
    return emails, ids


def participant_exclusion_reason(live_class, participant):
    """
    Why a Zoom participant must be skipped entirely (not counted, not queued),
    judged from the participant's own identifiers, or None.
    """
    from django.contrib.auth import get_user_model
    from courses.models import ZoomIdentityLink

    email = participant_email(participant).lower()
    account_id = stable_zoom_account_id(participant)

    host_emails, host_ids = _host_pool_identifiers()
    if (email and email in host_emails) or (account_id and account_id in host_ids):
        return "zoom_host_account"
    if email and email in getattr(settings, "ZOOM_ATTENDANCE_EXCLUDED_EMAILS", frozenset()):
        return "test_account"

    if email:
        users = list(get_user_model().objects.filter(email__iexact=email))
        # Excluded only when the email belongs solely to excluded people; if an
        # eligible enrolled student shares it, identity resolution decides.
        if users and all(excluded_user_reason(u, live_class) for u in users):
            return excluded_user_reason(users[0], live_class)

    if account_id:
        link = ZoomIdentityLink.objects.filter(zoom_account_id=account_id, is_ambiguous=False).select_related("student").first()
        if link:
            reason = excluded_user_reason(link.student, live_class)
            if reason:
                return reason
    return None
