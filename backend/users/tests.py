from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status

from .models import Mentorship

User = get_user_model()


class MentorshipTests(APITestCase):
    """
    Phase 1: explicit, persistent student<->mentor relationship -- NOT
    derived from course Enrollment. See ARCHITECTURE_PROPOSAL.md Phase 1
    and users/models.py Mentorship docstring.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser(username="p1_admin", password="pw")
        self.mentor1 = User.objects.create_user(username="p1_mentor1", password="pw", is_mentor=True, is_student=False)
        self.mentor2 = User.objects.create_user(username="p1_mentor2", password="pw", is_mentor=True, is_student=False)
        self.student1 = User.objects.create_user(username="p1_student1", password="pw")
        self.student2 = User.objects.create_user(username="p1_student2", password="pw")
        self.plain_teacher = User.objects.create_user(username="p1_teacher_not_mentor", password="pw", is_teacher=True, is_student=False)

        self.list_url = reverse('mentorship-list')

    def _detail_url(self, pk):
        return reverse('mentorship-detail', kwargs={'pk': pk})

    # --- Item 7: Admin can manage (create) mentorships ---
    def test_admin_can_create_mentorship(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.list_url, {
            "student": self.student1.id,
            "mentor": self.mentor1.id,
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(Mentorship.objects.count(), 1)
        m = Mentorship.objects.first()
        self.assertEqual(m.assigned_by, self.admin)
        self.assertEqual(m.status, Mentorship.Status.ACTIVE)

    # --- Item 9: Duplicate ACTIVE mentor assignment prevented ---
    def test_duplicate_active_mentorship_prevented(self):
        self.client.force_authenticate(user=self.admin)
        Mentorship.objects.create(student=self.student1, mentor=self.mentor1, assigned_by=self.admin)
        res = self.client.post(self.list_url, {
            "student": self.student1.id,
            "mentor": self.mentor1.id,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Mentorship.objects.filter(student=self.student1, mentor=self.mentor1).count(), 1)

    def test_reassignment_after_deactivation_is_allowed_and_preserves_history(self):
        self.client.force_authenticate(user=self.admin)
        old = Mentorship.objects.create(student=self.student1, mentor=self.mentor1, assigned_by=self.admin)
        old.status = Mentorship.Status.INACTIVE
        old.save()

        res = self.client.post(self.list_url, {"student": self.student1.id, "mentor": self.mentor1.id})
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # Old row still exists (history preserved), plus the new active one.
        self.assertEqual(Mentorship.objects.filter(student=self.student1, mentor=self.mentor1).count(), 2)
        self.assertTrue(Mentorship.objects.filter(pk=old.pk).exists())

    def test_only_mentor_role_users_can_be_assigned_as_mentor(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(self.list_url, {
            "student": self.student1.id,
            "mentor": self.plain_teacher.id,  # is_teacher=True but is_mentor=False
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_student_cannot_create_mentorship(self):
        self.client.force_authenticate(user=self.student1)
        res = self.client.post(self.list_url, {"student": self.student1.id, "mentor": self.mentor1.id})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_mentor_cannot_create_mentorship_for_themselves(self):
        self.client.force_authenticate(user=self.mentor1)
        res = self.client.post(self.list_url, {"student": self.student1.id, "mentor": self.mentor1.id})
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    # --- Item 5: Mentor can access (read) their assigned students ---
    def test_mentor_can_see_assigned_students(self):
        Mentorship.objects.create(student=self.student1, mentor=self.mentor1, assigned_by=self.admin)
        self.client.force_authenticate(user=self.mentor1)
        res = self.client.get(self.list_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        student_ids = [row['student'] for row in res.data]
        self.assertIn(self.student1.id, student_ids)

    # --- Item 6: Mentor cannot access unrelated students ---
    def test_mentor_cannot_see_unrelated_mentorships(self):
        Mentorship.objects.create(student=self.student1, mentor=self.mentor1, assigned_by=self.admin)
        Mentorship.objects.create(student=self.student2, mentor=self.mentor2, assigned_by=self.admin)
        self.client.force_authenticate(user=self.mentor1)
        res = self.client.get(self.list_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        student_ids = [row['student'] for row in res.data]
        self.assertIn(self.student1.id, student_ids)
        self.assertNotIn(self.student2.id, student_ids)

    def test_student_sees_only_their_own_mentorships(self):
        Mentorship.objects.create(student=self.student1, mentor=self.mentor1, assigned_by=self.admin)
        Mentorship.objects.create(student=self.student2, mentor=self.mentor2, assigned_by=self.admin)
        self.client.force_authenticate(user=self.student1)
        res = self.client.get(self.list_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res.data), 1)
        self.assertEqual(res.data[0]['student'], self.student1.id)

    def test_mentor_role_is_distinct_from_teacher_role(self):
        # A mentor must never be treated as a teacher just because both
        # roles exist -- explicit requirement from ARCHITECTURE_PROPOSAL.md.
        self.assertFalse(self.mentor1.is_teacher)
        self.assertTrue(self.mentor1.is_mentor)
        self.assertFalse(self.plain_teacher.is_mentor)
        self.assertTrue(self.plain_teacher.is_teacher)


class AdminRoleAndPrivilegeEscalationTests(APITestCase):
    """
    Phase 1 (second pass): ADMIN (is_staff, non-superuser) gets the same
    administrative access as SUPER ADMIN (is_superuser) -- but must never be
    able to grant is_superuser/is_staff to anyone, including themselves.
    """

    def setUp(self):
        self.superadmin = User.objects.create_superuser(username="p1c_superadmin", password="pw")
        self.admin = User.objects.create_user(username="p1c_admin", password="pw", is_staff=True, is_student=False)
        self.teacher = User.objects.create_user(username="p1c_teacher", password="pw", is_teacher=True, is_student=False)
        self.student = User.objects.create_user(username="p1c_student", password="pw")

    def test_admin_can_list_users(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-user-list'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_admin_can_view_admin_stats(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-stats'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

    def test_teacher_cannot_access_admin_user_management(self):
        self.client.force_authenticate(user=self.teacher)
        res = self.client.get(reverse('admin-user-list'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_student_cannot_access_admin_user_management(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.get(reverse('admin-user-list'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_cannot_grant_superuser_to_new_user(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(reverse('admin-user-list'), {
            "username": "p1c_escalation_attempt",
            "is_student": True,
            "is_superuser": True,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(username="p1c_escalation_attempt").exists())

    def test_admin_cannot_grant_staff_to_new_user(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(reverse('admin-user-list'), {
            "username": "p1c_escalation_attempt2",
            "is_student": True,
            "is_staff": True,
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(User.objects.filter(username="p1c_escalation_attempt2").exists())

    def test_admin_cannot_promote_existing_user_to_staff(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.patch(reverse('admin-user-detail', kwargs={'pk': self.teacher.pk}), {"is_staff": True})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.teacher.refresh_from_db()
        self.assertFalse(self.teacher.is_staff)

    def test_superadmin_can_grant_staff(self):
        self.client.force_authenticate(user=self.superadmin)
        res = self.client.patch(reverse('admin-user-detail', kwargs={'pk': self.teacher.pk}), {"is_staff": True})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.is_staff)

    def test_admin_can_create_ordinary_student(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(reverse('admin-user-list'), {
            "username": "p1c_new_student",
            "is_student": True,
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)


class TeacherMentorProfileTests(APITestCase):
    """Phase 1 (second pass): TeacherProfile / MentorProfile self-service + admin management."""

    def setUp(self):
        self.admin = User.objects.create_superuser(username="p1d_admin", password="pw")
        self.teacher = User.objects.create_user(username="p1d_teacher", password="pw", is_teacher=True, is_student=False)
        self.mentor = User.objects.create_user(username="p1d_mentor", password="pw", is_mentor=True, is_student=False)
        self.student = User.objects.create_user(username="p1d_student", password="pw")

    def test_teacher_can_view_own_profile_lazily_created(self):
        self.client.force_authenticate(user=self.teacher)
        res = self.client.get(reverse('my-teacher-profile'))
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        from .models import TeacherProfile
        self.assertTrue(TeacherProfile.objects.filter(user=self.teacher).exists())

    def test_teacher_can_update_own_profile(self):
        self.client.force_authenticate(user=self.teacher)
        res = self.client.patch(reverse('my-teacher-profile'), {"bio": "20 years teaching Bharatanatyam"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['bio'], "20 years teaching Bharatanatyam")

    def test_student_cannot_access_teacher_profile_endpoint(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.get(reverse('my-teacher-profile'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_mentor_can_view_and_update_own_profile(self):
        self.client.force_authenticate(user=self.mentor)
        res = self.client.patch(reverse('my-mentor-profile'), {"availability_status": "BUSY"})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['availability_status'], "BUSY")

    def test_teacher_cannot_access_mentor_profile_endpoint(self):
        self.client.force_authenticate(user=self.teacher)
        res = self.client.get(reverse('my-mentor-profile'))
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_view_and_edit_any_teacher_profile(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.patch(
            reverse('admin-user-teacher-profile', kwargs={'pk': self.teacher.pk}),
            {"specialization": "Kathak"}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['specialization'], "Kathak")

    def test_admin_cannot_create_teacher_profile_for_non_teacher(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(reverse('admin-user-teacher-profile', kwargs={'pk': self.student.pk}))
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
