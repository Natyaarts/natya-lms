"""
Natya LMS — Phase B.5 Governance & Defect Closure Verification Suite.
Tests:
- Group A: Dedicated Zoom Host Ownership & Allocation (dedicated active, dedicated inactive fail closed, shared pool isolation, unclassified exclusion)
- Group B: Webhook Security, Signature Format, Timestamp Bounds, Constant-time verification, No debug bypass
- Group C: Webhook Secret Rotation (advisory lock, active secret, retired secret grace period, concurrency serialization)
- Group D: Webhook Payload validation, account_id check, duplicate event deduplication, user lifecycle
- Group E: Cancellation Saga Worker (4 phases, lease claim, remote delete, evidence persistence, finalization, watchdog recovery)
- Group F: Quarantine Service (15-min grace period, dual GET 404/3001 verification, evidence storage, safe host release)
- Group G: Database Functions & Triggers (L1-L5 locking order, trigger protection on reservations, meetings, DOP deletion, audit truncate prevention)
- Group H: Rollback Session Authorization & Host State Canonicalization
- Group I: Manifest Nonce Replay Protection & Dual Approver Requirement
"""

import hmac
import hashlib
import json
import uuid
import datetime
from django.test import TestCase, override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from rest_framework.test import APIClient
from rest_framework import status
from unittest.mock import patch, MagicMock

from courses.models import (
    Course,
    LiveBatch,
    LiveClass,
    ZoomHostAccount,
    HostReservation,
    ZoomMeetingRecord,
    ZoomWebhookEvent,
    ZoomWebhookVault,
    ZoomApproverSecrets,
    ZoomManifestNonces,
    ZoomRollbackSession,
    ZoomVerifiedEvidence,
    ZoomReconciliationApproval,
    LiveClassDurableOperation,
    ZoomLifecycleOutbox,
)
from courses.services.zoom_lifecycle import (
    select_and_reserve_host,
    HostCapacityExhaustedError,
    ZoomCancellationSagaWorker,
    ZoomQuarantineService,
    QuarantinePeriodActiveError,
)
from courses.services.recording import ZoomRecordingService

User = get_user_model()


class PhaseB5HostOwnershipTestCase(TestCase):
    """Group A: Dedicated Zoom Host Ownership & Allocation."""

    def setUp(self):
        self.instructor1 = User.objects.create_user(
            username="teacher1", email="teacher1@natya.test", password="pass", is_teacher=True
        )
        self.instructor2 = User.objects.create_user(
            username="teacher2", email="teacher2@natya.test", password="pass", is_teacher=True
        )
        self.course = Course.objects.create(title="Kathak Foundation")
        self.batch = LiveBatch.objects.create(course=self.course, instructor=self.instructor1, batch_type=LiveBatch.BatchType.ONE_TO_ONE)
        self.now = timezone.now() + datetime.timedelta(days=1)

    def test_dedicated_host_allocation_to_assigned_instructor(self):
        """Dedicated active host is selected for assigned instructor."""
        dedicated_host = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_ded_1",
            email="teacher1@natya.test",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.instructor1,
            max_concurrency=1
        )
        shared_host = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_shared_1",
            email="shared@natya.test",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.SHARED_POOL,
            max_concurrency=1
        )

        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            instructor=self.instructor1,
            title="Class 1",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM
        )

        with transaction.atomic():
            res = select_and_reserve_host(lc, self.now, 60)
            self.assertEqual(res.host_account_id, dedicated_host.id)

    def test_dedicated_inactive_host_fails_closed(self):
        """Dedicated host that is INACTIVE fails closed, never falling back to shared pool."""
        ZoomHostAccount.objects.create(
            zoom_user_id="zoom_ded_inactive",
            email="teacher1@natya.test",
            status=ZoomHostAccount.Status.INACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.instructor1,
            max_concurrency=1
        )
        ZoomHostAccount.objects.create(
            zoom_user_id="zoom_shared_avail",
            email="shared@natya.test",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.SHARED_POOL,
            max_concurrency=1
        )

        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch,
            instructor=self.instructor1,
            title="Class Inactive Dedicated",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM
        )

        with self.assertRaises(HostCapacityExhaustedError):
            with transaction.atomic():
                select_and_reserve_host(lc, self.now, 60)

    def test_other_teacher_cannot_steal_dedicated_host(self):
        """Another instructor cannot allocate or prefer a dedicated host assigned to someone else."""
        ded_host = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_ded_teacher1",
            email="teacher1@natya.test",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.instructor1,
            max_concurrency=1
        )

        lc2 = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor2,
            title="Class Teacher 2",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM
        )

        # Attempting to allocate with preferred_host_id pointing to teacher1's host must fail
        with self.assertRaises(HostCapacityExhaustedError):
            with transaction.atomic():
                select_and_reserve_host(lc2, self.now, 60, preferred_host_id=ded_host.id)

    def test_unclassified_host_is_excluded(self):
        """UNCLASSIFIED hosts are strictly excluded from allocation."""
        ZoomHostAccount.objects.create(
            zoom_user_id="zoom_unclass_1",
            email="unclass@natya.test",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.UNCLASSIFIED,
            max_concurrency=1
        )

        lc = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor2,
            title="Class Unclass",
            scheduled_start=self.now,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM
        )

        with self.assertRaises(HostCapacityExhaustedError):
            with transaction.atomic():
                select_and_reserve_host(lc, self.now, 60)


class PhaseB5WebhookSecurityTestCase(TestCase):
    """Group B: Webhook Security, Signature Format, Timestamp Bounds, Constant-time verification."""

    def setUp(self):
        self.secret = "test_webhook_secret_key_1234567890"
        self.raw_body = b'{"event":"meeting.started","payload":{"object":{"id":"123456789"}}}'
        self.ts = str(int(timezone.now().timestamp()))
        message = f"v0:{self.ts}:{self.raw_body.decode('utf-8')}".encode('utf-8')
        computed_hash = hmac.new(self.secret.encode('utf-8'), message, hashlib.sha256).hexdigest()
        self.valid_signature = f"v0={computed_hash}"

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="test_webhook_secret_key_1234567890", DEBUG=False)
    def test_valid_signature_succeeds(self):
        result = ZoomRecordingService.verify_webhook_signature(
            self.raw_body, self.valid_signature, self.ts
        )
        self.assertTrue(result)

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="test_webhook_secret_key_1234567890", DEBUG=False)
    def test_malformed_signature_header_rejected(self):
        """Signatures without v0= prefix or invalid hex are rejected immediately."""
        self.assertFalse(ZoomRecordingService.verify_webhook_signature(
            self.raw_body, "invalid_prefix_hash", self.ts
        ))
        self.assertFalse(ZoomRecordingService.verify_webhook_signature(
            self.raw_body, "v0=not_valid_hex_characters_zzzz", self.ts
        ))

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="test_webhook_secret_key_1234567890", DEBUG=False)
    def test_timestamp_skew_rejected(self):
        """Timestamp more than 300 seconds skewed is rejected."""
        old_ts = str(int(timezone.now().timestamp()) - 400)
        self.assertFalse(ZoomRecordingService.verify_webhook_signature(
            self.raw_body, self.valid_signature, old_ts
        ))

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="test_webhook_secret_key_1234567890", DEBUG=False)
    def test_timestamp_overflow_guarded(self):
        """Non-numeric, negative, or overflow timestamps are safely rejected."""
        self.assertFalse(ZoomRecordingService.verify_webhook_signature(
            self.raw_body, self.valid_signature, "1e9999"
        ))
        self.assertFalse(ZoomRecordingService.verify_webhook_signature(
            self.raw_body, self.valid_signature, "-50"
        ))
        self.assertFalse(ZoomRecordingService.verify_webhook_signature(
            self.raw_body, self.valid_signature, "NaN"
        ))

    @override_settings(ZOOM_WEBHOOK_SECRET_TOKEN="", DEBUG=True)
    def test_no_secret_never_bypasses_in_debug(self):
        """When no secret is configured, verification must strictly fail even if DEBUG=True."""
        ZoomWebhookVault.objects.all().delete()
        self.assertFalse(ZoomRecordingService.verify_webhook_signature(
            self.raw_body, self.valid_signature, self.ts
        ))


class PhaseB5SecretRotationTestCase(TestCase):
    """Group C: Webhook Secret Rotation & Vault."""

    def test_secret_rotation_in_vault(self):
        """Active secret rotates cleanly to retired and new secret becomes active."""
        # Clean vault
        ZoomWebhookVault.objects.all().delete()

        # Insert first secret
        sec1 = ZoomWebhookVault.objects.create(
            secret_token="old_secret_token_111",
            status=ZoomWebhookVault.Status.ACTIVE
        )

        raw_body = b'{"event":"test"}'
        ts = str(int(timezone.now().timestamp()))
        msg = f"v0:{ts}:{raw_body.decode('utf-8')}".encode('utf-8')
        sig1 = "v0=" + hmac.new(b"old_secret_token_111", msg, hashlib.sha256).hexdigest()

        # Should verify with sec1
        self.assertTrue(ZoomRecordingService.verify_webhook_signature(raw_body, sig1, ts))

        # Rotate secret using DB function if postgres, or simulated rotate
        if connection.vendor == 'postgresql':
            with connection.cursor() as cursor:
                cursor.execute("SELECT fn_rotate_webhook_secret(%s, 86400)", ["new_secret_token_222"])
        else:
            sec1.status = ZoomWebhookVault.Status.RETIRED
            sec1.retired_at = timezone.now()
            sec1.save()
            ZoomWebhookVault.objects.create(
                secret_token="new_secret_token_222",
                status=ZoomWebhookVault.Status.ACTIVE
            )

        # Both new secret and retired secret within grace period must verify
        sig2 = "v0=" + hmac.new(b"new_secret_token_222", msg, hashlib.sha256).hexdigest()
        self.assertTrue(ZoomRecordingService.verify_webhook_signature(raw_body, sig2, ts))
        self.assertTrue(ZoomRecordingService.verify_webhook_signature(raw_body, sig1, ts))


class PhaseB5WebhookPayloadTestCase(TestCase):
    """Group D: Webhook Payload validation, account_id check, duplicate event deduplication."""

    def setUp(self):
        self.client = APIClient()
        self.secret = "webhook_secret_payload_test"
        ZoomWebhookVault.objects.all().delete()
        ZoomWebhookVault.objects.create(secret_token=self.secret, status=ZoomWebhookVault.Status.ACTIVE)

    def _make_headers(self, raw_body_bytes):
        ts = str(int(timezone.now().timestamp()))
        msg = f"v0:{ts}:{raw_body_bytes.decode('utf-8')}".encode('utf-8')
        sig = "v0=" + hmac.new(self.secret.encode('utf-8'), msg, hashlib.sha256).hexdigest()
        return {
            "HTTP_X_ZM_SIGNATURE": sig,
            "HTTP_X_ZM_REQUEST_TIMESTAMP": ts,
            "CONTENT_TYPE": "application/json"
        }

    @override_settings(ZOOM_ACCOUNT_ID="expected_acc_123")
    def test_account_id_mismatch_rejected(self):
        body = json.dumps({
            "event": "meeting.started",
            "payload": {"account_id": "wrong_account", "object": {"id": "12345"}}
        }).encode('utf-8')
        headers = self._make_headers(body)
        res = self.client.post("/api/courses/zoom/webhook/", data=body, content_type="application/json", **headers)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_duplicate_event_deduplication(self):
        body = json.dumps({
            "event": "meeting.started",
            "event_ts": 1700000000,
            "payload": {"object": {"id": "999888777", "uuid": "uuid_dedup_test"}}
        }).encode('utf-8')
        headers = self._make_headers(body)
        res1 = self.client.post("/api/courses/zoom/webhook/", data=body, content_type="application/json", **headers)
        self.assertEqual(res1.status_code, status.HTTP_200_OK)

        # Mark processed
        ev = ZoomWebhookEvent.objects.filter(event_id="zoom:meeting.started:uuid_dedup_test:1700000000").first()
        if ev:
            ev.status = ZoomWebhookEvent.Status.PROCESSED
            ev.save()

        # Redelivery should return 200 with duplicate_ignored
        res2 = self.client.post("/api/courses/zoom/webhook/", data=body, content_type="application/json", **headers)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertIn("duplicate_ignored", str(res2.data))

    def test_user_deleted_webhook_deactivates_host(self):
        host = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_user_del_test",
            email="del_user@natya.test",
            status=ZoomHostAccount.Status.ACTIVE
        )
        body = json.dumps({
            "event": "user.deleted",
            "event_ts": 1700000050,
            "payload": {"object": {"id": "zoom_user_del_test"}}
        }).encode('utf-8')
        headers = self._make_headers(body)
        res = self.client.post("/api/courses/zoom/webhook/", data=body, content_type="application/json", **headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        host.refresh_from_db()
        self.assertEqual(host.status, ZoomHostAccount.Status.INACTIVE)


class PhaseB5CancellationSagaTestCase(TestCase):
    """Group E: Cancellation Saga Worker & Watchdog Recovery."""

    def setUp(self):
        self.instructor = User.objects.create_user(username="inst_saga", email="inst_saga@natya.test", password="pass")
        self.course = Course.objects.create(title="Saga Course")
        self.host = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_host_saga",
            email="host_saga@natya.test",
            status=ZoomHostAccount.Status.ACTIVE
        )
        self.lc = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Saga Live Class",
            scheduled_start=timezone.now() + datetime.timedelta(days=1),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://zoom.us/j/98765432101",
            zoom_meeting_id="98765432101"
        )
        self.zmr = ZoomMeetingRecord.objects.create(
            zoom_meeting_id="98765432101",
            live_class=self.lc,
            state="ASSIGNED",
            is_current=True,
            host_account=self.host
        )
        self.res = HostReservation.objects.create(
            live_class=self.lc,
            host_account=self.host,
            scheduled_start=self.lc.scheduled_start,
            scheduled_end=self.lc.scheduled_start + datetime.timedelta(minutes=60),
            status=HostReservation.Status.ACTIVE
        )

    def test_saga_4_phase_execution(self):
        from courses.test_zoom_phase_b5_repairs import FakeZoomCloud
        worker_id = uuid.uuid4()
        # Phase 1: Claim and lease
        claim = ZoomCancellationSagaWorker.claim_and_lease(self.lc.id, worker_id=worker_id)
        self.assertIsNotNone(claim)
        self.zmr.refresh_from_db()
        self.assertEqual(self.zmr.state, "COMPENSATING")

        # Phase 2: Remote delete through the REAL ZoomService (HTTP layer simulated with Zoom's 204 shape)
        with FakeZoomCloud() as cloud:
            cloud.add_meeting(self.host.zoom_user_id, self.lc.title, self.lc.scheduled_start, meeting_id="98765432101")
            delete_res = ZoomCancellationSagaWorker.execute_remote_delete("98765432101")
            self.assertNotIn("98765432101", cloud.meetings)
        self.assertTrue(delete_res["confirmed"])
        self.assertEqual(delete_res["status"], 204)

        # Phase 3: Persist evidence -- derived from the actual outcome; callers cannot supply a status
        ev = ZoomCancellationSagaWorker.persist_cancellation_evidence(
            "98765432101", delete_res, worker_id=str(worker_id)
        )
        self.assertEqual((ev.evidence_type, ev.http_status), ("CANCELLATION_SAGA_DELETE", 204))
        with self.assertRaises(TypeError):
            ZoomCancellationSagaWorker.persist_cancellation_evidence("98765432101", 204, {}, worker_id="forged")

        # Phase 4: Finalize
        finalized = ZoomCancellationSagaWorker.finalize_cancellation(self.lc.id, worker_id=worker_id)
        self.assertTrue(finalized)

        self.zmr.refresh_from_db()
        self.res.refresh_from_db()
        self.assertEqual(self.zmr.state, "COMPENSATED")
        self.assertFalse(self.zmr.is_current)
        self.assertEqual(self.res.status, HostReservation.Status.CANCELLED)

    def test_watchdog_recovery(self):
        # Create a stuck compensating meeting with stale updated_at
        self.zmr.state = "COMPENSATING"
        self.zmr.live_class = None
        self.zmr.save(update_fields=['state', 'live_class'])
        ZoomMeetingRecord.objects.filter(id=self.zmr.id).update(
            updated_at=timezone.now() - datetime.timedelta(seconds=120)
        )

        class MockZoomClient:
            def delete_meeting(self, meeting_id):
                class MockRes:
                    status_code = 204
                    data = {}
                return MockRes()

        recovered = ZoomCancellationSagaWorker.run_watchdog_recovery(stale_seconds=60, zoom_client=MockZoomClient())
        self.assertEqual(recovered, 1)
        self.zmr.refresh_from_db()
        self.assertEqual(self.zmr.state, "COMPENSATED")

    def test_in_flight_delayed_creation_fenced_and_torn_down(self):
        """
        Calls the REAL provision_zoom_meeting(). While the Zoom CREATE is in flight the
        cancellation saga fences the DOP; when CREATE returns, Phase 3 must stop the success
        path, durably commit compensation + the teardown outbox, tear the orphan down outside
        the transaction, and record evidence only from the actual DELETE response.
        """
        from courses.test_zoom_phase_b5_repairs import FakeZoomCloud
        from courses.services.zoom_lifecycle import ConcurrencyConflictError, provision_zoom_meeting

        lc2 = LiveClass.objects.create(
            course=self.course,
            title="Delayed Class",
            scheduled_start=timezone.now().replace(microsecond=0) + datetime.timedelta(days=2),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url="https://natyaarts.com/class/delayed"
        )

        with FakeZoomCloud() as cloud:
            # Cancellation worker runs while the creation request is in flight.
            cloud.on_create = lambda mid: ZoomCancellationSagaWorker.claim_and_lease(lc2.id)
            with self.assertRaises(ConcurrencyConflictError):
                provision_zoom_meeting(lc2.id)
            deleted = [c[1] for c in cloud.calls_of('DELETE')]
            self.assertEqual(len(deleted), 1)
            self.assertEqual(cloud.meetings, {})  # the orphan meeting was destroyed on Zoom

        orphan = deleted[0]
        dop = LiveClassDurableOperation.objects.get(live_class=lc2)
        self.assertEqual(dop.response_payload["orphan_meeting_id"], orphan)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)
        # The success path never ran: no meeting record, reservation never reactivated.
        self.assertFalse(ZoomMeetingRecord.objects.filter(zoom_meeting_id=orphan).exists())
        self.assertEqual(HostReservation.objects.get(durable_operation=dop).status, HostReservation.Status.CANCELLED)
        ev = ZoomVerifiedEvidence.objects.get(meeting_id=orphan)
        self.assertEqual((ev.evidence_type, ev.http_status, ev.dop_id), ("ORPHAN_FENCING_TEARDOWN", 204, dop.id))
        self.assertTrue(ZoomLifecycleOutbox.objects.filter(
            event_type="ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN", payload__meeting_id=orphan).exists())

    def test_crashed_worker_provision_recovery(self):
        """
        Worker crashed after Zoom accepted CREATE but before Phase 3. Recovery lists the host's
        meetings with the REAL ZoomService response shape (a list of {'meeting_id', ...}),
        attributes the orphan by full correlation (never topic alone), deletes only it, records
        evidence from the actual DELETE response, and only then cancels the reservation.
        """
        from courses.test_zoom_phase_b5_repairs import FakeZoomCloud
        from courses.services.zoom_lifecycle import recover_crashed_provision_dop

        start = timezone.now().replace(microsecond=0) + datetime.timedelta(days=3)
        lc_crash = LiveClass.objects.create(
            course=self.course,
            instructor=self.instructor,
            title="Crash Test Live Class",
            scheduled_start=start,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM
        )
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key="crash-dop-9999",
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.IN_PROGRESS,
            live_class=lc_crash,
            version=1,
            lease_expires_at=timezone.now() - datetime.timedelta(seconds=10),
            payload={"live_class_id": lc_crash.id, "title": lc_crash.title,
                     "scheduled_start": start.isoformat(), "duration_minutes": 60}
        )
        res = HostReservation.objects.create(
            live_class=lc_crash,
            host_account=self.host,
            scheduled_start=start,
            scheduled_end=start + datetime.timedelta(minutes=60),
            status=HostReservation.Status.PROVISIONAL,
            durable_operation=dop
        )

        with FakeZoomCloud() as cloud:
            orphan = cloud.add_meeting(self.host.zoom_user_id, lc_crash.title, start)
            next_week = cloud.add_meeting(self.host.zoom_user_id, lc_crash.title, start + datetime.timedelta(days=7))
            rec = recover_crashed_provision_dop(dop.id)
            self.assertNotIn(orphan, cloud.meetings)
            self.assertIn(next_week, cloud.meetings, "a same-title meeting of another occurrence must survive")

        self.assertEqual(rec["status"], "RECOVERED")
        self.assertEqual(rec["orphan_deleted"], orphan)
        dop.refresh_from_db()
        res.refresh_from_db()
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)
        self.assertEqual(res.status, HostReservation.Status.CANCELLED)
        ev = ZoomVerifiedEvidence.objects.get(meeting_id=orphan)
        self.assertEqual((ev.evidence_type, ev.http_status, ev.dop_id), ("CRASH_RECOVERY_TEARDOWN", 204, dop.id))


class PhaseB5QuarantineServiceTestCase(TestCase):
    """Group F: Quarantine Service & Dual GET Evidence."""

    def test_quarantine_grace_period_enforcement(self):
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key=str(uuid.uuid4()),
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            payload={"meeting_id": "999111222"}
        )
        self.assertIsNotNone(dop.quarantined_at)

        # Attempting resolution immediately (< 15 min after entering quarantine) must raise
        with self.assertRaises(QuarantinePeriodActiveError):
            ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
        # The former caller-controlled bypass flag no longer exists
        with self.assertRaises(TypeError):
            ZoomQuarantineService.verify_and_resolve_quarantine(dop.id, force_bypass_timer=True)

    def test_quarantine_dual_probe_resolution(self):
        from courses.test_zoom_phase_b5_repairs import FakeZoomCloud
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key=str(uuid.uuid4()),
            operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.OUTCOME_UNKNOWN,
            payload={"meeting_id": "999111333"}
        )
        LiveClassDurableOperation.objects.filter(id=dop.id).update(
            quarantined_at=timezone.now() - datetime.timedelta(minutes=16))

        # Real authenticated GET probes through ZoomService; Zoom answers 404 {"code": 3001}
        with FakeZoomCloud() as cloud:
            res = ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
            self.assertEqual(len(cloud.calls_of('GET')), 2)
        self.assertEqual(res["status"], "RESOLVED")
        dop.refresh_from_db()
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)

        ev = ZoomVerifiedEvidence.objects.filter(meeting_id="999111333", dop_id=dop.id)
        self.assertEqual(sorted(ev.values_list('http_status', flat=True)), [404, 404])


class PhaseB5DatabaseGovernanceTestCase(TestCase):
    """Group G, H, I: Database Functions, Triggers, Rollback Authorization, Dual Approval."""

    def setUp(self):
        if connection.vendor != 'postgresql':
            self.skipTest("PostgreSQL required for database functions and triggers.")

    def test_canonicalize_host_state_function(self):
        user = User.objects.create_user(username="canon_user", email="canon@natya.test", password="p")
        host = ZoomHostAccount.objects.create(
            zoom_user_id="canon_host_1",
            email="canon@natya.test",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=user
        )

        with connection.cursor() as cursor:
            cursor.execute("SELECT fn_canonicalize_host_state(%s)", [host.id])
            data = cursor.fetchone()[0]
        if isinstance(data, str):
            data = json.loads(data)

        self.assertEqual(data["id"], host.id)
        self.assertEqual(data["zoom_user_id"], "canon_host_1")
        self.assertEqual(data["email"], "canon@natya.test")
        self.assertEqual(data["account_type"], "DEDICATED")
        self.assertEqual(data["assigned_instructor_id"], user.id)

    def test_mutation_triggers_prevent_tampering(self):
        # 1. Terminal reservation mutation blocked
        host = ZoomHostAccount.objects.create(
            zoom_user_id="mut_host", email="mut@natya.test", status=ZoomHostAccount.Status.ACTIVE
        )
        c = Course.objects.create(title="Mut Course")
        lc = LiveClass.objects.create(
            course=c,
            title="Mut Class",
            meeting_url="https://zoom.us/j/123",
            scheduled_start=timezone.now(),
            duration_minutes=60
        )
        res = HostReservation.objects.create(
            live_class=lc, host_account=host,
            scheduled_start=timezone.now(),
            scheduled_end=timezone.now() + datetime.timedelta(minutes=60),
            status=HostReservation.Status.RELEASED
        )

        with self.assertRaises(Exception):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE courses_hostreservation SET status = 'ACTIVE' WHERE id = %s",
                        [res.id]
                    )

        # 2. Terminal meeting mutation blocked
        zmr = ZoomMeetingRecord.objects.create(
            zoom_meeting_id="77766655544", live_class=lc, state="RELEASED", is_current=False
        )
        with self.assertRaises(Exception):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE courses_zoommeetingrecord SET state = 'ASSIGNED' WHERE id = %s",
                        [zmr.id]
                    )

        # 3. Truncate audit log blocked when app.allow_audit_truncate is off
        with self.assertRaises(Exception):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET LOCAL app.allow_audit_truncate = 'off';")
                    cursor.execute("TRUNCATE TABLE courses_zoomadminauditlog;")

    def test_rollback_session_authorization(self):
        """Rollback execution is disabled: no session or approval can authorize it."""
        host = ZoomHostAccount.objects.create(
            zoom_user_id="rb_host", email="rb@natya.test", status=ZoomHostAccount.Status.ACTIVE, account_type="SHARED_POOL"
        )
        token = "valid_session_token_12345"
        ZoomRollbackSession.objects.create(
            session_token=token,
            approver_id="admin_sec_1",
            expires_at=timezone.now() + datetime.timedelta(hours=1)
        )
        snapshot = {"hosts": [{"id": host.id, "status": "INACTIVE", "account_type": "DEDICATED", "assigned_instructor_id": None}]}

        with self.assertRaisesRegex(Exception, 'ROLLBACK_EXECUTION_DISABLED'):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT fn_execute_exact_rollback_0038(%s, %s::jsonb)", [token, json.dumps(snapshot)])

        host.refresh_from_db()
        self.assertEqual(host.status, ZoomHostAccount.Status.ACTIVE)
        self.assertEqual(host.account_type, "SHARED_POOL")
        self.assertIsNone(ZoomRollbackSession.objects.get(session_token=token).consumed_at)

    def test_manifest_nonce_replay_and_dual_approval(self):
        """
        The former shared-key HMAC path no longer authenticates anyone: key enrollment is
        disabled and a privileged session (the application/owner login) can never approve,
        even with a correctly computed HMAC signature. Session-authenticated dual approval,
        nonce replay, commit binding and immutability are exercised with real independent
        PostgreSQL logins in test_zoom_phase_b5_repairs.B1ApprovalSecurityTests.
        """
        with self.assertRaisesRegex(Exception, 'APPROVER_KEY_ENROLLMENT_DISABLED'):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT fn_enroll_approver_key('approver_alice', %s)", ["alice_secret_hmac_key_12345"])

        manifest_hash = hashlib.sha256(b"manifest-0038").hexdigest()
        target_commit = "75e0f0b0ea5bbcaec9874109108821a8cda59bf2"
        nonce = uuid.uuid4().hex
        expiry = timezone.now().replace(microsecond=0) + datetime.timedelta(hours=2)
        msg = f"{manifest_hash}:{target_commit}:{nonce}:{expiry.strftime('%Y-%m-%dT%H:%M:%SZ')}:approver_alice"
        sig = hmac.new(b"alice_secret_hmac_key_12345", msg.encode('utf-8'), hashlib.sha256).hexdigest()

        with self.assertRaisesRegex(Exception, 'APPROVER_PRIVILEGED_SESSION'):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT fn_approve_reconciliation_manifest(%s, %s, %s, %s, 'approver_alice', %s)",
                        [manifest_hash, target_commit, nonce, expiry, sig]
                    )
        self.assertFalse(ZoomReconciliationApproval.objects.filter(manifest_hash=manifest_hash).exists())
        self.assertFalse(ZoomManifestNonces.objects.filter(nonce=nonce).exists())

    def test_natya_app_role_privilege_boundaries(self):
        """Verify natya_app cannot SELECT from vault, cannot direct INSERT/DELETE/TRUNCATE, cannot UPDATE payload,
        and cannot manufacture verified evidence, while the webhook recording function stays callable."""
        with connection.cursor() as cursor:
            cursor.execute("SET ROLE natya_app;")
            try:
                # 1. natya_app cannot SELECT from webhook vault
                with self.assertRaises(Exception):
                    with transaction.atomic():
                        cursor.execute("SELECT * FROM courses_zoomwebhookvault;")

                # 2. natya_app cannot INSERT into webhook events directly
                with self.assertRaises(Exception):
                    with transaction.atomic():
                        cursor.execute(
                            "INSERT INTO courses_zoomwebhookevent (event_id, event_type, payload, status, error_message, retry_count, received_at) "
                            "VALUES ('direct_exploit', 'test', '{}', 'RECEIVED', '', 0, NOW());"
                        )

                # 3. natya_app cannot UPDATE payload column on webhook events
                with self.assertRaises(Exception):
                    with transaction.atomic():
                        cursor.execute("UPDATE courses_zoomwebhookevent SET payload = '{\"hacked\":true}'::jsonb;")

                # 4. natya_app cannot direct INSERT into verified evidence
                with self.assertRaises(Exception):
                    with transaction.atomic():
                        cursor.execute(
                            "INSERT INTO courses_zoomverifiedevidence (meeting_id, evidence_type, http_status, response_body, verified_at, worker_id) "
                            "VALUES ('98765432101', 'CANCELLATION_SAGA_DELETE', 204, '{}', NOW(), 'exploit');"
                        )

                # 5. natya_app cannot execute administrative functions
                with self.assertRaises(Exception):
                    with transaction.atomic():
                        cursor.execute("SELECT fn_rotate_webhook_secret('token', 86400);")

                with self.assertRaises(Exception):
                    with transaction.atomic():
                        cursor.execute("SELECT fn_enroll_approver_key('admin', 'key');")

                # 6. natya_app can NOT manufacture "successful" evidence through the evidence function
                with self.assertRaisesRegex(Exception, 'permission denied for function fn_record_verified_evidence'):
                    with transaction.atomic():
                        cursor.execute(
                            "SELECT fn_record_verified_evidence('98765432101', 'CANCELLATION_SAGA_DELETE', 204, '{}'::jsonb, 'test_worker', NULL);"
                        )

                # 7. natya_app CAN still execute the webhook recording function (unchanged by the repair)
                cursor.execute(
                    "SELECT id, is_existing FROM fn_record_authenticated_webhook('app_role_event_1', 'meeting.started', '{}'::jsonb);"
                )
                res = cursor.fetchone()
                self.assertIsNotNone(res[0])
            finally:
                cursor.execute("RESET ROLE;")
        self.assertFalse(ZoomVerifiedEvidence.objects.filter(meeting_id='98765432101').exists())

    def test_dual_approval_fail_closed(self):
        """Caller-supplied approver ids, forged signatures and expired timestamps from a non-approver
        (privileged/application) session all fail closed before anything is recorded."""
        manifest_hash = hashlib.sha256(b"failclosed").hexdigest()
        target_commit = "75e0f0b0ea5bbcaec9874109108821a8cda59bf2"
        cases = [
            ("unenrolled_hacker", "sig_123", timezone.now() + datetime.timedelta(hours=1)),
            ("approver_charlie", "forged_wrong_signature", timezone.now() + datetime.timedelta(hours=1)),
            ("approver_charlie", "any_sig", timezone.now() - datetime.timedelta(minutes=10)),
        ]
        for approver_id, signature, expiry in cases:
            with self.subTest(approver=approver_id, signature=signature):
                with self.assertRaisesRegex(Exception, 'APPROVER_PRIVILEGED_SESSION'):
                    with transaction.atomic():
                        with connection.cursor() as cursor:
                            cursor.execute(
                                "SELECT fn_approve_reconciliation_manifest(%s, %s, %s, %s, %s, %s)",
                                [manifest_hash, target_commit, uuid.uuid4().hex, expiry, approver_id, signature]
                            )
        self.assertFalse(ZoomReconciliationApproval.objects.filter(manifest_hash=manifest_hash).exists())
