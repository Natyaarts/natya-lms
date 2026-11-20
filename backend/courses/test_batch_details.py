"""
Admin "batch details" view support:
  * GET /live-batches/<id>/students/ returns student name / email / phone
    only to admins and the batch's own instructor or mentor -- students of
    the batch (who can also read this endpoint) get None for all three.
  * POST /live-batches/<id>/students/ (add student) returns the same fields.
  * GET /live-classes/?batch=<id> narrows to that batch; a non-numeric value
    returns nothing instead of a server error.
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from courses.models import Course, LiveBatch, LiveBatchStudent, LiveClass

User = get_user_model()

STUDENTS_URL = "/api/courses/live-batches/{}/students/"
CLASSES_URL = "/api/courses/live-classes/"


def _rows(data):
    return data if isinstance(data, list) else data.get("results", [])


class BatchDetailsApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(
            username="bd_admin", email="bd_admin@example.com", password="Password123!"
        )
        self.teacher = User.objects.create_user(
            username="bd_teacher", email="bd_teacher@example.com", password="Password123!", is_teacher=True
        )
        self.mentor = User.objects.create_user(
            username="bd_mentor", email="bd_mentor@example.com", password="Password123!", is_mentor=True
        )
        self.other_teacher = User.objects.create_user(
            username="bd_other_teacher", email="bd_other@example.com", password="Password123!", is_teacher=True
        )
        self.student = User.objects.create_user(
            username="bd_student", email="bd_student@example.com", password="Password123!",
            first_name="Asha", last_name="Kumar", phone_number="+919800000001",
        )
        self.classmate = User.objects.create_user(
            username="bd_classmate", email="bd_classmate@example.com", password="Password123!",
            first_name="Bala", last_name="Nair", phone_number="+919800000002",
        )
        self.newcomer = User.objects.create_user(
            username="bd_newcomer", email="bd_newcomer@example.com", password="Password123!",
            first_name="Chitra", last_name="Rao",
        )

        self.course = Course.objects.create(
            title="Batch Details Course", description="d", course_type=Course.CourseType.LIVE
        )
        self.batch = LiveBatch.objects.create(
            course=self.course, instructor=self.teacher, mentor=self.mentor,
            batch_type=LiveBatch.BatchType.GROUP, max_participants=20,
        )
        self.other_batch = LiveBatch.objects.create(
            course=self.course, instructor=self.other_teacher,
            batch_type=LiveBatch.BatchType.GROUP, max_participants=20,
        )
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.classmate)

        start = timezone.now() + timezone.timedelta(days=1)
        self.batch_class = LiveClass.objects.create(
            batch=self.batch, course=self.course, instructor=self.teacher, title="Batch session",
            meeting_url="https://zoom.us/j/1", scheduled_start=start, duration_minutes=60,
        )
        self.other_class = LiveClass.objects.create(
            batch=self.other_batch, course=self.course, instructor=self.other_teacher, title="Other session",
            meeting_url="https://zoom.us/j/2", scheduled_start=start, duration_minutes=60,
        )

    def _students(self, user):
        self.client.force_authenticate(user)
        res = self.client.get(STUDENTS_URL.format(self.batch.id))
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.content)
        return {r["student"]: r for r in _rows(res.json())}

    def test_admin_sees_names_and_contact_details(self):
        rows = self._students(self.admin)
        self.assertEqual(set(rows), {self.student.id, self.classmate.id})
        row = rows[self.student.id]
        self.assertEqual(row["student_name"], "Asha Kumar")
        self.assertEqual(row["student_email"], "bd_student@example.com")
        self.assertEqual(row["student_phone"], "+919800000001")
        self.assertEqual(row["student_username"], "bd_student")

    def test_batch_instructor_and_mentor_see_contact_details(self):
        for user in (self.teacher, self.mentor):
            row = self._students(user)[self.classmate.id]
            self.assertEqual(row["student_email"], "bd_classmate@example.com", user.username)
            self.assertEqual(row["student_phone"], "+919800000002", user.username)

    def test_student_of_batch_does_not_see_classmates_details(self):
        rows = self._students(self.student)
        for row in rows.values():
            self.assertIsNone(row["student_name"])
            self.assertIsNone(row["student_email"])
            self.assertIsNone(row["student_phone"])
            # Pre-existing field is unchanged.
            self.assertTrue(row["student_username"])

    def test_teacher_of_another_batch_cannot_read_roster(self):
        self.client.force_authenticate(self.other_teacher)
        res = self.client.get(STUDENTS_URL.format(self.batch.id))
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_add_student_returns_details_for_admin(self):
        self.client.force_authenticate(self.admin)
        res = self.client.post(STUDENTS_URL.format(self.batch.id), {"student_id": self.newcomer.id}, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.content)
        self.assertEqual(res.json()["student_name"], "Chitra Rao")
        self.assertEqual(res.json()["student_email"], "bd_newcomer@example.com")
        self.assertTrue(LiveBatchStudent.objects.filter(batch=self.batch, student=self.newcomer).exists())

    def test_live_classes_batch_filter(self):
        self.client.force_authenticate(self.admin)
        res = self.client.get(CLASSES_URL, {"batch": self.batch.id})
        self.assertEqual(res.status_code, status.HTTP_200_OK, res.content)
        self.assertEqual([r["id"] for r in _rows(res.json())], [self.batch_class.id])

        res = self.client.get(CLASSES_URL)
        ids = {r["id"] for r in _rows(res.json())}
        self.assertTrue({self.batch_class.id, self.other_class.id} <= ids)

    def test_live_classes_batch_filter_rejects_non_numeric(self):
        self.client.force_authenticate(self.admin)
        res = self.client.get(CLASSES_URL, {"batch": "abc"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(_rows(res.json()), [])
