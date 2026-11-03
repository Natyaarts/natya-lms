import threading
import time
import json
import datetime
from unittest.mock import patch, MagicMock

from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from django.db import connection, connections
from django.contrib.auth import get_user_model

from courses.models import (
    Course,
    LiveBatch,
    LiveClass,
    LiveBatchStudent,
    Attendance,
    AttendanceSession,
    UnmatchedZoomParticipant,
    ZoomWebhookEvent,
)
from courses.services.zoom_attendance import ZoomAttendanceService

User = get_user_model()


@override_settings(
    ZOOM_WEBHOOK_SECRET_TOKEN='test_concurrency_webhook_secret',
    ENABLE_ZOOM_ATTENDANCE_V2=True,
    DEBUG=False,
)
class ZoomAttendanceConcurrencyTestCase(TransactionTestCase):
    """
    Genuine multi-threaded concurrency and row-locking tests executed against PostgreSQL.
    Uses TransactionTestCase so each thread commits within its own real database connection.
    """

    def setUp(self):
        super().setUp()
        connection.close()

        self.admin1 = User.objects.create_superuser(
            username='admin_concur_1',
            email='admin1@example.com',
            password='testpassword123'
        )
        self.admin2 = User.objects.create_superuser(
            username='admin_concur_2',
            email='admin2@example.com',
            password='testpassword123'
        )
        self.instructor = User.objects.create_user(
            username='teacher_concur',
            email='teacher_concur@example.com',
            password='testpassword123',
            is_teacher=True
        )
        self.student = User.objects.create_user(
            username='+919876500001',
            email='student_concur@example.com',
            first_name='Kavya',
            last_name='Nair',
            password='testpassword123'
        )

        self.course = Course.objects.create(
            title='Bharathanatyam Concurrency Test',
            price=2500.00,
            course_type=Course.CourseType.LIVE,
            is_published=True
        )
        self.batch = LiveBatch.objects.create(
            course=self.course,
            instructor=self.instructor,
            batch_type=LiveBatch.BatchType.GROUP
        )
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student)

        self.live_class = LiveClass.objects.create(
            title='Abhinaya Live Concurrency',
            course=self.course,
            batch=self.batch,
            instructor=self.instructor,
            scheduled_start=timezone.now() - datetime.timedelta(minutes=30),
            duration_minutes=60,
            meeting_url='https://zoom.us/j/99887766554',
            host_url='https://zoom.us/s/99887766554',
            zoom_meeting_id='99887766554',
            zoom_uuid='AbcConcur123==',
            status=LiveClass.ClassStatus.LIVE
        )

    def tearDown(self):
        super().tearDown()
        connections.close_all()

    # Test C1: Two admins linking different unmatched participants for the same student concurrently
    def test_c1_concurrent_linking_same_student_different_participants(self):
        join_1 = timezone.now() - datetime.timedelta(minutes=45)
        leave_1 = join_1 + datetime.timedelta(minutes=15)
        unmatched_1 = UnmatchedZoomParticipant.objects.create(
            live_class=self.live_class,
            zoom_participant_uuid="uuid_part_1",
            participant_name="Kavya Phone",
            join_time=join_1,
            leave_time=leave_1,
            duration_seconds=900,
            status=UnmatchedZoomParticipant.Status.PENDING
        )

        join_2 = timezone.now() - datetime.timedelta(minutes=25)
        leave_2 = join_2 + datetime.timedelta(minutes=20)
        unmatched_2 = UnmatchedZoomParticipant.objects.create(
            live_class=self.live_class,
            zoom_participant_uuid="uuid_part_2",
            participant_name="Kavya Laptop",
            join_time=join_2,
            leave_time=leave_2,
            duration_seconds=1200,
            status=UnmatchedZoomParticipant.Status.PENDING
        )

        barrier = threading.Barrier(2)
        results = {}
        errors = []

        def worker1():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                att, un = ZoomAttendanceService.link_unmatched_participant(
                    unmatched_1.id, self.student.id, self.admin1
                )
                results['w1'] = (att.id, un.status)
            except Exception as e:
                errors.append(('w1', e))
            finally:
                connections.close_all()

        def worker2():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                att, un = ZoomAttendanceService.link_unmatched_participant(
                    unmatched_2.id, self.student.id, self.admin2
                )
                results['w2'] = (att.id, un.status)
            except Exception as e:
                errors.append(('w2', e))
            finally:
                connections.close_all()

        t1 = threading.Thread(target=worker1)
        t2 = threading.Thread(target=worker2)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(errors), 0, f"Concurrent linking failed with errors: {errors}")
        self.assertIn('w1', results)
        self.assertIn('w2', results)

        connection.close()
        att = Attendance.objects.get(live_class=self.live_class, student=self.student)
        self.assertEqual(att.sessions.count(), 2)
        # Total duration must equal 15m (900s) + 20m (1200s) = 35m without lost updates
        self.assertEqual(att.duration_minutes, 35)

    # Test C2: Two admins attempting to link the EXACT SAME unmatched participant concurrently
    def test_c2_concurrent_linking_same_unmatched_participant_race(self):
        unmatched = UnmatchedZoomParticipant.objects.create(
            live_class=self.live_class,
            zoom_participant_uuid="uuid_single_race",
            participant_name="Race Attendee",
            join_time=timezone.now() - datetime.timedelta(minutes=20),
            duration_seconds=600,
            status=UnmatchedZoomParticipant.Status.PENDING
        )

        barrier = threading.Barrier(2)
        results = []
        rejected_errors = []

        def link_worker(admin_user):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                att, un = ZoomAttendanceService.link_unmatched_participant(
                    unmatched.id, self.student.id, admin_user
                )
                results.append(admin_user.username)
            except ValueError as e:
                rejected_errors.append(str(e))
            except Exception as e:
                rejected_errors.append(f"UNEXPECTED: {e}")
            finally:
                connections.close_all()

        t1 = threading.Thread(target=link_worker, args=(self.admin1,))
        t2 = threading.Thread(target=link_worker, args=(self.admin2,))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        connection.close()
        # Exactly one admin must succeed, and the other must be safely rejected by select_for_update
        self.assertEqual(len(results), 1)
        self.assertEqual(len(rejected_errors), 1)
        self.assertIn("already been linked", rejected_errors[0])

    # Test C3: Webhook leave and report reconciliation concurrently updating attendance
    @patch('courses.services.zoom.ZoomService.get_access_token', return_value='mock_token')
    @patch('requests.get')
    def test_c3_concurrent_webhook_and_report_reconciliation(self, mock_get, mock_token):
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student,
            status=Attendance.Status.ABSENT,
            duration_minutes=0
        )

        join_time = timezone.now() - datetime.timedelta(minutes=40)
        leave_time = timezone.now()

        # Mock Zoom report API response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "participants": [{
                "id": "p_report_uuid_1",
                "user_id": "16778901",
                "user_email": self.student.email,
                "join_time": join_time.isoformat(),
                "leave_time": leave_time.isoformat(),
                "duration": 2400
            }],
            "next_page_token": ""
        }
        mock_get.return_value = mock_response

        barrier = threading.Barrier(2)
        errors = []

        def webhook_worker():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                leave_payload = {
                    "object": {
                        "id": "99887766554",
                        "uuid": "AbcConcur123==",
                        "participant": {
                            "user_id": "16778901",
                            "participant_uuid": "p_webhook_uuid_1",
                            "email": self.student.email,
                            "user_name": "Kavya Nair",
                            "join_time": join_time.isoformat(),
                            "leave_time": leave_time.isoformat(),
                            "duration": 2400
                        }
                    }
                }
                ZoomAttendanceService.handle_participant_left(leave_payload)
            except Exception as e:
                errors.append(('webhook', e))
            finally:
                connections.close_all()

        def report_worker():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                ZoomAttendanceService.reconcile_meeting_participants(self.live_class)
            except Exception as e:
                errors.append(('report', e))
            finally:
                connections.close_all()

        t1 = threading.Thread(target=webhook_worker)
        t2 = threading.Thread(target=report_worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(errors), 0, f"Concurrent reconciliation failed: {errors}")

        connection.close()
        att.refresh_from_db()
        self.assertGreaterEqual(att.duration_minutes, 40)
        self.assertEqual(att.status, Attendance.Status.ABSENT)

    # Test C4: Manual attendance decisions and duration survive concurrent webhooks and reports
    @patch('courses.services.zoom.ZoomService.get_access_token', return_value='mock_token')
    @patch('requests.get')
    def test_c4_manual_override_survives_concurrent_updates(self, mock_get, mock_token):
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student,
            status=Attendance.Status.PRESENT,
            duration_minutes=55,
            is_manual_override=True,
            is_duration_manual_override=True
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "participants": [{
                "id": "p_report_uuid_override",
                "user_id": "16778902",
                "user_email": self.student.email,
                "join_time": (timezone.now() - datetime.timedelta(minutes=10)).isoformat(),
                "leave_time": timezone.now().isoformat(),
                "duration": 600
            }],
            "next_page_token": ""
        }
        mock_get.return_value = mock_response

        barrier = threading.Barrier(2)
        errors = []

        def webhook_worker():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                leave_payload = {
                    "object": {
                        "id": "99887766554",
                        "uuid": "AbcConcur123==",
                        "participant": {
                            "user_id": "16778902",
                            "participant_uuid": "p_webhook_override",
                            "email": self.student.email,
                            "leave_time": timezone.now().isoformat(),
                            "duration": 300
                        }
                    }
                }
                ZoomAttendanceService.handle_participant_left(leave_payload)
            except Exception as e:
                errors.append(('webhook', e))
            finally:
                connections.close_all()

        def report_worker():
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                ZoomAttendanceService.reconcile_meeting_participants(self.live_class)
            except Exception as e:
                errors.append(('report', e))
            finally:
                connections.close_all()

        t1 = threading.Thread(target=webhook_worker)
        t2 = threading.Thread(target=report_worker)
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        self.assertEqual(len(errors), 0)

        connection.close()
        att.refresh_from_db()
        # Admin manual decisions MUST SURVIVE ALL CONCURRENT UPDATES
        self.assertEqual(att.status, Attendance.Status.PRESENT)
        self.assertEqual(att.duration_minutes, 55)
        self.assertTrue(att.is_manual_override)
        self.assertTrue(att.is_duration_manual_override)

