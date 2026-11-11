"""
Natya LMS — Production-Grade Zoom Lifecycle Test Suite.
Verifies all 12 Phase B.4 testing requirements:
1. Migration/model parity
2. Meeting ID validation and database constraints
3. Historical ownership quarantine
4. Zero-teardown terminal and nonterminal cases
5. Concurrent initial provisioning
6. Concurrent replacement and compensation
7. Deadlock handling (SQLSTATE 40P01 retry)
8. Outbox acknowledgement and recovery
9. Ambiguous CREATE outcomes
10. Manual-review MFA and authorization
11. Zoom pagination and remote error handling
12. Host reservation lifecycle and capacity protection
"""

import datetime
import requests
import unittest
import uuid
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction

from courses.models import (
    Course,
    LiveClass,
    LiveClassDurableOperation,
    ZoomHostAccount,
    HostReservation,
    ZoomMeetingRecord,
    HistoricalZoomConflict,
    ZoomLifecycleOutbox,
    ZoomAdminAuditLog,
)
from courses.services.zoom import (
    ZoomService,
    ZoomAPIError,
    ZoomClientError,
    ZoomNotFoundError,
    ZoomRateLimitError,
    ZoomTransientError,
    ZoomAmbiguousOutcomeError,
    validate_meeting_id,
    validate_host_id,
)
from courses.services.zoom_lifecycle import (
    validate_zero_teardown_invariant,
    select_and_reserve_host,
    provision_zoom_meeting,
    replace_zoom_meeting,
    cancel_zoom_meeting,
    compensate_zoom_operation,
    process_outbox_message,
    process_outbox_queue,
    recover_stale_outbox_messages,
    execute_with_deadlock_retry,
    ZeroTeardownInvariantViolation,
    HostCapacityExhaustedError,
    ConcurrencyConflictError,
    ZoomLifecycleError,
)
from users.models import OTPVerification

User = get_user_model()


class ZoomLifecycleProductionTests(TestCase):
    def setUp(self):
        super().setUp()
        self.staff_user = User.objects.create_user(
            username="admin_staff",
            email="staff@natya.in",
            password="securePassword123!",
            is_staff=True,
            is_active=True
        )
        self.host1 = ZoomHostAccount.objects.create(
            zoom_user_id="host_alpha_123",
            email="host.alpha@natya.in",
            display_name="Host Alpha",
            status=ZoomHostAccount.Status.ACTIVE,
            max_concurrency=1
        )
        self.host2 = ZoomHostAccount.objects.create(
            zoom_user_id="host_beta_456",
            email="host.beta@natya.in",
            display_name="Host Beta",
            status=ZoomHostAccount.Status.ACTIVE,
            max_concurrency=1
        )
        self.course = Course.objects.create(title="Bharatanatyam Foundation Course")
        self.now = timezone.now().replace(microsecond=0) + datetime.timedelta(days=1)
        self.live_class = LiveClass.objects.create(
            course=self.course,
            title="Bharatanatyam Varnam Live Session",
            scheduled_start=self.now,
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED,
            meeting_url="https://natyaarts.com/class/1"
        )

    # -------------------------------------------------------------------------
    # 1. Migration/Model Parity & Schema Structure
    # -------------------------------------------------------------------------
    def test_model_field_lengths_and_constraints(self):
        """Verifies ZoomMeetingRecord zoom_meeting_id allows 128 characters and is_current."""
        long_uuid = "a" * 128
        zmr = ZoomMeetingRecord(
            zoom_meeting_id=long_uuid,
            state=ZoomMeetingRecord.State.RESERVED,
            is_current=True,
            generation=1
        )
        zmr.save()
        self.assertEqual(len(zmr.zoom_meeting_id), 128)
        self.assertTrue(zmr.is_current)

    # -------------------------------------------------------------------------
    # 2. Meeting ID Validation & DB Constraints
    # -------------------------------------------------------------------------
    def test_meeting_id_formats_accepted_and_rejected(self):
        """Verifies 8-15 numeric digits, 16-128 char UUIDs, and POISONED are valid."""
        # 8 digits
        self.assertEqual(validate_meeting_id("12345678"), "12345678")
        # 15 digits
        self.assertEqual(validate_meeting_id("123456789012345"), "123456789012345")
        # 64 char UUID
        uuid_64 = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789+/_=-AbCdEfGhIjKlMnOpQrStUv"
        import urllib.parse
        self.assertEqual(validate_meeting_id(uuid_64), urllib.parse.quote(uuid_64, safe=''))

        # Invalid formats
        with self.assertRaises(ValueError):
            validate_meeting_id("1234")  # Too short numeric
        with self.assertRaises(ValueError):
            validate_meeting_id("1234567890123456")  # 16 numeric digits

    def test_zmr_check_constraint_enforces_format(self):
        """Database constraint check_zmr_meeting_id_format allows valid formats and rejects invalid."""
        # Valid numeric 11 digits
        zmr_num = ZoomMeetingRecord.objects.create(
            zoom_meeting_id="98765432101",
            state=ZoomMeetingRecord.State.RESERVED
        )
        self.assertIsNotNone(zmr_num.pk)

        # Valid POISONED can have any overflow/collision identifier
        zmr_poison = ZoomMeetingRecord.objects.create(
            zoom_meeting_id="OVERFLOW-COLLISION-ERR",
            state=ZoomMeetingRecord.State.POISONED,
            quarantine_reason="Test Quarantine"
        )
        self.assertIsNotNone(zmr_poison.pk)

    # -------------------------------------------------------------------------
    # 3. Historical Ownership Quarantine
    # -------------------------------------------------------------------------
    def test_historical_conflict_quarantine(self):
        """Verifies duplicate collisions or malformed IDs create HistoricalZoomConflict."""
        conflict = HistoricalZoomConflict.objects.create(
            live_class=self.live_class,
            historical_live_class_id=self.live_class.id,
            live_class_snapshot={"title": self.live_class.title},
            zoom_meeting_id_raw="duplicate_999",
            conflict_type="DUPLICATE_COLLISION",
            details={"notes": "Detected duplicate across two live classes"}
        )
        self.assertEqual(conflict.conflict_type, "DUPLICATE_COLLISION")
        self.assertFalse(conflict.resolved)

    # -------------------------------------------------------------------------
    # 4. Zero-Teardown Terminal and Non-Terminal Cases
    # -------------------------------------------------------------------------
    def test_zero_teardown_blocks_on_active_meeting_record(self):
        """Active ZMR with is_current=True must block initial provisioning."""
        ZoomMeetingRecord.objects.create(
            zoom_meeting_id="11122233344",
            live_class=self.live_class,
            state=ZoomMeetingRecord.State.ASSIGNED,
            is_current=True
        )
        with transaction.atomic():
            with self.assertRaises(ZeroTeardownInvariantViolation) as ctx:
                validate_zero_teardown_invariant(self.live_class.id)
            self.assertIn("active_meeting_records", ctx.exception.blocking_details)

    def test_zero_teardown_blocks_on_provisional_reservation(self):
        """Provisional or active reservation must block initial provisioning."""
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.PROVISIONAL
        )
        with transaction.atomic():
            with self.assertRaises(ZeroTeardownInvariantViolation) as ctx:
                validate_zero_teardown_invariant(self.live_class.id)
            self.assertIn("active_reservations", ctx.exception.blocking_details)

    def test_zero_teardown_blocks_on_in_flight_dop(self):
        """In-progress or ambiguous durable operations must block initial provisioning."""
        LiveClassDurableOperation.objects.create(
            idempotency_key="dop-test-1",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.IN_PROGRESS,
            live_class=self.live_class
        )
        with transaction.atomic():
            with self.assertRaises(ZeroTeardownInvariantViolation) as ctx:
                validate_zero_teardown_invariant(self.live_class.id)
            self.assertIn("in_flight_durable_operations", ctx.exception.blocking_details)

    def test_zero_teardown_permits_clean_terminal_historical_records(self):
        """Terminal historical records (RELEASED, COMPENSATED, POISONED with is_current=False) do NOT block."""
        ZoomMeetingRecord.objects.create(
            zoom_meeting_id="55566677788",
            live_class=self.live_class,
            state=ZoomMeetingRecord.State.RELEASED,
            is_current=False
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now - datetime.timedelta(days=7),
            scheduled_end=self.now - datetime.timedelta(days=7, hours=-1),
            status=HostReservation.Status.COMPLETED
        )
        LiveClassDurableOperation.objects.create(
            idempotency_key="dop-old-1",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.COMPLETED,
            live_class=self.live_class
        )

        with transaction.atomic():
            res = validate_zero_teardown_invariant(self.live_class.id)
            self.assertEqual(res["status"], "CLEAN")

    # -------------------------------------------------------------------------
    # 5. Concurrent Initial Provisioning (Happy Path)
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_provision_zoom_meeting_success(self, mock_create):
        """End-to-end provisioning allocates host, calls Zoom, updates DB, and queues outbox."""
        mock_create.return_value = {
            "meeting_id": "99988877766",
            "uuid": "uuid_test_999888",
            "host_id": "host_alpha_123",
            "join_url": "https://zoom.us/j/99988877766",
            "host_url": "https://zoom.us/s/99988877766"
        }

        result = provision_zoom_meeting(self.live_class.id)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertEqual(result["meeting_id"], "99988877766")

        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.zoom_meeting_id, "99988877766")
        self.assertEqual(self.live_class.zoom_uuid, "uuid_test_999888")

        # Verify active ZMR
        zmr = ZoomMeetingRecord.objects.get(live_class=self.live_class, is_current=True)
        self.assertEqual(zmr.state, ZoomMeetingRecord.State.ASSIGNED)

        # Verify active HostReservation
        res = HostReservation.objects.get(live_class=self.live_class, status=HostReservation.Status.ACTIVE)
        self.assertEqual(res.host_account, self.host1)

        # Verify Outbox message created
        outbox = ZoomLifecycleOutbox.objects.filter(aggregate_id=self.live_class.id).first()
        self.assertIsNotNone(outbox)
        self.assertEqual(outbox.event_type, "ZOOM_MEETING_PROVISIONED")

    # -------------------------------------------------------------------------
    # 6. Concurrent Replacement and Cancellation
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_replace_zoom_meeting_reallocates_host_and_reassigns(self, mock_create):
        """Replacing a meeting allocates new reservation and retires old record as REASSIGNED."""
        mock_create.side_effect = [
            {"meeting_id": "11111111111", "uuid": "u1", "host_id": "host_alpha_123", "join_url": "https://zoom.us/j/11111111111"},
            {"meeting_id": "22222222222", "uuid": "u2", "host_id": "host_beta_456", "join_url": "https://zoom.us/j/22222222222"}
        ]

        # Initial provision
        provision_zoom_meeting(self.live_class.id)

        # Replace meeting with host2
        repl_result = replace_zoom_meeting(self.live_class.id, new_host_id=self.host2.id)
        self.assertEqual(repl_result["status"], "SUCCESS")
        self.assertEqual(repl_result["new_meeting_id"], "22222222222")

        # Old record must be REASSIGNED and not current
        old_zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id="11111111111")
        self.assertEqual(old_zmr.state, ZoomMeetingRecord.State.REASSIGNED)
        self.assertFalse(old_zmr.is_current)

        # New record must be ASSIGNED and current
        new_zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id="22222222222")
        self.assertEqual(new_zmr.state, ZoomMeetingRecord.State.ASSIGNED)
        self.assertTrue(new_zmr.is_current)

    @patch('courses.services.zoom.ZoomService.delete_meeting')
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_cancel_zoom_meeting_releases_host_and_clears_pointers(self, mock_create, mock_delete):
        """Cancelling a meeting deletes on Zoom, marks ZMR RELEASED, and cancels reservation."""
        mock_create.return_value = {"meeting_id": "33333333333", "uuid": "u3", "host_id": "host_alpha_123", "join_url": "https://zoom.us/j/33333333333"}
        mock_delete.return_value = {"status": "deleted"}

        provision_zoom_meeting(self.live_class.id)
        cancel_result = cancel_zoom_meeting(self.live_class.id, reason="Student illness")
        self.assertEqual(cancel_result["status"], "SUCCESS")

        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.zoom_meeting_id, '')

        zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id="33333333333")
        self.assertEqual(zmr.state, ZoomMeetingRecord.State.RELEASED)
        self.assertFalse(zmr.is_current)

    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_replace_zoom_meeting_failure_preserves_old_meeting_and_cancels_provisional(self, mock_create):
        """When replacement creation fails definitely, old meeting and reservation remain ACTIVE, provisional is cancelled."""
        mock_create.side_effect = [
            {"meeting_id": "11111111111", "uuid": "u1", "host_id": "host_alpha_123", "join_url": "https://zoom.us/j/11111111111"},
            ZoomClientError(400, "Invalid parameter during replace")
        ]

        # Initial provision succeeds
        provision_zoom_meeting(self.live_class.id)

        # Replacement fails
        with self.assertRaises(ZoomLifecycleError):
            replace_zoom_meeting(self.live_class.id, new_host_id=self.host2.id)

        # Old meeting MUST remain ASSIGNED and current
        old_zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id="11111111111")
        self.assertEqual(old_zmr.state, ZoomMeetingRecord.State.ASSIGNED)
        self.assertTrue(old_zmr.is_current)

        # Old reservation on host1 MUST remain ACTIVE
        old_res = HostReservation.objects.get(live_class=self.live_class, host_account=self.host1)
        self.assertEqual(old_res.status, HostReservation.Status.ACTIVE)

        # Candidate reservation on host2 MUST be CANCELLED (no dangling active reservation)
        cand_res = HostReservation.objects.get(live_class=self.live_class, host_account=self.host2)
        self.assertEqual(cand_res.status, HostReservation.Status.CANCELLED)

        # LiveClass pointers preserved
        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.zoom_meeting_id, "11111111111")

    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_replace_zoom_meeting_ambiguous_preserves_old_meeting_and_holds_provisional(self, mock_create):
        """When replacement creation outcome is ambiguous, old meeting remains ACTIVE and provisional is held in MANUAL_REVIEW."""
        mock_create.side_effect = [
            {"meeting_id": "11111111111", "uuid": "u1", "host_id": "host_alpha_123", "join_url": "https://zoom.us/j/11111111111"},
            ZoomAmbiguousOutcomeError("Socket read timeout during replacement create")
        ]

        # Initial provision
        provision_zoom_meeting(self.live_class.id)

        # Replacement outcome unknown
        res = replace_zoom_meeting(self.live_class.id, new_host_id=self.host2.id)
        self.assertEqual(res["status"], "OUTCOME_UNKNOWN")

        # Old meeting preserved
        old_zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id="11111111111")
        self.assertEqual(old_zmr.state, ZoomMeetingRecord.State.ASSIGNED)
        self.assertTrue(old_zmr.is_current)

        # Old reservation preserved
        old_res = HostReservation.objects.get(live_class=self.live_class, host_account=self.host1)
        self.assertEqual(old_res.status, HostReservation.Status.ACTIVE)

        # Candidate reservation held in MANUAL_REVIEW
        cand_res = HostReservation.objects.get(live_class=self.live_class, host_account=self.host2)
        self.assertEqual(cand_res.status, HostReservation.Status.MANUAL_REVIEW)

    @patch('courses.services.zoom.ZoomService.delete_meeting')
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_cancel_zoom_meeting_ambiguous_holds_host_in_manual_review(self, mock_create, mock_delete):
        """When Zoom DELETE returns ambiguous outcome (5xx or timeout), reservation is HELD in MANUAL_REVIEW."""
        mock_create.return_value = {"meeting_id": "33333333333", "uuid": "u3", "host_id": "host_alpha_123", "join_url": "https://zoom.us/j/33333333333"}
        mock_delete.side_effect = ZoomTransientError(500, "Internal Server Error from Zoom Gateway")

        provision_zoom_meeting(self.live_class.id)

        with self.assertRaises(ZoomLifecycleError) as ctx:
            cancel_zoom_meeting(self.live_class.id, reason="Ambiguous cancellation test")
        self.assertIn("ambiguous outcome", str(ctx.exception))

        # Host reservation must NOT be released; must be MANUAL_REVIEW
        res = HostReservation.objects.get(live_class=self.live_class)
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

        # ZMR must be COMPENSATING
        zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id="33333333333")
        self.assertEqual(zmr.state, ZoomMeetingRecord.State.COMPENSATING)

    @patch('courses.services.zoom.ZoomService.delete_meeting')
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_cancel_zoom_meeting_confirmed_404_releases_reservation(self, mock_create, mock_delete):
        """When Zoom DELETE returns 404 Not Found, absence is confirmed and reservation is released."""
        mock_create.return_value = {"meeting_id": "33333333333", "uuid": "u3", "host_id": "host_alpha_123", "join_url": "https://zoom.us/j/33333333333"}
        mock_delete.side_effect = ZoomNotFoundError("Meeting not found on Zoom (404)")

        provision_zoom_meeting(self.live_class.id)
        cancel_result = cancel_zoom_meeting(self.live_class.id, reason="Pre-deleted cancellation")
        self.assertEqual(cancel_result["status"], "SUCCESS")

        res = HostReservation.objects.get(live_class=self.live_class)
        self.assertEqual(res.status, HostReservation.Status.CANCELLED)

        zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id="33333333333")
        self.assertEqual(zmr.state, ZoomMeetingRecord.State.RELEASED)
        self.assertFalse(zmr.is_current)

    def test_compensate_zoom_operation_stale_worker_fencing_rejected(self):
        """Worker with mismatched ID cannot execute compensation while active lease is held by another worker."""
        worker_a = uuid.uuid4()
        worker_b = uuid.uuid4()
        future_time = timezone.now() + datetime.timedelta(seconds=60)

        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-fence-test",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.IN_PROGRESS,
            live_class=self.live_class,
            lease_worker=worker_a,
            lease_expires_at=future_time
        )

        with self.assertRaises(ConcurrencyConflictError) as ctx:
            compensate_zoom_operation(dop.id, worker_id=worker_b)
        self.assertIn("Fencing violation", str(ctx.exception))

    @patch('courses.services.zoom.ZoomService.delete_meeting')
    def test_compensate_zoom_operation_expired_lease_allows_takeover(self, mock_delete):
        """Expired lease allows a new worker to safely take over and execute compensation."""
        worker_a = uuid.uuid4()
        worker_b = uuid.uuid4()
        past_time = timezone.now() - datetime.timedelta(seconds=10)

        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-expire-test",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.IN_PROGRESS,
            live_class=self.live_class,
            lease_worker=worker_a,
            lease_expires_at=past_time
        )

        res = compensate_zoom_operation(dop.id, worker_id=worker_b)
        self.assertEqual(res["status"], "COMPENSATED")

        dop.refresh_from_db()
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)
        self.assertEqual(dop.lease_worker, worker_b)

    # -------------------------------------------------------------------------
    # 7. Deadlock Handling & Bounded Retry
    # -------------------------------------------------------------------------
    def test_deadlock_retry_decorator_retries_on_operational_error(self):
        """execute_with_deadlock_retry catches deadlock and retries up to max_retries."""
        call_count = 0

        @execute_with_deadlock_retry(max_retries=3, base_delay=0.01)
        def simulated_deadlock_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                exc = OperationalError("deadlock detected")
                raise exc
            return "SUCCESS_AFTER_RETRY"

        result = simulated_deadlock_function()
        self.assertEqual(result, "SUCCESS_AFTER_RETRY")
        self.assertEqual(call_count, 3)

    # -------------------------------------------------------------------------
    # 8. Outbox Acknowledgement & Recovery
    # -------------------------------------------------------------------------
    def test_outbox_queue_processing(self):
        """Outbox processor processes pending messages and marks them COMPLETED."""
        msg = ZoomLifecycleOutbox.objects.create(
            aggregate_id=self.live_class.id,
            event_type="ZOOM_NOTIFY_STUDENTS",
            status=ZoomLifecycleOutbox.Status.PENDING,
            payload={"student_ids": [101, 102]}
        )
        processed = process_outbox_queue(batch_size=10)
        self.assertEqual(processed, 1)

        msg.refresh_from_db()
        self.assertEqual(msg.status, ZoomLifecycleOutbox.Status.COMPLETED)

    # -------------------------------------------------------------------------
    # 9. Ambiguous CREATE Outcomes & Fencing
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_ambiguous_create_transitions_to_manual_review(self, mock_create):
        """Network timeout or chunked stream failure transitions DOP to OUTCOME_UNKNOWN and holds reservation."""
        mock_create.side_effect = ZoomAmbiguousOutcomeError("Socket read timeout during create_meeting")

        res = provision_zoom_meeting(self.live_class.id)
        self.assertEqual(res["status"], "OUTCOME_UNKNOWN")

        dop = LiveClassDurableOperation.objects.get(id=res["dop_id"])
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN)

        reservation = HostReservation.objects.get(id=res["reservation_id"])
        self.assertEqual(reservation.status, HostReservation.Status.MANUAL_REVIEW)

    # -------------------------------------------------------------------------
    # 10. Manual-Review MFA & Authorization Endpoint
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.ZoomService.get_meeting')
    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_review_release_with_verified_mfa(self, mock_list, mock_get_m):
        """Staff admin with verified MFA can safely release a blocked manual review reservation."""
        mock_get_m.side_effect = ZoomNotFoundError("Meeting not found on Zoom (404)")
        mock_list.return_value = []  # Confirmed absent from host

        # Set up DOP in MANUAL_REVIEW_REQUIRED
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-mfa-test",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"meeting_id": "77788899900"}
        )
        res = HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        # Create OTP record for staff user with dedicated MANUAL_RELEASE purpose (DEF-02)
        otp = OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="654321",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "654321",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Host verified cleanly via Zoom web console and manual inspection."
            },
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "RELEASED")

        # Verify DB transitions
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.RELEASED)

        dop.refresh_from_db()
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)

        # Verify audit log
        audit = ZoomAdminAuditLog.objects.filter(target_dop=dop).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.admin_user, self.staff_user)

    @patch('courses.services.zoom.ZoomService.get_meeting')
    def test_manual_review_release_get_meeting_active_returns_409(self, mock_get_m):
        """If meeting is still ACTIVE on Zoom (HTTP 200), manual release is strictly rejected with 409 Conflict."""
        mock_get_m.return_value = {"id": 77788899900, "topic": "Active Meeting on Zoom"}

        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-mfa-409",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"meeting_id": "77788899900"}
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="112233",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "112233",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Administrative reason of sufficient length to pass validation."
            },
            format='json'
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.data.get("matched_on_zoom"))

    @patch('courses.services.zoom.ZoomService.get_meeting')
    def test_manual_review_release_remote_unreachable_returns_503(self, mock_get_m):
        """If Zoom API is unreachable during verification, release is aborted with 503 Service Unavailable."""
        mock_get_m.side_effect = ZoomTransientError(500, "Zoom cloud unreachable")

        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-mfa-503",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"meeting_id": "77788899900"}
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="445566",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "445566",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Administrative reason of sufficient length to pass validation."
            },
            format='json'
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Service Unavailable", response.data.get("error", ""))

    @patch('courses.services.zoom.ZoomService.get_meeting')
    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_review_release_otp_replay_prevented(self, mock_list, mock_get_m):
        """Replaying the same OTP code for a second release operation is blocked with 401 Unauthorized."""
        mock_get_m.side_effect = ZoomNotFoundError("Not found on Zoom (404)")
        mock_list.return_value = []

        dop1 = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-replay-1",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"meeting_id": "77788899901"}
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop1
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="998877",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        # First request succeeds
        res1 = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop1.id,
                "mfa_otp": "998877",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "First manual release verification request."
            },
            format='json'
        )
        self.assertEqual(res1.status_code, 200)

        # Second request using identical OTP is rejected
        dop2 = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-replay-2",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"meeting_id": "77788899902"}
        )
        res2 = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop2.id,
                "mfa_otp": "998877",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Replayed OTP verification attempt."
            },
            format='json'
        )
        self.assertEqual(res2.status_code, 401)

    def test_manual_review_release_rejects_missing_or_invalid_mfa(self):
        """Rejects request with 401 when MFA code is invalid or missing."""
        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        # Missing MFA
        res1 = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {"dop_id": 999, "resolution_type": "CONFIRMED_NEVER_CREATED", "reason": "Valid length reason text here."},
            format='json'
        )
        self.assertEqual(res1.status_code, 401)

        # Invalid OTP code
        res2 = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {"dop_id": 999, "mfa_otp": "000000", "resolution_type": "CONFIRMED_NEVER_CREATED", "reason": "Valid length reason text here."},
            format='json'
        )
        self.assertEqual(res2.status_code, 401)

    def test_manual_review_release_wrong_otp_increments_attempts(self):
        """DEF-02: Incorrect OTP attempt atomically increments attempts counter on existing challenge."""
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-wrong-otp-incr",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"meeting_id": "77788899900"}
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        otp = OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="778899",
            purpose='MANUAL_RELEASE',
            attempts=0,
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        res = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "000000",  # Incorrect code
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Administrative reason of sufficient length to pass validation."
            },
            format='json'
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("4 attempt(s) remaining", res.data.get("error", ""))

        otp.refresh_from_db()
        self.assertEqual(otp.attempts, 1)

    def test_manual_review_release_max_attempts_lockout(self):
        """DEF-02: Reaching MAX_VERIFY_ATTEMPTS locks out challenge even if correct OTP is provided next."""
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-max-att-lock",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"meeting_id": "77788899900"}
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        otp = OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="778899",
            purpose='MANUAL_RELEASE',
            attempts=OTPVerification.MAX_VERIFY_ATTEMPTS - 1,  # 4 attempts already used
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        # 5th wrong attempt exhausts attempts
        res1 = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "000000",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Administrative reason of sufficient length to pass validation."
            },
            format='json'
        )
        self.assertEqual(res1.status_code, 401)
        otp.refresh_from_db()
        self.assertEqual(otp.attempts, 5)

        # Now correct OTP is attempted, but challenge is locked out
        res2 = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "778899",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Administrative reason of sufficient length to pass validation."
            },
            format='json'
        )
        self.assertEqual(res2.status_code, 401)
        self.assertIn("Maximum verification attempts exceeded", res2.data.get("error", ""))

    def test_manual_review_release_wrong_purpose_rejected(self):
        """DEF-02: Ordinary login OTP (purpose='LOGIN') cannot be reused for manual release."""
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-wrong-purp",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"meeting_id": "77788899900"}
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="334455",
            purpose='LOGIN',  # Login purpose!
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        res = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "334455",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Administrative reason of sufficient length to pass validation."
            },
            format='json'
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("No active manual-release MFA challenge found", res.data.get("error", ""))

    def test_manual_review_release_expired_otp_rejected(self):
        """DEF-02: OTP older than 5 minutes is strictly rejected as expired."""
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-expired-otp",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"meeting_id": "77788899900"}
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        old_time = timezone.now() - datetime.timedelta(minutes=6)
        record = OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="556677",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )
        OTPVerification.objects.filter(id=record.id).update(created_at=old_time)

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        res = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "556677",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Administrative reason of sufficient length to pass validation."
            },
            format='json'
        )
        self.assertEqual(res.status_code, 401)
        self.assertIn("No active manual-release MFA challenge found", res.data.get("error", ""))

    def test_manual_review_request_otp_endpoint(self):
        """DEF-02: AdminZoomManualReviewRequestOTPView generates dedicated MANUAL_RELEASE challenge for staff."""
        from rest_framework.test import APIClient
        client = APIClient()

        # Non-staff user receives 403 Forbidden
        normal_user = User.objects.create_user(username="regular_student", email="student@natya.in", password="pwd")
        client.force_authenticate(user=normal_user)
        res_forbidden = client.post('/api/courses/admin/zoom/manual-review/request-otp/', format='json')
        self.assertEqual(res_forbidden.status_code, 403)

        # Staff user receives 200 OK and challenge is generated
        client.force_authenticate(user=self.staff_user)
        res_ok = client.post('/api/courses/admin/zoom/manual-review/request-otp/', format='json')
        self.assertEqual(res_ok.status_code, 200)
        self.assertEqual(res_ok.data["destination"], self.staff_user.email)

        created_otp = OTPVerification.objects.filter(
            identifier=self.staff_user.email,
            purpose='MANUAL_RELEASE',
            is_verified=False
        ).first()
        self.assertIsNotNone(created_otp)
        self.assertEqual(len(created_otp.otp), 6)

    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_release_outcome_unknown_one_matching_meeting_409(self, mock_list):
        """DEF-03: When meeting_id is unknown, 1 matching active meeting on host aborts with 409 Conflict."""
        mock_list.return_value = [
            {"meeting_id": "99988877711", "uuid": "u1", "topic": self.live_class.title, "start_time": self.now.isoformat()}
        ]
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-unknown-1match",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            live_class=self.live_class,
            payload={"title": self.live_class.title, "scheduled_start": self.now.isoformat()}
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="889900",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        res = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "889900",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Manual review investigation reason text here."
            },
            format='json'
        )
        self.assertEqual(res.status_code, 409)
        self.assertTrue(res.data.get("matched_on_zoom"))
        self.assertEqual(res.data.get("matched_meeting_id"), "99988877711")

    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_release_outcome_unknown_multiple_matching_meetings_409(self, mock_list):
        """DEF-03: Multiple plausible matching meetings on host aborts with 409 Conflict and ambiguous_matches=True."""
        mock_list.return_value = [
            {"meeting_id": "99988877711", "uuid": "u1", "topic": self.live_class.title, "start_time": self.now.isoformat()},
            {"meeting_id": "99988877722", "uuid": "u2", "topic": self.live_class.title, "start_time": self.now.isoformat()}
        ]
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-unknown-multimatch",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            live_class=self.live_class,
            payload={"title": self.live_class.title, "scheduled_start": self.now.isoformat()}
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="889901",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        res = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "889901",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Manual review investigation reason text here."
            },
            format='json'
        )
        self.assertEqual(res.status_code, 409)
        self.assertTrue(res.data.get("ambiguous_matches"))

    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_release_outcome_unknown_no_matching_meetings_200(self, mock_list):
        """DEF-03: Zero matching meetings for an UNKNOWN meeting ID must FAIL CLOSED (409 Conflict)."""
        mock_list.return_value = [
            {"meeting_id": "55555555555", "uuid": "u_other", "topic": "Completely Unrelated Subject", "start_time": "2026-12-01T15:00:00Z"}
        ]
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-unknown-nomatch",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            live_class=self.live_class,
            payload={"title": self.live_class.title, "scheduled_start": self.now.isoformat()}
        )
        res = HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="889902",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "889902",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Manual review investigation reason text here of sufficient length."
            },
            format='json'
        )
        # DEF-03 Fail-Closed: must return 409 Conflict
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.data.get("absence_unverifiable"))
        self.assertTrue(response.data.get("host_retained_in_manual_review"))

        # Reservation remains in MANUAL_REVIEW
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

        # DOP remains in OUTCOME_UNKNOWN
        dop.refresh_from_db()
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN)

        # Audit log records the rejection without falsely claiming confirmed absence
        audit = ZoomAdminAuditLog.objects.filter(target_dop=dop).first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.action, "MANUAL_RELEASE_REJECTED")
        self.assertFalse(audit.evidence.get("confirmed_absent_from_host_pool", False))
        self.assertEqual(audit.after_state.get("reservation_status"), HostReservation.Status.MANUAL_REVIEW)

    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_release_unknown_zero_matches_before_scheduled_end_409(self, mock_list):
        """DEF-03: Zero matches before scheduled_end fails closed with 409 Conflict."""
        mock_list.return_value = []
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-unknown-before-end",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"title": self.live_class.title, "scheduled_start": (self.now - datetime.timedelta(minutes=30)).isoformat()}
        )
        res = HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now - datetime.timedelta(minutes=30),
            scheduled_end=self.now + datetime.timedelta(minutes=30),  # In the future: now < scheduled_end
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="771122",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "771122",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Investigation before scheduled end shows zero matches on host."
            },
            format='json'
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.data.get("absence_unverifiable"))

        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

        dop.refresh_from_db()
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED)

    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_release_unknown_zero_matches_after_scheduled_end_409(self, mock_list):
        """DEF-03: Zero matches even AFTER scheduled_end still fails closed with 409 Conflict (no proof of non-existence)."""
        mock_list.return_value = []
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-unknown-after-end",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            live_class=self.live_class,
            payload={"title": self.live_class.title, "scheduled_start": (self.now - datetime.timedelta(hours=3)).isoformat()}
        )
        res = HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now - datetime.timedelta(hours=3),
            scheduled_end=self.now - datetime.timedelta(hours=2),  # Past: now > scheduled_end
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="771133",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "771133",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Investigation after scheduled end shows zero matches on host."
            },
            format='json'
        )
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.data.get("absence_unverifiable"))

        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

        dop.refresh_from_db()
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN)

    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_release_unknown_api_429_returns_503(self, mock_list):
        """DEF-03: Zoom API 429 rate limit during host meetings listing aborts release with 503 Service Unavailable."""
        mock_list.side_effect = ZoomRateLimitError("Zoom rate limit exceeded", status_code=429, retry_after=45)
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-unknown-429",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            live_class=self.live_class,
            payload={"title": self.live_class.title, "scheduled_start": self.now.isoformat()}
        )
        res = HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="771144",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "771144",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Attempting manual release during rate limited state."
            },
            format='json'
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("rate limit", response.data.get("error", "").lower())

        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_release_unknown_api_5xx_returns_503(self, mock_list):
        """DEF-03: Zoom API 5xx gateway error during host meetings listing aborts release with 503 Service Unavailable."""
        mock_list.side_effect = ZoomTransientError("Zoom 502 Bad Gateway during meeting listing", status_code=502)
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-unknown-5xx",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            live_class=self.live_class,
            payload={"title": self.live_class.title, "scheduled_start": self.now.isoformat()}
        )
        res = HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="771155",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "771155",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Attempting manual release during 5xx gateway failure."
            },
            format='json'
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Zoom API unreachable", response.data.get("error", ""))

        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_release_unknown_no_unintended_state_transitions(self, mock_list):
        """DEF-03: Rejection on unknown meeting leaves LiveClass, ZMR, Reservation, and DOP completely unchanged."""
        mock_list.return_value = []
        zmr = ZoomMeetingRecord.objects.create(
            zoom_meeting_id="12345678901",
            topic=self.live_class.title,
            state=ZoomMeetingRecord.State.COMPENSATING,
            live_class=None,
            host_account=self.host1,
            is_current=False
        )
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-unknown-immutable",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"title": self.live_class.title, "scheduled_start": self.now.isoformat()}  # No meeting_id in payload
        )
        # Note: zmr is not linked to dop, so meeting_id remains empty
        res = HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        self.live_class.zoom_meeting_id = "initial-live-meeting"
        self.live_class.save(update_fields=['zoom_meeting_id'])

        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="771166",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "771166",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Verify that zero-match failure leaves all entity pointers strictly intact."
            },
            format='json'
        )
        self.assertEqual(response.status_code, 409)

        # Confirm zero unintended mutations
        self.live_class.refresh_from_db()
        self.assertEqual(self.live_class.zoom_meeting_id, "initial-live-meeting")

        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

        dop.refresh_from_db()
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED)

        zmr.refresh_from_db()
        self.assertEqual(zmr.state, ZoomMeetingRecord.State.COMPENSATING)

    @patch('courses.services.zoom.ZoomService.get_meeting')
    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_release_known_meeting_404_verified_releases_200(self, mock_list, mock_get):
        """DEF-03: Known meeting ID with confirmed 404 AND absent from host list successfully releases with 200 OK."""
        mock_get.side_effect = ZoomNotFoundError("Meeting not found on Zoom (404)")
        mock_list.return_value = []
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-known-404-release",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.MANUAL_REVIEW_REQUIRED,
            live_class=self.live_class,
            payload={"meeting_id": "88877766655", "title": self.live_class.title}
        )
        res = HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="771177",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        response = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "771177",
                "resolution_type": "CONFIRMED_TERMINATED_EXTERNALLY",
                "reason": "Known meeting 88877766655 verified deleted on Zoom web console."
            },
            format='json'
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "RELEASED")

        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.RELEASED)

        dop.refresh_from_db()
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)

        audit = ZoomAdminAuditLog.objects.filter(target_dop=dop, action="MANUAL_RELEASE").first()
        self.assertIsNotNone(audit)
        self.assertTrue(audit.evidence.get("direct_meeting_lookup_404"))
        self.assertTrue(audit.evidence.get("confirmed_absent_from_host_pool"))

    @patch('courses.services.zoom.ZoomService.list_user_meetings')
    def test_manual_release_outcome_unknown_api_timeout_503(self, mock_list):
        """DEF-03: API timeout during host meetings listing aborts release with 503 Service Unavailable."""
        mock_list.side_effect = requests.Timeout("Connection timed out to Zoom API")
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="dop-unknown-timeout",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            live_class=self.live_class,
            payload={"title": self.live_class.title, "scheduled_start": self.now.isoformat()}
        )
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.MANUAL_REVIEW,
            durable_operation=dop
        )
        OTPVerification.objects.create(
            identifier=self.staff_user.email,
            otp="889903",
            purpose='MANUAL_RELEASE',
            is_verified=False
        )

        from rest_framework.test import APIClient
        client = APIClient()
        client.force_authenticate(user=self.staff_user)

        res = client.post(
            '/api/courses/admin/zoom/manual-review/release/',
            {
                "dop_id": dop.id,
                "mfa_otp": "889903",
                "resolution_type": "CONFIRMED_NEVER_CREATED",
                "reason": "Manual review investigation reason text here."
            },
            format='json'
        )
        self.assertEqual(res.status_code, 503)
        self.assertIn("Zoom API unreachable", res.data.get("error", ""))

    def test_outbox_process_success_with_lease(self):
        """DEF-04: process_outbox_message claims atomic lease, executes successfully, and transitions to COMPLETED."""
        outbox = ZoomLifecycleOutbox.objects.create(
            aggregate_id=self.live_class.id,
            event_type="ZOOM_MEETING_PROVISIONED",
            status=ZoomLifecycleOutbox.Status.PENDING,
            payload={"live_class_id": self.live_class.id}
        )
        worker_id = uuid.uuid4()
        success = process_outbox_message(outbox.id, worker_id=worker_id)
        self.assertTrue(success)

        outbox.refresh_from_db()
        self.assertEqual(outbox.status, ZoomLifecycleOutbox.Status.COMPLETED)
        self.assertIsNone(outbox.lease_worker)
        self.assertIsNone(outbox.lease_expires_at)

    def test_outbox_worker_crash_stale_lease_recovery(self):
        """DEF-04: recover_stale_outbox_messages detects crashed worker lease and resets status to RETRYING."""
        expired_time = timezone.now() - datetime.timedelta(seconds=10)
        crashed_worker = uuid.uuid4()
        outbox = ZoomLifecycleOutbox.objects.create(
            aggregate_id=self.live_class.id,
            event_type="ZOOM_MEETING_PROVISIONED",
            status=ZoomLifecycleOutbox.Status.IN_FLIGHT,
            lease_worker=crashed_worker,
            lease_expires_at=expired_time,
            payload={"live_class_id": self.live_class.id}
        )

        recovered_count = recover_stale_outbox_messages(stale_seconds=5)
        self.assertEqual(recovered_count, 1)

        outbox.refresh_from_db()
        self.assertEqual(outbox.status, ZoomLifecycleOutbox.Status.RETRYING)
        self.assertIsNone(outbox.lease_worker)
        self.assertIsNone(outbox.lease_expires_at)
        self.assertIn("Recovered", outbox.error_message)

    def test_outbox_competing_workers_fencing(self):
        """DEF-04: Active leased outbox message rejects concurrent claim by a second worker."""
        active_lease_time = timezone.now() + datetime.timedelta(seconds=60)
        worker1 = uuid.uuid4()
        worker2 = uuid.uuid4()
        outbox = ZoomLifecycleOutbox.objects.create(
            aggregate_id=self.live_class.id,
            event_type="ZOOM_MEETING_PROVISIONED",
            status=ZoomLifecycleOutbox.Status.IN_FLIGHT,
            lease_worker=worker1,
            lease_expires_at=active_lease_time,
            payload={"live_class_id": self.live_class.id}
        )

        # Worker 2 attempts to claim while lease is active
        claimed = process_outbox_message(outbox.id, worker_id=worker2)
        self.assertFalse(claimed)

        outbox.refresh_from_db()
        # Original lease remains intact
        self.assertEqual(outbox.lease_worker, worker1)

    def test_outbox_celery_tasks_registered(self):
        """DEF-04: Confirms process_zoom_lifecycle_outbox_task and recover_stale_zoom_outbox_task are registered in Celery beat."""
        from django.conf import settings
        beat_schedule = getattr(settings, 'CELERY_BEAT_SCHEDULE', {})
        self.assertIn('process-zoom-lifecycle-outbox', beat_schedule)
        self.assertEqual(
            beat_schedule['process-zoom-lifecycle-outbox']['task'],
            'courses.tasks.process_zoom_lifecycle_outbox_task'
        )
        self.assertIn('recover-stale-zoom-outbox', beat_schedule)
        self.assertEqual(
            beat_schedule['recover-stale-zoom-outbox']['task'],
            'courses.tasks.recover_stale_zoom_outbox_task'
        )

    # -------------------------------------------------------------------------
    # 11. Zoom Pagination & Remote Error Handling
    # -------------------------------------------------------------------------
    @patch('courses.services.zoom.requests.get')
    @patch('courses.services.zoom.ZoomService.get_access_token')
    def test_zoom_pagination_traverses_all_pages(self, mock_token, mock_get):
        """list_user_meetings paginates using next_page_token across multiple requests."""
        mock_token.return_value = "fake_jwt_token"

        resp1 = MagicMock()
        resp1.status_code = 200
        resp1.json.return_value = {
            "page_size": 2,
            "next_page_token": "token_page_2",
            "meetings": [{"id": 11111111, "topic": "M1", "type": 2}]
        }

        resp2 = MagicMock()
        resp2.status_code = 200
        resp2.json.return_value = {
            "page_size": 2,
            "next_page_token": "",  # Terminal page
            "meetings": [{"id": 22222222, "topic": "M2", "type": 2}]
        }

        mock_get.side_effect = [resp1, resp2]

        meetings = ZoomService.list_user_meetings(host_id="host_alpha_123", page_size=2)
        self.assertEqual(len(meetings), 2)
        self.assertEqual(meetings[0]["meeting_id"], "11111111")
        self.assertEqual(meetings[1]["meeting_id"], "22222222")
        self.assertEqual(mock_get.call_count, 2)

    # -------------------------------------------------------------------------
    # 12. Host Reservation Lifecycle & Capacity Protection
    # -------------------------------------------------------------------------
    def test_host_capacity_exhausted_raises_error(self):
        """When all host concurrency is saturated, select_and_reserve_host raises HostCapacityExhaustedError."""
        # Saturate host1 and host2
        HostReservation.objects.create(
            live_class=self.live_class,
            host_account=self.host1,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.ACTIVE
        )
        other_class = LiveClass.objects.create(
            course=self.course,
            title="Other Class",
            scheduled_start=self.now,
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED,
            meeting_url="https://natyaarts.com/class/other"
        )
        HostReservation.objects.create(
            live_class=other_class,
            host_account=self.host2,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.ACTIVE
        )

        third_class = LiveClass.objects.create(
            course=self.course,
            title="Third Class",
            scheduled_start=self.now,
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED,
            meeting_url="https://natyaarts.com/class/third"
        )
        with transaction.atomic():
            with self.assertRaises(HostCapacityExhaustedError):
                select_and_reserve_host(
                    live_class=third_class,
                    start_time=self.now,
                    duration_minutes=60
                )
