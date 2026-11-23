"""
Gate for AUTOMATIC Zoom attendance reconciliation (catch-up task,
auto-complete, meeting.ended and the reconcile task itself).

Automatic reconciliation only runs for classes scheduled at or after
settings.ZOOM_ATTENDANCE_AUTOMATION_START, so switching the feature on never
finalizes classes that ran before it was enabled. If the setting is missing,
unparseable or not timezone-aware, automatic reconciliation does nothing.

Manual Sync (attendance/reconcile endpoint) and the recover_zoom_attendance
command do not use this gate: older classes are handled explicitly by an
admin, dry-run first.
"""
import datetime
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def automation_start():
    """
    The configured start as an aware datetime, or None when missing/invalid.
    Format: ISO 8601 with an explicit UTC offset, e.g.
    2026-11-25T00:00:00+05:30 or 2026-11-24T18:30:00Z.
    """
    raw = str(getattr(settings, "ZOOM_ATTENDANCE_AUTOMATION_START", "") or "").strip()
    if not raw:
        return None
    try:
        value = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00") if raw.endswith("Z") else raw)
    except ValueError:
        logger.warning("[ZoomAttendance] ZOOM_ATTENDANCE_AUTOMATION_START is not a valid ISO 8601 date-time; automatic reconciliation is off.")
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        logger.warning("[ZoomAttendance] ZOOM_ATTENDANCE_AUTOMATION_START has no UTC offset; automatic reconciliation is off.")
        return None
    return value


def automatic_reconciliation_allowed(live_class):
    """True only if the start is configured and the class is scheduled at or after it."""
    start = automation_start()
    if start is None or not live_class.scheduled_start:
        return False
    return live_class.scheduled_start >= start
