"""
Regression & Concurrency Test Suite for 40 Simultaneous Zoom Live Classes.

Covers:
1. 40 teachers provisioning 40 independent dedicated Zoom hosts concurrently.
2. Independent progress across hosts (slow Zoom call on one host does not block others).
3. Same-host concurrent allocation rejection.
4. 40 simultaneous cold-cache OAuth token requests + storm prevention on failure.
5. HTTP 429 Retry-After handling, retry success, and exhausted retries.
6. Retryable provisioning failures preserve LiveClass records (no deletion).
7. Idempotent retry prevents duplicate Zoom meetings.
8. Verified matching meeting.ended webhook releases only its own reservation.
9. Zoom GET returning 'waiting' never releases a reservation.
10. Running meeting / unconfirmed end_meeting never releases a reservation.
11. Duplicate completion webhooks are harmless.
12. Completing teacher A's class never releases teacher B's host.
13. Ambiguous Zoom outcomes remain quarantined in MANUAL_REVIEW.
14. Recording uploads are routed to a separate Celery queue/worker from lifecycle tasks.
"""
import datetime
import json
import pathlib
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connections
from django.test import TransactionTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from courses.models import (
    Course,
    HostReservation,
    LiveBatch,
    LiveClass,
    LiveClassDurableOperation,
    ZoomHostAccount,
    ZoomMeetingRecord,
)
from courses.services.zoom import (
    ZoomAPIError,
    ZoomRateLimitError,
    ZoomService,
    ZoomTransientError,
)
from courses.services.zoom_lifecycle import (
    HostCapacityExhaustedError,
    RetryableZoomProvisionError,
    ZeroTeardownInvariantViolation,
    complete_zoom_meeting_reservation,
    provision_zoom_meeting,
)
from courses.views import LiveClassViewSet, ZoomWebhookView

User = get_user_model()

TEST_ZOOM_CREDS = {
    "ZOOM_ACCOUNT_ID": "acct_test_40",
    "ZOOM_CLIENT_ID": "client_test_40",
    "ZOOM_CLIENT_SECRET": "secret_test_40",
    "ZOOM_WEBHOOK_SECRET_TOKEN": "webhook_secret_test",
    "ZOOM_EXCLUDED_TEACHER_IDS": [],
}


@override_settings(**TEST_ZOOM_CREDS)
class FortyTeacherConcurrencyTests(TransactionTestCase):
    """
    Uses TransactionTestCase so real PostgreSQL row-level locks (SELECT ... FOR UPDATE)
    and multi-threaded connection handling are exercised across 40 concurrent threads.
    """

    def _create_teachers_and_hosts(self, count=40, start_dt=None):
        start_dt = start_dt or (timezone.now().replace(microsecond=0) + datetime.timedelta(days=2))
        course = Course.objects.create(title="Natya 40-Teacher Course", is_published=True)
        classes = []
        hosts = []
        teachers = []
        for i in range(count):
            t = User.objects.create_user(
                username=f"conc_teacher_{i}",
                email=f"conc_teacher_{i}@natyaarts.com",
                first_name=f"Teacher{i}",
                is_teacher=True,
            )
            h = ZoomHostAccount.objects.create(
                zoom_user_id=f"zoom_conc_{i:02d}",
                email=f"conc_teacher_{i}@natyaarts.com",
                display_name=f"Host {i}",
                status=ZoomHostAccount.Status.ACTIVE,
                account_type=ZoomHostAccount.AccountType.DEDICATED,
                assigned_instructor=t,
                max_concurrency=1,
            )
            b = LiveBatch.objects.create(course=course, instructor=t, batch_type=LiveBatch.BatchType.GROUP)
            lc = LiveClass.objects.create(
                course=course,
                batch=b,
                instructor=t,
                title=f"Concurrent Class {i}",
                scheduled_start=start_dt,
                duration_minutes=60,
                meeting_provider=LiveClass.MeetingProvider.ZOOM,
            )
            teachers.append(t)
            hosts.append(h)
            classes.append(lc)
        return course, teachers, hosts, classes

    def test_01_40_teachers_provision_40_unique_hosts_simultaneously(self):
        _, _, hosts, classes = self._create_teachers_and_hosts(count=40)

        def fake_create(host_id=None, topic=None, start_time_iso=None, duration_minutes=60, **kwargs):
            time.sleep(0.03)
            mid = str(80000000000 + abs(hash(f"{host_id}-{uuid.uuid4()}")) % 999999999)
            return {
                "meeting_id": mid,
                "uuid": f"uuid-{uuid.uuid4().hex[:10]}",
                "host_id": host_id,
                "join_url": f"https://zoom.us/j/{mid}",
                "host_url": f"https://zoom.us/s/{mid}",
                "password": "pass",
            }

        def run_one(lc_id):
            try:
                return ("OK", provision_zoom_meeting(live_class_id=lc_id))
            except Exception as exc:
                return ("ERR", f"{type(exc).__name__}: {exc}")
            finally:
                connections.close_all()

        results = []
        with mock.patch.object(ZoomService, "create_meeting", side_effect=fake_create):
            with ThreadPoolExecutor(max_workers=40) as pool:
                futures = [pool.submit(run_one, lc.id) for lc in classes]
                for fut in as_completed(futures):
                    results.append(fut.result())

        ok = [r for r in results if r[0] == "OK"]
        errs = [r for r in results if r[0] != "OK"]
        self.assertEqual(len(errs), 0, f"Unexpected errors: {errs}")
        self.assertEqual(len(ok), 40)

        active_res = HostReservation.objects.filter(
            live_class__in=classes,
            status=HostReservation.Status.ACTIVE,
        )
        self.assertEqual(active_res.count(), 40)
        self.assertEqual(active_res.values("host_account_id").distinct().count(), 40)
        self.assertEqual(
            ZoomMeetingRecord.objects.filter(live_class__in=classes, is_current=True).count(),
            40,
        )

    def test_02_slow_host_does_not_block_other_39_hosts(self):
        _, _, _, classes = self._create_teachers_and_hosts(count=40)
        slow_host_id = "zoom_conc_00"
        completion_times = {}
        lock = threading.Lock()
        t_start = time.monotonic()

        def fake_create(host_id=None, topic=None, start_time_iso=None, duration_minutes=60, **kwargs):
            if host_id == slow_host_id:
                time.sleep(4.0)
            else:
                time.sleep(0.01)
            mid = str(81000000000 + abs(hash(f"{host_id}-{uuid.uuid4()}")) % 999999999)
            return {
                "meeting_id": mid,
                "uuid": f"uuid-{uuid.uuid4().hex[:10]}",
                "host_id": host_id,
                "join_url": f"https://zoom.us/j/{mid}",
                "host_url": f"https://zoom.us/s/{mid}",
            }

        def run_one(idx, lc_id):
            try:
                res = provision_zoom_meeting(live_class_id=lc_id)
                with lock:
                    completion_times[idx] = time.monotonic() - t_start
                return res
            finally:
                connections.close_all()

        with mock.patch.object(ZoomService, "create_meeting", side_effect=fake_create):
            with ThreadPoolExecutor(max_workers=40) as pool:
                futures = [pool.submit(run_one, idx, lc.id) for idx, lc in enumerate(classes)]
                for fut in as_completed(futures):
                    fut.result()

        fast_times = [completion_times[i] for i in range(1, 40)]
        self.assertEqual(len(fast_times), 39)
        self.assertLess(max(fast_times), completion_times[0] - 0.5, "Fast hosts were blocked by slow host #0")
        self.assertGreaterEqual(completion_times[0], 3.8)

    def test_03_same_host_concurrent_allocation_rejects_second(self):
        start_dt = timezone.now().replace(microsecond=0) + datetime.timedelta(days=3)
        course = Course.objects.create(title="Shared Pool Test Course", is_published=True)
        t1 = User.objects.create_user(
            username="pool_t1", email="pool_t1@natyaarts.com", first_name="PoolT1", is_teacher=True
        )
        t2 = User.objects.create_user(
            username="pool_t2", email="pool_t2@natyaarts.com", first_name="PoolT2", is_teacher=True
        )
        shared_host = ZoomHostAccount.objects.create(
            zoom_user_id="zoom_shared_pool_01",
            email="sharedpool01@natyaarts.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.SHARED_POOL,
            max_concurrency=1,
        )
        b1 = LiveBatch.objects.create(course=course, instructor=t1, batch_type=LiveBatch.BatchType.GROUP)
        b2 = LiveBatch.objects.create(course=course, instructor=t2, batch_type=LiveBatch.BatchType.GROUP)
        lc1 = LiveClass.objects.create(
            course=course, batch=b1, instructor=t1, title="Shared Class 1",
            scheduled_start=start_dt, duration_minutes=60, meeting_provider=LiveClass.MeetingProvider.ZOOM,
        )
        lc2 = LiveClass.objects.create(
            course=course, batch=b2, instructor=t2, title="Shared Class 2",
            scheduled_start=start_dt, duration_minutes=60, meeting_provider=LiveClass.MeetingProvider.ZOOM,
        )

        def fake_create(host_id=None, **kwargs):
            time.sleep(0.15)
            mid = str(82000000000 + abs(hash(uuid.uuid4())) % 999999999)
            return {"meeting_id": mid, "uuid": "u1", "host_id": host_id, "join_url": "https://zoom.us/j/1"}

        def run_one(lc_id):
            try:
                return ("OK", provision_zoom_meeting(live_class_id=lc_id, preferred_host_id=shared_host.id))
            except HostCapacityExhaustedError as exc:
                return ("EXHAUSTED", str(exc))
            finally:
                connections.close_all()

        with mock.patch.object(ZoomService, "create_meeting", side_effect=fake_create):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = [f.result() for f in [pool.submit(run_one, lc1.id), pool.submit(run_one, lc2.id)]]

        statuses = sorted(r[0] for r in results)
        self.assertEqual(statuses, ["EXHAUSTED", "OK"])
        self.assertEqual(
            HostReservation.objects.filter(host_account=shared_host, status=HostReservation.Status.ACTIVE).count(),
            1,
        )

    def test_04_40_simultaneous_cold_cache_token_requests_and_failure_storm_guard(self):
        cache.clear()
        self.assertGreater(ZoomService.LOCK_WAIT_TIMEOUT, ZoomService.DEFAULT_TIMEOUT_SECONDS)

        oauth_calls = 0
        oauth_lock = threading.Lock()

        def fake_post(url, **kwargs):
            nonlocal oauth_calls
            with oauth_lock:
                oauth_calls += 1
            time.sleep(0.35)
            resp = mock.MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"access_token": "tok_cold_40", "expires_in": 3600}
            return resp

        with mock.patch("courses.services.zoom.requests.post", side_effect=fake_post):
            with ThreadPoolExecutor(max_workers=40) as pool:
                tokens = [f.result() for f in [pool.submit(ZoomService.get_access_token) for _ in range(40)]]

        self.assertEqual(oauth_calls, 1)
        self.assertEqual(tokens, ["tok_cold_40"] * 40)

        # Now verify failure storm guard: if OAuth fails, waiters fail fast without a 39x storm
        cache.clear()
        fail_calls = 0

        def fake_fail_post(url, **kwargs):
            nonlocal fail_calls
            with oauth_lock:
                fail_calls += 1
            time.sleep(0.2)
            resp = mock.MagicMock()
            resp.status_code = 503
            resp.text = "Service Unavailable"
            resp.json.return_value = {"message": "Service Unavailable"}
            return resp

        def get_tok_safe():
            try:
                return ("OK", ZoomService.get_access_token())
            except ZoomTransientError as exc:
                return ("TRANSIENT", str(exc))

        with mock.patch("courses.services.zoom.requests.post", side_effect=fake_fail_post):
            with ThreadPoolExecutor(max_workers=15) as pool:
                outcomes = [f.result() for f in [pool.submit(get_tok_safe) for _ in range(15)]]

        self.assertEqual(fail_calls, 1)
        self.assertTrue(all(o[0] == "TRANSIENT" for o in outcomes))
        cache.clear()


@override_settings(**TEST_ZOOM_CREDS)
class ZoomRateLimitAndCompletionTests(TestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.teacher_a = User.objects.create_user(
            username="teacher_a", email="teachera@natyaarts.com", first_name="Teacher A", is_teacher=True
        )
        self.teacher_b = User.objects.create_user(
            username="teacher_b", email="teacherb@natyaarts.com", first_name="Teacher B", is_teacher=True
        )
        self.host_a = ZoomHostAccount.objects.create(
            zoom_user_id="z_host_a_0001",
            email="teachera@natyaarts.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.teacher_a,
            max_concurrency=1,
        )
        self.host_b = ZoomHostAccount.objects.create(
            zoom_user_id="z_host_b_0002",
            email="teacherb@natyaarts.com",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.DEDICATED,
            assigned_instructor=self.teacher_b,
            max_concurrency=1,
        )
        self.course = Course.objects.create(title="Bharathanatyam", is_published=True)
        self.batch_a = LiveBatch.objects.create(course=self.course, instructor=self.teacher_a, batch_type=LiveBatch.BatchType.GROUP)
        self.batch_b = LiveBatch.objects.create(course=self.course, instructor=self.teacher_b, batch_type=LiveBatch.BatchType.GROUP)
        self.start_dt = timezone.now().replace(microsecond=0) + datetime.timedelta(days=1)

    def test_05_429_retry_after_succeeds_and_repeated_429_exhausts(self):
        resp_429 = mock.MagicMock(status_code=429, headers={"Retry-After": "0.1"}, text="Rate limit")
        resp_429.json.return_value = {"message": "Rate limit"}
        resp_201 = mock.MagicMock(status_code=201, headers={}, text="{}")
        resp_201.json.return_value = {
            "id": 91234567890,
            "uuid": "uuid-ok",
            "host_id": "z_host_a",
            "join_url": "https://zoom.us/j/91234567890",
            "start_url": "https://zoom.us/s/91234567890",
            "password": "123",
        }

        with mock.patch.object(ZoomService, "get_access_token", return_value="tok_test"), \
             mock.patch("courses.services.zoom.time.sleep") as mock_sleep, \
             mock.patch("courses.services.zoom.requests.post", side_effect=[resp_429, resp_429, resp_201]):
            res = ZoomService.create_meeting(host_id="z_host_a_0001", topic="Test 429")
            self.assertEqual(res["meeting_id"], "91234567890")
            self.assertEqual(mock_sleep.call_count, 2)

        # Repeated 429 beyond MAX_429_RETRIES raises ZoomRateLimitError
        with mock.patch.object(ZoomService, "get_access_token", return_value="tok_test"), \
             mock.patch("courses.services.zoom.time.sleep"), \
             mock.patch("courses.services.zoom.requests.post", return_value=resp_429):
            with self.assertRaises(ZoomRateLimitError):
                ZoomService.create_meeting(host_id="z_host_a_0001", topic="Test 429 Exhausted")

    def test_06_retryable_429_error_does_not_delete_live_class(self):
        factory = APIRequestFactory()
        payload = {
            "batch": self.batch_a.id,
            "title": "Preserved On 429",
            "scheduled_start": self.start_dt.isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
        }
        req = factory.post("/api/courses/live-classes/", data=payload, format="json")
        force_authenticate(req, user=self.teacher_a)

        with mock.patch.object(ZoomService, "create_meeting", side_effect=ZoomRateLimitError("HTTP 429", retry_after="2")):
            view = LiveClassViewSet.as_view({"post": "create"})
            response = view(req)

        self.assertEqual(response.status_code, 429)
        self.assertTrue(LiveClass.objects.filter(title="Preserved On 429").exists())
        lc = LiveClass.objects.get(title="Preserved On 429")
        self.assertEqual(lc.zoom_meeting_id, "")
        self.assertFalse(
            HostReservation.objects.filter(live_class=lc, status=HostReservation.Status.ACTIVE).exists()
        )

    def test_07_duplicate_provisioning_idempotency_no_duplicate_meeting(self):
        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch_a,
            instructor=self.teacher_a,
            title="Idempotent Class",
            scheduled_start=self.start_dt,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
        )
        with mock.patch.object(ZoomService, "create_meeting", return_value={
            "meeting_id": "92222222222",
            "uuid": "u-idem",
            "host_id": "z_host_a_0001",
            "join_url": "https://zoom.us/j/92222222222",
            "host_url": "https://zoom.us/s/92222222222",
        }) as mock_create:
            r1 = provision_zoom_meeting(live_class_id=lc.id, idempotency_key="idem-key-1")
            with self.assertRaises(ZeroTeardownInvariantViolation):
                provision_zoom_meeting(live_class_id=lc.id, idempotency_key="idem-key-1")

            # And API generate-zoom returns existing meeting without calling Zoom again
            factory = APIRequestFactory()
            req = factory.post(f"/api/courses/live-classes/{lc.id}/generate-zoom/")
            force_authenticate(req, user=self.teacher_a)
            resp = LiveClassViewSet.as_view({"post": "generate_zoom"})(req, pk=lc.id)
            self.assertEqual(resp.status_code, 200)

        self.assertEqual(r1["status"], "SUCCESS")
        self.assertEqual(mock_create.call_count, 1)
        self.assertEqual(ZoomMeetingRecord.objects.filter(live_class=lc).count(), 1)

    def _provision_pair(self):
        lc_a = LiveClass.objects.create(
            course=self.course, batch=self.batch_a, instructor=self.teacher_a,
            title="Class A", scheduled_start=self.start_dt, duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
        )
        lc_b = LiveClass.objects.create(
            course=self.course, batch=self.batch_b, instructor=self.teacher_b,
            title="Class B", scheduled_start=self.start_dt, duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
        )
        with mock.patch.object(ZoomService, "create_meeting", side_effect=[
            {"meeting_id": "93333333331", "uuid": "uuid-a", "host_id": "z_host_a", "join_url": "https://zoom.us/j/1", "host_url": "https://zoom.us/s/1"},
            {"meeting_id": "93333333332", "uuid": "uuid-b", "host_id": "z_host_b", "join_url": "https://zoom.us/j/2", "host_url": "https://zoom.us/s/2"},
        ]):
            provision_zoom_meeting(live_class_id=lc_a.id)
            provision_zoom_meeting(live_class_id=lc_b.id)
        lc_a.refresh_from_db()
        lc_b.refresh_from_db()
        return lc_a, lc_b

    def test_08_11_12_matching_meeting_ended_webhook_releases_only_own_and_is_idempotent(self):
        lc_a, lc_b = self._provision_pair()
        res_a = HostReservation.objects.get(live_class=lc_a, status=HostReservation.Status.ACTIVE)
        res_b = HostReservation.objects.get(live_class=lc_b, status=HostReservation.Status.ACTIVE)

        # Webhook for meeting A (with real HMAC-SHA256 Zoom webhook signature)
        import hashlib
        import hmac
        factory = APIRequestFactory()
        for idx in range(2):  # Send duplicate webhook to prove idempotency
            ts = str(int(time.time()) + idx)
            body = json.dumps({
                "event": "meeting.ended",
                "event_ts": int(ts),
                "payload": {"object": {"id": "93333333331", "uuid": "uuid-a"}},
            }).encode("utf-8")
            msg = f"v0:{ts}:{body.decode('utf-8')}".encode("utf-8")
            sig = "v0=" + hmac.new(TEST_ZOOM_CREDS["ZOOM_WEBHOOK_SECRET_TOKEN"].encode("utf-8"), msg, hashlib.sha256).hexdigest()
            req = factory.post(
                "/api/courses/zoom-webhook/",
                data=body,
                content_type="application/json",
                HTTP_X_ZM_REQUEST_TIMESTAMP=ts,
                HTTP_X_ZM_SIGNATURE=sig,
            )
            resp = ZoomWebhookView.as_view()(req)
            self.assertEqual(resp.status_code, 200)

        res_a.refresh_from_db()
        res_b.refresh_from_db()
        zmr_a = ZoomMeetingRecord.objects.get(zoom_meeting_id="93333333331")
        zmr_b = ZoomMeetingRecord.objects.get(zoom_meeting_id="93333333332")

        self.assertEqual(res_a.status, HostReservation.Status.COMPLETED)
        self.assertEqual(zmr_a.state, ZoomMeetingRecord.State.RELEASED)
        self.assertFalse(zmr_a.is_current)

        # Teacher B's reservation and meeting record remain completely ACTIVE
        self.assertEqual(res_b.status, HostReservation.Status.ACTIVE)
        self.assertEqual(zmr_b.state, ZoomMeetingRecord.State.ASSIGNED)
        self.assertTrue(zmr_b.is_current)

    def test_09_waiting_zoom_meeting_does_not_release_reservation(self):
        lc_a, _ = self._provision_pair()
        with mock.patch.object(ZoomService, "get_meeting", return_value={"meeting_id": "93333333331", "status": "waiting"}):
            # Calling without authoritative termination evidence must NOT release
            result = complete_zoom_meeting_reservation(
                live_class_id=lc_a.id,
                meeting_id="93333333331",
                source="get_waiting_check",
                end_on_zoom=False,
            )
        self.assertEqual(result["status"], "UNVERIFIED_NOT_RELEASED")
        self.assertTrue(
            HostReservation.objects.filter(live_class=lc_a, status=HostReservation.Status.ACTIVE).exists()
        )

    def test_10_running_meeting_unconfirmed_end_cannot_release_reservation(self):
        lc_a, _ = self._provision_pair()
        with mock.patch.object(ZoomService, "end_meeting", side_effect=ZoomTransientError("Zoom 503 timeout")):
            result = complete_zoom_meeting_reservation(
                live_class_id=lc_a.id,
                meeting_id="93333333331",
                source="explicit_end",
                end_on_zoom=True,
            )
        self.assertEqual(result["status"], "HELD_UNCONFIRMED_END")
        self.assertTrue(
            HostReservation.objects.filter(live_class=lc_a, status=HostReservation.Status.ACTIVE).exists()
        )

    def test_13_ambiguous_provision_quarantined_and_cannot_be_completed_blindly(self):
        lc_a = LiveClass.objects.create(
            course=self.course, batch=self.batch_a, instructor=self.teacher_a,
            title="Ambiguous Class", scheduled_start=self.start_dt, duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
        )
        with mock.patch.object(ZoomService, "create_meeting", side_effect=ZoomTransientError("Gateway timeout")):
            # Note: requests.Timeout/ZoomAmbiguousOutcomeError triggers OUTCOME_UNKNOWN + MANUAL_REVIEW
            pass

        from courses.services.zoom import ZoomAmbiguousOutcomeError
        with mock.patch.object(ZoomService, "create_meeting", side_effect=ZoomAmbiguousOutcomeError("504 Gateway Timeout")):
            res = provision_zoom_meeting(live_class_id=lc_a.id)

        self.assertEqual(res["status"], "OUTCOME_UNKNOWN")
        hr = HostReservation.objects.get(live_class=lc_a)
        self.assertEqual(hr.status, HostReservation.Status.MANUAL_REVIEW)

        # Completion service must keep MANUAL_REVIEW quarantined
        lc_a.zoom_meeting_id = "94444444444"
        lc_a.save(update_fields=["zoom_meeting_id"])
        comp = complete_zoom_meeting_reservation(
            live_class_id=lc_a.id,
            meeting_id="94444444444",
            source="webhook_meeting_ended",
        )
        self.assertEqual(comp["status"], "HELD_MANUAL_REVIEW")
        hr.refresh_from_db()
        self.assertEqual(hr.status, HostReservation.Status.MANUAL_REVIEW)

    def test_14_recording_uploads_isolated_from_lifecycle_celery_queue(self):
        routes = settings.CELERY_TASK_ROUTES
        self.assertEqual(routes["courses.tasks.zoom_provisioning_watchdog_task"]["queue"], "zoom_lifecycle")
        self.assertEqual(routes["courses.tasks.process_zoom_lifecycle_outbox_task"]["queue"], "zoom_lifecycle")
        self.assertEqual(routes["courses.tasks.recover_stale_zoom_outbox_task"]["queue"], "zoom_lifecycle")
        self.assertEqual(routes["courses.tasks.process_zoom_recording_completed"]["queue"], "recordings")
        self.assertEqual(routes["courses.tasks.recover_stale_zoom_recordings"]["queue"], "recordings")
        self.assertEqual(settings.CELERY_WORKER_PREFETCH_MULTIPLIER, 1)

        celery_cfg = (pathlib.Path(settings.BASE_DIR) / ".ebextensions" / "celery.config").read_text(encoding="utf-8")
        self.assertIn("celery-recordings-worker.service", celery_cfg)
        self.assertIn("-Q zoom_lifecycle,notifications,default", celery_cfg)
        self.assertIn("-Q recordings", celery_cfg)
        self.assertNotIn("--pool=solo", celery_cfg)

    def test_15_mismatched_meeting_uuid_cannot_release_reservation_and_missing_uuid_preserves_compat(self):
        lc_a, lc_b = self._provision_pair()
        res_a = HostReservation.objects.get(live_class=lc_a, status=HostReservation.Status.ACTIVE)

        # Mismatched UUID must return IGNORED_UUID_MISMATCH and leave reservation ACTIVE
        mismatch = complete_zoom_meeting_reservation(
            live_class_id=lc_a.id,
            meeting_id="93333333331",
            meeting_uuid="uuid-wrong-meeting",
            source="webhook_meeting_ended",
        )
        self.assertEqual(mismatch["status"], "IGNORED_UUID_MISMATCH")
        res_a.refresh_from_db()
        self.assertEqual(res_a.status, HostReservation.Status.ACTIVE)
        self.assertTrue(ZoomMeetingRecord.objects.get(zoom_meeting_id="93333333331").is_current)

        # Missing incoming UUID preserves compatibility and completes normally when meeting_id matches
        ok = complete_zoom_meeting_reservation(
            live_class_id=lc_a.id,
            meeting_id="93333333331",
            meeting_uuid=None,
            source="webhook_meeting_ended",
        )
        self.assertEqual(ok["status"], "COMPLETED")
        res_a.refresh_from_db()
        self.assertEqual(res_a.status, HostReservation.Status.COMPLETED)

    def test_16_post_401_refresh_then_5xx_raises_ambiguous_and_quarantines_in_manual_review(self):
        from courses.services.zoom import ZoomAmbiguousOutcomeError

        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch_a,
            instructor=self.teacher_a,
            title="401 Then 502 Class",
            scheduled_start=self.start_dt,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
        )
        resp_401 = mock.MagicMock(status_code=401, headers={}, text="Invalid access token")
        resp_502 = mock.MagicMock(status_code=502, headers={}, text="Bad Gateway")

        with mock.patch.object(ZoomService, "get_access_token", side_effect=["stale_tok", "fresh_tok"]), \
             mock.patch("courses.services.zoom.requests.post", side_effect=[resp_401, resp_502]) as mock_post:
            res = provision_zoom_meeting(live_class_id=lc.id)

        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(res["status"], "OUTCOME_UNKNOWN")
        hr = HostReservation.objects.get(live_class=lc)
        self.assertEqual(hr.status, HostReservation.Status.MANUAL_REVIEW)
        dop = LiveClassDurableOperation.objects.get(live_class=lc)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN)

    def test_17_oauth_refresh_lock_disabled_429_retry_and_released_on_all_failure_paths(self):
        import requests as req_lib
        from courses.services.zoom import ZoomAuthError

        self.assertLess(ZoomService.DEFAULT_TIMEOUT_SECONDS, ZoomService.LOCK_WAIT_TIMEOUT)
        self.assertLess(ZoomService.LOCK_WAIT_TIMEOUT, ZoomService.LOCK_TIMEOUT_SECONDS)

        # 1. OAuth 429 must NOT retry and must release lock
        cache.clear()
        resp_429 = mock.MagicMock(status_code=429, headers={"Retry-After": "1"}, text="Rate limited")
        resp_429.json.return_value = {"message": "Rate limited"}
        with mock.patch("courses.services.zoom.requests.post", return_value=resp_429) as mock_post:
            with self.assertRaises(ZoomRateLimitError):
                ZoomService.get_access_token()
        self.assertEqual(mock_post.call_count, 1)
        self.assertIsNone(cache.get(ZoomService.CACHE_KEY_REFRESH_LOCK))

        # 2. OAuth Timeout, 401, and 500 must each release lock
        for exc_or_status in [req_lib.Timeout("read timed out"), 401, 500]:
            cache.clear()
            if isinstance(exc_or_status, Exception):
                with mock.patch("courses.services.zoom.requests.post", side_effect=exc_or_status):
                    with self.assertRaises(ZoomTransientError):
                        ZoomService.get_access_token()
            else:
                resp_err = mock.MagicMock(status_code=exc_or_status, headers={}, text="Err")
                resp_err.json.return_value = {"message": "Err"}
                expected_exc = ZoomAuthError if exc_or_status == 401 else ZoomTransientError
                with mock.patch("courses.services.zoom.requests.post", return_value=resp_err):
                    with self.assertRaises(expected_exc):
                        ZoomService.get_access_token()
            self.assertIsNone(cache.get(ZoomService.CACHE_KEY_REFRESH_LOCK))

        # 3. OAuth 200 success releases lock
        cache.clear()
        resp_200 = mock.MagicMock(status_code=200, headers={})
        resp_200.json.return_value = {"access_token": "tok_ok", "expires_in": 3600}
        with mock.patch("courses.services.zoom.requests.post", return_value=resp_200):
            tok = ZoomService.get_access_token()
        self.assertEqual(tok, "tok_ok")
        self.assertIsNone(cache.get(ZoomService.CACHE_KEY_REFRESH_LOCK))
        cache.clear()

    def test_18_runtime_exclusion_of_owner_and_excluded_hosts(self):
        from courses.services.zoom_lifecycle import HostCapacityExhaustedError

        owner_host = ZoomHostAccount.objects.create(
            zoom_user_id="z_owner_0001",
            email="vijay.pn@natyaarts.com",
            display_name="Tenant Owner",
            status=ZoomHostAccount.Status.ACTIVE,
            account_type=ZoomHostAccount.AccountType.SHARED_POOL,
            max_concurrency=1,
        )
        lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch_a,
            instructor=self.teacher_a,
            title="Excluded Owner Host Check",
            scheduled_start=self.start_dt,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
        )
        with self.assertRaises(HostCapacityExhaustedError):
            provision_zoom_meeting(live_class_id=lc.id, preferred_host_id=owner_host.id)

        # Removing teacher_a's dedicated host must NOT fall back to owner_host in SHARED_POOL
        self.host_a.status = ZoomHostAccount.Status.INACTIVE
        self.host_a.save(update_fields=["status"])
        with self.assertRaises(HostCapacityExhaustedError):
            provision_zoom_meeting(live_class_id=lc.id)
        self.host_a.status = ZoomHostAccount.Status.ACTIVE
        self.host_a.save(update_fields=["status"])

    def test_19_update_meeting_and_list_users_handle_429_retry_after(self):
        resp_429 = mock.MagicMock(status_code=429, headers={"Retry-After": "0.01"}, text="Rate limit")
        resp_204 = mock.MagicMock(status_code=204, headers={}, text="")
        resp_200 = mock.MagicMock(status_code=200, headers={}, text="{}")
        resp_200.json.return_value = {"users": [{"id": "u1", "email": "t@natya.com", "type": 2, "status": "active"}]}

        with mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom.time.sleep"), \
             mock.patch("courses.services.zoom.requests.patch", side_effect=[resp_429, resp_204]) as mock_patch, \
             mock.patch("courses.services.zoom.requests.get", side_effect=[resp_429, resp_200]) as mock_get:
            upd = ZoomService.update_meeting("93333333331", topic="Updated")
            users = ZoomService.list_users()

        self.assertEqual(upd["status"], "updated")
        self.assertEqual(mock_patch.call_count, 2)
        self.assertEqual(len(users), 1)
        self.assertEqual(mock_get.call_count, 2)

    def test_20_recurring_series_recovers_from_single_transient_429_and_celery_t3_medium_limits(self):
        from rest_framework.test import APIClient
        from courses.services.zoom_lifecycle import RetryableZoomProvisionError

        client = APIClient()
        client.force_authenticate(user=self.teacher_a)

        call_counter = {"n": 0}
        real_provision = provision_zoom_meeting

        def flaky_provision(live_class_id, **kwargs):
            call_counter["n"] += 1
            if call_counter["n"] == 2:
                raise RetryableZoomProvisionError("Retryable Zoom provisioning error: HTTP 429")
            return real_provision(live_class_id=live_class_id, **kwargs)

        with mock.patch("courses.services.zoom_lifecycle.provision_zoom_meeting", side_effect=flaky_provision), \
             mock.patch.object(ZoomService, "get_access_token", return_value="tok"), \
             mock.patch("courses.services.zoom.requests.post", side_effect=lambda *a, **kw: mock.MagicMock(
                 status_code=201, headers={},
                 json=lambda: {"id": 94000000000 + call_counter["n"], "uuid": f"u-{call_counter['n']}", "join_url": "https://zoom.us/j/1", "start_url": "https://zoom.us/s/1"},
             )), \
             mock.patch("time.sleep"):
            resp = client.post(
                "/api/courses/live-classes/",
                {
                    "title": "Recurring Resilient",
                    "course": self.course.id,
                    "batch": self.batch_a.id,
                    "scheduled_start": (self.start_dt + datetime.timedelta(days=1)).isoformat(),
                    "duration_minutes": 60,
                    "meeting_provider": "ZOOM",
                    "recurrence": {"frequency": "DAILY", "occurrence_count": 2},
                },
                format="json",
            )
        self.assertEqual(resp.status_code, 201, getattr(resp, "data", None))
        self.assertEqual(call_counter["n"], 3)

        self.assertEqual(
            settings.CELERY_TASK_ROUTES["courses.tasks.process_zoom_participant_webhook_task"]["queue"],
            "notifications",
        )
        celery_cfg = (pathlib.Path(settings.BASE_DIR) / ".ebextensions" / "celery.config").read_text(encoding="utf-8")
        self.assertIn("-Q recordings --pool=prefork --concurrency=1", celery_cfg)
