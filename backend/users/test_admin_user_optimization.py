from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from decimal import Decimal

from courses.models import Course, Enrollment, LiveBatch, LiveBatchStudent, CourseInstructor
from orders.models import Purchase

User = get_user_model()


class AdminUserOptimizationTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            username="super_opt",
            email="super_opt@example.com",
            password="Password123!",
            is_staff=True,
            is_superuser=True
        )
        self.client.force_authenticate(user=self.superadmin)

        # Create sample courses
        self.course1 = Course.objects.create(
            title="Course 1 - Bharatanatyam",
            description="Basics of Bharatanatyam",
            price=Decimal("1999.00"),
            is_published=True
        )
        self.course2 = Course.objects.create(
            title="Course 2 - Kathak",
            description="Basics of Kathak",
            price=Decimal("2499.00"),
            is_published=True
        )
        self.course3 = Course.objects.create(
            title="Course 3 - Carnatic Music",
            description="Basics of Carnatic",
            price=Decimal("1499.00"),
            is_published=True
        )

        # Create students, teachers, mentors
        self.student = User.objects.create_user(
            username="student_opt",
            email="student_opt@example.com",
            first_name="Priya",
            last_name="Sharma",
            phone_number="+919876543210",
            is_student=True,
            is_teacher=False,
            is_mentor=False,
            is_active=True,
            student_type="CAREER_ACADEMY"
        )
        self.teacher = User.objects.create_user(
            username="teacher_opt",
            email="teacher_opt@example.com",
            first_name="Ananya",
            last_name="Bhat",
            phone_number="+919876543211",
            is_student=False,
            is_teacher=True,
            is_mentor=False,
            is_active=True
        )
        self.mentor = User.objects.create_user(
            username="mentor_opt",
            email="mentor_opt@example.com",
            first_name="Vikram",
            last_name="Rao",
            phone_number="+919876543212",
            is_student=False,
            is_teacher=False,
            is_mentor=True,
            is_active=False  # Inactive mentor for filter test
        )

        # Live Batch for course 3
        self.batch = LiveBatch.objects.create(
            course=self.course3,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )

    def test_pagination_and_response_structure(self):
        """AdminUserViewSet returns standard paginated response with count and results."""
        res = self.client.get("/api/users/admin-users/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIn("count", res.data)
        self.assertIn("results", res.data)
        self.assertGreaterEqual(res.data["count"], 4)
        self.assertIsInstance(res.data["results"], list)

    def test_role_filtering(self):
        """Filter by role=students, teachers, mentors."""
        # Students
        res_students = self.client.get("/api/users/admin-users/?role=students")
        self.assertEqual(res_students.status_code, status.HTTP_200_OK)
        usernames = [u["username"] for u in res_students.data["results"]]
        self.assertIn("student_opt", usernames)
        self.assertNotIn("teacher_opt", usernames)
        self.assertNotIn("mentor_opt", usernames)

        # Teachers
        res_teachers = self.client.get("/api/users/admin-users/?role=teachers")
        self.assertEqual(res_teachers.status_code, status.HTTP_200_OK)
        usernames = [u["username"] for u in res_teachers.data["results"]]
        self.assertIn("teacher_opt", usernames)
        self.assertNotIn("student_opt", usernames)

        # Mentors
        res_mentors = self.client.get("/api/users/admin-users/?role=mentors")
        self.assertEqual(res_mentors.status_code, status.HTTP_200_OK)
        usernames = [u["username"] for u in res_mentors.data["results"]]
        self.assertIn("mentor_opt", usernames)
        self.assertNotIn("student_opt", usernames)

    def test_status_and_track_filtering(self):
        """Filter by status=active/inactive and student_type."""
        # Inactive
        res_inactive = self.client.get("/api/users/admin-users/?status=inactive")
        self.assertEqual(res_inactive.status_code, status.HTTP_200_OK)
        usernames = [u["username"] for u in res_inactive.data["results"]]
        self.assertIn("mentor_opt", usernames)
        self.assertNotIn("student_opt", usernames)

        # Student Type CAREER_ACADEMY
        res_track = self.client.get("/api/users/admin-users/?student_type=CAREER_ACADEMY")
        self.assertEqual(res_track.status_code, status.HTTP_200_OK)
        usernames = [u["username"] for u in res_track.data["results"]]
        self.assertIn("student_opt", usernames)

    def test_search_filter(self):
        """Search by partial name, email, or phone number."""
        res_name = self.client.get("/api/users/admin-users/?search=Priya")
        self.assertEqual(res_name.status_code, status.HTTP_200_OK)
        self.assertEqual(res_name.data["count"], 1)
        self.assertEqual(res_name.data["results"][0]["username"], "student_opt")

        res_phone = self.client.get("/api/users/admin-users/?search=9876543211")
        self.assertEqual(res_phone.status_code, status.HTTP_200_OK)
        self.assertEqual(res_phone.data["count"], 1)
        self.assertEqual(res_phone.data["results"][0]["username"], "teacher_opt")

    def test_courses_count_includes_purchases_enrollments_batches_and_instructors(self):
        """
        Verify courses_count accurately unions:
        - Successful Purchase
        - Direct Enrollment
        - LiveBatchStudent
        - CourseInstructor
        """
        # 1. Student has Purchase on Course 1
        Purchase.objects.create(
            user=self.student,
            course=self.course1,
            amount=Decimal("1999.00"),
            status=Purchase.Status.SUCCESS
        )

        # 2. Student has Direct Enrollment on Course 2
        Enrollment.objects.create(
            user=self.student,
            course=self.course2
        )

        # 3. Student has LiveBatch assignment on Course 3 (via batch)
        LiveBatchStudent.objects.create(
            batch=self.batch,
            student=self.student
        )

        # 4. Teacher has CourseInstructor assignment on Course 1 and Course 2
        CourseInstructor.objects.create(
            course=self.course1,
            user=self.teacher,
            role=CourseInstructor.InstructorRole.TEACHER
        )
        CourseInstructor.objects.create(
            course=self.course2,
            user=self.teacher,
            role=CourseInstructor.InstructorRole.TEACHER
        )

        # Check list endpoint precomputed counts
        res = self.client.get("/api/users/admin-users/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        user_map = {u["id"]: u for u in res.data["results"]}

        student_data = user_map[self.student.id]
        # Course 1 (Purchase) + Course 2 (Enrollment) + Course 3 (LiveBatch) = 3 courses
        self.assertEqual(student_data["courses_count"], 3)

        teacher_data = user_map[self.teacher.id]
        # Course 1 (Instructor) + Course 2 (Instructor) = 2 courses
        self.assertEqual(teacher_data["courses_count"], 2)

    def test_student_and_teacher_courses_endpoint(self):
        """
        Verify GET /api/users/admin-users/{id}/courses/ returns union of all
        purchases, enrollments, live batch students, and instructor records.
        """
        Purchase.objects.create(
            user=self.student,
            course=self.course1,
            amount=Decimal("1999.00"),
            status=Purchase.Status.SUCCESS
        )
        LiveBatchStudent.objects.create(
            batch=self.batch,
            student=self.student
        )
        CourseInstructor.objects.create(
            course=self.course2,
            user=self.teacher,
            role=CourseInstructor.InstructorRole.TEACHER
        )

        # Check student courses
        res_student_courses = self.client.get(f"/api/users/admin-users/{self.student.id}/courses/")
        self.assertEqual(res_student_courses.status_code, status.HTTP_200_OK)
        student_course_ids = [c["course_id"] for c in res_student_courses.data]
        self.assertIn(self.course1.id, student_course_ids)
        self.assertIn(self.course3.id, student_course_ids)

        # Check teacher courses (CourseInstructor visibility)
        res_teacher_courses = self.client.get(f"/api/users/admin-users/{self.teacher.id}/courses/")
        self.assertEqual(res_teacher_courses.status_code, status.HTTP_200_OK)
        teacher_course_ids = [c["course_id"] for c in res_teacher_courses.data]
        self.assertIn(self.course2.id, teacher_course_ids)
        self.assertEqual(res_teacher_courses.data[0]["role"], "Teacher")

    def test_unassign_course_cleans_up_all_relation_types(self):
        """Unassigning a course deletes Enrollment, LiveBatchStudent, or CourseInstructor."""
        Enrollment.objects.create(user=self.student, course=self.course1)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student)
        CourseInstructor.objects.create(course=self.course2, user=self.teacher, role="TEACHER")

        # Unassign student from course 1
        res1 = self.client.post(f"/api/users/admin-users/{self.student.id}/unassign_course/", {"course_id": self.course1.id})
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertFalse(Enrollment.objects.filter(user=self.student, course=self.course1).exists())

        # Unassign student from course 3 (via batch)
        res3 = self.client.post(f"/api/users/admin-users/{self.student.id}/unassign_course/", {"course_id": self.course3.id})
        self.assertEqual(res3.status_code, status.HTTP_200_OK)
        self.assertFalse(LiveBatchStudent.objects.filter(student=self.student, batch__course=self.course3).exists())

        # Unassign teacher from course 2 (CourseInstructor)
        res_t = self.client.post(f"/api/users/admin-users/{self.teacher.id}/unassign_course/", {"course_id": self.course2.id})
        self.assertEqual(res_t.status_code, status.HTTP_200_OK)
        self.assertFalse(CourseInstructor.objects.filter(user=self.teacher, course=self.course2).exists())

    def test_unassign_teacher_preserves_student_enrollments_and_batch_memberships(self):
        """
        Verify that unassigning a teacher does NOT delete student enrollments,
        live batch memberships, or other teachers' assignments.
        """
        student1 = User.objects.create_user(
            username="student_enrolled",
            email="s_enrolled@example.com",
            is_student=True
        )
        student2 = User.objects.create_user(
            username="student_batch",
            email="s_batch@example.com",
            is_student=True
        )
        other_teacher = User.objects.create_user(
            username="other_teacher",
            email="other_t@example.com",
            is_teacher=True
        )

        # Enroll student 1 via direct Enrollment
        enrollment = Enrollment.objects.create(user=student1, course=self.course1)
        # Enroll student 2 via LiveBatchStudent in a batch of course 1
        batch_c1 = LiveBatch.objects.create(
            course=self.course1,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )
        batch_student = LiveBatchStudent.objects.create(batch=batch_c1, student=student2)
        # Assign teacher and other_teacher as CourseInstructor
        instructor1 = CourseInstructor.objects.create(course=self.course1, user=self.teacher, role="TEACHER")
        instructor2 = CourseInstructor.objects.create(course=self.course1, user=other_teacher, role="TEACHER")

        # Now unassign self.teacher from course1
        res = self.client.post(
            f"/api/users/admin-users/{self.teacher.id}/unassign_course/",
            {"course_id": self.course1.id}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # 1. Teacher's instructor assignment IS deleted
        self.assertFalse(CourseInstructor.objects.filter(user=self.teacher, course=self.course1).exists())

        # 2. Student 1's enrollment is PRESERVED
        self.assertTrue(Enrollment.objects.filter(id=enrollment.id).exists())
        self.assertTrue(Enrollment.objects.filter(user=student1, course=self.course1).exists())

        # 3. Student 2's batch membership is PRESERVED
        self.assertTrue(LiveBatchStudent.objects.filter(id=batch_student.id).exists())
        self.assertTrue(LiveBatchStudent.objects.filter(student=student2, batch=batch_c1).exists())

        # 4. The LiveBatch itself is PRESERVED
        self.assertTrue(LiveBatch.objects.filter(id=batch_c1.id).exists())

        # 5. Other teacher's CourseInstructor assignment is PRESERVED
        self.assertTrue(CourseInstructor.objects.filter(id=instructor2.id).exists())

    def test_teacher_profiles_visibility_sreehari_sandra_sreelakshmi(self):
        """
        Explicitly verify that teachers such as SREEHARI, Sandra.C, and
        Kalakshetra Sreelakshmi have their CourseInstructor assignments
        correctly returned in /admin-users/{id}/courses/ and courses_count.
        """
        teacher_sreehari = User.objects.create_user(
            username="sreehari",
            first_name="SREEHARI",
            email="sreehari@example.com",
            is_teacher=True
        )
        teacher_sandra = User.objects.create_user(
            username="sandra_c",
            first_name="Sandra.C",
            email="sandra.c@example.com",
            is_teacher=True
        )
        teacher_sreelakshmi = User.objects.create_user(
            username="kalakshetra_sreelakshmi",
            first_name="Kalakshetra",
            last_name="Sreelakshmi",
            email="sreelakshmi@example.com",
            is_teacher=True
        )

        # SREEHARI -> course 1 and course 2
        CourseInstructor.objects.create(course=self.course1, user=teacher_sreehari, role="TEACHER")
        CourseInstructor.objects.create(course=self.course2, user=teacher_sreehari, role="TEACHER")

        # Sandra.C -> course 2
        CourseInstructor.objects.create(course=self.course2, user=teacher_sandra, role="TEACHER")

        # Kalakshetra Sreelakshmi -> course 1 and course 3
        CourseInstructor.objects.create(course=self.course1, user=teacher_sreelakshmi, role="TEACHER")
        CourseInstructor.objects.create(course=self.course3, user=teacher_sreelakshmi, role="TEACHER")

        # 1. Verify GET /api/users/admin-users/{id}/courses/ for SREEHARI
        res_sreehari = self.client.get(f"/api/users/admin-users/{teacher_sreehari.id}/courses/")
        self.assertEqual(res_sreehari.status_code, status.HTTP_200_OK)
        sreehari_cids = {c["course_id"] for c in res_sreehari.data}
        self.assertEqual(sreehari_cids, {self.course1.id, self.course2.id})
        for c in res_sreehari.data:
            self.assertEqual(c["role"], "Teacher")

        # 2. Verify GET /api/users/admin-users/{id}/courses/ for Sandra.C
        res_sandra = self.client.get(f"/api/users/admin-users/{teacher_sandra.id}/courses/")
        self.assertEqual(res_sandra.status_code, status.HTTP_200_OK)
        sandra_cids = {c["course_id"] for c in res_sandra.data}
        self.assertEqual(sandra_cids, {self.course2.id})
        self.assertEqual(res_sandra.data[0]["role"], "Teacher")

        # 3. Verify GET /api/users/admin-users/{id}/courses/ for Kalakshetra Sreelakshmi
        res_sreelakshmi = self.client.get(f"/api/users/admin-users/{teacher_sreelakshmi.id}/courses/")
        self.assertEqual(res_sreelakshmi.status_code, status.HTTP_200_OK)
        sreelakshmi_cids = {c["course_id"] for c in res_sreelakshmi.data}
        self.assertEqual(sreelakshmi_cids, {self.course1.id, self.course3.id})
        for c in res_sreelakshmi.data:
            self.assertEqual(c["role"], "Teacher")

        # 4. Verify courses_count in teachers list API
        res_teachers = self.client.get("/api/users/admin-users/?role=teachers")
        self.assertEqual(res_teachers.status_code, status.HTTP_200_OK)
        teacher_map = {u["username"]: u["courses_count"] for u in res_teachers.data["results"]}
        self.assertEqual(teacher_map.get("sreehari"), 2)
        self.assertEqual(teacher_map.get("sandra_c"), 1)
        self.assertEqual(teacher_map.get("kalakshetra_sreelakshmi"), 2)


class CourseListOptimizationTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin_course_opt",
            email="admin_copt@example.com",
            password="Password123!",
            is_staff=True,
            is_superuser=True
        )
        self.client.force_authenticate(user=self.admin)

        from courses.models import Module, VideoLesson, LessonProgress
        self.course = Course.objects.create(
            title="Course Perf Test",
            description="Testing course list performance",
            price=Decimal("999.00"),
            is_published=True
        )
        self.module = Module.objects.create(course=self.course, title="Module 1")
        self.lesson1 = VideoLesson.objects.create(module=self.module, title="Lesson 1")
        self.lesson2 = VideoLesson.objects.create(module=self.module, title="Lesson 2")
        LessonProgress.objects.create(user=self.admin, lesson=self.lesson1, completed=True)

    def test_course_list_response_and_progress(self):
        """GET /api/courses/ returns prefetched modules and calculates progress accurately."""
        res = self.client.get("/api/courses/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        courses = res.data if isinstance(res.data, list) else res.data.get("results", [])
        self.assertGreaterEqual(len(courses), 1)

        matched = next((c for c in courses if c["id"] == self.course.id), None)
        self.assertIsNotNone(matched)
        self.assertEqual(matched["title"], "Course Perf Test")
        self.assertEqual(len(matched["modules"]), 1)
        # 1 out of 2 lessons completed = 50%
        self.assertEqual(matched["progress_percentage"], 50)

