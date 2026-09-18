"""
Custom Admin Dashboard Completion phase.

Covers the two backend additions made in the `courses` app:
1. AdminCertificateListView -- a new read-only, admin-wide list over the
   existing Certificate model (previously only owner-scoped list/
   retrieve existed via CertificateViewSet).
2. AdminAssignmentListView -- a new read-only, admin-wide list over the
   existing Assignment model, annotated with a live
   pending_submission_count so an admin can see grading backlog per
   assignment without a separate query.

Neither addition changes certificate issuance, assignment grading, or any
other existing business logic -- both are pure read paths.
"""
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Assignment, AssignmentSubmission, Certificate, Course, Module

User = get_user_model()


def _make_course(title="Admin Dash Course"):
    return Course.objects.create(title=title, description="x", price=10, is_published=True)


class AdminCertificateListViewTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods.
        self.admin = User.objects.create_user(username="cert_admin", password="pw", is_staff=True, is_student=False)
        self.student_a = User.objects.create_user(username="cert_dash_student_a", password="pw")
        self.student_b = User.objects.create_user(username="cert_dash_student_b", password="pw")
        self.course_1 = _make_course("Cert Dash Course 1")
        self.course_2 = _make_course("Cert Dash Course 2")

        self.cert_a = Certificate.objects.create(
            student=self.student_a, course=self.course_1, verification_id="CERT-DASH-A",
            learner_name_snapshot="Student A", course_title_snapshot=self.course_1.title,
        )
        self.cert_b = Certificate.objects.create(
            student=self.student_b, course=self.course_2, verification_id="CERT-DASH-B",
            learner_name_snapshot="Student B", course_title_snapshot=self.course_2.title,
        )

    def test_admin_sees_certificates_across_all_students(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-certificates'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 2)

    def test_student_field_rendered_as_nested_object(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-certificates'))
        row = next(r for r in res.data['results'] if r['verification_id'] == 'CERT-DASH-A')
        self.assertEqual(row['student'], {'id': self.student_a.id, 'username': 'cert_dash_student_a'})

    def test_filter_by_student_id(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-certificates'), {'student_id': self.student_a.id})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['verification_id'], 'CERT-DASH-A')

    def test_filter_by_course_id(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-certificates'), {'course_id': self.course_2.id})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['verification_id'], 'CERT-DASH-B')

    def test_student_cannot_access_admin_certificate_list(self):
        self.client.force_authenticate(user=self.student_a)
        res = self.client.get(reverse('admin-certificates'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_certificates_url_not_shadowed_by_course_detail_route(self):
        """
        courses/urls.py registers CourseViewSet at the router root (r'') --
        admin/certificates/ must resolve to AdminCertificateListView, not
        be swallowed as a course detail lookup for pk="admin".
        """
        from django.urls import resolve
        match = resolve('/api/courses/admin/certificates/')
        self.assertEqual(match.view_name, 'admin-certificates')


class AdminAssignmentListViewTests(APITestCase):
    def setUp(self):
        cache.clear()  # rate-limiting gap fix: LocMemCache isn't reset between test methods.
        self.admin = User.objects.create_user(username="assign_admin", password="pw", is_staff=True, is_student=False)
        self.student = User.objects.create_user(username="assign_dash_student", password="pw")
        self.course = _make_course("Assignment Dash Course")
        self.module = Module.objects.create(course=self.course, title="M1", order=1)

        self.assignment_needs_grading = Assignment.objects.create(
            module=self.module, title="Needs Grading", max_marks=10, order=1, is_published=True,
        )
        AssignmentSubmission.objects.create(
            assignment=self.assignment_needs_grading, student=self.student, attempt_number=1,
            status=AssignmentSubmission.Status.SUBMITTED, content="text",
        )

        self.assignment_graded = Assignment.objects.create(
            module=self.module, title="Already Graded", max_marks=10, order=2, is_published=True,
        )
        AssignmentSubmission.objects.create(
            assignment=self.assignment_graded, student=self.student, attempt_number=1,
            status=AssignmentSubmission.Status.GRADED, content="text", marks_awarded=8,
        )

    def test_admin_lists_all_assignments(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-assignments'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['count'], 2)

    def test_pending_submission_count_reflects_only_submitted_status(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-assignments'))
        needs_grading_row = next(r for r in res.data['results'] if r['title'] == 'Needs Grading')
        graded_row = next(r for r in res.data['results'] if r['title'] == 'Already Graded')
        self.assertEqual(needs_grading_row['pending_submission_count'], 1)
        self.assertEqual(graded_row['pending_submission_count'], 0)

    def test_course_and_module_context_included(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-assignments'))
        row = next(r for r in res.data['results'] if r['title'] == 'Needs Grading')
        self.assertEqual(row['course_id'], self.course.id)
        self.assertEqual(row['module_id'], self.module.id)

    def test_filter_by_needs_grading(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-assignments'), {'needs_grading': 'true'})
        self.assertEqual(res.data['count'], 1)
        self.assertEqual(res.data['results'][0]['title'], 'Needs Grading')

    def test_filter_by_course_id(self):
        other_course = _make_course("Other Assignment Dash Course")
        other_module = Module.objects.create(course=other_course, title="M1", order=1)
        Assignment.objects.create(module=other_module, title="Other Course Assignment", max_marks=10, order=1, is_published=True)

        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-assignments'), {'course_id': self.course.id})
        self.assertEqual(res.data['count'], 2)

    def test_student_cannot_access_admin_assignment_list(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.get(reverse('admin-assignments'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
