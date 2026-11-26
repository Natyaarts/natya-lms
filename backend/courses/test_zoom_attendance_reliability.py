"""
End-to-end reliability tests for Zoom automatic attendance.

The flow under test is the real one: a student taps Join (personal Zoom link
issued), Zoom sends signed participant webhooks to the real endpoint, the
reconcile task reads the participant report through the real fetch code, and
attendance is finalized. Only Zoom's HTTP API is replaced: its responses are
test fixtures shaped like Zoom's documented payloads (they are not real
attendance data).
"""
import datetime
import hashlib
import hmac
import json
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from courses.models import (
    Attendance, AttendanceConfiguration, AttendanceSession, Course, LiveBatch, LiveBatchStudent, LiveClass,
    LiveClassRegistrant, UnmatchedZoomParticipant, ZoomWebhookEvent,
)
from courses.services.recording import ZoomRecordingService
from courses.services.zoom import ZoomService
from courses.tasks import process_zoom_participant_webhook_task, reconcile_zoom_attendance_task

User = get_user_model()
SECRET = "reliability_webhook_secret"
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "reliability"}}
MEETING_ID = "88855501234"
RUN_UUID = "RunUuidAbc123=="


def z(dt):
    return dt.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resp(status, body=None):
    r = mock.MagicMock(status_code=status, headers={}, text=json.dumps(body or {}))
    r.json.return_value = body or {}
    return r


@override_settings(
    ENABLE_ZOOM_ATTENDANCE_V2=True, ZOOM_ATTENDANCE_USE_REGISTRATION=True, CACHES=LOCMEM,
    ZOOM_ATTENDANCE_EXCLUDED_USER_IDS=frozenset(), ZOOM_ATTENDANCE_AUTOMATION_START="2000-01-01T00:00:00+00:00",
    ZOOM_WEBHOOK_SECRET_TOKEN=SECRET, ZOOM_ACCOUNT_ID="",
)
class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = User.objects.create_user(username="rel_teacher", email="teacher@natya.test", password="x",
                                                is_teacher=True, is_student=False)
        # Two students whose Zoom display names look alike, one without an email.
        self.s1 = User.objects.create_user(username="legacy_aaaaaaaaaaaaaaaaaaaaaaaa", email="", password="x",
                                           first_name="Priya", last_name="S")
        self.s2 = User.objects.create_user(username="rel_s2", email="priya.s@example.test", password="x",
                                           first_name="Priya", last_name="S.")
        self.s3 = User.objects.create_user(username="rel_s3", email="", password="x", first_name="Meera")
        self.course = Course.objects.create(title="Rel Course", course_type=Course.CourseType.LIVE, is_published=True, price=0)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type=LiveBatch.BatchType.GROUP)
        for s in (self.s1, self.s2, self.s3):
            LiveBatchStudent.objects.create(batch=self.batch, student=s)
        self.start = (timezone.now() - datetime.timedelta(hours=3)).replace(second=0, microsecond=0)
        self.lc = self.make_class(self.start, status=LiveClass.ClassStatus.LIVE)
        # Students were in the batch well before the class.
        LiveBatchStudent.objects.filter(batch=self.batch).update(created_at=self.start - datetime.timedelta(days=30))
        self.report_rows = []
        self.report_status = 200

    def make_class(self, start, status=LiveClass.ClassStatus.COMPLETED, provider=LiveClass.MeetingProvider.ZOOM,
                   meeting_id=MEETING_ID, store_id=True):
        return LiveClass.objects.create(
            batch=self.batch, course=self.course, instructor=self.teacher, title=f"Rel {start:%d %H%M}",
            meeting_url=f"https://us06web.zoom.us/j/{meeting_id}?pwd=x", meeting_provider=provider,
            zoom_meeting_id=meeting_id if store_id else "", scheduled_start=start, duration_minutes=60, status=status,
        )

    def at(self, minutes, seconds=0):
        return self.start + datetime.timedelta(minutes=minutes, seconds=seconds)

    # ---- the student taps Join in the app
    def tap_join(self, student, registrant_id):
        client = APIClient()
        client.force_authenticate(student)
        body = {"registrant_id": registrant_id, "join_url": f"https://us06web.zoom.us/w/{MEETING_ID}?tk={registrant_id}"}
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.post", return_value=resp(201, body)):
            res = client.post(f"/api/courses/live-classes/{self.lc.id}/join/")
        self.assertEqual(res.status_code, 200, res.content)
        return res.data

    # ---- Zoom calls our webhook endpoint (signed), processed by the real task
    def webhook(self, event, obj, ts=None):
        body = json.dumps({"event": event, "event_ts": int((ts or timezone.now()).timestamp() * 1000),
                           "payload": {"account_id": "acc", "object": obj}}).encode()
        stamp = str(int(timezone.now().timestamp()))
        sig = "v0=" + hmac.new(SECRET.encode(), f"v0:{stamp}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
        with mock.patch("courses.tasks.process_zoom_participant_webhook_task.delay",
                        side_effect=lambda i: process_zoom_participant_webhook_task(i)), \
             mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as reconcile:
            res = APIClient().post("/api/courses/zoom/webhook/", data=body, content_type="application/json",
                                   HTTP_X_ZM_SIGNATURE=sig, HTTP_X_ZM_REQUEST_TIMESTAMP=stamp)
        self.assertEqual(res.status_code, 200, res.content)
        return reconcile

    def participant(self, event, *, uid, name, join=None, leave=None, registrant_id="", pu="", email=""):
        p = {"user_id": uid, "user_name": name, "id": "", "participant_uuid": pu or f"pu-{uid}",
             "email": email, "registrant_id": registrant_id}
        if join is not None:
            p["join_time"] = z(join)
        if leave is not None:
            p["leave_time"] = z(leave)
        return self.webhook(event, {"id": MEETING_ID, "uuid": RUN_UUID, "start_time": z(self.start), "participant": p},
                            ts=leave or join)

    # ---- Zoom report API (fixtures in Zoom's documented shape)
    def row(self, *, uid, name, join, leave, registrant_id="", email=""):
        return {"id": "", "user_id": uid, "name": name, "user_email": email, "registrant_id": registrant_id,
                "join_time": z(join), "leave_time": z(leave), "duration": int((leave - join).total_seconds())}

    def fake_get(self, url, headers=None, timeout=None):
        if "/past_meetings/" in url:
            return resp(200, {"meetings": [{"uuid": RUN_UUID, "start_time": z(self.start)}]})
        if "/report/meetings/" in url:
            if self.report_status != 200:
                return resp(self.report_status, {"code": 3001, "message": "not ready"})
            return resp(200, {"participants": self.report_rows, "next_page_token": ""})
        return resp(404, {})

    def reconcile(self):
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=self.fake_get) as get:
            result = reconcile_zoom_attendance_task.apply(args=[self.lc.id]).result
        self.get_calls = get.call_count
        return result

    def att(self, student):
        return Attendance.objects.filter(live_class=self.lc, student=student).first()


class EndToEndTests(_Base):
    def run_class(self):
        """Taps, live webhooks, class ends; report has reconnects, a 2nd device, a guest and the teacher."""
        d1 = self.tap_join(self.s1, "reg-s1")
        d2 = self.tap_join(self.s2, "reg-s2")
        self.assertIn("tk=reg-s1", d1["meeting_url"])  # personal link handed to the app
        self.assertIn("tk=reg-s2", d2["meeting_url"])

        # Both students show up in Zoom as "Priya S"; only the personal link tells them apart.
        self.participant("meeting.participant_joined", uid="101", name="Priya S", join=self.at(0), registrant_id="reg-s1")
        self.participant("meeting.participant_joined", uid="102", name="Priya S", join=self.at(1), registrant_id="reg-s2")
        # A guest using the plain link with the very same display name.
        self.participant("meeting.participant_joined", uid="103", name="Priya S", join=self.at(2))
        self.participant("meeting.participant_left", uid="101", name="Priya S", leave=self.at(30), registrant_id="reg-s1")

        LiveClass.objects.filter(pk=self.lc.pk).update(status=LiveClass.ClassStatus.COMPLETED)
        self.lc.refresh_from_db()
        self.lc.refresh_from_db()
        self.report_rows = [
            # s1: drops at 30 and reconnects at 32 -> 58 min
            self.row(uid="101", name="Priya S", join=self.at(0), leave=self.at(30), registrant_id="reg-s1"),
            self.row(uid="111", name="Priya S", join=self.at(32), leave=self.at(60), registrant_id="reg-s1"),
            # s2: phone 1-21 and a second device 11-31 (overlap) -> 30 min, not 40
            self.row(uid="102", name="Priya S", join=self.at(1), leave=self.at(21), registrant_id="reg-s2"),
            self.row(uid="112", name="Priya S (iPad)", join=self.at(11), leave=self.at(31), registrant_id="reg-s2"),
            # guest with the same name, no personal link
            self.row(uid="103", name="Priya S", join=self.at(2), leave=self.at(60)),
            # teacher (host)
            self.row(uid="100", name="Teacher", join=self.at(0), leave=self.at(60), email="teacher@natya.test"),
        ]
        return self.reconcile()

    def test_full_flow_identifies_by_personal_link_and_finalizes(self):
        result = self.run_class()
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["classification"]["final"])

        a1, a2 = self.att(self.s1), self.att(self.s2)
        self.assertEqual((a1.status, a1.duration_minutes), (Attendance.Status.PRESENT, 58))
        self.assertEqual((a2.status, a2.duration_minutes), (Attendance.Status.ABSENT, 30))  # 50% < 75%
        a3 = self.att(self.s3)  # on the roster, never joined: ABSENT once finalized
        self.assertEqual((a3.status, a3.duration_minutes, a3.is_manual_override), (Attendance.Status.ABSENT, 0, False))
        self.assertIsNone(Attendance.objects.filter(live_class=self.lc, student=self.teacher).first())

        # The guest is never matched by name; it waits in the review queue (webhook + report merged).
        queue = UnmatchedZoomParticipant.objects.filter(live_class=self.lc)
        self.assertEqual(queue.count(), 1)
        self.assertEqual(queue.get().status, UnmatchedZoomParticipant.Status.PENDING)

        self.lc.refresh_from_db()
        self.assertIsNotNone(self.lc.attendance_finalized_at)
        from users.models import AdminAuditLog
        log = AdminAuditLog.objects.get(action="ZOOM_ATTENDANCE_FINALIZED", target_id=str(self.lc.id))
        self.assertEqual(
            (log.metadata["present"], log.metadata["absent"], log.metadata["no_show_absent"], log.metadata["unmatched_rows"]),
            (1, 2, 1, 1),
        )
        self.assertNotIn("Priya", json.dumps(log.metadata))

    def test_running_the_task_again_changes_nothing(self):
        self.run_class()
        snapshot = (
            list(Attendance.objects.filter(live_class=self.lc).order_by("id").values_list("status", "duration_minutes")),
            AttendanceSession.objects.filter(attendance__live_class=self.lc).count(),
            UnmatchedZoomParticipant.objects.filter(live_class=self.lc).count(),
        )
        second = self.reconcile()
        third = self.reconcile()
        self.assertEqual(second["classification"]["changed"], 0)
        self.assertEqual(third["classification"]["changed"], 0)
        self.assertEqual(snapshot, (
            list(Attendance.objects.filter(live_class=self.lc).order_by("id").values_list("status", "duration_minutes")),
            AttendanceSession.objects.filter(attendance__live_class=self.lc).count(),
            UnmatchedZoomParticipant.objects.filter(live_class=self.lc).count(),
        ))

    def test_live_webhooks_alone_promote_but_never_mark_absent(self):
        self.tap_join(self.s1, "reg-s1")
        self.participant("meeting.participant_joined", uid="101", name="x", join=self.at(0), registrant_id="reg-s1")
        self.participant("meeting.participant_left", uid="101", name="x", leave=self.at(50), registrant_id="reg-s1")
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)  # 50/60 during class
        self.participant("meeting.participant_joined", uid="102", name="x", join=self.at(1), registrant_id="reg-s2")
        self.assertIsNone(self.att(self.s2))  # reg-s2 was never issued: no guessing


class ReportAvailabilityTests(_Base):
    def setUp(self):
        super().setUp()
        LiveClass.objects.filter(pk=self.lc.pk).update(status=LiveClass.ClassStatus.COMPLETED)
        self.lc.refresh_from_db()
        LiveClassRegistrant.objects.create(live_class=self.lc, student=self.s1, registrant_id="reg-s1", email="e@x.test")
        Attendance.objects.create(live_class=self.lc, student=self.s2, status=Attendance.Status.ABSENT)
        self.report_rows = [self.row(uid="1", name="a", join=self.at(0), leave=self.at(60), registrant_id="reg-s1")]

    def test_report_not_ready_retries_and_never_marks_absent(self):
        self.report_status = 404
        result = self.reconcile()
        self.assertEqual(result["status"], "not_ready")
        self.assertGreater(self.get_calls, 7)  # retried (eager mode runs the retries immediately)
        self.lc.refresh_from_db()
        self.assertIsNone(self.lc.attendance_finalized_at)
        self.assertEqual(self.att(self.s2).reconciliation_status, "PENDING")
        self.assertIsNone(self.att(self.s3))  # no report -> nobody marked absent

        self.report_status = 200  # report published later: the catch-up run completes it
        result = self.reconcile()
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)
        self.assertEqual(self.att(self.s2).status, Attendance.Status.ABSENT)

    def test_temporary_zoom_errors_are_retried_then_succeed(self):
        calls = {"n": 0}
        real = self.fake_get

        def flaky(url, headers=None, timeout=None):
            if "/report/meetings/" in url and calls["n"] < 2:
                calls["n"] += 1
                return resp(503 if calls["n"] == 1 else 429)
            return real(url, headers, timeout)

        self.fake_get = flaky
        result = self.reconcile()
        self.assertEqual(result["status"], "success")
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)

    def test_expired_token_is_refreshed_once(self):
        responses = {"report": [resp(401, {"code": 124, "message": "Invalid access token."})]}
        real = self.fake_get

        def get(url, headers=None, timeout=None):
            if "/report/meetings/" in url and responses["report"]:
                return responses["report"].pop(0)
            return real(url, headers, timeout)

        state = {"token": "old-token"}

        def get_token(force_refresh=False, rejected_token=None):
            if force_refresh:
                state["token"] = "new-token"
            return state["token"]

        with mock.patch.object(ZoomService, "get_access_token", side_effect=get_token) as token, \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=get) as http:
            result = reconcile_zoom_attendance_task.apply(args=[self.lc.id]).result
        self.assertEqual(result["status"], "success")
        refreshes = [c for c in token.call_args_list if c.kwargs.get("force_refresh")]
        self.assertEqual([c.kwargs.get("rejected_token") for c in refreshes], ["old-token"])
        retried = [c for c in http.call_args_list if "/report/meetings/" in c.args[0]]
        self.assertEqual(retried[-1].kwargs["headers"]["Authorization"], "Bearer new-token")

    def test_permanent_api_error_is_reported_not_finalized(self):
        self.report_status = 400
        result = self.reconcile()
        self.assertEqual(result["status"], "error")
        self.lc.refresh_from_db()
        self.assertIsNone(self.lc.attendance_finalized_at)
        self.assertIsNone(self.att(self.s3))
        self.assertEqual(self.att(self.s2).reconciliation_status, "PENDING")


class ThresholdAndLateTests(_Base):
    def setUp(self):
        super().setUp()
        LiveClass.objects.filter(pk=self.lc.pk).update(status=LiveClass.ClassStatus.COMPLETED)
        self.lc.refresh_from_db()
        LiveClassRegistrant.objects.create(live_class=self.lc, student=self.s1, registrant_id="reg-s1", email="e@x.test")

    def attend(self, join, leave):
        self.report_rows = [self.row(uid="1", name="a", join=join, leave=leave, registrant_id="reg-s1")]
        self.reconcile()
        return self.att(self.s1)

    def test_exactly_threshold_is_present(self):
        self.assertEqual(self.attend(self.at(0), self.at(45)).status, Attendance.Status.PRESENT)  # 75.0%

    def test_just_below_threshold_is_absent_even_if_it_rounds_to_threshold(self):
        # 2699 s of 3600 s = 74.97 %, displayed as 75.0 % but below the threshold.
        self.assertEqual(self.attend(self.at(0), self.at(44, 59)).status, Attendance.Status.ABSENT)
        self.assertEqual(self.lc.calculate_attendance_metrics(2699)["suggested_status"], "ABSENT")
        self.assertEqual(self.lc.calculate_attendance_metrics(2700)["suggested_status"], "PRESENT")

    def test_threshold_is_admin_configurable(self):
        config = AttendanceConfiguration.get_solo()
        config.default_min_attendance_percentage = 50
        config.save()
        self.assertEqual(self.attend(self.at(0), self.at(31)).status, Attendance.Status.PRESENT)

    def test_late_rule_off_by_default(self):
        self.assertEqual(self.attend(self.at(12), self.at(60)).status, Attendance.Status.PRESENT)

    def test_late_join_meeting_threshold_is_late_when_rule_on(self):
        config = AttendanceConfiguration.get_solo()
        config.late_after_minutes = 10
        config.save()
        self.assertEqual(self.attend(self.at(12), self.at(60)).status, Attendance.Status.LATE)

    def test_late_join_below_threshold_is_absent(self):
        config = AttendanceConfiguration.get_solo()
        config.late_after_minutes = 10
        config.save()
        self.assertEqual(self.attend(self.at(20), self.at(60)).status, Attendance.Status.ABSENT)  # 66.7%

    def test_late_is_measured_from_when_the_teacher_actually_started(self):
        config = AttendanceConfiguration.get_solo()
        config.late_after_minutes = 10
        config.save()
        ZoomWebhookEvent.objects.create(
            event_id="ended-1", event_type="meeting.ended", live_class=self.lc, meeting_uuid=RUN_UUID,
            status=ZoomWebhookEvent.Status.PROCESSED,
            payload={"payload": {"object": {"id": MEETING_ID, "uuid": RUN_UUID, "start_time": z(self.at(8)),
                                            "end_time": z(self.at(60))}}},
        )
        # Joined 12 min after the scheduled start but 4 min after the meeting began.
        self.assertEqual(self.attend(self.at(12), self.at(60)).status, Attendance.Status.PRESENT)
        self.lc.refresh_from_db()
        self.assertIsNotNone(self.lc.attendance_finalized_at)

    def test_manual_status_is_never_overwritten(self):
        Attendance.objects.create(live_class=self.lc, student=self.s1, status=Attendance.Status.EXCUSED,
                                  is_manual_override=True)
        self.assertEqual(self.attend(self.at(0), self.at(5)).status, Attendance.Status.EXCUSED)

    def test_settings_api_saves_and_validates_late_rule(self):
        admin = User.objects.create_user(username="rel_admin", password="x", is_staff=True, is_superuser=True)
        client = APIClient()
        client.force_authenticate(admin)
        ok = client.patch("/api/courses/attendance-settings/", {"late_after_minutes": 15}, format="json")
        self.assertEqual((ok.status_code, ok.data["late_after_minutes"]), (200, 15))
        bad = client.patch("/api/courses/attendance-settings/", {"late_after_minutes": 241}, format="json")
        self.assertEqual(bad.status_code, 400)


class NoShowAbsentTests(_Base):
    """Students on the roster who never joined become ABSENT -- only when it is safe."""

    def setUp(self):
        super().setUp()
        LiveClass.objects.filter(pk=self.lc.pk).update(status=LiveClass.ClassStatus.COMPLETED)
        self.lc.refresh_from_db()
        LiveClassRegistrant.objects.create(live_class=self.lc, student=self.s1, registrant_id="reg-s1", email="e@x.test")
        self.report_rows = [self.row(uid="1", name="a", join=self.at(0), leave=self.at(60), registrant_id="reg-s1")]

    def finalized(self):
        self.lc.refresh_from_db()
        return self.lc.attendance_finalized_at is not None

    def test_no_shows_are_absent_and_roster_is_this_batch_only(self):
        other_batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type=LiveBatch.BatchType.GROUP)
        other_batch_student = User.objects.create_user(username="rel_other", password="x")
        LiveBatchStudent.objects.create(batch=other_batch, student=other_batch_student)
        from courses.models import Enrollment
        Enrollment.objects.create(user=other_batch_student, course=self.course)  # same course, other batch
        joined_later = User.objects.create_user(username="rel_later", password="x")
        later = LiveBatchStudent.objects.create(batch=self.batch, student=joined_later)
        LiveBatchStudent.objects.filter(pk=later.pk).update(created_at=self.at(120))  # added after the class
        inactive = User.objects.create_user(username="rel_inactive", password="x", is_active=False)
        LiveBatchStudent.objects.create(batch=self.batch, student=inactive)
        protected = User.objects.create_user(username="rel_protected", password="x")
        LiveBatchStudent.objects.create(batch=self.batch, student=protected)

        with override_settings(ZOOM_ATTENDANCE_EXCLUDED_USER_IDS=frozenset({protected.id})):
            result = self.reconcile()
        self.assertTrue(result["classification"]["final"])
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)
        for s in (self.s2, self.s3):
            a = self.att(s)
            self.assertEqual((a.status, a.reconciliation_status), (Attendance.Status.ABSENT, "RECONCILED"))
        for s in (other_batch_student, joined_later, inactive, protected, self.teacher):
            self.assertIsNone(self.att(s))
        self.assertEqual(result["classification"]["no_show_absent"], 2)

    def test_waits_for_the_configured_period_after_the_meeting_ended(self):
        config = AttendanceConfiguration.get_solo()
        config.finalize_after_minutes = 24 * 60  # meeting ended 2 h ago; wait 24 h
        config.save()
        from courses.services.zoom_attendance import ZoomAttendanceService
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=self.fake_get):
            result = ZoomAttendanceService.reconcile_meeting_participants(self.lc)
        self.assertEqual(result["classification"]["not_final_reason"], "waiting_finalization_period")
        self.assertFalse(self.finalized())
        self.assertIsNone(self.att(self.s3))
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)  # promotion is never delayed

        config.finalize_after_minutes = 15
        config.save()
        self.reconcile()
        self.assertTrue(self.finalized())
        self.assertEqual(self.att(self.s3).status, Attendance.Status.ABSENT)

    def test_never_while_the_meeting_is_still_running(self):
        ZoomWebhookEvent.objects.create(
            event_id="restart", event_type="meeting.started", live_class=self.lc, meeting_uuid="Run2==",
            status=ZoomWebhookEvent.Status.PROCESSED,
            payload={"payload": {"object": {"uuid": "Run2==", "start_time": z(self.at(70))}}},
        )
        result = self.reconcile()
        self.assertFalse(result["classification"]["final"])
        self.assertEqual(result["classification"]["not_final_reason"], "meeting_in_progress")
        self.assertIsNone(self.att(self.s3))
        self.assertFalse(self.finalized())

    def test_never_from_a_partial_report(self):
        real = self.fake_get

        def two_runs(url, headers=None, timeout=None):
            if "/past_meetings/" in url:
                return resp(200, {"meetings": [{"uuid": RUN_UUID, "start_time": z(self.start)},
                                               {"uuid": "Run2==", "start_time": z(self.at(30))}]})
            if "/report/meetings/Run2" in url:
                return resp(404, {"code": 3001})
            return real(url, headers, timeout)

        self.fake_get = two_runs
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=two_runs):
            from courses.services.zoom_attendance import ZoomAttendanceService
            result = ZoomAttendanceService.reconcile_meeting_participants(self.lc)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["classification"]["not_final_reason"], "report_incomplete")
        self.assertIsNone(self.att(self.s3))

    def test_excused_and_admin_edited_records_are_kept(self):
        Attendance.objects.create(live_class=self.lc, student=self.s2, status=Attendance.Status.EXCUSED)
        Attendance.objects.create(live_class=self.lc, student=self.s3, status=Attendance.Status.PRESENT,
                                  is_manual_override=True, duration_minutes=60, is_duration_manual_override=True)
        self.reconcile()
        self.assertTrue(self.finalized())
        self.assertEqual(self.att(self.s2).status, Attendance.Status.EXCUSED)
        a3 = self.att(self.s3)
        self.assertEqual((a3.status, a3.duration_minutes), (Attendance.Status.PRESENT, 60))

    def test_classes_before_automation_start_never_get_no_show_rows(self):
        from courses.services.zoom_attendance import ZoomAttendanceService
        with override_settings(ZOOM_ATTENDANCE_AUTOMATION_START=z(self.at(24 * 60))):
            result = ZoomAttendanceService.apply_meeting_report(self.lc, self.report_rows)  # e.g. manual Sync
        self.assertTrue(result["classification"]["final"])
        self.assertEqual(result["classification"]["no_show_absent"], 0)
        self.assertIsNone(self.att(self.s3))

    def test_linking_a_plain_link_joiner_later_corrects_an_automatic_absent(self):
        self.report_rows.append(self.row(uid="9", name="Guest", join=self.at(0), leave=self.at(60)))
        self.reconcile()
        self.assertEqual(self.att(self.s3).status, Attendance.Status.ABSENT)
        admin = User.objects.create_user(username="rel_admin2", password="x", is_staff=True)
        from courses.services.zoom_attendance import ZoomAttendanceService
        guest = UnmatchedZoomParticipant.objects.get(live_class=self.lc)
        ZoomAttendanceService.link_unmatched_participant(guest.id, self.s3.id, admin)
        self.assertEqual(self.att(self.s3).status, Attendance.Status.PRESENT)

    def test_catch_up_waits_for_the_period(self):
        from courses.tasks import zoom_attendance_catch_up_task
        ZoomWebhookEvent.objects.create(
            event_id="end-1", event_type="meeting.ended", live_class=self.lc, meeting_uuid=RUN_UUID,
            status=ZoomWebhookEvent.Status.PROCESSED,
            payload={"payload": {"object": {"uuid": RUN_UUID, "start_time": z(self.start), "end_time": z(self.at(60))}}},
        )
        config = AttendanceConfiguration.get_solo()
        config.finalize_after_minutes = 24 * 60
        config.save()
        with mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async"):
            self.assertEqual(zoom_attendance_catch_up_task()["queued"], [])
            config.finalize_after_minutes = 15
            config.save()
            cache.clear()
            self.assertEqual(zoom_attendance_catch_up_task()["queued"], [self.lc.id])


class ReportOutcomeTests(_Base):
    """
    The four report outcomes must never be confused:
      success with zero participants -> valid no-show evidence (once confirmed ended + waited);
      failed (permanent API error)   -> never finalizes;
      not ready (404)                -> never finalizes;
      incomplete (some runs missing) -> never finalizes.
    """

    def setUp(self):
        super().setUp()
        LiveClass.objects.filter(pk=self.lc.pk).update(status=LiveClass.ClassStatus.COMPLETED)
        self.lc.refresh_from_db()
        Attendance.objects.create(live_class=self.lc, student=self.s1, status=Attendance.Status.ABSENT)  # app tap only
        self.report_rows = []

    def roster_statuses(self):
        return [getattr(self.att(s), "status", None) for s in (self.s1, self.s2, self.s3)]

    def assert_nothing_finalized(self):
        self.lc.refresh_from_db()
        self.assertIsNone(self.lc.attendance_finalized_at)
        self.assertEqual(self.roster_statuses(), [Attendance.Status.ABSENT, None, None])
        self.assertEqual(self.att(self.s1).reconciliation_status, "PENDING")

    # 1. successful, complete, zero participants
    def test_successful_empty_report_finalizes_the_roster_as_absent(self):
        result = self.reconcile()  # Zoom lists the run as a past (ended) instance; report = 200, no rows
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["total_records"], 0)
        self.assertTrue(result["classification"]["final"])
        self.assertEqual(self.roster_statuses(), [Attendance.Status.ABSENT] * 3)
        self.assertEqual(result["classification"]["no_show_absent"], 2)
        from users.models import AdminAuditLog
        log = AdminAuditLog.objects.get(action="ZOOM_ATTENDANCE_FINALIZED", target_id=str(self.lc.id))
        self.assertEqual((log.metadata["report_rows"], log.metadata["no_show_absent"]), (0, 2))

    def test_successful_empty_report_confirmed_by_meeting_ended_webhook(self):
        ZoomWebhookEvent.objects.create(
            event_id="end-e", event_type="meeting.ended", live_class=self.lc, meeting_uuid=RUN_UUID,
            status=ZoomWebhookEvent.Status.PROCESSED,
            payload={"payload": {"object": {"uuid": RUN_UUID, "start_time": z(self.start), "end_time": z(self.at(60))}}},
        )
        no_instances = lambda url, headers=None, timeout=None: (  # noqa: E731
            resp(404, {}) if "/past_meetings/" in url else self.fake_get(url, headers, timeout))
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=no_instances):
            result = reconcile_zoom_attendance_task.apply(args=[self.lc.id]).result
        self.assertTrue(result["classification"]["final"])
        self.assertEqual(self.roster_statuses(), [Attendance.Status.ABSENT] * 3)

    def test_successful_empty_report_without_confirmed_end_does_not_finalize(self):
        # Instances API unavailable and no meeting.ended webhook: the empty report
        # may belong to nothing that happened -- not trusted.
        from courses.services.zoom_attendance import ZoomAttendanceService
        no_instances = lambda url, headers=None, timeout=None: (  # noqa: E731
            resp(404, {}) if "/past_meetings/" in url else self.fake_get(url, headers, timeout))
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=no_instances):
            result = ZoomAttendanceService.reconcile_meeting_participants(self.lc)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["classification"]["not_final_reason"], "empty_report_meeting_end_unconfirmed")
        self.assert_nothing_finalized()

    def test_successful_empty_report_waits_for_the_period(self):
        config = AttendanceConfiguration.get_solo()
        config.finalize_after_minutes = 24 * 60
        config.save()
        from courses.services.zoom_attendance import ZoomAttendanceService
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=self.fake_get):
            result = ZoomAttendanceService.reconcile_meeting_participants(self.lc)
        self.assertEqual(result["classification"]["not_final_reason"], "waiting_finalization_period")
        self.assert_nothing_finalized()

    def test_successful_empty_report_contradicted_by_live_joins_does_not_finalize(self):
        UnmatchedZoomParticipant.objects.create(live_class=self.lc, zoom_user_id="5", join_time=self.at(1),
                                                is_active=False)
        from courses.services.zoom_attendance import ZoomAttendanceService
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=self.fake_get):
            result = ZoomAttendanceService.reconcile_meeting_participants(self.lc)
        self.assertEqual(result["classification"]["not_final_reason"], "empty_report_contradicts_live_joins")
        self.assert_nothing_finalized()

    def test_report_with_rows_only_outside_the_class_window_does_not_finalize(self):
        week_ago = self.start - datetime.timedelta(days=7)
        self.report_rows = [self.row(uid="1", name="a", join=week_ago, leave=week_ago + datetime.timedelta(minutes=60))]
        from courses.services.zoom_attendance import ZoomAttendanceService
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=self.fake_get):
            result = ZoomAttendanceService.reconcile_meeting_participants(self.lc)
        self.assertEqual(result["classification"]["not_final_reason"], "report_outside_class_window")
        self.assert_nothing_finalized()

    # 2. failed
    def test_failed_report_never_finalizes(self):
        for status in (400, 403):
            self.report_status = status
            result = self.reconcile()
            self.assertEqual(result["status"], "error")
            self.assert_nothing_finalized()

    # 3. not ready
    def test_not_ready_report_never_finalizes(self):
        self.report_status = 404
        result = self.reconcile()
        self.assertEqual(result["status"], "not_ready")
        self.assert_nothing_finalized()

    def test_transient_failure_that_never_recovers_never_finalizes(self):
        self.report_status = 503
        result = self.reconcile()
        self.assertEqual(result["status"], "transient_error")
        self.assert_nothing_finalized()

    # 4. incomplete: one run's report fetched (even if empty), another not ready
    def test_incomplete_report_never_finalizes_even_if_the_fetched_part_is_empty(self):
        def two_runs(url, headers=None, timeout=None):
            if "/past_meetings/" in url:
                return resp(200, {"meetings": [{"uuid": RUN_UUID, "start_time": z(self.start)},
                                               {"uuid": "Run2==", "start_time": z(self.at(30))}]})
            if "/report/meetings/Run2" in url:
                return resp(404, {"code": 3001})
            return self.fake_get(url, headers, timeout)

        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=two_runs):
            result = reconcile_zoom_attendance_task.apply(args=[self.lc.id]).result
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["classification"]["not_final_reason"], "report_incomplete")
        self.assert_nothing_finalized()


@override_settings(ENABLE_ZOOM_ATTENDANCE_V2=False, ZOOM_ATTENDANCE_USE_REGISTRATION=False,
                   ZOOM_ATTENDANCE_AUTOMATION_START="")
class SwitchesOffTests(_Base):
    """Deployed with every attendance switch OFF: no automatic attendance processing anywhere."""

    def setUp(self):
        super().setUp()
        LiveClassRegistrant.objects.create(live_class=self.lc, student=self.s1, registrant_id="reg-s1", email="e@x.test")
        self.admin = User.objects.create_user(username="off_admin", password="x", is_staff=True, is_superuser=True)

    def post_webhook(self, event, obj):
        body = json.dumps({"event": event, "event_ts": int(timezone.now().timestamp() * 1000),
                           "payload": {"account_id": "acc", "object": obj}}).encode()
        stamp = str(int(timezone.now().timestamp()))
        sig = "v0=" + hmac.new(SECRET.encode(), f"v0:{stamp}:{body.decode()}".encode(), hashlib.sha256).hexdigest()
        with mock.patch("courses.tasks.process_zoom_participant_webhook_task.delay") as process, \
             mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as reconcile:
            res = APIClient().post("/api/courses/zoom/webhook/", data=body, content_type="application/json",
                                   HTTP_X_ZM_SIGNATURE=sig, HTTP_X_ZM_REQUEST_TIMESTAMP=stamp)
        return res, process, reconcile

    def test_participant_webhook_is_stored_but_not_processed(self):
        res, process, reconcile = self.post_webhook("meeting.participant_joined", {
            "id": MEETING_ID, "uuid": RUN_UUID, "start_time": z(self.start),
            "participant": {"user_id": "1", "user_name": "x", "participant_uuid": "pu-1", "registrant_id": "reg-s1",
                            "join_time": z(self.at(1))}})
        self.assertEqual((res.status_code, res.data["status"]), (200, "disabled"))
        process.assert_not_called()
        reconcile.assert_not_called()
        self.assertEqual(ZoomWebhookEvent.objects.get().status, ZoomWebhookEvent.Status.IGNORED)
        self.assertFalse(Attendance.objects.exists())

    def test_meeting_ended_schedules_nothing(self):
        res, process, reconcile = self.post_webhook("meeting.ended", {
            "id": MEETING_ID, "uuid": RUN_UUID, "start_time": z(self.at(0)), "end_time": z(self.at(60))})
        self.assertEqual(res.status_code, 200)
        reconcile.assert_not_called()

    def test_beat_catch_up_and_reconcile_task_do_nothing_and_call_no_zoom_api(self):
        from courses.tasks import zoom_attendance_catch_up_task
        LiveClass.objects.filter(pk=self.lc.pk).update(status=LiveClass.ClassStatus.COMPLETED)
        with mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as dispatch, \
             mock.patch("courses.services.zoom_attendance.requests.get") as http:
            self.assertEqual(zoom_attendance_catch_up_task(), {"status": "disabled"})
            result = reconcile_zoom_attendance_task.apply(args=[self.lc.id]).result
        self.assertEqual(result["status"], "disabled")
        dispatch.assert_not_called()
        http.assert_not_called()
        self.lc.refresh_from_db()
        self.assertIsNone(self.lc.attendance_finalized_at)

    def test_join_gives_the_plain_link_and_calls_no_zoom_api(self):
        LiveClassRegistrant.objects.all().delete()
        client = APIClient()
        client.force_authenticate(self.s2)
        with mock.patch("courses.services.zoom_attendance.requests.post") as post:
            res = client.post(f"/api/courses/live-classes/{self.lc.id}/join/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.data["meeting_url"], self.lc.meeting_url)
        post.assert_not_called()
        self.assertFalse(LiveClassRegistrant.objects.exists())

    def test_admin_sync_button_is_disabled(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        with mock.patch("courses.services.zoom_attendance.requests.get") as http:
            res = client.post(f"/api/courses/live-classes/{self.lc.id}/attendance/reconcile/")
        self.assertEqual(res.data["status"], "disabled")
        http.assert_not_called()

    def test_class_list_auto_complete_schedules_no_reconciliation(self):
        LiveClass.objects.filter(pk=self.lc.pk).update(status=LiveClass.ClassStatus.SCHEDULED)
        client = APIClient()
        client.force_authenticate(self.admin)
        with mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as dispatch, \
             mock.patch("courses.services.recording.ZoomRecordingService.auto_sync_live_class_recording"):
            res = client.get("/api/courses/live-classes/")
        self.assertEqual(res.status_code, 200)
        self.lc.refresh_from_db()
        self.assertEqual(self.lc.status, LiveClass.ClassStatus.COMPLETED)  # existing behaviour, unchanged
        dispatch.assert_not_called()

    def test_main_switch_on_without_start_date_still_processes_nothing(self):
        from courses.tasks import zoom_attendance_catch_up_task
        LiveClass.objects.filter(pk=self.lc.pk).update(status=LiveClass.ClassStatus.COMPLETED)
        with override_settings(ENABLE_ZOOM_ATTENDANCE_V2=True, ZOOM_ATTENDANCE_AUTOMATION_START=""), \
             mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as dispatch, \
             mock.patch("courses.services.zoom_attendance.requests.get") as http:
            self.assertEqual(zoom_attendance_catch_up_task()["status"], "automation_start_not_configured")
            self.assertEqual(reconcile_zoom_attendance_task.apply(args=[self.lc.id]).result["status"],
                             "automation_not_allowed")
        dispatch.assert_not_called()
        http.assert_not_called()


class RecoveryPreviewTests(_Base):
    """recover_zoom_attendance (November recovery): the preview writes nothing at all; --apply does."""

    def setUp(self):
        super().setUp()
        LiveClass.objects.filter(pk=self.lc.pk).update(status=LiveClass.ClassStatus.COMPLETED)
        self.lc.refresh_from_db()
        LiveClassRegistrant.objects.create(live_class=self.lc, student=self.s1, registrant_id="reg-s1", email="e@x.test")
        self.report_rows = [
            self.row(uid="1", name="a", join=self.at(0), leave=self.at(60), registrant_id="reg-s1"),
            self.row(uid="9", name="Guest", join=self.at(0), leave=self.at(60)),
        ]

    def recover(self, *extra):
        import zoneinfo
        day = self.start.astimezone(zoneinfo.ZoneInfo("Asia/Kolkata")).date().isoformat()
        out = StringIO()
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.get", side_effect=self.fake_get):
            call_command("recover_zoom_attendance", "--date", day, "--class-id", str(self.lc.id), *extra, stdout=out)
        return out.getvalue()

    def snapshot(self):
        from users.models import AdminAuditLog
        self.lc.refresh_from_db()
        return (
            list(Attendance.objects.order_by("id").values_list("student_id", "status", "duration_minutes")),
            AttendanceSession.objects.count(), UnmatchedZoomParticipant.objects.count(),
            AdminAuditLog.objects.count(), self.lc.attendance_finalized_at,
        )

    def test_preview_writes_nothing_and_apply_writes(self):
        before = self.snapshot()
        out = self.recover()
        self.assertIn("DRY-RUN", out)
        self.assertEqual(self.snapshot(), before)  # no attendance, sessions, queue, audit or finalization
        self.assertNotIn("e@x.test", out)

        self.recover("--apply")
        self.lc.refresh_from_db()
        self.assertIsNotNone(self.lc.attendance_finalized_at)
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)
        self.assertEqual(UnmatchedZoomParticipant.objects.filter(live_class=self.lc).count(), 1)  # guest: for review
        from users.models import AdminAuditLog
        self.assertTrue(AdminAuditLog.objects.filter(target_id=str(self.lc.id)).exists())

    def test_manual_decisions_survive_recovery(self):
        Attendance.objects.create(live_class=self.lc, student=self.s1, status=Attendance.Status.EXCUSED,
                                  is_manual_override=True)
        self.recover("--apply")
        self.assertEqual(self.att(self.s1).status, Attendance.Status.EXCUSED)


class SharedMeetingIdTests(_Base):
    """A series that re-uses one Zoom link: every occurrence has the same meeting ID."""

    def setUp(self):
        super().setUp()
        self.next_week = self.make_class(self.start + datetime.timedelta(days=7), status=LiveClass.ClassStatus.SCHEDULED)
        self.last_week = self.make_class(self.start - datetime.timedelta(days=7))

    def test_lookup_picks_the_occurrence_of_the_event_time(self):
        find = ZoomRecordingService.find_live_class_for_zoom_meeting
        self.assertEqual(find(MEETING_ID, "", at=self.at(10)), self.lc)
        self.assertEqual(find(MEETING_ID, "", at=self.start - datetime.timedelta(days=7)), self.last_week)
        self.assertEqual(find(MEETING_ID, ""), self.next_week)  # no time given: old behaviour kept

    def test_participant_webhook_lands_on_todays_class_not_next_week(self):
        LiveClassRegistrant.objects.create(live_class=self.lc, student=self.s1, registrant_id="reg-s1", email="e@x.test")
        self.participant("meeting.participant_joined", uid="101", name="x", join=self.at(1), registrant_id="reg-s1")
        self.assertIsNotNone(self.att(self.s1))
        self.assertFalse(Attendance.objects.filter(live_class=self.next_week).exists())
        event = ZoomWebhookEvent.objects.get(event_type="meeting.participant_joined")
        self.assertEqual(event.live_class_id, self.lc.id)

    def test_meeting_ended_reconciles_todays_class(self):
        dispatch = self.webhook("meeting.ended", {"id": MEETING_ID, "uuid": RUN_UUID, "start_time": z(self.at(0)),
                                                  "end_time": z(self.at(60))}, ts=self.at(60))
        self.assertEqual(dispatch.call_args.kwargs["args"], [self.lc.id])

    def test_personal_link_from_last_week_still_identifies_the_student(self):
        LiveClassRegistrant.objects.create(live_class=self.last_week, student=self.s1, registrant_id="reg-old",
                                           email="e@x.test")
        self.participant("meeting.participant_joined", uid="101", name="x", join=self.at(1), registrant_id="reg-old")
        self.assertIsNotNone(self.att(self.s1))


@override_settings(ENABLE_ZOOM_ATTENDANCE_V2=True, CACHES=LOCMEM,
                   ZOOM_ATTENDANCE_AUTOMATION_START="2000-01-01T00:00:00+00:00")
class CatchUpTests(_Base):
    def test_pasted_zoom_link_class_is_reconciled_only_with_proof_it_is_our_meeting(self):
        from courses.tasks import zoom_attendance_catch_up_task
        LiveClass.objects.filter(pk=self.lc.pk).delete()
        ours = self.make_class(self.start, provider=LiveClass.MeetingProvider.OTHER, store_id=False)
        foreign = self.make_class(self.start, provider=LiveClass.MeetingProvider.OTHER, meeting_id="99912345678",
                                  store_id=False)
        ZoomWebhookEvent.objects.create(
            event_id="e-ours", event_type="meeting.ended", live_class=ours, meeting_uuid=RUN_UUID,
            status=ZoomWebhookEvent.Status.PROCESSED,
            payload={"payload": {"object": {"uuid": RUN_UUID, "start_time": z(self.start), "end_time": z(self.at(60))}}},
        )
        with mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as dispatch:
            result = zoom_attendance_catch_up_task()
        self.assertEqual(result["queued"], [ours.id])
        self.assertEqual(dispatch.call_count, 1)
        self.assertNotIn(foreign.id, result["queued"])


class DiagnoseCommandTests(_Base):
    def test_read_only_and_prints_no_personal_details(self):
        LiveClassRegistrant.objects.create(live_class=self.lc, student=self.s2, registrant_id="reg-s2",
                                           email="priya.s@example.test")
        UnmatchedZoomParticipant.objects.create(live_class=self.lc, participant_name="Priya S",
                                                participant_email="guest@example.test", raw_evidence={"user_name": "Priya S"})
        before = (Attendance.objects.count(), UnmatchedZoomParticipant.objects.count(), ZoomWebhookEvent.objects.count())
        out = StringIO()
        inspect = mock.MagicMock()
        inspect.ping.return_value = {"w1": {"ok": "pong"}}
        inspect.active_queues.return_value = {"w1": [{"name": "notifications"}, {"name": "default"}]}
        with mock.patch("core.celery.app.control.inspect", return_value=inspect):
            call_command("zoom_attendance_diagnose", "--class-id", str(self.lc.id), stdout=out)
        text = out.getvalue()
        self.assertIn(f"LiveClass #{self.lc.id}", text)
        self.assertIn("'notifications' queue consumed (attendance tasks)", text)
        for secret in ("Priya", "example.test", "reg-s2", "legacy_", "tk="):
            self.assertNotIn(secret, text)
        self.assertEqual(before, (Attendance.objects.count(), UnmatchedZoomParticipant.objects.count(),
                                  ZoomWebhookEvent.objects.count()))
