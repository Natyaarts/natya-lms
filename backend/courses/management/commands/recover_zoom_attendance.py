"""
Historical Zoom attendance recovery for completed Zoom classes on one date.

    # Preview only (default) -- nothing is committed:
    python manage.py recover_zoom_attendance --date 2026-11-20
    python manage.py recover_zoom_attendance --date 2026-11-20 --class-id 123 --output /tmp/audit.json

    # Apply after reviewing the preview:
    python manage.py recover_zoom_attendance --date 2026-11-20 --class-id 123 --apply

For each class it fetches the actual Zoom Past Meeting Participants Report
(read-only GET) and runs the SAME code path as live reconciliation
(ZoomAttendanceService.apply_meeting_report). In dry-run mode that runs
inside a database transaction that is always rolled back, so the preview is
exactly what --apply would do and nothing is written. Re-running is
idempotent (report sessions are keyed and updated in place).

Manual status and manual duration overrides are never changed. The audit
report identifies students by ID only (no names, emails or phone numbers);
unmatched Zoom participants are reported as counts.

Safety: --apply refuses a class whose report contains rows outside the
class's time window (a recurring meeting ID can return another occurrence's
report when no meeting UUID is stored).
"""
import datetime
import json
import zoneinfo

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from courses.models import Attendance, LiveClass, UnmatchedZoomParticipant
from courses.services.attendance_classification import attended_seconds, scheduled_end
from courses.services.zoom_attendance import ZoomAttendanceService, parse_zoom_timestamp

WINDOW_BEFORE = datetime.timedelta(hours=2)
WINDOW_AFTER = datetime.timedelta(hours=3)


class _DryRunRollback(Exception):
    pass


def _snapshot(live_class):
    rows = {}
    for att in Attendance.objects.filter(live_class=live_class).prefetch_related('sessions'):
        att.live_class = live_class
        seconds = attended_seconds(att)
        metrics = live_class.calculate_attendance_metrics(seconds)
        rows[att.student_id] = {
            "student_id": att.student_id,
            "status": att.status,
            "duration_minutes": att.duration_minutes,
            "attended_seconds": int(seconds),
            "attendance_percentage": metrics["attendance_percentage"],
            "zoom_reported_duration_seconds": att.zoom_reported_duration_seconds,
            "sessions": len(att.sessions.all()),
            "manual_status": att.is_manual_override,
            "manual_duration": att.is_duration_manual_override,
            "reconciliation_status": att.reconciliation_status,
        }
    return rows


def _pending_unmatched(live_class):
    qs = UnmatchedZoomParticipant.objects.filter(live_class=live_class, status=UnmatchedZoomParticipant.Status.PENDING)
    return {"count": qs.count(), "total_seconds": sum(u.duration_seconds for u in qs)}


class Command(BaseCommand):
    help = "Preview (default) or apply Zoom attendance recovery for completed Zoom classes on a date."

    def add_arguments(self, parser):
        parser.add_argument('--date', required=True, help="Class date, YYYY-MM-DD, in --tz.")
        parser.add_argument('--tz', default='Asia/Kolkata', help="Timezone for --date (default Asia/Kolkata).")
        parser.add_argument('--class-id', type=int, action='append', dest='class_ids',
                            help="Limit to this LiveClass ID (repeatable). Recommended: test one class first.")
        parser.add_argument('--apply', action='store_true', help="Commit the changes. Without it, nothing is written.")
        parser.add_argument('--output', help="Also write the JSON audit report to this path.")

    def handle(self, *args, **opts):
        try:
            day = datetime.date.fromisoformat(opts['date'])
            tz = zoneinfo.ZoneInfo(opts['tz'])
        except (ValueError, zoneinfo.ZoneInfoNotFoundError) as exc:
            raise CommandError(f"Invalid --date/--tz: {exc}")

        apply = bool(opts['apply'])
        day_start = datetime.datetime.combine(day, datetime.time.min, tzinfo=tz)
        day_end = day_start + datetime.timedelta(days=1)

        classes = LiveClass.objects.filter(
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            status=LiveClass.ClassStatus.COMPLETED,
            scheduled_start__gte=day_start,
            scheduled_start__lt=day_end,
        ).order_by('scheduled_start')
        if opts['class_ids']:
            classes = classes.filter(id__in=opts['class_ids'])

        mode = "APPLY" if apply else "DRY-RUN (nothing will be written)"
        self.stdout.write(f"Zoom attendance recovery -- {mode}")
        self.stdout.write(f"Date {day.isoformat()} ({opts['tz']}): {classes.count()} completed Zoom class(es)\n")

        report = {
            "mode": "apply" if apply else "dry_run",
            "date": day.isoformat(),
            "timezone": opts['tz'],
            "generated_at": timezone.now().isoformat(),
            "classes": [],
        }
        for live_class in classes:
            entry = self._process_class(live_class, apply)
            report["classes"].append(entry)
            self._print_entry(entry)

        totals = {}
        for e in report["classes"]:
            totals[e["result"]] = totals.get(e["result"], 0) + 1
        report["summary"] = totals
        self.stdout.write(f"\nSummary: {totals or 'no classes'}")
        if not apply:
            self.stdout.write("DRY-RUN: no changes were committed. Re-run with --apply to write them.")

        if opts.get('output'):
            with open(opts['output'], 'w', encoding='utf-8') as fh:
                json.dump(report, fh, indent=2, default=str)
            self.stdout.write(f"Audit report written to {opts['output']}")

    def _process_class(self, live_class, apply):
        entry = {
            "live_class_id": live_class.id,
            "scheduled_start": live_class.scheduled_start.isoformat(),
            "duration_minutes": live_class.duration_minutes,
            "meeting_ref": "uuid" if live_class.zoom_uuid else ("meeting_id" if live_class.zoom_meeting_id else None),
        }
        if not entry["meeting_ref"]:
            entry["result"] = "skipped_no_meeting_id"
            return entry

        try:
            fetched = ZoomAttendanceService.fetch_meeting_report(live_class)
        except Exception as exc:
            entry.update(result="error_fetching_report", error=str(exc))
            return entry
        if fetched.get("status") != "success":
            entry.update(result=f"report_{fetched.get('status')}", error=str(fetched.get("error", ""))[:300])
            return entry

        participants = fetched["participants"]
        window_start = live_class.scheduled_start - WINDOW_BEFORE
        window_end = scheduled_end(live_class) + WINDOW_AFTER
        out_of_window = sum(
            1 for p in participants
            if p.get('join_time') and not (window_start <= parse_zoom_timestamp(p['join_time']) <= window_end)
        )
        entry.update(report_rows=len(participants), report_rows_out_of_window=out_of_window)

        before = _snapshot(live_class)
        unmatched_before = _pending_unmatched(live_class)
        try:
            with transaction.atomic():
                result = ZoomAttendanceService.apply_meeting_report(live_class, participants)
                after = _snapshot(live_class)
                unmatched_after = _pending_unmatched(live_class)
                if not apply or out_of_window:
                    raise _DryRunRollback()
            committed = True
        except _DryRunRollback:
            committed = False
        except Exception as exc:
            entry.update(result="error_applying_report", error=str(exc))
            return entry

        students = []
        for sid in sorted(set(before) | set(after)):
            b, a = before.get(sid, {}), after.get(sid, {})
            students.append({
                "student_id": sid,
                "status_before": b.get("status"),
                "status_after": a.get("status"),
                "changed": b.get("status") != a.get("status"),
                "duration_minutes_before": b.get("duration_minutes"),
                "duration_minutes_after": a.get("duration_minutes"),
                "attendance_percentage": a.get("attendance_percentage"),
                "zoom_sessions": a.get("sessions"),
                "manual_status": a.get("manual_status"),
                "manual_duration": a.get("manual_duration"),
                "new_record": sid not in before,
            })

        entry.update(
            matched_rows=result.get("reconciled"),
            unmatched_rows=result.get("unmatched"),
            skipped_rows=result.get("skipped"),
            classification=result.get("classification"),
            unmatched_pending_before=unmatched_before,
            unmatched_pending_after=unmatched_after,
            students=students,
            committed=committed,
        )
        if out_of_window:
            # Never committed: the report may belong to another occurrence of a recurring meeting.
            entry["result"] = "refused_out_of_window_rows" if apply else "previewed_WARNING_out_of_window_rows"
        elif committed:
            entry["result"] = "applied"
        else:
            entry["result"] = "previewed"
        return entry

    def _print_entry(self, e):
        w = self.stdout.write
        w(f"\n=== LiveClass #{e['live_class_id']}  start={e['scheduled_start']}  "
          f"duration={e['duration_minutes']}m  ref={e['meeting_ref']}  -> {e['result']}")
        if e.get("error"):
            w(f"    error: {e['error']}")
        if "students" not in e:
            return
        w(f"    report rows={e['report_rows']} matched={e['matched_rows']} unmatched={e['unmatched_rows']} "
          f"skipped={e['skipped_rows']} out_of_window={e['report_rows_out_of_window']}")
        c = e.get("classification") or {}
        w(f"    final={c.get('final')} present={c.get('present')} absent={c.get('absent')} "
          f"other={c.get('other')} changed={c.get('changed')} manual={c.get('manual')}")
        u = e["unmatched_pending_after"]
        w(f"    unmatched participants pending review: {u['count']} (total {u['total_seconds'] // 60} min)")
        w("    student_id  status_before -> status_after  minutes  pct     sessions  flags")
        for s in e["students"]:
            flags = ",".join(f for f, on in (("manual_status", s["manual_status"]),
                                              ("manual_duration", s["manual_duration"]),
                                              ("new", s["new_record"]),
                                              ("CHANGED", s["changed"])) if on)
            w(f"    {s['student_id']:>10}  {str(s['status_before']):>8} -> {str(s['status_after']):<8}  "
              f"{str(s['duration_minutes_after']):>7}  {str(s['attendance_percentage']):>6}%  "
              f"{str(s['zoom_sessions']):>8}  {flags}")
