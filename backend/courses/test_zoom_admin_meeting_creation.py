"""
Regression tests for the Zoom create-meeting blocker (admin class creation).

Production failure: AdminZoomMeetingCreateView / the admin page's "Auto-Generate Zoom"
button created meetings outside the host-pool lifecycle (no host_id -> ValueError -> 500,
and when it did work it created a second, ad-hoc class + meeting + host reservation).

These tests drive the REAL views, provision_zoom_meeting, cancel_zoom_meeting and the
provisioning watchdog through the REAL ZoomService. Only the HTTP layer and the OAuth
token are replaced, by FakeZoomCloud (Zoom's actual response shapes). No live Zoom calls.

Covered:
  - Zoom class created with a blank meeting_url is provisioned server-side and persisted
  - malformed meeting URLs rejected
  - independent host assignment from the pool; capacity exhaustion -> 409, no orphans
  - endpoint: lifecycle only, idempotent, input validation, object-level permissions
    (including assigned and unauthorized mentors)
  - definite CREATE failure -> nothing held, retry succeeds
  - ambiguous CREATE -> host held, watchdog verification, then retry (or stays held)
  - cancel / cancel-series delete the meeting and release the host; ambiguous delete is
    held and the watchdog finishes it; teardown happens after commit
  - attendance and recording webhooks resolve API-provisioned classes
  - stale and replaced meeting webhooks do not corrupt active class
  - simultaneous requests (PostgreSQL, real threads): one meeting per class, distinct hosts
"""
import datetime
import hashlib
import hmac
import json
import threading
import uuid
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from courses.models import (
    Course, LiveBatch, LiveBatchStudent, LiveClass, LiveClassDurableOperation, ZoomHostAccount,
    HostReservation, ZoomMeetingRecord, RecurrenceRule, Attendance, LiveClassRegistrant,
    ZoomWebhookEvent,
)
from courses.services.recording import ZoomRecordingService
from courses.services.zoom_lifecycle import provision_zoom_meeting, run_provisioning_watchdog
from courses.test_zoom_phase_b5_repairs import FakeZoomCloud, IS_PG, ZOOM_CREDS

User = get_user_model()

CREATE_URL = "/api/courses/admin/zoom/create-meeting/"
CLASSES_URL = "/api/courses/live-classes/"
HELD = [HostReservation.Status.PROVISIONAL, HostReservation.Status.ACTIVE,
        HostReservation.Status.DRAINING, HostReservation.Status.MANUAL_REVIEW]


class _AdminZoomFixture:
    def make_fixture(self):
        uid = uuid.uuid4().hex[:8]
        self.uid = uid
        self.admin = User.objects.create_superuser(
            username=f"zoom_admin_{uid}", email=f"zoom.admin_{uid}@example.test", password="x-Admin-pass-1")
        self.teacher = User.objects.create_user(
            username=f"teacher_kala_{uid}", email=f"kala.teacher_{uid}@example.test", password="x-Teacher-pass-1")
        self.teacher.is_teacher = True
        self.teacher.save()
        self.other_teacher = User.objects.create_user(
            username=f"teacher_other_{uid}", email=f"other.teacher_{uid}@example.test", password="x-Teacher-pass-2")
        self.other_teacher.is_teacher = True
        self.other_teacher.save()
        self.mentor = User.objects.create_user(
            username=f"mentor_assigned_{uid}", email=f"assigned.mentor_{uid}@example.test", password="x-Mentor-pass-1")
        self.mentor.is_mentor = True
        self.mentor.save()
        self.other_mentor = User.objects.create_user(
            username=f"mentor_other_{uid}", email=f"other.mentor_{uid}@example.test", password="x-Mentor-pass-2")
        self.other_mentor.is_mentor = True
        self.other_mentor.save()
        self.student = User.objects.create_user(
            username=f"student_meera_{uid}", email=f"meera.student_{uid}@example.test", password="x-Student-pass-1")

        self.HOST_A = f"pool_host_alpha_{uid}"
        self.HOST_B = f"pool_host_beta_{uid}"
        self.host_a = ZoomHostAccount.objects.create(
            zoom_user_id=self.HOST_A, email=f"pool-alpha_{uid}@hosts.example.test", display_name="Pool Alpha",
            status=ZoomHostAccount.Status.ACTIVE, max_concurrency=1,
            account_type=ZoomHostAccount.AccountType.SHARED_POOL)
        self.host_b = ZoomHostAccount.objects.create(
            zoom_user_id=self.HOST_B, email=f"pool-beta_{uid}@hosts.example.test", display_name="Pool Beta",
            status=ZoomHostAccount.Status.ACTIVE, max_concurrency=1,
            account_type=ZoomHostAccount.AccountType.SHARED_POOL)

        self.course = Course.objects.create(title=f"Kuchipudi Live Foundation {uid}", is_published=True)
        self.batch = LiveBatch.objects.create(
            course=self.course, instructor=self.teacher, mentor=self.mentor,
            batch_type=LiveBatch.BatchType.GROUP, max_participants=20)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student)
        self.start = timezone.now().replace(microsecond=0) + datetime.timedelta(days=3)

    def make_instructor_and_batch(self, name_suffix):
        teacher = User.objects.create_user(
            username=f"teacher_{name_suffix}_{self.uid}",
            email=f"{name_suffix}_{self.uid}@example.test",
            password="x-Pass-1",
        )
        teacher.is_teacher = True
        teacher.save()
        batch = LiveBatch.objects.create(
            course=self.course, instructor=teacher, batch_type=LiveBatch.BatchType.GROUP, max_participants=20
        )
        return teacher, batch

    def client_for(self, user):
        client = APIClient()
        if user is not None:
            client.force_authenticate(user=user)
        return client

    def create_class_via_api(self, title, start=None, user=None, batch=None, extra=None):
        payload = {
            "title": title,
            "course": self.course.id,
            "batch": (batch or self.batch).id,
            "scheduled_start": (start or self.start).isoformat(),
            "duration_minutes": 60,
            "meeting_provider": "ZOOM",
            "meeting_url": "",
        }
        payload.update(extra or {})
        return self.client_for(user or self.admin).post(CLASSES_URL, payload, format="json")

    def make_zoom_class(self, title, start=None, batch=None, **kwargs):
        b = batch or self.batch
        defaults = dict(course=self.course, batch=b, instructor=b.instructor, title=title, scheduled_start=start or self.start,
                        duration_minutes=60, meeting_provider=LiveClass.MeetingProvider.ZOOM, meeting_url="")
        defaults.update(kwargs)
        return LiveClass.objects.create(**defaults)

    def provision_via_endpoint(self, lc, user=None):
        return self.client_for(user or self.admin).post(CREATE_URL, {"live_class_id": lc.id}, format="json")

    def held_reservations(self, lc):
        return HostReservation.objects.filter(live_class=lc, status__in=HELD)


# =============================================================================
# 1. Admin class creation (the page's real flow): blank URL -> server-side provisioning
# =============================================================================
class AdminZoomClassCreationTests(_AdminZoomFixture, TransactionTestCase):
    def setUp(self):
        self.make_fixture()
        self._rem_patch = mock.patch("courses.tasks.send_class_reminder.apply_async")
        self._notif_patch = mock.patch("notifications.services.LiveClassNotificationService.notify_scheduled")
        self._rem_patch.start()
        self._notif_patch.start()

    def tearDown(self):
        self._rem_patch.stop()
        self._notif_patch.stop()

    def test_zoom_class_with_blank_url_is_provisioned_on_a_pool_host_and_persisted(self):
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            res = self.create_class_via_api("Jathiswaram Practice")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)

        lc = LiveClass.objects.get(id=res.data["id"])
        mid = lc.zoom_meeting_id
        self.assertTrue(mid)
        self.assertIn(mid, cloud.meetings)
        self.assertEqual(cloud.meetings[mid]["host_id"], self.HOST_A)
        self.assertEqual(lc.meeting_url, f"https://zoom.us/j/{mid}")
        self.assertEqual(res.data["meeting_url"], lc.meeting_url)
        self.assertEqual(lc.zoom_uuid, cloud.meetings[mid]["uuid"])

        # Exactly one CREATE, on the reserved pool host (never "me"), outside any DB transaction.
        posts = cloud.calls_of("POST")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][1], self.HOST_A)
        self.assertFalse(posts[0][2], "Zoom CREATE must not run inside a DB transaction")

        zmr = ZoomMeetingRecord.objects.get(live_class=lc, is_current=True)
        self.assertEqual((zmr.zoom_meeting_id, zmr.state, zmr.host_account_id),
                         (mid, ZoomMeetingRecord.State.ASSIGNED, self.host_a.id))
        resv = HostReservation.objects.get(live_class=lc)
        self.assertEqual((resv.status, resv.host_account_id), (HostReservation.Status.ACTIVE, self.host_a.id))
        dop = LiveClassDurableOperation.objects.get(live_class=lc)
        self.assertEqual(dop.status, LiveClassDurableOperation.Status.COMPLETED)
        self.assertEqual(resv.durable_operation_id, dop.id)
        self.assertEqual(zmr.durable_operation_id, dop.id)

    def test_malformed_zoom_url_is_rejected_on_class_creation(self):
        """Supplying a malformed/invalid URL when creating a class is rejected with 400 Bad Request."""
        res = self.create_class_via_api("Malformed URL Class", extra={"meeting_url": "not-a-valid-url"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("meeting_url", res.data)
        self.assertFalse(LiveClass.objects.filter(title="Malformed URL Class").exists())

    def test_overlapping_classes_get_independent_hosts_and_exhaustion_leaves_no_orphan(self):
        t2, b2 = self.make_instructor_and_batch("t2")
        t3, b3 = self.make_instructor_and_batch("t3")
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            r1 = self.create_class_via_api("Overlap One", batch=self.batch)
            r2 = self.create_class_via_api("Overlap Two", batch=b2, start=self.start + datetime.timedelta(minutes=30))
            self.assertEqual((r1.status_code, r2.status_code), (201, 201))

            h1 = HostReservation.objects.get(live_class_id=r1.data["id"]).host_account_id
            h2 = HostReservation.objects.get(live_class_id=r2.data["id"]).host_account_id
            self.assertEqual({h1, h2}, {self.host_a.id, self.host_b.id})

            # Pool exhausted for the overlapping slot: 409, the class is not kept, nothing held, no CREATE.
            r3 = self.create_class_via_api("Overlap Three", batch=b3, start=self.start + datetime.timedelta(minutes=15))
            self.assertEqual(r3.status_code, status.HTTP_409_CONFLICT)
            self.assertFalse(LiveClass.objects.filter(title="Overlap Three").exists())
            # Exactly 2 held reservations exist right now (for r1 and r2; r3 leaked nothing)
            self.assertEqual(HostReservation.objects.filter(status__in=HELD).count(), 2)

            # A non-overlapping slot reuses the first host.
            r4 = self.create_class_via_api("Next Day", batch=self.batch, start=self.start + datetime.timedelta(days=1))
            self.assertEqual(r4.status_code, 201)
            self.assertEqual(HostReservation.objects.get(live_class_id=r4.data["id"]).host_account_id, self.host_a.id)
            self.assertEqual(HostReservation.objects.filter(status__in=HELD).count(), 3)
            self.assertEqual([c[1] for c in cloud.calls_of("POST")], [self.HOST_A, self.HOST_B, self.HOST_A])
            self.assertEqual(len(cloud.meetings), 3)

    def test_definite_create_failure_on_class_creation_leaves_nothing_then_retry_succeeds(self):
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            cloud.fail("POST", self.HOST_A, 403, times=1)
            failed = self.create_class_via_api("Refused By Zoom")
            self.assertEqual(failed.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
            self.assertFalse(LiveClass.objects.filter(title="Refused By Zoom").exists())
            self.assertFalse(HostReservation.objects.filter(status__in=HELD).exists())
            self.assertEqual(cloud.meetings, {})

            retry = self.create_class_via_api("Refused By Zoom")
        self.assertEqual(retry.status_code, status.HTTP_201_CREATED, retry.data)
        lc = LiveClass.objects.get(id=retry.data["id"])
        self.assertIn(lc.zoom_meeting_id, cloud.meetings)
        self.assertEqual(self.held_reservations(lc).get().status, HostReservation.Status.ACTIVE)

    def test_explicit_meeting_url_is_still_required_for_non_zoom_providers(self):
        res = self.create_class_via_api("Meet Class", extra={"meeting_provider": "GOOGLE_MEET"})
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("meeting_url", res.data)


# =============================================================================
# 2. AdminZoomMeetingCreateView: lifecycle only, idempotent, validated, permissioned
# =============================================================================
class AdminZoomMeetingEndpointTests(_AdminZoomFixture, TestCase):
    def setUp(self):
        self.make_fixture()
        self._rem_patch = mock.patch("courses.tasks.send_class_reminder.apply_async")
        self._notif_patch = mock.patch("notifications.services.LiveClassNotificationService.notify_scheduled")
        self._rem_patch.start()
        self._notif_patch.start()

    def tearDown(self):
        self._rem_patch.stop()
        self._notif_patch.stop()

    def test_endpoint_provisions_existing_class_through_the_lifecycle(self):
        lc = self.make_zoom_class("Endpoint Class")
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            res = self.provision_via_endpoint(lc)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(res.data["status"], "SUCCESS")
        lc.refresh_from_db()
        self.assertEqual(res.data["meeting_id"], lc.zoom_meeting_id)
        self.assertEqual(res.data["join_url"], lc.meeting_url)
        self.assertEqual(cloud.meetings[lc.zoom_meeting_id]["host_id"], self.HOST_A)
        self.assertEqual(self.held_reservations(lc).get().status, HostReservation.Status.ACTIVE)
        self.assertTrue(ZoomMeetingRecord.objects.filter(live_class=lc, is_current=True).exists())
        self.assertEqual(LiveClassDurableOperation.objects.get(live_class=lc).status,
                         LiveClassDurableOperation.Status.COMPLETED)

    def test_duplicate_requests_do_not_create_a_second_meeting(self):
        lc = self.make_zoom_class("Duplicate Class")
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            first = self.provision_via_endpoint(lc)
            second = self.provision_via_endpoint(lc)
            third = self.client_for(self.admin).post(f"{CLASSES_URL}{lc.id}/generate-zoom/")
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["status"], "ALREADY_PROVISIONED")
        self.assertEqual(second.data["meeting_id"], first.data["meeting_id"])
        self.assertEqual(third.status_code, 200)
        self.assertEqual(len(cloud.calls_of("POST")), 1)
        self.assertEqual(len(cloud.meetings), 1)
        self.assertEqual(HostReservation.objects.filter(live_class=lc).count(), 1)
        self.assertEqual(LiveClassDurableOperation.objects.filter(live_class=lc).count(), 1)

    def test_invalid_requests_are_rejected_without_any_zoom_call(self):
        meet = LiveClass.objects.create(
            course=self.course, batch=self.batch, instructor=self.teacher, title="Meet Class",
            scheduled_start=self.start + datetime.timedelta(hours=2),
            duration_minutes=60, meeting_provider=LiveClass.MeetingProvider.GOOGLE_MEET,
            meeting_url="https://meet.google.com/abc-defg-hij")
        cancelled = self.make_zoom_class("Cancelled Class", status=LiveClass.ClassStatus.CANCELLED)
        client = self.client_for(self.admin)
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            cases = [
                ({"topic": "ad hoc", "course_id": self.course.id}, 400),
                ({"live_class_id": "abc"}, 400),
                ({"live_class_id": 99999999}, 404),
                ({"live_class_id": meet.id}, 400),
                ({"live_class_id": cancelled.id}, 400),
            ]
            for body, expected in cases:
                with self.subTest(body=body):
                    self.assertEqual(client.post(CREATE_URL, body, format="json").status_code, expected)
        self.assertEqual(cloud.calls, [])
        self.assertEqual(LiveClass.objects.count(), 2)
        self.assertFalse(HostReservation.objects.exists())

    def test_object_level_permissions(self):
        """Verifies object-level permissions: unauthenticated, students, unauthorized teachers,
        and unauthorized mentors are rejected (401/403), while the batch instructor AND
        the assigned mentor are authorized to manage Zoom creation."""
        lc = self.make_zoom_class("Permission Class")
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            # 1. Unauthenticated -> 401/403
            self.assertIn(self.client_for(None).post(CREATE_URL, {"live_class_id": lc.id}).status_code, (401, 403))
            # 2. Student -> 403
            self.assertEqual(self.provision_via_endpoint(lc, user=self.student).status_code, 403)
            # 3. Unrelated teacher -> 403
            self.assertEqual(self.provision_via_endpoint(lc, user=self.other_teacher).status_code, 403)
            # 4. Unauthorized mentor -> 403
            self.assertEqual(self.provision_via_endpoint(lc, user=self.other_mentor).status_code, 403)
            self.assertEqual(cloud.calls, [])

            # 5. Assigned mentor (batch.mentor == user) may provision!
            res_mentor = self.provision_via_endpoint(lc, user=self.mentor)
            self.assertEqual(res_mentor.status_code, 201, res_mentor.data)
            self.assertEqual(len(cloud.calls_of("POST")), 1)

            # 6. The batch's own instructor may also provision (already provisioned -> 200)
            res_teacher = self.provision_via_endpoint(lc, user=self.teacher)
            self.assertEqual(res_teacher.status_code, 200)
            self.assertEqual(res_teacher.data["status"], "ALREADY_PROVISIONED")

    def test_definite_failure_returns_502_releases_everything_and_retry_succeeds(self):
        lc = self.make_zoom_class("Retry Class")
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            cloud.fail("POST", self.HOST_A, 403, times=1)
            failed = self.provision_via_endpoint(lc)
            self.assertEqual(failed.status_code, status.HTTP_502_BAD_GATEWAY)
            lc.refresh_from_db()
            self.assertEqual(lc.zoom_meeting_id, "")
            self.assertFalse(self.held_reservations(lc).exists())
            self.assertEqual(LiveClassDurableOperation.objects.get(live_class=lc).status,
                             LiveClassDurableOperation.Status.FAILED)

            retry = self.provision_via_endpoint(lc)
        self.assertEqual(retry.status_code, 201, retry.data)
        lc.refresh_from_db()
        self.assertIn(lc.zoom_meeting_id, cloud.meetings)
        self.assertEqual(len(cloud.meetings), 1)
        self.assertEqual(self.held_reservations(lc).get().status, HostReservation.Status.ACTIVE)

    def test_capacity_exhausted_returns_409_and_holds_nothing(self):
        t1, b1 = self.make_instructor_and_batch("cap1")
        t2, b2 = self.make_instructor_and_batch("cap2")
        t3, b3 = self.make_instructor_and_batch("cap3")
        busy = [
            self.make_zoom_class("Busy 0", batch=b1),
            self.make_zoom_class("Busy 1", batch=b2),
        ]
        lc = self.make_zoom_class("No Host Left", batch=b3)
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            for b in busy:
                self.assertEqual(self.provision_via_endpoint(b).status_code, 201)
            res = self.provision_via_endpoint(lc)
        self.assertEqual(res.status_code, status.HTTP_409_CONFLICT)
        self.assertFalse(HostReservation.objects.filter(live_class=lc).exists())
        self.assertEqual(len(cloud.calls_of("POST")), 2)

    def test_ambiguous_create_holds_host_until_verified_then_retry_succeeds(self):
        t2, b2 = self.make_instructor_and_batch("amb2")
        lc = self.make_zoom_class("Ambiguous Class")
        other = self.make_zoom_class("Overlapping Neighbour", batch=b2)
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            cloud.fail("POST", self.HOST_A, "timeout")
            res = self.provision_via_endpoint(lc)
            self.assertEqual(res.status_code, status.HTTP_202_ACCEPTED, res.data)
            self.assertEqual(res.data["status"], "OUTCOME_UNKNOWN")
            resv = HostReservation.objects.get(live_class=lc)
            self.assertEqual(resv.status, HostReservation.Status.MANUAL_REVIEW)
            dop = LiveClassDurableOperation.objects.get(live_class=lc)
            self.assertEqual(dop.status, LiveClassDurableOperation.Status.OUTCOME_UNKNOWN)

            # Retrying now must NOT send a second CREATE.
            self.assertEqual(self.provision_via_endpoint(lc).status_code, status.HTTP_409_CONFLICT)
            self.assertEqual(len(cloud.calls_of("POST")), 1)
            # The held host is not handed to an overlapping class.
            cloud.fail("POST", self.HOST_A, "timeout", times=0)
            self.assertEqual(self.provision_via_endpoint(other).status_code, 201)
            self.assertEqual(HostReservation.objects.get(live_class=other).host_account_id, self.host_b.id)

            # Watchdog inside the 15-minute grace period: still held.
            report = run_provisioning_watchdog()
            self.assertEqual(report["resolved"], [])
            resv.refresh_from_db()
            self.assertEqual(resv.status, HostReservation.Status.MANUAL_REVIEW)

            # After the grace period the host listing proves no meeting was created -> released.
            LiveClassDurableOperation.objects.filter(id=dop.id).update(
                quarantined_at=timezone.now() - datetime.timedelta(minutes=16))
            report = run_provisioning_watchdog()
            self.assertEqual([r["dop_id"] for r in report["resolved"]], [dop.id])
            resv.refresh_from_db()
            self.assertEqual(resv.status, HostReservation.Status.RELEASED)

            retry = self.provision_via_endpoint(lc)
        self.assertEqual(retry.status_code, 201, retry.data)
        lc.refresh_from_db()
        self.assertEqual(cloud.meetings[lc.zoom_meeting_id]["host_id"], self.HOST_A)
        self.assertEqual(len(cloud.meetings), 2)

    def test_ambiguous_create_that_did_create_a_meeting_stays_held(self):
        lc = self.make_zoom_class("Created Then Timed Out")
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            # Zoom creates the meeting, then the response is lost.
            cloud.hook("POST", self.HOST_A, lambda: cloud.add_meeting(self.HOST_A, lc.title, lc.scheduled_start, 60))
            cloud.fail("POST", self.HOST_A, "timeout", times=1)
            self.assertEqual(self.provision_via_endpoint(lc).status_code, 202)
            dop = LiveClassDurableOperation.objects.get(live_class=lc)
            LiveClassDurableOperation.objects.filter(id=dop.id).update(
                quarantined_at=timezone.now() - datetime.timedelta(minutes=16))
            report = run_provisioning_watchdog()
            self.assertEqual([r["dop_id"] for r in report["quarantine_held"]], [dop.id])
            self.assertEqual(self.provision_via_endpoint(lc).status_code, 409)
        self.assertEqual(HostReservation.objects.get(live_class=lc).status, HostReservation.Status.MANUAL_REVIEW)
        self.assertEqual(len(cloud.calls_of("POST")), 1)
        self.assertEqual(len(cloud.meetings), 1)


# =============================================================================
# 3. Cancellation releases the meeting and the host (runs as TransactionTestCase
#    so commit boundaries and non-transactional HTTP teardowns are truly verified)
# =============================================================================
class ZoomCancellationReleaseTests(_AdminZoomFixture, TransactionTestCase):
    def setUp(self):
        self.make_fixture()
        self._rem_patch = mock.patch("courses.tasks.send_class_reminder.apply_async")
        self._notif_sched = mock.patch("notifications.services.LiveClassNotificationService.notify_scheduled")
        self._notif_cancel = mock.patch("notifications.services.LiveClassNotificationService.notify_cancelled")
        self._rem_patch.start()
        self._notif_sched.start()
        self._notif_cancel.start()

    def tearDown(self):
        self._rem_patch.stop()
        self._notif_sched.stop()
        self._notif_cancel.stop()

    def _provisioned(self, title, start=None, batch=None):
        lc = self.make_zoom_class(title, start=start, batch=batch)
        self.assertEqual(provision_zoom_meeting(lc.id)["status"], "SUCCESS")
        lc.refresh_from_db()
        return lc

    def test_cancel_deletes_meeting_and_releases_host_for_reuse(self):
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            lc = self._provisioned("To Be Cancelled")
            mid = lc.zoom_meeting_id
            res = self.client_for(self.admin).post(f"{CLASSES_URL}{lc.id}/cancel/", {"reason": "Teacher unwell"})
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual(res.data["status"], LiveClass.ClassStatus.CANCELLED)
            self.assertEqual(res.data["zoom_teardown"]["status"], "RELEASED")
            self.assertNotIn(mid, cloud.meetings)
            deletes = cloud.calls_of("DELETE")
            self.assertEqual([d[1] for d in deletes], [mid])
            self.assertFalse(deletes[0][2], "Zoom DELETE must not run inside a DB transaction")

            resv = HostReservation.objects.get(live_class=lc)
            self.assertEqual(resv.status, HostReservation.Status.CANCELLED)
            zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id=mid)
            self.assertEqual((zmr.state, zmr.is_current), (ZoomMeetingRecord.State.RELEASED, False))

            # The freed slot goes to the first host again.
            replacement = self._provisioned("Replacement")
        self.assertEqual(HostReservation.objects.get(live_class=replacement).host_account_id, self.host_a.id)

    def test_ambiguous_cancel_holds_host_until_watchdog_confirms_delete(self):
        t2, b2 = self.make_instructor_and_batch("neigh")
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            lc = self._provisioned("Ambiguous Cancel")
            mid = lc.zoom_meeting_id
            cloud.fail("DELETE", mid, "timeout")
            res = self.client_for(self.admin).post(f"{CLASSES_URL}{lc.id}/cancel/")
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual(res.data["status"], LiveClass.ClassStatus.CANCELLED)
            self.assertEqual(res.data["zoom_teardown"]["status"], "PENDING_VERIFICATION")
            self.assertIn(mid, cloud.meetings)
            resv = HostReservation.objects.get(live_class=lc)
            self.assertEqual(resv.status, HostReservation.Status.MANUAL_REVIEW)
            zmr = ZoomMeetingRecord.objects.get(zoom_meeting_id=mid)
            self.assertEqual(zmr.state, ZoomMeetingRecord.State.COMPENSATING)

            # While held, an overlapping class cannot take host A.
            neighbour = self._provisioned("Neighbour", batch=b2)
            self.assertEqual(HostReservation.objects.get(live_class=neighbour).host_account_id, self.host_b.id)

            # Zoom recovers; the watchdog's cancellation stage finishes the delete and releases host A.
            cloud.fail("DELETE", mid, "timeout", times=0)
            ZoomMeetingRecord.objects.filter(id=zmr.id).update(
                updated_at=timezone.now() - datetime.timedelta(minutes=5))
            run_provisioning_watchdog()
        self.assertNotIn(mid, cloud.meetings)
        resv.refresh_from_db()
        zmr.refresh_from_db()
        self.assertEqual(resv.status, HostReservation.Status.CANCELLED)
        self.assertEqual(zmr.state, ZoomMeetingRecord.State.COMPENSATED)

    def test_cancel_series_tears_down_every_occurrence(self):
        rule = RecurrenceRule.objects.create(created_by=self.admin)
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            occurrences = [self._provisioned(f"Series {i}", start=self.start + datetime.timedelta(days=7 * i))
                           for i in range(3)]
            LiveClass.objects.filter(id__in=[o.id for o in occurrences]).update(recurrence_rule=rule)
            mids = [o.zoom_meeting_id for o in occurrences]
            res = self.client_for(self.admin).post(f"{CLASSES_URL}{occurrences[0].id}/cancel-series/")
            self.assertEqual(res.status_code, 200, res.data)
            self.assertEqual(len(res.data), 3)
            deletes = cloud.calls_of("DELETE")
        self.assertEqual(sorted(d[1] for d in deletes), sorted(mids))
        self.assertTrue(all(not d[2] for d in deletes), "no Zoom DELETE inside the cancel transaction")
        self.assertEqual(cloud.meetings, {})
        self.assertFalse(HostReservation.objects.filter(status__in=HELD).exists())
        self.assertEqual(LiveClass.objects.filter(status=LiveClass.ClassStatus.CANCELLED).count(), 3)

    def test_cancel_of_unprovisioned_or_non_zoom_class_makes_no_zoom_call(self):
        lc = self.make_zoom_class("Never Provisioned")
        meet = LiveClass.objects.create(
            course=self.course, batch=self.batch, instructor=self.teacher, title="Meet Class",
            scheduled_start=self.start + datetime.timedelta(hours=2),
            duration_minutes=60, meeting_provider=LiveClass.MeetingProvider.GOOGLE_MEET,
            meeting_url="https://meet.google.com/abc-defg-hij")
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            r1 = self.client_for(self.admin).post(f"{CLASSES_URL}{lc.id}/cancel/")
            r2 = self.client_for(self.admin).post(f"{CLASSES_URL}{meet.id}/cancel/")
        self.assertEqual((r1.status_code, r2.status_code), (200, 200))
        self.assertEqual(r1.data["zoom_teardown"]["status"], "NOT_PROVISIONED")
        self.assertNotIn("zoom_teardown", r2.data)
        self.assertEqual(cloud.calls, [])


# =============================================================================
# 4. Attendance and recording workflows on an API-provisioned class
# =============================================================================
WEBHOOK_SECRET = "test_zoom_webhook_secret_admin_create"


@override_settings(ZOOM_WEBHOOK_SECRET_TOKEN=WEBHOOK_SECRET, ENABLE_ZOOM_ATTENDANCE_V2=True, DEBUG=False)
class ProvisionedClassWebhookTests(_AdminZoomFixture, TestCase):
    def setUp(self):
        self.make_fixture()
        self._rem_patch = mock.patch("courses.tasks.send_class_reminder.apply_async")
        self._notif_patch = mock.patch("notifications.services.LiveClassNotificationService.notify_scheduled")
        self._rem_patch.start()
        self._notif_patch.start()

        self.lc = self.make_zoom_class("Webhook Class", start=timezone.now().replace(microsecond=0)
                                       + datetime.timedelta(minutes=5))
        with self.settings(**ZOOM_CREDS), FakeZoomCloud():
            res = self.provision_via_endpoint(self.lc)
        self.assertEqual(res.status_code, 201, res.data)
        self.lc.refresh_from_db()
        self.mid, self.uuid = self.lc.zoom_meeting_id, self.lc.zoom_uuid
        self.assertTrue(self.mid and self.uuid)

    def tearDown(self):
        self._rem_patch.stop()
        self._notif_patch.stop()

    def _post_webhook(self, body):
        raw = json.dumps(body).encode("utf-8")
        ts = str(int(timezone.now().timestamp()))
        sig = "v0=" + hmac.new(WEBHOOK_SECRET.encode(), f"v0:{ts}:{raw.decode()}".encode(), hashlib.sha256).hexdigest()
        return APIClient().post("/api/courses/zoom/webhook/", data=raw, content_type="application/json",
                                HTTP_X_ZM_SIGNATURE=sig, HTTP_X_ZM_REQUEST_TIMESTAMP=ts)

    def test_provisioned_meeting_resolves_to_its_class(self):
        self.assertEqual(ZoomRecordingService.find_live_class_for_zoom_meeting(self.mid, self.uuid), self.lc)
        self.assertEqual(ZoomRecordingService.find_live_class_for_zoom_meeting(self.mid, ""), self.lc)

    def test_participant_joined_records_attendance_for_provisioned_class(self):
        LiveClass.objects.filter(id=self.lc.id).update(status=LiveClass.ClassStatus.LIVE)
        LiveClassRegistrant.objects.create(live_class=self.lc, student=self.student,
                                           registrant_id="reg_meera_1", email=self.student.email)
        # Webhook event_ts must be an integer in seconds
        res = self._post_webhook({
            "event": "meeting.participant_joined", "event_ts": int(timezone.now().timestamp()),
            "payload": {"object": {"id": self.mid, "uuid": self.uuid, "participant": {
                "user_id": "16778240", "participant_uuid": "p_uuid_meera", "user_name": "Meera",
                "registrant_id": "reg_meera_1", "join_time": timezone.now().isoformat()}}},
        })
        self.assertEqual(res.status_code, 200, res.data)
        att = Attendance.objects.get(live_class=self.lc, student=self.student)
        self.assertTrue(att.is_currently_in_class)

    def test_meeting_ended_schedules_attendance_reconciliation(self):
        with mock.patch("courses.tasks.reconcile_zoom_attendance_task.apply_async") as reconcile:
            res = self._post_webhook({
                "event": "meeting.ended", "event_ts": int(timezone.now().timestamp()),
                "payload": {"object": {"id": self.mid, "uuid": self.uuid}},
            })
        self.assertEqual(res.status_code, 200, res.data)
        reconcile.assert_called_once_with(args=[self.lc.id], countdown=900)

    def test_recording_completed_is_linked_and_queued(self):
        with mock.patch("courses.tasks.process_zoom_recording_completed.delay") as delay:
            res = self._post_webhook({
                "event": "recording.completed", "event_ts": int(timezone.now().timestamp()),
                "download_token": "dl-token",
                "payload": {"object": {"id": self.mid, "uuid": self.uuid, "topic": self.lc.title,
                                       "recording_files": [{"id": "rf1", "file_type": "MP4",
                                                            "download_url": "https://zoom.us/rec/download/rf1",
                                                            "status": "completed", "file_size": 1024}]}},
            })
        self.assertEqual(res.status_code, 200, res.data)
        self.assertEqual(res.data["status"], "queued")
        event = ZoomWebhookEvent.objects.get(event_type="recording.completed")
        self.assertEqual(event.live_class_id, self.lc.id)
        delay.assert_called_once_with(event.id)

    def test_stale_or_replaced_meeting_webhook_does_not_affect_active_class(self):
        """
        When a meeting has been replaced on a LiveClass, an incoming webhook for the old,
        stale meeting ID must not update attendance or link records to the active live class.
        """
        stale_mid, stale_uuid = self.mid, self.uuid
        new_mid = "99988877766"
        new_uuid = "uuid-99988877766=="
        self.lc.zoom_meeting_id = new_mid
        self.lc.zoom_uuid = new_uuid
        self.lc.meeting_url = f"https://zoom.us/j/{new_mid}"
        self.lc.host_url = f"https://zoom.us/s/{new_mid}?zak=redact-me"
        self.lc.save(update_fields=['zoom_meeting_id', 'zoom_uuid', 'meeting_url', 'host_url'])
        ZoomMeetingRecord.objects.filter(live_class=self.lc, zoom_meeting_id=stale_mid).update(
            is_current=False, state=ZoomMeetingRecord.State.REASSIGNED
        )

        LiveClass.objects.filter(id=self.lc.id).update(status=LiveClass.ClassStatus.LIVE)
        LiveClassRegistrant.objects.create(
            live_class=self.lc, student=self.student,
            registrant_id="reg_meera_stale", email=self.student.email
        )

        # 1. Stale participant joined webhook for stale_mid -> must NOT record attendance
        res_part = self._post_webhook({
            "event": "meeting.participant_joined",
            "event_ts": int(timezone.now().timestamp()),
            "payload": {"object": {"id": stale_mid, "uuid": stale_uuid, "participant": {
                "user_id": "16778240", "participant_uuid": "p_uuid_stale", "user_name": "Meera",
                "registrant_id": "reg_meera_stale", "join_time": timezone.now().isoformat()}}},
        })
        self.assertEqual(res_part.status_code, 200)
        self.assertFalse(Attendance.objects.filter(live_class=self.lc, student=self.student).exists())

        # 2. Stale recording.completed webhook for stale_mid -> recorded with live_class=None
        with mock.patch("courses.tasks.process_zoom_recording_completed.delay") as delay:
            res_rec = self._post_webhook({
                "event": "recording.completed",
                "event_ts": int(timezone.now().timestamp()),
                "download_token": "dl-token",
                "payload": {"object": {"id": stale_mid, "uuid": stale_uuid, "topic": "Old Stale Meeting",
                                       "recording_files": [{"id": "rf_stale", "file_type": "MP4",
                                                            "download_url": "https://zoom.us/rec/download/rf_stale",
                                                            "status": "completed", "file_size": 1024}]}},
            })
        self.assertEqual(res_rec.status_code, 200)
        stale_event = ZoomWebhookEvent.objects.filter(meeting_uuid=stale_uuid).first()
        self.assertIsNotNone(stale_event)
        self.assertIsNone(stale_event.live_class_id)
        self.lc.refresh_from_db()
        self.assertEqual(self.lc.zoom_meeting_id, new_mid)


# =============================================================================
# 5. Simultaneous requests (PostgreSQL, real threads and connections)
# =============================================================================
class ConcurrentAdminZoomCreationTests(_AdminZoomFixture, TransactionTestCase):
    def setUp(self):
        if not IS_PG:
            self.skipTest("PostgreSQL required for real row-lock concurrency")
        self.make_fixture()
        self._rem_patch = mock.patch("courses.tasks.send_class_reminder.apply_async")
        self._notif_patch = mock.patch("notifications.services.LiveClassNotificationService.notify_scheduled")
        self._rem_patch.start()
        self._notif_patch.start()

    def tearDown(self):
        self._rem_patch.stop()
        self._notif_patch.stop()

    def _run_concurrently(self, fns):
        barrier = threading.Barrier(len(fns))
        results, errors = [None] * len(fns), []

        def runner(i, fn):
            try:
                barrier.wait(timeout=10)
                results[i] = fn()
            except Exception as exc:  # surfaced below
                errors.append(exc)
            finally:
                connection.close()

        threads = [threading.Thread(target=runner, args=(i, fn)) for i, fn in enumerate(fns)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        self.assertEqual(errors, [])
        return results

    def test_simultaneous_requests_for_one_class_create_exactly_one_meeting(self):
        lc = self.make_zoom_class("Race One Class")
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            responses = self._run_concurrently([lambda: self.provision_via_endpoint(lc)] * 6)
        codes = sorted(r.status_code for r in responses)
        self.assertEqual(codes.count(201), 1, codes)
        self.assertTrue(set(codes) <= {200, 201, 409}, codes)
        self.assertEqual(len(cloud.calls_of("POST")), 1)
        self.assertEqual(len(cloud.meetings), 1)
        self.assertEqual(ZoomMeetingRecord.objects.filter(live_class=lc).count(), 1)
        self.assertEqual(self.held_reservations(lc).count(), 1)
        lc.refresh_from_db()
        self.assertIn(lc.zoom_meeting_id, cloud.meetings)

    def test_second_request_while_create_is_in_flight_does_not_duplicate(self):
        lc = self.make_zoom_class("In Flight Class")
        second = {}
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            def concurrent_request():
                t = threading.Thread(target=lambda: (second.update(res=self.provision_via_endpoint(lc)),
                                                     connection.close()))
                t.start()
                t.join(timeout=30)
            cloud.hook("POST", self.HOST_A, concurrent_request)
            first = self.provision_via_endpoint(lc)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second["res"].status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(len(cloud.calls_of("POST")), 1)
        self.assertEqual(self.held_reservations(lc).count(), 1)

    def test_simultaneous_overlapping_class_creation_assigns_distinct_hosts(self):
        t1, b1 = self.make_instructor_and_batch("conc1")
        t2, b2 = self.make_instructor_and_batch("conc2")
        t3, b3 = self.make_instructor_and_batch("conc3")
        batches = [b1, b2, b3]
        titles = [f"Race Class {i}" for i in range(3)]
        with self.settings(**ZOOM_CREDS), FakeZoomCloud() as cloud:
            responses = self._run_concurrently([
                (lambda idx=i: self.create_class_via_api(titles[idx], batch=batches[idx]))
                for i in range(3)
            ])
        codes = sorted(r.status_code for r in responses)
        self.assertEqual(codes, [201, 201, 409], [getattr(r, "data", None) for r in responses])
        created = [r.data["id"] for r in responses if r.status_code == 201]
        hosts = set(HostReservation.objects.filter(live_class_id__in=created, status=HostReservation.Status.ACTIVE)
                    .values_list("host_account_id", flat=True))
        self.assertEqual(hosts, {self.host_a.id, self.host_b.id})
        self.assertEqual(LiveClass.objects.filter(title__in=titles).count(), 2)
        self.assertEqual(HostReservation.objects.filter(status__in=HELD).count(), 2)
        self.assertEqual(sorted(c[1] for c in cloud.calls_of("POST")), sorted([self.HOST_A, self.HOST_B]))
        self.assertEqual(len(cloud.meetings), 2)
