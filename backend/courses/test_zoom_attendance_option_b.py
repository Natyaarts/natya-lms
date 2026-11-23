"""
Zoom attendance, Option B (no Zoom registration): both joining workflows,
reliable identity only, learned Zoom identities, exclusions, multiple meeting
runs, finalization, review-queue de-duplication, Ignore action, retries and
the catch-up task.

No network: Zoom HTTP calls are routed to canned responses by URL, and
webhooks are fed straight into the service handlers.
"""
import datetime
import urllib.parse
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from courses.models import (
    Attendance,
    AttendanceConfiguration,
    AttendanceSession,
    Course,
    CourseInstructor,
    Enrollment,
    LiveBatch,
    LiveBatchStudent,
    LiveClass,
    LiveClassJoinIntent,
    UnmatchedZoomParticipant,
    ZoomHostAccount,
    ZoomIdentityLink,
    ZoomWebhookEvent,
)
from courses.services.zoom_attendance import ZoomAttendanceService, ZoomTransientError

User = get_user_model()
UTC = datetime.timezone.utc
MEETING_ID = "77700011122"
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "optb"}}


def _z(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Resp:
    def __init__(self, status, data=None):
        self.status_code = status
        self._data = data or {}
        self.text = ""

    def json(self):
        return self._data


@override_settings(ENABLE_ZOOM_ATTENDANCE_V2=True, ZOOM_ATTENDANCE_EXCLUDED_USER_IDS=frozenset(),
                   ZOOM_ATTENDANCE_EXCLUDED_EMAILS=frozenset(), CACHES=LOCMEM,
                   ZOOM_ATTENDANCE_AUTOMATION_START="2000-01-01T00:00:00+00:00")
class _Base(TestCase):
    status = LiveClass.ClassStatus.COMPLETED

    def setUp(self):
        config = AttendanceConfiguration.get_solo()
        config.default_min_attendance_percentage = 75
        config.save()
        self.admin = User.objects.create_superuser(username="ob_admin", email="ob_admin@example.com", password="pw123456!")
        self.teacher = User.objects.create_user(username="ob_teacher", email="athira@example.com", password="pw123456!", is_teacher=True)
        self.mentor = User.objects.create_user(username="ob_mentor", email="mentor@example.com", password="pw123456!", is_mentor=True)
        self.s1 = User.objects.create_user(username="ob_s1", email="s1@example.com", password="pw123456!")
        self.s2 = User.objects.create_user(username="ob_s2", email="s2@example.com", password="pw123456!")
        self.course = Course.objects.create(title="OB Course", description="d", course_type=Course.CourseType.LIVE)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, mentor=self.mentor,
                                              batch_type=LiveBatch.BatchType.GROUP)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.s1)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.s2)
        self.t0 = (timezone.now() - datetime.timedelta(days=1)).replace(second=0, microsecond=0)
        self.lc = self._class(self.t0, "uuid-run-A==")

    def _class(self, start, uuid, meeting_id=MEETING_ID, status=None):
        return LiveClass.objects.create(
            batch=self.batch, course=self.course, instructor=self.teacher, title="Session",
            meeting_url=f"https://zoom.us/j/{meeting_id}", meeting_provider=LiveClass.MeetingProvider.ZOOM,
            zoom_meeting_id=meeting_id, zoom_uuid=uuid, scheduled_start=start, duration_minutes=60,
            status=status or self.status,
        )

    def at(self, minutes, base=None):
        return (base or self.t0) + datetime.timedelta(minutes=minutes)

    def row(self, start, end, *, email="", name="Someone", pid="", user_id="", account_id="", base=None, **extra):
        r = {"id": account_id, "user_id": user_id, "name": name, "user_email": email,
             "join_time": _z(self.at(start, base)), "leave_time": _z(self.at(end, base)),
             "duration": int((end - start) * 60)}
        if pid:
            r["participant_uuid"] = pid
        r.update(extra)
        return r

    def webhook(self, kind, minute, *, email="", name="Guest", user_id="", participant_uuid="", account_id="", uuid="uuid-run-A==", **extra):
        participant = {"user_id": user_id, "user_name": name, "email": email, "participant_uuid": participant_uuid, "id": account_id}
        participant["join_time" if kind == "join" else "leave_time"] = _z(self.at(minute))
        participant.update(extra)
        payload = {"object": {"id": MEETING_ID, "uuid": uuid, "participant": participant}}
        handler = ZoomAttendanceService.handle_participant_joined if kind == "join" else ZoomAttendanceService.handle_participant_left
        return handler(payload)

    def meeting_event(self, event_type, uuid, start_min, end_min=None, lc=None):
        lc = lc or self.lc
        obj = {"id": MEETING_ID, "uuid": uuid, "start_time": _z(self.at(start_min, lc.scheduled_start))}
        if end_min is not None:
            obj["end_time"] = _z(self.at(end_min, lc.scheduled_start))
        return ZoomWebhookEvent.objects.create(
            event_id=f"{event_type}:{uuid}:{start_min}:{end_min}", event_type=event_type,
            payload={"event": event_type, "payload": {"object": obj}}, live_class=lc, meeting_uuid=uuid,
        )

    def apply(self, rows, complete=True, lc=None):
        return ZoomAttendanceService.apply_meeting_report(lc or self.lc, rows, complete=complete)

    def att(self, student, lc=None):
        return Attendance.objects.filter(live_class=lc or self.lc, student=student).first()

    def queue(self, lc=None, status=UnmatchedZoomParticipant.Status.PENDING):
        return UnmatchedZoomParticipant.objects.filter(live_class=lc or self.lc, status=status)


# --------------------------------------------------------------------------- joining workflows
class JoiningWorkflowTests(_Base):
    status = LiveClass.ClassStatus.LIVE

    def test_app_join_makes_no_zoom_registration_call_by_default(self):
        client = APIClient()
        client.force_authenticate(self.s1)
        with mock.patch("requests.post") as post:
            res = client.post(f"/api/courses/live-classes/{self.lc.id}/join/")
        self.assertEqual(res.status_code, 200)
        post.assert_not_called()
        self.assertEqual(res.data["meeting_url"], self.lc.meeting_url)
        self.assertTrue(res.data["join_intent_token"])

    def test_app_or_shared_link_student_signed_in_with_lms_email_is_counted(self):
        self.webhook("join", 0, email="S1@Example.com", user_id="101")
        self.assertIsNotNone(self.att(self.s1))
        self.assertFalse(self.queue().exists())

    def test_teacher_shared_link_guest_goes_to_review_not_guessed(self):
        # No registrant, no customer key, no email; a name resembling a student.
        self.webhook("join", 0, name="ob_s1 phone", user_id="102")
        self.assertIsNone(self.att(self.s1))
        self.assertEqual(self.queue().count(), 1)

    def test_customer_key_resolves_from_both_webhook_and_report(self):
        intent = ZoomAttendanceService.create_join_intent(self.lc, self.s2)
        self.webhook("join", 0, user_id="103", customer_key=intent.token)
        self.apply([self.row(0, 50, user_id="103", customer_key=intent.token)], complete=False)
        att = self.att(self.s2)
        self.assertIsNotNone(att)
        self.assertFalse(self.queue().exists())
        self.assertEqual(att.sessions.filter(source=AttendanceSession.Source.REPORT).count(), 1)


# --------------------------------------------------------------------------- identity
class IdentityTests(_Base):
    def test_first_time_unknown_participant_goes_to_review(self):
        result = self.apply([self.row(0, 50, name="Kavita Nair", user_id="201")])
        self.assertEqual(result["unmatched"], 1)
        self.assertEqual(self.queue().count(), 1)
        self.assertEqual(Attendance.objects.filter(live_class=self.lc).count(), 0)

    def test_duplicate_email_accounts_are_not_chosen_arbitrarily(self):
        twin = User.objects.create_user(username="ob_twin", email="s1@example.com", password="pw123456!")
        LiveBatchStudent.objects.create(batch=self.batch, student=twin)
        self.apply([self.row(0, 50, email="s1@example.com", user_id="202")])
        self.assertIsNone(self.att(self.s1))
        self.assertIsNone(self.att(twin))
        self.assertEqual(self.queue().count(), 1)

    def test_shared_email_with_one_enrolled_account_matches_that_account(self):
        User.objects.create_user(username="ob_other", email="s1@example.com", password="pw123456!")  # not enrolled
        self.apply([self.row(0, 50, email="s1@example.com", user_id="203")])
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)

    def test_learned_zoom_identity_matches_in_next_class(self):
        acct = "AbCdEfGhIjKlMnOpQr12"
        self.apply([self.row(0, 50, name="Shyama", user_id="204", account_id=acct)])
        rec = self.queue().get()
        _, linked = ZoomAttendanceService.link_unmatched_participant(rec.id, self.s2.id, self.admin)
        self.assertEqual(linked.learned_identity, "learned")
        link = ZoomIdentityLink.objects.get(zoom_account_id=acct)
        self.assertEqual(link.student, self.s2)

        nxt = self._class(self.t0 + datetime.timedelta(days=7), "uuid-next==", meeting_id="77700099999")
        self.apply([self.row(0, 55, name="Totally different name", user_id="9", account_id=acct, base=nxt.scheduled_start)], lc=nxt)
        self.assertEqual(self.att(self.s2, nxt).status, Attendance.Status.PRESENT)
        self.assertFalse(self.queue(nxt).exists())

    def test_device_name_guest_is_never_learned(self):
        self.apply([self.row(0, 50, name="Shyama's iPhone", user_id="205")])
        rec = self.queue().get()
        _, linked = ZoomAttendanceService.link_unmatched_participant(rec.id, self.s2.id, self.admin)
        self.assertIsNone(linked.learned_identity)
        self.assertFalse(ZoomIdentityLink.objects.exists())
        nxt = self._class(self.t0 + datetime.timedelta(days=7), "uuid-next2==", meeting_id="77700099998")
        self.apply([self.row(0, 50, name="Shyama's iPhone", user_id="205", base=nxt.scheduled_start)], lc=nxt)
        self.assertIsNone(self.att(self.s2, nxt))
        self.assertEqual(self.queue(nxt).count(), 1)

    def test_conflicting_links_make_zoom_account_ambiguous(self):
        acct = "SharedFamilyAcct0001"
        ZoomIdentityLink.objects.create(zoom_account_id=acct, student=self.s1, created_by=self.admin)
        self.apply([self.row(0, 50, user_id="206", account_id=acct)])  # auto-matched to s1
        self.assertIsNotNone(self.att(self.s1))
        nxt = self._class(self.t0 + datetime.timedelta(days=7), "uuid-next3==", meeting_id="77700099997")
        rec = UnmatchedZoomParticipant.objects.create(live_class=nxt, zoom_user_id="1", join_time=nxt.scheduled_start,
                                                      raw_evidence={"id": acct})
        ZoomAttendanceService.link_unmatched_participant(rec.id, self.s2.id, self.admin)
        self.assertTrue(ZoomIdentityLink.objects.get(zoom_account_id=acct).is_ambiguous)
        third = self._class(self.t0 + datetime.timedelta(days=14), "uuid-next4==", meeting_id="77700099996")
        self.apply([self.row(0, 50, user_id="2", account_id=acct, base=third.scheduled_start)], lc=third)
        self.assertEqual(self.queue(third).count(), 1)

    def test_learned_identity_for_student_not_in_this_class_goes_to_review(self):
        outsider = User.objects.create_user(username="ob_out", email="out@example.com", password="pw123456!")
        ZoomIdentityLink.objects.create(zoom_account_id="OutsiderAccount00001", student=outsider)
        self.apply([self.row(0, 50, user_id="207", account_id="OutsiderAccount00001")])
        self.assertIsNone(self.att(outsider))
        self.assertEqual(self.queue().count(), 1)


# --------------------------------------------------------------------------- exclusions
class ExclusionTests(_Base):
    def assert_excluded(self, **row_kwargs):
        result = self.apply([self.row(0, 55, **row_kwargs)])
        self.assertEqual(result["excluded"], 1, result)
        self.assertFalse(self.queue().exists())
        self.assertEqual(Attendance.objects.filter(live_class=self.lc).count(), 0)

    def test_instructor_athira_excluded_in_report(self):
        self.assert_excluded(email="athira@example.com", name="Athira", user_id="301")

    def test_instructor_excluded_in_webhook(self):
        self.webhook("join", 0, email="athira@example.com", user_id="302")
        self.assertFalse(self.queue().exists())

    def test_substitute_instructor_excluded(self):
        sub = User.objects.create_user(username="ob_sub", email="sub@example.com", password="pw123456!", is_teacher=True)
        self.lc.substitute_instructor = sub
        self.lc.is_substitute_instructor = True
        self.lc.save()
        self.assert_excluded(email="sub@example.com", user_id="303")

    def test_batch_mentor_excluded(self):
        self.assert_excluded(email="mentor@example.com", user_id="304")

    def test_course_teaching_staff_excluded(self):
        co = User.objects.create_user(username="ob_co", email="co@example.com", password="pw123456!", is_teacher=True)
        CourseInstructor.objects.create(course=self.course, user=co)
        self.assert_excluded(email="co@example.com", user_id="305")

    def test_staff_and_superuser_excluded(self):
        User.objects.create_user(username="ob_staff", email="staff@example.com", password="pw123456!", is_staff=True)
        self.assert_excluded(email="staff@example.com", user_id="306")
        self.assert_excluded(email="ob_admin@example.com", user_id="307")

    def test_zoom_host_pool_account_excluded_by_email_and_account_id(self):
        ZoomHostAccount.objects.create(zoom_user_id="HostPoolAccount00001", email="host1@natya.example")
        self.assert_excluded(email="host1@natya.example", user_id="308")
        self.assert_excluded(account_id="HostPoolAccount00001", user_id="309")

    def test_configured_test_account_excluded(self):
        with override_settings(ZOOM_ATTENDANCE_EXCLUDED_EMAILS=frozenset({"tester@example.com"})):
            self.assert_excluded(email="tester@example.com", user_id="310")

    def test_protected_account_ids_excluded_even_if_enrolled(self):
        with override_settings(ZOOM_ATTENDANCE_EXCLUDED_USER_IDS=frozenset({self.s1.id})):
            self.assert_excluded(email="s1@example.com", user_id="311")

    def test_default_protected_ids_are_1_25_39_96(self):
        from django.conf import settings as dj
        from core import settings as core_settings
        self.assertEqual(core_settings.ZOOM_ATTENDANCE_EXCLUDED_USER_IDS, frozenset({1, 25, 39, 96}))
        self.assertIsNotNone(dj)

    def test_admin_cannot_link_instructor_as_student(self):
        LiveBatchStudent.objects.create(batch=self.batch, student=self.teacher)
        rec = UnmatchedZoomParticipant.objects.create(live_class=self.lc, zoom_user_id="1", join_time=self.t0)
        with self.assertRaises(ValueError):
            ZoomAttendanceService.link_unmatched_participant(rec.id, self.teacher.id, self.admin)


# --------------------------------------------------------------------------- multiple runs + finalization
class MeetingRunsAndFinalizationTests(_Base):
    status = LiveClass.ClassStatus.LIVE  # never flipped by auto-complete

    def router(self, reports, instances=None):
        def fake_get(url, headers=None, timeout=None):
            if "/past_meetings/" in url:
                return _Resp(200, {"meetings": instances or []}) if instances is not None else _Resp(404)
            for uuid, resp in reports.items():
                if urllib.parse.quote(urllib.parse.quote(uuid, safe=""), safe="") in url or urllib.parse.quote(uuid, safe="") in url:
                    return resp
            return _Resp(404)
        return fake_get

    def reconcile(self, reports, instances=None):
        with mock.patch("courses.services.zoom.ZoomService.get_access_token", return_value="tok"), \
                mock.patch("courses.services.zoom_attendance.requests.get", side_effect=self.router(reports, instances)):
            return ZoomAttendanceService.reconcile_meeting_participants(self.lc)

    def test_restarted_meeting_reconciles_every_run_without_double_counting(self):
        self.meeting_event("meeting.ended", "runA==", 0, 20)
        self.meeting_event("meeting.ended", "runB==", 22, 60)
        result = self.reconcile(
            {"runA==": _Resp(200, {"participants": [self.row(0, 20, email="s1@example.com", user_id="1")]}),
             "runB==": _Resp(200, {"participants": [self.row(22, 50, email="s1@example.com", user_id="1"),
                                                    self.row(30, 45, email="s1@example.com", user_id="7")]})},
            instances=[{"uuid": "runA==", "start_time": _z(self.at(0))}, {"uuid": "runB==", "start_time": _z(self.at(22))},
                       {"uuid": "other-day==", "start_time": _z(self.at(-24 * 60))}],
        )
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["runs"], 2)  # the other day's run is outside the class window
        att = self.att(self.s1)
        self.assertEqual(att.duration_minutes, 48)  # 0-20 + 22-50, overlap 30-45 counted once
        self.assertEqual(att.status, Attendance.Status.PRESENT)

    def test_genuinely_ended_live_class_is_finalized_with_absent(self):
        self.meeting_event("meeting.ended", "runA==", 0, 60)
        Attendance.objects.create(live_class=self.lc, student=self.s2)  # placeholder, no evidence
        result = self.reconcile({"runA==": _Resp(200, {"participants": [self.row(0, 20, email="s1@example.com", user_id="1")]})})
        self.assertTrue(result["classification"]["final"])
        self.assertEqual(self.att(self.s1).status, Attendance.Status.ABSENT)
        self.assertEqual(self.att(self.s2).reconciliation_status, "RECONCILED")
        self.lc.refresh_from_db()
        self.assertIsNotNone(self.lc.attendance_finalized_at)

    def test_not_finalized_while_another_run_is_active(self):
        self.meeting_event("meeting.ended", "runA==", 0, 20)
        # Restarted run started recently, no meeting.ended yet.
        recent = timezone.now() - datetime.timedelta(minutes=10)
        ZoomWebhookEvent.objects.create(
            event_id="started-runB", event_type="meeting.started", live_class=self.lc, meeting_uuid="runB==",
            payload={"payload": {"object": {"uuid": "runB==", "start_time": _z(recent)}}},
        )
        self.lc.scheduled_start = recent - datetime.timedelta(minutes=30)
        self.lc.status = LiveClass.ClassStatus.COMPLETED
        self.lc.save()
        Attendance.objects.create(live_class=self.lc, student=self.s2, status=Attendance.Status.PRESENT)
        result = ZoomAttendanceService.apply_meeting_report(self.lc, [], complete=True)
        self.assertFalse(result["classification"]["final"])
        self.assertEqual(self.att(self.s2).status, Attendance.Status.PRESENT)
        self.lc.refresh_from_db()
        self.assertIsNone(self.lc.attendance_finalized_at)

    def test_incomplete_report_never_marks_absent(self):
        self.meeting_event("meeting.ended", "runA==", 0, 20)
        self.meeting_event("meeting.ended", "runB==", 22, 60)
        Attendance.objects.create(live_class=self.lc, student=self.s2, status=Attendance.Status.PRESENT)
        result = self.reconcile({"runA==": _Resp(200, {"participants": []}), "runB==": _Resp(404)})
        self.assertEqual(result["status"], "partial")
        self.assertFalse(result["classification"]["final"])
        self.assertEqual(self.att(self.s2).status, Attendance.Status.PRESENT)

    def test_transient_zoom_errors_raise_for_retry(self):
        with self.assertRaises(ZoomTransientError):
            self.reconcile({"uuid-run-A==": _Resp(429)})
        with self.assertRaises(ZoomTransientError):
            self.reconcile({"uuid-run-A==": _Resp(503)})

    def test_manual_overrides_survive_final_pass(self):
        self.meeting_event("meeting.ended", "runA==", 0, 60)
        Attendance.objects.create(live_class=self.lc, student=self.s1, status=Attendance.Status.EXCUSED, is_manual_override=True)
        Attendance.objects.create(live_class=self.lc, student=self.s2, duration_minutes=50, is_duration_manual_override=True)
        self.reconcile({"runA==": _Resp(200, {"participants": [self.row(0, 5, email="s1@example.com", user_id="1"),
                                                                self.row(0, 5, email="s2@example.com", user_id="2")]})})
        self.assertEqual(self.att(self.s1).status, Attendance.Status.EXCUSED)
        self.assertEqual(self.att(self.s2).duration_minutes, 50)
        self.assertEqual(self.att(self.s2).status, Attendance.Status.PRESENT)

    def test_threshold_comes_from_admin_setting_not_hardcoded(self):
        config = AttendanceConfiguration.get_solo()
        config.default_min_attendance_percentage = 60
        config.save()
        self.meeting_event("meeting.ended", "runA==", 0, 60)
        self.reconcile({"runA==": _Resp(200, {"participants": [self.row(0, 40, email="s1@example.com", user_id="1")]})})
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)  # 66.7% >= 60%


# --------------------------------------------------------------------------- review queue
class ReviewQueueTests(_Base):
    status = LiveClass.ClassStatus.LIVE

    def test_webhook_and_report_evidence_merge_into_one_queue_record(self):
        self.webhook("join", 0, name="Guest", user_id="16778240", participant_uuid="PU-1")
        self.webhook("leave", 30, name="Guest", user_id="16778240", participant_uuid="PU-1", duration=1800)
        self.apply([self.row(1, 30, name="Guest", user_id="16778240")], complete=False)
        self.assertEqual(UnmatchedZoomParticipant.objects.filter(live_class=self.lc).count(), 1)

    def test_leave_without_duration_closes_the_open_record(self):
        # Zoom may omit "duration" on participant_left; the long-running join
        # must still be closed, not duplicated.
        self.webhook("join", 0, name="Guest", user_id="555", participant_uuid="PU-LONG")
        self.webhook("leave", 40, name="Guest", user_id="555", participant_uuid="PU-LONG")
        recs = UnmatchedZoomParticipant.objects.filter(live_class=self.lc)
        self.assertEqual(recs.count(), 1)
        rec = recs.get()
        self.assertFalse(rec.is_active)
        self.assertEqual(rec.leave_time, self.at(40))
        self.assertEqual(rec.duration_seconds, 40 * 60)

    def test_reused_per_meeting_id_in_another_run_is_a_new_record(self):
        # Per-meeting participant IDs repeat across meeting runs: no merge without a time match.
        self.webhook("join", 0, name="Guest A", user_id="16778250")
        self.webhook("join", 45, name="Guest B", user_id="16778250")
        self.assertEqual(UnmatchedZoomParticipant.objects.filter(live_class=self.lc).count(), 2)

    def test_report_evidence_follows_an_earlier_admin_link(self):
        self.webhook("join", 0, name="Guest", user_id="16778241")
        rec = self.queue().get()
        ZoomAttendanceService.link_unmatched_participant(rec.id, self.s1.id, self.admin)
        self.apply([self.row(0, 50, name="Guest", user_id="16778241")], complete=False)
        self.assertEqual(self.att(self.s1).sessions.filter(source=AttendanceSession.Source.REPORT).count(), 1)
        self.assertFalse(self.queue().exists())

    def test_ignored_participant_stays_out(self):
        self.webhook("join", 0, name="Observer", user_id="16778242")
        rec = self.queue().get()
        ZoomAttendanceService.ignore_unmatched_participant(rec.id, self.admin)
        self.apply([self.row(0, 50, name="Observer", user_id="16778242")], complete=False)
        self.assertFalse(self.queue().exists())
        self.assertEqual(UnmatchedZoomParticipant.objects.filter(live_class=self.lc).count(), 1)

    def test_ignore_endpoint_permissions_and_class_scope(self):
        rec = UnmatchedZoomParticipant.objects.create(live_class=self.lc, zoom_user_id="5", join_time=self.t0)
        other = self._class(self.t0 + datetime.timedelta(days=2), "uuid-other==", meeting_id="77700055555")
        client = APIClient()
        client.force_authenticate(self.s1)
        url = f"/api/courses/live-classes/{self.lc.id}/attendance/ignore-unmatched/"
        self.assertEqual(client.post(url, {"unmatched_id": rec.id}, format="json").status_code, 403)
        client.force_authenticate(self.admin)
        wrong = f"/api/courses/live-classes/{other.id}/attendance/ignore-unmatched/"
        self.assertEqual(client.post(wrong, {"unmatched_id": rec.id}, format="json").status_code, 404)
        res = client.post(url, {"unmatched_id": rec.id}, format="json")
        self.assertEqual(res.status_code, 200, res.content)
        rec.refresh_from_db()
        self.assertEqual(rec.status, UnmatchedZoomParticipant.Status.IGNORED)

    def test_link_endpoint_rejects_record_from_another_class(self):
        other = self._class(self.t0 + datetime.timedelta(days=2), "uuid-other2==", meeting_id="77700055556")
        rec = UnmatchedZoomParticipant.objects.create(live_class=other, zoom_user_id="6", join_time=self.t0)
        client = APIClient()
        client.force_authenticate(self.admin)
        res = client.post(f"/api/courses/live-classes/{self.lc.id}/attendance/link-unmatched/",
                          {"unmatched_id": rec.id, "student_id": self.s1.id}, format="json")
        self.assertEqual(res.status_code, 404)

    def test_cannot_ignore_a_linked_participant(self):
        rec = UnmatchedZoomParticipant.objects.create(live_class=self.lc, zoom_user_id="7", join_time=self.t0)
        ZoomAttendanceService.link_unmatched_participant(rec.id, self.s1.id, self.admin)
        with self.assertRaises(ValueError):
            ZoomAttendanceService.ignore_unmatched_participant(rec.id, self.admin)

    def test_suggestions_never_use_names_and_need_a_single_join_click(self):
        UnmatchedZoomParticipant.objects.create(live_class=self.lc, zoom_user_id="8", participant_name="ob_s1",
                                                join_time=self.at(5))
        client = APIClient()
        client.force_authenticate(self.admin)
        url = f"/api/courses/live-classes/{self.lc.id}/attendance/live/"
        row = client.get(url).json()["unmatched_participants"][0]
        self.assertIsNone(row["suggested_student_id"])  # name similarity is not used

        intent = LiveClassJoinIntent.objects.create(student=self.s2, live_class=self.lc, token="t-1",
                                                    expires_at=self.at(20))
        LiveClassJoinIntent.objects.filter(pk=intent.pk).update(created_at=self.at(4))
        row = client.get(url).json()["unmatched_participants"][0]
        self.assertEqual(row["suggested_student_id"], self.s2.id)

        second = LiveClassJoinIntent.objects.create(student=self.s1, live_class=self.lc, token="t-2", expires_at=self.at(20))
        LiveClassJoinIntent.objects.filter(pk=second.pk).update(created_at=self.at(6))
        row = client.get(url).json()["unmatched_participants"][0]
        self.assertIsNone(row["suggested_student_id"])  # two students clicked Join: no suggestion

    def test_linking_before_finalization_does_not_demote(self):
        Attendance.objects.create(live_class=self.lc, student=self.s1, status=Attendance.Status.PRESENT)
        rec = UnmatchedZoomParticipant.objects.create(live_class=self.lc, zoom_user_id="9", join_time=self.at(0),
                                                      leave_time=self.at(5), duration_seconds=300, is_active=False)
        att, _ = ZoomAttendanceService.link_unmatched_participant(rec.id, self.s1.id, self.admin)
        self.assertEqual(att.status, Attendance.Status.PRESENT)


# --------------------------------------------------------------------------- tasks
@override_settings(ENABLE_ZOOM_ATTENDANCE_V2=True, CACHES=LOCMEM, CELERY_TASK_ALWAYS_EAGER=True,
                   ZOOM_ATTENDANCE_EXCLUDED_USER_IDS=frozenset(),
                   ZOOM_ATTENDANCE_AUTOMATION_START="2000-01-01T00:00:00+00:00")
class TaskTests(_Base):
    def run_task(self, outcomes):
        from courses.tasks import reconcile_zoom_attendance_task
        calls = {"n": 0}

        def fake(live_class):
            out = outcomes[min(calls["n"], len(outcomes) - 1)]
            calls["n"] += 1
            if isinstance(out, Exception):
                raise out
            return out

        with mock.patch.object(ZoomAttendanceService, "reconcile_meeting_participants", side_effect=fake):
            result = reconcile_zoom_attendance_task.apply(args=[self.lc.id]).get()
        return result, calls["n"]

    def test_transient_error_retried_once_per_failure(self):
        ok = {"status": "success", "classification": {"final": True}}
        result, n = self.run_task([ZoomTransientError("HTTP 429"), ZoomTransientError("HTTP 503"), ok])
        self.assertEqual(n, 3)
        self.assertEqual(result["status"], "success")

    def test_not_ready_then_success(self):
        result, n = self.run_task([{"status": "not_ready"}, {"status": "success", "classification": {"final": True}}])
        self.assertEqual(n, 2)
        self.assertEqual(result["status"], "success")

    def test_not_final_is_retried(self):
        result, n = self.run_task([{"status": "success", "classification": {"final": False}},
                                   {"status": "success", "classification": {"final": True}}])
        self.assertEqual(n, 2)

    def test_permanent_error_not_retried(self):
        result, n = self.run_task([{"status": "error", "error": "HTTP 403"}])
        self.assertEqual(n, 1)
        self.assertEqual(result["status"], "error")

    def test_retries_are_bounded(self):
        from courses.tasks import ZOOM_ATTENDANCE_RETRY_DELAYS
        result, n = self.run_task([{"status": "not_ready"}])
        self.assertEqual(n, len(ZOOM_ATTENDANCE_RETRY_DELAYS) + 1)

    def test_task_is_noop_when_flag_off(self):
        with override_settings(ENABLE_ZOOM_ATTENDANCE_V2=False):
            result, n = self.run_task([{"status": "success"}])
        self.assertEqual((result["status"], n), ("disabled", 0))

    def test_catch_up_queues_only_ended_unfinalized_classes_once(self):
        from courses.tasks import zoom_attendance_catch_up_task
        finalized = self._class(self.at(-180), "u-fin==", meeting_id="1")
        finalized.attendance_finalized_at = timezone.now()
        finalized.save()
        cancelled = self._class(self.at(-240), "u-can==", meeting_id="2", status=LiveClass.ClassStatus.CANCELLED)
        future = self._class(timezone.now() + datetime.timedelta(hours=2), "u-fut==", meeting_id="3",
                             status=LiveClass.ClassStatus.SCHEDULED)
        with mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as dispatch:
            first = zoom_attendance_catch_up_task()
            second = zoom_attendance_catch_up_task()
        self.assertEqual(first["queued"], [self.lc.id])
        self.assertEqual(second["queued"], [])  # already pending
        self.assertEqual(dispatch.call_count, 1)
        self.assertNotIn(finalized.id, first["queued"])
        self.assertNotIn(cancelled.id, first["queued"])
        self.assertNotIn(future.id, first["queued"])

    def test_catch_up_is_noop_when_flag_off(self):
        from courses.tasks import zoom_attendance_catch_up_task
        with override_settings(ENABLE_ZOOM_ATTENDANCE_V2=False):
            self.assertEqual(zoom_attendance_catch_up_task(), {"status": "disabled"})


# --------------------------------------------------------------------------- automation start gate
import hashlib as _hashlib
import hmac as _hmac
import io as _io
import json as _json

from django.core.management import call_command as _call_command

from courses.services.attendance_automation import automatic_reconciliation_allowed, automation_start

_WEBHOOK_SECRET = "optb_webhook_secret"


@override_settings(ENABLE_ZOOM_ATTENDANCE_V2=True, CACHES=LOCMEM, ZOOM_ATTENDANCE_EXCLUDED_USER_IDS=frozenset(),
                   ZOOM_WEBHOOK_SECRET_TOKEN=_WEBHOOK_SECRET, DEBUG=False)
class AutomationStartTests(_Base):
    def start_at(self, dt):
        return override_settings(ZOOM_ATTENDANCE_AUTOMATION_START=dt.isoformat())

    def ended_class(self, start, meeting_id, uuid, status=LiveClass.ClassStatus.COMPLETED):
        return self._class(start, uuid, meeting_id=meeting_id, status=status)

    # ---- setting format / fail-safe
    def test_valid_formats_parse_timezone_aware(self):
        for raw in ("2026-11-25T00:00:00+05:30", "2026-11-24T18:30:00Z", "2026-11-24T18:30:00+00:00"):
            with override_settings(ZOOM_ATTENDANCE_AUTOMATION_START=raw):
                value = automation_start()
                self.assertIsNotNone(value, raw)
                self.assertEqual(value.astimezone(UTC), datetime.datetime(2026, 11, 24, 18, 30, tzinfo=UTC))

    def test_missing_setting_disables_automation(self):
        for raw in ("", "   "):
            with override_settings(ZOOM_ATTENDANCE_AUTOMATION_START=raw):
                self.assertIsNone(automation_start())
                self.assertFalse(automatic_reconciliation_allowed(self.lc))

    def test_invalid_setting_disables_automation(self):
        for raw in ("not-a-date", "2026-13-45T99:00:00+05:30", "2026-11-25T00:00:00", "25/11/2026"):
            with override_settings(ZOOM_ATTENDANCE_AUTOMATION_START=raw):
                self.assertIsNone(automation_start(), raw)  # naive (no offset) is invalid too
                self.assertFalse(automatic_reconciliation_allowed(self.lc))

    # ---- before / exactly at / after
    def test_class_before_at_and_after_start(self):
        start = self.lc.scheduled_start
        before = self.ended_class(start - datetime.timedelta(minutes=1), "8801", "u-before==")
        after = self.ended_class(start + datetime.timedelta(minutes=1), "8802", "u-after==")
        with self.start_at(start):
            self.assertFalse(automatic_reconciliation_allowed(before))
            self.assertTrue(automatic_reconciliation_allowed(self.lc))   # exactly at
            self.assertTrue(automatic_reconciliation_allowed(after))

    # ---- trigger 1: catch-up task
    def test_catch_up_ignores_classes_before_start(self):
        from courses.tasks import zoom_attendance_catch_up_task
        start = self.lc.scheduled_start  # self.lc is exactly at the start
        before = self.ended_class(start - datetime.timedelta(hours=2), "8803", "u-b2==")
        after = self.ended_class(start + datetime.timedelta(hours=1), "8804", "u-a2==")
        with self.start_at(start), mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as dispatch:
            result = zoom_attendance_catch_up_task()
        self.assertEqual(sorted(result["queued"]), sorted([self.lc.id, after.id]))
        self.assertNotIn(before.id, result["queued"])
        self.assertEqual(dispatch.call_count, 2)

    def test_catch_up_does_nothing_without_valid_setting(self):
        from courses.tasks import zoom_attendance_catch_up_task
        for raw in ("", "garbage", "2026-11-25T00:00:00"):
            with override_settings(ZOOM_ATTENDANCE_AUTOMATION_START=raw), \
                    mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as dispatch:
                result = zoom_attendance_catch_up_task()
            self.assertEqual(result["status"], "automation_start_not_configured")
            dispatch.assert_not_called()

    # ---- trigger 2: auto-complete (class list views)
    def _expired_scheduled(self, start, meeting_id, uuid):
        return self.ended_class(start, meeting_id, uuid, status=LiveClass.ClassStatus.SCHEDULED)

    def _load_class_list(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        with mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as dispatch, \
                mock.patch("courses.services.recording.ZoomRecordingService.auto_sync_live_class_recording"):
            res = client.get("/api/courses/live-classes/")
        self.assertEqual(res.status_code, 200, res.content)
        return [c.kwargs.get("args", c.args[0] if c.args else None)[0] for c in dispatch.call_args_list]

    def test_auto_complete_only_reconciles_classes_from_start(self):
        start = timezone.now() - datetime.timedelta(hours=10)
        before = self._expired_scheduled(start - datetime.timedelta(hours=3), "8805", "u-ac-b==")
        at = self._expired_scheduled(start, "8806", "u-ac-at==")
        after = self._expired_scheduled(start + datetime.timedelta(hours=2), "8807", "u-ac-a==")
        with self.start_at(start):
            dispatched = self._load_class_list()
        self.assertNotIn(before.id, dispatched)
        self.assertIn(at.id, dispatched)
        self.assertIn(after.id, dispatched)
        before.refresh_from_db()
        self.assertEqual(before.status, LiveClass.ClassStatus.COMPLETED)  # still auto-completed, just not reconciled

    def test_auto_complete_does_not_reconcile_without_setting(self):
        self._expired_scheduled(timezone.now() - datetime.timedelta(hours=5), "8808", "u-ac-m==")
        with override_settings(ZOOM_ATTENDANCE_AUTOMATION_START=""):
            self.assertEqual(self._load_class_list(), [])

    # ---- trigger 3: meeting.ended webhook
    def _post_meeting_ended(self, live_class):
        body = _json.dumps({
            "event": "meeting.ended",
            "event_ts": int(timezone.now().timestamp() * 1000),
            "payload": {"object": {"id": live_class.zoom_meeting_id, "uuid": live_class.zoom_uuid,
                                   "start_time": _z(live_class.scheduled_start),
                                   "end_time": _z(live_class.scheduled_start + datetime.timedelta(minutes=60))}},
        }).encode("utf-8")
        ts = str(int(timezone.now().timestamp()))
        sig = "v0=" + _hmac.new(_WEBHOOK_SECRET.encode(), f"v0:{ts}:{body.decode()}".encode(), _hashlib.sha256).hexdigest()
        client = APIClient()
        with mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as dispatch:
            res = client.post("/api/courses/zoom/webhook/", data=body, content_type="application/json",
                              HTTP_X_ZM_SIGNATURE=sig, HTTP_X_ZM_REQUEST_TIMESTAMP=ts)
        self.assertEqual(res.status_code, 200, res.content)
        return dispatch

    def test_meeting_ended_only_reconciles_classes_from_start(self):
        start = self.lc.scheduled_start
        before = self.ended_class(start - datetime.timedelta(days=1), "8809", "u-me-b==")
        after = self.ended_class(start + datetime.timedelta(hours=1), "8810", "u-me-a==")
        with self.start_at(start):
            self.assertFalse(self._post_meeting_ended(before).called)
            at_dispatch = self._post_meeting_ended(self.lc)
            after_dispatch = self._post_meeting_ended(after)
        self.assertEqual(at_dispatch.call_args.kwargs["args"], [self.lc.id])
        self.assertEqual(after_dispatch.call_args.kwargs["args"], [after.id])

    def test_meeting_ended_does_not_reconcile_without_setting(self):
        with override_settings(ZOOM_ATTENDANCE_AUTOMATION_START="invalid"):
            self.assertFalse(self._post_meeting_ended(self.lc).called)

    # ---- final gate in the task itself
    def test_reconcile_task_refuses_classes_before_start_or_without_setting(self):
        from courses.tasks import reconcile_zoom_attendance_task
        before = self.ended_class(self.lc.scheduled_start - datetime.timedelta(days=1), "8811", "u-t-b==")
        with mock.patch.object(ZoomAttendanceService, "reconcile_meeting_participants") as service, \
                override_settings(CELERY_TASK_ALWAYS_EAGER=True):
            with self.start_at(self.lc.scheduled_start):
                self.assertEqual(reconcile_zoom_attendance_task.apply(args=[before.id]).get()["status"], "automation_not_allowed")
            with override_settings(ZOOM_ATTENDANCE_AUTOMATION_START=""):
                self.assertEqual(reconcile_zoom_attendance_task.apply(args=[self.lc.id]).get()["status"], "automation_not_allowed")
        service.assert_not_called()

    # ---- manual paths are not blocked
    def test_manual_sync_works_for_older_classes_and_without_setting(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        with override_settings(ZOOM_ATTENDANCE_AUTOMATION_START=""), \
                mock.patch.object(ZoomAttendanceService, "fetch_meeting_reports",
                                  return_value={"status": "success", "participants": [self.row(0, 50, email="s1@example.com", user_id="1")], "runs": 1}):
            res = client.post(f"/api/courses/live-classes/{self.lc.id}/attendance/reconcile/")
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(res.json()["status"], "success")
        self.assertEqual(self.att(self.s1).status, Attendance.Status.PRESENT)

    def test_recovery_dry_run_works_for_older_classes_and_without_setting(self):
        import zoneinfo
        day = self.t0.astimezone(zoneinfo.ZoneInfo("Asia/Kolkata")).date().isoformat()
        out = _io.StringIO()
        with override_settings(ZOOM_ATTENDANCE_AUTOMATION_START=""), \
                mock.patch.object(ZoomAttendanceService, "fetch_meeting_reports",
                                  return_value={"status": "success", "participants": [self.row(0, 50, email="s1@example.com", user_id="1")], "runs": 1}):
            _call_command("recover_zoom_attendance", "--date", day, "--class-id", str(self.lc.id), stdout=out)
        self.assertIn("previewed", out.getvalue())
        self.assertIsNone(self.att(self.s1))  # dry run: nothing written
