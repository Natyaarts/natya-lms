from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status
from users.models import OnboardingField

User = get_user_model()

class StudentOnboardingTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.student = User.objects.create_user(
            username="+919876543210",
            phone_number="+919876543210",
            password="testpassword123",
            is_student=True,
            is_teacher=False,
            is_staff=False,
            is_onboarded=False
        )

    def test_default_onboarding_fields_available(self):
        res = self.client.get('/api/users/onboarding-fields/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        field_names = [f['name'] for f in res.data]
        self.assertIn('student_type', field_names)
        self.assertIn('full_name', field_names)
        self.assertIn('phone_number', field_names)
        self.assertIn('email', field_names)
        self.assertIn('state', field_names)
        self.assertIn('district', field_names)
        self.assertIn('pincode', field_names)

    def test_incomplete_student_me_shows_not_onboarded(self):
        self.client.force_authenticate(user=self.student)
        res = self.client.get('/api/users/me/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['is_onboarded'])

    def test_save_profile_career_academy(self):
        self.client.force_authenticate(user=self.student)
        payload = {
            "student_type": "Career Academy",
            "full_name": "Aarav Sharma",
            "email": "aarav@example.com",
            "state": "Karnataka",
            "district": "Bengaluru Urban",
            "pincode": "560001",
        }
        res = self.client.post('/api/users/save-profile/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['is_onboarded'])
        self.assertEqual(res.data['student_type'], 'CAREER_ACADEMY')
        self.assertEqual(res.data['first_name'], 'Aarav')
        self.assertEqual(res.data['last_name'], 'Sharma')
        self.assertEqual(res.data['state'], 'Karnataka')
        self.assertEqual(res.data['district'], 'Bengaluru Urban')
        self.assertEqual(res.data['pincode'], '560001')

        # Verify DB
        self.student.refresh_from_db()
        self.assertEqual(self.student.student_type, User.StudentType.CAREER_ACADEMY)
        self.assertEqual(self.student.first_name, 'Aarav')
        self.assertEqual(self.student.last_name, 'Sharma')
        self.assertTrue(self.student.is_profile_complete)

        # Verify /api/users/me/
        me_res = self.client.get('/api/users/me/')
        self.assertEqual(me_res.status_code, status.HTTP_200_OK)
        self.assertTrue(me_res.data['is_onboarded'])
        self.assertEqual(me_res.data['student_type'], 'CAREER_ACADEMY')

    def test_save_profile_regular(self):
        self.client.force_authenticate(user=self.student)
        payload = {
            "student_type": "Regular",
            "full_name": "Priya Nair",
            "email": "priya@example.com",
            "state": "Kerala",
            "district": "Ernakulam",
            "pincode": "682001",
        }
        res = self.client.post('/api/users/save-profile/', payload, format='json')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['student_type'], 'REGULAR')
        self.assertEqual(res.data['first_name'], 'Priya')
        self.assertEqual(res.data['last_name'], 'Nair')
        self.assertTrue(res.data['is_onboarded'])

    def test_staff_user_is_always_complete(self):
        staff = User.objects.create_user(
            username="admin_user",
            password="testpassword123",
            is_staff=True,
            is_student=False
        )
        self.assertTrue(staff.is_profile_complete)

    def test_save_profile_unauthenticated_returns_401(self):
        res = self.client.post('/api/users/save-profile/', {"student_type": "Regular"}, format='json')
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

