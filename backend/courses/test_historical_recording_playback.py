"""
Historical recordings: batch-isolated playback and subtitles.

Student A (Batch A) and Student B (Batch B) are in the same course. Each may
list, play and load subtitles for their own batch's recording only. The
subtitle endpoint shares the playback endpoint's authorization exactly.
No S3 access: the S3 client is mocked.
"""
from unittest import mock

from botocore.exceptions import ClientError
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from courses.models import (
    Course,
    HistoricalRecordingClassMapping,
    HistoricalRecordingFile,
    HistoricalRecordingSession,
    HistoricalRecordingTeacher,
    LiveBatch,
    LiveBatchStudent,
)

User = get_user_model()
Status = HistoricalRecordingClassMapping.Status
BUCKET = "natya-wise-recordings"
S3_CLIENT = "courses.historical_recording_views.HistoricalRecordingSessionListView.get_s3_client"


def _user(uid, username, **extra):
    # Explicit ids: auto ids would collide with protected ids (1, 25, 39, 96).
    return User.objects.create_user(id=uid, username=username, email=f"{username}@example.test", password="x", **extra)


class _Body:
    def __init__(self, data):
        self.data = data

    def read(self, n=-1):
        return self.data if n is None or n < 0 else self.data[:n]


class BatchIsolatedPlaybackTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(id=2001, username="hr_admin", email="hr_admin@example.test", password="x")
        self.student_a = _user(2002, "student_a")
        self.student_b = _user(2003, "student_b")
        self.teacher_a = _user(2004, "teacher_a", is_teacher=True, is_student=False)
        self.teacher_none = _user(2005, "teacher_none", is_teacher=True, is_student=False)
        self.inactive = _user(2006, "inactive_a", is_active=False)

        self.course = Course.objects.create(title="Bharatanatyam", course_type=Course.CourseType.LIVE, is_published=True, price=0)
        self.batch_a = LiveBatch.objects.create(course=self.course, batch_type=LiveBatch.BatchType.GROUP)
        self.batch_b = LiveBatch.objects.create(course=self.course, batch_type=LiveBatch.BatchType.GROUP)
        LiveBatchStudent.objects.create(batch=self.batch_a, student=self.student_a)
        LiveBatchStudent.objects.create(batch=self.batch_b, student=self.student_b)
        LiveBatchStudent.objects.create(batch=self.batch_a, student=self.inactive)

        self.session_a = HistoricalRecordingSession.objects.create(course=self.course, source_session_id="SA", source_class_id="CLA", session_title="A")
        self.session_b = HistoricalRecordingSession.objects.create(course=self.course, source_session_id="SB", source_class_id="CLB", session_title="B")
        HistoricalRecordingClassMapping.objects.create(source_class_id="CLA", batch=self.batch_a, status=Status.CONFIRMED)
        HistoricalRecordingClassMapping.objects.create(source_class_id="CLB", batch=self.batch_b, status=Status.CONFIRMED)
        HistoricalRecordingTeacher.objects.create(session=self.session_a, teacher=self.teacher_a,
                                                  source=HistoricalRecordingTeacher.Source.SESSION_TEACHER)

        mk = lambda session, ftype, key, **kw: HistoricalRecordingFile.objects.create(
            session=session, file_type=ftype, s3_bucket=kw.pop("bucket", BUCKET), s3_key=key, is_verified=kw.pop("verified", True), **kw)
        self.mp4_a = mk(self.session_a, "MP4", "wise-recordings/a/a.mp4")
        self.vtt_a = mk(self.session_a, "VTT", "wise-recordings/a/a.vtt")
        self.mp4_b = mk(self.session_b, "MP4", "wise-recordings/b/b.mp4")
        self.vtt_b = mk(self.session_b, "VTT", "wise-recordings/b/b.vtt")

        self.s3 = mock.Mock()
        self.s3.generate_presigned_url.return_value = "https://signed.example/object"
        self.s3.get_object.return_value = {"Body": _Body(b"WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nNamaskaram\n")}
        patcher = mock.patch(S3_CLIENT, return_value=self.s3)
        patcher.start()
        self.addCleanup(patcher.stop)

    def get(self, user, url):
        if user is None:
            self.client.force_authenticate(None)
        else:
            self.client.force_authenticate(user)
        return self.client.get(url)

    def playback(self, user, f):
        return self.get(user, f"/api/courses/historical-recordings/files/{f.pk}/playback/")

    def subtitles(self, user, f):
        return self.get(user, f"/api/courses/historical-recordings/files/{f.pk}/subtitles/")

    # ---- batch isolation (the core requirement)
    def test_each_student_lists_only_their_batch(self):
        ids = lambda u: [r["id"] for r in self.get(u, "/api/courses/historical-recordings/").json()["results"]]
        self.assertEqual(ids(self.student_a), [self.session_a.pk])
        self.assertEqual(ids(self.student_b), [self.session_b.pk])

    def test_each_student_plays_only_their_batch(self):
        self.assertEqual(self.playback(self.student_a, self.mp4_a).status_code, 200)
        self.assertEqual(self.playback(self.student_a, self.mp4_b).status_code, 404)
        self.assertEqual(self.playback(self.student_b, self.mp4_b).status_code, 200)
        self.assertEqual(self.playback(self.student_b, self.mp4_a).status_code, 404)

    def test_each_student_gets_only_their_batch_subtitles(self):
        self.assertEqual(self.subtitles(self.student_a, self.mp4_a).status_code, 200)
        self.assertEqual(self.subtitles(self.student_a, self.mp4_b).status_code, 404)
        self.assertEqual(self.subtitles(self.student_a, self.vtt_b).status_code, 404)
        self.assertEqual(self.subtitles(self.student_b, self.mp4_b).status_code, 200)
        self.assertEqual(self.subtitles(self.student_b, self.mp4_a).status_code, 404)

    def test_removed_from_batch_loses_access_immediately(self):
        self.assertEqual(self.playback(self.student_a, self.mp4_a).status_code, 200)
        LiveBatchStudent.objects.filter(batch=self.batch_a, student=self.student_a).delete()
        self.assertEqual(self.playback(self.student_a, self.mp4_a).status_code, 404)
        self.assertEqual(self.subtitles(self.student_a, self.mp4_a).status_code, 404)

    def test_mapping_not_confirmed_grants_nothing(self):
        HistoricalRecordingClassMapping.objects.filter(source_class_id="CLA").update(status=Status.AMBIGUOUS, batch=None)
        self.assertEqual(self.playback(self.student_a, self.mp4_a).status_code, 404)
        self.assertEqual(self.subtitles(self.student_a, self.mp4_a).status_code, 404)

    # ---- subtitles endpoint behaviour
    def test_subtitles_returned_as_private_vtt_text(self):
        res = self.subtitles(self.student_a, self.mp4_a)
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res["Content-Type"].startswith("text/vtt"))
        self.assertIn("no-store", res["Cache-Control"])
        self.assertEqual(res["X-Content-Type-Options"], "nosniff")
        self.assertIn("Namaskaram", res.content.decode())
        self.s3.get_object.assert_called_once_with(Bucket=BUCKET, Key="wise-recordings/a/a.vtt")

    def test_subtitles_by_vtt_file_id_also_works(self):
        self.assertEqual(self.subtitles(self.student_a, self.vtt_a).status_code, 200)

    def test_subtitles_404_when_session_has_no_verified_vtt(self):
        self.vtt_a.is_verified = False
        self.vtt_a.save()
        self.assertEqual(self.subtitles(self.student_a, self.mp4_a).status_code, 404)
        self.s3.get_object.assert_not_called()

    def test_subtitle_outside_expected_bucket_is_never_served(self):
        self.vtt_a.s3_bucket = "some-other-bucket"
        self.vtt_a.save()
        self.assertEqual(self.subtitles(self.student_a, self.mp4_a).status_code, 404)
        self.assertFalse(self.playback(self.student_a, self.mp4_a).json()["has_subtitles"])

    def test_missing_webvtt_header_is_added(self):
        self.s3.get_object.return_value = {"Body": _Body(b"00:00:01.000 --> 00:00:02.000\nHi\n")}
        self.assertTrue(self.subtitles(self.student_a, self.mp4_a).content.decode().startswith("WEBVTT"))

    def test_oversized_subtitle_rejected(self):
        from courses.historical_recording_views import MAX_SUBTITLE_BYTES
        self.s3.get_object.return_value = {"Body": _Body(b"x" * (MAX_SUBTITLE_BYTES + 10))}
        self.assertEqual(self.subtitles(self.student_a, self.mp4_a).status_code, 500)

    def test_s3_error_is_reported_without_details(self):
        self.s3.get_object.side_effect = ClientError({"Error": {"Code": "AccessDenied", "Message": "secret detail"}}, "GetObject")
        res = self.subtitles(self.student_a, self.mp4_a)
        self.assertEqual(res.status_code, 500)
        self.assertNotIn("secret detail", res.content.decode())

    # ---- other roles
    def test_mapped_teacher_and_admin_allowed_unmapped_teacher_denied(self):
        self.assertEqual(self.subtitles(self.teacher_a, self.mp4_a).status_code, 200)
        self.assertEqual(self.subtitles(self.teacher_a, self.mp4_b).status_code, 404)
        self.assertEqual(self.subtitles(self.teacher_none, self.mp4_a).status_code, 404)
        self.assertEqual(self.subtitles(self.admin, self.mp4_b).status_code, 200)

    def test_inactive_and_anonymous_denied(self):
        self.assertEqual(self.subtitles(self.inactive, self.mp4_a).status_code, 404)
        self.assertEqual(self.playback(self.inactive, self.mp4_a).status_code, 404)
        self.assertIn(self.subtitles(None, self.mp4_a).status_code, (401, 403))
        self.assertIn(self.playback(None, self.mp4_a).status_code, (401, 403))

    # ---- playback contract used by web and Android
    def test_playback_reports_subtitles_and_15_minute_expiry(self):
        data = self.playback(self.student_a, self.mp4_a).json()
        self.assertTrue(data["has_subtitles"])
        self.assertEqual(data["expires_in"], 900)
        self.assertEqual(data["url"], "https://signed.example/object")
        for call in self.s3.generate_presigned_url.call_args_list:
            self.assertEqual(call.kwargs["ExpiresIn"], 900)
            self.assertEqual(call.kwargs["Params"]["Bucket"], BUCKET)
