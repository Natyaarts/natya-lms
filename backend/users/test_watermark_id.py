from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class StudentWatermarkIdTests(APITestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="wm_super", password="password123", email="super@example.com"
        )
        self.staff_admin = User.objects.create_user(
            username="wm_staff", password="password123", email="staff@example.com", is_staff=True
        )
        self.student = User.objects.create_user(
            username="wm_student",
            password="password123",
            email="student@example.com",
            first_name="Radha",
            last_name="Nair",
            phone_number="+919876543210",
            parent_name="Nair Senior",
            parent_phone="+919876543211",
            is_student=True,
        )
        self.other_student = User.objects.create_user(
            username="wm_other", password="password123", email="other@example.com"
        )

    def test_student_id_auto_generated_and_formatted(self):
        """A new user automatically receives a unique student_id formatted as NAT-XXXXXX."""
        self.assertTrue(self.student.student_id.startswith("NAT-"))
        self.assertEqual(len(self.student.student_id), 10)  # "NAT-" (4) + 6 hex chars = 10 chars
        self.assertTrue(self.student.student_id[4:].isalnum())

    def test_student_id_stable_on_subsequent_saves(self):
        """Saving an existing user retains the same student_id."""
        initial_id = self.student.student_id
        self.student.first_name = "Radhika"
        self.student.save()
        self.student.refresh_from_db()
        self.assertEqual(self.student.student_id, initial_id)

    def test_uniqueness_constraint_enforced(self):
        """Database uniqueness constraint prevents duplicate student_ids."""
        duplicate_candidate = self.student.student_id
        with self.assertRaises(IntegrityError):
            User.objects.create(
                username="duplicate_student",
                student_id=duplicate_candidate,
            )

    def test_get_or_create_student_id_backfill(self):
        """get_or_create_student_id generates and persists an ID if None."""
        User.objects.filter(id=self.student.id).update(student_id=None)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.student_id)

        generated_id = self.student.get_or_create_student_id()
        self.assertTrue(generated_id.startswith("NAT-"))
        self.student.refresh_from_db()
        self.assertEqual(self.student.student_id, generated_id)

    def test_current_user_me_endpoint_returns_student_id(self):
        """GET /api/users/me/ returns student_id for the authenticated user."""
        self.client.force_authenticate(user=self.student)
        response = self.client.get(reverse('current-user'))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data.get('student_id'), self.student.student_id)

    def test_custom_user_details_serializer_student_id_is_read_only(self):
        """User cannot modify their own student_id via PATCH /api/auth/user/."""
        self.client.force_authenticate(user=self.student)
        original_id = self.student.student_id
        response = self.client.patch('/api/auth/user/', {
            'student_id': 'NAT-HACKED99',
            'first_name': 'UpdatedRadha',
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.student.refresh_from_db()
        self.assertEqual(self.student.student_id, original_id)
        self.assertEqual(self.student.first_name, 'UpdatedRadha')

    def test_watermark_lookup_requires_authentication(self):
        """Unauthenticated call to watermark lookup is rejected with 401."""
        response = self.client.get(reverse('lookup-watermark') + f"?id={self.student.student_id}")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_watermark_lookup_forbidden_for_regular_students(self):
        """Ordinary students cannot use the watermark lookup endpoint (403)."""
        self.client.force_authenticate(user=self.student)
        response = self.client.get(reverse('lookup-watermark') + f"?id={self.student.student_id}")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_watermark_lookup_missing_id_parameter(self):
        """Staff call without query parameter returns 400 Bad Request."""
        self.client.force_authenticate(user=self.staff_admin)
        response = self.client.get(reverse('lookup-watermark'))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_watermark_lookup_not_found(self):
        """Staff lookup with non-existent watermark ID returns 404."""
        self.client.force_authenticate(user=self.staff_admin)
        response = self.client.get(reverse('lookup-watermark') + "?id=NAT-NONEXIST")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_watermark_lookup_success_returns_non_sensitive_data(self):
        """Staff lookup returns user identity information and does NOT expose sensitive data."""
        self.client.force_authenticate(user=self.staff_admin)
        response = self.client.get(reverse('lookup-watermark') + f"?id={self.student.student_id}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        self.assertEqual(data['student_id'], self.student.student_id)
        self.assertEqual(data['user_id'], self.student.id)
        self.assertEqual(data['username'], self.student.username)
        self.assertEqual(data['email'], self.student.email)
        self.assertEqual(data['phone_number'], self.student.phone_number)
        self.assertEqual(data['parent_name'], self.student.parent_name)
        self.assertEqual(data['parent_phone'], self.student.parent_phone)

        # Ensure no sensitive credentials or tokens are leaked
        self.assertNotIn('password', data)
        self.assertNotIn('token', data)
        self.assertNotIn('otp', data)
        self.assertNotIn('secret', data)
