"""
Release-closure regression tests for audit items B7 (cancellation saga lease/finalization),
B8 (webhook-event write/truncate capabilities under real logins), B9 (compensation lock
ordering and fail-closed behaviour) and B10 (recurring-series rollback).

All Zoom traffic goes through the real ZoomService against FakeZoomCloud (HTTP layer only).
PostgreSQL-specific tests skip on other backends and are meant for the isolated local cluster.
"""
import datetime
import threading
import time
import uuid

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from courses.models import (
    Course, LiveBatch, LiveClass, LiveClassDurableOperation, ZoomHostAccount, HostReservation,
    ZoomMeetingRecord, ZoomLifecycleOutbox, ZoomVerifiedEvidence, RecurrenceRule,
)
from courses.services.zoom_lifecycle import (
    ZoomCancellationSagaWorker, compensate_zoom_operation, provision_zoom_meeting, cancel_zoom_meeting,
    ConcurrencyConflictError, ZoomLifecycleError,
)
from courses.test_zoom_phase_b5_repairs import FakeZoomCloud, _LifecycleFixture, IS_PG, ZOOM_CREDS

User = get_user_model()


def _pg_connect(user, autocommit=True):
    import psycopg2
    s = connection.settings_dict
    conn = psycopg2.connect(host=s['HOST'] or '127.0.0.1', port=s['PORT'] or 5432, user=user,
                            password=s.get('PASSWORD') if user == s['USER'] else None, dbname=s['NAME'])
    conn.autocommit = autocommit
    return conn


# =============================================================================
# B7 -- cancellation saga: durable lease, fenced finalization, bound reservation
# =============================================================================
class B7CancellationSagaTests(_LifecycleFixture, TestCase):
    def setUp(self):
        self.make_fixture()
        LiveClass.objects.filter(id=self.lc.id).update(meeting_provider=LiveClass.MeetingProvider.ZOOM)

    def make_assigned(self, meeting_id, lc=None, generation=1, start=None, res_status=HostReservation.Status.ACTIVE):
        lc = lc or self.lc
        start = start or lc.scheduled_start
        res = HostReservation.objects.create(
            live_class=lc, host_account=self.host, scheduled_start=start,
            scheduled_end=start + datetime.timedelta(minutes=60), status=res_status,
            generation=generation)
        zmr = ZoomMeetingRecord.objects.create(
            zoom_meeting_id=meeting_id, live_class=lc, state="ASSIGNED", is_current=True, host_account=self.host,
            generation=generation, scheduled_start=start, scheduled_end=start + datetime.timedelta(minutes=60))
        LiveClass.objects.filter(id=lc.id).update(zoom_meeting_id=meeting_id)
        return res, zmr

    def expire(self, zmr):
        ZoomMeetingRecord.objects.filter(id=zmr.id).update(lease_expires_at=timezone.now() - datetime.timedelta(seconds=1))

    def test_lease_is_durable_and_finalization_is_fenced(self):
        res, zmr = self.make_assigned("85077700001")
        w1, w2 = uuid.uuid4(), uuid.uuid4()
        ZoomCancellationSagaWorker.claim_and_lease(self.lc.id, worker_id=w1, lease_seconds=60)
        zmr.refresh_from_db()
        self.assertEqual((zmr.state, zmr.lease_worker, zmr.historical_live_class_id), ("COMPENSATING", w1, self.lc.id))
        self.assertGreater(zmr.lease_expires_at, timezone.now())
        self.assertFalse(ZoomCancellationSagaWorker.finalize_cancellation(self.lc.id, worker_id=w2))
        with self.assertRaises(ValueError):
            ZoomCancellationSagaWorker.finalize_cancellation(self.lc.id)
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.ACTIVE)
        self.assertTrue(ZoomCancellationSagaWorker.finalize_cancellation(self.lc.id, worker_id=w1))
        res.refresh_from_db()
        zmr.refresh_from_db()
        self.assertEqual((res.status, zmr.state), (HostReservation.Status.CANCELLED, "COMPENSATED"))

    def test_watchdog_never_takes_over_a_live_lease(self):
        _, zmr = self.make_assigned("85077700001")
        w1 = uuid.uuid4()
        ZoomCancellationSagaWorker.claim_and_lease(self.lc.id, worker_id=w1, lease_seconds=300)
        ZoomMeetingRecord.objects.filter(id=zmr.id).update(updated_at=timezone.now() - datetime.timedelta(hours=1))
        with FakeZoomCloud() as cloud:
            self.assertEqual(ZoomCancellationSagaWorker.run_watchdog_recovery(stale_seconds=60), 0)
            self.assertEqual(cloud.calls, [])
        zmr.refresh_from_db()
        self.assertEqual(zmr.lease_worker, w1)

    def test_watchdog_finalization_cancels_only_the_bound_reservation(self):
        # The superseded meeting's reservation is DRAINING (only one ACTIVE reservation per class).
        old_res, old_zmr = self.make_assigned("85077700001", generation=1, res_status=HostReservation.Status.DRAINING)
        ZoomCancellationSagaWorker.claim_and_lease(self.lc.id, worker_id=uuid.uuid4())
        self.expire(old_zmr)
        # Replacement on the SAME class and host (new generation) -- must survive.
        new_res, new_zmr = self.make_assigned("85077700002", generation=2)
        # Same host and generation, but another class -- must survive.
        other_lc = LiveClass.objects.create(course=self.course, title="Other", meeting_url="https://natyaarts.com/c/o",
                                            meeting_provider=LiveClass.MeetingProvider.ZOOM,
                                            scheduled_start=self.start + datetime.timedelta(days=3), duration_minutes=60)
        other_res, _ = self.make_assigned("85077700003", lc=other_lc, generation=1)
        with FakeZoomCloud() as cloud:
            for mid in ("85077700001", "85077700002", "85077700003"):
                cloud.add_meeting(self.HOST_ID, "x", self.start, meeting_id=mid)
            self.assertEqual(ZoomCancellationSagaWorker.run_watchdog_recovery(stale_seconds=60), 1)
            self.assertEqual([c[1] for c in cloud.calls_of('DELETE')], ["85077700001"])
        for obj in (old_res, new_res, other_res, old_zmr, new_zmr):
            obj.refresh_from_db()
        self.assertEqual(old_res.status, HostReservation.Status.CANCELLED)
        self.assertEqual(new_res.status, HostReservation.Status.ACTIVE)
        self.assertEqual(other_res.status, HostReservation.Status.ACTIVE)
        self.assertEqual((old_zmr.state, new_zmr.state, new_zmr.is_current), ("COMPENSATED", "ASSIGNED", True))
        self.lc.refresh_from_db()
        self.assertEqual(self.lc.zoom_meeting_id, "85077700002")

    def test_original_worker_cannot_finalize_after_takeover(self):
        res, zmr = self.make_assigned("85077700001")
        w1 = uuid.uuid4()
        ZoomCancellationSagaWorker.claim_and_lease(self.lc.id, worker_id=w1)
        self.expire(zmr)
        with FakeZoomCloud() as cloud:
            cloud.add_meeting(self.HOST_ID, "x", self.start, meeting_id="85077700001")
            self.assertEqual(ZoomCancellationSagaWorker.run_watchdog_recovery(stale_seconds=60), 1)
        self.assertFalse(ZoomCancellationSagaWorker.finalize_cancellation(self.lc.id, worker_id=w1))

    def test_unconfirmed_watchdog_delete_releases_lease_and_holds(self):
        res, zmr = self.make_assigned("85077700001")
        ZoomCancellationSagaWorker.claim_and_lease(self.lc.id, worker_id=uuid.uuid4())
        self.expire(zmr)
        with FakeZoomCloud() as cloud:
            cloud.add_meeting(self.HOST_ID, "x", self.start, meeting_id="85077700001")
            cloud.fail('DELETE', "85077700001", 500)
            self.assertEqual(ZoomCancellationSagaWorker.run_watchdog_recovery(stale_seconds=60), 0)
        zmr.refresh_from_db()
        res.refresh_from_db()
        self.assertEqual(zmr.state, "COMPENSATING")
        self.assertLessEqual(zmr.lease_expires_at, timezone.now())
        self.assertEqual(res.status, HostReservation.Status.ACTIVE)
        self.assertFalse(ZoomVerifiedEvidence.objects.exists())

    def test_ambiguous_cancel_is_recovered_by_the_scheduled_watchdog(self):
        from courses.tasks import zoom_provisioning_watchdog_task
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            meeting = provision_zoom_meeting(self.lc.id)["meeting_id"]
            cloud.fail('DELETE', meeting, 500)
            with self.assertRaises(ZoomLifecycleError):
                cancel_zoom_meeting(self.lc.id)
            res = HostReservation.objects.get(live_class=self.lc)
            zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id=meeting)
            self.assertEqual((res.status, zmr.state), (HostReservation.Status.MANUAL_REVIEW, "COMPENSATING"))
            ZoomMeetingRecord.objects.filter(id=zmr.id).update(updated_at=timezone.now() - datetime.timedelta(minutes=5))
            cloud.fail('DELETE', meeting, 500, times=0)
            report = zoom_provisioning_watchdog_task.apply().get()
            self.assertEqual(report["cancellations_finalized"], 1)
            self.assertNotIn(meeting, cloud.meetings)
        res.refresh_from_db()
        zmr.refresh_from_db()
        self.assertEqual((res.status, zmr.state), (HostReservation.Status.CANCELLED, "COMPENSATED"))
        self.assertEqual(ZoomVerifiedEvidence.objects.get(meeting_id=meeting).http_status, 204)


# =============================================================================
# B9 -- compensation: global lock order, fail-closed, fenced
# =============================================================================
class B9CompensationTests(_LifecycleFixture, TestCase):
    def setUp(self):
        self.make_fixture()

    def provisioned(self, cloud):
        meeting = provision_zoom_meeting(self.lc.id)["meeting_id"]
        dop = LiveClassDurableOperation.objects.filter(live_class=self.lc).order_by('-id').first()
        return meeting, dop, HostReservation.objects.get(durable_operation=dop)

    def test_confirmed_delete_compensates(self):
        with FakeZoomCloud() as cloud:
            meeting, dop, res = self.provisioned(cloud)
            self.assertEqual(compensate_zoom_operation(dop.id)["status"], "COMPENSATED")
            self.assertNotIn(meeting, cloud.meetings)
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.CANCELLED)
        self.assertEqual(ZoomMeetingRecord.objects.get(zoom_meeting_id=meeting).state, "COMPENSATED")

    def test_unconfirmed_delete_holds_host_and_queues_teardown(self):
        for action in (500, 'timeout', 403):
            with self.subTest(delete_failure=action):
                LiveClassDurableOperation.objects.filter(live_class=self.lc).update(status='COMPENSATED')
                HostReservation.objects.filter(live_class=self.lc).exclude(
                    status__in=['CANCELLED', 'RELEASED', 'COMPLETED']).update(status='CANCELLED')
                ZoomMeetingRecord.objects.filter(live_class=self.lc).update(is_current=False, state='RELEASED')
                LiveClass.objects.filter(id=self.lc.id).update(zoom_meeting_id='')
                ZoomLifecycleOutbox.objects.all().delete()
                with FakeZoomCloud() as cloud:
                    meeting, dop, res = self.provisioned(cloud)
                    cloud.fail('DELETE', meeting, action)
                    result = compensate_zoom_operation(dop.id)
                    self.assertIn(meeting, cloud.meetings)
                self.assertEqual(result["status"], "HELD")
                dop.refresh_from_db()
                res.refresh_from_db()
                self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPENSATION_REQUIRED)
                self.assertEqual(dop.response_payload["orphan_meeting_id"], meeting)
                self.assertEqual(res.status, HostReservation.Status.MANUAL_REVIEW)
                self.assertTrue(ZoomLifecycleOutbox.objects.filter(
                    payload__meeting_id=meeting, status=ZoomLifecycleOutbox.Status.PENDING).exists())

    def test_terminal_reservations_are_never_rewritten(self):
        dop, res = self.make_inflight_dop()
        released = HostReservation.objects.create(
            live_class=self.lc, host_account=self.host, scheduled_start=self.start,
            scheduled_end=self.start + datetime.timedelta(minutes=60), status=HostReservation.Status.RELEASED,
            durable_operation=dop, generation=self.next_generation())
        self.assertEqual(compensate_zoom_operation(dop.id)["status"], "COMPENSATED")
        released.refresh_from_db()
        res.refresh_from_db()
        self.assertEqual(released.status, HostReservation.Status.RELEASED)
        self.assertEqual(res.status, HostReservation.Status.CANCELLED)

    def test_lost_claim_changes_nothing(self):
        with FakeZoomCloud() as cloud:
            meeting, dop, res = self.provisioned(cloud)
            cloud.hook('DELETE', meeting, lambda: LiveClassDurableOperation.objects.filter(id=dop.id).update(version=999))
            with self.assertRaises(ConcurrencyConflictError):
                compensate_zoom_operation(dop.id)
        res.refresh_from_db()
        self.assertEqual(res.status, HostReservation.Status.ACTIVE)

    def test_foreign_active_lease_is_rejected(self):
        dop, _ = self.make_inflight_dop(lease_expired=False)
        with self.assertRaisesRegex(ConcurrencyConflictError, "Fencing violation"):
            compensate_zoom_operation(dop.id, worker_id=uuid.uuid4())


class B9CompensationDeadlockTests(_LifecycleFixture, TransactionTestCase):
    """Real independent sessions: a LiveClass-first holder (like claim_and_lease) vs compensation."""

    def setUp(self):
        if not IS_PG:
            self.skipTest("PostgreSQL required")
        self.make_fixture()

    def _deadlocks(self):
        c = _pg_connect(connection.settings_dict['USER'])
        try:
            with c.cursor() as k:
                k.execute("SELECT pg_stat_clear_snapshot()")
                k.execute("SELECT deadlocks FROM pg_stat_database WHERE datname = current_database()")
                return k.fetchone()[0]
        finally:
            c.close()

    def test_compensation_follows_global_lock_order_without_deadlock(self):
        dop, _ = self.make_inflight_dop()
        before = self._deadlocks()
        holder = _pg_connect(connection.settings_dict['USER'], autocommit=False)
        cur = holder.cursor()
        cur.execute("SELECT pg_backend_pid()")
        holder_pid = cur.fetchone()[0]
        cur.execute("SELECT 1 FROM courses_liveclass WHERE id = %s FOR UPDATE", [self.lc.id])   # L1

        result = {}

        def run():
            try:
                result['value'] = compensate_zoom_operation(dop.id)
            except Exception as exc:
                result['error'] = exc
            finally:
                connection.close()

        t = threading.Thread(target=run)
        t.start()
        mon = _pg_connect(connection.settings_dict['USER'])
        blocked = False
        for _ in range(200):
            with mon.cursor() as k:
                k.execute("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
                          "AND wait_event_type = 'Lock' AND pid <> %s", [holder_pid])
                if k.fetchone()[0]:
                    blocked = True
                    break
            time.sleep(0.05)
        mon.close()
        self.assertTrue(blocked, "compensation should be waiting on the LiveClass lock")

        # The holder now takes L2, as claim_and_lease does. With the old DOP-before-LiveClass
        # order this deadlocked; with the global order the DOP row is still free.
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("SELECT 1 FROM courses_liveclassdurableoperation WHERE id = %s FOR UPDATE", [dop.id])
        holder.commit()
        holder.close()
        t.join(30)
        self.assertFalse(t.is_alive())
        self.assertNotIn('error', result, result.get('error'))
        self.assertEqual(result['value']['status'], "COMPENSATED")
        time.sleep(1.2)  # let cumulative statistics flush
        self.assertEqual(self._deadlocks(), before)


# =============================================================================
# B8 -- webhook events: owner login vs runtime role (real logins)
# =============================================================================
class B8WebhookCapabilityTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not IS_PG:
            return
        suffix = uuid.uuid4().hex[:8]
        cls.roles = {"owner": f"natya_t_wh_owner_{suffix}", "runtime": f"natya_t_wh_runtime_{suffix}"}
        cls._su = _pg_connect(connection.settings_dict['USER'])
        with cls._su.cursor() as cur:
            cur.execute(f"CREATE ROLE {cls.roles['owner']} LOGIN NOSUPERUSER NOCREATEROLE")
            cur.execute(f"GRANT {connection.settings_dict['USER']} TO {cls.roles['owner']} WITH INHERIT TRUE, SET FALSE")
            cur.execute(f"CREATE ROLE {cls.roles['runtime']} LOGIN NOSUPERUSER")
            cur.execute(f"GRANT natya_app TO {cls.roles['runtime']}")
        cls._sessions = {k: _pg_connect(r) for k, r in cls.roles.items()}

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

    def run_as(self, who, statement, params=None):
        with self._sessions[who].cursor() as cur:
            cur.execute(statement, params or [])
            return cur.fetchall() if cur.description else None

    def in_rolled_back_tx(self, who, *statements):
        with self._sessions[who].cursor() as cur:
            cur.execute("BEGIN")
            try:
                for st in statements:
                    cur.execute(st)
            finally:
                cur.execute("ROLLBACK")

    def test_runtime_role_capabilities(self):
        event_id = self.run_as("runtime", "SELECT id FROM fn_record_authenticated_webhook(%s, 'meeting.started', '{}'::jsonb)",
                               [f"evt-{uuid.uuid4().hex}"])[0][0]
        self.run_as("runtime", "UPDATE courses_zoomwebhookevent SET status = 'PROCESSED' WHERE id = %s", [event_id])
        denied = [
            "INSERT INTO courses_zoomwebhookevent (event_id, event_type, payload, status, error_message, retry_count, received_at) "
            "VALUES ('direct', 't', '{}', 'RECEIVED', '', 0, now())",
            f"UPDATE courses_zoomwebhookevent SET payload = '{{}}'::jsonb WHERE id = {event_id}",
            f"DELETE FROM courses_zoomwebhookevent WHERE id = {event_id}",
            "TRUNCATE courses_zoomwebhookevent",
            "SELECT * FROM courses_zoomwebhookvault",
        ]
        for statement in denied:
            with self.subTest(statement=statement[:50]):
                with self.assertRaisesRegex(Exception, 'permission denied'):
                    self.run_as("runtime", statement)

    def test_owner_login_capabilities_are_unrestricted_by_design(self):
        """Documents owner capabilities (the production login): truncate guard is an accident guard only."""
        self.run_as("owner", "INSERT INTO courses_zoomwebhookevent (event_id, event_type, payload, status, error_message, "
                             "retry_count, received_at, meeting_uuid, participant_uuid) "
                             "VALUES (%s, 't', '{}', 'RECEIVED', '', 0, now(), '', '')",
                    [f"owner-{uuid.uuid4().hex}"])
        with self.assertRaisesRegex(Exception, 'CANNOT_TRUNCATE_AUDIT_LOG'):
            self.in_rolled_back_tx("owner", "SET LOCAL app.allow_audit_truncate = 'off'", "TRUNCATE courses_zoomwebhookevent")
        # Deliberate bypasses available to the owner (rolled back):
        self.in_rolled_back_tx("owner", "SET LOCAL app.allow_audit_truncate = 'on'", "TRUNCATE courses_zoomwebhookevent")
        self.in_rolled_back_tx("owner", "ALTER TABLE courses_zoomwebhookevent DISABLE TRIGGER trg_prevent_audit_truncate_webhookevent")
        self.assertGreaterEqual(self.run_as("owner", "SELECT count(*) FROM courses_zoomwebhookevent")[0][0], 1)


# =============================================================================
# B10 -- recurring-series rollback
# =============================================================================
class B10RecurringRollbackTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_superuser(username="b10_admin", email="b10@natyaarts.com", password="Pw123456!x")
        teacher = User.objects.create_user(username="b10_teacher", email="b10t@natyaarts.com", password="Pw123456!x",
                                           is_staff=True)
        teacher.is_teacher = True
        teacher.save()
        self.hosts = [ZoomHostAccount.objects.create(zoom_user_id=f"b10pool_host_{i}", email=f"b10h{i}@natyaarts.com",
                                                     status=ZoomHostAccount.Status.ACTIVE, max_concurrency=1)
                      for i in range(2)]
        self.course = Course.objects.create(title="B10 Course", is_published=True)
        self.batch = LiveBatch.objects.create(course=self.course, instructor=teacher,
                                              batch_type=LiveBatch.BatchType.GROUP, max_participants=20)
        self.now = timezone.now().replace(microsecond=0) + datetime.timedelta(days=2)
        self.client.force_authenticate(user=self.admin)

    def post_series(self, title, count=3):
        return self.client.post("/api/courses/live-classes/", {
            "title": title, "course": self.course.id, "batch": self.batch.id,
            "scheduled_start": self.now.isoformat(), "duration_minutes": 60, "meeting_provider": "ZOOM",
            "recurrence": {"frequency": "DAILY", "occurrence_count": count},
        }, format='json')

    def arm_next_create_failure(self, cloud, action):
        cloud.on_create = lambda mid: [cloud.fail('POST', h.zoom_user_id, action, times=1) for h in self.hosts]

    def test_ambiguous_occurrence_stops_series_and_is_held_not_deleted(self):
        with FakeZoomCloud() as cloud:
            self.arm_next_create_failure(cloud, 'timeout')
            resp = self.post_series("B10 Ambiguous")
            self.assertEqual(cloud.meetings, {}, "occurrence 1's meeting must be cancelled on Zoom")
        self.assertEqual(resp.status_code, 409, resp.data)
        rb = resp.data["rollback"]
        occ = list(LiveClass.objects.filter(title="B10 Ambiguous").order_by('scheduled_start'))
        self.assertEqual(len(occ), 2, "the never-provisioned occurrence is deleted; the other two are retained")
        self.assertEqual(len(rb["deleted_live_class_ids"]), 1)
        self.assertTrue(all(o.status == LiveClass.ClassStatus.CANCELLED for o in occ))
        held = LiveClassDurableOperation.objects.get(live_class=occ[1])
        self.assertEqual(held.status, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN)
        self.assertEqual(HostReservation.objects.get(durable_operation=held).status, HostReservation.Status.MANUAL_REVIEW)
        self.assertEqual(HostReservation.objects.get(live_class=occ[0]).status, HostReservation.Status.CANCELLED)
        self.assertTrue(rb["recurrence_rule_retained"])
        self.assertTrue(RecurrenceRule.objects.filter(occurrences__in=occ).exists())

    def test_failed_cancel_during_rollback_holds_host_then_watchdog_recovers(self):
        from courses.tasks import zoom_provisioning_watchdog_task
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            self.arm_next_create_failure(cloud, 400)           # occurrence 2: definite failure
            cloud.fail('DELETE', '85012340001', 500)           # rollback cancel of occurrence 1 fails
            resp = self.post_series("B10 Cancel Fails")
            self.assertEqual(resp.status_code, 409, resp.data)
            self.assertIn('85012340001', cloud.meetings)
            occ = list(LiveClass.objects.filter(title="B10 Cancel Fails"))
            self.assertEqual(len(occ), 1, "only the occurrence with a live Zoom obligation is retained")
            res = HostReservation.objects.get(live_class=occ[0])
            zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id='85012340001')
            self.assertEqual((occ[0].status, res.status, zmr.state),
                             (LiveClass.ClassStatus.CANCELLED, HostReservation.Status.MANUAL_REVIEW, "COMPENSATING"))
            # Recovery: Zoom is reachable again; the scheduled watchdog finishes the teardown.
            cloud.fail('DELETE', '85012340001', 500, times=0)
            ZoomMeetingRecord.objects.filter(id=zmr.id).update(updated_at=timezone.now() - datetime.timedelta(minutes=5))
            zoom_provisioning_watchdog_task.apply().get()
            self.assertNotIn('85012340001', cloud.meetings)
        res.refresh_from_db()
        zmr.refresh_from_db()
        self.assertEqual((res.status, zmr.state), (HostReservation.Status.CANCELLED, "COMPENSATED"))

    def test_definite_failure_without_live_obligations_deletes_cleanly(self):
        with FakeZoomCloud() as cloud:
            cloud.fail('POST', self.hosts[0].zoom_user_id, 400)
            cloud.fail('POST', self.hosts[1].zoom_user_id, 400)
            resp = self.post_series("B10 Clean")
        self.assertEqual(resp.status_code, 409, resp.data)
        self.assertFalse(LiveClass.objects.filter(title="B10 Clean").exists())
        self.assertFalse(resp.data["rollback"]["recurrence_rule_retained"])
