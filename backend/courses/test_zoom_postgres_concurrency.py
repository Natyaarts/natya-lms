"""
Natya LMS — Phase B.4 PostgreSQL Concurrency Integration Test Suite.
Tests multi-connection row-level locking, deterministic lock ordering,
deadlock detection, and worker fencing against a real PostgreSQL database.

NOTE: SQLite serializes database transactions at the file level and does not support
PostgreSQL row-level locks (FOR UPDATE / FOR NO KEY UPDATE) or multi-connection
concurrency. These tests MUST only be executed against a real PostgreSQL instance.
"""

import threading
import datetime
import unittest
from unittest.mock import patch
from django.test import TransactionTestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import connection, connections, transaction

from courses.models import (
    Course,
    LiveClass,
    LiveClassDurableOperation,
    ZoomHostAccount,
    HostReservation,
    ZoomMeetingRecord,
    ZoomLifecycleOutbox,
)
from courses.services.zoom_lifecycle import (
    provision_zoom_meeting,
    replace_zoom_meeting,
    cancel_zoom_meeting,
    compensate_zoom_operation,
    select_and_reserve_host,
    ZeroTeardownInvariantViolation,
    HostCapacityExhaustedError,
    ConcurrencyConflictError,
)

User = get_user_model()


@unittest.skipUnless(connection.vendor == 'postgresql', "Real PostgreSQL required for multi-connection concurrency tests")
class ZoomPostgresConcurrencyTests(TransactionTestCase):
    """
    Real PostgreSQL concurrency tests using separate database connections across threads.
    TransactionTestCase ensures transactions are real and committed, not wrapped in test transactions.
    """

    def setUp(self):
        super().setUp()
        self.host1 = ZoomHostAccount.objects.create(
            zoom_user_id="pg_host_alpha_1",
            email="pg.alpha@natya.in",
            display_name="PG Host Alpha",
            status=ZoomHostAccount.Status.ACTIVE,
            max_concurrency=1
        )
        self.host2 = ZoomHostAccount.objects.create(
            zoom_user_id="pg_host_beta_2",
            email="pg.beta@natya.in",
            display_name="PG Host Beta",
            status=ZoomHostAccount.Status.ACTIVE,
            max_concurrency=1
        )
        self.course = Course.objects.create(title="Postgres Concurrency Test Course")
        self.now = timezone.now().replace(microsecond=0) + datetime.timedelta(days=2)
        self.live_class = LiveClass.objects.create(
            course=self.course,
            title="Postgres Concurrent Session",
            scheduled_start=self.now,
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED,
            meeting_url="https://natyaarts.com/class/pg-concurrency"
        )

    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_concurrent_initial_provisioning_mutual_exclusion(self, mock_create):
        """
        Two concurrent worker threads attempt to provision the same LiveClass.
        PostgreSQL FOR UPDATE row-level locking ensures exactly one succeeds,
        and the second is rejected cleanly by the zero-teardown invariant check.
        """
        mock_create.return_value = {
            "meeting_id": "77766655544",
            "uuid": "pg_uuid_1",
            "host_id": "pg_host_alpha_1",
            "join_url": "https://zoom.us/j/77766655544"
        }

        results = []
        errors = []

        def worker():
            # Close connection to force new thread-local DB connection
            connections.close_all()
            try:
                res = provision_zoom_meeting(self.live_class.id)
                results.append(res)
            except Exception as e:
                errors.append(e)
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Exactly one must succeed
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "SUCCESS")

        # The other must fail with zero-teardown violation or concurrency conflict
        self.assertEqual(len(errors), 1)
        self.assertTrue(
            isinstance(errors[0], (ZeroTeardownInvariantViolation, ConcurrencyConflictError))
        )

    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_concurrent_host_allocation_capacity_saturation(self, mock_create):
        """
        Two separate classes attempt to allocate host1 (max_concurrency=1) concurrently.
        PostgreSQL row locking on ZoomHostAccount serializes the checks, ensuring the
        second thread receives HostCapacityExhaustedError instead of overbooking the host.
        """
        other_class = LiveClass.objects.create(
            course=self.course,
            title="Competing Class",
            scheduled_start=self.now,
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED,
            meeting_url="https://natyaarts.com/class/competing"
        )
        # Saturate host2 so host1 is the only candidate
        HostReservation.objects.create(
            live_class=other_class,
            host_account=self.host2,
            scheduled_start=self.now,
            scheduled_end=self.now + datetime.timedelta(hours=1),
            status=HostReservation.Status.ACTIVE
        )

        results = []
        errors = []

        def allocate_host(lc_id):
            connections.close_all()
            try:
                with transaction.atomic():
                    lc = LiveClass.objects.select_for_update().get(id=lc_id)
                    res = select_and_reserve_host(
                        live_class=lc,
                        start_time=self.now,
                        duration_minutes=60,
                        preferred_host_id=self.host1.id
                    )
                    results.append(res)
            except Exception as e:
                errors.append(e)
            finally:
                connections.close_all()

        third_class = LiveClass.objects.create(
            course=self.course,
            title="Third Class",
            scheduled_start=self.now,
            duration_minutes=60,
            status=LiveClass.ClassStatus.SCHEDULED,
            meeting_url="https://natyaarts.com/class/third"
        )

        t1 = threading.Thread(target=allocate_host, args=(self.live_class.id,))
        t2 = threading.Thread(target=allocate_host, args=(third_class.id,))

        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # One reservation must succeed, one must fail with HostCapacityExhaustedError
        self.assertEqual(len(results), 1)
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], HostCapacityExhaustedError)

    @patch('courses.services.zoom.ZoomService.delete_meeting')
    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_competing_replacement_and_cancellation_lock_serialization(self, mock_create, mock_delete):
        """
        Competing replacement and cancellation threads attempt to modify the same LiveClass.
        PostgreSQL Level 1 row locking on LiveClass forces serialized execution,
        preventing race conditions or dangling reservations.
        """
        mock_create.side_effect = [
            {"meeting_id": "11122233344", "uuid": "u1", "host_id": "pg_host_alpha_1", "join_url": "https://zoom.us/j/11122233344"},
            {"meeting_id": "55566677788", "uuid": "u2", "host_id": "pg_host_beta_2", "join_url": "https://zoom.us/j/55566677788"}
        ]
        mock_delete.return_value = {"status": "deleted"}

        # Initial provision
        provision_zoom_meeting(self.live_class.id)
        ZoomLifecycleOutbox.objects.filter(aggregate_id=self.live_class.id).update(
            status=ZoomLifecycleOutbox.Status.COMPLETED
        )

        results = []
        errors = []

        def do_replace():
            connections.close_all()
            try:
                res = replace_zoom_meeting(self.live_class.id, new_host_id=self.host2.id)
                results.append(("REPLACE", res))
            except Exception as e:
                errors.append(("REPLACE", e))
            finally:
                connections.close_all()

        def do_cancel():
            connections.close_all()
            try:
                res = cancel_zoom_meeting(self.live_class.id, reason="Concurrent cancel test")
                results.append(("CANCEL", res))
            except Exception as e:
                errors.append(("CANCEL", e))
            finally:
                connections.close_all()

        t1 = threading.Thread(target=do_replace)
        t2 = threading.Thread(target=do_cancel)

        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        # Both operations ran under Level 1 serialization without deadlocks
        total_ops = len(results) + len(errors)
        self.assertEqual(total_ops, 2)
        # Database state must be consistent
        self.live_class.refresh_from_db()
        active_res = HostReservation.objects.filter(
            live_class=self.live_class,
            status=HostReservation.Status.ACTIVE
        ).count()
        # Active reservations can never exceed 1
        self.assertLessEqual(active_res, 1)

    @patch('courses.services.zoom.ZoomService.create_meeting')
    def test_competing_replacement_requests_serialization(self, mock_create):
        """
        DEF-01: Two competing replacement requests attempt to replace the same LiveClass concurrently.
        Phase 1 Level 1 row locking and in-flight durable operation guards serialize the calls.
        Exactly one replacement succeeds, and the competing thread is safely rejected
        without violating idx_zmr_active_live_class or leaving an untracked orphan meeting.
        """
        mock_create.side_effect = [
            {"meeting_id": "11100011100", "uuid": "u_init", "host_id": "pg_host_alpha_1", "join_url": "https://zoom.us/j/11100011100"},
            {"meeting_id": "22200022200", "uuid": "u_repl1", "host_id": "pg_host_beta_2", "join_url": "https://zoom.us/j/22200022200"},
            {"meeting_id": "33300033300", "uuid": "u_repl2", "host_id": "pg_host_beta_2", "join_url": "https://zoom.us/j/33300033300"},
        ]

        # Initial provision
        provision_zoom_meeting(self.live_class.id)
        ZoomLifecycleOutbox.objects.filter(aggregate_id=self.live_class.id).update(
            status=ZoomLifecycleOutbox.Status.COMPLETED
        )

        results = []
        errors = []

        def do_competing_replace(ident):
            connections.close_all()
            try:
                res = replace_zoom_meeting(self.live_class.id, new_host_id=self.host2.id)
                results.append((ident, res))
            except Exception as e:
                errors.append((ident, e))
            finally:
                connections.close_all()

        t1 = threading.Thread(target=do_competing_replace, args=("THREAD_1",))
        t2 = threading.Thread(target=do_competing_replace, args=("THREAD_2",))

        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        # Exactly one replacement must succeed
        self.assertEqual(len(results), 1, f"Expected 1 success, got {len(results)}: {results}")
        self.assertEqual(len(errors), 1, f"Expected 1 error, got {len(errors)}: {errors}")

        # The competing thread must be rejected by DEF-01 zero-teardown guard or concurrency conflict
        self.assertTrue(
            isinstance(errors[0][1], (ZeroTeardownInvariantViolation, ConcurrencyConflictError)),
            f"Expected ZeroTeardownInvariantViolation, got {type(errors[0][1])}: {errors[0][1]}"
        )

        # Invariant checks: exactly ONE meeting record is current
        self.live_class.refresh_from_db()
        current_zmrs = ZoomMeetingRecord.objects.filter(
            live_class=self.live_class,
            is_current=True
        )
        self.assertEqual(current_zmrs.count(), 1)

        # Invariant checks: exactly ONE reservation is active
        active_res = HostReservation.objects.filter(
            live_class=self.live_class,
            status=HostReservation.Status.ACTIVE
        )
        self.assertEqual(active_res.count(), 1)


