from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework import status
from unittest.mock import patch
from courses.models import Course, Enrollment
from orders.models import Purchase
from notifications.models import Notification

User = get_user_model()

class PaymentNotificationTests(APITestCase):
    def setUp(self):
        # Create users
        self.student = User.objects.create_user(username="student_pay_test", password="password123")
        self.student.is_student = True
        self.student.save()

        self.staff_user = User.objects.create_user(username="staff_pay_test", password="password123")
        self.staff_user.is_superuser = True
        self.staff_user.save()

        # Create course
        self.course = Course.objects.create(
            title="Sitar Masterclass",
            description="Learn Sitar",
            price=1500.00,
            is_published=True
        )

        # Create a pending purchase
        self.purchase = Purchase.objects.create(
            user=self.student,
            course=self.course,
            amount=1500.00,
            status="PENDING",
            razorpay_order_id="order_dummy_123"
        )

    @patch('orders.views.client')
    def test_verify_payment_success_triggers_notifications(self, mock_client):
        self.client.force_authenticate(user=self.student)
        mock_client.utility.verify_payment_signature.return_value = True

        url = reverse('verify-payment')
        payload = {
            "razorpay_payment_id": "pay_dummy_123",
            "razorpay_order_id": "order_dummy_123",
            "razorpay_signature": "sig_dummy_123"
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "SUCCESS")

        # Payment Notification should exist
        pay_notif = Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").first()
        self.assertIsNotNone(pay_notif)
        self.assertEqual(pay_notif.title, "Payment successful")

        # Enrollment Notification should exist
        enroll_notif = Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").first()
        self.assertIsNotNone(enroll_notif)

    @patch('orders.views.client')
    def test_verify_payment_failure_creates_no_notifications(self, mock_client):
        self.client.force_authenticate(user=self.student)

        import razorpay.errors
        mock_client.utility.verify_payment_signature.side_effect = razorpay.errors.SignatureVerificationError("Invalid Signature")

        url = reverse('verify-payment')
        payload = {
            "razorpay_payment_id": "pay_dummy_123",
            "razorpay_order_id": "order_dummy_123",
            "razorpay_signature": "sig_dummy_invalid"
        }

        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "FAILED")
        self.assertEqual(Notification.objects.filter(recipient=self.student).count(), 0)

    def test_admin_mark_paid_triggers_notifications(self):
        self.client.force_authenticate(user=self.staff_user)

        url = reverse('purchases-admin-mark-paid', kwargs={'pk': self.purchase.pk})
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "SUCCESS")

        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").count(), 1)

    def test_user_mark_purchase_paid_triggers_notification(self):
        self.client.force_authenticate(user=self.staff_user)

        url = reverse('admin-user-mark-purchase-paid', kwargs={'pk': self.student.pk})
        payload = {
            "purchase_id": self.purchase.id
        }
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, payload)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.purchase.refresh_from_db()
        self.assertEqual(self.purchase.status, "SUCCESS")
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)

    def test_admin_mark_paid_repeated_calls_do_not_duplicate_notifications(self):
        self.client.force_authenticate(user=self.staff_user)

        url = reverse('purchases-admin-mark-paid', kwargs={'pk': self.purchase.pk})

        with self.captureOnCommitCallbacks(execute=True):
            response1 = self.client.post(url)
        self.assertEqual(response1.status_code, status.HTTP_200_OK)

        with self.captureOnCommitCallbacks(execute=True):
            response2 = self.client.post(url)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)

        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="PAYMENT").count(), 1)
        self.assertEqual(Notification.objects.filter(recipient=self.student, notification_type="ENROLLMENT").count(), 1)
