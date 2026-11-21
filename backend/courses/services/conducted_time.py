"""
Actual ("conducted") time of a live class, derived from the Zoom
`meeting.started` / `meeting.ended` webhook events already stored in
ZoomWebhookEvent (raw payload). No schema change and no Zoom API call, so it
also works for classes that ran before this existed.

Read-only. Events are matched to the class by its own Zoom identifiers and
then kept only if the meeting actually started inside this class's time
window -- a recurring Zoom meeting re-uses one meeting id for every
occurrence, and the webhook handler may have attached an occurrence's event
to a different LiveClass of the same series.
"""
import datetime

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

ENDED_EVENTS = ('meeting.ended',)
STARTED_EVENTS = ('meeting.started',)

# A meeting counts for this class if it started between 2h before the
# scheduled start and 3h after the scheduled end.
WINDOW_BEFORE = datetime.timedelta(hours=2)
WINDOW_AFTER = datetime.timedelta(hours=3)


def _parse(value):
    if not value or not isinstance(value, str):
        return None
    dt = parse_datetime(value)
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, datetime.timezone.utc)
    return dt


def _meeting_object(payload):
    if not isinstance(payload, dict):
        return {}
    inner = payload.get('payload')
    obj = inner.get('object') if isinstance(inner, dict) else None
    return obj if isinstance(obj, dict) else {}


def _class_event_filter(live_class):
    q = Q(live_class_id=live_class.id)
    if live_class.zoom_uuid:
        q |= Q(meeting_uuid=live_class.zoom_uuid)
    if live_class.zoom_meeting_id:
        q |= Q(live_class__zoom_meeting_id=live_class.zoom_meeting_id)
    return q


def get_conducted_time(live_class, now=None):
    """
    Returns None when nothing is known, otherwise:
      {
        "started_at": iso, "ended_at": iso | None,
        "minutes": int | None,     # total of the (merged) meeting segments;
                                   # None if a start was seen but no end
        "segments": int,           # >1 when the host ended and restarted
        "in_progress": bool,       # class is LIVE and has no matching end yet
        "source": "zoom_webhook",
      }
    """
    from courses.models import ZoomWebhookEvent

    if not live_class.scheduled_start:
        return None
    now = now or timezone.now()
    window_start = live_class.scheduled_start - WINDOW_BEFORE
    window_end = (
        live_class.scheduled_start
        + datetime.timedelta(minutes=live_class.duration_minutes or 60)
        + WINDOW_AFTER
    )

    events = (
        ZoomWebhookEvent.objects
        .filter(_class_event_filter(live_class))
        .filter(event_type__in=ENDED_EVENTS + STARTED_EVENTS)
        .only('event_type', 'payload')
    )

    segments = set()
    starts = set()
    for ev in events:
        obj = _meeting_object(ev.payload)
        start = _parse(obj.get('start_time'))
        if start is None or not (window_start <= start <= window_end):
            continue
        if ev.event_type in ENDED_EVENTS:
            end = _parse(obj.get('end_time'))
            if end is not None and end > start:
                segments.add((start, end))
        else:
            starts.add(start)

    if not segments and not starts:
        return None

    # Merge overlapping segments (redeliveries, duplicate events).
    merged = []
    for start, end in sorted(segments):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    # A meeting.started with no meeting.ended after it is only "running" while
    # the class itself is LIVE; otherwise the end event was simply missed.
    last_end = merged[-1][1] if merged else None
    open_starts = sorted(s for s in starts if last_end is None or s >= last_end)
    in_progress = bool(open_starts) and live_class.status == 'LIVE'

    total_seconds = sum((end - start).total_seconds() for start, end in merged)
    if in_progress:
        total_seconds += max(0.0, (now - open_starts[0]).total_seconds())

    first_start = min([m[0] for m in merged] + open_starts)
    if not merged and not in_progress:
        minutes = None  # start seen, end never received
    else:
        minutes = int(total_seconds // 60)
    return {
        "started_at": first_start.isoformat(),
        "ended_at": None if (in_progress or last_end is None) else last_end.isoformat(),
        "minutes": minutes,
        "segments": len(merged) + (1 if in_progress else 0),
        "in_progress": in_progress,
        "source": "zoom_webhook",
    }
