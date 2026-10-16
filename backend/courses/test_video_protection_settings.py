from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import VideoProtectionSetting

User = get_user_model()


class VideoProtectionSettingTests(APITestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="vps_super", password="password123", email="super@example.com"
        )
        self.staff_admin = User.objects.create_user(
            username="vps_staff", password="password123", email="staff@example.com", is_staff=True
        )
        self.student = User.objects.create_user(
            username="vps_student", password="password123", email="student@example.com"
        )
        self.url = reverse('video-protection-settings')

    def test_default_settings_instantiation(self):
        """get_settings() returns default configuration matching the security specification."""
        setting = VideoProtectionSetting.get_settings()
        self.assertTrue(setting.watermark_enabled)
        self.assertFalse(setting.download_enabled)
        self.assertTrue(setting.screen_capture_protection_enabled)
        self.assertTrue(setting.moving_watermark_enabled)
        self.assertEqual(setting.watermark_position_interval, 15)

    def test_singleton_pattern(self):
        """Only one settings instance exists (pk=1); subsequent saves update the singleton."""
        setting1 = VideoProtectionSetting.get_settings()
        self.assertEqual(setting1.pk, 1)

        # Creating another instance forces pk=1
        new_instance = VideoProtectionSetting(watermark_enabled=False)
        new_instance.save()
        self.assertEqual(VideoProtectionSetting.objects.count(), 1)
        self.assertEqual(VideoProtectionSetting.objects.first().watermark_enabled, False)

    def test_position_interval_validation(self):
        """Interval must be between 5 and 300 seconds."""
        setting = VideoProtectionSetting.get_settings()
        setting.watermark_position_interval = 4
        with self.assertRaises(ValidationError):
            setting.clean()

        setting.watermark_position_interval = 301
        with self.assertRaises(ValidationError):
            setting.clean()

        setting.watermark_position_interval = 25
        setting.clean()  # Should not raise

    def test_get_settings_unauthenticated_fails(self):
        """Unauthenticated GET returns 401 Unauthorized."""
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_get_settings_authenticated_student_success(self):
        """Authenticated learner can read video protection settings."""
        self.client.force_authenticate(user=self.student)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('watermark_enabled', response.data)
        self.assertIn('screen_capture_protection_enabled', response.data)
        self.assertIn('moving_watermark_enabled', response.data)
        self.assertIn('watermark_position_interval', response.data)
        self.assertIn('download_enabled', response.data)

    def test_patch_settings_forbidden_for_student(self):
        """Learners cannot modify video protection settings (403 Forbidden)."""
        self.client.force_authenticate(user=self.student)
        response = self.client.patch(self.url, {'watermark_enabled': False})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_patch_settings_staff_success_and_auditing(self):
        """Staff user can update video protection settings; updated_by tracks the author."""
        self.client.force_authenticate(user=self.staff_admin)
        response = self.client.patch(self.url, {
            'watermark_position_interval': 20,
            'moving_watermark_enabled': False,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['watermark_position_interval'], 20)
        self.assertFalse(response.data['moving_watermark_enabled'])
        self.assertEqual(response.data['updated_by'], self.staff_admin.id)
        self.assertEqual(response.data['updated_by_username'], self.staff_admin.username)

        # Confirm persisted in database
        persisted = VideoProtectionSetting.get_settings()
        self.assertEqual(persisted.watermark_position_interval, 20)
        self.assertFalse(persisted.moving_watermark_enabled)
        self.assertEqual(persisted.updated_by, self.staff_admin)

    def test_patch_settings_validation_error(self):
        """Submitting invalid position interval returns 400 Bad Request."""
        self.client.force_authenticate(user=self.staff_admin)
        response = self.client.patch(self.url, {'watermark_position_interval': 2})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('watermark_position_interval', response.data)
