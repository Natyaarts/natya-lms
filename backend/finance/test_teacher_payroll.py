"""
Teacher salary: rate cards, monthly statements from conducted live classes,
review corrections and the DRAFT -> APPROVED -> PAID flow.
"""
import datetime
import itertools
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Attendance, Course, LiveBatch, LiveBatchStudent, LiveClass, ZoomWebhookEvent
from finance import teacher_payroll as payroll
from finance.models import TeacherPayrollStatement, TeacherRateCard, TeacherRateTier
from users.models import AdminAuditLog

User = get_user_model()
UTC = datetime.timezone.utc
_ids = itertools.count(1)


def _z(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _user(uid, username, **extra):
    # Explicit ids: auto ids would collide with the protected/excluded ids (1, 25, 39, 96).
    return User.objects.create_user(id=uid, username=username, email=f"{username}@example.test", password="x", **extra)


class TeacherPayrollTestBase(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(id=5001, username="pay_admin", email="pay_admin@example.test", password="x")
        self.teacher = _user(5002, "pay_teacher", first_name="Meera", is_teacher=True, is_student=False)
        self.sub = _user(5003, "pay_sub", is_teacher=True, is_student=False)
        self.students = [_user(5100 + i, f"pay_student_{i}") for i in range(8)]
        self.staff = _user(5009, "pay_staff", is_staff=True, is_student=False)
        self.course = Course.objects.create(title="Bharatanatyam", course_type=Course.CourseType.LIVE, is_published=True, price=0)
        self.course2 = Course.objects.create(title="Kuchipudi", course_type=Course.CourseType.LIVE, is_published=True, price=0)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.teacher, batch_type=LiveBatch.BatchType.GROUP)
        self.batch2 = LiveBatch.objects.create(course=self.course2, instructor=self.teacher, batch_type=LiveBatch.BatchType.GROUP)
        for s in self.students:
            LiveBatchStudent.objects.create(batch=self.batch, student=s)
        self.nov = datetime.datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
        self.client.force_authenticate(self.admin)

    # -- fixtures
    def card(self, teacher=None, course=None, per_class="0", per_student="0", tiers=((1, 1, "500"), (2, 5, "800"), (6, None, "1000"))):
        card = TeacherRateCard.objects.create(teacher=teacher or self.teacher, course=course,
                                              per_class_amount=Decimal(per_class), per_student_amount=Decimal(per_student))
        for low, high, rate in tiers:
            TeacherRateTier.objects.create(rate_card=card, min_students=low, max_students=high, hourly_rate=Decimal(rate))
        return card

    def live_class(self, start=None, batch=None, status_=LiveClass.ClassStatus.COMPLETED, duration=60, zoom=None,
                   present=0, late=0, absent=0, substitute=None):
        batch = batch or self.batch
        start = start or self.nov
        lc = LiveClass.objects.create(
            batch=batch, course=batch.course, instructor=batch.instructor, title=f"Class {next(_ids)}",
            meeting_url="https://zoom.us/j/777", meeting_provider=LiveClass.MeetingProvider.ZOOM, zoom_meeting_id="777",
            scheduled_start=start, duration_minutes=duration, status=status_, substitute_instructor=substitute,
        )
        if zoom is not None:
            obj = {"id": 777, "uuid": "", "start_time": _z(start), "end_time": _z(start + datetime.timedelta(minutes=zoom))}
            ZoomWebhookEvent.objects.create(event_id=f"pay-{next(_ids)}", event_type="meeting.ended",
                                            payload={"event": "meeting.ended", "payload": {"object": obj}}, live_class=lc)
        pool = iter(self.students)
        for st, n in (("PRESENT", present), ("LATE", late), ("ABSENT", absent)):
            for _ in range(n):
                Attendance.objects.create(live_class=lc, student=next(pool), status=st)
        return lc

    def generate(self, month="2026-11", teacher_ids=None):
        body = {"month": month}
        if teacher_ids is not None:
            body["teacher_ids"] = teacher_ids
        return self.client.post(reverse("teacher-salary-generate"), body, format="json")

    def statement(self, teacher=None, month_start=datetime.date(2026, 11, 1)):
        return TeacherPayrollStatement.objects.exclude(status="CANCELLED").get(teacher=teacher or self.teacher, period_start=month_start)

    def detail(self, statement):
        return self.client.get(reverse("teacher-salary-statement", kwargs={"pk": statement.pk})).data


class CalculationTests(TeacherPayrollTestBase):
    def test_full_formula_tier_by_students_capped_zoom_time(self):
        self.card(per_class="100", per_student="20")
        lc = self.live_class(zoom=52, present=3, late=1, absent=2)
        # Teacher's own and a staff attendance row must never be counted as students.
        Attendance.objects.create(live_class=lc, student=self.teacher, status="PRESENT")
        Attendance.objects.create(live_class=lc, student=self.staff, status="PRESENT")
        self.assertEqual(self.generate().status_code, 200)
        line = self.detail(self.statement())["lines"][0]
        self.assertEqual(line["students_counted"], 4)          # 3 present + 1 late
        self.assertEqual(line["tier_label"], "2-5 students")
        self.assertEqual(line["hourly_rate"], "800.00")
        self.assertEqual(line["paid_minutes"], 52)
        self.assertEqual(line["hours_amount"], "693.33")       # 800 x 52/60
        self.assertEqual(line["class_amount"], "100.00")
        self.assertEqual(line["student_amount"], "80.00")      # 4 x 20
        self.assertEqual(line["amount"], "873.33")
        self.assertIn(payroll.ATTENDANCE_NOT_FINALIZED, line["flags"])
        st = self.statement()
        self.assertEqual((st.class_count, st.total_minutes, st.net_amount), (1, 52, Decimal("873.33")))

    def test_zoom_time_is_capped_at_schedule(self):
        self.card()
        self.live_class(zoom=75, present=1)
        self.generate()
        line = self.detail(self.statement())["lines"][0]
        self.assertEqual(line["paid_minutes"], 60)
        self.assertIn(payroll.ZOOM_TIME_CAPPED, line["flags"])
        self.assertEqual(line["amount"], "500.00")

    def test_missing_zoom_time_blocks_until_admin_enters_minutes(self):
        self.card()
        self.live_class(zoom=None, present=1)
        self.generate()
        st = self.statement()
        line = self.detail(st)["lines"][0]
        self.assertTrue(line["blocking"])
        self.assertEqual(line["amount"], "0.00")
        self.assertEqual(self.client.post(reverse("teacher-salary-approve", kwargs={"pk": st.pk})).status_code, 400)
        res = self.client.patch(reverse("teacher-salary-line", kwargs={"pk": line["id"]}),
                                {"minutes_override": 45, "admin_note": "Zoom webhook missed"}, format="json")
        self.assertEqual(res.status_code, 200)
        fixed = res.data["lines"][0]
        self.assertEqual((fixed["paid_minutes"], fixed["amount"], fixed["blocking"]), (45, "375.00", False))
        self.assertEqual(self.client.post(reverse("teacher-salary-approve", kwargs={"pk": st.pk})).status_code, 200)

    def test_course_card_overrides_default_and_missing_card_blocks(self):
        self.card(tiers=((1, None, "400"),))                             # default
        self.card(course=self.course2, tiers=((1, None, "900"),))       # Kuchipudi
        self.live_class(zoom=60, present=1)
        self.live_class(batch=self.batch2, zoom=60, present=0)
        self.generate()
        lines = {ln["course_title"]: ln for ln in self.detail(self.statement())["lines"]}
        self.assertEqual(lines["Bharatanatyam"]["hourly_rate"], "400.00")
        # 0 students and the only tier starts at 1 -> needs review.
        self.assertIn(payroll.NO_TIER_FOR_STUDENTS, lines["Kuchipudi"]["flags"])
        other = _user(5004, "pay_other_teacher", is_teacher=True, is_student=False)
        batch = LiveBatch.objects.create(course=self.course, instructor=other, batch_type=LiveBatch.BatchType.ONE_TO_ONE)
        self.live_class(batch=batch, zoom=30)
        self.generate(teacher_ids=[other.pk])
        self.assertIn(payroll.NO_RATE_CARD, self.detail(self.statement(other))["lines"][0]["flags"])

    def test_substitute_is_paid_not_primary(self):
        self.card(teacher=self.sub, tiers=((0, None, "600"),))
        self.card()
        self.live_class(zoom=60, present=2, substitute=self.sub)
        self.live_class(zoom=60, present=2)
        res = self.generate()
        by_teacher = {r["teacher_id"]: r for r in res.data["results"]}
        self.assertEqual(set(by_teacher), {self.teacher.pk, self.sub.pk})
        sub_line = self.detail(self.statement(self.sub))["lines"][0]
        self.assertTrue(sub_line["is_substitute"])
        self.assertEqual(sub_line["amount"], "600.00")
        self.assertEqual(len(self.detail(self.statement())["lines"]), 1)

    def test_only_completed_classes_inside_the_ist_month(self):
        self.card()
        self.live_class(zoom=60, present=1)                                                      # counted
        self.live_class(zoom=60, present=1, status_=LiveClass.ClassStatus.CANCELLED)
        self.live_class(zoom=60, present=1, status_=LiveClass.ClassStatus.SCHEDULED)
        # 30 Nov 20:00 UTC = 1 Dec 01:30 IST -> December, not November.
        self.live_class(start=datetime.datetime(2026, 11, 30, 20, 0, tzinfo=UTC), zoom=60, present=1)
        # 31 Oct 19:00 UTC = 1 Nov 00:30 IST -> November.
        self.live_class(start=datetime.datetime(2026, 10, 31, 19, 0, tzinfo=UTC), zoom=60, present=1)
        self.generate()
        self.assertEqual(self.statement().class_count, 2)

    def test_protected_accounts_are_never_paid(self):
        protected = _user(25, "pay_protected", is_teacher=True, is_student=False)
        batch = LiveBatch.objects.create(course=self.course, instructor=protected, batch_type=LiveBatch.BatchType.GROUP)
        self.live_class(batch=batch, zoom=60, present=1)
        self.assertNotIn(25, payroll.teachers_with_classes(2026, 11))
        res = self.generate(teacher_ids=[25])
        self.assertEqual(res.data["results"][0]["result"], "skipped")
        self.assertFalse(TeacherPayrollStatement.objects.filter(teacher_id=25).exists())


class WorkflowTests(TeacherPayrollTestBase):
    def test_regenerate_keeps_corrections_and_picks_up_new_rates(self):
        card = self.card(tiers=((1, None, "500"),))
        self.live_class(zoom=60, present=1)
        self.generate()
        st = self.statement()
        line_id = self.detail(st)["lines"][0]["id"]
        self.client.patch(reverse("teacher-salary-line", kwargs={"pk": line_id}), {"attendee_count_override": 3}, format="json")
        card.tiers.update(hourly_rate=Decimal("700"))
        res = self.client.post(reverse("teacher-salary-regenerate", kwargs={"pk": st.pk}))
        self.assertEqual(res.status_code, 200)
        line = res.data["lines"][0]
        self.assertEqual((line["attendee_count_override"], line["students_counted"], line["amount"]), (3, 3, "700.00"))
        self.assertIn(payroll.STUDENTS_OVERRIDDEN, line["flags"])

    def test_exclude_line_and_adjustment(self):
        self.card(tiers=((1, None, "600"),))
        self.live_class(zoom=60, present=1)
        self.live_class(zoom=30, present=1)
        self.generate()
        st = self.statement()
        lines = self.detail(st)["lines"]
        self.client.patch(reverse("teacher-salary-line", kwargs={"pk": lines[1]["id"]}), {"is_excluded": True}, format="json")
        no_reason = self.client.patch(reverse("teacher-salary-statement", kwargs={"pk": st.pk}), {"adjustment_amount": "250"}, format="json")
        self.assertEqual(no_reason.status_code, 400)
        res = self.client.patch(reverse("teacher-salary-statement", kwargs={"pk": st.pk}),
                                {"adjustment_amount": "-100", "adjustment_reason": "Late start deduction"}, format="json")
        self.assertEqual(res.status_code, 200)
        self.assertEqual((res.data["class_count"], res.data["classes_amount"], res.data["net_amount"]), (1, "600.00", "500.00"))

    def test_approve_pay_and_lock(self):
        self.card(tiers=((1, None, "500"),))
        self.live_class(zoom=60, present=1)
        self.generate()
        st = self.statement()
        url = lambda name: reverse(name, kwargs={"pk": st.pk})
        self.assertEqual(self.client.post(url("teacher-salary-mark-paid")).status_code, 400)  # not approved yet
        self.assertEqual(self.client.post(url("teacher-salary-approve")).data["status"], "APPROVED")
        # Approved statements are locked: no corrections, no regeneration, no re-generation of the month.
        line_id = self.detail(st)["lines"][0]["id"]
        self.assertEqual(self.client.patch(reverse("teacher-salary-line", kwargs={"pk": line_id}), {"minutes_override": 10}, format="json").status_code, 400)
        self.assertEqual(self.client.post(url("teacher-salary-regenerate")).status_code, 400)
        self.assertEqual(self.generate().data["results"][0]["result"], "skipped")
        res = self.client.post(url("teacher-salary-mark-paid"), {"method": "UPI", "reference_number": "UTR123"}, format="json")
        self.assertEqual((res.data["status"], res.data["method"], res.data["reference_number"]), ("PAID", "UPI", "UTR123"))
        self.assertEqual(self.client.post(url("teacher-salary-cancel")).status_code, 400)
        actions = set(AdminAuditLog.objects.filter(target_type="TeacherPayrollStatement").values_list("action", flat=True))
        self.assertTrue({"TEACHER_PAYROLL_GENERATED", "TEACHER_PAYROLL_APPROVED", "TEACHER_PAYROLL_PAID"} <= actions)

    def test_rate_change_never_alters_approved_statement(self):
        card = self.card(tiers=((1, None, "500"),))
        self.live_class(zoom=60, present=1)
        self.generate()
        st = self.statement()
        self.client.post(reverse("teacher-salary-approve", kwargs={"pk": st.pk}))
        card.tiers.update(hourly_rate=Decimal("9999"))
        card.delete()
        st.refresh_from_db()
        self.assertEqual(st.net_amount, Decimal("500.00"))
        self.assertEqual(self.detail(st)["lines"][0]["amount"], "500.00")

    def test_class_is_never_paid_twice_and_cancel_releases_it(self):
        self.card(tiers=((1, None, "500"),))
        self.live_class(zoom=60, present=1)
        self.generate()
        st = self.statement()
        self.client.post(reverse("teacher-salary-approve", kwargs={"pk": st.pk}))
        self.client.post(reverse("teacher-salary-cancel", kwargs={"pk": st.pk}), {"reason": "Wrong month"}, format="json")
        self.assertEqual(self.generate().data["results"][0]["result"], "created")
        self.assertEqual(self.statement().class_count, 1)
        self.assertEqual(TeacherPayrollStatement.objects.filter(teacher=self.teacher).count(), 2)

    def test_zero_net_cannot_be_approved(self):
        self.card(tiers=((1, None, "500"),))
        self.live_class(zoom=60, present=1)
        self.generate()
        st = self.statement()
        line_id = self.detail(st)["lines"][0]["id"]
        self.client.patch(reverse("teacher-salary-line", kwargs={"pk": line_id}), {"is_excluded": True}, format="json")
        self.assertEqual(self.client.post(reverse("teacher-salary-approve", kwargs={"pk": st.pk})).status_code, 400)

    def test_no_classes_gives_clear_message(self):
        res = self.generate(teacher_ids=[self.teacher.pk])
        self.assertEqual(res.data["results"][0]["result"], "skipped")
        self.assertIn("No completed classes", res.data["results"][0]["reason"])


class RateCardApiTests(TeacherPayrollTestBase):
    url = staticmethod(lambda: reverse("teacher-salary-rate-cards"))

    def post(self, **body):
        data = {"teacher_id": self.teacher.pk, "course_id": None, "per_class_amount": "50", "per_student_amount": "10",
                "tiers": [{"min_students": 1, "max_students": 1, "hourly_rate": "500"},
                          {"min_students": 2, "max_students": None, "hourly_rate": "800"}]}
        data.update(body)
        return self.client.post(self.url(), data, format="json")

    def test_create_update_and_list(self):
        res = self.post()
        self.assertEqual(res.status_code, 201, res.data)
        self.assertEqual(len(res.data["tiers"]), 2)
        upd = self.client.patch(reverse("teacher-salary-rate-card", kwargs={"pk": res.data["id"]}),
                                {"tiers": [{"min_students": 0, "max_students": None, "hourly_rate": "650"}]}, format="json")
        self.assertEqual(upd.status_code, 200, upd.data)
        self.assertEqual(upd.data["tiers"], [{"min_students": 0, "max_students": None, "hourly_rate": "650.00"}])
        self.assertEqual(len(self.client.get(self.url(), {"teacher_id": self.teacher.pk}).data), 1)
        self.assertTrue(AdminAuditLog.objects.filter(action="TEACHER_RATE_CARD_UPDATED").exists())

    def test_invalid_tiers_rejected(self):
        overlap = [{"min_students": 1, "max_students": 5, "hourly_rate": "500"}, {"min_students": 5, "max_students": None, "hourly_rate": "800"}]
        open_middle = [{"min_students": 1, "max_students": None, "hourly_rate": "500"}, {"min_students": 6, "max_students": 9, "hourly_rate": "800"}]
        backwards = [{"min_students": 5, "max_students": 2, "hourly_rate": "500"}]
        negative = [{"min_students": 1, "max_students": None, "hourly_rate": "-1"}]
        for tiers in (overlap, open_middle, backwards, negative):
            self.assertEqual(self.post(tiers=tiers).status_code, 400, tiers)

    def test_duplicates_and_non_teachers_rejected(self):
        self.assertEqual(self.post().status_code, 201)
        self.assertEqual(self.post().status_code, 400)                       # second default card
        self.assertEqual(self.post(course_id=self.course.pk).status_code, 201)
        self.assertEqual(self.post(course_id=self.course.pk).status_code, 400)
        self.assertEqual(self.post(teacher_id=self.students[0].pk).status_code, 400)
        protected = _user(96, "pay_protected_96", is_teacher=True, is_student=False)
        self.assertEqual(self.post(teacher_id=protected.pk).status_code, 400)

    def test_admin_only(self):
        for user in (self.teacher, self.students[0]):
            self.client.force_authenticate(user)
            for name in ("teacher-salary-rate-cards", "teacher-salary-statements", "teacher-salary-teachers"):
                self.assertEqual(self.client.get(reverse(name)).status_code, status.HTTP_403_FORBIDDEN)
            self.assertEqual(self.generate().status_code, status.HTTP_403_FORBIDDEN)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(reverse("teacher-salary-statements")).status_code, (401, 403))

    def test_teachers_endpoint_lists_payable_teachers_only(self):
        _user(39, "pay_protected_39", is_teacher=True, is_student=False)
        data = self.client.get(reverse("teacher-salary-teachers")).data
        ids = {t["id"] for t in data["teachers"]}
        self.assertTrue({self.teacher.pk, self.sub.pk} <= ids)
        self.assertNotIn(39, ids)
        self.assertNotIn(self.students[0].pk, ids)
        self.assertTrue(any(c["title"] == "Kuchipudi" for c in data["courses"]))
