import hmac
import hashlib
import json
import datetime
from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status
from django.contrib.auth import get_user_model

from courses.models import (
    Course,
    LiveBatch,
    LiveClass,
    LiveBatchStudent,
    Attendance,
    AttendanceSession,
    UnmatchedZoomParticipant,
    LiveClassJoinIntent,
    LiveClassRegistrant,
    ZoomWebhookEvent,
)
from courses.services.zoom_attendance import ZoomAttendanceService, encode_zoom_uuid

User = get_user_model()


@override_settings(
    ZOOM_WEBHOOK_SECRET_TOKEN='test_zoom_webhook_secret_xyz',
    ENABLE_ZOOM_ATTENDANCE_V2=True,
    DEBUG=False,
)
class ZoomAttendanceV2TestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.secret_token = 'test_zoom_webhook_secret_xyz'
        self.webhook_url = '/api/courses/zoom/webhook/'

        # 1. Staff / Admin user
        self.admin = User.objects.create_superuser(
            username='admin_attend',
            email='admin_attend@example.com',
            password='testpassword123'
        )

        # 2. Instructor / Teacher
        self.instructor = User.objects.create_user(
            username='teacher_priya',
            email='teacher_priya@example.com',
            password='testpassword123',
            is_teacher=True
        )

        # 3. Student with Email (Path A)
        self.student_email = User.objects.create_user(
            username='+919876543210',
            email='student_email@example.com',
            first_name='Ananya',
            last_name='Nair',
            password='testpassword123'
        )

        # 4. Student without Email / Phone-only (Path B)
        self.student_phone = User.objects.create_user(
            username='+919876543211',
            email='',
            first_name='Kavya',
            last_name='Menon',
            password='testpassword123'
        )

        # 5. Live Batch & Class
        self.course = Course.objects.create(
            title='Bharathanatyam Advanced Live',
            description='Live dance course',
            price=3000.00,
            course_type=Course.CourseType.LIVE,
            is_published=True
        )
        self.batch = LiveBatch.objects.create(
            course=self.course,
            instructor=self.instructor,
            batch_type=LiveBatch.BatchType.GROUP
        )
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student_email)
        LiveBatchStudent.objects.create(batch=self.batch, student=self.student_phone)

        now = timezone.now()
        self.live_class = LiveClass.objects.create(
            title='Abhinaya Workshop Session 1',
            course=self.course,
            batch=self.batch,
            instructor=self.instructor,
            scheduled_start=now - datetime.timedelta(minutes=10),
            duration_minutes=60,
            meeting_url='https://zoom.us/j/98765432101',
            host_url='https://zoom.us/s/98765432101',
            zoom_meeting_id='98765432101',
            zoom_uuid='AbcDefG1234==',
            status=LiveClass.ClassStatus.LIVE
        )

    def _sign_webhook(self, raw_bytes, ts=None):
        ts_str = str(ts or int(timezone.now().timestamp()))
        msg = f"v0:{ts_str}:{raw_bytes.decode('utf-8')}".encode('utf-8')
        sig = 'v0=' + hmac.new(self.secret_token.encode('utf-8'), msg, hashlib.sha256).hexdigest()
        return {
            'HTTP_X_ZM_SIGNATURE': sig,
            'HTTP_X_ZM_REQUEST_TIMESTAMP': ts_str,
            'content_type': 'application/json'
        }

    # Test 1: Student with email joins successfully (Path A)
    @patch('courses.services.zoom.ZoomService.get_access_token', return_value='mock_zoom_token')
    @patch('requests.post')
    def test_01_student_with_email_joins_successfully(self, mock_post, mock_token):
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "registrant_id": "reg_ananya_99",
            "join_url": "https://zoom.us/w/98765432101?tk=mock_token_ananya"
        }
        mock_post.return_value = mock_response

        self.client.force_authenticate(user=self.student_email)
        res = self.client.post(f"/api/courses/live-classes/{self.live_class.id}/join/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data.get("registrant_id"), "reg_ananya_99")
        self.assertIn("https://zoom.us/w/98765432101", res.data.get("meeting_url"))

        registrant = LiveClassRegistrant.objects.filter(
            live_class=self.live_class,
            student=self.student_email
        ).first()
        self.assertIsNotNone(registrant)
        self.assertEqual(registrant.registrant_id, "reg_ananya_99")

    # Test 2: Student without email joins successfully (Path B)
    def test_02_student_without_email_joins_successfully(self):
        self.client.force_authenticate(user=self.student_phone)
        res = self.client.post(f"/api/courses/live-classes/{self.live_class.id}/join/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertIsNone(res.data.get("registrant_id"))
        self.assertIsNotNone(res.data.get("join_intent_token"))

        intent = LiveClassJoinIntent.objects.filter(
            live_class=self.live_class,
            student=self.student_phone
        ).first()
        self.assertIsNotNone(intent)
        self.assertEqual(intent.token, res.data.get("join_intent_token"))

    # Test 3: Student joins Zoom (Webhook creates session evidence, does not auto-mark status)
    def test_03_student_joins_zoom(self):
        LiveClassRegistrant.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            registrant_id="reg_ananya_99",
            email=self.student_email.email
        )

        join_payload = {
            "event": "meeting.participant_joined",
            "event_ts": int(timezone.now().timestamp()),
            "payload": {
                "object": {
                    "id": "98765432101",
                    "uuid": "AbcDefG1234==",
                    "participant": {
                        "user_id": "16778241",
                        "participant_uuid": "p_uuid_ananya_1",
                        "user_name": "Ananya Nair",
                        "registrant_id": "reg_ananya_99",
                        "join_time": timezone.now().isoformat()
                    }
                }
            }
        }
        raw_body = json.dumps(join_payload).encode('utf-8')
        headers = self._sign_webhook(raw_body)
        res = self.client.post(self.webhook_url, data=raw_body, **headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        att = Attendance.objects.get(live_class=self.live_class, student=self.student_email)
        self.assertTrue(att.is_currently_in_class)
        self.assertIsNotNone(att.first_joined_at)
        # Business rule check: status remains ABSENT until admin manually decides!
        self.assertEqual(att.status, Attendance.Status.ABSENT)

        session = AttendanceSession.objects.filter(attendance=att).first()
        self.assertIsNotNone(session)
        self.assertTrue(session.is_active)
        self.assertEqual(session.zoom_participant_uuid, "p_uuid_ananya_1")

    # Test 4: Student leaves Zoom (Webhook closes active session, calculates duration)
    def test_04_student_leaves_zoom(self):
        LiveClassRegistrant.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            registrant_id="reg_ananya_99",
            email=self.student_email.email
        )
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            status=Attendance.Status.ABSENT,
            is_currently_in_class=True
        )
        join_ts = timezone.now() - datetime.timedelta(minutes=30)
        AttendanceSession.objects.create(
            attendance=att,
            session_id=f"{self.live_class.id}:{self.student_email.id}:p_uuid_ananya_1:{int(join_ts.timestamp())}",
            zoom_participant_uuid="p_uuid_ananya_1",
            join_time=join_ts,
            is_active=True
        )

        leave_time = timezone.now()
        leave_payload = {
            "event": "meeting.participant_left",
            "event_ts": int(leave_time.timestamp()),
            "payload": {
                "object": {
                    "id": "98765432101",
                    "uuid": "AbcDefG1234==",
                    "participant": {
                        "user_id": "16778241",
                        "participant_uuid": "p_uuid_ananya_1",
                        "user_name": "Ananya Nair",
                        "registrant_id": "reg_ananya_99",
                        "leave_time": leave_time.isoformat(),
                        "duration": 1800
                    }
                }
            }
        }
        raw_body = json.dumps(leave_payload).encode('utf-8')
        headers = self._sign_webhook(raw_body)
        res = self.client.post(self.webhook_url, data=raw_body, **headers)
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        att.refresh_from_db()
        self.assertFalse(att.is_currently_in_class)
        self.assertIsNotNone(att.last_left_at)
        self.assertGreaterEqual(att.duration_minutes, 29)

        session = AttendanceSession.objects.filter(attendance=att).first()
        self.assertFalse(session.is_active)
        self.assertGreaterEqual(session.duration_seconds, 1800)

    # Test 5: Student reconnects multiple times
    def test_05_student_reconnects_multiple_times(self):
        LiveClassRegistrant.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            registrant_id="reg_ananya_99",
            email=self.student_email.email
        )

        t0 = timezone.now() - datetime.timedelta(minutes=50)
        t1 = t0 + datetime.timedelta(minutes=10)
        t2 = t1 + datetime.timedelta(minutes=5)
        t3 = t2 + datetime.timedelta(minutes=15)
        t4 = t3 + datetime.timedelta(minutes=5)
        t5 = t4 + datetime.timedelta(minutes=10)

        cycles = [
            ("meeting.participant_joined", t0, "p1", 0),
            ("meeting.participant_left", t1, "p1", 600),
            ("meeting.participant_joined", t2, "p2", 0),
            ("meeting.participant_left", t3, "p2", 900),
            ("meeting.participant_joined", t4, "p3", 0),
            ("meeting.participant_left", t5, "p3", 600),
        ]

        for ev_type, ts, p_uuid, dur in cycles:
            payload = {
                "event": ev_type,
                "event_ts": int(ts.timestamp()),
                "payload": {
                    "object": {
                        "id": "98765432101",
                        "uuid": "AbcDefG1234==",
                        "participant": {
                            "user_id": "16778241",
                            "participant_uuid": p_uuid,
                            "user_name": "Ananya Nair",
                            "registrant_id": "reg_ananya_99",
                            "join_time": ts.isoformat() if "joined" in ev_type else t0.isoformat(),
                            "leave_time": ts.isoformat() if "left" in ev_type else None,
                            "duration": dur
                        }
                    }
                }
            }
            raw = json.dumps(payload).encode('utf-8')
            res = self.client.post(self.webhook_url, data=raw, **self._sign_webhook(raw))
            self.assertEqual(res.status_code, status.HTTP_200_OK)

        att = Attendance.objects.get(live_class=self.live_class, student=self.student_email)
        sessions = AttendanceSession.objects.filter(attendance=att).order_by('join_time')
        self.assertEqual(sessions.count(), 3)
        self.assertAlmostEqual(att.duration_minutes, 35, delta=1)

    # Test 6: Duplicate join webhook (idempotency key prevents duplicate sessions)
    def test_06_duplicate_join_webhook(self):
        LiveClassRegistrant.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            registrant_id="reg_ananya_99",
            email=self.student_email.email
        )
        ts = int(timezone.now().timestamp())
        payload = {
            "event": "meeting.participant_joined",
            "event_ts": ts,
            "payload": {
                "object": {
                    "id": "98765432101",
                    "uuid": "AbcDefG1234==",
                    "participant": {
                        "user_id": "16778241",
                        "participant_uuid": "p_dedupe_join",
                        "user_name": "Ananya Nair",
                        "registrant_id": "reg_ananya_99",
                        "join_time": timezone.now().isoformat()
                    }
                }
            }
        }
        raw = json.dumps(payload).encode('utf-8')
        headers = self._sign_webhook(raw, ts)

        res1 = self.client.post(self.webhook_url, data=raw, **headers)
        self.assertEqual(res1.status_code, status.HTTP_200_OK)

        res2 = self.client.post(self.webhook_url, data=raw, **headers)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data.get("status"), "duplicate_ignored")

        att = Attendance.objects.get(live_class=self.live_class, student=self.student_email)
        self.assertEqual(att.sessions.count(), 1)

    # Test 7: Duplicate leave webhook
    def test_07_duplicate_leave_webhook(self):
        LiveClassRegistrant.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            registrant_id="reg_ananya_99",
            email=self.student_email.email
        )
        ts = int(timezone.now().timestamp())
        payload = {
            "event": "meeting.participant_left",
            "event_ts": ts,
            "payload": {
                "object": {
                    "id": "98765432101",
                    "uuid": "AbcDefG1234==",
                    "participant": {
                        "user_id": "16778241",
                        "participant_uuid": "p_dedupe_leave",
                        "user_name": "Ananya Nair",
                        "registrant_id": "reg_ananya_99",
                        "leave_time": timezone.now().isoformat(),
                        "duration": 600
                    }
                }
            }
        }
        raw = json.dumps(payload).encode('utf-8')
        headers = self._sign_webhook(raw, ts)

        res1 = self.client.post(self.webhook_url, data=raw, **headers)
        self.assertEqual(res1.status_code, status.HTTP_200_OK)

        res2 = self.client.post(self.webhook_url, data=raw, **headers)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(res2.data.get("status"), "duplicate_ignored")

    # Test 8: Leave event received before join event (Out-of-order handling)
    def test_08_leave_event_received_before_join_event(self):
        LiveClassRegistrant.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            registrant_id="reg_ananya_99",
            email=self.student_email.email
        )
        now = timezone.now()
        leave_payload = {
            "event": "meeting.participant_left",
            "event_ts": int(now.timestamp()),
            "payload": {
                "object": {
                    "id": "98765432101",
                    "uuid": "AbcDefG1234==",
                    "participant": {
                        "user_id": "16778241",
                        "participant_uuid": "p_out_of_order",
                        "user_name": "Ananya Nair",
                        "registrant_id": "reg_ananya_99",
                        "leave_time": now.isoformat(),
                        "duration": 1200
                    }
                }
            }
        }
        raw = json.dumps(leave_payload).encode('utf-8')
        res = self.client.post(self.webhook_url, data=raw, **self._sign_webhook(raw))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        att = Attendance.objects.get(live_class=self.live_class, student=self.student_email)
        session = att.sessions.first()
        self.assertIsNotNone(session)
        self.assertFalse(session.is_active)
        self.assertEqual(session.duration_seconds, 1200)

    # Test 9: Participant identity cannot be determined (Routed to Unmatched)
    def test_09_participant_identity_cannot_be_determined(self):
        payload = {
            "event": "meeting.participant_joined",
            "event_ts": int(timezone.now().timestamp()),
            "payload": {
                "object": {
                    "id": "98765432101",
                    "uuid": "AbcDefG1234==",
                    "participant": {
                        "user_id": "16778299",
                        "participant_uuid": "uuid_unknown_dancer",
                        "user_name": "iPhone (Guest)",
                        "join_time": timezone.now().isoformat()
                    }
                }
            }
        }
        raw = json.dumps(payload).encode('utf-8')
        res = self.client.post(self.webhook_url, data=raw, **self._sign_webhook(raw))
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        unmatched = UnmatchedZoomParticipant.objects.filter(
            live_class=self.live_class,
            zoom_participant_uuid="uuid_unknown_dancer"
        ).first()
        self.assertIsNotNone(unmatched)
        self.assertEqual(unmatched.status, UnmatchedZoomParticipant.Status.PENDING)
        self.assertEqual(unmatched.participant_name, "iPhone (Guest)")

    # Test 10: Admin manually links unmatched participant
    def test_10_admin_manually_links_unmatched_participant(self):
        unmatched = UnmatchedZoomParticipant.objects.create(
            live_class=self.live_class,
            zoom_participant_uuid="uuid_guest_phone",
            participant_name="Dance Student",
            join_time=timezone.now() - datetime.timedelta(minutes=40),
            leave_time=timezone.now(),
            duration_seconds=2400,
            is_active=False,
            status=UnmatchedZoomParticipant.Status.PENDING
        )

        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            f"/api/courses/live-classes/{self.live_class.id}/attendance/link-unmatched/",
            data={"unmatched_id": unmatched.id, "student_id": self.student_phone.id}
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        unmatched.refresh_from_db()
        self.assertEqual(unmatched.status, UnmatchedZoomParticipant.Status.RESOLVED)
        self.assertEqual(unmatched.linked_student, self.student_phone)

        att = Attendance.objects.get(live_class=self.live_class, student=self.student_phone)
        self.assertGreaterEqual(att.duration_minutes, 39)

    # Test 11: Two admins attempt to resolve the same participant
    def test_11_two_admins_attempt_to_resolve_same_participant(self):
        unmatched = UnmatchedZoomParticipant.objects.create(
            live_class=self.live_class,
            zoom_participant_uuid="uuid_contention",
            participant_name="Contention Guest",
            join_time=timezone.now() - datetime.timedelta(minutes=20),
            leave_time=timezone.now(),
            duration_seconds=1200,
            is_active=False,
            status=UnmatchedZoomParticipant.Status.PENDING
        )

        att, _ = ZoomAttendanceService.link_unmatched_participant(
            unmatched_id=unmatched.id,
            student_id=self.student_phone.id,
            admin_user=self.admin
        )
        self.assertIsNotNone(att)

        with self.assertRaises(ValueError):
            ZoomAttendanceService.link_unmatched_participant(
                unmatched_id=unmatched.id,
                student_id=self.student_email.id,
                admin_user=self.instructor
            )

    # Test 12: Zoom report reconciliation
    @patch('courses.services.zoom.ZoomService.get_access_token', return_value='mock_report_token')
    @patch('requests.get')
    def test_12_zoom_report_reconciliation(self, mock_get, mock_token):
        LiveClassRegistrant.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            registrant_id="reg_report_1",
            email=self.student_email.email
        )
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "page_count": 1,
            "page_size": 300,
            "total_records": 1,
            "next_page_token": "",
            "participants": [
                {
                    "id": "p_report_ananya",
                    "user_id": "16778241",
                    "name": "Ananya Nair",
                    "user_email": self.student_email.email,
                    "registrant_id": "reg_report_1",
                    "join_time": (timezone.now() - datetime.timedelta(minutes=45)).isoformat(),
                    "leave_time": timezone.now().isoformat(),
                    "duration": 2700
                }
            ]
        }
        mock_get.return_value = mock_res

        result = ZoomAttendanceService.reconcile_meeting_participants(self.live_class)
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("reconciled"), 1)

        att = Attendance.objects.get(live_class=self.live_class, student=self.student_email)
        self.assertEqual(att.reconciliation_status, "RECONCILED")
        self.assertEqual(att.zoom_reported_duration_seconds, 2700)

    # Test 13: Paginated Zoom reports
    @patch('courses.services.zoom.ZoomService.get_access_token', return_value='mock_report_token')
    @patch('requests.get')
    def test_13_paginated_zoom_reports(self, mock_get, mock_token):
        LiveClassRegistrant.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            registrant_id="reg_page_1",
            email=self.student_email.email
        )
        mock_res1 = MagicMock()
        mock_res1.status_code = 200
        mock_res1.json.return_value = {
            "page_count": 2,
            "page_size": 1,
            "total_records": 2,
            "next_page_token": "token_page_2",
            "participants": [
                {
                    "id": "p_page_1",
                    "name": "Ananya Nair",
                    "registrant_id": "reg_page_1",
                    "join_time": (timezone.now() - datetime.timedelta(minutes=30)).isoformat(),
                    "leave_time": timezone.now().isoformat(),
                    "duration": 1800
                }
            ]
        }
        mock_res2 = MagicMock()
        mock_res2.status_code = 200
        mock_res2.json.return_value = {
            "page_count": 2,
            "page_size": 1,
            "total_records": 2,
            "next_page_token": "",
            "participants": [
                {
                    "id": "p_page_2",
                    "name": "Unknown Attendee",
                    "join_time": (timezone.now() - datetime.timedelta(minutes=20)).isoformat(),
                    "leave_time": timezone.now().isoformat(),
                    "duration": 1200
                }
            ]
        }
        mock_get.side_effect = [mock_res1, mock_res2]

        result = ZoomAttendanceService.reconcile_meeting_participants(self.live_class)
        self.assertEqual(result.get("status"), "success")
        self.assertEqual(result.get("total_records"), 2)
        self.assertEqual(result.get("reconciled"), 1)
        self.assertEqual(result.get("unmatched"), 1)

    # Test 14: Zoom report temporarily unavailable (404)
    @patch('courses.services.zoom.ZoomService.get_access_token', return_value='mock_report_token')
    @patch('requests.get')
    def test_14_zoom_report_temporarily_unavailable(self, mock_get, mock_token):
        mock_res = MagicMock()
        mock_res.status_code = 404
        mock_get.return_value = mock_res

        result = ZoomAttendanceService.reconcile_meeting_participants(self.live_class)
        self.assertEqual(result.get("status"), "not_ready")

    # Test 15: Manual attendance override survives reconciliation
    @patch('courses.services.zoom.ZoomService.get_access_token', return_value='mock_report_token')
    @patch('requests.get')
    def test_15_manual_attendance_override_survives_reconciliation(self, mock_get, mock_token):
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            status=Attendance.Status.EXCUSED,
            duration_minutes=0,
            is_manual_override=True,
            marked_by=self.admin
        )
        LiveClassRegistrant.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            registrant_id="reg_excused_1",
            email=self.student_email.email
        )
        mock_res = MagicMock()
        mock_res.status_code = 200
        mock_res.json.return_value = {
            "page_count": 1,
            "next_page_token": "",
            "participants": [
                {
                    "id": "p_excused_1",
                    "name": "Ananya Nair",
                    "registrant_id": "reg_excused_1",
                    "join_time": (timezone.now() - datetime.timedelta(minutes=50)).isoformat(),
                    "leave_time": timezone.now().isoformat(),
                    "duration": 3000
                }
            ]
        }
        mock_get.return_value = mock_res

        ZoomAttendanceService.reconcile_meeting_participants(self.live_class)

        att.refresh_from_db()
        self.assertEqual(att.status, Attendance.Status.EXCUSED)
        self.assertTrue(att.is_manual_override)

    # Test 16: Duration calculation across multiple sessions
    def test_16_duration_calculation_across_multiple_sessions(self):
        att = Attendance.objects.create(live_class=self.live_class, student=self.student_email)
        now = timezone.now()

        AttendanceSession.objects.create(
            attendance=att,
            session_id="s1",
            join_time=now - datetime.timedelta(minutes=60),
            leave_time=now - datetime.timedelta(minutes=50),
            duration_seconds=600,
            is_active=False
        )
        AttendanceSession.objects.create(
            attendance=att,
            session_id="s2",
            join_time=now - datetime.timedelta(minutes=40),
            leave_time=now - datetime.timedelta(minutes=25),
            duration_seconds=900,
            is_active=False
        )
        AttendanceSession.objects.create(
            attendance=att,
            session_id="s3",
            join_time=now - datetime.timedelta(minutes=15),
            leave_time=now,
            duration_seconds=900,
            is_active=False
        )

        total_sec = att.calculate_total_duration_seconds()
        self.assertEqual(total_sec, 2400)
        self.assertEqual(int(total_sec // 60), 40)

    # Test 17: Active session duration updates dynamically
    def test_17_active_session_duration_updates_dynamically(self):
        att = Attendance.objects.create(live_class=self.live_class, student=self.student_email)
        ten_mins_ago = timezone.now() - datetime.timedelta(minutes=10)

        session = AttendanceSession.objects.create(
            attendance=att,
            session_id="s_active",
            join_time=ten_mins_ago,
            is_active=True
        )

        dur = session.calculate_duration()
        self.assertGreaterEqual(dur, 595)
        self.assertLessEqual(dur, 610)

    # Test 18: Admin API permission checks
    def test_18_admin_api_permission_checks(self):
        self.client.force_authenticate(user=self.student_email)
        res_live = self.client.get(f"/api/courses/live-classes/{self.live_class.id}/attendance/live/")
        self.assertEqual(res_live.status_code, status.HTTP_403_FORBIDDEN)

        res_link = self.client.post(
            f"/api/courses/live-classes/{self.live_class.id}/attendance/link-unmatched/",
            data={"unmatched_id": 1, "student_id": self.student_phone.id}
        )
        self.assertEqual(res_link.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.instructor)
        res_teacher = self.client.get(f"/api/courses/live-classes/{self.live_class.id}/attendance/live/")
        self.assertEqual(res_teacher.status_code, status.HTTP_200_OK)

    # Test 19: Live attendance polling endpoint
    def test_19_live_attendance_polling_endpoint(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(f"/api/courses/live-classes/{self.live_class.id}/attendance/live/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        data = res.data
        self.assertIn("live_class", data)
        self.assertIn("server_timestamp", data)
        self.assertIn("currently_connected_count", data)
        self.assertIn("students", data)
        self.assertIn("unmatched_participants", data)

        student_entry = next(s for s in data["students"] if s["student_id"] == self.student_email.id)
        self.assertEqual(student_entry["student_name"], "Ananya Nair")
        self.assertIn("formatted_duration", student_entry)
        self.assertIn("sessions", student_entry)

    # Test 20: Existing attendance and live-class functionality remain compatible
    def test_20_existing_attendance_functionality_compatible(self):
        self.client.force_authenticate(user=self.admin)

        res_get = self.client.get(f"/api/courses/live-classes/{self.live_class.id}/attendance/")
        self.assertEqual(res_get.status_code, status.HTTP_200_OK)
        self.assertIsInstance(res_get.data, list)

        post_data = [
            {"student": self.student_email.id, "status": "PRESENT", "duration_minutes": 55},
            {"student": self.student_phone.id, "status": "LATE", "duration_minutes": 35}
        ]
        res_post = self.client.post(
            f"/api/courses/live-classes/{self.live_class.id}/attendance/",
            data=post_data,
            format='json'
        )
        self.assertEqual(res_post.status_code, status.HTTP_200_OK)

        att_email = Attendance.objects.get(live_class=self.live_class, student=self.student_email)
        self.assertEqual(att_email.status, Attendance.Status.PRESENT)
        self.assertEqual(att_email.duration_minutes, 55)
        self.assertTrue(att_email.is_manual_override)

    # Test 21: Multiple simultaneous participant joins in the same second without deduplication collision
    def test_21_simultaneous_participant_joins_different_students(self):
        same_ts = int(timezone.now().timestamp())

        payload_1 = {
            "event": "meeting.participant_joined",
            "event_ts": same_ts,
            "payload": {
                "object": {
                    "id": "98765432101",
                    "uuid": "AbcDefG1234==",
                    "participant": {
                        "user_id": "1001",
                        "participant_uuid": "p_uuid_student_1",
                        "user_name": "Student One",
                        "join_time": timezone.now().isoformat()
                    }
                }
            }
        }
        payload_2 = {
            "event": "meeting.participant_joined",
            "event_ts": same_ts,
            "payload": {
                "object": {
                    "id": "98765432101",
                    "uuid": "AbcDefG1234==",
                    "participant": {
                        "user_id": "1002",
                        "participant_uuid": "p_uuid_student_2",
                        "user_name": "Student Two",
                        "join_time": timezone.now().isoformat()
                    }
                }
            }
        }

        raw_1 = json.dumps(payload_1).encode('utf-8')
        res_1 = self.client.post(self.webhook_url, data=raw_1, **self._sign_webhook(raw_1, same_ts))
        self.assertEqual(res_1.status_code, status.HTTP_200_OK)
        self.assertNotEqual(res_1.data.get("status"), "duplicate_ignored")

        raw_2 = json.dumps(payload_2).encode('utf-8')
        res_2 = self.client.post(self.webhook_url, data=raw_2, **self._sign_webhook(raw_2, same_ts))
        self.assertEqual(res_2.status_code, status.HTTP_200_OK)
        # CRITICAL VERIFICATION: Student 2 must NOT be dropped as duplicate_ignored
        self.assertNotEqual(res_2.data.get("status"), "duplicate_ignored")

        events = ZoomWebhookEvent.objects.filter(
            event_type="meeting.participant_joined",
            event_ts=same_ts
        )
        self.assertEqual(events.count(), 2)
        event_ids = [e.event_id for e in events]
        self.assertIn("p_uuid_student_1", event_ids[0] + event_ids[1])
        self.assertIn("p_uuid_student_2", event_ids[0] + event_ids[1])

    # Test 22: Heartbeat does NOT overwrite admin manual ABSENT decision
    def test_22_heartbeat_preserves_admin_absent_decision(self):
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student_phone,
            status=Attendance.Status.ABSENT,
            is_manual_override=True,
            duration_minutes=0
        )

        self.client.force_authenticate(user=self.student_phone)
        res = self.client.post(f"/api/courses/live-classes/{self.live_class.id}/heartbeat/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        att.refresh_from_db()
        # Admin decided ABSENT; client heartbeat must NEVER flip it to PRESENT!
        self.assertEqual(att.status, Attendance.Status.ABSENT)
        self.assertTrue(att.is_manual_override)

    # Test 23: Student join_class does NOT auto-classify as PRESENT or LATE
    def test_23_join_class_does_not_auto_classify_status(self):
        self.client.force_authenticate(user=self.student_phone)
        res = self.client.post(f"/api/courses/live-classes/{self.live_class.id}/join/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        att = Attendance.objects.get(live_class=self.live_class, student=self.student_phone)
        # Status remains ABSENT (administrative authority), evidence is collected
        self.assertEqual(att.status, Attendance.Status.ABSENT)
        self.assertTrue(att.is_currently_in_class)
        self.assertIsNotNone(att.joined_at)

    # Test 24: Admin attendance submission without manual duration preserves Zoom duration
    def test_24_attendance_post_preserves_zoom_duration(self):
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            status=Attendance.Status.ABSENT,
            duration_minutes=42,
            is_manual_override=False,
            is_duration_manual_override=False
        )

        self.client.force_authenticate(user=self.admin)
        post_data = [{
            "student": self.student_email.id,
            "status": "PRESENT",
            "is_duration_manual": False
        }]
        res = self.client.post(
            f"/api/courses/live-classes/{self.live_class.id}/attendance/",
            data=post_data,
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        att.refresh_from_db()
        self.assertEqual(att.status, Attendance.Status.PRESENT)
        self.assertTrue(att.is_manual_override)
        # Duration must NOT be wiped out to 0!
        self.assertEqual(att.duration_minutes, 42)
        self.assertFalse(att.is_duration_manual_override)

    # Test 25: Admin explicitly edits duration and it survives subsequent webhook and report reconciliation
    def test_25_admin_edited_duration_survives_subsequent_webhooks(self):
        att = Attendance.objects.create(
            live_class=self.live_class,
            student=self.student_email,
            status=Attendance.Status.ABSENT,
            duration_minutes=0
        )

        self.client.force_authenticate(user=self.admin)
        post_data = [{
            "student": self.student_email.id,
            "status": "PRESENT",
            "duration_minutes": 50,
            "is_duration_manual": True
        }]
        res = self.client.post(
            f"/api/courses/live-classes/{self.live_class.id}/attendance/",
            data=post_data,
            format='json'
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        att.refresh_from_db()
        self.assertEqual(att.duration_minutes, 50)
        self.assertTrue(att.is_duration_manual_override)

        # Webhook participant left arrives with duration 10 min
        leave_payload = {
            "object": {
                "id": "98765432101",
                "uuid": "AbcDefG1234==",
                "participant": {
                    "user_id": "16778241",
                    "participant_uuid": "p_uuid_ananya_1",
                    "user_name": "Ananya Nair",
                    "email": self.student_email.email,
                    "leave_time": timezone.now().isoformat(),
                    "duration": 600
                }
            }
        }
        ZoomAttendanceService.handle_participant_left(leave_payload)

        att.refresh_from_db()
        # Duration must STILL be 50 because admin explicitly set it!
        self.assertEqual(att.duration_minutes, 50)

    # Test 26: Zoom UUID encoding tests across all Zoom documentation variants
    def test_26_zoom_uuid_encoding_variants(self):
        from urllib.parse import quote

        # 1. UUID without slash
        uuid_normal = "AbcDef123=="
        self.assertEqual(encode_zoom_uuid(uuid_normal), "AbcDef123%3D%3D")

        # 2. UUID with one slash in middle
        uuid_one_slash = "Abc/Def123=="
        # Single encode: Abc%2FDef123%3D%3D; Double encode: Abc%252FDef123%253D%253D
        self.assertEqual(encode_zoom_uuid(uuid_one_slash), quote(quote(uuid_one_slash, safe=''), safe=''))
        self.assertIn("%252F", encode_zoom_uuid(uuid_one_slash))

        # 3. UUID starting with slash
        uuid_leading_slash = "/AbcDef123=="
        self.assertTrue(encode_zoom_uuid(uuid_leading_slash).startswith("%252F"))

        # 4. UUID with double slash
        uuid_double_slash = "Abc//Def123=="
        self.assertIn("%252F%252F", encode_zoom_uuid(uuid_double_slash))

        # 5. UUID with plus signs and padding
        uuid_plus = "+Abc/Def=="
        self.assertIn("%252B", encode_zoom_uuid(uuid_plus))
        self.assertIn("%252F", encode_zoom_uuid(uuid_plus))

    # Test 27: Auto-completed class triggers reconciliation task
    @patch('courses.tasks.reconcile_zoom_attendance_task.apply_async')
    def test_27_auto_completed_class_schedules_reconciliation(self, mock_apply_async):
        from courses.views import LiveClassViewSet
        view = LiveClassViewSet()

        past_class = LiveClass.objects.create(
            title='Expired Class',
            course=self.course,
            scheduled_start=timezone.now() - datetime.timedelta(hours=3),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            meeting_url='https://zoom.us/j/1122334455',
            zoom_meeting_id='1122334455',
            zoom_uuid='uuid_expired_123',
            status=LiveClass.ClassStatus.LIVE
        )

        view._auto_complete_expired_classes()
        past_class.refresh_from_db()
        self.assertEqual(past_class.status, LiveClass.ClassStatus.COMPLETED)
        mock_apply_async.assert_called_once_with(args=[past_class.id], countdown=900)

    # Test 28: Live attendance endpoint includes suggested match for unmatched attendees
    def test_28_live_attendance_endpoint_includes_suggestions(self):
        intent = LiveClassJoinIntent.objects.create(
            student=self.student_phone,
            live_class=self.live_class,
            token="token_kavya_test_123",
            expires_at=timezone.now() + datetime.timedelta(minutes=15)
        )

        UnmatchedZoomParticipant.objects.create(
            live_class=self.live_class,
            zoom_participant_uuid="uuid_phone_guest",
            participant_name="Kavya M",
            join_time=timezone.now(),
            duration_seconds=300
        )

        self.client.force_authenticate(user=self.admin)
        res = self.client.get(f"/api/courses/live-classes/{self.live_class.id}/attendance/live/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)

        unmatched_list = res.data.get("unmatched_participants", [])
        self.assertTrue(len(unmatched_list) > 0)
        unmatched_item = unmatched_list[0]
        self.assertEqual(unmatched_item.get("suggested_student_id"), self.student_phone.id)
        self.assertIsNotNone(unmatched_item.get("suggested_reason"))
