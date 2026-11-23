"""
Zoom meeting "runs" (instances) for a live class, derived from the stored
meeting.started / meeting.ended webhook events.

A teacher can end and restart a meeting; each run gets its own Zoom UUID.
Attendance must not be finalized while any run of the class is still active,
and a run that Zoom reported as ended is evidence the class genuinely ended.

Read-only; events are matched to the class by its Zoom identifiers and kept
only if the run started inside the class's time window (a recurring/shared
meeting ID could otherwise attach another occurrence's events).
"""
import datetime

from django.utils import timezone

from courses.services.conducted_time import (
    WINDOW_AFTER,
    WINDOW_BEFORE,
    _class_event_filter,
    _meeting_object,
    _parse,
)

# A run with a start but no end is treated as abandoned (missed meeting.ended)
# after this long, so a lost webhook cannot block finalization forever.
STALE_ACTIVE_RUN = datetime.timedelta(hours=8)


def class_window(live_class):
    start = live_class.scheduled_start
    end = start + datetime.timedelta(minutes=live_class.duration_minutes or 60)
    return start - WINDOW_BEFORE, end + WINDOW_AFTER


def meeting_runs(live_class):
    """
    Returns {uuid: {"started_at": dt|None, "ended_at": dt|None}} for runs of
    this class's meeting that started inside the class window.
    """
    from courses.models import ZoomWebhookEvent

    if not live_class.scheduled_start:
        return {}
    window_start, window_end = class_window(live_class)
    runs = {}
    events = (
        ZoomWebhookEvent.objects
        .filter(_class_event_filter(live_class))
        .filter(event_type__in=("meeting.started", "meeting.ended"))
        .only("event_type", "payload", "meeting_uuid")
    )
    for ev in events:
        obj = _meeting_object(ev.payload)
        uuid = str(obj.get("uuid") or ev.meeting_uuid or "").strip()
        start = _parse(obj.get("start_time"))
        if not uuid or start is None or not (window_start <= start <= window_end):
            continue
        run = runs.setdefault(uuid, {"started_at": None, "ended_at": None})
        if run["started_at"] is None or start < run["started_at"]:
            run["started_at"] = start
        if ev.event_type == "meeting.ended":
            end = _parse(obj.get("end_time"))
            if end is not None and (run["ended_at"] is None or end > run["ended_at"]):
                run["ended_at"] = end
    return runs


def run_state(live_class, now=None, refresh=False):
    """
    {"active": bool, "ended": bool, "uuids": [...]} -- cached on the instance.
    active: some run started (recently) and has no meeting.ended yet.
    ended:  at least one run was reported ended by Zoom.
    """
    cached = getattr(live_class, "_zoom_run_state", None)
    if cached is not None and not refresh:
        return cached
    now = now or timezone.now()
    runs = meeting_runs(live_class)
    active = any(
        r["ended_at"] is None and r["started_at"] is not None and now - r["started_at"] < STALE_ACTIVE_RUN
        for r in runs.values()
    )
    ended = any(r["ended_at"] is not None for r in runs.values())
    state = {"active": active, "ended": ended, "uuids": sorted(runs)}
    live_class._zoom_run_state = state
    return state
