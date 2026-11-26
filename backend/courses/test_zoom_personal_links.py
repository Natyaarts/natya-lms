"""
Personal Zoom join links (registrants), actual class duration on the history
list, and the legacy student name backfill.
"""
import datetime
import itertools
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from courses.models import (
    Course, LiveBatch, LiveBatchStudent, LiveClass, LiveClassRegistrant, UnmatchedZoomParticipant, ZoomWebhookEvent,
)
from courses.services.conducted_time import bulk_conducted_minutes, get_conducted_time
from courses.services.zoom import ZoomService
from courses.services.zoom_attendance import ZoomAttendanceService

User = get_user_model()
UTC = datetime.timezone.utc
_ids = itertools.count(1)


def _resp(status_code, body=None):
    r = mock.MagicMock(status_code=status_code, headers={}, text=str(body or ""))
    r.json.return_value = body or {}
    return r


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.teacher = User.objects.create_user(id=7001, username="pl_teacher", password="x", is_teacher=True, is_student=False)
        self.phone_student = User.objects.create_user(id=7002, username="legacy_69e30dcd98ee51775f421914", email="",
                                                      first_name="Sabitha", last_name="Azeez", password="x")
        self.mail_student = User.objects.create_user(id=7003, username="pl_mail", email="ananya@example.test", password="x")
        self.course = Course.objects.create(title="PL Course", course_type=Course.CourseType.LIVE, is_published=True, price=0)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type=LiveBatch.BatchType.GROUP)
        for s in (self.phone_student, self.mail_student):
            LiveBatchStudent.objects.create(batch=self.batch, student=s)
        self.start = timezone.now() - datetime.timedelta(minutes=5)
        self.lc = self._class(self.start)

    def _class(self, start, status=LiveClass.ClassStatus.LIVE, meeting_id="88812345678"):
        return LiveClass.objects.create(
            batch=self.batch, course=self.course, instructor=self.teacher, title=f"PL {next(_ids)}",
            meeting_url=f"https://zoom.us/j/{meeting_id}", meeting_provider=LiveClass.MeetingProvider.ZOOM,
            zoom_meeting_id=meeting_id, scheduled_start=start, duration_minutes=60, status=status,
        )


class RegistrantTests(_Base):
    def test_phone_only_student_gets_internal_email_and_real_name(self):
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.post",
                        return_value=_resp(201, {"registrant_id": "reg-1", "join_url": "https://zoom.us/w/888?tk=a"})) as post:
            reg = ZoomAttendanceService.register_student_for_meeting(self.lc, self.phone_student)
        self.assertEqual((reg.registrant_id, reg.join_url), ("reg-1", "https://zoom.us/w/888?tk=a"))
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["email"], "student-7002@students.natyaarts.com")
        self.assertEqual((payload["first_name"], payload["last_name"]), ("Sabitha", "Azeez"))

    def test_real_email_used_when_present_and_unnamed_account_is_not_shown_as_username(self):
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.post",
                        return_value=_resp(201, {"registrant_id": "reg-2", "join_url": "u"})) as post:
            ZoomAttendanceService.register_student_for_meeting(self.lc, self.mail_student)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["email"], "ananya@example.test")
        self.assertEqual((payload["first_name"], payload["last_name"]), ("Student", "#7003"))

    def test_registration_is_enabled_on_old_meetings_then_retried(self):
        disabled = _resp(400, {"code": 3043, "message": "Registration has not been enabled for this meeting: 88812345678."})
        ok = _resp(201, {"registrant_id": "reg-3", "join_url": "https://zoom.us/w/888?tk=b"})
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.post", side_effect=[disabled, ok]) as post, \
             mock.patch.object(ZoomService, "enable_meeting_registration", return_value=True) as enable:
            reg = ZoomAttendanceService.register_student_for_meeting(self.lc, self.phone_student)
        enable.assert_called_once_with("88812345678")
        self.assertEqual(post.call_count, 2)
        self.assertEqual(reg.registrant_id, "reg-3")

    def test_meeting_that_cannot_use_registration_falls_back_and_is_not_retried(self):
        disabled = _resp(400, {"message": "Registration has not been enabled for this meeting."})
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.post", return_value=disabled) as post, \
             mock.patch.object(ZoomService, "enable_meeting_registration", return_value=False):
            self.assertIsNone(ZoomAttendanceService.register_student_for_meeting(self.lc, self.phone_student))
            self.assertIsNone(ZoomAttendanceService.register_student_for_meeting(self.lc, self.mail_student))
        self.assertEqual(post.call_count, 1)  # second student skipped the API (cached as unsupported)

    def test_recurring_meeting_reuses_registration_and_resolves_identity(self):
        earlier = self._class(self.start - datetime.timedelta(days=7), status=LiveClass.ClassStatus.COMPLETED)
        LiveClassRegistrant.objects.create(live_class=earlier, student=self.phone_student, registrant_id="reg-9",
                                           join_url="https://zoom.us/w/888?tk=z", email="student-7002@students.natyaarts.com")
        # Student joins this week with the personal link from last week (no Join click this week).
        who = ZoomAttendanceService.resolve_student_identity(self.lc, {"registrant_id": "reg-9"}, {})
        self.assertEqual(who, self.phone_student)
        with mock.patch("courses.services.zoom_attendance.requests.post") as post:
            reg = ZoomAttendanceService.register_student_for_meeting(self.lc, self.phone_student)
        post.assert_not_called()
        self.assertEqual((reg.live_class_id, reg.registrant_id), (self.lc.id, "reg-9"))

    def test_registrant_of_other_meeting_or_non_member_is_not_resolved(self):
        other_course = Course.objects.create(title="Other", course_type=Course.CourseType.LIVE, is_published=True, price=0)
        # Its own teacher: PostgreSQL forbids one teacher having two overlapping classes.
        other_teacher = User.objects.create_user(id=7010, username="pl_teacher2", password="x", is_teacher=True,
                                                 is_student=False)
        other_batch = LiveBatch.objects.create(course=other_course, instructor=other_teacher, batch_type=LiveBatch.BatchType.GROUP)
        outsider = User.objects.create_user(id=7009, username="pl_outsider", password="x")
        LiveBatchStudent.objects.create(batch=other_batch, student=outsider)
        other = LiveClass.objects.create(
            batch=other_batch, course=other_course, instructor=other_teacher, title="Other", meeting_url="https://zoom.us/j/1",
            meeting_provider=LiveClass.MeetingProvider.ZOOM, zoom_meeting_id="1", scheduled_start=self.start, duration_minutes=60,
        )
        LiveClassRegistrant.objects.create(live_class=other, student=outsider, registrant_id="reg-x", email="o@x.test")
        self.assertIsNone(ZoomAttendanceService.resolve_student_identity(self.lc, {"registrant_id": "reg-x"}, {}))

    @override_settings(ENABLE_ZOOM_ATTENDANCE_V2=True, ZOOM_ATTENDANCE_USE_REGISTRATION=True)
    def test_join_returns_personal_link_for_phone_only_student(self):
        client = APIClient()
        client.force_authenticate(self.phone_student)
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom_attendance.requests.post",
                        return_value=_resp(201, {"registrant_id": "reg-5", "join_url": "https://zoom.us/w/888?tk=p"})):
            res = client.post(f"/api/courses/live-classes/{self.lc.id}/join/")
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.data["meeting_url"], res.data["registrant_id"]), ("https://zoom.us/w/888?tk=p", "reg-5"))

    def test_join_keeps_plain_link_when_personal_links_are_off(self):
        client = APIClient()
        client.force_authenticate(self.phone_student)
        with mock.patch("courses.services.zoom_attendance.requests.post") as post:
            res = client.post(f"/api/courses/live-classes/{self.lc.id}/join/")
        post.assert_not_called()
        self.assertEqual(res.data["meeting_url"], "https://zoom.us/j/88812345678")


class MeetingCreationTests(TestCase):
    def _create(self):
        created = _resp(201, {"id": 123, "uuid": "u", "host_id": "h", "join_url": "https://zoom.us/j/123",
                              "start_url": "https://zoom.us/s/123", "password": "p"})
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom.requests.post", return_value=created) as post:
            ZoomService.create_meeting(host_id="z_host_a_0001", topic="T", start_time_iso="2026-12-01T10:00:00")
        return post.call_args.kwargs["json"]["settings"]

    @override_settings(ZOOM_ATTENDANCE_USE_REGISTRATION=True)
    def test_new_meetings_have_auto_approved_registration_without_emails(self):
        settings = self._create()
        self.assertEqual(settings["approval_type"], 0)
        self.assertFalse(settings["registrants_confirmation_email"])
        self.assertFalse(settings["registrants_email_notification"])

    def test_no_registration_when_personal_links_are_off(self):
        self.assertNotIn("approval_type", self._create())

    def test_enable_meeting_registration_patches_settings(self):
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom.requests.patch", return_value=_resp(204)) as patch:
            self.assertTrue(ZoomService.enable_meeting_registration("888 1234 5678"))
        self.assertIn("/meetings/88812345678", patch.call_args.args[0])
        self.assertEqual(patch.call_args.kwargs["json"]["settings"]["approval_type"], 0)


class HistoryDurationTests(_Base):
    def _ended(self, lc, start, minutes):
        obj = {"id": int(lc.zoom_meeting_id), "uuid": "", "start_time": start.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "end_time": (start + datetime.timedelta(minutes=minutes)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
        ZoomWebhookEvent.objects.create(event_id=f"pl-{next(_ids)}", event_type="meeting.ended",
                                        payload={"event": "meeting.ended", "payload": {"object": obj}}, live_class=lc)

    def test_bulk_matches_single_and_history_shows_zoom_time_and_unmatched(self):
        day = datetime.datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
        a = self._class(day, status=LiveClass.ClassStatus.COMPLETED)
        b = self._class(day + datetime.timedelta(days=7), status=LiveClass.ClassStatus.COMPLETED)  # same recurring meeting id
        c = self._class(day, status=LiveClass.ClassStatus.COMPLETED, meeting_id="5550001")       # no Zoom events
        self._ended(a, day + datetime.timedelta(minutes=2), 54)
        self._ended(b, b.scheduled_start, 41)
        bulk = bulk_conducted_minutes([a, b, c])
        self.assertEqual(bulk, {a.id: get_conducted_time(a)["minutes"], b.id: get_conducted_time(b)["minutes"]})
        self.assertEqual(bulk, {a.id: 54, b.id: 41})

        UnmatchedZoomParticipant.objects.create(live_class=a, participant_name="Sandhya", duration_seconds=3480,
                                                status=UnmatchedZoomParticipant.Status.PENDING)
        admin = User.objects.create_superuser(id=7010, username="pl_admin", email="pl_admin@x.test", password="x")
        client = APIClient()
        client.force_authenticate(admin)
        res = client.get("/api/courses/live-classes/history/?page_size=100")
        self.assertEqual(res.status_code, 200)
        rows = {r["id"]: r for r in (res.data["results"] if isinstance(res.data, dict) else res.data)}
        self.assertEqual((rows[a.id]["conducted_minutes"], rows[a.id]["unmatched_count"]), (54, 1))
        self.assertEqual((rows[c.id]["conducted_minutes"], rows[c.id]["unmatched_count"]), (None, 0))


class LegacyNameBackfillTests(TestCase):
    def setUp(self):
        import openpyxl
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "export.xlsx"
        wb = openpyxl.Workbook()
        fees = wb.active
        fees.title = "Student Fees"
        fees.append(["Student Name", "Student Email", "Student ID"])
        fees.append(["Sabitha  Abdul Azeez", "", "69e30dcd98ee51775f421914"])
        fees.append(["Kavya", "", "aaaaaaaaaaaaaaaaaaaaaaaa"])
        fees.append(["Meera", "", "bbbbbbbbbbbbbbbbbbbbbbbb"])
        courses = wb.create_sheet("Student Courses")
        courses.append(["Student ID", "Student Name"])
        courses.append(["bbbbbbbbbbbbbbbbbbbbbbbb", "Someone Else"])   # conflicting name -> skipped
        courses.append(["69e30dcd98ee51775f421914", "sabitha abdul azeez"])  # same name, other case -> fine
        wb.save(self.path)
        mk = lambda uid, username, **kw: User.objects.create_user(id=uid, username=username, password="x", **kw)
        self.unnamed = mk(8001, "legacy_69e30dcd98ee51775f421914")
        self.named = mk(8002, "legacy_aaaaaaaaaaaaaaaaaaaaaaaa", first_name="Kavya R")
        self.conflict = mk(8003, "legacy_bbbbbbbbbbbbbbbbbbbbbbbb")
        self.missing = mk(8004, "legacy_cccccccccccccccccccccccc")
        self.protected = mk(39, "legacy_dddddddddddddddddddddddd")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cmd(self, *args):
        out = StringIO()
        call_command("backfill_legacy_student_names", "--workbook", str(self.path), *args, stdout=out)
        return out.getvalue()

    def test_dry_run_changes_nothing_and_prints_counts_only(self):
        out = self.run_cmd()
        self.assertIn("to_update: 1", out)
        self.assertIn("skipped_conflicting_names: 1", out)
        self.assertIn("not_in_export: 1", out)
        self.assertNotIn("Sabitha", out)
        self.unnamed.refresh_from_db()
        self.assertEqual(self.unnamed.first_name, "")

    def test_apply_fills_only_empty_names_by_exact_id(self):
        self.run_cmd("--apply")
        for u in (self.unnamed, self.named, self.conflict, self.missing, self.protected):
            u.refresh_from_db()
        self.assertEqual(self.unnamed.get_full_name(), "Sabitha Abdul Azeez")
        self.assertEqual(self.named.first_name, "Kavya R")      # never overwritten
        self.assertEqual(self.conflict.first_name, "")          # conflicting export names
        self.assertEqual(self.missing.first_name, "")
        self.assertEqual(self.protected.first_name, "")
