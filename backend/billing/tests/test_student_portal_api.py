from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework import status

from courses.models import Course
from billing.models import (
    StudentBillingPlan, StudentInvoice, BillingAccessExtension,
    BillingType, PlanStatus, InvoiceStatus, PaymentMethod
)
from billing.services.lifecycle import create_billing_plan, extend_access

User = get_user_model()


class StudentPortalAPITests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="portal_student",
            email="portal_student@example.com",
            password="testpassword123",
            phone_number="+919876543210"
        )
        self.other_student = User.objects.create_user(
            username="other_student",
            email="other_student@example.com",
            password="testpassword123"
        )
        self.course = Course.objects.create(
            title="Bharatanatyam Fundamentals",
            price=2500,
            is_published=True
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.student)

        # Create billing plan for student
        self.plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2500.00"),
            start_date=date(2026, 11, 1),
            grace_period_days=7,
            issue_initial_invoice=True
        )
        self.invoice = StudentInvoice.objects.get(billing_plan=self.plan)

        # Create plan and invoice for other student
        self.other_plan = create_billing_plan(
            student=self.other_student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2500.00"),
            start_date=date(2026, 11, 1),
            issue_initial_invoice=True
        )
        self.other_invoice = StudentInvoice.objects.get(billing_plan=self.other_plan)

    def test_get_my_billing_summary_and_plans(self):
        response = self.client.get('/api/billing/my-billing/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        self.assertIn("plans", data)
        self.assertIn("alerts", data)
        self.assertEqual(len(data["plans"]), 1)
        self.assertEqual(data["plans"][0]["id"], self.plan.id)
        self.assertEqual(data["alerts"]["unpaid_invoices_count"], 1)
        self.assertEqual(Decimal(data["alerts"]["total_outstanding_amount"]), Decimal("2500.00"))

    def test_student_data_isolation(self):
        # Student cannot see other student's plan in list
        response = self.client.get('/api/billing/my-billing/')
        plan_ids = [p["id"] for p in response.json()["plans"]]
        self.assertIn(self.plan.id, plan_ids)
        self.assertNotIn(self.other_plan.id, plan_ids)

        # Student cannot see other student's invoices
        inv_response = self.client.get('/api/billing/my-billing/invoices/')
        inv_ids = [i["id"] for i in inv_response.json()]
        self.assertIn(self.invoice.id, inv_ids)
        self.assertNotIn(self.other_invoice.id, inv_ids)

    def test_my_invoices_filter_by_status(self):
        response = self.client.get(f'/api/billing/my-billing/invoices/?status={InvoiceStatus.ISSUED}')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.json()), 1)

        paid_response = self.client.get(f'/api/billing/my-billing/invoices/?status={InvoiceStatus.PAID}')
        self.assertEqual(paid_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(paid_response.json()), 0)

    def test_pay_invoice_returns_checkout_payload(self):
        response = self.client.post(f'/api/billing/my-billing/invoices/{self.invoice.id}/pay/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        self.assertIn("razorpay_order_id", data)
        self.assertEqual(data["amount"], "2500.00")
        self.assertEqual(data["amount_in_paise"], 250000)
        self.assertEqual(data["course_title"], self.course.title)
        self.assertEqual(data["invoice_number"], self.invoice.invoice_number)

    def test_cannot_pay_another_students_invoice(self):
        response = self.client.post(f'/api/billing/my-billing/invoices/{self.other_invoice.id}/pay/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_verify_payment_marks_invoice_paid_and_restores_access(self):
        # Set plan to RESTRICTED
        self.plan.status = PlanStatus.RESTRICTED
        self.plan.save(update_fields=['status'])

        payload = {
            "razorpay_order_id": "order_test_123",
            "razorpay_payment_id": "pay_test_456"
        }
        response = self.client.post(f'/api/billing/my-billing/invoices/{self.invoice.id}/verify/', data=payload)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.invoice.refresh_from_db()
        self.plan.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.PAID)
        self.assertEqual(self.plan.status, PlanStatus.ACTIVE)

    def test_get_receipt_contains_academy_metadata(self):
        # Pay invoice first
        self.invoice.status = InvoiceStatus.PAID
        self.invoice.save()

        response = self.client.get(f'/api/billing/my-billing/invoices/{self.invoice.id}/receipt/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.json()

        self.assertIn("academy_details", data)
        self.assertEqual(data["academy_details"]["name"], "Natya Arts Academy")
        self.assertIn("gstin", data["academy_details"])
        self.assertEqual(data["invoice_number"], self.invoice.invoice_number)
        self.assertEqual(data["course_details"]["title"], self.course.title)

    def test_alerts_reflect_grace_and_extensions(self):
        # Set plan into grace period
        self.plan.status = PlanStatus.GRACE_PERIOD
        self.plan.grace_until = date.today() + timedelta(days=3)
        self.plan.save()

        # Add active extension
        extend_access(
            plan=self.plan,
            extended_until=date.today() + timedelta(days=10),
            reason="Special concession by Dean"
        )

        response = self.client.get('/api/billing/my-billing/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        alerts = response.json()["alerts"]

        self.assertTrue(alerts["has_grace_warning"])
        self.assertEqual(len(alerts["grace_courses"]), 1)
        self.assertEqual(alerts["grace_courses"][0]["days_left"], 3)
        self.assertEqual(len(alerts["active_extensions"]), 1)
        self.assertEqual(alerts["active_extensions"][0]["reason"], "Special concession by Dean")

    def test_duplicate_payment_verification_is_idempotent(self):
        """
        Verify that duplicate verify calls return processed: False and do NOT double-advance cycles.
        """
        payload = {
            "razorpay_order_id": "order_dup_123",
            "razorpay_payment_id": "pay_dup_456"
        }
        res1 = self.client.post(f'/api/billing/my-billing/invoices/{self.invoice.id}/verify/', data=payload)
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertTrue(res1.json()["processed"])

        self.plan.refresh_from_db()
        first_period_start = self.plan.current_period_start
        first_period_end = self.plan.current_period_end
        invoices_count = StudentInvoice.objects.filter(billing_plan=self.plan).count()

        # Second identical verify call
        res2 = self.client.post(f'/api/billing/my-billing/invoices/{self.invoice.id}/verify/', data=payload)
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertFalse(res2.json()["processed"])

        self.plan.refresh_from_db()
        # Verify dates did not advance a second time
        self.assertEqual(self.plan.current_period_start, first_period_start)
        self.assertEqual(self.plan.current_period_end, first_period_end)
        # Verify no duplicate invoices were created
        self.assertEqual(StudentInvoice.objects.filter(billing_plan=self.plan).count(), invoices_count)

    def test_webhook_and_verify_idempotency_race(self):
        """
        If webhook marks the invoice PAID first, subsequent client verify calls must return safely
        without errors and without double-advancing the billing cycle.
        """
        from billing.services.payment import process_invoice_payment_success
        paid_inv, processed = process_invoice_payment_success(
            invoice=self.invoice,
            razorpay_payment_id="pay_webhook_race_999"
        )
        self.assertTrue(processed)
        self.assertEqual(paid_inv.status, InvoiceStatus.PAID)

        self.plan.refresh_from_db()
        period_start_after_webhook = self.plan.current_period_start

        # Client verify fires right after webhook
        payload = {
            "razorpay_order_id": "order_race_123",
            "razorpay_payment_id": "pay_webhook_race_999"
        }
        res = self.client.post(f'/api/billing/my-billing/invoices/{self.invoice.id}/verify/', data=payload)
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertFalse(res.json()["processed"])

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.current_period_start, period_start_after_webhook)

    def test_cross_student_receipt_access_returns_404(self):
        """
        Student must never be able to access another student's invoice receipt.
        """
        response = self.client.get(f'/api/billing/my-billing/invoices/{self.other_invoice.id}/receipt/')
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_cross_student_verify_returns_404(self):
        """
        Student must never be able to trigger payment verification on another student's invoice.
        """
        payload = {
            "razorpay_order_id": "order_hack_123",
            "razorpay_payment_id": "pay_hack_456"
        }
        response = self.client.post(f'/api/billing/my-billing/invoices/{self.other_invoice.id}/verify/', data=payload)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_grace_status_filter_computed(self):
        """
        Verify that ?status=GRACE accurately filters invoices whose associated plan is in grace.
        """
        # Initially plan is ACTIVE -> ?status=GRACE returns 0
        res1 = self.client.get('/api/billing/my-billing/invoices/?status=GRACE')
        self.assertEqual(res1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res1.json()), 0)

        # Move plan to GRACE_PERIOD
        self.plan.status = PlanStatus.GRACE_PERIOD
        self.plan.grace_until = date.today() + timedelta(days=5)
        self.plan.save(update_fields=['status', 'grace_until'])

        res2 = self.client.get('/api/billing/my-billing/invoices/?status=GRACE')
        self.assertEqual(res2.status_code, status.HTTP_200_OK)
        self.assertEqual(len(res2.json()), 1)
        self.assertEqual(res2.json()[0]["id"], self.invoice.id)

    def test_never_cache_headers_on_my_billing(self):
        """
        Ensure user-specific billing endpoints explicitly forbid browser/proxy caching.
        """
        res = self.client.get('/api/billing/my-billing/')
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        cache_control = res.headers.get('Cache-Control', '')
        self.assertIn('no-cache', cache_control)
        self.assertIn('no-store', cache_control)
