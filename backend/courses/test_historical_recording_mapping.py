import csv
import os
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase
from rest_framework.test import APITestCase

from courses.historical_recording_access import visible_sessions
from courses.management.commands.map_historical_recordings import build_plan
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


def _user(uid, username, **extra):
    # Explicit ids: auto ids in a fresh test DB would collide with protected ids (1, 25, 39, 96).
    return User.objects.create_user(id=uid, username=username, email=f"{username}@example.test", password="x", **extra)


def _course(title):
    return Course.objects.create(title=title, course_type=Course.CourseType.LIVE, is_published=True, price=0)


def _batch(course):
    return LiveBatch.objects.create(course=course, batch_type=LiveBatch.BatchType.GROUP)


def _session(course, sid, cid, teacher_name="T"):
    return HistoricalRecordingSession.objects.create(
        course=course, source_session_id=sid, source_class_id=cid, teacher_name=teacher_name
    )


class AccessPolicyTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(id=1001, username="admin", email="a@example.test", password="x")
        self.s1 = _user(1002, "s1")
        self.s2 = _user(1003, "s2")
        self.teacher = _user(1004, "t1", is_teacher=True, is_student=False)
        self.teacher_ambiguous = _user(1005, "t2", is_teacher=True, is_student=False)
        self.teacher_unmapped = _user(1006, "t3", is_teacher=True, is_student=False)
        self.protected = _user(39, "protected", is_teacher=True, is_student=False)

        self.course = _course("C")
        self.other_course = _course("Other")
        self.b1, self.b2 = _batch(self.course), _batch(self.course)
        self.b_other = _batch(self.other_course)
        LiveBatchStudent.objects.create(batch=self.b1, student=self.s1)
        LiveBatchStudent.objects.create(batch=self.b2, student=self.s2)
        LiveBatchStudent.objects.create(batch=self.b_other, student=self.s2)

        self.confirmed = _session(self.course, "S1", "CLS1")
        self.ambiguous = _session(self.course, "S2", "CLS2")
        self.unmapped = _session(self.course, "S3", "CLS3")
        self.no_class = _session(self.course, "S4", "")
        self.wrong_course = _session(self.course, "S5", "CLS5")

        HistoricalRecordingClassMapping.objects.create(source_class_id="CLS1", batch=self.b1, status=Status.CONFIRMED)
        HistoricalRecordingClassMapping.objects.create(source_class_id="CLS2", status=Status.AMBIGUOUS)
        # Mapped batch belongs to another course than the session -> must not grant access.
        HistoricalRecordingClassMapping.objects.create(source_class_id="CLS5", batch=self.b_other, status=Status.CONFIRMED)

        src = HistoricalRecordingTeacher.Source.SESSION_TEACHER
        HistoricalRecordingTeacher.objects.create(session=self.confirmed, teacher=self.teacher, source=src)
        HistoricalRecordingTeacher.objects.create(session=self.ambiguous, teacher=self.teacher_ambiguous, source=src)
        HistoricalRecordingTeacher.objects.create(session=self.confirmed, teacher=self.protected, source=src)

        self.mp4 = HistoricalRecordingFile.objects.create(
            session=self.confirmed, file_type="MP4", s3_bucket="natya-wise-recordings",
            s3_key="wise-recordings/c/s1.mp4", is_verified=True,
        )
        self.mp4_unverified = HistoricalRecordingFile.objects.create(
            session=self.confirmed, file_type="MP4", s3_bucket="natya-wise-recordings",
            s3_key="wise-recordings/c/s1b.mp4", is_verified=False,
        )

    def ids(self, user):
        return set(visible_sessions(user).values_list("source_session_id", flat=True))

    def test_admin_sees_everything(self):
        self.assertEqual(self.ids(self.admin), {"S1", "S2", "S3", "S4", "S5"})

    def test_student_sees_only_confirmed_class_of_own_batch(self):
        self.assertEqual(self.ids(self.s1), {"S1"})
        # Same course, different batch; and a batch in another course: nothing.
        self.assertEqual(self.ids(self.s2), set())

    def test_teacher_sees_only_explicitly_mapped_confirmed_sessions(self):
        self.assertEqual(self.ids(self.teacher), {"S1"})
        self.assertEqual(self.ids(self.teacher_ambiguous), set())
        self.assertEqual(self.ids(self.teacher_unmapped), set())

    def test_protected_account_never_gets_teacher_access(self):
        self.assertEqual(self.ids(self.protected), set())

    def test_teacher_loses_access_when_demoted(self):
        self.teacher.is_teacher = False
        self.teacher.save()
        self.assertEqual(self.ids(self.teacher), set())

    def test_confirmed_requires_batch(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            HistoricalRecordingClassMapping.objects.create(source_class_id="X", status=Status.CONFIRMED)
        with self.assertRaises(IntegrityError), transaction.atomic():
            HistoricalRecordingClassMapping.objects.create(source_class_id="Y", batch=self.b1, status=Status.UNRESOLVED)

    def test_list_api_scopes(self):
        self.client.force_authenticate(self.s1)
        data = self.client.get("/api/courses/historical-recordings/").json()
        self.assertEqual([r["id"] for r in data["results"]], [self.confirmed.pk])
        self.assertEqual([f["id"] for f in data["results"][0]["files"]], [self.mp4.pk])
        self.assertFalse(data["access"]["mapping_pending"])

        self.client.force_authenticate(self.s2)
        data = self.client.get("/api/courses/historical-recordings/").json()
        self.assertEqual(data["results"], [])
        self.assertTrue(data["access"]["mapping_pending"])

    def test_playback_signs_fresh_15_minute_url_only_for_entitled_users(self):
        client = mock.Mock()
        client.generate_presigned_url.return_value = "https://signed.example/x"
        url = f"/api/courses/historical-recordings/files/{self.mp4.pk}/playback/"
        with mock.patch(
            "courses.historical_recording_views.HistoricalRecordingSessionListView.get_s3_client", return_value=client
        ):
            self.client.force_authenticate(self.s2)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.force_authenticate(self.s1)
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["expires_in"], 900)
            kwargs = client.generate_presigned_url.call_args.kwargs
            self.assertEqual(kwargs["ExpiresIn"], 900)
            self.assertEqual(kwargs["Params"]["Bucket"], "natya-wise-recordings")
            unverified = f"/api/courses/historical-recordings/files/{self.mp4_unverified.pk}/playback/"
            self.assertEqual(self.client.get(unverified).status_code, 404)


class MappingPlanTests(TestCase):
    def setUp(self):
        self.l1 = _user(2001, "l1")
        self.l2 = _user(2002, "l2", phone_number="9876543210")
        self.l3 = _user(2003, "l3")
        self.tu1 = _user(2101, "tu1", is_teacher=True, is_student=False)
        self.tu2 = _user(2102, "tu2", is_teacher=True, is_student=False)
        _user(39, "protected", is_teacher=True)

        self.c1, self.c2, self.c3 = _course("C1"), _course("C2"), _course("C3")
        self.b1, self.b2, self.b3 = _batch(self.c1), _batch(self.c2), _batch(self.c3)
        LiveBatchStudent.objects.create(batch=self.b1, student=self.l1)
        LiveBatchStudent.objects.create(batch=self.b1, student=self.l2)
        LiveBatchStudent.objects.create(batch=self.b2, student=self.l3)
        LiveBatchStudent.objects.create(batch=self.b3, student=self.l3)

        _session(self.c1, "sa1", "A")
        _session(self.c1, "sa2", "A")
        _session(self.c2, "sb1", "B")
        _session(self.c2, "sb2", "B")
        _session(self.c1, "sx1", "X")   # sessions span two courses
        _session(self.c2, "sx2", "X")
        _session(self.c3, "sn1", "N")   # no learners in source

        self.source_sessions = [
            {"class_id": "A", "session_id": "sa1", "teacher_user_id": "T1", "teacher_name": "Teacher One"},
            {"class_id": "A", "session_id": "sa2", "teacher_user_id": "T1", "teacher_name": "Teacher One"},
            {"class_id": "B", "session_id": "sb1", "teacher_user_id": "T2", "teacher_name": "Teacher Two"},
            {"class_id": "B", "session_id": "sb2", "teacher_user_id": "T1", "teacher_name": "Teacher One"},
        ]
        self.learners = [
            {"Course ID": "A", "Student ID": "x1", "Student Email": "L1@example.test", "Student Phone": ""},
            {"Course ID": "A", "Student ID": "x2", "Student Email": "", "Student Phone": "+91 98765 43210"},
            {"Course ID": "A", "Student ID": "x9", "Student Email": "nobody@example.test", "Student Phone": ""},
            {"Course ID": "B", "Student ID": "x3", "Student Email": "l3@example.test", "Student Phone": ""},
            {"Course ID": "X", "Student ID": "x1", "Student Email": "l1@example.test", "Student Phone": ""},
        ]
        self.audit = [
            {"batch_id": str(self.b1.pk), "course_id": str(self.c1.pk), "status": "MISSING_CONFIRMED",
             "expected_instructor_id": str(self.tu1.pk)},
        ]

    def plan(self, corrections=None, manual=None, audit=None, learners=None):
        return build_plan(self.source_sessions, learners or self.learners,
                          self.audit if audit is None else audit, corrections or {}, manual or {})

    def teachers(self, plan):
        return sorted((s["source_session_id"], uid, src) for s, uid, src, _ in plan.session_teachers)

    def test_class_to_batch_by_learner_identity(self):
        p = self.plan()
        self.assertEqual((p.classes["A"].status, p.classes["A"].batch_id), (Status.CONFIRMED, self.b1.pk))
        self.assertEqual((p.classes["A"].resolved_learners, p.classes["A"].matched_learners), (2, 2))
        self.assertEqual((p.classes["B"].status, p.classes["B"].batch_id), (Status.CONFIRMED, self.b2.pk))
        self.assertEqual(p.classes["X"].status, Status.AMBIGUOUS)
        self.assertEqual(p.classes["N"].status, Status.UNRESOLVED)

    def test_shared_batch_is_ambiguous(self):
        learners = self.learners + [{"Course ID": "N", "Student ID": "x3", "Student Email": "l3@example.test"}]
        LiveBatchStudent.objects.filter(batch=self.b3).delete()
        HistoricalRecordingSession.objects.filter(source_class_id="N").update(course=self.c2)
        p = self.plan(learners=learners)
        self.assertEqual(p.classes["B"].status, Status.AMBIGUOUS)
        self.assertEqual(p.classes["N"].status, Status.AMBIGUOUS)

    def test_session_teachers_from_verified_legacy_accounts(self):
        p = self.plan()
        self.assertEqual(p.teacher_accounts["T1"][:2], ("VERIFIED", self.tu1.pk))
        self.assertEqual(p.teacher_accounts["T2"][0], "UNRESOLVED")
        src = HistoricalRecordingTeacher.Source.SESSION_TEACHER
        self.assertEqual(self.teachers(p), [("sa1", self.tu1.pk, src), ("sa2", self.tu1.pk, src), ("sb2", self.tu1.pk, src)])

    def test_multiple_teachers_via_reviewed_account_rows(self):
        p = self.plan(manual={"T2": self.tu2.pk})
        got = {(s, u) for s, u, _ in self.teachers(p)}
        self.assertIn(("sb1", self.tu2.pk), got)
        self.assertIn(("sb2", self.tu1.pk), got)

    def test_correction_applies_to_class_or_fails_closed(self):
        p = self.plan(corrections={"B": ["Teacher One"]})
        corr = HistoricalRecordingTeacher.Source.CORRECTION
        self.assertEqual([t for t in self.teachers(p) if t[0].startswith("sb")],
                         [("sb1", self.tu1.pk, corr), ("sb2", self.tu1.pk, corr)])
        # Unverified legacy teacher, or a name not among the class's own teachers -> no teachers at all.
        for note in ("Teacher Two", "Somebody Else"):
            p = self.plan(corrections={"B": [note]})
            self.assertEqual([t for t in self.teachers(p) if t[0].startswith("sb")], [])
            self.assertEqual(p.classes["B"].teacher_status, "CORRECTION_UNRESOLVED")

    def test_blank_correction_decision_is_pending_not_approval(self):
        p = self.plan(corrections={"B": []})
        self.assertEqual(p.classes["B"].teacher_status, "CORRECTION_PENDING")
        self.assertEqual([t for t in self.teachers(p) if t[0].startswith("sb")], [])
        # A confirmed decision in another file for the same class id still applies.
        p = self.plan(corrections={"B": ["Teacher One"]})
        self.assertTrue(p.classes["B"].teacher_status.startswith("CORRECTION"))

    def test_correction_files_keyed_by_exact_class_id(self):
        from courses.management.commands.map_historical_recordings import read_corrections

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "c.csv")
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(["No", "Batch Name", "Class ID", "Current Teacher Names", "Correct Teacher Name(s) / Notes"])
                w.writerow(["1", "x", "B", "Teacher One, Teacher Two", ""])
                w.writerow(["2", "x", "A", "Teacher One", "Teacher One"])
                w.writerow(["3", "x", "", "Teacher One", "Teacher One"])
            self.assertEqual(read_corrections([path]), {"B": [], "A": ["Teacher One"]})

    def test_protected_and_conflicting_accounts_rejected(self):
        audit = [dict(self.audit[0], expected_instructor_id="39")]
        p = self.plan(audit=audit)
        self.assertEqual(p.teacher_accounts["T1"][0], "REJECTED")
        self.assertEqual(p.session_teachers, [])
        p = self.plan(manual={"T1": self.tu2.pk})
        self.assertEqual(p.teacher_accounts["T1"][0], "AMBIGUOUS")
        p = self.plan(manual={"T2": self.tu1.pk})
        self.assertEqual(p.teacher_accounts["T1"][0], "AMBIGUOUS")
        self.assertEqual(p.teacher_accounts["T2"][0], "AMBIGUOUS")

    def test_command_dry_run_then_commit(self):
        import openpyxl

        with tempfile.TemporaryDirectory() as tmp:
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "All Sessions"
            cols = ["class_id", "session_id", "teacher_user_id", "teacher_name"]
            ws.append(cols)
            for r in self.source_sessions:
                ws.append([r[c] for c in cols])
            ws2 = wb.create_sheet("Student Courses")
            cols2 = ["Course ID", "Student ID", "Student Email", "Student Phone"]
            ws2.append(cols2)
            for r in self.learners:
                ws2.append([r.get(c, "") for c in cols2])
            book = os.path.join(tmp, "src.xlsx")
            wb.save(book)
            audit = os.path.join(tmp, "audit.csv")
            with open(audit, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(self.audit[0]))
                w.writeheader()
                w.writerows(self.audit)
            out = os.path.join(tmp, "out")
            args = ["map_historical_recordings", "--workbook", book, "--livebatch-audit", audit, "--output-dir", out]

            _session(self.c1, "s-noclass", "")  # must still appear in the report, as unresolved
            with self.assertRaises(CommandError):
                call_command(*args, "--expect-sessions", "2192", stdout=open(os.devnull, "w"))
            call_command(*args, "--expect-sessions", "8", stdout=open(os.devnull, "w"))
            self.assertFalse(HistoricalRecordingClassMapping.objects.exists())
            self.assertTrue(os.path.exists(os.path.join(out, "class_mappings.csv")))
            with open(os.path.join(out, "session_mappings.csv"), encoding="utf-8") as fh:
                report = list(csv.DictReader(fh))
            self.assertEqual(len(report), HistoricalRecordingSession.objects.count())
            self.assertEqual(
                {r["source_session_id"] for r in report},
                set(HistoricalRecordingSession.objects.values_list("source_session_id", flat=True)),
            )
            by_id = {r["source_session_id"]: r for r in report}
            self.assertEqual(by_id["s-noclass"]["status"], Status.UNRESOLVED)
            self.assertEqual(by_id["sa1"]["status"], Status.CONFIRMED)

            call_command(*args, "--commit", stdout=open(os.devnull, "w"))
            self.assertEqual(
                HistoricalRecordingClassMapping.objects.get(source_class_id="A").batch_id, self.b1.pk
            )
            self.assertEqual(HistoricalRecordingClassMapping.objects.filter(status=Status.CONFIRMED).count(), 2)
            self.assertEqual(HistoricalRecordingTeacher.objects.count(), 3)
            self.assertEqual(set(visible_sessions(self.l1).values_list("source_session_id", flat=True)), {"sa1", "sa2"})
            self.assertEqual(set(visible_sessions(self.tu1).values_list("source_session_id", flat=True)), {"sa1", "sa2", "sb2"})
