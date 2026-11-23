"""
Automatic Zoom attendance classification:
PRESENT when attended % >= global threshold, ABSENT below it after the final
report reconciliation; never ABSENT mid-class; manual overrides preserved;
reconciliation idempotent; recording/legacy events cannot bypass the threshold.

No Zoom/S3/network access: Zoom report rows are passed in directly (or the
read-only fetch is mocked) and webhooks call the service handlers.
"""
import datetime
import io
import json
import os
import tempfile
import zoneinfo
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from courses.models import (
    Attendance,
    AttendanceConfiguration,
    AttendanceSession,
    Course,
    Enrollment,
    LiveBatch,
    LiveBatchStudent,
    LiveClass,
    UnmatchedZoomParticipant,
)
from courses.serializers import AttendanceSerializer
from courses.services.attendance_classification import apply_automatic_attendance
from courses.services.recording import ZoomRecordingService
from courses.services.zoom_attendance import ZoomAttendanceService

User = get_user_model()
UTC = datetime.timezone.utc
MEETING_ID = "88800011122"
MEETING_UUID = "auto-class-uuid=="


def _z(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Base(TestCase):
    status = LiveClass.ClassStatus.COMPLETED

    def setUp(self):
        config = AttendanceConfiguration.get_solo()
        config.default_min_attendance_percentage = 75
        config.save()

        self.admin = User.objects.create_superuser(username="ac_admin", email="ac_admin@example.com", password="pw123456!")
        self.teacher = User.objects.create_user(username="ac_teacher", email="ac_teacher@example.com", password="pw123456!", is_teacher=True)
        self.s1 = User.objects.create_user(username="ac_s1", email="ac_s1@example.com", password="pw123456!")
        self.s2 = User.objects.create_user(username="ac_s2", email="ac_s2@example.com", password="pw123456!")
        self.course = Course.objects.create(title="AC Course", description="d", course_type=Course.CourseType.LIVE)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type=LiveBatch.BatchType.GROUP)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.s1)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.s2)

        # A day ago, on a whole minute: the class is genuinely over in real time.
        self.t0 = (timezone.now() - datetime.timedelta(days=1)).replace(second=0, microsecond=0)
        self.lc = LiveClass.objects.create(
            batch=self.batch, course=self.course, instructor=self.teacher, title="Session",
            meeting_url=f"https://zoom.us/j/{MEETING_ID}", meeting_provider=LiveClass.MeetingProvider.ZOOM,
            zoom_meeting_id=MEETING_ID, zoom_uuid=MEETING_UUID,
            scheduled_start=self.t0, duration_minutes=60, status=self.status,
        )

    def at(self, minutes):
        return self.t0 + datetime.timedelta(minutes=minutes)

    def row(self, student, start, end, pid=None, **extra):
        r = {
            "id": pid or f"pid-{student.id}-{start}",
            "user_id": "",
            "name": "Participant",
            "user_email": student.email,
            "join_time": _z(self.at(start)),
            "leave_time": _z(self.at(end)),
            "duration": int((end - start) * 60),
        }
        r.update(extra)
        return r

    def att(self, student):
        return Attendance.objects.get(live_class=self.lc, student=student)


class ReportReconciliationClassificationTests(_Base):
    def test_above_threshold_becomes_present(self):
        result = ZoomAttendanceService.apply_meeting_report(self.lc, [self.row(self.s1, 0, 50)])
        att = self.att(self.s1)
        self.assertEqual(att.status, Attendance.Status.PRESENT)
        self.assertEqual(att.duration_minutes, 50)
        self.assertEqual(att.reconciliation_status, "RECONCILED")
        self.assertTrue(result["classification"]["final"])

    def test_exactly_at_threshold_becomes_present(self):
        ZoomAttendanceService.apply_meeting_report(self.lc, [self.row(self.s1, 0, 45)])  # 45/60 = 75.0%
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)

    def test_below_threshold_becomes_absent_after_final_reconciliation(self):
        # A stale automatic PRESENT (not a manual decision) must be corrected.
        Attendance.objects.create(live_class=self.lc, student=self.s1, status=Attendance.Status.PRESENT)
        # s2 has a placeholder row but no Zoom evidence at all.
        Attendance.objects.create(live_class=self.lc, student=self.s2, status=Attendance.Status.ABSENT)

        result = ZoomAttendanceService.apply_meeting_report(self.lc, [self.row(self.s1, 0, 30)])  # 50%
        s1, s2 = self.att(self.s1), self.att(self.s2)
        self.assertEqual(s1.status, Attendance.Status.ABSENT)
        self.assertEqual(s1.duration_minutes, 30)
        self.assertEqual(s2.status, Attendance.Status.ABSENT)
        self.assertEqual(s2.reconciliation_status, "RECONCILED")
        self.assertEqual(result["classification"]["present"], 0)
        self.assertEqual(result["classification"]["absent"], 2)

    def test_multiple_sessions_aggregated_without_overlap_double_count(self):
        # Two devices overlapping 15-30, plus a webhook session inside the same span.
        att = Attendance.objects.create(live_class=self.lc, student=self.s1)
        AttendanceSession.objects.create(
            attendance=att, session_id="wh-overlap", join_time=self.at(5), leave_time=self.at(25),
            duration_seconds=1200, is_active=False, source=AttendanceSession.Source.WEBHOOK,
        )
        ZoomAttendanceService.apply_meeting_report(self.lc, [
            self.row(self.s1, 0, 30, pid="dev-a"),
            self.row(self.s1, 15, 50, pid="dev-b"),
        ])
        att = self.att(self.s1)
        self.assertEqual(att.duration_minutes, 50)  # union 0-50, not 30+35+20
        self.assertEqual(att.status, Attendance.Status.PRESENT)
        self.assertEqual(att.zoom_reported_duration_seconds, (30 + 35) * 60)  # Zoom's own per-row figures

        client = APIClient()
        client.force_authenticate(self.admin)
        data = client.get(f"/api/courses/live-classes/{self.lc.id}/attendance/live/").json()
        row = next(s for s in data["students"] if s["student_id"] == self.s1.id)
        self.assertEqual(row["total_duration_minutes"], 50)
        self.assertEqual(row["status"], "PRESENT")
        self.assertEqual(row["suggested_status"], "PRESENT")

    def test_reprocessing_same_report_does_not_increase_duration(self):
        rows = [self.row(self.s1, 0, 20, pid="a"), self.row(self.s1, 25, 50, pid="b")]
        ZoomAttendanceService.apply_meeting_report(self.lc, rows)
        first = self.att(self.s1)
        snapshot = (first.duration_minutes, first.zoom_reported_duration_seconds, first.status, first.sessions.count())

        for _ in range(2):
            ZoomAttendanceService.apply_meeting_report(self.lc, rows)
        again = self.att(self.s1)
        self.assertEqual(
            (again.duration_minutes, again.zoom_reported_duration_seconds, again.status, again.sessions.count()),
            snapshot,
        )
        self.assertEqual(snapshot, (45, 45 * 60, Attendance.Status.PRESENT, 2))

    def test_manual_status_override_is_never_changed(self):
        Attendance.objects.create(live_class=self.lc, student=self.s1, status=Attendance.Status.EXCUSED, is_manual_override=True)
        Attendance.objects.create(live_class=self.lc, student=self.s2, status=Attendance.Status.ABSENT, is_manual_override=True)
        ZoomAttendanceService.apply_meeting_report(self.lc, [self.row(self.s1, 0, 10), self.row(self.s2, 0, 55)])
        self.assertEqual(self.att(self.s1).status, Attendance.Status.EXCUSED)
        self.assertEqual(self.att(self.s2).status, Attendance.Status.ABSENT)
        self.assertEqual(self.att(self.s2).duration_minutes, 55)  # evidence still recorded

    def test_manual_duration_override_is_never_changed(self):
        Attendance.objects.create(live_class=self.lc, student=self.s1, duration_minutes=10, is_duration_manual_override=True)
        Attendance.objects.create(live_class=self.lc, student=self.s2, duration_minutes=50, is_duration_manual_override=True)
        ZoomAttendanceService.apply_meeting_report(self.lc, [self.row(self.s1, 0, 55), self.row(self.s2, 0, 5)])
        s1, s2 = self.att(self.s1), self.att(self.s2)
        self.assertEqual(s1.duration_minutes, 10)
        self.assertEqual(s2.duration_minutes, 50)
        # The admin-entered duration is the attended time used for classification.
        self.assertEqual(s1.status, Attendance.Status.ABSENT)
        self.assertEqual(s2.status, Attendance.Status.PRESENT)

    def test_rows_with_missing_timestamps_are_skipped_not_set_to_now(self):
        no_join = self.row(self.s1, 0, 50, pid="nj")
        no_join["join_time"] = None
        no_leave = self.row(self.s2, 0, 50, pid="nl")
        no_leave["leave_time"] = None  # falls back to join + duration
        result = ZoomAttendanceService.apply_meeting_report(self.lc, [no_join, no_leave])
        self.assertEqual(result["skipped"], 1)
        self.assertFalse(AttendanceSession.objects.filter(attendance__student=self.s1).exists())
        session = AttendanceSession.objects.get(attendance__student=self.s2)
        self.assertEqual(session.leave_time, self.at(50))
        self.assertEqual(self.att(self.s2).status, Attendance.Status.PRESENT)

    def test_stale_open_webhook_session_does_not_inflate_after_class(self):
        # Joined at the start; the "left" webhook was lost. Zoom's report says 10 minutes.
        att = Attendance.objects.create(live_class=self.lc, student=self.s1)
        AttendanceSession.objects.create(
            attendance=att, session_id="wh-stale", join_time=self.at(0), is_active=True,
            source=AttendanceSession.Source.WEBHOOK,
        )
        ZoomAttendanceService.apply_meeting_report(self.lc, [self.row(self.s1, 0, 10), self.row(self.s2, 0, 60)])
        att = self.att(self.s1)
        self.assertEqual(att.duration_minutes, 10)
        self.assertEqual(att.status, Attendance.Status.ABSENT)
        self.assertEqual(AttendanceSerializer(att).data["suggested_status"], "ABSENT")

    def test_reconcile_endpoint_path_applies_classification(self):
        with mock.patch.object(ZoomAttendanceService, "fetch_meeting_report",
                               return_value={"status": "success", "participants": [self.row(self.s1, 0, 50)]}):
            result = ZoomAttendanceService.reconcile_meeting_participants(self.lc)
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)


class InProgressClassTests(_Base):
    status = LiveClass.ClassStatus.LIVE

    def setUp(self):
        super().setUp()
        self.clock = self.at(0)
        patcher = mock.patch("django.utils.timezone.now", side_effect=lambda: self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _hook(self, kind, student, minute, pid="pp-1", duration=None):
        self.clock = self.at(minute) + datetime.timedelta(seconds=5)
        participant = {"participant_uuid": pid, "user_id": "1677", "user_name": "S", "email": student.email}
        participant["join_time" if kind == "join" else "leave_time"] = _z(self.at(minute))
        if duration is not None:
            participant["duration"] = duration
        payload = {"object": {"id": MEETING_ID, "uuid": MEETING_UUID, "participant": participant}}
        handler = ZoomAttendanceService.handle_participant_joined if kind == "join" else ZoomAttendanceService.handle_participant_left
        self.assertTrue(handler(payload))
        return self.att(student)

    def test_temporary_disconnect_is_not_marked_absent(self):
        self._hook("join", self.s1, 0)
        att = self._hook("leave", self.s1, 20)  # 33% so far, class still running
        self.assertEqual(att.duration_minutes, 20)
        self.assertNotEqual(att.status, Attendance.Status.PRESENT)
        self.assertEqual(att.reconciliation_status, "PENDING")  # nothing finalised mid-class

        self._hook("join", self.s1, 22)
        att = self._hook("leave", self.s1, 50)  # 20 + 28 = 48 min = 80%
        self.assertEqual(att.status, Attendance.Status.PRESENT)

        self._hook("join", self.s1, 51)
        att = self._hook("leave", self.s1, 52)  # another brief drop: stays PRESENT
        self.assertEqual(att.status, Attendance.Status.PRESENT)

    def test_final_rules_do_not_demote_while_class_in_progress(self):
        Attendance.objects.create(live_class=self.lc, student=self.s1, status=Attendance.Status.PRESENT)
        self.clock = self.at(30)
        result = ZoomAttendanceService.apply_meeting_report(self.lc, [self.row(self.s1, 0, 10)])
        self.assertFalse(result["classification"]["final"])
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)

    def test_recording_service_participant_events_cannot_bypass_threshold(self):
        payload = lambda key, minute, **kw: {"object": {"id": MEETING_ID, "uuid": MEETING_UUID, "participant": {
            "participant_uuid": "rec-1", "user_name": "S", "email": self.s1.email, key: _z(self.at(minute)), **kw}}}
        self.clock = self.at(1)
        ZoomRecordingService.handle_participant_webhook(payload("join_time", 0), "meeting.participant_joined")
        self.assertEqual(self.att(self.s1).status, Attendance.Status.ABSENT)
        self.clock = self.at(2)
        ZoomRecordingService.handle_participant_webhook(payload("leave_time", 2, duration=120), "meeting.participant_left")
        att = self.att(self.s1)
        self.assertEqual(att.status, Attendance.Status.ABSENT)
        self.assertEqual(att.duration_minutes, 2)

    def test_recording_service_does_not_match_by_username(self):
        stranger = User.objects.create_user(username="Some Name", email="", password="pw123456!")
        payload = {"object": {"id": MEETING_ID, "uuid": MEETING_UUID, "participant": {
            "participant_uuid": "rec-2", "user_name": "Some Name", "join_time": _z(self.at(0))}}}
        ZoomRecordingService.handle_participant_webhook(payload, "meeting.participant_joined")
        self.assertFalse(Attendance.objects.filter(student=stranger).exists())
        self.assertTrue(UnmatchedZoomParticipant.objects.filter(live_class=self.lc, zoom_participant_uuid="rec-2").exists())


class ParticipantWebhookTaskTests(_Base):
    status = LiveClass.ClassStatus.LIVE

    def test_task_marks_event_processed(self):
        # Previously raised NameError (timezone not imported) after the attendance
        # update, leaving every participant event stuck in PROCESSING.
        from courses.models import ZoomWebhookEvent
        from courses.tasks import process_zoom_participant_webhook_task

        event = ZoomWebhookEvent.objects.create(
            event_id="task-evt-1", event_type="meeting.participant_joined",
            payload={"event": "meeting.participant_joined", "payload": {"object": {
                "id": MEETING_ID, "uuid": MEETING_UUID,
                "participant": {"participant_uuid": "t-1", "email": self.s1.email, "join_time": _z(timezone.now())},
            }}},
            live_class=self.lc, meeting_uuid=MEETING_UUID,
        )
        result = process_zoom_participant_webhook_task.apply(args=[event.id]).get()
        event.refresh_from_db()
        self.assertEqual(event.status, ZoomWebhookEvent.Status.PROCESSED)
        self.assertIsNotNone(event.processed_at)
        self.assertTrue(result["handled"])
        self.assertTrue(self.att(self.s1).sessions.exists())


class UnmatchedLinkingClassificationTests(_Base):
    def _unmatched(self, start, end, pid):
        return UnmatchedZoomParticipant.objects.create(
            live_class=self.lc, zoom_participant_uuid=pid, participant_name="Unknown",
            join_time=self.at(start), leave_time=self.at(end), duration_seconds=int((end - start) * 60),
            is_active=False,
        )

    def test_linking_above_threshold_marks_present(self):
        u = self._unmatched(0, 50, "u-1")
        att, _ = ZoomAttendanceService.link_unmatched_participant(u.id, self.s1.id, self.admin)
        att.refresh_from_db()
        self.assertEqual(att.status, Attendance.Status.PRESENT)
        self.assertEqual(att.duration_minutes, 50)

    def test_linking_below_threshold_marks_absent(self):
        Attendance.objects.create(live_class=self.lc, student=self.s2, status=Attendance.Status.PRESENT)
        u = self._unmatched(0, 10, "u-2")
        att, _ = ZoomAttendanceService.link_unmatched_participant(u.id, self.s2.id, self.admin)
        att.refresh_from_db()
        self.assertEqual(att.status, Attendance.Status.ABSENT)

    def test_linking_course_enrolled_student_outside_batch(self):
        # Previously raised FieldError (enrollments.filter(student=...)).
        s3 = User.objects.create_user(username="ac_s3", email="ac_s3@example.com", password="pw123456!")
        Enrollment.objects.create(user=s3, course=self.course)
        u = self._unmatched(0, 46, "u-3")
        att, _ = ZoomAttendanceService.link_unmatched_participant(u.id, s3.id, self.admin)
        att.refresh_from_db()
        self.assertEqual(att.status, Attendance.Status.PRESENT)

    def test_linking_preserves_manual_status(self):
        Attendance.objects.create(live_class=self.lc, student=self.s1, status=Attendance.Status.EXCUSED, is_manual_override=True)
        u = self._unmatched(0, 5, "u-4")
        att, _ = ZoomAttendanceService.link_unmatched_participant(u.id, self.s1.id, self.admin)
        att.refresh_from_db()
        self.assertEqual(att.status, Attendance.Status.EXCUSED)


class ClassifierUnitTests(_Base):
    def test_promote_only_when_not_final(self):
        att = Attendance.objects.create(live_class=self.lc, student=self.s1, status=Attendance.Status.PRESENT)
        decision = apply_automatic_attendance(att, final=False)
        self.assertEqual(decision["status"], Attendance.Status.PRESENT)
        self.assertFalse(decision["changed"])
        decision = apply_automatic_attendance(att, final=True)
        self.assertEqual(decision["status"], Attendance.Status.ABSENT)


class RecoveryCommandTests(_Base):
    def _run(self, *args):
        out = io.StringIO()
        day = self.t0.astimezone(zoneinfo.ZoneInfo("Asia/Kolkata")).date().isoformat()
        call_command("recover_zoom_attendance", "--date", day, *args, stdout=out)
        return out.getvalue()

    def _fetch(self, rows):
        return mock.patch.object(ZoomAttendanceService, "fetch_meeting_report",
                                 return_value={"status": "success", "participants": rows})

    def test_dry_run_previews_and_writes_nothing(self):
        Attendance.objects.create(live_class=self.lc, student=self.s1)
        with self._fetch([self.row(self.s1, 0, 50), {"id": "x", "name": "Guest", "user_email": "",
                                                      "join_time": _z(self.at(0)), "leave_time": _z(self.at(30)), "duration": 1800}]):
            out = self._run()
        self.assertIn("DRY-RUN", out)
        self.assertIn("previewed", out)
        self.assertIn("PRESENT", out)
        self.assertNotIn("ac_s1@example.com", out)  # IDs only, no personal data
        self.assertNotIn("Guest", out)
        att = self.att(self.s1)
        self.assertEqual(att.status, Attendance.Status.ABSENT)
        self.assertFalse(att.sessions.exists())
        self.assertFalse(UnmatchedZoomParticipant.objects.exists())

    def test_apply_is_idempotent_and_writes_audit_report(self):
        rows = [self.row(self.s1, 0, 50), self.row(self.s2, 0, 20)]
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with self._fetch(rows):
            self._run("--apply", "--class-id", str(self.lc.id), "--output", path)
            self._run("--apply", "--class-id", str(self.lc.id))
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)
        self.assertEqual(self.att(self.s2).status, Attendance.Status.ABSENT)
        self.assertEqual(AttendanceSession.objects.filter(attendance__live_class=self.lc).count(), 2)
        with open(path, encoding="utf-8") as fh:
            audit = json.load(fh)
        self.assertEqual(audit["mode"], "apply")
        self.assertEqual(audit["classes"][0]["result"], "applied")
        changed = {s["student_id"]: s for s in audit["classes"][0]["students"]}
        self.assertEqual(changed[self.s1.id]["status_after"], "PRESENT")

    def test_apply_refuses_report_rows_outside_class_window(self):
        far = self.row(self.s1, 0, 50)
        far["join_time"] = _z(self.at(-24 * 60))  # another occurrence of a recurring meeting
        with self._fetch([far]):
            out = self._run("--apply")
        self.assertIn("refused_out_of_window_rows", out)
        self.assertFalse(Attendance.objects.filter(live_class=self.lc, student=self.s1).exists())
