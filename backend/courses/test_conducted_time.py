"""
Conducted (actual) class time derived from stored Zoom meeting.started /
meeting.ended webhook events, and the extra fields on the live attendance
endpoint (`conducted`, `zoom_attendance_enabled`).
"""
import datetime
import itertools

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from courses.models import Course, LiveBatch, LiveBatchStudent, LiveClass, ZoomWebhookEvent
from courses.services.conducted_time import get_conducted_time

User = get_user_model()
UTC = datetime.timezone.utc
_ids = itertools.count(1)


def _z(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConductedTimeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(username="ct_admin", email="ct_admin@example.com", password="pw123456!")
        self.teacher = User.objects.create_user(username="ct_teacher", email="ct_t@example.com", password="pw123456!", is_teacher=True)
        self.student = User.objects.create_user(username="ct_student", email="ct_s@example.com", password="pw123456!")
        self.course = Course.objects.create(title="CT Course", description="d", course_type=Course.CourseType.LIVE)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type=LiveBatch.BatchType.GROUP)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student)
        self.start = datetime.datetime(2026, 11, 20, 16, 0, tzinfo=UTC)
        self.lc = self._class(self.start)

    def _class(self, start, status=LiveClass.ClassStatus.COMPLETED):
        return LiveClass.objects.create(
            batch=self.batch, course=self.course, instructor=self.teacher, title="Session",
            meeting_url="https://zoom.us/j/555", meeting_provider=LiveClass.MeetingProvider.ZOOM,
            zoom_meeting_id="555", scheduled_start=start, duration_minutes=60, status=status,
        )

    def _event(self, event_type, start, end=None, live_class=None, uuid=""):
        obj = {"id": 555, "uuid": uuid, "start_time": _z(start)}
        if end is not None:
            obj["end_time"] = _z(end)
        return ZoomWebhookEvent.objects.create(
            event_id=f"ct-{next(_ids)}", event_type=event_type,
            payload={"event": event_type, "payload": {"object": obj}},
            live_class=live_class or self.lc, meeting_uuid=uuid,
        )

    def test_no_events_returns_none(self):
        self.assertIsNone(get_conducted_time(self.lc))

    def test_meeting_ended_gives_actual_duration(self):
        self._event("meeting.ended", self.start + datetime.timedelta(minutes=3), self.start + datetime.timedelta(minutes=55))
        c = get_conducted_time(self.lc)
        self.assertEqual(c["minutes"], 52)
        self.assertEqual(c["segments"], 1)
        self.assertFalse(c["in_progress"])
        self.assertEqual(c["started_at"], (self.start + datetime.timedelta(minutes=3)).isoformat())
        self.assertEqual(c["ended_at"], (self.start + datetime.timedelta(minutes=55)).isoformat())

    def test_redelivery_is_not_double_counted_and_restart_is_added(self):
        s1, e1 = self.start, self.start + datetime.timedelta(minutes=30)
        self._event("meeting.ended", s1, e1)
        self._event("meeting.ended", s1, e1)  # Zoom redelivery
        self._event("meeting.ended", self.start + datetime.timedelta(minutes=35), self.start + datetime.timedelta(minutes=60))
        c = get_conducted_time(self.lc)
        self.assertEqual(c["minutes"], 55)
        self.assertEqual(c["segments"], 2)

    def test_other_occurrence_of_recurring_meeting_is_ignored(self):
        next_week = self._class(self.start + datetime.timedelta(days=7), status=LiveClass.ClassStatus.SCHEDULED)
        # Event for THIS week's meeting, but attached by the webhook to next week's class.
        self._event("meeting.ended", self.start, self.start + datetime.timedelta(minutes=40), live_class=next_week)
        # Event for next week's meeting.
        self._event("meeting.ended", next_week.scheduled_start, next_week.scheduled_start + datetime.timedelta(minutes=20), live_class=next_week)
        self.assertEqual(get_conducted_time(self.lc)["minutes"], 40)
        self.assertEqual(get_conducted_time(next_week)["minutes"], 20)

    def test_started_without_end(self):
        self._event("meeting.started", self.start)
        c = get_conducted_time(self.lc)  # COMPLETED: end event was missed
        self.assertIsNone(c["minutes"])
        self.assertFalse(c["in_progress"])
        self.assertIsNone(c["ended_at"])

        self.lc.status = LiveClass.ClassStatus.LIVE
        self.lc.save(update_fields=["status"])
        c = get_conducted_time(self.lc, now=self.start + datetime.timedelta(minutes=25))
        self.assertTrue(c["in_progress"])
        self.assertEqual(c["minutes"], 25)

    @override_settings(ENABLE_ZOOM_ATTENDANCE_V2=False)
    def test_live_attendance_endpoint_reports_conducted_and_flag(self):
        self._event("meeting.ended", self.start, self.start + datetime.timedelta(minutes=45))
        client = APIClient()
        client.force_authenticate(self.admin)
        res = client.get(f"/api/courses/live-classes/{self.lc.id}/attendance/live/")
        self.assertEqual(res.status_code, 200, res.content)
        data = res.json()
        self.assertEqual(data["conducted"]["minutes"], 45)
        self.assertIs(data["zoom_attendance_enabled"], False)

    @override_settings(ENABLE_ZOOM_ATTENDANCE_V2=True)
    def test_live_attendance_endpoint_without_events(self):
        client = APIClient()
        client.force_authenticate(self.admin)
        data = client.get(f"/api/courses/live-classes/{self.lc.id}/attendance/live/").json()
        self.assertIsNone(data["conducted"])
        self.assertIs(data["zoom_attendance_enabled"], True)
