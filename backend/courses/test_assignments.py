"""
Phase 4.7: Assignments & Grading.

Reuses the existing course fixture helper (courses/test_completion.py)
rather than duplicating it. Assignments deliberately never touch
courses/services/completion.py -- no test here asserts anything about
module/course completion or certificates; those are covered by their own
dedicated (untouched) test files.
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Assignment, AssignmentSubmission, Course, CourseInstructor, Enrollment, Module
from courses.services.assignments import GradingValidationError, SubmissionNotAllowedError, create_submission, grade_submission, return_for_revision
from courses.test_completion import _make_course
from django.core.cache import cache

User = get_user_model()


def _make_assignment(module=None, is_published=True, max_marks=Decimal("100.00"), order=1):
    if module is None:
        course = _make_course()
        module = Module.objects.create(course=course, title="M1", order=1)
    return Assignment.objects.create(module=module, title="HW1", order=order, is_published=is_published, max_marks=max_marks)


class AssignmentModelTests(TestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: see other setUp() comments in this file.

    def test_create_assignment_with_defaults(self):
        assignment = _make_assignment()
        self.assertFalse(assignment.is_published is None)
        self.assertEqual(assignment.max_marks, Decimal("100.00"))

    def test_duplicate_order_within_module_rejected(self):
        course = _make_course()
        module = Module.objects.create(course=course, title="M1", order=1)
        Assignment.objects.create(module=module, title="A1", order=1, is_published=True)
        from django.db import IntegrityError
        with self.assertRaises(IntegrityError):
            Assignment.objects.create(module=module, title="A2", order=1, is_published=True)

    def test_max_marks_must_be_positive(self):
        from django.core.exceptions import ValidationError
        course = _make_course()
        module = Module.objects.create(course=course, title="M1", order=1)
        assignment = Assignment(module=module, title="A1", order=1, max_marks=Decimal("0.00"))
        with self.assertRaises(ValidationError):
            assignment.full_clean()

    def test_deleting_module_cascades_to_assignment(self):
        course = _make_course()
        module = Module.objects.create(course=course, title="M1", order=1)
        assignment = Assignment.objects.create(module=module, title="A1", order=1, is_published=True)
        module.delete()
        self.assertFalse(Assignment.objects.filter(pk=assignment.pk).exists())


class SubmissionServiceTests(TestCase):
    """Direct, unit-level tests of the state-transition service --
    no authorization here (that's the view layer's job, tested below)."""

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.assignment = _make_assignment(max_marks=Decimal("100.00"))
        self.student = User.objects.create_user(username="svc_student", password="pw")
        self.teacher = User.objects.create_user(username="svc_teacher", password="pw", is_teacher=True)

    def test_first_submission_allowed(self):
        submission = create_submission(self.assignment, self.student, "answer", None)
        self.assertEqual(submission.attempt_number, 1)
        self.assertEqual(submission.status, AssignmentSubmission.Status.SUBMITTED)

    def test_second_submission_while_pending_rejected(self):
        create_submission(self.assignment, self.student, "answer", None)
        with self.assertRaises(SubmissionNotAllowedError):
            create_submission(self.assignment, self.student, "answer again", None)

    def test_resubmission_allowed_after_return_for_revision(self):
        s1 = create_submission(self.assignment, self.student, "answer", None)
        return_for_revision(s1, self.teacher, "fix part 2")
        s2 = create_submission(self.assignment, self.student, "revised answer", None)
        self.assertEqual(s2.attempt_number, 2)

    def test_resubmission_not_allowed_after_graded(self):
        s1 = create_submission(self.assignment, self.student, "answer", None)
        grade_submission(s1, self.teacher, Decimal("90.00"), "great")
        with self.assertRaises(SubmissionNotAllowedError):
            create_submission(self.assignment, self.student, "another try", None)

    def test_grading_rejects_negative_marks(self):
        s1 = create_submission(self.assignment, self.student, "answer", None)
        with self.assertRaises(GradingValidationError):
            grade_submission(s1, self.teacher, Decimal("-5.00"), "bad")

    def test_grading_rejects_marks_exceeding_max(self):
        s1 = create_submission(self.assignment, self.student, "answer", None)
        with self.assertRaises(GradingValidationError):
            grade_submission(s1, self.teacher, Decimal("101.00"), "too high")

    def test_grading_accepts_marks_equal_to_max(self):
        s1 = create_submission(self.assignment, self.student, "answer", None)
        grade_submission(s1, self.teacher, Decimal("100.00"), "perfect")
        s1.refresh_from_db()
        self.assertEqual(s1.marks_awarded, Decimal("100.00"))

    def test_historical_submission_preserved_after_resubmission(self):
        s1 = create_submission(self.assignment, self.student, "first draft", None)
        return_for_revision(s1, self.teacher, "needs work")
        s2 = create_submission(self.assignment, self.student, "second draft", None)
        grade_submission(s2, self.teacher, Decimal("70.00"), "better")

        s1.refresh_from_db()
        self.assertEqual(s1.status, AssignmentSubmission.Status.RETURNED_FOR_REVISION)
        self.assertEqual(s1.content, "first draft")
        self.assertEqual(s1.feedback, "needs work")
        self.assertEqual(AssignmentSubmission.objects.filter(student=self.student, assignment=self.assignment).count(), 2)


class AssignmentPublishingAndAccessTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.assignment = _make_assignment(module=self.module, is_published=True)
        self.student = User.objects.create_user(username="pub_student", password="pw")

    def test_published_assignment_visible_to_enrolled_student(self):
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('assignment-detail', kwargs={'pk': self.assignment.id}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_unpublished_assignment_hidden(self):
        self.assignment.is_published = False
        self.assignment.save()
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('assignment-detail', kwargs={'pk': self.assignment.id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_no_course_access_blocked(self):
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('assignment-detail', kwargs={'pk': self.assignment.id}))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonymous_blocked(self):
        response = self.client.get(reverse('assignment-detail', kwargs={'pk': self.assignment.id}))
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_assignment_shown_in_module_sidebar_when_published(self):
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('course-detail', kwargs={'pk': self.course.id}))
        module_data = next(m for m in response.data['modules'] if m['id'] == self.module.id)
        self.assertEqual(len(module_data['assignments']), 1)
        self.assertEqual(module_data['assignments'][0]['status'], "NOT_SUBMITTED")

    def test_unpublished_assignment_not_in_sidebar(self):
        self.assignment.is_published = False
        self.assignment.save()
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)
        response = self.client.get(reverse('course-detail', kwargs={'pk': self.course.id}))
        module_data = next(m for m in response.data['modules'] if m['id'] == self.module.id)
        self.assertEqual(module_data['assignments'], [])


class SubmissionAPITests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.assignment = _make_assignment(module=self.module, max_marks=Decimal("50.00"))
        self.student = User.objects.create_user(username="submit_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)

    def test_submit_text_only(self):
        response = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"content": "my answer"})
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], "SUBMITTED")
        self.assertEqual(response.data['attempt_number'], 1)

    def test_submit_requires_content_or_file(self):
        response = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_submit_file(self):
        file = SimpleUploadedFile("homework.pdf", b"%PDF-1.4 fake pdf content", content_type="application/pdf")
        response = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"file": file}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_submit_rejects_disallowed_extension(self):
        file = SimpleUploadedFile("virus.exe", b"MZ fake binary", content_type="application/octet-stream")
        response = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"file": file}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_submit_rejects_oversized_file(self):
        big_content = b"0" * (11 * 1024 * 1024)  # 11MB > 10MB cap
        file = SimpleUploadedFile("big.pdf", big_content, content_type="application/pdf")
        response = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"file": file}, format='multipart')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_second_submission_while_pending_rejected_via_api(self):
        self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"content": "first"})
        response = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"content": "second"})
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_cannot_submit_to_unpublished_assignment(self):
        self.assignment.is_published = False
        self.assignment.save()
        response = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"content": "answer"})
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_student_cannot_submit_for_another_student(self):
        """The submit endpoint never reads a student id from the request
        body at all -- always request.user -- so there is no field to
        even attempt to spoof."""
        other_student = User.objects.create_user(username="submit_other_student", password="pw")
        response = self.client.post(
            reverse('assignment-submit', kwargs={'pk': self.assignment.id}),
            {"content": "answer", "student": other_student.id},
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        submission = AssignmentSubmission.objects.get(pk=response.data['id'])
        self.assertEqual(submission.student_id, self.student.id)


class GradingAuthorizationTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.assignment = _make_assignment(module=self.module, max_marks=Decimal("100.00"))
        self.student = User.objects.create_user(username="grade_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)
        submit = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"content": "answer"})
        self.submission_id = submit.data['id']

    def _grade(self, user, marks=80, feedback="good"):
        self.client.force_authenticate(user)
        return self.client.post(
            reverse('assignment-submission-grade', kwargs={'pk': self.submission_id}),
            {"marks_awarded": marks, "feedback": feedback}, format='json',
        )

    def test_student_cannot_grade_own_submission(self):
        response = self._grade(self.student)
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))
        submission = AssignmentSubmission.objects.get(pk=self.submission_id)
        self.assertEqual(submission.status, AssignmentSubmission.Status.SUBMITTED)

    def test_assigned_instructor_can_grade(self):
        teacher = User.objects.create_user(username="grade_assigned_teacher", password="pw", is_teacher=True)
        CourseInstructor.objects.create(course=self.course, user=teacher, role='TEACHER')
        response = self._grade(teacher)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], "GRADED")

    def test_unassigned_instructor_cannot_grade(self):
        other_course = _make_course()
        other_teacher = User.objects.create_user(username="grade_unassigned_teacher", password="pw", is_teacher=True)
        CourseInstructor.objects.create(course=other_course, user=other_teacher, role='TEACHER')
        response = self._grade(other_teacher)
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))
        submission = AssignmentSubmission.objects.get(pk=self.submission_id)
        self.assertEqual(submission.status, AssignmentSubmission.Status.SUBMITTED)

    def test_admin_staff_can_grade(self):
        admin_user = User.objects.create_user(username="grade_admin", password="pw", is_staff=True)
        response = self._grade(admin_user)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_superuser_can_grade(self):
        superuser = User.objects.create_superuser(username="grade_superuser", password="pw")
        response = self._grade(superuser)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_marks_cannot_exceed_max_marks(self):
        superuser = User.objects.create_superuser(username="grade_over_max", password="pw")
        response = self._grade(superuser, marks=150)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_marks_cannot_be_negative(self):
        superuser = User.objects.create_superuser(username="grade_negative", password="pw")
        response = self._grade(superuser, marks=-10)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_return_for_revision_requires_instructor_or_admin(self):
        response = self.client.post(reverse('assignment-submission-return-for-revision', kwargs={'pk': self.submission_id}), {"feedback": "redo"})
        self.client.force_authenticate(self.student)
        self.assertIn(response.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND, status.HTTP_401_UNAUTHORIZED))

    def test_return_for_revision_then_resubmission_allowed(self):
        superuser = User.objects.create_superuser(username="grade_return_super", password="pw")
        self.client.force_authenticate(superuser)
        response = self.client.post(reverse('assignment-submission-return-for-revision', kwargs={'pk': self.submission_id}), {"feedback": "redo part 2"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['status'], "RETURNED_FOR_REVISION")

        self.client.force_authenticate(self.student)
        resubmit = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"content": "revised answer"})
        self.assertEqual(resubmit.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resubmit.data['attempt_number'], 2)

    def test_resubmission_without_return_for_revision_rejected(self):
        """Grading (not returning) must NOT unblock a new submission in
        this phase's scope."""
        superuser = User.objects.create_superuser(username="grade_no_revision_super", password="pw")
        self._grade(superuser)
        self.client.force_authenticate(self.student)
        resubmit = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"content": "another try"})
        self.assertEqual(resubmit.status_code, status.HTTP_409_CONFLICT)


class SubmissionOwnershipTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.assignment = _make_assignment(module=self.module)
        self.student_a = User.objects.create_user(username="own_student_a", password="pw")
        self.student_b = User.objects.create_user(username="own_student_b", password="pw")
        Enrollment.objects.create(user=self.student_a, course=self.course)
        Enrollment.objects.create(user=self.student_b, course=self.course)

        self.client.force_authenticate(self.student_a)
        submit = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"content": "a's answer"})
        self.submission_id = submit.data['id']

    def test_student_b_cannot_retrieve_student_a_submission(self):
        self.client.force_authenticate(self.student_b)
        response = self.client.get(reverse('assignment-submission-detail', kwargs={'pk': self.submission_id}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_student_b_my_list_does_not_include_student_a_submission(self):
        self.client.force_authenticate(self.student_b)
        response = self.client.get(reverse('assignment-submission-my'))
        self.assertEqual(response.data['count'], 0)

    def test_student_a_sees_own_submission_in_my_list(self):
        self.client.force_authenticate(self.student_a)
        response = self.client.get(reverse('assignment-submission-my'), {"assignment_id": self.assignment.id})
        self.assertEqual(response.data['count'], 1)


class LockedCourseAssignmentTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: see other setUp() comments in this file.

    def test_locked_course_assignment_submit_rejected(self):
        course = _make_course()
        module = Module.objects.create(course=course, title="M1", order=1)
        assignment = _make_assignment(module=module)
        student = User.objects.create_user(username="locked_student", password="pw")
        self.client.force_authenticate(student)
        response = self.client.post(reverse('assignment-submit', kwargs={'pk': assignment.id}), {"content": "answer"})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(AssignmentSubmission.objects.count(), 0)


class AssignmentQueryCountTests(TestCase):
    """Section: 'Assignment listings must not perform one query per
    assignment/submission.'"""

    def setUp(self):
        cache.clear()  # rate-limiting gap fix: see other setUp() comments in this file.

    def _course_with_n_assignments(self, n):
        course = _make_course()
        module = Module.objects.create(course=course, title="M", order=1)
        for i in range(n):
            Assignment.objects.create(module=module, title=f"HW{i}", order=i, is_published=True)
        return course, module

    def test_module_sidebar_assignment_query_count_constant(self):
        from rest_framework.test import APIClient
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        student = User.objects.create_user(username="assign_qc_student", password="pw")
        client = APIClient()
        client.force_authenticate(student)

        one_course, _ = self._course_with_n_assignments(1)
        Enrollment.objects.create(user=student, course=one_course)
        with CaptureQueriesContext(connection) as ctx_one:
            r1 = client.get(reverse('course-detail', kwargs={'pk': one_course.id}))
        self.assertEqual(r1.status_code, 200)
        count_one = len(ctx_one.captured_queries)

        five_course, _ = self._course_with_n_assignments(5)
        Enrollment.objects.create(user=student, course=five_course)
        with CaptureQueriesContext(connection) as ctx_five:
            r5 = client.get(reverse('course-detail', kwargs={'pk': five_course.id}))
        self.assertEqual(r5.status_code, 200)
        count_five = len(ctx_five.captured_queries)

        self.assertEqual(count_one, count_five)

    def test_grading_queue_listing_query_count_bounded(self):
        from rest_framework.test import APIClient
        from django.test.utils import CaptureQueriesContext
        from django.db import connection

        course, module = self._course_with_n_assignments(1)
        assignment = module.assignments.first()
        teacher = User.objects.create_superuser(username="assign_qc_teacher", password="pw")

        for i in range(5):
            student = User.objects.create_user(username=f"assign_qc_sub_student_{i}", password="pw")
            Enrollment.objects.create(user=student, course=course)
            create_submission(assignment, student, f"answer {i}", None)

        client = APIClient()
        client.force_authenticate(teacher)
        with CaptureQueriesContext(connection) as ctx:
            response = client.get(reverse('assignment-submission-by-assignment', kwargs={'assignment_id': assignment.id}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['count'], 5)
        # select_related keeps this small and fixed regardless of submission count.
        self.assertLessEqual(len(ctx.captured_queries), 6)


class NotificationTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods, and reused PKs across rolled-back transactions can leak throttle state across test classes.
        self.course = _make_course()
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.assignment = _make_assignment(module=self.module, max_marks=Decimal("100.00"))
        self.student = User.objects.create_user(username="notify_student", password="pw")
        Enrollment.objects.create(user=self.student, course=self.course)
        self.client.force_authenticate(self.student)
        submit = self.client.post(reverse('assignment-submit', kwargs={'pk': self.assignment.id}), {"content": "answer"})
        self.submission_id = submit.data['id']

    def test_grading_sends_notification(self):
        from notifications.models import Notification, NotificationType
        superuser = User.objects.create_superuser(username="notify_super", password="pw")
        self.client.force_authenticate(superuser)
        self.client.post(reverse('assignment-submission-grade', kwargs={'pk': self.submission_id}), {"marks_awarded": 90, "feedback": "great"}, format='json')
        self.assertTrue(Notification.objects.filter(recipient=self.student, notification_type=NotificationType.ASSIGNMENT).exists())

    def test_return_for_revision_sends_notification(self):
        from notifications.models import Notification, NotificationType
        superuser = User.objects.create_superuser(username="notify_super2", password="pw")
        self.client.force_authenticate(superuser)
        self.client.post(reverse('assignment-submission-return-for-revision', kwargs={'pk': self.submission_id}), {"feedback": "redo"})
        self.assertTrue(Notification.objects.filter(recipient=self.student, notification_type=NotificationType.ASSIGNMENT).exists())
