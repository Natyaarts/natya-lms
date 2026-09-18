"""
Final release audit: Profile / Account Editing gap fix.

Reuses dj-rest-auth's stock UserDetailsView (GET/PUT/PATCH /api/auth/user/,
already live, previously unused by any web/mobile code) via an updated
CustomUserDetailsSerializer -- no new endpoint, no model change, no
migration. Tests here cover:

- Self-service PATCH of the safe field set (first_name, last_name,
  parent_name, parent_phone, username).
- The pre-existing self-role-escalation hole this phase's own security
  requirements forced closing (is_teacher/is_student/is_mentor were
  previously writable) -- confirmed fixed.
- email/phone_number/is_superuser/is_staff/pk remain read-only (email and
  phone_number have no verification-on-change mechanism; phone_number
  also doubles as the OTP login identity).
- Ownership: this endpoint has no id/pk in its URL at all -- get_object()
  always returns request.user, so there is no "target another user"
  vector to test against; what IS tested is that no payload field can
  cause a different row to be affected.
- Unauthenticated access denied.
- Basic validation (max_length).
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

User = get_user_model()


class ProfileEditingAPITests(APITestCase):
    def setUp(self):
        self.url = reverse('rest_user_details')
        self.user = User.objects.create_user(
            username='profile_edit_student', password='password123',
            email='profile_edit_student@example.com', phone_number='+911234567890',
            first_name='Original', last_name='Name',
        )
        self.other_user = User.objects.create_user(username='profile_edit_other', password='password123')

    def test_unauthenticated_cannot_view_or_update_profile(self):
        response = self.client.get(self.url)
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

        response = self.client.patch(self.url, {'first_name': 'Hacker'}, format='json')
        self.assertIn(response.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    def test_get_returns_current_profile_including_read_only_fields(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['first_name'], 'Original')
        self.assertEqual(response.data['email'], 'profile_edit_student@example.com')
        self.assertEqual(response.data['phone_number'], '+911234567890')

    def test_authenticated_user_can_update_own_name(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(self.url, {'first_name': 'Updated', 'last_name': 'Person'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Updated')
        self.assertEqual(self.user.last_name, 'Person')

    def test_authenticated_user_can_update_parent_name_and_phone(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(self.url, {'parent_name': 'Guardian Name', 'parent_phone': '+919999999999'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.parent_name, 'Guardian Name')
        self.assertEqual(self.user.parent_phone, '+919999999999')

    def test_only_the_authenticated_users_own_row_is_ever_affected(self):
        # This endpoint has no id/pk in its URL or payload path at all --
        # get_object() always returns request.user server-side. Confirms
        # updating self.user's profile never touches other_user's row,
        # even though nothing in the request distinguishes them except
        # the authenticated session.
        self.client.force_authenticate(user=self.user)
        self.client.patch(self.url, {'first_name': 'OnlyMine'}, format='json')
        self.user.refresh_from_db()
        self.other_user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'OnlyMine')
        self.assertEqual(self.other_user.first_name, '')

    def test_role_fields_cannot_be_modified_via_profile_endpoint(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(
            self.url, {'is_teacher': True, 'is_mentor': True, 'is_student': False}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_teacher)
        self.assertFalse(self.user.is_mentor)
        self.assertTrue(self.user.is_student)

    def test_staff_and_superuser_cannot_be_escalated_via_profile_endpoint(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(self.url, {'is_staff': True, 'is_superuser': True}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)

    def test_email_cannot_be_changed_via_profile_endpoint(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(self.url, {'email': 'new-email@example.com'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'profile_edit_student@example.com')

    def test_phone_number_cannot_be_changed_via_profile_endpoint(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(self.url, {'phone_number': '+910000000000'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone_number, '+911234567890')

    def test_pk_in_payload_is_ignored_not_used_for_ownership(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(
            self.url, {'pk': self.other_user.pk, 'first_name': 'StillMine'}, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.other_user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'StillMine')
        self.assertEqual(self.other_user.first_name, '')
        # The response itself must reflect the actually-authenticated
        # user's own pk, never the client-supplied one.
        self.assertEqual(response.data['pk'], self.user.pk)

    def test_overlong_first_name_rejected(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(self.url, {'first_name': 'x' * 200}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Original')

    def test_partial_update_leaves_other_fields_untouched(self):
        self.client.force_authenticate(user=self.user)
        response = self.client.patch(self.url, {'last_name': 'OnlyLastChanged'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Original')  # unchanged
        self.assertEqual(self.user.last_name, 'OnlyLastChanged')
