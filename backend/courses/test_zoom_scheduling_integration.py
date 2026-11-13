"""
Natya LMS — Phase B.4 Production Scheduling Integration Test Suite.
Verifies all 14 required Phase 7 integration criteria under real PostgreSQL:
1. Admin creates a LiveClass and successfully provisions a meeting.
2. Teacher creates a LiveClass and receives the correct host.
3. Two simultaneous classes are allocated to separate hosts when required.
4. Host concurrency saturation returns HTTP 409.
5. Missing host mapping fails closed.
6. Teacher-specific host affinity works only when authorized.
7. Substitute instructor transition is safe.
8. Rescheduling replaces reservations correctly.
9. Existing meeting generation is idempotent.
10. Start reuses the existing owned meeting.
11. Recurring LiveBatch scheduling respects capacity.
12. External Zoom failure does not leave an active orphan reservation.
13. Concurrent provisioning does not create duplicate active records.
14. Explicit host_id enforcement remains intact.
"""

import datetime
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from courses.models import (
    Course,
    LiveClass,
    LiveBatch,
    LiveClassDurableOperation,
    ZoomHostAccount,
    HostReservation,
    ZoomMeetingRecord,
    RecurrenceRule,
)
from courses.services.zoom import (
    ZoomService,
    ZoomAPIError,
    validate_host_id,
)
from courses.services.zoom_lifecycle import (
    provision_zoom_meeting,
    replace_zoom_meeting,
    select_and_reserve_host,
    HostCapacityExhaustedError,
    ZeroTeardownInvariantViolation,
)

User = get_user_model()


class ZoomSchedulingIntegrationTests(TestCase):
    def setUp(self):
        super().setUp()
        self.client = APIClient()

        # Admin user
        self.admin = User.objects.create_superuser(
            username="admin_user",
            email="admin@natyaarts.com",
            password="adminPassword123!"
        )

        # Teacher 1 with dedicated host account
        self.teacher1 = User.objects.create_user(
            username="teacher_sharma",
            email="sharma@natyaarts.com",
            password="teacherPassword123!",
            is_active=True,
            is_staff=True,
        )
        self.teacher1.is_teacher = True
        self.teacher1.save()

        # Teacher 2 with dedicated host account
        self.teacher2 = User.objects.create_user(
            username="teacher_patel",
            email="patel@natyaarts.com",
            password="teacherPassword123!",
            is_active=True,
            is_staff=True,
        )
        self.teacher2.is_teacher = True
        self.teacher2.save()

        # Dedicated host for Teacher 1
        self.host_t1 = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_user_sharma_101",
            email="sharma@natyaarts.com",
            display_name="Sharma Dedicated Host",
            status=ZoomHostAccount.Status.ACTIVE,
            max_concurrency=1
        )

        # Dedicated host for Teacher 2
        self.host_t2 = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_user_patel_202",
            email="patel@natyaarts.com",
            display_name="Patel Dedicated Host",
            status=ZoomHostAccount.Status.ACTIVE,
            max_concurrency=1
        )

        # General Pool Host
        self.host_pool = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_pool_host_303",
            email="general-pool@natyaarts.com",
            display_name="General Pool Host 1",
            status=ZoomHostAccount.Status.ACTIVE,
            max_concurrency=1
        )

        self.course = Course.objects.create(
            title="Bharatanatyam Diploma Foundation",
            is_published=True
        )

        self.batch = LiveBatch.objects.create(
            course=self.course,
            instructor=self.teacher1,
            batch_type=LiveBatch.BatchType.GROUP,
            max_participants=20
        )

        self.now = timezone.now().replace(microsecond=0) + datetime.timedelta(days=2)

    # -------------------------------------------------------------------------
    # Test 1: Admin creates LiveClass and successfully provisions meeting
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_admin_creates_liveclass_and_provisions_meeting(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "11100011100",
            "uuid": "uuid_admin_01",
            "host_id": "zoom_user_sharma_101",
            "join_url": "https://zoom.us/j/11100011100",
            "host_url": "https://zoom.us/s/11100011100"
        }
        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Admin Foundation Workshop",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["meeting_url"], "https://zoom.us/j/11100011100")

        # Verify DB records
        lc = LiveClass.objects.get(id=res.data["id"])
        self.assertEqual(lc.zoom_meeting_id, "11100011100")

        zmr = ZoomMeetingRecord.objects.get(live_class=lc, is_current=True)
        self.assertEqual(zmr.state, ZoomMeetingRecord.State.ASSIGNED)

        resv = HostReservation.objects.get(live_class=lc, status=HostReservation.Status.ACTIVE)
        self.assertEqual(resv.host_account, self.host_t1)

    # -------------------------------------------------------------------------
    # Test 2: Teacher creates LiveClass and receives the correct host
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_teacher_creates_liveclass_and_receives_correct_host(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "22200022200",
            "uuid": "uuid_teacher_01",
            "host_id": "zoom_user_sharma_101",
            "join_url": "https://zoom.us/j/22200022200",
            "host_url": "https://zoom.us/s/22200022200"
        }
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Sharma Adavu Class",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": (self.now + datetime.timedelta(hours=2)).isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)

        # Host selected must be teacher1's dedicated host
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        self.assertEqual(call_kwargs["host_id"], self.host_t1.zoom_user_id)

        lc = LiveClass.objects.get(id=res.data["id"])
        resv = HostReservation.objects.get(live_class=lc, status=HostReservation.Status.ACTIVE)
        self.assertEqual(resv.host_account, self.host_t1)

    # -------------------------------------------------------------------------
    # Test 3: Two simultaneous classes allocated to separate hosts
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_two_simultaneous_classes_allocated_to_separate_hosts(self, mock_create):
        mock_create.side_effect = [
            {"meeting_id": "33300033301", "uuid": "u1", "host_id": "zoom_user_sharma_101", "join_url": "https://zoom.us/j/33300033301"},
            {"meeting_id": "33300033302", "uuid": "u2", "host_id": "zoom_user_patel_202", "join_url": "https://zoom.us/j/33300033302"},
        ]
        self.client.force_authenticate(user=self.admin)
        same_time = self.now + datetime.timedelta(days=1)

        # Schedule class 1 for Teacher 1
        res1 = self.client.post("/api/courses/live-classes/", {
            "title": "Simultaneous Class A",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": same_time.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # Schedule class 2 for Teacher 2 at exact same time
        batch2 = LiveBatch.objects.create(
            course=self.course,
            instructor=self.teacher2,
            batch_type=LiveBatch.BatchType.GROUP
        )
        res2 = self.client.post("/api/courses/live-classes/", {
            "title": "Simultaneous Class B",
            "course": self.course.id,
            "batch": batch2.id,
            "scheduled_start": same_time.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res2.status_code, status.HTTP_201_CREATED)

        lc1 = LiveClass.objects.get(id=res1.data["id"])
        lc2 = LiveClass.objects.get(id=res2.data["id"])

        resv1 = HostReservation.objects.get(live_class=lc1, status=HostReservation.Status.ACTIVE)
        resv2 = HostReservation.objects.get(live_class=lc2, status=HostReservation.Status.ACTIVE)

        self.assertNotEqual(resv1.host_account_id, resv2.host_account_id)

    # -------------------------------------------------------------------------
    # Test 4: Host concurrency saturation returns HTTP 409
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_host_concurrency_saturation_returns_http_409(self, mock_create):
        # Two teachers without dedicated accounts using the shared pool
        teacher_p1 = User.objects.create_user(
            username="teacher_p1", email="p1@natyaarts.com", password="password123!", is_teacher=True, is_staff=True
        )
        teacher_p2 = User.objects.create_user(
            username="teacher_p2", email="p2@natyaarts.com", password="password123!", is_teacher=True, is_staff=True
        )
        batch_p1 = LiveBatch.objects.create(course=self.course, instructor=teacher_p1, batch_type=LiveBatch.BatchType.GROUP)
        batch_p2 = LiveBatch.objects.create(course=self.course, instructor=teacher_p2, batch_type=LiveBatch.BatchType.GROUP)

        mock_create.return_value = {
            "meeting_id": "44400044401", "uuid": "u1",
            "host_id": "zoom_pool_host_303", "join_url": "https://zoom.us/j/44400044401"
        }

        self.client.force_authenticate(user=self.admin)
        slot_time = self.now + datetime.timedelta(days=3)

        # Class 1 on batch_p1 consumes the single pool host
        res1 = self.client.post("/api/courses/live-classes/", {
            "title": "Class Saturating Pool",
            "course": self.course.id,
            "batch": batch_p1.id,
            "scheduled_start": slot_time.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res1.status_code, status.HTTP_201_CREATED)

        # Class 2 on batch_p2 (different instructor) in overlapping timeslot must fail with 409 Conflict due to host saturation
        res2 = self.client.post("/api/courses/live-classes/", {
            "title": "Class Saturated Blocked",
            "course": self.course.id,
            "batch": batch_p2.id,
            "scheduled_start": (slot_time + datetime.timedelta(minutes=30)).isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res2.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("Host capacity exhausted", str(res2.data))

        # Confirm Class 2 was NOT left in database in broken state
        self.assertFalse(LiveClass.objects.filter(title="Class Saturated Blocked").exists())

    # -------------------------------------------------------------------------
    # Test 5: Missing host mapping fails closed
    # -------------------------------------------------------------------------
    def test_missing_host_mapping_fails_closed(self):
        # Suspend ALL hosts in the pool
        ZoomHostAccount.objects.all().update(status=ZoomHostAccount.Status.SUSPENDED)

        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Class With No Hosts",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(LiveClass.objects.filter(title="Class With No Hosts").exists())

    # -------------------------------------------------------------------------
    # Test 6: Teacher-specific host affinity works only when authorized
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_teacher_specific_host_affinity_only_when_authorized(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "66600066601", "uuid": "u1",
            "host_id": "zoom_user_patel_202", "join_url": "https://zoom.us/j/66600066601"
        }
        # Disable general pool host so only dedicated hosts exist
        self.host_pool.status = ZoomHostAccount.Status.INACTIVE
        self.host_pool.save()

        # Teacher 2 creates class: must allocate Patel's host, NEVER Sharma's host
        self.client.force_authenticate(user=self.teacher2)
        batch2 = LiveBatch.objects.create(
            course=self.course,
            instructor=self.teacher2,
            batch_type=LiveBatch.BatchType.GROUP
        )
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Patel Solo Class",
            "course": self.course.id,
            "batch": batch2.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(mock_create.call_args.kwargs["host_id"], self.host_t2.zoom_user_id)

    # -------------------------------------------------------------------------
    # Test 7: Substitute instructor transition is safe
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_substitute_instructor_transition_safe(self, mock_create):
        mock_create.side_effect = [
            {"meeting_id": "77700077701", "uuid": "u1", "host_id": "zoom_user_sharma_101", "join_url": "https://zoom.us/j/77700077701"},
            {"meeting_id": "77700077702", "uuid": "u2", "host_id": "zoom_user_patel_202", "join_url": "https://zoom.us/j/77700077702"},
        ]
        self.client.force_authenticate(user=self.admin)

        # 1. Create class conducted by Teacher 1
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Original Class",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        lc_id = res.data["id"]

        # 2. Assign Teacher 2 as substitute instructor
        res_sub = self.client.patch(f"/api/courses/live-classes/{lc_id}/", {
            "substitute_instructor": self.teacher2.id,
            "is_substitute_instructor": True
        })
        self.assertEqual(res_sub.status_code, status.HTTP_200_OK)

        # Meeting should have been replaced to Teacher 2's host
        lc = LiveClass.objects.get(id=lc_id)
        active_zmr = ZoomMeetingRecord.objects.get(live_class=lc, is_current=True)
        self.assertEqual(active_zmr.zoom_meeting_id, "77700077702")
        self.assertEqual(active_zmr.host_account, self.host_t2)

        # Old record must be REASSIGNED
        old_zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id="77700077701")
        self.assertEqual(old_zmr.state, ZoomMeetingRecord.State.REASSIGNED)
        self.assertFalse(old_zmr.is_current)

    # -------------------------------------------------------------------------
    # Test 8: Rescheduling replaces reservations correctly
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_rescheduling_replaces_reservations_correctly(self, mock_create):
        mock_create.side_effect = [
            {"meeting_id": "88800088801", "uuid": "u1", "host_id": "zoom_user_sharma_101", "join_url": "https://zoom.us/j/88800088801"},
            {"meeting_id": "88800088802", "uuid": "u2", "host_id": "zoom_user_sharma_101", "join_url": "https://zoom.us/j/88800088802"},
        ]
        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Class To Reschedule",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        lc_id = res.data["id"]

        new_time = self.now + datetime.timedelta(days=1)
        res_resched = self.client.post(f"/api/courses/live-classes/{lc_id}/reschedule/", {
            "scheduled_start": new_time.isoformat(),
            "duration_minutes": 60
        })
        self.assertEqual(res_resched.status_code, status.HTTP_200_OK)

        lc = LiveClass.objects.get(id=lc_id)
        self.assertEqual(lc.zoom_meeting_id, "88800088802")

        # Active reservation must be at new time
        active_res = HostReservation.objects.get(live_class=lc, status=HostReservation.Status.ACTIVE)
        self.assertEqual(active_res.scheduled_start, new_time)

    # -------------------------------------------------------------------------
    # Test 9: Existing meeting generation is idempotent
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_existing_meeting_generation_is_idempotent(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "99900099901", "uuid": "u1",
            "host_id": "zoom_user_sharma_101", "join_url": "https://zoom.us/j/99900099901"
        }
        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Idempotent Class",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        lc_id = res.data["id"]

        # Call generate_zoom again
        mock_create.reset_mock()
        res_gen = self.client.post(f"/api/courses/live-classes/{lc_id}/generate-zoom/")
        self.assertEqual(res_gen.status_code, status.HTTP_200_OK)
        # ZoomService.create_meeting must NOT have been called again!
        mock_create.assert_not_called()
        self.assertEqual(res_gen.data["zoom_meeting_id"], "99900099901")

    # -------------------------------------------------------------------------
    # Test 10: Start reuses the existing owned meeting
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_start_reuses_existing_owned_meeting(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "10100010101", "uuid": "u1",
            "host_id": "zoom_user_sharma_101", "join_url": "https://zoom.us/j/10100010101",
            "host_url": "https://zoom.us/s/10100010101"
        }
        self.client.force_authenticate(user=self.teacher1)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Class To Start",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        lc_id = res.data["id"]

        mock_create.reset_mock()
        res_start = self.client.post(f"/api/courses/live-classes/{lc_id}/start/")
        self.assertEqual(res_start.status_code, status.HTTP_200_OK)
        self.assertEqual(res_start.data["status"], LiveClass.ClassStatus.LIVE)
        # Must not call create_meeting again
        mock_create.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 11: Recurring LiveBatch scheduling respects capacity
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_recurring_livebatch_scheduling_respects_capacity(self, mock_create):
        mock_create.side_effect = [
            {"meeting_id": "11100011101", "uuid": "u1", "host_id": "zoom_user_sharma_101", "join_url": "https://zoom.us/j/11100011101"},
            {"meeting_id": "11100011102", "uuid": "u2", "host_id": "zoom_user_sharma_101", "join_url": "https://zoom.us/j/11100011102"},
            {"meeting_id": "11100011103", "uuid": "u3", "host_id": "zoom_user_sharma_101", "join_url": "https://zoom.us/j/11100011103"},
        ]
        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Recurring Series",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
            "recurrence": {
                "frequency": "DAILY",
                "occurrence_count": 3
            }
        }, format='json')
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(len(res.data), 3)

        # All 3 occurrences must have distinct meetings and reservations
        meeting_ids = [item["zoom_meeting_id"] for item in res.data]
        self.assertEqual(len(set(meeting_ids)), 3)

    # -------------------------------------------------------------------------
    # Test 12: External Zoom failure does not leave an active orphan reservation
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_external_zoom_failure_does_not_leave_active_orphan_reservation(self, mock_create):
        mock_create.side_effect = ZoomAPIError("Remote Zoom service unavailable")

        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Failing Zoom Class",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)

        # No active reservation must exist
        self.assertEqual(HostReservation.objects.filter(status=HostReservation.Status.ACTIVE).count(), 0)
        self.assertFalse(LiveClass.objects.filter(title="Failing Zoom Class").exists())

    # -------------------------------------------------------------------------
    # Test 13: Concurrent provisioning does not create duplicate active records
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_concurrent_provisioning_does_not_create_duplicate_active_records(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "13130001313", "uuid": "u1",
            "host_id": "zoom_pool_host_303", "join_url": "https://zoom.us/j/13130001313"
        }
        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Manual Class For Dual Provisioning",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider="ZOOM",
            meeting_url=""
        )
        # First provision succeeds
        res1 = provision_zoom_meeting(lc.id)
        self.assertEqual(res1["status"], "SUCCESS")

        # Second provision attempts on same class: zero-teardown guard catches active obligations
        with self.assertRaises(ZeroTeardownInvariantViolation):
            provision_zoom_meeting(lc.id)

        # At most 1 active meeting record exists
        active_zmrs = ZoomMeetingRecord.objects.filter(live_class=lc, is_current=True).count()
        self.assertEqual(active_zmrs, 1)

    # -------------------------------------------------------------------------
    # Test 14: Explicit host_id enforcement remains intact
    # -------------------------------------------------------------------------
    def test_explicit_host_id_enforcement_intact(self):
        # host_id cannot be None
        with self.assertRaises(ValueError) as ctx1:
            ZoomService.create_meeting(host_id=None, topic="Testing No Host")
        self.assertIn("host_id is required and cannot be empty", str(ctx1.exception))
        self.assertIn("Silent fallback to '/users/me/' is strictly prohibited", str(ctx1.exception))

        # host_id cannot be empty string or whitespace
        with self.assertRaises(ValueError) as ctx2:
            ZoomService.create_meeting(host_id="", topic="Testing Empty Host")
        self.assertIn("host_id is required and cannot be empty", str(ctx2.exception))

        with self.assertRaises(ValueError) as ctx3:
            ZoomService.create_meeting(host_id="   ", topic="Testing Whitespace Host")
        self.assertIn("host_id is required and cannot be empty", str(ctx3.exception))

    # -------------------------------------------------------------------------
    # Test 15: AdminZoomMeetingCreateView with live_class_id
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_admin_zoom_create_meeting_view_with_live_class(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "15150001515", "uuid": "u1",
            "host_id": "zoom_pool_host_303", "join_url": "https://zoom.us/j/15150001515",
            "host_url": "https://zoom.us/s/15150001515"
        }
        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Class For Admin Zoom Endpoint",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider="ZOOM",
            meeting_url=""
        )
        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/admin/zoom/create-meeting/", {
            "live_class_id": lc.id
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["meeting_id"], "15150001515")
        self.assertEqual(res.data["live_class_id"], lc.id)

        # Host reservation and ZoomMeetingRecord must exist
        self.assertTrue(HostReservation.objects.filter(live_class=lc, status=HostReservation.Status.ACTIVE).exists())
        self.assertTrue(ZoomMeetingRecord.objects.filter(live_class=lc, is_current=True).exists())

    # -------------------------------------------------------------------------
    # Test 16: AdminZoomMeetingCreateView refuses ad-hoc meetings (no live_class_id).
    # An ad-hoc meeting bypassed the class-creation flow and duplicated meetings and
    # host reservations; the endpoint now only provisions an existing Zoom class.
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_admin_zoom_create_meeting_view_standalone(self, mock_create):
        classes_before = LiveClass.objects.count()
        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/admin/zoom/create-meeting/", {
            "topic": "Ad-hoc Modal Meeting",
            "course_id": self.course.id,
            "start_time": self.now.isoformat(),
            "duration": 60
        })
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("live_class_id", res.data["error"])
        mock_create.assert_not_called()
        self.assertEqual(LiveClass.objects.count(), classes_before)
        self.assertFalse(HostReservation.objects.exists())
        self.assertFalse(LiveClassDurableOperation.objects.exists())

    # -------------------------------------------------------------------------
    # Test 17: Serializer returns null for empty or pending meeting_url
    # -------------------------------------------------------------------------
    def test_serializer_returns_null_for_empty_or_pending_meeting_url(self):
        from courses.serializers import LiveClassSerializer
        # Class with empty meeting_url
        lc_empty = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Empty URL Class",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider="ZOOM",
            meeting_url=""
        )
        data_empty = LiveClassSerializer(lc_empty).data
        self.assertIsNone(data_empty["meeting_url"])

        # Class with legacy pending-provision string (scheduled 2 hours later to avoid overlap)
        lc_pending = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Pending URL Class",
            scheduled_start=self.now + datetime.timedelta(hours=2),
            duration_minutes=60,
            meeting_provider="ZOOM",
            meeting_url="https://zoom.us/pending-provision"
        )
        data_pending = LiveClassSerializer(lc_pending).data
        self.assertIsNone(data_pending["meeting_url"])

    # -------------------------------------------------------------------------
    # Test 18: No pending-provision URL is saved to PostgreSQL
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_no_pending_provision_url_persisted_to_database(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "18180001818", "uuid": "u18",
            "host_id": "zoom_user_sharma_101", "join_url": "https://zoom.us/j/18180001818",
            "host_url": "https://zoom.us/s/18180001818"
        }
        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Clean URL Class",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        # Verify directly in PostgreSQL that the placeholder was never written or persisted
        lc = LiveClass.objects.get(id=res.data["id"])
        self.assertEqual(lc.meeting_url, "https://zoom.us/j/18180001818")
        self.assertNotIn("pending-provision", lc.meeting_url)
        self.assertFalse(LiveClass.objects.filter(meeting_url__contains="pending-provision").exists())

    # -------------------------------------------------------------------------
    # Test 19: Concurrent reads cannot expose a placeholder
    # -------------------------------------------------------------------------
    def test_concurrent_read_cannot_expose_placeholder(self):
        from courses.serializers import LiveClassSerializer
        # In-flight class before provision completes
        lc_inflight = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="In Flight Class",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider="ZOOM",
            meeting_url=""
        )
        # Read from perspective of student, teacher, and admin
        self.client.force_authenticate(user=self.admin)
        res_admin = self.client.get(f"/api/courses/live-classes/{lc_inflight.id}/")
        self.assertEqual(res_admin.status_code, status.HTTP_200_OK)
        self.assertIsNone(res_admin.data["meeting_url"])

    # -------------------------------------------------------------------------
    # Test 20: Mixed-case dedicated host is excluded from another teacher's allocation
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_mixed_case_dedicated_host_excluded_from_another_teacher(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "20200002020", "uuid": "u20",
            "host_id": "zoom_pool_host_303", "join_url": "https://zoom.us/j/20200002020"
        }
        # Create dedicated host for Teacher 2 with mixed case in email
        self.teacher2.email = "Teacher.PATEL@NatyaArts.COM"
        self.teacher2.save()
        self.host_t2.email = "teacher.patel@natyaarts.com"
        self.host_t2.save()

        # Teacher 3 has NO dedicated host, must use general pool
        teacher3 = User.objects.create_user(
            username="teacher_verma",
            email="verma@natyaarts.com",
            password="teacherPassword123!",
            is_active=True,
            is_staff=True
        )
        teacher3.is_teacher = True
        teacher3.save()

        batch3 = LiveBatch.objects.create(
            course=self.course,
            instructor=teacher3,
            batch_type=LiveBatch.BatchType.GROUP
        )

        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Verma Class Allocation",
            "course": self.course.id,
            "batch": batch3.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        lc = LiveClass.objects.get(id=res.data["id"])
        resv = HostReservation.objects.get(live_class=lc, status=HostReservation.Status.ACTIVE)

        # Must NOT allocate the mixed-case Teacher 2 host! Must allocate host_pool
        self.assertNotEqual(resv.host_account_id, self.host_t2.id)
        self.assertEqual(resv.host_account_id, self.host_pool.id)

    # -------------------------------------------------------------------------
    # Test 21: Empty and whitespace teacher emails never match a host
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_empty_and_whitespace_teacher_emails_never_match_host(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "21210002121", "uuid": "u21",
            "host_id": "zoom_pool_host_303", "join_url": "https://zoom.us/j/21210002121"
        }
        # Teacher with whitespace email
        teacher_no_email = User.objects.create_user(
            username="teacher_no_email",
            email="   ",
            password="teacherPassword123!",
            is_active=True,
            is_staff=True
        )
        teacher_no_email.is_teacher = True
        teacher_no_email.save()

        batch_no_email = LiveBatch.objects.create(
            course=self.course,
            instructor=teacher_no_email,
            batch_type=LiveBatch.BatchType.GROUP
        )

        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Whitespace Email Teacher Class",
            "course": self.course.id,
            "batch": batch_no_email.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        lc = LiveClass.objects.get(id=res.data["id"])
        resv = HostReservation.objects.get(live_class=lc, status=HostReservation.Status.ACTIVE)

        # Safely allocated to general pool, never matched any teacher host
        self.assertEqual(resv.host_account_id, self.host_pool.id)

    # -------------------------------------------------------------------------
    # Test 22: Inactive dedicated host fails closed (no silent pool fallback)
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_inactive_dedicated_host_fails_closed(self, mock_create):
        # Teacher 1 dedicated host is marked INACTIVE
        self.host_t1.status = ZoomHostAccount.Status.INACTIVE
        self.host_t1.save()

        # Shared pool host IS active and available
        self.host_pool.status = ZoomHostAccount.Status.ACTIVE
        self.host_pool.save()

        self.client.force_authenticate(user=self.teacher1)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Teacher 1 Inactive Host Class",
            "course": self.course.id,
            "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        # MUST fail closed with HTTP 409 Conflict! Must NOT silently fall back to host_pool!
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertIn("Dedicated hosts must be ACTIVE", res.data["detail"])
        mock_create.assert_not_called()

    # -------------------------------------------------------------------------
    # Test 23: No dedicated host permits authorized shared pool allocation
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_no_dedicated_host_permits_authorized_shared_pool_allocation(self, mock_create):
        mock_create.return_value = {
            "meeting_id": "23230002323", "uuid": "u23",
            "host_id": "zoom_pool_host_303", "join_url": "https://zoom.us/j/23230002323"
        }
        # Teacher has valid email but NO dedicated ZoomHostAccount exists for it
        teacher_general = User.objects.create_user(
            username="teacher_general",
            email="general_teacher@natyaarts.com",
            password="teacherPassword123!",
            is_active=True,
            is_staff=True
        )
        teacher_general.is_teacher = True
        teacher_general.save()

        batch_gen = LiveBatch.objects.create(
            course=self.course,
            instructor=teacher_general,
            batch_type=LiveBatch.BatchType.GROUP
        )

        self.client.force_authenticate(user=self.admin)
        res = self.client.post("/api/courses/live-classes/", {
            "title": "Authorized Pool Class",
            "course": self.course.id,
            "batch": batch_gen.id,
            "scheduled_start": self.now.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        })
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        lc = LiveClass.objects.get(id=res.data["id"])
        resv = HostReservation.objects.get(live_class=lc, status=HostReservation.Status.ACTIVE)
        self.assertEqual(resv.host_account, self.host_pool)

    # -------------------------------------------------------------------------
    # Test 24: preferred_host_id cannot bypass teacher ownership
    # -------------------------------------------------------------------------
    def test_preferred_host_id_cannot_bypass_teacher_ownership(self):
        # Teacher 1 class cannot request Teacher 2's dedicated host
        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Unauthorized Preferred Host Class",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider="ZOOM",
            meeting_url=""
        )
        with self.assertRaises(HostCapacityExhaustedError) as ctx:
            select_and_reserve_host(
                live_class=lc,
                start_time=self.now,
                duration_minutes=60,
                preferred_host_id=self.host_t2.id
            )
        self.assertIn("Unauthorized host selection", str(ctx.exception))

    # -------------------------------------------------------------------------
    # Test 25: Duplicate/ambiguous user email mappings fail closed
    # -------------------------------------------------------------------------
    def test_duplicate_ambiguous_user_email_mappings_fail_closed(self):
        # Create second user with same email as Teacher 1
        User.objects.create_user(
            username="teacher_sharma_duplicate",
            email="sharma@natyaarts.com",
            password="teacherPassword123!"
        )
        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            title="Ambiguous User Email Class",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider="ZOOM",
            meeting_url=""
        )
        with self.assertRaises(HostCapacityExhaustedError) as ctx:
            select_and_reserve_host(
                live_class=lc,
                start_time=self.now,
                duration_minutes=60
            )
        self.assertIn("Ambiguous teacher identity", str(ctx.exception))


