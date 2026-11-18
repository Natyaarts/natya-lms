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


class TeacherBatchAndClassIsolationTests(APITestCase):
    def setUp(self):
        from courses.models import CourseInstructor

        self.superadmin = User.objects.create_user(
            username="superadmin_iso",
            email="superadmin_iso@example.com",
            password="pw",
            is_staff=True,
            is_superuser=True,
        )
        self.yathivandhya = User.objects.create_user(
            username="yathivandhya",
            email="yathivandhya@example.com",
            password="pw",
            is_teacher=True,
            first_name="Yathivandhya",
            last_name="K",
        )
        self.bhagyareshmi = User.objects.create_user(
            username="bhagyareshmi",
            email="bhagyareshmi@example.com",
            password="pw",
            is_teacher=True,
            first_name="Bhagyareshmi",
            last_name="R",
        )
        self.batch_mentor = User.objects.create_user(
            username="mentor_iso",
            email="mentor_iso@example.com",
            password="pw",
            is_mentor=True,
            first_name="Mentor",
            last_name="User",
        )

        self.shared_course = Course.objects.create(
            title="Shared Kuchipudi Course",
            course_type=Course.CourseType.LIVE,
            is_published=True,
        )
        # Both teachers assigned at course level
        CourseInstructor.objects.create(
            course=self.shared_course,
            user=self.yathivandhya,
            role=CourseInstructor.InstructorRole.TEACHER,
            is_primary=True,
        )
        CourseInstructor.objects.create(
            course=self.shared_course,
            user=self.bhagyareshmi,
            role=CourseInstructor.InstructorRole.TEACHER,
            is_primary=False,
        )

        self.batch_yathi = LiveBatch.objects.create(
            course=self.shared_course,
            instructor=self.yathivandhya,
            mentor=self.batch_mentor,
            batch_type=LiveBatch.BatchType.GROUP,
            max_participants=15,
        )
        self.batch_bhagya = LiveBatch.objects.create(
            course=self.shared_course,
            instructor=self.bhagyareshmi,
            batch_type=LiveBatch.BatchType.GROUP,
            max_participants=15,
        )
        self.batch_unassigned = LiveBatch.objects.create(
            course=self.shared_course,
            instructor=None,
            mentor=self.batch_mentor,
            batch_type=LiveBatch.BatchType.GROUP,
            max_participants=10,
        )

        base_start = timezone.now() + timedelta(days=2)
        self.lc_yathi_1 = LiveClass.objects.create(
            course=self.shared_course,
            batch=self.batch_yathi,
            instructor=self.yathivandhya,
            title="Yathi Regular Session",
            scheduled_start=base_start,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.OTHER,
            meeting_url="https://example.com/y1",
        )
        self.lc_yathi_sub = LiveClass.objects.create(
            course=self.shared_course,
            batch=self.batch_yathi,
            instructor=self.yathivandhya,
            substitute_instructor=self.bhagyareshmi,
            title="Yathi Substituted Session",
            scheduled_start=base_start + timedelta(hours=3),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.OTHER,
            meeting_url="https://example.com/y_sub",
        )
        self.lc_bhagya = LiveClass.objects.create(
            course=self.shared_course,
            batch=self.batch_bhagya,
            instructor=self.bhagyareshmi,
            title="Bhagya Regular Session",
            scheduled_start=base_start + timedelta(hours=6),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.OTHER,
            meeting_url="https://example.com/b1",
        )

    def _rows(self, resp):
        return resp.data if isinstance(resp.data, list) else resp.data.get("results", [])

    def test_shared_course_teachers_isolated_to_own_batches_and_classes(self):
        # Bhagyareshmi sees only her own batch (not Yathivandhya's batch or unassigned batch)
        self.client.force_authenticate(user=self.bhagyareshmi)
        b_resp = self.client.get("/api/courses/live-batches/")
        self.assertEqual(b_resp.status_code, 200)
        b_ids = {row["id"] for row in self._rows(b_resp)}
        self.assertEqual(b_ids, {self.batch_bhagya.id})

        # Direct detail request for Yathivandhya's batch returns 404
        self.assertEqual(self.client.get(f"/api/courses/live-batches/{self.batch_yathi.id}/").status_code, 404)

        # Bhagyareshmi sees her own class + the specific class where she is substitute_instructor
        c_resp = self.client.get("/api/courses/live-classes/")
        self.assertEqual(c_resp.status_code, 200)
        c_ids = {row["id"] for row in self._rows(c_resp)}
        self.assertEqual(c_ids, {self.lc_bhagya.id, self.lc_yathi_sub.id})
        self.assertNotIn(self.lc_yathi_1.id, c_ids)

        # Direct detail request for Yathivandhya's non-substituted class returns 404
        self.assertEqual(self.client.get(f"/api/courses/live-classes/{self.lc_yathi_1.id}/").status_code, 404)

        # Substitute instructor can access, start, and PATCH metadata ONLY on the specifically assigned class
        sub_detail = self.client.get(f"/api/courses/live-classes/{self.lc_yathi_sub.id}/")
        self.assertEqual(sub_detail.status_code, 200)
        sub_patch = self.client.patch(
            f"/api/courses/live-classes/{self.lc_yathi_sub.id}/",
            {"title": "Yathi Substituted Updated"},
            format="json",
        )
        self.assertEqual(sub_patch.status_code, 200)
        self.assertEqual(sub_patch.data["title"], "Yathi Substituted Updated")

        # Substitute instructor cannot PATCH another class in the same batch or move class to another batch
        self.assertEqual(
            self.client.patch(
                f"/api/courses/live-classes/{self.lc_yathi_1.id}/",
                {"title": "Forbidden"},
                format="json",
            ).status_code,
            404,
        )
        move_batch = self.client.patch(
            f"/api/courses/live-classes/{self.lc_yathi_sub.id}/",
            {"batch": self.batch_bhagya.id},
            format="json",
        )
        self.assertIn(move_batch.status_code, (400, 403, 404))

        sub_start = self.client.post(f"/api/courses/live-classes/{self.lc_yathi_sub.id}/start/")
        self.assertEqual(sub_start.status_code, 200)
        self.assertEqual(self.client.post(f"/api/courses/live-classes/{self.lc_yathi_1.id}/start/").status_code, 404)

        # Course-level teacher still sees the assigned course
        courses_resp = self.client.get("/api/courses/?assigned_only=true")
        self.assertEqual(courses_resp.status_code, 200)
        course_ids = {row["id"] for row in self._rows(courses_resp)}
        self.assertIn(self.shared_course.id, course_ids)

    def test_batch_mentor_access_unassigned_batch_and_superadmin_visibility(self):
        # Batch mentor sees batch_yathi and batch_unassigned; unassigned batch keeps instructor_name == ""
        self.client.force_authenticate(user=self.batch_mentor)
        b_resp = self.client.get("/api/courses/live-batches/")
        self.assertEqual(b_resp.status_code, 200)
        rows_by_id = {row["id"]: row for row in self._rows(b_resp)}
        self.assertEqual(set(rows_by_id.keys()), {self.batch_yathi.id, self.batch_unassigned.id})
        self.assertEqual(rows_by_id[self.batch_unassigned.id]["instructor_name"], "")
        self.assertEqual(rows_by_id[self.batch_unassigned.id]["mentor_name"], "Mentor User")

        # Mentor can read batch_yathi classes and report, but cannot PATCH non-report fields on LiveClass
        c_resp = self.client.get("/api/courses/live-classes/")
        self.assertEqual(c_resp.status_code, 200)
        c_ids = {row["id"] for row in self._rows(c_resp)}
        self.assertEqual(c_ids, {self.lc_yathi_1.id, self.lc_yathi_sub.id})
        mentor_patch = self.client.patch(
            f"/api/courses/live-classes/{self.lc_yathi_1.id}/",
            {"title": "Should Fail"},
            format="json",
        )
        self.assertEqual(mentor_patch.status_code, 403)

        # Superadmin sees all batches and all classes
        self.client.force_authenticate(user=self.superadmin)
        sa_batches = {row["id"] for row in self._rows(self.client.get("/api/courses/live-batches/"))}
        self.assertEqual(sa_batches, {self.batch_yathi.id, self.batch_bhagya.id, self.batch_unassigned.id})
        sa_classes = {row["id"] for row in self._rows(self.client.get("/api/courses/live-classes/"))}
        self.assertEqual(sa_classes, {self.lc_yathi_1.id, self.lc_yathi_sub.id, self.lc_bhagya.id})

    def test_course_mentor_cannot_access_other_batch_students_while_explicit_assignees_can(self):
        from courses.models import CourseInstructor

        course_mentor = User.objects.create_user(
            username="course_mentor_only",
            email="course_mentor_only@example.com",
            password="pw",
            is_mentor=True,
        )
        CourseInstructor.objects.create(
            course=self.shared_course,
            user=course_mentor,
            role=CourseInstructor.InstructorRole.MENTOR,
        )
        student = User.objects.create_user(
            username="batch_student_iso",
            email="batch_student_iso@example.com",
            password="pw",
            is_student=True,
        )

        # Course-level mentor still sees the assigned course, but gets 404 for batch_bhagya students
        self.client.force_authenticate(user=course_mentor)
        course_ids = {row["id"] for row in self._rows(self.client.get("/api/courses/?assigned_only=true"))}
        self.assertIn(self.shared_course.id, course_ids)
        self.assertEqual(self.client.get(f"/api/courses/live-batches/{self.batch_bhagya.id}/students/").status_code, 404)
        self.assertEqual(
            self.client.post(
                f"/api/courses/live-batches/{self.batch_bhagya.id}/students/",
                {"student_id": student.id},
                format="json",
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.delete(f"/api/courses/live-batches/{self.batch_bhagya.id}/students/{student.id}/").status_code,
            404,
        )

        # Explicit batch instructor (yathivandhya) and explicit batch mentor (batch_mentor) retain GET/POST/DELETE
        self.client.force_authenticate(user=self.yathivandhya)
        self.assertEqual(self.client.get(f"/api/courses/live-batches/{self.batch_yathi.id}/students/").status_code, 200)
        self.assertEqual(
            self.client.post(
                f"/api/courses/live-batches/{self.batch_yathi.id}/students/",
                {"student_id": student.id},
                format="json",
            ).status_code,
            201,
        )
        self.assertEqual(
            self.client.delete(f"/api/courses/live-batches/{self.batch_yathi.id}/students/{student.id}/").status_code,
            204,
        )

        self.client.force_authenticate(user=self.batch_mentor)
        self.assertEqual(
            self.client.post(
                f"/api/courses/live-batches/{self.batch_yathi.id}/students/",
                {"student_id": student.id},
                format="json",
            ).status_code,
            201,
        )
        self.assertEqual(self.client.get(f"/api/courses/live-batches/{self.batch_yathi.id}/students/").status_code, 200)
        self.assertEqual(
            self.client.delete(f"/api/courses/live-batches/{self.batch_yathi.id}/students/{student.id}/").status_code,
            204,
        )

