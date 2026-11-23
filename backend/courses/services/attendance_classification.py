"""
Automatic attendance classification from Zoom evidence -- the single place
that decides an Attendance row's automatic status.

Rules (global threshold from AttendanceConfiguration, via
LiveClass.calculate_attendance_metrics):

  * PRESENT  when attended % >= threshold. Applied at any time, including
             while the class is still running (it can only go up from there,
             because session time is a union of intervals that only grows).
  * ABSENT   when attended % <  threshold, but ONLY on a final pass (e.g.
             after the post-meeting Zoom report has been reconciled) and only
             once the class is no longer in progress. Mid-class webhooks never
             demote anyone, so a temporary disconnect cannot cause ABSENT.
  * Rows with is_manual_override=True never have their status changed.
  * Rows with is_duration_manual_override=True keep their duration_minutes;
    that admin-entered duration is then the attended time used to classify.

Attended time is Attendance.calculate_total_duration_seconds() (union of all
sessions -- webhook, report and manually-linked -- so overlaps are not double
counted). Once a class is over, still-open sessions (a lost "left" event) are
cut off at the meeting end instead of accruing time forever.
"""
import datetime

from django.db.models import Max
from django.utils import timezone

_UNSET = object()


def scheduled_end(live_class):
    if not live_class.scheduled_start:
        return None
    return live_class.scheduled_start + datetime.timedelta(minutes=live_class.duration_minutes or 60)


def is_class_in_progress(live_class, now=None):
    """True while the class is running: status LIVE, or SCHEDULED inside its scheduled window."""
    from courses.models import LiveClass

    now = now or timezone.now()
    if live_class.status == LiveClass.ClassStatus.LIVE:
        return True
    if live_class.status == LiveClass.ClassStatus.SCHEDULED and live_class.scheduled_start:
        return live_class.scheduled_start <= now < scheduled_end(live_class)
    return False


def meeting_end_estimate(live_class, refresh=False):
    """
    Best estimate of when the meeting ended: the latest leave time in the Zoom
    participant report for this class, else the scheduled end. Cached on the
    LiveClass instance; pass refresh=True after writing new report sessions.
    """
    from courses.models import AttendanceSession

    cached = getattr(live_class, '_meeting_end_estimate', _UNSET)
    if cached is not _UNSET and not refresh:
        return cached
    latest = (
        AttendanceSession.objects
        .filter(attendance__live_class=live_class, source=AttendanceSession.Source.REPORT)
        .aggregate(m=Max('leave_time'))['m']
    )
    end = latest or scheduled_end(live_class)
    live_class._meeting_end_estimate = end
    return end


def evidence_cutoff(att, now=None):
    """
    Time at which still-open sessions stop counting. While the class runs this
    is `now`. Afterwards it is the meeting end, so a session whose "left"
    event was lost cannot inflate attendance. Only queries the meeting end
    when the row actually has an open session.
    """
    from courses.models import AttendanceSession

    now = now or timezone.now()
    sessions = list(att.sessions.all())
    has_open_session = any(s.is_active and not s.leave_time for s in sessions)
    if not has_open_session or is_class_in_progress(att.live_class, now):
        return now
    # Zoom's report for this student is authoritative for when they left;
    # otherwise fall back to the meeting end for the whole class.
    own_report_end = max(
        (s.leave_time for s in sessions if s.source == AttendanceSession.Source.REPORT and s.leave_time),
        default=None,
    )
    end = own_report_end or meeting_end_estimate(att.live_class)
    return min(now, end) if end else now


def attended_seconds(att, now=None):
    """Attended time used for percentage/classification (and shown in the attendance APIs)."""
    if att.is_duration_manual_override:
        return (att.duration_minutes or 0) * 60
    return att.calculate_total_duration_seconds(current_time=evidence_cutoff(att, now))


def apply_automatic_attendance(att, *, final, now=None, save=False):
    """
    Recompute duration and apply the automatic status to one Attendance row.

    final=False  (webhooks / mid-class): may promote to PRESENT, never demotes.
    final=True   (after report reconciliation or manual linking): also sets
                 ABSENT below threshold -- unless the class is still in progress.

    Mutates `att`; saves only the changed fields when save=True. Returns a
    dict describing the decision (for logs, API results and audit reports).
    """
    from courses.models import Attendance

    now = now or timezone.now()
    live_class = att.live_class
    previous_status = att.status
    update_fields = []

    seconds = attended_seconds(att, now)
    if not att.is_duration_manual_override and att.sessions.exists():
        minutes = int(seconds // 60)
        if att.duration_minutes != minutes:
            att.duration_minutes = minutes
            update_fields.append('duration_minutes')

    metrics = live_class.calculate_attendance_metrics(seconds)
    final_allowed = bool(final) and not is_class_in_progress(live_class, now)

    new_status = previous_status
    if not att.is_manual_override:
        if metrics['suggested_status'] == Attendance.Status.PRESENT:
            new_status = Attendance.Status.PRESENT
        elif final_allowed:
            new_status = Attendance.Status.ABSENT
    if new_status != previous_status:
        att.status = new_status
        update_fields.append('status')

    if save and update_fields:
        att.save(update_fields=update_fields)

    return {
        'previous_status': previous_status,
        'status': att.status,
        'changed': att.status != previous_status,
        'attended_seconds': int(seconds),
        'attendance_percentage': metrics['attendance_percentage'],
        'suggested_status': metrics['suggested_status'],
        'threshold': metrics['effective_threshold'],
        'final': final_allowed,
        'manual_status': bool(att.is_manual_override),
        'manual_duration': bool(att.is_duration_manual_override),
        'update_fields': update_fields,
    }
