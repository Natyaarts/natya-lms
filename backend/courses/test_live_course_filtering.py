from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from rest_framework import status
from rest_framework.test import APITestCase
from courses.models import Course, LiveBatch, LiveBatchStudent, LiveClass, Enrollment

User = get_user_model()


class LiveCourseFilteringTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_superuser(
            username="superadmin", email="superadmin@natyaarts.com", password="password123"
        )
        self.teacher = User.objects.create_user(
            username="teacher1", email="teacher1@natyaarts.com", password="password123", is_teacher=True
        )
        self.student_assigned = User.objects.create_user(
            username="student_assigned", email="assigned@natyaarts.com", password="password123"
        )
        self.student_unassigned = User.objects.create_user(
            username="student_unassigned", email="unassigned@natyaarts.com", password="password123"
        )

        # 1. Published Recorded Course
        self.recorded_course = Course.objects.create(
            title="Bharatanatyam Foundations (Recorded)",
            course_type=Course.CourseType.RECORDED,
            is_published=True,
            price=999
        )

        # 2. Published Live Course A (Assigned to student_assigned)
        self.live_course_a = Course.objects.create(
            title="Live Advanced Abhinaya (Batch A)",
            course_type=Course.CourseType.LIVE,
            is_published=True,
            price=2999
        )
        self.batch_a = LiveBatch.objects.create(
            course=self.live_course_a,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP
        )
        LiveBatchStudent.objects.create(
            batch=self.batch_a,
            student=self.student_assigned
        )
        self.live_class_a = LiveClass.objects.create(
            course=self.live_course_a,
            batch=self.batch_a,
            instructor=self.teacher,
            title="Session 1: Natyashastra Deep Dive",
            scheduled_start=timezone.now() + timedelta(days=2),
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED,
            meeting_url="https://zoom.us/j/123456789"
        )

        # 3. Published Live Course B (NOT assigned to student_assigned or student_unassigned)
        self.live_course_b = Course.objects.create(
            title="Live Nattuvangam Intensive (Batch B)",
            course_type=Course.CourseType.LIVE,
            is_published=True,
            price=4999
        )
        self.batch_b = LiveBatch.objects.create(
            course=self.live_course_b,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.ONE_TO_ONE
        )
        self.live_class_b = LiveClass.objects.create(
            course=self.live_course_b,
            batch=self.batch_b,
            instructor=self.teacher,
            title="Session 1: Tala Mastery",
            scheduled_start=timezone.now() + timedelta(days=3),
            duration_minutes=45,
            status=LiveClass.ClassStatus.SCHEDULED,
            meeting_url="https://zoom.us/j/987654321"
        )

    def test_anonymous_user_only_sees_published_recorded_courses(self):
        res = self.client.get("/api/courses/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        course_ids = [c["id"] for c in res.data]
        self.assertIn(self.recorded_course.id, course_ids)
        self.assertNotIn(self.live_course_a.id, course_ids)
        self.assertNotIn(self.live_course_b.id, course_ids)

    def test_assigned_student_only_sees_assigned_live_course(self):
        self.client.force_authenticate(user=self.student_assigned)
        res = self.client.get("/api/courses/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        course_ids = [c["id"] for c in res.data]
        self.assertIn(self.recorded_course.id, course_ids)
        self.assertIn(self.live_course_a.id, course_ids)
        self.assertNotIn(self.live_course_b.id, course_ids)

    def test_unassigned_student_sees_no_live_courses(self):
        self.client.force_authenticate(user=self.student_unassigned)
        res = self.client.get("/api/courses/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        course_ids = [c["id"] for c in res.data]
        self.assertIn(self.recorded_course.id, course_ids)
        self.assertNotIn(self.live_course_a.id, course_ids)
        self.assertNotIn(self.live_course_b.id, course_ids)

    def test_unassigned_student_cannot_retrieve_live_course_detail(self):
        self.client.force_authenticate(user=self.student_unassigned)
        res = self.client.get(f"/api/courses/{self.live_course_a.id}/")
        self.assertEqual(res.status_code, status.HTTP_404_NOT_FOUND)

    def test_assigned_student_can_retrieve_live_course_detail(self):
        self.client.force_authenticate(user=self.student_assigned)
        res = self.client.get(f"/api/courses/{self.live_course_a.id}/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["id"], self.live_course_a.id)

    def test_live_class_listing_only_shows_assigned_batch_sessions(self):
        self.client.force_authenticate(user=self.student_assigned)
        res = self.client.get("/api/courses/live-classes/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("results", res.data)
        class_ids = [c["id"] for c in results]
        self.assertIn(self.live_class_a.id, class_ids)
        self.assertNotIn(self.live_class_b.id, class_ids)

    def test_unassigned_student_sees_zero_live_classes(self):
        self.client.force_authenticate(user=self.student_unassigned)
        res = self.client.get("/api/courses/live-classes/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        results = res.data.get("results", res.data)
        self.assertEqual(len(results), 0)

    def test_teacher_sees_conducted_live_courses_and_batches(self):
        self.client.force_authenticate(user=self.teacher)
        res = self.client.get("/api/courses/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        course_ids = [c["id"] for c in res.data]
        self.assertIn(self.live_course_a.id, course_ids)
        self.assertIn(self.live_course_b.id, course_ids)

    def test_superadmin_sees_all_courses_and_classes(self):
        self.client.force_authenticate(user=self.superadmin)
        res = self.client.get("/api/courses/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        course_ids = [c["id"] for c in res.data]
        self.assertIn(self.recorded_course.id, course_ids)
        self.assertIn(self.live_course_a.id, course_ids)
        self.assertIn(self.live_course_b.id, course_ids)

