from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import (
    Course, LiveClass, LiveBatch, LiveBatchStudent, Attendance,
    AttendanceSession, AttendanceConfiguration
)

User = get_user_model()


class AttendanceSettingsUnitTests(APITestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="att_super", password="password123", email="super@example.com"
        )
        self.staff_admin = User.objects.create_user(
            username="att_staff", password="password123", email="staff@example.com", is_staff=True
        )
        self.instructor = User.objects.create_user(
            username="att_instructor", password="password123", email="inst@example.com", is_teacher=True
        )
        self.student = User.objects.create_user(
            username="att_student", password="password123", email="student@example.com", is_student=True
        )

        self.course = Course.objects.create(
            title="Bharatanatyam Foundations",
            description="Foundation course",
            is_published=True
        )
        self.batch = LiveBatch.objects.create(
            course=self.course,
            instructor=self.instructor,
            batch_type=LiveBatch.BatchType.GROUP
        )
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student)

        self.live_class = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            instructor=self.instructor,
            title="Adavus Practice Session",
            scheduled_start=timezone.now() + timezone.timedelta(days=1),
            duration_minutes=10,  # 10 minutes as per prompt example
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/1234567890"
        )

    def test_attendance_configuration_singleton(self):
        """AttendanceConfiguration enforces singleton pk=1 and defaults to 75%."""
        config1 = AttendanceConfiguration.get_solo()
        self.assertEqual(config1.pk, 1)
        self.assertEqual(config1.default_min_attendance_percentage, 75)
        self.assertEqual(AttendanceConfiguration.get_default_percentage(), 75)

        # Creating another instance with different value overwrites/enforces pk=1
        config2 = AttendanceConfiguration(default_min_attendance_percentage=80)
        config2.save()
        self.assertEqual(AttendanceConfiguration.objects.count(), 1)
        self.assertEqual(AttendanceConfiguration.get_default_percentage(), 80)

    def test_attendance_configuration_validation(self):
        """Validation ensures percentage is strictly between 1 and 100."""
        config = AttendanceConfiguration.get_solo()

        # Reject 0
        config.default_min_attendance_percentage = 0
        with self.assertRaises(ValidationError):
            config.clean()

        # Reject 101
        config.default_min_attendance_percentage = 101
        with self.assertRaises(ValidationError):
            config.clean()

        # Accept valid 1 and 100
        config.default_min_attendance_percentage = 1
        config.clean()
        config.default_min_attendance_percentage = 100
        config.clean()

    def test_live_class_percentage_override_validation(self):
        """LiveClass.min_attendance_percentage accepts 1-100 or null, rejecting out-of-range."""
        self.live_class.min_attendance_percentage = 0
        with self.assertRaises(ValidationError):
            self.live_class.clean()

        self.live_class.min_attendance_percentage = 105
        with self.assertRaises(ValidationError):
            self.live_class.clean()

        self.live_class.min_attendance_percentage = 90
        self.live_class.clean()

        self.live_class.min_attendance_percentage = None
        self.live_class.clean()

    def test_effective_threshold_inheritance(self):
        """Effective threshold defaults to global configuration and uses override when provided."""
        AttendanceConfiguration.objects.filter(pk=1).update(default_min_attendance_percentage=75)

        # When None, inherits global default (75)
        self.live_class.min_attendance_percentage = None
        self.live_class.save()
        self.assertEqual(self.live_class.get_effective_min_attendance_percentage(), 75)

        # When set, returns class override
        self.live_class.min_attendance_percentage = 85
        self.live_class.save()
        self.assertEqual(self.live_class.get_effective_min_attendance_percentage(), 85)

    def test_calculate_attendance_metrics_prompt_example(self):
        """
        Verify the exact prompt example:
        Class duration: 10 minutes.
        Configured threshold: 75%.
        Student attendance: 8 minutes (480 seconds).
        Calculated attendance: 80%.
        Suggested status: PRESENT.
        """
        self.live_class.duration_minutes = 10
        self.live_class.min_attendance_percentage = 75
        self.live_class.save()

        metrics = self.live_class.calculate_attendance_metrics(zoom_duration_seconds=8 * 60)
        self.assertEqual(metrics["attendance_percentage"], 80.0)
        self.assertEqual(metrics["suggested_status"], "PRESENT")
        self.assertEqual(metrics["effective_threshold"], 75)
        self.assertTrue(metrics["is_override"])
        self.assertEqual(metrics["scheduled_duration_minutes"], 10)

    def test_calculate_attendance_metrics_statuses(self):
        """Calculates correct suggested status for ABSENT (0%), LATE (< threshold), and PRESENT (>= threshold)."""
        self.live_class.duration_minutes = 60
        self.live_class.min_attendance_percentage = 70
        self.live_class.save()

        # 0 seconds attended -> 0%, ABSENT
        m_zero = self.live_class.calculate_attendance_metrics(0)
        self.assertEqual(m_zero["attendance_percentage"], 0.0)
        self.assertEqual(m_zero["suggested_status"], "ABSENT")

        # 30 minutes (1800s out of 3600s) -> 50.0% (< 70%), LATE
        m_late = self.live_class.calculate_attendance_metrics(1800)
        self.assertEqual(m_late["attendance_percentage"], 50.0)
        self.assertEqual(m_late["suggested_status"], "LATE")

        # 42 minutes (2520s out of 3600s) -> 70.0% (== 70%), PRESENT
        m_exact = self.live_class.calculate_attendance_metrics(2520)
        self.assertEqual(m_exact["attendance_percentage"], 70.0)
        self.assertEqual(m_exact["suggested_status"], "PRESENT")

        # 60 minutes (3600s out of 3600s) -> 100.0%, PRESENT
        m_full = self.live_class.calculate_attendance_metrics(3600)
        self.assertEqual(m_full["attendance_percentage"], 100.0)
        self.assertEqual(m_full["suggested_status"], "PRESENT")

    def test_multiple_zoom_sessions_aggregation(self):
        """Aggregates multiple Zoom sessions for the same student without double-counting."""
        now = timezone.now()
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student,
            status=Attendance.Status.ABSENT
        )

        # Session 1: 3 minutes (180 seconds)
        AttendanceSession.objects.create(
            attendance=att,
            session_id=f"{self.live_class.id}:{self.student.id}:s1",
            join_time=now - timezone.timedelta(minutes=10),
            leave_time=now - timezone.timedelta(minutes=7),
            duration_seconds=180,
            is_active=False
        )

        # Session 2: 5 minutes (300 seconds)
        AttendanceSession.objects.create(
            attendance=att,
            session_id=f"{self.live_class.id}:{self.student.id}:s2",
            join_time=now - timezone.timedelta(minutes=6),
            leave_time=now - timezone.timedelta(minutes=1),
            duration_seconds=300,
            is_active=False
        )

        total_sec = att.calculate_total_duration_seconds()
        self.assertEqual(total_sec, 480)  # 3m + 5m = 8m

        metrics = self.live_class.calculate_attendance_metrics(total_sec)
        self.assertEqual(metrics["attendance_percentage"], 80.0)
        self.assertEqual(metrics["suggested_status"], "PRESENT")

    def test_boundary_conditions_75_percent_threshold(self):
        """
        Verify exact prompt requirement #1:
        10-minute class (600s), 75% threshold:
        - 7m 29s (449s): below threshold -> LATE
        - 7m 30s (450s): exactly qualifies -> PRESENT
        - 8m 00s (480s): qualifies -> PRESENT
        """
        self.live_class.duration_minutes = 10
        self.live_class.min_attendance_percentage = 75
        self.live_class.save()

        # 7m 29s = 449 seconds -> 74.8% -> LATE
        m_7m29 = self.live_class.calculate_attendance_metrics(7 * 60 + 29)
        self.assertEqual(m_7m29["attendance_percentage"], 74.8)
        self.assertEqual(m_7m29["suggested_status"], "LATE")

        # 7m 30s = 450 seconds -> 75.0% -> PRESENT
        m_7m30 = self.live_class.calculate_attendance_metrics(7 * 60 + 30)
        self.assertEqual(m_7m30["attendance_percentage"], 75.0)
        self.assertEqual(m_7m30["suggested_status"], "PRESENT")

        # 8m 00s = 480 seconds -> 80.0% -> PRESENT
        m_8m = self.live_class.calculate_attendance_metrics(8 * 60)
        self.assertEqual(m_8m["attendance_percentage"], 80.0)
        self.assertEqual(m_8m["suggested_status"], "PRESENT")

    def test_percentage_capped_at_100_when_attendance_exceeds_class_duration(self):
        """
        Verify exact prompt requirement #2:
        10-minute class with 12 minutes of reported attendance:
        - Displayed percentage must not exceed 100%.
        """
        self.live_class.duration_minutes = 10
        self.live_class.min_attendance_percentage = 75
        self.live_class.save()

        # 12 minutes (720s) in a 10-minute (600s) class
        metrics = self.live_class.calculate_attendance_metrics(12 * 60)
        self.assertEqual(metrics["attendance_percentage"], 100.0)
        self.assertEqual(metrics["suggested_status"], "PRESENT")

    def test_multiple_overlapping_zoom_sessions_interval_union(self):
        """
        Verify exact prompt requirement #3:
        Count the union of attended time intervals, not the sum of overlapping intervals.
        Session 1: 10:00 to 10:08 (8 minutes = 480s)
        Session 2: 10:04 to 10:10 (6 minutes = 360s, overlapping by 4m)
        Sum is 840s (14m), but union is 600s (10m).
        """
        now = timezone.now()
        base_time = now - timezone.timedelta(minutes=20)
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student,
            status=Attendance.Status.ABSENT
        )

        # Session 1: base to base + 8m
        AttendanceSession.objects.create(
            attendance=att,
            session_id=f"{self.live_class.id}:{self.student.id}:overlap_1",
            join_time=base_time,
            leave_time=base_time + timezone.timedelta(minutes=8),
            duration_seconds=480,
            is_active=False
        )

        # Session 2: base + 4m to base + 10m
        AttendanceSession.objects.create(
            attendance=att,
            session_id=f"{self.live_class.id}:{self.student.id}:overlap_2",
            join_time=base_time + timezone.timedelta(minutes=4),
            leave_time=base_time + timezone.timedelta(minutes=10),
            duration_seconds=360,
            is_active=False
        )

        total_sec = att.calculate_total_duration_seconds()
        # Union must be exactly 10 minutes = 600 seconds (not sum 840s)
        self.assertEqual(total_sec, 600)

    def test_duplicate_identical_zoom_sessions_interval_union(self):
        """
        Verify that duplicate concurrent sessions (e.g. laptop and mobile identical timestamps)
        are unioned to the single interval, not doubled.
        """
        now = timezone.now()
        base_time = now - timezone.timedelta(minutes=15)
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student,
            status=Attendance.Status.ABSENT
        )

        # Two concurrent sessions with identical start and end times
        AttendanceSession.objects.create(
            attendance=att,
            session_id=f"{self.live_class.id}:{self.student.id}:dup_1",
            join_time=base_time,
            leave_time=base_time + timezone.timedelta(minutes=10),
            duration_seconds=600,
            is_active=False
        )
        AttendanceSession.objects.create(
            attendance=att,
            session_id=f"{self.live_class.id}:{self.student.id}:dup_2",
            join_time=base_time,
            leave_time=base_time + timezone.timedelta(minutes=10),
            duration_seconds=600,
            is_active=False
        )

        total_sec = att.calculate_total_duration_seconds()
        self.assertEqual(total_sec, 600)

    def test_global_threshold_inheritance_dynamic_update(self):
        """
        Verify exact prompt requirement #5:
        Changing the global threshold updates classes without an override.
        Classes with explicit overrides retain their own threshold.
        """
        AttendanceConfiguration.objects.filter(pk=1).update(default_min_attendance_percentage=75)

        class_no_override = LiveClass.objects.create(
            course=self.course,
            title="Class Without Override",
            scheduled_start=timezone.now() + timezone.timedelta(days=3),
            duration_minutes=60,
            min_attendance_percentage=None,
            meeting_url="https://zoom.us/j/999111"
        )
        class_with_override = LiveClass.objects.create(
            course=self.course,
            title="Class With Override",
            scheduled_start=timezone.now() + timezone.timedelta(days=3),
            duration_minutes=60,
            min_attendance_percentage=90,
            meeting_url="https://zoom.us/j/999222"
        )

        self.assertEqual(class_no_override.get_effective_min_attendance_percentage(), 75)
        self.assertEqual(class_with_override.get_effective_min_attendance_percentage(), 90)

        # Update global default from 75 to 80
        config = AttendanceConfiguration.get_solo()
        config.default_min_attendance_percentage = 80
        config.save()

        # Class without override dynamically reflects new global threshold (80)
        self.assertEqual(class_no_override.get_effective_min_attendance_percentage(), 80)
        # Class with override retains its own override (90)
        self.assertEqual(class_with_override.get_effective_min_attendance_percentage(), 90)

    def test_manual_admin_decisions_survive_recalculation(self):
        """
        Verify exact prompt requirement #6:
        PRESENT, LATE, ABSENT, and EXCUSED manual decisions survive recalculation.
        """
        now = timezone.now()
        for initial_status in [
            Attendance.Status.PRESENT,
            Attendance.Status.LATE,
            Attendance.Status.ABSENT,
            Attendance.Status.EXCUSED
        ]:
            att = Attendance.objects.create(
                live_class=self.live_class,
                student=self.student,
                status=initial_status,
                is_manual_override=True
            )
            # Add session that would otherwise suggest a different status
            AttendanceSession.objects.create(
                attendance=att,
                session_id=f"{self.live_class.id}:{self.student.id}:status_{initial_status}",
                join_time=now - timezone.timedelta(minutes=10),
                leave_time=now,
                duration_seconds=600,
                is_active=False
            )
            # Recalculate duration
            total_sec = att.calculate_total_duration_seconds()
            metrics = self.live_class.calculate_attendance_metrics(total_sec)

            # Manual status in database MUST remain untouched
            att.refresh_from_db()
            self.assertEqual(att.status, initial_status)
            self.assertTrue(att.is_manual_override)

            att.sessions.all().delete()
            att.delete()

    def test_manual_duration_override_survives_recalculation(self):
        """
        Verify exact prompt requirement #7:
        Manual duration override is respected and survives session calculation.
        """
        now = timezone.now()
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student,
            status=Attendance.Status.PRESENT,
            duration_minutes=5,  # Admin explicitly set duration to 5m
            is_manual_override=True,
            is_duration_manual_override=True
        )
        # Even if raw sessions show 10m
        AttendanceSession.objects.create(
            attendance=att,
            session_id=f"{self.live_class.id}:{self.student.id}:dur_override",
            join_time=now - timezone.timedelta(minutes=10),
            leave_time=now,
            duration_seconds=600,
            is_active=False
        )
        from courses.serializers import AttendanceSerializer
        data = AttendanceSerializer(att).data
        # Serializer should use the manual override (5m = 300s / 600s = 50.0%)
        self.assertEqual(data["attendance_percentage"], 50.0)
        self.assertEqual(data["suggested_status"], "LATE")


class AttendanceSettingsAPITests(APITestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username="api_super", password="password123", email="apisuper@example.com"
        )
        self.staff_admin = User.objects.create_user(
            username="api_staff", password="password123", email="apistaff@example.com", is_staff=True
        )
        self.instructor = User.objects.create_user(
            username="api_instructor", password="password123", email="apiinst@example.com", is_teacher=True
        )
        self.other_instructor = User.objects.create_user(
            username="api_other_inst", password="password123", email="otherinst@example.com", is_teacher=True
        )
        self.student = User.objects.create_user(
            username="api_student", password="password123", email="apistudent@example.com", is_student=True
        )

        self.course = Course.objects.create(
            title="Kathak Advanced",
            description="Kathak course",
            is_published=True
        )
        self.batch = LiveBatch.objects.create(
            course=self.course,
            instructor=self.instructor,
            batch_type=LiveBatch.BatchType.GROUP
        )
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student)

        self.live_class = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            instructor=self.instructor,
            title="Kathak Live Workshop",
            scheduled_start=timezone.now() + timezone.timedelta(days=2),
            duration_minutes=20,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/1234567890"
        )
        self.global_settings_url = reverse('attendance-settings')
        self.threshold_url = reverse('live-class-attendance-threshold', kwargs={'pk': self.live_class.pk})

    def test_global_attendance_settings_permissions(self):
        """Only authenticated superusers and staff admins can view and modify global settings."""
        # Anonymous -> 401
        res = self.client.get(self.global_settings_url)
        self.assertEqual(res.status_code, status.HTTP_401_UNAUTHORIZED)

        # Student -> 403
        self.client.force_authenticate(user=self.student)
        res = self.client.get(self.global_settings_url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Teacher (non-staff) -> 403
        self.client.force_authenticate(user=self.instructor)
        res = self.client.get(self.global_settings_url)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

        # Staff admin -> 200
        self.client.force_authenticate(user=self.staff_admin)
        res = self.client.get(self.global_settings_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['default_min_attendance_percentage'], 75)

    def test_global_attendance_settings_update(self):
        """Staff admin can update global default percentage, rejecting invalid bounds."""
        self.client.force_authenticate(user=self.staff_admin)

        # Reject out of bounds (< 1)
        res = self.client.patch(self.global_settings_url, {"default_min_attendance_percentage": 0})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Reject out of bounds (> 100)
        res = self.client.patch(self.global_settings_url, {"default_min_attendance_percentage": 105})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Valid update (80%)
        res = self.client.patch(self.global_settings_url, {"default_min_attendance_percentage": 80})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data['default_min_attendance_percentage'], 80)
        self.assertEqual(res.data['updated_by'], self.staff_admin.id)

    def test_per_class_attendance_threshold_api(self):
        """Instructors and admins can view, set, and clear per-class overrides."""
        self.client.force_authenticate(user=self.instructor)

        # GET initially returns no override
        res = self.client.get(self.threshold_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['is_override'])
        self.assertIsNone(res.data['min_attendance_percentage'])

        # Set custom override: 90%
        res = self.client.patch(self.threshold_url, {"min_attendance_percentage": 90})
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertTrue(res.data['is_override'])
        self.assertEqual(res.data['min_attendance_percentage'], 90)
        self.assertEqual(res.data['effective_min_attendance_percentage'], 90)

        # Reject invalid override (> 100)
        res = self.client.patch(self.threshold_url, {"min_attendance_percentage": 105})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

        # Clear override (null) to inherit global default
        res = self.client.patch(self.threshold_url, {"min_attendance_percentage": None}, format="json")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.data['is_override'])
        self.assertIsNone(res.data['min_attendance_percentage'])

    def test_live_attendance_metrics_and_admin_authority_preservation(self):
        """
        Verify live_attendance endpoint provides:
        1. Effective threshold banner data.
        2. Attendance percentage and suggested status for each student.
        3. Never mutates or overwrites manual attendance decisions made by the administrator.
        """
        # Create an attendance record manually marked ABSENT by admin
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student,
            status=Attendance.Status.ABSENT,
            is_manual_override=True,
            notes="Manually marked absent initially"
        )
        now = timezone.now()
        # Student attended 18 minutes out of 20 scheduled (90%)
        AttendanceSession.objects.create(
            attendance=att,
            session_id=f"{self.live_class.id}:{self.student.id}:sess1",
            join_time=now - timezone.timedelta(minutes=18),
            leave_time=now,
            duration_seconds=18 * 60,
            is_active=False
        )

        self.client.force_authenticate(user=self.instructor)
        live_att_url = reverse('live-class-live-attendance', kwargs={'pk': self.live_class.pk})
        res = self.client.get(live_att_url)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        # Verify threshold metadata
        self.assertEqual(res.data['scheduled_duration'], 20)
        self.assertIn('effective_threshold', res.data)
        self.assertIn('global_default_threshold', res.data)

        # Verify student metrics
        student_row = next(s for s in res.data['students'] if s['student_id'] == self.student.id)
        self.assertEqual(student_row['attendance_percentage'], 90.0)
        self.assertEqual(student_row['suggested_status'], "PRESENT")

        # CRITICAL BUSINESS INVARIANT: The stored status MUST REMAIN ABSENT (never mutated by suggestion)
        self.assertEqual(student_row['status'], Attendance.Status.ABSENT)
        att.refresh_from_db()
        self.assertEqual(att.status, Attendance.Status.ABSENT)
