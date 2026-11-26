"""
READ-ONLY diagnosis of Zoom automatic attendance.

Prints configuration, worker/scheduler status and, per class, counts of the
evidence the attendance pipeline has (webhook events, Zoom sessions, review
queue, personal links, attendance statuses). With --zoom it also reads the
Zoom participant report and shows how each row WOULD be identified -- inside
a transaction that is always rolled back.

Never writes to the database, never changes Zoom, never prints names, emails,
phone numbers, tokens or join links. Safe to run on production.

  python manage.py zoom_attendance_diagnose --date 2026-11-24
  python manage.py zoom_attendance_diagnose --class-id 812 --zoom
"""
import datetime
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))


class _Rollback(Exception):
    pass


class Command(BaseCommand):
    help = "Read-only Zoom attendance diagnosis (counts only; never writes)."

    def add_arguments(self, parser):
        parser.add_argument("--class-id", type=int, action="append", default=[], help="LiveClass id (repeatable).")
        parser.add_argument("--date", help="All classes scheduled on this date (YYYY-MM-DD, India time).")
        parser.add_argument("--zoom", action="store_true",
                            help="Also read the Zoom participant report / meeting settings (read-only API calls).")

    # ------------------------------------------------------------------ output
    def out(self, label, value=""):
        self.stdout.write(f"  {label:<44} {value}")

    def head(self, title):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(title))

    # ------------------------------------------------------------------ main
    def handle(self, *args, **opts):
        from courses.models import LiveClass

        classes = list(LiveClass.objects.filter(id__in=opts["class_id"])) if opts["class_id"] else []
        if opts["date"]:
            try:
                day = datetime.date.fromisoformat(opts["date"])
            except ValueError:
                raise CommandError("--date must be YYYY-MM-DD")
            start = datetime.datetime.combine(day, datetime.time.min, tzinfo=IST)
            classes += list(
                LiveClass.objects.filter(scheduled_start__gte=start, scheduled_start__lt=start + datetime.timedelta(days=1))
                .exclude(id__in=[c.id for c in classes]).order_by("scheduled_start")
            )

        self.settings_report()
        self.workers_report()
        self.webhook_report()
        if opts["zoom"]:
            self.token_report()
        for lc in classes:
            self.class_report(lc, use_zoom=opts["zoom"])
        if not classes:
            self.stdout.write("\n(No classes selected: pass --class-id or --date for per-class detail.)")
        self.stdout.write("\nRead-only: nothing was changed.")

    # ------------------------------------------------------------------ sections
    def settings_report(self):
        from courses.models import AttendanceConfiguration
        from courses.services.attendance_automation import automation_start

        self.head("Settings")
        raw_start = getattr(settings, "ZOOM_ATTENDANCE_AUTOMATION_START", "")
        parsed = automation_start()
        self.out("ENABLE_ZOOM_ATTENDANCE_V2", getattr(settings, "ENABLE_ZOOM_ATTENDANCE_V2", False))
        self.out("ZOOM_ATTENDANCE_AUTOMATION_START", f"{raw_start or '(unset)'} -> {'valid' if parsed else 'NOT ACTIVE'}")
        self.out("ZOOM_ATTENDANCE_USE_REGISTRATION (personal links)", getattr(settings, "ZOOM_ATTENDANCE_USE_REGISTRATION", False))
        self.out("Zoom credentials present (account/client/secret)", " / ".join(
            "yes" if getattr(settings, k, "") else "NO"
            for k in ("ZOOM_ACCOUNT_ID", "ZOOM_CLIENT_ID", "ZOOM_CLIENT_SECRET")))
        self.out("Zoom webhook secret present", "yes" if getattr(settings, "ZOOM_WEBHOOK_SECRET_TOKEN", "") else "NO")
        self.out("Attendance threshold %", AttendanceConfiguration.get_default_percentage())
        self.out("Late rule (minutes, 0 = off)", AttendanceConfiguration.get_late_after_minutes())
        self.out("Excluded user ids (count)", len(getattr(settings, "ZOOM_ATTENDANCE_EXCLUDED_USER_IDS", ())))
        backend = settings.CACHES.get("default", {}).get("BACKEND", "")
        shared = "redis" in backend.lower()
        self.out("Cache backend", f"{backend.rsplit('.', 1)[-1]} ({'shared by all processes' if shared else 'PER-PROCESS'})")
        broker = str(getattr(settings, "CELERY_BROKER_URL", "") or "")
        self.out("Celery broker type", broker.split(":", 1)[0] if broker else "(unset)")
        sched = getattr(settings, "CELERY_BEAT_SCHEDULE", {})
        catch_up = sched.get("zoom-attendance-catch-up")
        self.out("Beat: zoom-attendance-catch-up", str(catch_up["schedule"]) if catch_up else "MISSING")

    def workers_report(self):
        self.head("Celery workers (live check, 3s timeout)")
        try:
            from core.celery import app
        except Exception:
            from celery import current_app as app
        try:
            insp = app.control.inspect(timeout=3)
            ping = insp.ping() or {}
            queues = insp.active_queues() or {}
        except Exception as exc:
            self.out("Inspect failed", exc.__class__.__name__)
            return
        self.out("Workers answering", len(ping))
        consuming = sorted({q.get("name") for qs in queues.values() for q in (qs or [])})
        self.out("Queues being consumed", ", ".join(consuming) or "(none)")
        self.out("'notifications' queue consumed (attendance tasks)", "yes" if "notifications" in consuming else "NO")

    def webhook_report(self):
        from courses.models import ZoomWebhookEvent

        self.head("Zoom webhooks received in the last 7 days (type / status: count)")
        since = timezone.now() - datetime.timedelta(days=7)
        rows = (
            ZoomWebhookEvent.objects.filter(received_at__gte=since, event_type__startswith="meeting.")
            .values("event_type", "status").annotate(n=Count("id")).order_by("event_type", "status")
        )
        if not rows:
            self.out("(none)", "-> Zoom is not sending meeting events, or the app subscription is off")
        for r in rows:
            self.out(f"{r['event_type']} / {r['status']}", r["n"])

    def token_report(self):
        from courses.services.zoom import ZoomService

        self.head("Zoom OAuth (read-only)")
        try:
            token = ZoomService.get_access_token()
            self.out("Access token", "obtained" if token else "EMPTY")
        except Exception as exc:
            self.out("Access token", f"FAILED ({exc.__class__.__name__})")

    def class_report(self, lc, use_zoom):
        from courses.models import (
            Attendance, AttendanceSession, LiveClassJoinIntent, LiveClassRegistrant,
            UnmatchedZoomParticipant, ZoomWebhookEvent,
        )
        from courses.services.attendance_automation import automatic_reconciliation_allowed
        from courses.services.attendance_classification import is_class_in_progress
        from courses.services.conducted_time import get_conducted_time
        from courses.services.zoom_attendance import ZoomAttendanceService

        self.head(f"LiveClass #{lc.id}")
        start = lc.scheduled_start.astimezone(IST).strftime("%Y-%m-%d %H:%M IST") if lc.scheduled_start else "-"
        self.out("Scheduled / duration / status", f"{start} / {lc.duration_minutes} min / {lc.status}")
        meeting_id = ZoomAttendanceService._meeting_id_for(lc)
        self.out("Provider", lc.meeting_provider)
        self.out("Zoom meeting id stored / usable / uuid stored",
                 f"{'yes' if lc.zoom_meeting_id else 'no'} / {'yes' if meeting_id else 'NO'} / {'yes' if lc.zoom_uuid else 'no'}")
        shared = 0
        if meeting_id:
            from courses.models import LiveClass
            shared = (LiveClass.objects.filter(zoom_meeting_id=meeting_id).count()
                      + LiveClass.objects.filter(zoom_meeting_id="", meeting_url__icontains=meeting_id).count())
        self.out("Classes sharing this meeting id", shared)
        self.out("Automatic reconciliation allowed for this class", automatic_reconciliation_allowed(lc))
        self.out("Attendance finalized", "yes" if lc.attendance_finalized_at else "no")
        self.out("Still in progress (blocks finalizing)", is_class_in_progress(lc))
        conducted = get_conducted_time(lc)
        self.out("Zoom conducted minutes", conducted["minutes"] if conducted else "(no meeting.started/ended events)")

        members = set()
        if lc.batch_id:
            members |= set(lc.batch.students.values_list("student_id", flat=True))
        if lc.course_id:
            members |= set(lc.course.enrollments.values_list("user_id", flat=True))
        self.out("Students in class (batch + course)", len(members))

        events = Counter(
            (e, s) for e, s in ZoomWebhookEvent.objects.filter(live_class=lc).values_list("event_type", "status")
        )
        self.out("Webhook events attached to this class", dict(sorted(events.items())) or "(none)")

        statuses = dict(Attendance.objects.filter(live_class=lc).values_list("status").annotate(n=Count("id")))
        self.out("Attendance rows by status", statuses or "(none)")
        app_only = Attendance.objects.filter(live_class=lc, sessions__isnull=True).count()
        self.out("  rows with NO Zoom evidence (app tap only)", app_only)
        sources = dict(AttendanceSession.objects.filter(attendance__live_class=lc)
                       .values_list("source").annotate(n=Count("id")))
        self.out("Zoom sessions by source", sources or "(none)")

        unmatched = UnmatchedZoomParticipant.objects.filter(live_class=lc)
        self.out("Review queue by status", dict(unmatched.values_list("status").annotate(n=Count("id"))) or "(none)")
        pend = list(unmatched.filter(status=UnmatchedZoomParticipant.Status.PENDING).values_list("raw_evidence", flat=True))
        if pend:
            def has(key):
                return sum(1 for ev in pend if isinstance(ev, dict) and str(ev.get(key) or "").strip())
            self.out("  pending with registrant_id / email / customer_key",
                     f"{has('registrant_id')} / {sum(1 for ev in pend if isinstance(ev, dict) and (ev.get('email') or ev.get('user_email')))} / {has('customer_key')}")

        self.out("Personal links issued for this class", LiveClassRegistrant.objects.filter(live_class=lc).count())
        self.out("Join taps (join intents)", LiveClassJoinIntent.objects.filter(live_class=lc).count())

        if use_zoom and meeting_id:
            self.zoom_class_report(lc, meeting_id, members)

    def zoom_class_report(self, lc, meeting_id, members):
        import requests
        from courses.services.attendance_identity import participant_exclusion_reason
        from courses.services.zoom_attendance import ZoomAttendanceService, ZoomTransientError, _zoom_get
        from courses.services.zoom_meeting_runs import class_window
        from courses.services.zoom_attendance import parse_zoom_timestamp

        try:
            res = _zoom_get(f"https://api.zoom.us/v2/meetings/{meeting_id}", ZoomAttendanceService._zoom_headers())
            if res.status_code == 200:
                data = res.json()
                approval = (data.get("settings") or {}).get("approval_type")
                self.out("Zoom meeting type / registration", f"{data.get('type')} / "
                         f"{'ON' if approval in (0, 1) else 'off'}")
            else:
                self.out("Zoom meeting lookup", f"HTTP {res.status_code} (meeting may be on another Zoom account or deleted)")
        except (ZoomTransientError, requests.RequestException, Exception) as exc:
            self.out("Zoom meeting lookup", f"failed ({exc.__class__.__name__})")

        try:
            report = ZoomAttendanceService.fetch_meeting_reports(lc)
        except Exception as exc:
            self.out("Participant report", f"failed ({exc.__class__.__name__})")
            return
        rows = report.get("participants") or []
        self.out("Participant report status / runs", f"{report.get('status')} / {report.get('runs')} ({report.get('run_source')})")
        self.out("Report rows", len(rows))
        if not rows:
            return
        w_start, w_end = class_window(lc)
        in_window = [p for p in rows if p.get("join_time") and w_start <= parse_zoom_timestamp(p["join_time"]) <= w_end]
        self.out("  rows inside the class time window", len(in_window))
        self.out("  rows with registrant_id (personal link)", sum(1 for p in in_window if p.get("registrant_id")))
        self.out("  rows with an email", sum(1 for p in in_window if p.get("user_email") or p.get("email")))
        self.out("  rows with customer_key", sum(1 for p in in_window if p.get("customer_key")))

        outcome = Counter()
        students = set()
        try:
            with transaction.atomic():  # identity lookups only; always rolled back
                for p in in_window:
                    if participant_exclusion_reason(lc, p):
                        outcome["excluded (teacher/host/staff/test)"] += 1
                        continue
                    student = ZoomAttendanceService.resolve_student_identity(lc, p, {})
                    if student:
                        outcome["identified"] += 1
                        students.add(student.pk)
                    else:
                        outcome["NOT identifiable -> review queue"] += 1
                raise _Rollback()
        except _Rollback:
            pass
        for k, v in sorted(outcome.items()):
            self.out(f"  {k}", v)
        self.out("  distinct students identified", len(students))
        self.out("  identified students who are class members", len(students & members))
