from datetime import timedelta
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from courses.models import (
    Course,
    Enrollment,
    LiveBatch,
    LiveBatchStudent,
    LiveClass,
)

User = get_user_model()


class TeacherHostVisibilityAndCourseStudentsTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="admin_u",
            email="admin_u@example.com",
            password="pw",
            is_staff=True,
            is_superuser=True,
        )
        self.teacher = User.objects.create_user(
            username="teacher_u",
            email="teacher_u@example.com",
            password="pw",
            is_teacher=True,
            first_name="Guru",
            last_name="Teacher",
        )
        self.student_1 = User.objects.create_user(
            username="student_1",
            email="s1@example.com",
            password="pw",
            is_student=True,
            first_name="Ananya",
            last_name="Rao",
            phone_number="+919800000001",
        )
        self.student_2 = User.objects.create_user(
            username="student_2",
            email="s2@example.com",
            password="pw",
            is_student=True,
            first_name="Meera",
            last_name="Nair",
            phone_number="+919800000002",
        )
        self.student_other_course = User.objects.create_user(
            username="student_3",
            email="s3@example.com",
            password="pw",
            is_student=True,
            first_name="Kiran",
            last_name="Das",
            phone_number="+919800000003",
        )
        self.student_unassigned = User.objects.create_user(
            username="student_4",
            email="s4@example.com",
            password="pw",
            is_student=True,
            first_name="Unassigned",
            last_name="User",
            phone_number="+919800000004",
        )

        self.course_a = Course.objects.create(
            title="Bharatanatyam Foundation",
            course_type=Course.CourseType.LIVE,
            is_published=True,
        )
        self.course_b = Course.objects.create(
            title="Carnatic Vocal",
            course_type=Course.CourseType.RECORDED,
            is_published=True,
        )

        self.batch_a1 = LiveBatch.objects.create(
            course=self.course_a,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP,
            max_participants=20,
        )
        self.batch_a2 = LiveBatch.objects.create(
            course=self.course_a,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP,
            max_participants=20,
        )
        self.batch_b1 = LiveBatch.objects.create(
            course=self.course_b,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP,
            max_participants=20,
        )

        # Student 1 is associated with Course A via BOTH Enrollment and Batch A1 (must deduplicate)
        Enrollment.objects.create(user=self.student_1, course=self.course_a)
        LiveBatchStudent.objects.create(batch=self.batch_a1, student=self.student_1)

        # Student 2 is associated with Course A via Batch A2 only
        LiveBatchStudent.objects.create(batch=self.batch_a2, student=self.student_2)

        # Student 3 is enrolled in Course B only
        Enrollment.objects.create(user=self.student_other_course, course=self.course_b)

        self.live_class = LiveClass.objects.create(
            course=self.course_a,
            batch=self.batch_a1,
            instructor=self.teacher,
            title="Morning Adavu Session",
            scheduled_start=timezone.now() + timedelta(minutes=5),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            status=LiveClass.ClassStatus.LIVE,
            zoom_meeting_id="91112223334",
            meeting_url="https://zoom.us/j/91112223334",
            host_url="https://zoom.us/s/91112223334?zak=admin_only",
        )

    def test_teacher_cannot_see_host_url_but_admin_can_and_joining_still_works(self):
        # Teacher list/detail must NOT expose host_url
        self.client.force_authenticate(user=self.teacher)
        list_resp = self.client.get("/api/courses/live-classes/")
        self.assertEqual(list_resp.status_code, 200)
        rows = list_resp.data if isinstance(list_resp.data, list) else list_resp.data.get("results", [])
        self.assertEqual(len(rows), 1)
        self.assertNotIn("host_url", rows[0])
        self.assertEqual(rows[0]["meeting_url"], "https://zoom.us/j/91112223334")

        detail_resp = self.client.get(f"/api/courses/live-classes/{self.live_class.id}/")
        self.assertEqual(detail_resp.status_code, 200)
        self.assertNotIn("host_url", detail_resp.data)

        # Teacher join endpoint still works
        join_resp = self.client.post(f"/api/courses/live-classes/{self.live_class.id}/join/")
        self.assertEqual(join_resp.status_code, 200)
        self.assertEqual(join_resp.data["status"], "host_or_staff")
        self.assertTrue(join_resp.data["meeting_url"])

        # Assigned student join endpoint still works
        self.client.force_authenticate(user=self.student_1)
        student_join = self.client.post(f"/api/courses/live-classes/{self.live_class.id}/join/")
        self.assertEqual(student_join.status_code, 200)
        self.assertEqual(student_join.data["status"], "joined")

        # Admin retains host_url access
        self.client.force_authenticate(user=self.admin)
        admin_resp = self.client.get(f"/api/courses/live-classes/{self.live_class.id}/")
        self.assertEqual(admin_resp.status_code, 200)
        self.assertEqual(admin_resp.data["host_url"], "https://zoom.us/s/91112223334?zak=admin_only")

    def test_manage_course_students_filters_by_course_and_batch_and_deduplicates(self):
        self.client.force_authenticate(user=self.admin)

        # Course A: returns Student 1 (deduplicated across Enrollment + Batch A1) and Student 2 (Batch A2)
        resp_a = self.client.get(f"/api/courses/{self.course_a.id}/students/")
        self.assertEqual(resp_a.status_code, 200)
        self.assertEqual(resp_a.data["course_id"], self.course_a.id)
        self.assertEqual(resp_a.data["count"], 2)
        ids_a = {s["id"] for s in resp_a.data["students"]}
        self.assertEqual(ids_a, {self.student_1.id, self.student_2.id})
        self.assertNotIn(self.student_other_course.id, ids_a)
        self.assertNotIn(self.student_unassigned.id, ids_a)

        s1_row = next(s for s in resp_a.data["students"] if s["id"] == self.student_1.id)
        self.assertEqual(s1_row["name"], "Ananya Rao")
        self.assertEqual(s1_row["email"], "s1@example.com")
        self.assertEqual(s1_row["phone_number"], "+919800000001")
        self.assertEqual(set(s1_row["assignment_sources"]), {"COURSE_ENROLLMENT", "LIVE_BATCH"})

        # Batch A2 filter within Course A: returns only Student 2
        resp_a2 = self.client.get(f"/api/courses/{self.course_a.id}/students/?batch_id={self.batch_a2.id}")
        self.assertEqual(resp_a2.status_code, 200)
        self.assertEqual(resp_a2.data["count"], 1)
        self.assertEqual([s["id"] for s in resp_a2.data["students"]], [self.student_2.id])

        # Batch from Course B queried under Course A -> 404
        resp_cross = self.client.get(f"/api/courses/{self.course_a.id}/students/?batch_id={self.batch_b1.id}")
        self.assertEqual(resp_cross.status_code, 404)

        # Course B: returns only Student 3
        resp_b = self.client.get(f"/api/courses/{self.course_b.id}/students/")
        self.assertEqual(resp_b.status_code, 200)
        self.assertEqual(resp_b.data["count"], 1)
        self.assertEqual([s["id"] for s in resp_b.data["students"]], [self.student_other_course.id])

        # Regular student cannot access course students endpoint
        self.client.force_authenticate(user=self.student_1)
        resp_forbidden = self.client.get(f"/api/courses/{self.course_a.id}/students/")
        self.assertEqual(resp_forbidden.status_code, 403)

