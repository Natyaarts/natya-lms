import json
import pathlib
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from courses.models import Course, LiveBatch, LiveBatchStudent, LiveClass
from notifications.models import WhatsAppDeliveryLog
from notifications.services import (
    InteraktPermanentError,
    InteraktWhatsAppService,
    LiveClassNotificationService,
)
from notifications.tasks import send_live_class_whatsapp_task

User = get_user_model()


@override_settings(
    CELERY_TASK_ALWAYS_EAGER=True,
    CELERY_TASK_EAGER_PROPAGATES=True,
    INTERAKT_SECRET_KEY="test_interakt_key",
    IS_STAGING=False,
    FRONTEND_BASE_URL="https://academy.natyaarts.com",
)
class WhatsAppLiveClassNotificationTests(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="wa_teacher",
            email="wa_teacher@example.com",
            password="pw",
            is_teacher=True,
        )
        self.student_opted_in = User.objects.create_user(
            username="wa_s1",
            email="wa_s1@example.com",
            password="pw",
            is_student=True,
            first_name="Asha",
            phone_number="+919800000011",
            whatsapp_opt_in=True,
        )
        self.student_default_opt_out = User.objects.create_user(
            username="wa_s2",
            email="wa_s2@example.com",
            password="pw",
            is_student=True,
            first_name="Meera",
            phone_number="+919800000012",
        )
        self.student_other_batch = User.objects.create_user(
            username="wa_s3",
            email="wa_s3@example.com",
            password="pw",
            is_student=True,
            first_name="Kiran",
            phone_number="+919800000013",
            whatsapp_opt_in=True,
        )

        self.course = Course.objects.create(
            title="Mohiniyattam Batch",
            course_type=Course.CourseType.LIVE,
            is_published=True,
        )
        self.batch_1 = LiveBatch.objects.create(
            course=self.course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP,
            max_participants=20,
        )
        self.batch_2 = LiveBatch.objects.create(
            course=self.course,
            instructor=self.teacher,
            batch_type=LiveBatch.BatchType.GROUP,
            max_participants=20,
        )

        LiveBatchStudent.objects.create(batch=self.batch_1, student=self.student_opted_in)
        LiveBatchStudent.objects.create(batch=self.batch_1, student=self.student_default_opt_out)
        LiveBatchStudent.objects.create(batch=self.batch_2, student=self.student_other_batch)

        self.start_dt = timezone.now() + timedelta(days=1)
        self.live_class = LiveClass.objects.create(
            course=self.course,
            batch=self.batch_1,
            instructor=self.teacher,
            title="Evening Practice",
            scheduled_start=self.start_dt,
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            status=LiveClass.ClassStatus.SCHEDULED,
            zoom_meeting_id="95551112223",
            meeting_url="https://zoom.us/j/95551112223",
            host_url="https://zoom.us/s/95551112223?zak=secret_host_zak",
        )

    def test_default_opt_in_is_false_and_only_opted_in_batch_students_receive_whatsapp(self):
        self.assertFalse(self.student_default_opt_out.whatsapp_opt_in)

        ok_resp = mock.MagicMock(status_code=201)
        ok_resp.json.return_value = {"id": "interakt_msg_1"}

        with mock.patch("requests.post", return_value=ok_resp) as mock_post:
            LiveClassNotificationService.notify_scheduled(self.live_class)

        # Only student_opted_in in batch_1 should receive WhatsApp
        self.assertEqual(mock_post.call_count, 1)
        sent_payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_payload["fullPhoneNumber"], "919800000011")
        self.assertEqual(sent_payload["type"], "Template")
        self.assertEqual(sent_payload["template"]["name"], "live_class_scheduled_v1")
        self.assertEqual(len(sent_payload["template"]["bodyValues"]), 4)
        self.assertEqual(
            sent_payload["template"]["bodyValues"],
            [
                "Asha",
                self.course.title,
                self.live_class.title,
                self.live_class.scheduled_start.strftime("%b %d, %Y %H:%M"),
            ],
        )
        self.assertEqual(sent_payload["template"]["buttonValues"], {"0": [str(self.live_class.id)]})
        serialized = json.dumps(sent_payload)
        self.assertNotIn("zoom.us", serialized.lower())
        self.assertNotIn("zak=", serialized.lower())

        log = WhatsAppDeliveryLog.objects.get(recipient=self.student_opted_in, event_type="SCHEDULED")
        self.assertEqual(log.status, WhatsAppDeliveryLog.Status.SENT)
        self.assertEqual(log.provider_message_id, "interakt_msg_1")

    def test_reschedule_and_cancel_notifications_and_duplicate_prevention(self):
        ok_resp = mock.MagicMock(status_code=201)
        ok_resp.json.return_value = {"id": "interakt_msg_2"}

        with mock.patch("requests.post", return_value=ok_resp) as mock_post:
            LiveClassNotificationService.notify_scheduled(self.live_class)
            # Duplicate scheduled dispatch must be suppressed by idempotency_key
            LiveClassNotificationService.notify_scheduled(self.live_class)
            self.assertEqual(mock_post.call_count, 1)

            # Reschedule sends new notification
            old_start = self.live_class.scheduled_start
            self.live_class.scheduled_start = old_start + timedelta(hours=2)
            self.live_class.save(update_fields=["scheduled_start"])
            LiveClassNotificationService.notify_rescheduled(self.live_class, old_start)
            self.assertEqual(mock_post.call_count, 2)

            # Cancel sends cancellation notification
            self.live_class.status = LiveClass.ClassStatus.CANCELLED
            self.live_class.save(update_fields=["status"])
            LiveClassNotificationService.notify_cancelled(self.live_class)
            self.assertEqual(mock_post.call_count, 3)

    def test_transient_retry_and_no_premature_whatsapp_when_zoom_not_provisioned(self):
        unprovisioned_lc = LiveClass.objects.create(
            course=self.course,
            batch=self.batch_1,
            instructor=self.teacher,
            title="Unprovisioned Zoom",
            scheduled_start=self.start_dt + timedelta(days=3),
            duration_minutes=60,
            meeting_provider=LiveClass.MeetingProvider.ZOOM,
            status=LiveClass.ClassStatus.SCHEDULED,
            zoom_meeting_id="",
            meeting_url="",
        )
        with mock.patch("requests.post") as mock_post:
            LiveClassNotificationService.notify_scheduled(unprovisioned_lc)
        self.assertEqual(mock_post.call_count, 0)

        # Transient 500 schedules Celery retry (attempts=1, PENDING), then retry succeeds on 201
        from celery.exceptions import Retry
        err_resp = mock.MagicMock(status_code=500)
        ok_resp = mock.MagicMock(status_code=201)
        ok_resp.json.return_value = {"id": "retry_ok"}
        task_kwargs = {
            "recipient_id": self.student_opted_in.id,
            "live_class_id": self.live_class.id,
            "event_type": "SCHEDULED",
            "template_name": "live_class_scheduled_v1",
            "body_values": ["Asha", "Course", "Class", "Time", f"https://academy.natyaarts.com/live-classes/join/{self.live_class.id}"],
            "idempotency_key": "wa:retry:test:1",
        }
        with mock.patch("requests.post", return_value=err_resp):
            with self.assertRaises(Retry):
                send_live_class_whatsapp_task.apply(kwargs=task_kwargs).get()
        retry_log = WhatsAppDeliveryLog.objects.get(idempotency_key="wa:retry:test:1")
        self.assertEqual(retry_log.status, WhatsAppDeliveryLog.Status.PENDING)
        self.assertEqual(retry_log.attempts, 1)

        with mock.patch("requests.post", return_value=ok_resp) as mock_post:
            res = send_live_class_whatsapp_task.apply(kwargs=task_kwargs).get()
        self.assertEqual(res["status"], "SENT")
        self.assertEqual(mock_post.call_count, 1)
        retry_log.refresh_from_db()
        self.assertEqual(retry_log.status, WhatsAppDeliveryLog.Status.SENT)
        self.assertEqual(retry_log.attempts, 2)

        # Forbidden Zoom URL in template is rejected
        with self.assertRaises(InteraktPermanentError):
            InteraktWhatsAppService.send_template(
                phone_number="+919800000011",
                template_name="bad",
                body_values=["https://zoom.us/s/123?zak=abc"],
            )

    def test_enrolled_vs_unenrolled_join_and_no_host_url_exposure(self):
        # Enrolled student can join and never sees host_url
        self.client.force_authenticate(user=self.student_opted_in)
        ok_join = self.client.post(f"/api/courses/live-classes/{self.live_class.id}/join/")
        self.assertEqual(ok_join.status_code, 200)
        self.assertEqual(ok_join.data["meeting_url"], "https://zoom.us/j/95551112223")
        self.assertNotIn("host_url", ok_join.data)

        # Unenrolled student (batch_2 only) gets 403/404
        self.client.force_authenticate(user=self.student_other_batch)
        denied = self.client.post(f"/api/courses/live-classes/{self.live_class.id}/join/")
        self.assertIn(denied.status_code, (403, 404))

    def test_android_app_links_and_assetlinks_configuration(self):
        repo_root = pathlib.Path(settings.BASE_DIR).parent
        assetlinks = json.loads((repo_root / "frontend" / "public" / ".well-known" / "assetlinks.json").read_text(encoding="utf-8"))
        self.assertEqual(assetlinks[0]["target"]["package_name"], "com.natyaarts.academy")
        self.assertIn(
            "86:C7:1F:7F:22:B1:FB:E8:E9:39:0C:33:17:9E:12:94:33:8B:B2:EE:0C:CA:1A:CE:4E:C1:EE:2D:18:BA:41:B5",
            assetlinks[0]["target"]["sha256_cert_fingerprints"],
        )

        app_json = json.loads((repo_root / "mobile" / "app.json").read_text(encoding="utf-8"))
        filters = app_json["expo"]["android"]["intentFilters"]
        self.assertTrue(any(f.get("autoVerify") and f["data"][0]["pathPrefix"] == "/live-classes/join" for f in filters))

        manifest = (repo_root / "mobile" / "android" / "app" / "src" / "main" / "AndroidManifest.xml").read_text(encoding="utf-8")
        self.assertIn('android:autoVerify="true"', manifest)
        self.assertIn('android:pathPrefix="/live-classes/join"', manifest)

