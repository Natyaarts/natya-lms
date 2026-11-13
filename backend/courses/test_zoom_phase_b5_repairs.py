"""
Phase B.5 critical defect repairs (audit blockers B1-B5) -- regression tests.

Every lifecycle test here drives the REAL implementation (provision_zoom_meeting,
recover_crashed_provision_dop, ZoomQuarantineService, ZoomCancellationSagaWorker)
through the REAL ZoomService. Only the HTTP layer (requests.post/get/delete) and
the OAuth token fetch are replaced, by FakeZoomCloud, which answers with Zoom's
actual response shapes (201 create body, 204 delete, 404 {"code": 3001},
paginated meeting list). No live Zoom calls are made.

PostgreSQL-only tests (functions, triggers, grants, independent sessions) skip on
other backends and are expected to run against the isolated local PostgreSQL.
"""
import datetime
import itertools
import json
import re
import uuid
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from courses.models import (
    Course, LiveClass, LiveClassDurableOperation, ZoomHostAccount, HostReservation,
    ZoomMeetingRecord, ZoomLifecycleOutbox, ZoomVerifiedEvidence,
)
from courses.services import zoom as zoom_module
from courses.services.zoom import ZoomService
from courses.services.zoom_lifecycle import (
    provision_zoom_meeting, recover_crashed_provision_dop, process_outbox_message,
    ZoomCancellationSagaWorker, ZoomQuarantineService, ZoomCallOutcome,
    zoom_delete_outcome, zoom_get_outcome, _record_evidence_from_outcome,
    ConcurrencyConflictError, QuarantinePeriodActiveError, QuarantineVerificationError,
    QuarantineStateError, ZeroTeardownInvariantViolation, PROVISION_LEASE_SECONDS,
)

User = get_user_model()
IS_PG = connection.vendor == 'postgresql'

_USER_MEETINGS = re.compile(r'^https://api\.zoom\.us/v2/users/(?P<host>[^/?]+)/meetings$')
_MEETING = re.compile(r'^https://api\.zoom\.us/v2/meetings/(?P<mid>[^/?]+)$')
_ERROR_BODIES = {
    401: {"code": 124, "message": "Invalid access token."},
    403: {"code": 200, "message": "No permission."},
    404: {"code": 3001, "message": "Meeting does not exist."},
    429: {"code": 429, "message": "You have reached the maximum per-second rate limit."},
}


def _zoom_time(dt):
    return dt.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


class FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload
        self.text = "" if payload is None else json.dumps(payload)
        self.headers = {}

    def json(self):
        if self._payload is None:
            raise ValueError("No JSON body")
        return self._payload


class FakeZoomCloud:
    """In-memory Zoom REST API at the HTTP layer. ZoomService itself runs unmodified."""

    def __init__(self):
        self.meetings = {}
        self.calls = []              # (method, kind, key, in_atomic_block)
        self._failures = {}          # (method, key) -> [action, remaining]
        self._hooks = {}             # (method, key) -> callable, run once at call start
        self._ids = itertools.count(85012340001)
        self.on_create = None        # callable(meeting_id), run once after a successful CREATE

    def add_meeting(self, host_id, topic, start, duration=60, created_at=None, meeting_id=None):
        mid = str(meeting_id or next(self._ids))
        self.meetings[mid] = {
            "id": int(mid), "uuid": f"uuid-{mid}==", "host_id": host_id, "topic": topic, "type": 2,
            "start_time": _zoom_time(start), "duration": int(duration), "timezone": "Asia/Kolkata",
            "created_at": _zoom_time(created_at or timezone.now()),
            "join_url": f"https://zoom.us/j/{mid}", "start_url": f"https://zoom.us/s/{mid}?zak=redact-me",
        }
        return mid

    def fail(self, method, key, action, times=1000):
        """action: HTTP status int, 'timeout' or 'connection'. method: POST/LIST/GET/DELETE."""
        self._failures[(method, str(key))] = [action, times]

    def hook(self, method, key, fn):
        self._hooks[(method, str(key))] = fn

    def calls_of(self, method):
        return [c for c in self.calls if c[0] == method]

    def _failure(self, method, key):
        entry = self._failures.get((method, key))
        if not entry or entry[1] <= 0:
            return None
        entry[1] -= 1
        action = entry[0]
        if action == 'timeout':
            raise requests.Timeout(f"simulated timeout on {method} {key}")
        if action == 'connection':
            raise requests.ConnectionError(f"simulated connection drop on {method} {key}")
        return FakeResponse(action, _ERROR_BODIES.get(action, {"code": action, "message": "Internal Server Error"}))

    def handle(self, method, url, **kwargs):
        m_user, m_meeting = _USER_MEETINGS.match(url), _MEETING.match(url)
        if m_user:
            kind, key = ('POST' if method == 'POST' else 'LIST'), m_user.group('host')
        elif m_meeting:
            kind, key = method, m_meeting.group('mid')
        else:
            raise AssertionError(f"Unexpected Zoom call {method} {url}")
        self.calls.append((kind, key, connection.in_atomic_block))
        hook = self._hooks.pop((kind, key), None)
        if hook:
            hook()
        failure = self._failure(kind, key)
        if failure is not None:
            return failure

        if kind == 'POST':
            body = kwargs.get('json') or {}
            start = datetime.datetime.fromisoformat(body['start_time'])
            mid = self.add_meeting(key, body.get('topic'), start, body.get('duration', 60))
            response = FakeResponse(201, dict(self.meetings[mid]))  # Zoom has already answered
            if self.on_create:
                cb, self.on_create = self.on_create, None
                cb(mid)  # concurrent activity while the response is in flight
            return response
        if kind == 'LIST':
            items = [dict(x) for x in self.meetings.values() if x['host_id'] == key]
            return FakeResponse(200, {"page_size": 300, "total_records": len(items),
                                      "next_page_token": "", "meetings": items})
        if key not in self.meetings:
            return FakeResponse(404, {"code": 3001, "message": f"Meeting does not exist: {key}."})
        if kind == 'GET':
            return FakeResponse(200, dict(self.meetings[key], status="waiting", settings={}))
        del self.meetings[key]
        return FakeResponse(204, None)

    def __enter__(self):
        self._patches = [
            mock.patch.object(zoom_module.requests, 'post', side_effect=lambda url, **kw: self.handle('POST', url, **kw)),
            mock.patch.object(zoom_module.requests, 'get', side_effect=lambda url, **kw: self.handle('GET', url, **kw)),
            mock.patch.object(zoom_module.requests, 'delete', side_effect=lambda url, **kw: self.handle('DELETE', url, **kw)),
            mock.patch.object(ZoomService, 'get_access_token', return_value='fake-token'),
            mock.patch.object(ZoomService, '_invalidate_cached_token', return_value=None),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patches):
            p.stop()
        return False


class _LifecycleFixture:
    HOST_ID = "hostrepair_0001"

    def make_fixture(self):
        self.host = ZoomHostAccount.objects.create(
            zoom_user_id=self.HOST_ID, email="host.repair@natya.in", display_name="Repair Host",
            status=ZoomHostAccount.Status.ACTIVE, max_concurrency=1)
        self.course = Course.objects.create(title="B5 Repair Course")
        self.start = timezone.now().replace(microsecond=0) + datetime.timedelta(days=1)
        self.lc = LiveClass.objects.create(
            course=self.course, title="Varnam Live Session", scheduled_start=self.start,
            duration_minutes=60, status=LiveClass.ClassStatus.SCHEDULED,
            meeting_url="https://natyaarts.com/class/b5")

    def next_generation(self):
        last = HostReservation.objects.filter(live_class=self.lc).order_by('-generation').first()
        return (last.generation + 1) if last else 1

    def make_inflight_dop(self, lease_expired=True, status=LiveClassDurableOperation.Status.IN_PROGRESS,
                          res_status=HostReservation.Status.PROVISIONAL):
        lease = timezone.now() + datetime.timedelta(seconds=(-30 if lease_expired else 300))
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key=f"b5-{uuid.uuid4().hex}", operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=status, live_class=self.lc, lease_worker=uuid.uuid4(), lease_expires_at=lease,
            payload={"live_class_id": self.lc.id, "title": self.lc.title,
                     "scheduled_start": self.lc.scheduled_start.isoformat(), "duration_minutes": 60})
        res = HostReservation.objects.create(
            live_class=self.lc, host_account=self.host, scheduled_start=self.start,
            scheduled_end=self.start + datetime.timedelta(minutes=60), status=res_status, durable_operation=dop,
            generation=self.next_generation())
        return dop, res

    def make_quarantined_dop(self, meeting_id="85099990001", minutes_ago=20, with_host=True):
        dop = LiveClassDurableOperation.objects.create(
            idempotency_key=f"q-{uuid.uuid4().hex}", operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.OUTCOME_UNKNOWN, live_class=self.lc,
            payload={"live_class_id": self.lc.id, "title": self.lc.title, "meeting_id": meeting_id,
                     "scheduled_start": self.lc.scheduled_start.isoformat(), "duration_minutes": 60})
        self.assertIsNotNone(dop.quarantined_at, "save() must stamp quarantined_at on entering quarantine")
        LiveClassDurableOperation.objects.filter(id=dop.id).update(
            quarantined_at=timezone.now() - datetime.timedelta(minutes=minutes_ago))
        res = None
        if with_host:
            res = HostReservation.objects.create(
                live_class=self.lc, host_account=self.host, scheduled_start=self.start,
                scheduled_end=self.start + datetime.timedelta(minutes=60),
                status=HostReservation.Status.MANUAL_REVIEW, durable_operation=dop, generation=self.next_generation())
        dop.refresh_from_db()
        return dop, res


# =============================================================================
# B5 -- provisioning fencing violation (real provision_zoom_meeting)
# =============================================================================
class B5ProvisionFencingTests(_LifecycleFixture, TestCase):
    def setUp(self):
        self.make_fixture()

    def _cancel_during_create(self, cloud):
        cloud.on_create = lambda mid: ZoomCancellationSagaWorker.claim_and_lease(self.lc.id)

    def test_fenced_create_stops_success_path_and_confirms_teardown(self):
        with FakeZoomCloud() as cloud:
            self._cancel_during_create(cloud)
            with self.assertRaises(ConcurrencyConflictError):
                provision_zoom_meeting(self.lc.id)
        dop = LiveClassDurableOperation.objects.get(live_class=self.lc)
        orphan = dop.response_payload["orphan_meeting_id"]
        res = HostReservation.objects.get(durable_operation=dop)
        # Success path never ran.
        self.assertFalse(ZoomMeetingRecord.objects.filter(zoom_meeting_id=orphan).exists())
        self.lc.refresh_from_db()
        self.assertNotEqual(self.lc.zoom_meeting_id, orphan)
        # Remote meeting deleted, evidence reflects the actual 204, bound to the DOP.
        self.assertNotIn(orphan, cloud.meetings)
        ev = ZoomVerifiedEvidence.objects.get(meeting_id=orphan)
        self.assertEqual((ev.evidence_type, ev.http_status, ev.dop_id), ("ORPHAN_FENCING_TEARDOWN", 204, dop.id))
        # Durable compensation committed; reservation was held then cancelled, never reactivated.
        outbox = ZoomLifecycleOutbox.objects.get(event_type="ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN")
        self.assertEqual(outbox.status, ZoomLifecycleOutbox.Status.COMPLETED)
        self.assertEqual(outbox.payload["meeting_id"], orphan)
        self.assertEqual(res.status, HostReservation.Status.CANCELLED)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)
        self.assertIsNone(dop.lease_worker)

    def test_fenced_create_failed_teardown_leaves_durable_retry_and_holds_host(self):
        for action in (500, 'timeout', 403):
            with self.subTest(delete_failure=action):
                LiveClassDurableOperation.objects.filter(live_class=self.lc).update(status='COMPENSATED')
                HostReservation.objects.filter(live_class=self.lc).exclude(
                    status__in=['CANCELLED', 'RELEASED', 'COMPLETED']).update(status='CANCELLED')
                with FakeZoomCloud() as cloud:
                    self._cancel_during_create(cloud)
                    cloud.fail('DELETE', '85012340001', action)
                    with self.assertRaises(ConcurrencyConflictError):
                        provision_zoom_meeting(self.lc.id)
                    dop = LiveClassDurableOperation.objects.filter(live_class=self.lc).order_by('-id').first()
                    orphan = dop.response_payload["orphan_meeting_id"]
                    self.assertIn(orphan, cloud.meetings)
                    self.assertFalse(ZoomVerifiedEvidence.objects.filter(meeting_id=orphan).exists())
                    self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATION_REQUIRED)
                    self.assertIsNotNone(dop.quarantined_at)
                    res = HostReservation.objects.get(durable_operation=dop)
                    self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)
                    outbox = ZoomLifecycleOutbox.objects.get(event_type="ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN",
                                                             payload__dop_id=dop.id)
                    self.assertEqual(outbox.status, ZoomLifecycleOutbox.Status.PENDING)
                    # The durable retry works once Zoom recovers.
                    cloud.fail('DELETE', orphan, 500, times=0)
                    self.assertTrue(process_outbox_message(outbox.id))
                    self.assertNotIn(orphan, cloud.meetings)
                    outbox.refresh_from_db()
                    self.assertEqual(outbox.status, ZoomLifecycleOutbox.Status.COMPLETED)

    def test_orphan_discovered_by_fencing_restarts_quarantine_grace(self):
        def cancel_and_age_quarantine(mid):
            ZoomCancellationSagaWorker.claim_and_lease(self.lc.id)
            LiveClassDurableOperation.objects.filter(live_class=self.lc).update(
                quarantined_at=timezone.now() - datetime.timedelta(hours=1))
        with FakeZoomCloud() as cloud:
            cloud.on_create = cancel_and_age_quarantine
            cloud.fail('DELETE', '85012340001', 500)
            with self.assertRaises(ConcurrencyConflictError):
                provision_zoom_meeting(self.lc.id)
        dop = LiveClassDurableOperation.objects.get(live_class=self.lc)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATION_REQUIRED)
        self.assertGreater(dop.quarantined_at, timezone.now() - datetime.timedelta(minutes=1),
                           "grace must restart when a remote meeting first becomes known")
        with self.assertRaises(QuarantinePeriodActiveError):
            ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)

    def test_fenced_ambiguous_create_holds_host_without_teardown_target(self):
        with FakeZoomCloud() as cloud:
            cloud.hook('POST', self.HOST_ID, lambda: ZoomCancellationSagaWorker.claim_and_lease(self.lc.id))
            cloud.fail('POST', self.HOST_ID, 'timeout')
            with self.assertRaises(ConcurrencyConflictError):
                provision_zoom_meeting(self.lc.id)
        dop = LiveClassDurableOperation.objects.get(live_class=self.lc)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATION_REQUIRED)
        self.assertEqual(HostReservation.objects.get(durable_operation=dop).status, HostReservation.Status.MANUAL_REVIEW)
        self.assertFalse(ZoomMeetingRecord.objects.filter(live_class=self.lc).exists())

    def test_success_path_sets_and_clears_provisioning_lease(self):
        seen = {}
        with FakeZoomCloud() as cloud:
            def capture():
                d = LiveClassDurableOperation.objects.get(live_class=self.lc)
                seen.update(worker=d.lease_worker, expires=d.lease_expires_at)
            cloud.hook('POST', self.HOST_ID, capture)
            result = provision_zoom_meeting(self.lc.id)
        self.assertEqual(result["status"], "SUCCESS")
        self.assertIsNotNone(seen["worker"])
        self.assertGreater(seen["expires"], timezone.now() + datetime.timedelta(seconds=PROVISION_LEASE_SECONDS - 60))
        dop = LiveClassDurableOperation.objects.get(live_class=self.lc)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPLETED)
        self.assertIsNone(dop.lease_worker)
        self.assertIsNone(dop.lease_expires_at)

    def test_inflight_dop_is_never_redriven(self):
        dop, _ = self.make_inflight_dop(lease_expired=True)
        with FakeZoomCloud() as cloud:
            # Refused either by the zero-teardown invariant or by the B4 re-drive guard;
            # what matters is that no second CREATE is ever sent.
            with self.assertRaises((ConcurrencyConflictError, ZeroTeardownInvariantViolation)):
                provision_zoom_meeting(self.lc.id, idempotency_key=dop.idempotency_key)
            self.assertEqual(cloud.calls_of('POST'), [])
        self.assertEqual(HostReservation.objects.filter(live_class=self.lc).count(), 1)

    def test_late_create_after_crash_recovery_cannot_resurrect_meeting(self):
        """Delayed CREATE: recovery runs (lease expired) while CREATE is in flight; Phase 3 must fence."""
        recovery_result = {}
        with FakeZoomCloud() as cloud:
            def crash_recovery_during_create(mid):
                LiveClassDurableOperation.objects.filter(live_class=self.lc).update(
                    lease_expires_at=timezone.now() - datetime.timedelta(seconds=1))
                dop = LiveClassDurableOperation.objects.get(live_class=self.lc)
                recovery_result.update(recover_crashed_provision_dop(dop.id))
            cloud.on_create = crash_recovery_during_create
            with self.assertRaises(ConcurrencyConflictError):
                provision_zoom_meeting(self.lc.id)
            self.assertEqual(cloud.meetings, {})
        self.assertEqual(recovery_result["status"], "RECOVERED")
        dop = LiveClassDurableOperation.objects.get(live_class=self.lc)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)
        self.assertFalse(ZoomMeetingRecord.objects.filter(live_class=self.lc).exists())
        self.assertEqual(HostReservation.objects.get(durable_operation=dop).status, HostReservation.Status.CANCELLED)
        statuses = sorted(ZoomVerifiedEvidence.objects.filter(dop_id=dop.id).values_list('evidence_type', 'http_status'))
        self.assertEqual(statuses, [("CRASH_RECOVERY_TEARDOWN", 204), ("ORPHAN_FENCING_TEARDOWN", 404)])


# =============================================================================
# B4 -- crash recovery
# =============================================================================
class B4CrashRecoveryTests(_LifecycleFixture, TestCase):
    def setUp(self):
        self.make_fixture()

    def _seed_cloud(self, cloud, orphans=1):
        ids = [cloud.add_meeting(self.HOST_ID, self.lc.title, self.start) for _ in range(orphans)]
        # Same title, next week's occurrence: must never be touched.
        other = cloud.add_meeting(self.HOST_ID, self.lc.title, self.start + datetime.timedelta(days=7))
        # Same title AND same start, but owned by the DB (e.g. a replacement): must never be touched.
        owned = cloud.add_meeting(self.HOST_ID, self.lc.title, self.start)
        other_lc = LiveClass.objects.create(course=self.course, title=self.lc.title, scheduled_start=self.start,
                                            duration_minutes=60, meeting_url="https://natyaarts.com/class/x")
        ZoomMeetingRecord.objects.create(zoom_meeting_id=owned, live_class=other_lc, state="ASSIGNED",
                                         is_current=True, host_account=self.host)
        return ids, other, owned

    def test_deletes_only_the_correlated_orphan_using_real_list_shape(self):
        dop, res = self.make_inflight_dop()
        with FakeZoomCloud() as cloud:
            (orphan,), other, owned = self._seed_cloud(cloud)
            result = recover_crashed_provision_dop(dop.id)
            self.assertEqual(result["status"], "RECOVERED")
            self.assertEqual(result["orphan_deleted"], orphan)
            self.assertNotIn(orphan, cloud.meetings)
            self.assertIn(other, cloud.meetings)
            self.assertIn(owned, cloud.meetings)
            self.assertEqual([c[1] for c in cloud.calls_of('DELETE')], [orphan])
        ev = ZoomVerifiedEvidence.objects.get(meeting_id=orphan)
        self.assertEqual((ev.evidence_type, ev.http_status, ev.dop_id), ("CRASH_RECOVERY_TEARDOWN", 204, dop.id))
        res.refresh_from_db()
        dop.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.CANCELLED)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)

    def test_delete_answering_404_is_a_confirmed_absence(self):
        dop, res = self.make_inflight_dop()
        with FakeZoomCloud() as cloud:
            (orphan,), _, _ = self._seed_cloud(cloud)
            cloud.fail('DELETE', orphan, 404)
            result = recover_crashed_provision_dop(dop.id)
        self.assertEqual(result["status"], "RECOVERED")
        self.assertEqual(ZoomVerifiedEvidence.objects.get(meeting_id=orphan).http_status, 404)

    def test_listing_failure_holds_reservation(self):
        for action in (500, 'timeout', 401, 429):
            with self.subTest(listing_failure=action):
                LiveClassDurableOperation.objects.filter(live_class=self.lc).update(status='COMPENSATED')
                HostReservation.objects.filter(live_class=self.lc).update(status='CANCELLED')
                dop, res = self.make_inflight_dop()
                with FakeZoomCloud() as cloud:
                    self._seed_cloud(cloud)
                    cloud.fail('LIST', self.HOST_ID, action)
                    result = recover_crashed_provision_dop(dop.id)
                    self.assertEqual(cloud.calls_of('DELETE'), [])
                self.assertEqual(result["status"], "HELD")
                res.refresh_from_db()
                dop.refresh_from_db()
                self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)
                self.assertEqual(dop.status, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN)
                self.assertIsNotNone(dop.quarantined_at)
                self.assertFalse(ZoomVerifiedEvidence.objects.filter(dop_id=dop.id).exists())
                ZoomMeetingRecord.objects.all().delete()
                LiveClass.objects.exclude(id=self.lc.id).delete()

    def test_unconfirmed_delete_holds_and_schedules_durable_retry(self):
        for action in (500, 'timeout', 'connection', 403):
            with self.subTest(delete_failure=action):
                LiveClassDurableOperation.objects.filter(live_class=self.lc).update(status='COMPENSATED')
                HostReservation.objects.filter(live_class=self.lc).update(status='CANCELLED')
                dop, res = self.make_inflight_dop()
                with FakeZoomCloud() as cloud:
                    (orphan,), _, _ = self._seed_cloud(cloud)
                    cloud.fail('DELETE', orphan, action)
                    result = recover_crashed_provision_dop(dop.id)
                    self.assertIn(orphan, cloud.meetings)
                self.assertEqual(result["status"], "HELD")
                self.assertFalse(ZoomVerifiedEvidence.objects.filter(meeting_id=orphan).exists(),
                                 "no evidence may be recorded for an unconfirmed delete")
                res.refresh_from_db()
                self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)
                retry = ZoomLifecycleOutbox.objects.get(payload__dop_id=dop.id,
                                                        event_type="ZOOM_MEETING_COMMIT_FAILED_NEEDS_TEARDOWN")
                self.assertEqual(retry.status, ZoomLifecycleOutbox.Status.PENDING)
                self.assertEqual(retry.payload["meeting_id"], orphan)
                ZoomMeetingRecord.objects.all().delete()
                LiveClass.objects.exclude(id=self.lc.id).delete()

    def test_no_correlated_meeting_is_not_proof_and_holds(self):
        dop, res = self.make_inflight_dop()
        with FakeZoomCloud() as cloud:
            self._seed_cloud(cloud, orphans=0)
            result = recover_crashed_provision_dop(dop.id)
            self.assertEqual(cloud.calls_of('DELETE'), [])
        self.assertEqual(result["status"], "HELD")
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

    def test_multiple_correlated_meetings_are_never_guessed(self):
        dop, res = self.make_inflight_dop()
        with FakeZoomCloud() as cloud:
            self._seed_cloud(cloud, orphans=2)
            result = recover_crashed_provision_dop(dop.id)
            self.assertEqual(cloud.calls_of('DELETE'), [])
        self.assertEqual(result["status"], "HELD")
        self.assertIn("refusing to choose", result["reason"])

    def test_topic_alone_never_correlates(self):
        dop, res = self.make_inflight_dop()
        with FakeZoomCloud() as cloud:
            cloud.add_meeting(self.HOST_ID, self.lc.title, self.start, duration=45)  # wrong duration
            cloud.add_meeting(self.HOST_ID, self.lc.title, self.start,
                              created_at=timezone.now() - datetime.timedelta(days=3))  # predates the DOP
            result = recover_crashed_provision_dop(dop.id)
            self.assertEqual(cloud.calls_of('DELETE'), [])
        self.assertEqual(result["status"], "HELD")

    def test_refuses_while_provisioning_lease_active(self):
        dop, _ = self.make_inflight_dop(lease_expired=False)
        with FakeZoomCloud() as cloud:
            with self.assertRaises(ConcurrencyConflictError):
                recover_crashed_provision_dop(dop.id)
            self.assertEqual(cloud.calls, [])

    def test_lost_claim_aborts_finalization(self):
        dop, res = self.make_inflight_dop()
        with FakeZoomCloud() as cloud:
            self._seed_cloud(cloud)
            cloud.hook('LIST', self.HOST_ID, lambda: LiveClassDurableOperation.objects.filter(id=dop.id).update(
                version=999, lease_worker=uuid.uuid4()))
            with self.assertRaises(ConcurrencyConflictError):
                recover_crashed_provision_dop(dop.id)
        res.refresh_from_db()
        dop.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.PROVISIONAL)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.IN_PROGRESS)


# =============================================================================
# B3 -- quarantine fails closed
# =============================================================================
class B3QuarantineTests(_LifecycleFixture, TestCase):
    def setUp(self):
        self.make_fixture()

    def assert_held(self, dop, res):
        dop.refresh_from_db()
        res.refresh_from_db()
        self.assertIn(dop.status, LiveClassDurableOperation.QUARANTINE_STATUSES)
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)
        self.assertIsNone(dop.lease_worker)

    def test_resolves_only_after_real_dual_get_404_and_clear_listing(self):
        dop, res = self.make_quarantined_dop()
        with FakeZoomCloud() as cloud:
            result = ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
            self.assertEqual(len(cloud.calls_of('GET')), 2, "production path must really probe Zoom twice")
            self.assertEqual(len(cloud.calls_of('LIST')), 1)
        self.assertEqual(result["status"], "RESOLVED")
        ev = ZoomVerifiedEvidence.objects.filter(dop_id=dop.id, evidence_type="DUAL_GET_QUARANTINE_VERIFICATION")
        self.assertEqual(sorted(ev.values_list('http_status', flat=True)), [404, 404])
        res.refresh_from_db()
        dop.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.RELEASED)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)

    def test_probe_failures_keep_quarantine(self):
        cases = [('exists', None), ('5xx', 500), ('timeout', 'timeout'), ('auth', 401),
                 ('rate_limit', 429), ('connection', 'connection')]
        for name, action in cases:
            with self.subTest(probe=name):
                LiveClassDurableOperation.objects.filter(live_class=self.lc).update(status='COMPENSATED')
                HostReservation.objects.filter(live_class=self.lc).update(status='RELEASED')
                mid = str(85099990100 + len(name))
                dop, res = self.make_quarantined_dop(meeting_id=mid)
                with FakeZoomCloud() as cloud:
                    if action is None:
                        cloud.add_meeting(self.HOST_ID, "unrelated", self.start, meeting_id=mid)
                    else:
                        cloud.fail('GET', mid, action)
                    with self.assertRaises(QuarantineVerificationError):
                        ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
                self.assert_held(dop, res)

    def test_listing_blocker_or_failure_keeps_quarantine(self):
        dop, res = self.make_quarantined_dop()
        with FakeZoomCloud() as cloud:
            cloud.add_meeting(self.HOST_ID, self.lc.title, self.start)  # unowned, same topic+start
            with self.assertRaises(QuarantineVerificationError):
                ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
        self.assert_held(dop, res)
        with FakeZoomCloud() as cloud:
            cloud.fail('LIST', self.HOST_ID, 'timeout')
            with self.assertRaises(QuarantineVerificationError):
                ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
        self.assert_held(dop, res)

    def test_grace_period_measured_from_quarantine_entry_not_creation(self):
        dop, res = self.make_quarantined_dop(minutes_ago=1)
        LiveClassDurableOperation.objects.filter(id=dop.id).update(
            created_at=timezone.now() - datetime.timedelta(hours=6))
        with FakeZoomCloud() as cloud:
            with self.assertRaises(QuarantinePeriodActiveError):
                ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
            self.assertEqual(cloud.calls, [])
        self.assert_held(dop, res)

    def test_no_caller_bypass_flag_exists(self):
        dop, _ = self.make_quarantined_dop(minutes_ago=1)
        with self.assertRaises(TypeError):
            ZoomQuarantineService.verify_and_resolve_quarantine(dop.id, force_bypass_timer=True)

    def test_non_quarantined_dop_is_rejected(self):
        dop, _ = self.make_inflight_dop()
        with self.assertRaises(QuarantineStateError):
            ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)

    def test_unverifiable_operation_stays_quarantined(self):
        dop, _ = self.make_quarantined_dop(meeting_id="", with_host=False)
        with FakeZoomCloud():
            with self.assertRaises(QuarantineVerificationError):
                ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
        dop.refresh_from_db()
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN)

    def test_active_foreign_lease_blocks_resolution(self):
        dop, res = self.make_quarantined_dop()
        LiveClassDurableOperation.objects.filter(id=dop.id).update(
            lease_worker=uuid.uuid4(), lease_expires_at=timezone.now() + datetime.timedelta(minutes=5))
        with FakeZoomCloud() as cloud:
            with self.assertRaises(ConcurrencyConflictError):
                ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
            self.assertEqual(cloud.calls, [])


# =============================================================================
# B2 -- evidence integrity (application boundary)
# =============================================================================
class B2EvidenceApplicationTests(_LifecycleFixture, TestCase):
    def setUp(self):
        self.make_fixture()

    def test_outcome_cannot_be_constructed_or_supplied_by_callers(self):
        with self.assertRaises(TypeError):
            ZoomCallOutcome(object(), "85012340001", "DELETE", 204, ZoomCallOutcome.CONFIRMED_DELETED)
        with self.assertRaises(TypeError):
            _record_evidence_from_outcome({"http_status": 204, "meeting_id": "85012340001"}, "CANCELLATION_SAGA_DELETE")
        with self.assertRaises(TypeError):
            ZoomCancellationSagaWorker.persist_cancellation_evidence("85012340001", 204, {})
        self.assertFalse(ZoomVerifiedEvidence.objects.exists())

    def test_unconfirmed_outcomes_are_refused_as_evidence(self):
        dop, _ = self.make_inflight_dop()
        with FakeZoomCloud() as cloud:
            mid = cloud.add_meeting(self.HOST_ID, "x", self.start)
            cloud.fail('DELETE', mid, 500)
            failed_delete = zoom_delete_outcome(mid)
            exists = zoom_get_outcome(mid)
        self.assertEqual((failed_delete.result, failed_delete.http_status), (ZoomCallOutcome.AMBIGUOUS, 500))
        self.assertEqual((exists.result, exists.http_status), (ZoomCallOutcome.EXISTS, 200))
        with self.assertRaises(ValueError):
            _record_evidence_from_outcome(failed_delete, "CRASH_RECOVERY_TEARDOWN", dop_id=dop.id)
        with self.assertRaises(ValueError):
            _record_evidence_from_outcome(exists, "DUAL_GET_QUARANTINE_VERIFICATION", dop_id=dop.id)
        with self.assertRaises(AttributeError):
            failed_delete.http_status = 204
        self.assertFalse(ZoomVerifiedEvidence.objects.exists())

    def test_error_text_containing_404_or_3001_is_not_treated_as_deleted(self):
        mid = "84043001404"  # a real-looking meeting id whose digits contain both markers
        for action, expected in ((500, ZoomCallOutcome.AMBIGUOUS), (403, ZoomCallOutcome.FAILED),
                                 (429, ZoomCallOutcome.FAILED), ('timeout', ZoomCallOutcome.AMBIGUOUS)):
            with self.subTest(action=action):
                with FakeZoomCloud() as cloud:
                    cloud.add_meeting(self.HOST_ID, "x", self.start, meeting_id=mid)
                    cloud.fail('DELETE', mid, action)
                    result = ZoomCancellationSagaWorker.execute_remote_delete(mid)
                self.assertFalse(result["confirmed"])
                self.assertEqual(result["action"], expected)

    def test_watchdog_records_actual_status_and_skips_unconfirmed(self):
        zmr = ZoomMeetingRecord.objects.create(zoom_meeting_id="84043001404", live_class=None,
                                               state="COMPENSATING", is_current=False, host_account=self.host)
        ZoomMeetingRecord.objects.filter(id=zmr.id).update(updated_at=timezone.now() - datetime.timedelta(minutes=5))
        with FakeZoomCloud() as cloud:
            cloud.add_meeting(self.HOST_ID, "x", self.start, meeting_id="84043001404")
            cloud.fail('DELETE', "84043001404", 500)
            self.assertEqual(ZoomCancellationSagaWorker.run_watchdog_recovery(stale_seconds=60), 0)
        zmr.refresh_from_db()
        self.assertEqual(zmr.state, "COMPENSATING")
        self.assertFalse(ZoomVerifiedEvidence.objects.exists())
        with FakeZoomCloud() as cloud:  # meeting already gone -> 404 is a confirmation
            self.assertEqual(ZoomCancellationSagaWorker.run_watchdog_recovery(stale_seconds=60), 1)
        zmr.refresh_from_db()
        self.assertEqual(zmr.state, "COMPENSATED")
        self.assertEqual(ZoomVerifiedEvidence.objects.get().http_status, 404)


# =============================================================================
# B2/B3 -- PostgreSQL evidence boundary and SQL quarantine resolver
# =============================================================================
class B2B3PostgresEvidenceTests(_LifecycleFixture, TestCase):
    def setUp(self):
        if not IS_PG:
            self.skipTest("PostgreSQL required")
        self.make_fixture()

    def sql(self, statement, params=None, role=None):
        # SET LOCAL ROLE would outlive a *released* savepoint, so reset it explicitly
        # on success; on error the savepoint rollback reverts it.
        with transaction.atomic():
            with connection.cursor() as cur:
                if role:
                    cur.execute(f"SET LOCAL ROLE {role}")
                cur.execute(statement, params or [])
                rows = cur.fetchall() if cur.description else None
                if role:
                    cur.execute("RESET ROLE")
                return rows

    def assert_sql_error(self, pattern, statement, params=None, role=None):
        with self.assertRaisesRegex(Exception, pattern):
            self.sql(statement, params, role)

    def lease(self, dop, prefix):
        worker = uuid.uuid4()
        LiveClassDurableOperation.objects.filter(id=dop.id).update(
            lease_worker=worker, lease_expires_at=timezone.now() + datetime.timedelta(minutes=5))
        return f"{prefix}:{worker}"

    def record(self, mid, etype, status, dop_id, worker='t'):
        return self.sql("SELECT fn_record_verified_evidence(%s,%s,%s,'{}'::jsonb,%s,%s)",
                        [mid, etype, status, worker, dop_id])[0][0]

    def test_application_and_admin_roles_cannot_write_evidence(self):
        dop, _ = self.make_inflight_dop()
        call = "SELECT fn_record_verified_evidence('85012340001','CANCELLATION_SAGA_DELETE',204,'{}'::jsonb,'x',%s)"
        for role in ('natya_app', 'natya_admin'):
            self.assert_sql_error('permission denied for function fn_record_verified_evidence', call, [dop.id], role)
            self.assert_sql_error('permission denied for table courses_zoomverifiedevidence',
                                  "INSERT INTO courses_zoomverifiedevidence (meeting_id, evidence_type, http_status, "
                                  "response_body, verified_at, worker_id) VALUES ('85012340001','CANCELLATION_SAGA_DELETE',"
                                  "204,'{}',now(),'x')", role=role)
            self.sql("SELECT count(*) FROM courses_zoomverifiedevidence", role=role)  # read-only access remains
        self.assert_sql_error('does not exist',
                              "SELECT fn_record_verified_evidence('85012340001','CANCELLATION_SAGA_DELETE',204,'{}'::jsonb,'x')")
        privs = self.sql("SELECT has_function_privilege('public', 'fn_record_verified_evidence(text,text,integer,jsonb,text,bigint)', 'EXECUTE')")
        self.assertEqual(privs[0][0], False)

    def test_owner_boundary_rejects_non_confirmations_and_unbound_evidence(self):
        dop, _ = self.make_inflight_dop()
        self.assert_sql_error('EVIDENCE_NOT_A_CONFIRMATION', "SELECT fn_record_verified_evidence('85012340001','DUAL_GET_QUARANTINE_VERIFICATION',204,'{}'::jsonb,'t',%s)", [dop.id])
        self.assert_sql_error('EVIDENCE_NOT_A_CONFIRMATION', "SELECT fn_record_verified_evidence('85012340001','CRASH_RECOVERY_TEARDOWN',500,'{}'::jsonb,'t',%s)", [dop.id])
        self.assert_sql_error('EVIDENCE_DOP_REQUIRED', "SELECT fn_record_verified_evidence('85012340001','DUAL_GET_QUARANTINE_VERIFICATION',404,'{}'::jsonb,'t',NULL)")
        self.assert_sql_error('EVIDENCE_DOP_REQUIRED', "SELECT fn_record_verified_evidence('85012340001','CRASH_RECOVERY_TEARDOWN',204,'{}'::jsonb,'t',987654321)")
        self.assert_sql_error('INVALID_EVIDENCE_TYPE', "SELECT fn_record_verified_evidence('85012340001','MADE_UP',204,'{}'::jsonb,'t',%s)", [dop.id])
        self.assert_sql_error('INVALID_EVIDENCE', "SELECT fn_record_verified_evidence('fake_mid','CANCELLATION_SAGA_DELETE',204,'{}'::jsonb,'t',NULL)")

    def test_evidence_must_be_bound_to_an_operation_that_expects_it(self):
        dop, _ = self.make_inflight_dop()  # IN_PROGRESS, provisioning lease already expired
        crash = 'CRASH_RECOVERY_TEARDOWN'
        self.assert_sql_error('EVIDENCE_NOT_BOUND', "SELECT fn_record_verified_evidence('85012340001',%s,204,'{}'::jsonb,%s,%s)",
                              [crash, f"crash_recovery:{dop.lease_worker}", dop.id])  # lease expired
        holder = self.lease(dop, 'crash_recovery')
        self.assert_sql_error('EVIDENCE_NOT_BOUND', "SELECT fn_record_verified_evidence('85012340001',%s,204,'{}'::jsonb,%s,%s)",
                              [crash, f"crash_recovery:{uuid.uuid4()}", dop.id])  # not the lease holder
        self.record('85012340001', crash, 204, dop.id, holder)

        q, _ = self.make_quarantined_dop(meeting_id='85099990001')
        probe = self.lease(q, 'quarantine')
        self.assert_sql_error('EVIDENCE_NOT_BOUND', "SELECT fn_record_verified_evidence('85099990009','DUAL_GET_QUARANTINE_VERIFICATION',404,'{}'::jsonb,%s,%s)",
                              [probe, q.id])  # unrelated meeting
        self.assert_sql_error('EVIDENCE_NOT_BOUND', "SELECT fn_record_verified_evidence('85099990001','DUAL_GET_QUARANTINE_VERIFICATION',404,'{}'::jsonb,%s,%s)",
                              [f"quarantine:{uuid.uuid4()}", q.id])  # not the resolver lease holder
        self.assert_sql_error('EVIDENCE_NOT_BOUND', "SELECT fn_record_verified_evidence('85099990001','DUAL_GET_QUARANTINE_VERIFICATION',404,'{}'::jsonb,%s,%s)",
                              [holder.replace('crash_recovery', 'quarantine'), dop.id])  # DOP not quarantined
        self.record('85099990001', 'DUAL_GET_QUARANTINE_VERIFICATION', 404, q.id, probe)

        self.assert_sql_error('EVIDENCE_NOT_BOUND', "SELECT fn_record_verified_evidence('85012340002','ORPHAN_FENCING_TEARDOWN',204,'{}'::jsonb,'g',%s)", [dop.id])
        LiveClassDurableOperation.objects.filter(id=dop.id).update(response_payload={"orphan_meeting_id": "85012340002"})
        self.record('85012340002', 'ORPHAN_FENCING_TEARDOWN', 204, dop.id, 'g')

        self.assert_sql_error('EVIDENCE_NOT_BOUND', "SELECT fn_record_verified_evidence('85012340003','CANCELLATION_SAGA_DELETE',204,'{}'::jsonb,'s',NULL)")
        ZoomMeetingRecord.objects.create(zoom_meeting_id="85012340003", live_class=None, state="COMPENSATING",
                                         is_current=False, host_account=self.host)
        self.record('85012340003', 'CANCELLATION_SAGA_DELETE', 204, None, 's')

        self.assert_sql_error('EVIDENCE_NOT_BOUND', "SELECT fn_record_verified_evidence('85012340004','OUTBOX_TEARDOWN_CONFIRMATION',204,'{}'::jsonb,'o',NULL)")

    def test_evidence_rows_are_immutable(self):
        dop, _ = self.make_inflight_dop()
        ev_id = self.record('85012340001', 'CRASH_RECOVERY_TEARDOWN', 404, dop.id, self.lease(dop, 'crash_recovery'))
        self.assert_sql_error('EVIDENCE_IMMUTABLE', "UPDATE courses_zoomverifiedevidence SET http_status = 204 WHERE id = %s", [ev_id])
        self.assert_sql_error('EVIDENCE_IMMUTABLE', "DELETE FROM courses_zoomverifiedevidence WHERE id = %s", [ev_id])

    def test_sql_evidence_only_resolver_is_disabled(self):
        mid = "85099990001"
        dop, res = self.make_quarantined_dop(meeting_id=mid)
        probe = self.lease(dop, 'quarantine')
        self.record(mid, 'DUAL_GET_QUARANTINE_VERIFICATION', 404, dop.id, probe)
        self.record(mid, 'DUAL_GET_QUARANTINE_VERIFICATION', 404, dop.id, probe)
        LiveClassDurableOperation.objects.filter(id=dop.id).update(lease_worker=None, lease_expires_at=None)
        resolve = "SELECT fn_resolve_quarantined_dop(%s, '{}'::jsonb)"
        self.assert_sql_error('SQL_QUARANTINE_RESOLUTION_DISABLED', resolve, [dop.id])
        for role in ('natya_app', 'natya_admin'):
            self.assert_sql_error('permission denied for function fn_resolve_quarantined_dop', resolve, [dop.id], role)
        res.refresh_from_db()
        dop.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN)

    def test_planted_evidence_never_releases_a_host(self):
        """Even rows planted directly by the owner login cannot release a host: the resolver probes Zoom live."""
        mid = "85099990001"
        dop, res = self.make_quarantined_dop(meeting_id=mid)
        for _ in range(2):
            self.sql("INSERT INTO courses_zoomverifiedevidence (meeting_id, evidence_type, http_status, response_body, "
                     "verified_at, worker_id, dop_id) VALUES (%s,'DUAL_GET_QUARANTINE_VERIFICATION',404,'{}',clock_timestamp(),'planted',%s)",
                     [mid, dop.id])
        with FakeZoomCloud() as cloud:
            cloud.add_meeting(self.HOST_ID, self.lc.title, self.start, meeting_id=mid)  # meeting still exists
            with self.assertRaises(QuarantineVerificationError):
                ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)


# =============================================================================
# B1 -- approvals: session-authenticated identity, immutability, rollback disabled
# =============================================================================
class B1ApprovalSecurityTests(TransactionTestCase):
    """Uses real, independently authenticated PostgreSQL sessions (one per approver)."""

    COMMIT_X = "75e0f0b0ea5bbcaec9874109108821a8cda59bf2"
    COMMIT_Y = "0" * 40

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not IS_PG:
            return
        import psycopg2
        cls.suffix = uuid.uuid4().hex[:8]
        cls.roles = {k: f"natya_t_{k}_{cls.suffix}" for k in ("alice", "bob", "outsider", "conflicted")}
        cls._su = cls._connect(connection.settings_dict['USER'])
        with cls._su.cursor() as cur:
            for role in cls.roles.values():
                cur.execute(f"CREATE ROLE {role} LOGIN")
            for k in ("alice", "bob", "conflicted"):
                cur.execute(f"GRANT natya_approver TO {cls.roles[k]}")
            cur.execute(f"GRANT natya_app TO {cls.roles['conflicted']}")
        cls._sessions = {k: cls._connect(r) for k, r in cls.roles.items()}

    @classmethod
    def tearDownClass(cls):
        if IS_PG:
            for s in cls._sessions.values():
                s.close()
            with cls._su.cursor() as cur:
                for role in cls.roles.values():
                    cur.execute(f"DROP ROLE IF EXISTS {role}")
            cls._su.close()
        super().tearDownClass()

    @classmethod
    def _connect(cls, user):
        import psycopg2
        s = connection.settings_dict
        conn = psycopg2.connect(host=s['HOST'] or '127.0.0.1', port=s['PORT'] or 5432, user=user,
                                password=s.get('PASSWORD') if user == s['USER'] else None, dbname=s['NAME'])
        conn.autocommit = True
        return conn

    def setUp(self):
        if not IS_PG:
            self.skipTest("PostgreSQL required")
        self.manifest = uuid.uuid4().hex + uuid.uuid4().hex  # 64 lowercase hex chars
        self.expiry = timezone.now() + datetime.timedelta(hours=2)

    def approve(self, who, approver_id=None, commit=None, nonce=None, manifest=None, expiry=None):
        with self._sessions[who].cursor() as cur:
            cur.execute("SELECT fn_approve_reconciliation_manifest(%s,%s,%s,%s,%s,%s)",
                        [manifest or self.manifest, commit or self.COMMIT_X, nonce or uuid.uuid4().hex,
                         expiry or self.expiry, approver_id or self.roles.get(who, who), "ignored-signature"])
            return cur.fetchone()[0]

    def test_expired_or_overlong_approval_windows_are_rejected(self):
        with self.assertRaisesRegex(Exception, 'APPROVAL_EXPIRED'):
            self.approve("alice", expiry=timezone.now() - datetime.timedelta(minutes=1))
        with self.assertRaisesRegex(Exception, 'APPROVAL_WINDOW_TOO_LONG'):
            self.approve("alice", expiry=timezone.now() + datetime.timedelta(days=3))
        self.assertEqual(self.su("SELECT count(*) FROM courses_zoomreconciliationapproval WHERE manifest_hash=%s",
                                 [self.manifest])[0][0], 0)

    def su(self, statement, params=None):
        with self._su.cursor() as cur:
            cur.execute(statement, params or [])
            return cur.fetchall() if cur.description else None

    def test_two_independent_sessions_reach_dual_approval_without_authorizing_execution(self):
        first = self.approve("alice")
        self.assertEqual((first["approver_count"], first["is_fully_approved"]), (1, False))
        second = self.approve("bob")
        self.assertEqual((second["approver_count"], second["is_fully_approved"]), (2, True))
        self.assertEqual(second["authorizes_execution"], False)
        self.assertEqual(sorted(r[0] for r in self.su(
            "SELECT approver_id FROM courses_zoomreconciliationapproval WHERE manifest_hash=%s", [self.manifest])),
            sorted([self.roles["alice"], self.roles["bob"]]))

    def test_caller_supplied_id_cannot_impersonate_another_approver(self):
        with self.assertRaisesRegex(Exception, 'APPROVER_ID_MISMATCH'):
            self.approve("alice", approver_id=self.roles["bob"])

    def test_unauthorized_privileged_and_conflicted_sessions_are_rejected(self):
        with self.assertRaisesRegex(Exception, 'permission denied for function fn_approve_reconciliation_manifest'):
            self.approve("outsider")
        with self.assertRaisesRegex(Exception, 'APPROVER_ROLE_CONFLICT'):
            self.approve("conflicted")
        with self.assertRaisesRegex(Exception, 'APPROVER_PRIVILEGED_SESSION'):
            with self._su.cursor() as cur:
                cur.execute("SELECT fn_approve_reconciliation_manifest(%s,%s,%s,%s,%s,'x')",
                            [self.manifest, self.COMMIT_X, uuid.uuid4().hex, self.expiry, connection.settings_dict['USER']])

    def test_direct_inserts_are_blocked_for_owner_and_admin_roles(self):
        insert = ("INSERT INTO courses_zoomreconciliationapproval (manifest_hash, target_commit, nonce, expires_at, "
                  "approver_id, signature, approved_at) VALUES (%s,%s,%s,%s,%s,'forged',now())")
        params = [self.manifest, self.COMMIT_X, uuid.uuid4().hex, self.expiry, self.roles["alice"]]
        with self.assertRaisesRegex(Exception, 'APPROVER_PRIVILEGED_SESSION'):
            self.su(insert, params)
        with self.assertRaisesRegex(Exception, 'permission denied for table courses_zoomreconciliationapproval'):
            with self._su.cursor() as cur:
                cur.execute("BEGIN; SET LOCAL ROLE natya_admin;")
                try:
                    cur.execute(insert, params)
                finally:
                    cur.execute("ROLLBACK")
        with self.assertRaisesRegex(Exception, 'permission denied'):
            with self._sessions["alice"].cursor() as cur:
                cur.execute(insert, params)

    def test_approvals_are_immutable_and_revocation_is_one_way(self):
        self.approve("alice")
        where = ("WHERE manifest_hash = %s AND approver_id = %s", [self.manifest, self.roles["alice"]])
        with self.assertRaisesRegex(Exception, 'APPROVAL_IMMUTABLE'):
            self.su(f"UPDATE courses_zoomreconciliationapproval SET target_commit = %s {where[0]}", [self.COMMIT_Y] + where[1])
        with self.assertRaisesRegex(Exception, 'APPROVAL_ALREADY_RECORDED'):
            self.approve("alice")
        with self._sessions["alice"].cursor() as cur:
            cur.execute("SELECT fn_revoke_manifest_approval(%s,%s)", [self.manifest, self.roles["alice"]])
        with self.assertRaisesRegex(Exception, 'APPROVAL_IMMUTABLE'):
            self.su(f"UPDATE courses_zoomreconciliationapproval SET revoked_at = NULL {where[0]}", where[1])
        with self.assertRaisesRegex(Exception, 'APPROVAL_ALREADY_RECORDED'):
            self.approve("alice")  # a revoked approval cannot be silently re-activated
        with self.assertRaisesRegex(Exception, 'APPROVAL_IMMUTABLE'):
            self.su(f"DELETE FROM courses_zoomreconciliationapproval {where[0]}", where[1])
        with self.assertRaisesRegex(Exception, 'APPROVER_ID_MISMATCH'):
            with self._sessions["bob"].cursor() as cur:
                cur.execute("SELECT fn_revoke_manifest_approval(%s,%s)", [self.manifest, self.roles["alice"]])

    def test_nonce_replay_and_commit_binding(self):
        nonce = uuid.uuid4().hex
        self.approve("alice", nonce=nonce)
        with self.assertRaisesRegex(Exception, 'NONCE_REPLAY_DETECTED'):
            self.approve("bob", nonce=nonce)
        result = self.approve("bob", commit=self.COMMIT_Y)
        self.assertEqual(result["approver_count"], 1, "approvals for a different commit must not combine")
        with self.assertRaisesRegex(Exception, 'INVALID_TARGET_COMMIT'):
            self.approve("bob", commit="abc123", manifest=uuid.uuid4().hex * 2)
        with self.assertRaisesRegex(Exception, 'INVALID_MANIFEST_HASH'):
            self.approve("bob", manifest="sha256_manifest_not_hex")

    def test_key_enrollment_and_rollback_execution_are_disabled(self):
        self.approve("alice")
        self.approve("bob")
        with self.assertRaisesRegex(Exception, 'APPROVER_KEY_ENROLLMENT_DISABLED'):
            self.su("SELECT fn_enroll_approver_key('mallory', 'key')")
        with self.assertRaisesRegex(Exception, 'ROLLBACK_EXECUTION_DISABLED'):
            self.su("SELECT fn_execute_exact_rollback_0038('any-token', '{\"hosts\": []}'::jsonb)")
        for fn in ("fn_execute_exact_rollback_0038(text,jsonb)", "fn_enroll_approver_key(text,text)",
                   "fn_approve_reconciliation_manifest(text,text,text,timestamptz,text,text)",
                   "fn_record_verified_evidence(text,text,integer,jsonb,text,bigint)"):
            for role in ("natya_app", "natya_admin"):
                self.assertEqual(self.su("SELECT has_function_privilege(%s, %s, 'EXECUTE')", [role, fn])[0][0], False,
                                 f"{role} must not execute {fn}")
        self.assertEqual(self.su("SELECT count(*) FROM courses_zoomapproversecrets WHERE secret_hash <> '' OR is_active")[0][0], 0)


# =============================================================================
# B4/B5 -- no Zoom HTTP call is made while a database transaction is open
# =============================================================================
class NoRemoteCallInsideTransactionTests(_LifecycleFixture, TransactionTestCase):
    def setUp(self):
        self.make_fixture()

    def assert_no_call_in_transaction(self, cloud):
        self.assertTrue(cloud.calls)
        self.assertEqual([c for c in cloud.calls if c[2]], [], "Zoom HTTP call made inside a DB transaction")

    def test_provision_fenced_teardown(self):
        with FakeZoomCloud() as cloud:
            cloud.on_create = lambda mid: ZoomCancellationSagaWorker.claim_and_lease(self.lc.id)
            with self.assertRaises(ConcurrencyConflictError):
                provision_zoom_meeting(self.lc.id)
            self.assertEqual(len(cloud.calls_of('DELETE')), 1)
            self.assert_no_call_in_transaction(cloud)

    def test_crash_recovery(self):
        dop, _ = self.make_inflight_dop()
        with FakeZoomCloud() as cloud:
            cloud.add_meeting(self.HOST_ID, self.lc.title, self.start)
            self.assertEqual(recover_crashed_provision_dop(dop.id)["status"], "RECOVERED")
            self.assert_no_call_in_transaction(cloud)

    def test_quarantine_resolution(self):
        dop, _ = self.make_quarantined_dop()
        with FakeZoomCloud() as cloud:
            self.assertEqual(ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)["status"], "RESOLVED")
            self.assert_no_call_in_transaction(cloud)


# =============================================================================
# B3/B4 runtime wiring -- Celery beat task, outbox task, management command
# =============================================================================
ZOOM_CREDS = dict(ZOOM_ACCOUNT_ID="acct-test", ZOOM_CLIENT_ID="client-test", ZOOM_CLIENT_SECRET="secret-test")


class WatchdogRuntimeWiringTests(_LifecycleFixture, TestCase):
    def setUp(self):
        self.make_fixture()

    def run_task(self, task):
        return task.apply().get()

    def test_watchdog_is_registered_and_scheduled(self):
        from django.conf import settings
        from core.celery import app
        from courses import tasks
        entry = settings.CELERY_BEAT_SCHEDULE['zoom-provisioning-watchdog']
        self.assertEqual(entry['task'], 'courses.tasks.zoom_provisioning_watchdog_task')
        self.assertIn('courses.tasks.zoom_provisioning_watchdog_task', app.tasks)
        for name in ('process-zoom-lifecycle-outbox', 'recover-stale-zoom-outbox'):
            self.assertIn(name, settings.CELERY_BEAT_SCHEDULE)
        self.assertEqual(app.tasks['courses.tasks.zoom_provisioning_watchdog_task'].name,
                         tasks.zoom_provisioning_watchdog_task.name)

    def test_task_without_zoom_credentials_skips_and_holds(self):
        from courses.tasks import zoom_provisioning_watchdog_task
        dop, res = self.make_inflight_dop()
        env = {k: v for k, v in __import__('os').environ.items() if not k.startswith('ZOOM_')}
        with mock.patch.dict('os.environ', env, clear=True), \
                self.settings(ZOOM_ACCOUNT_ID="", ZOOM_CLIENT_ID="", ZOOM_CLIENT_SECRET=""):
            self.assertEqual(self.run_task(zoom_provisioning_watchdog_task)["status"], "skipped_no_credentials")
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.PROVISIONAL)

    def test_end_to_end_crash_hold_retry_quarantine_release(self):
        """crash -> expired lease -> failed delete (held) -> outbox retry -> grace -> verified release."""
        from courses.tasks import (zoom_provisioning_watchdog_task, process_zoom_lifecycle_outbox_task)
        dop, res = self.make_inflight_dop()
        # A healthy in-flight provisioning (active lease) on another class must not be touched.
        lc_live = LiveClass.objects.create(course=self.course, title="Other Class", meeting_url="https://natyaarts.com/class/o",
                                           scheduled_start=self.start + datetime.timedelta(days=1), duration_minutes=60)
        live_dop = LiveClassDurableOperation.objects.create(
            idempotency_key=f"live-{uuid.uuid4().hex}", operation_type=LiveClassDurableOperation.OperationType.ZOOM_PROVISION,
            status=LiveClassDurableOperation.Status.IN_PROGRESS, live_class=lc_live, lease_worker=uuid.uuid4(),
            lease_expires_at=timezone.now() + datetime.timedelta(minutes=2), payload={"title": lc_live.title})
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            orphan = cloud.add_meeting(self.HOST_ID, self.lc.title, self.start)
            cloud.fail('DELETE', orphan, 500)

            sweep1 = self.run_task(zoom_provisioning_watchdog_task)
            self.assertEqual((sweep1["recovery_held"], sweep1["recovered"]), (1, 0))
            res.refresh_from_db()
            dop.refresh_from_db()
            live_dop.refresh_from_db()
            self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)
            self.assertEqual(dop.status, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN)
            self.assertEqual(live_dop.status, LiveClassDurableOperation.Status.IN_PROGRESS)
            outbox = ZoomLifecycleOutbox.objects.get(payload__dop_id=dop.id)

            self.run_task(process_zoom_lifecycle_outbox_task)  # Zoom still failing
            outbox.refresh_from_db()
            self.assertEqual(outbox.status, ZoomLifecycleOutbox.Status.RETRYING)
            self.assertIn(orphan, cloud.meetings)

            cloud.fail('DELETE', orphan, 500, times=0)
            ZoomLifecycleOutbox.objects.filter(id=outbox.id).update(next_retry_at=timezone.now() - datetime.timedelta(seconds=1))
            self.run_task(process_zoom_lifecycle_outbox_task)
            outbox.refresh_from_db()
            self.assertEqual(outbox.status, ZoomLifecycleOutbox.Status.COMPLETED)
            self.assertNotIn(orphan, cloud.meetings)

            sweep2 = self.run_task(zoom_provisioning_watchdog_task)  # still inside the grace period
            self.assertEqual(sweep2["resolved"], 0)
            res.refresh_from_db()
            self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

            LiveClassDurableOperation.objects.filter(id=dop.id).update(
                quarantined_at=timezone.now() - datetime.timedelta(minutes=16))
            sweep3 = self.run_task(zoom_provisioning_watchdog_task)
            self.assertEqual(sweep3["resolved"], 1)
        res.refresh_from_db()
        dop.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.RELEASED)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATED)
        probes = ZoomVerifiedEvidence.objects.filter(dop_id=dop.id, evidence_type="DUAL_GET_QUARANTINE_VERIFICATION")
        self.assertEqual(probes.count(), 2)

    def test_watchdog_holds_on_listing_failure_and_unresolved_quarantine(self):
        from courses.tasks import zoom_provisioning_watchdog_task
        dop, res = self.make_inflight_dop()
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            cloud.fail('LIST', self.HOST_ID, 'timeout')
            self.run_task(zoom_provisioning_watchdog_task)
            res.refresh_from_db()
            self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)
            LiveClassDurableOperation.objects.filter(id=dop.id).update(
                quarantined_at=timezone.now() - datetime.timedelta(minutes=16))
            sweep = self.run_task(zoom_provisioning_watchdog_task)  # listing still failing
            self.assertEqual((sweep["resolved"], sweep["quarantine_held"]), (0, 1))
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

    def test_management_command_paths_fail_closed(self):
        from django.core.management import call_command, CommandError
        from io import StringIO
        dop, res = self.make_quarantined_dop(minutes_ago=1)
        with FakeZoomCloud() as cloud:
            with self.assertRaisesRegex(CommandError, 'Held'):
                call_command('zoom_provisioning_watchdog', '--resolve-dop', str(dop.id))
            self.assertEqual(cloud.calls, [])
            out = StringIO()
            LiveClassDurableOperation.objects.filter(id=dop.id).update(
                quarantined_at=timezone.now() - datetime.timedelta(minutes=16))
            call_command('zoom_provisioning_watchdog', '--resolve-dop', str(dop.id), stdout=out)
            self.assertIn('"RESOLVED"', out.getvalue())
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.RELEASED)

    def test_rollback_has_no_application_entry_point(self):
        """Static exposure check: no URL route and no application module references the rollback function."""
        import inspect
        from django.urls import get_resolver
        import courses.views, courses.admin, courses.tasks, courses.services.zoom_lifecycle

        def walk(patterns, prefix=''):
            for p in patterns:
                if hasattr(p, 'url_patterns'):
                    yield from walk(p.url_patterns, prefix + str(p.pattern))
                else:
                    yield prefix + str(p.pattern)
        self.assertEqual([r for r in walk(get_resolver().url_patterns) if 'rollback' in r.lower()], [])
        for module in (courses.views, courses.admin, courses.tasks, courses.services.zoom_lifecycle):
            self.assertNotIn('fn_execute_exact_rollback', inspect.getsource(module))


# =============================================================================
# Actual application-login behaviour (real PostgreSQL logins, not catalog values)
# =============================================================================
class ApplicationLoginRuntimePermissionTests(_LifecycleFixture, TransactionTestCase):
    """
    'owner' emulates the production login: a NON-superuser that runs migrations and therefore
    owns every object (here: inherits the owner's privileges, cannot SET ROLE to it).
    'runtime' emulates a least-privilege application login (member of natya_app).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not IS_PG:
            return
        suffix = uuid.uuid4().hex[:8]
        cls.roles = {"owner": f"natya_t_ownerlogin_{suffix}", "runtime": f"natya_t_runtime_{suffix}"}
        cls._su = B1ApprovalSecurityTests._connect(connection.settings_dict['USER'])
        owner_role = connection.settings_dict['USER']
        with cls._su.cursor() as cur:
            cur.execute(f"CREATE ROLE {cls.roles['owner']} LOGIN NOSUPERUSER NOCREATEROLE")
            cur.execute(f"GRANT {owner_role} TO {cls.roles['owner']} WITH INHERIT TRUE, SET FALSE")
            cur.execute(f"CREATE ROLE {cls.roles['runtime']} LOGIN NOSUPERUSER")
            cur.execute(f"GRANT natya_app TO {cls.roles['runtime']}")
        cls._sessions = {k: B1ApprovalSecurityTests._connect(r) for k, r in cls.roles.items()}

    @classmethod
    def tearDownClass(cls):
        if IS_PG:
            for s in cls._sessions.values():
                s.close()
            with cls._su.cursor() as cur:
                for role in cls.roles.values():
                    cur.execute(f"DROP ROLE IF EXISTS {role}")
            cls._su.close()
        super().tearDownClass()

    def setUp(self):
        if not IS_PG:
            self.skipTest("PostgreSQL required")
        self.make_fixture()

    def run_as(self, who, statement, params=None):
        with self._sessions[who].cursor() as cur:
            cur.execute(statement, params or [])
            return cur.fetchall() if cur.description else None

    def test_owner_login_function_path_consistency_checks(self):
        """Consistency checks on the FUNCTION path only -- not a trust boundary (see the limitation test below)."""
        with self._sessions["owner"].cursor() as cur:
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = session_user")
            self.assertFalse(cur.fetchone()[0])
        dop, res = self.make_inflight_dop()
        worker = uuid.uuid4()
        LiveClassDurableOperation.objects.filter(id=dop.id).update(
            lease_worker=worker, lease_expires_at=timezone.now() + datetime.timedelta(minutes=5))
        call = "SELECT fn_record_verified_evidence(%s,'CRASH_RECOVERY_TEARDOWN',%s,'{}'::jsonb,%s,%s)"
        ev_id = self.run_as("owner", call, ["85012340001", 204, f"crash_recovery:{worker}", dop.id])[0][0]
        self.assertTrue(ZoomVerifiedEvidence.objects.filter(id=ev_id, http_status=204, dop_id=dop.id).exists())
        for args, pattern in (
            (["85012340001", 500, f"crash_recovery:{worker}", dop.id], 'EVIDENCE_NOT_A_CONFIRMATION'),
            (["85012340001", 204, f"crash_recovery:{uuid.uuid4()}", dop.id], 'EVIDENCE_NOT_BOUND'),
        ):
            with self.assertRaisesRegex(Exception, pattern):
                self.run_as("owner", call, args)
        with self.assertRaisesRegex(Exception, 'EVIDENCE_IMMUTABLE'):
            self.run_as("owner", "UPDATE courses_zoomverifiedevidence SET http_status = 404 WHERE id = %s", [ev_id])
        with self.assertRaisesRegex(Exception, 'EVIDENCE_IMMUTABLE'):
            self.run_as("owner", "DELETE FROM courses_zoomverifiedevidence WHERE id = %s", [ev_id])
        with self.assertRaisesRegex(Exception, 'SQL_QUARANTINE_RESOLUTION_DISABLED'):
            self.run_as("owner", "SELECT fn_resolve_quarantined_dop(%s, '{}'::jsonb)", [dop.id])
        with self.assertRaisesRegex(Exception, 'ROLLBACK_EXECUTION_DISABLED'):
            self.run_as("owner", "SELECT fn_execute_exact_rollback_0038('t', '{}'::jsonb)")
        with self.assertRaisesRegex(Exception, 'APPROVER_IS_LEDGER_OWNER|UNAUTHORIZED_APPROVER|permission denied'):
            self.run_as("owner", "SELECT fn_approve_reconciliation_manifest(%s,%s,%s,%s,%s,'x')",
                        [uuid.uuid4().hex * 2, "0" * 40, uuid.uuid4().hex, timezone.now() + datetime.timedelta(hours=1),
                         self.roles["owner"]])
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.PROVISIONAL)

    def test_documented_limitation_owner_login_can_bypass_evidence_controls(self):
        """
        KNOWN LIMITATION -- this asserts an INSECURE capability on purpose so it stays visible.
        The production login owns the schema, so it can insert evidence directly with any HTTP status,
        disable the immutability trigger and replace functions (done here inside a rolled-back
        transaction). Lease/DOP binding is therefore a consistency check, not proof that a Zoom
        response occurred. What still holds: no host is released on stored evidence -- every release
        path makes its own live Zoom calls.
        """
        mid = "85099990001"
        dop, res = self.make_quarantined_dop(meeting_id=mid)
        insert = ("INSERT INTO courses_zoomverifiedevidence (meeting_id, evidence_type, http_status, response_body, "
                  "verified_at, worker_id, dop_id) VALUES (%s,'DUAL_GET_QUARANTINE_VERIFICATION',404,'{}',clock_timestamp(),'fabricated',%s)")
        bypassed = []
        with self._sessions["owner"].cursor() as cur:
            cur.execute("BEGIN")
            try:
                cur.execute(insert, [mid, dop.id]); bypassed.append("direct INSERT with fabricated 404")
                cur.execute("ALTER TABLE courses_zoomverifiedevidence DISABLE TRIGGER trg_protect_verified_evidence")
                cur.execute("UPDATE courses_zoomverifiedevidence SET http_status = 404 WHERE worker_id = 'fabricated'")
                bypassed.append("immutability trigger disabled and row modified")
                cur.execute("CREATE OR REPLACE FUNCTION fn_resolve_quarantined_dop(p_dop_id bigint, p_resolution_evidence jsonb) "
                            "RETURNS boolean LANGUAGE sql AS 'SELECT true'")
                bypassed.append("SECURITY DEFINER function replaced")
            finally:
                cur.execute("ROLLBACK")
        self.assertEqual(len(bypassed), 3)
        # Fabricated rows that DO persist still cannot release the host: the resolver probes Zoom live.
        self.run_as("owner", insert, [mid, dop.id])
        self.run_as("owner", insert, [mid, dop.id])
        with FakeZoomCloud() as cloud:
            cloud.add_meeting(self.HOST_ID, self.lc.title, self.start, meeting_id=mid)
            with self.assertRaises(QuarantineVerificationError):
                ZoomQuarantineService.verify_and_resolve_quarantine(dop.id)
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)

    def test_least_privilege_runtime_login_cannot_write_resolve_rollback_or_approve(self):
        dop, _ = self.make_inflight_dop()
        attempts = [
            "SELECT fn_record_verified_evidence('85012340001','CRASH_RECOVERY_TEARDOWN',204,'{}'::jsonb,'x',%s)" % dop.id,
            "INSERT INTO courses_zoomverifiedevidence (meeting_id, evidence_type, http_status, response_body, verified_at, worker_id) "
            "VALUES ('85012340001','CANCELLATION_SAGA_DELETE',204,'{}',now(),'x')",
            "SELECT fn_resolve_quarantined_dop(%s, '{}'::jsonb)" % dop.id,
            "SELECT fn_execute_exact_rollback_0038('t', '{}'::jsonb)",
            "SELECT fn_enroll_approver_key('a', 'b')",
            "SELECT fn_approve_reconciliation_manifest('a','b','c',now(),'d','e')",
        ]
        for statement in attempts:
            with self.subTest(statement=statement[:60]):
                with self.assertRaisesRegex(Exception, 'permission denied'):
                    self.run_as("runtime", statement)
        self.assertFalse(ZoomVerifiedEvidence.objects.exists())
