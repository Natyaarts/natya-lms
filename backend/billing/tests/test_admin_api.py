from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from courses.models import Course, Enrollment
from billing.models import (
    StudentBillingPlan,
    StudentInvoice,
    ManualPaymentRecord,
    BillingAccessExtension,
    BillingPlanChangeLog,
    BillingType,
    PlanStatus,
    InvoiceStatus,
    PaymentMethod,
)
from billing.services.lifecycle import create_billing_plan

User = get_user_model()


class BillingAdminAPITests(APITestCase):
    def setUp(self):
        # Users
        self.superadmin = User.objects.create_superuser(
            username="billing_admin", email="admin@example.com", password="adminpassword"
        )
        self.staff_admin = User.objects.create_user(
            username="staff_admin", email="staff@example.com", password="staffpassword", is_staff=True
        )
        self.student = User.objects.create_user(
            username="student1", email="student1@example.com", password="password123"
        )
        self.student2 = User.objects.create_user(
            username="student2", email="student2@example.com", password="password123"
        )

        # Course
        self.course = Course.objects.create(
            title="Bharatanatyam Masterclass", price=2500.00, is_published=True
        )
        self.enrollment = Enrollment.objects.create(
            user=self.student, course=self.course
        )

        self.start_date = date(2026, 10, 10)
        self.plan = create_billing_plan(
            student=self.student,
            course=self.course,
            enrollment=self.enrollment,
            billing_type=BillingType.MONTHLY,
            amount=Decimal('2500.00'),
            start_date=self.start_date,
            created_by=self.superadmin,
            issue_initial_invoice=True
        )
        self.invoice = StudentInvoice.objects.get(billing_plan=self.plan)

    def test_unauthorized_user_blocked(self):
        url = reverse('billing-plan-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_student_cannot_access_admin_billing_api(self):
        self.client.force_authenticate(user=self.student)
        url = reverse('billing-plan-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Also blocked from invoices list
        inv_url = reverse('billing-invoice-list')
        response = self.client.get(inv_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        # Also blocked from manual payments
        mp_url = reverse('billing-manual-payment-list')
        response = self.client.get(mp_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_admin_can_access_billing_plans(self):
        self.client.force_authenticate(user=self.staff_admin)
        url = reverse('billing-plan-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 1)

    def test_admin_can_create_billing_plan_with_calculated_dates(self):
        self.client.force_authenticate(user=self.superadmin)
        course2 = Course.objects.create(title="Carnatic Music", price=1800.00, is_published=True)

        url = reverse('billing-plan-list')
        payload = {
            "student": self.student.id,
            "course": course2.id,
            "billing_type": BillingType.EVERY_3_MONTHS,
            "amount": "5000.00",
            "start_date": "2026-10-15",
            "advance_invoice_days": 7,
            "grace_period_days": 7,
            "issue_initial_invoice": True
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['billing_type'], BillingType.EVERY_3_MONTHS)
        self.assertEqual(response.data['current_period_start'], "2026-10-15")
        # 3 months: 15 Oct -> 14 Jan
        self.assertEqual(response.data['current_period_end'], "2027-01-14")
        self.assertEqual(response.data['next_billing_date'], "2027-01-15")
        self.assertEqual(response.data['grace_until'], "2026-10-22")
        self.assertEqual(response.data['access_restriction_date'], "2026-10-23")

    def test_admin_can_update_billing_plan(self):
        self.client.force_authenticate(user=self.superadmin)
        url = reverse('billing-plan-detail', kwargs={'pk': self.plan.id})
        response = self.client.patch(url, {"notes": "Updated admin notes", "auto_restrict_access": False})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.notes, "Updated admin notes")
        self.assertFalse(self.plan.auto_restrict_access)

    def test_invoice_listing_and_filtering(self):
        self.client.force_authenticate(user=self.superadmin)
        url = reverse('billing-invoice-list')
        response = self.client.get(url, {'status': InvoiceStatus.ISSUED})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(all(inv['status'] == InvoiceStatus.ISSUED for inv in response.data))

        # Filter by student
        response = self.client.get(url, {'student': self.student.id})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(all(inv['student'] == self.student.id for inv in response.data))

    def test_invoice_detail(self):
        self.client.force_authenticate(user=self.superadmin)
        url = reverse('billing-invoice-detail', kwargs={'pk': self.invoice.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['invoice_number'], self.invoice.invoice_number)
        self.assertEqual(Decimal(response.data['amount']), Decimal('2500.00'))

    def test_manual_payment_rejects_insufficient_amount(self):
        self.client.force_authenticate(user=self.superadmin)
        url = reverse('billing-manual-payment-list')
        payload = {
            "invoice": self.invoice.id,
            "amount": "2000.00",  # Outstanding is 2500
            "payment_method": PaymentMethod.CASH,
            "reference_number": "REF-001"
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("amount", response.data)
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.ISSUED)

    def test_manual_payment_success_advances_billing_cycle(self):
        self.client.force_authenticate(user=self.superadmin)
        url = reverse('billing-manual-payment-list')
        payload = {
            "invoice": self.invoice.id,
            "amount": "2500.00",
            "payment_method": PaymentMethod.UPI,
            "reference_number": "UPI-SUCCESS-999",
            "notes": "Paid by parent via UPI"
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

        # Invoice is PAID
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.PAID)
        self.assertEqual(self.invoice.payment_method, PaymentMethod.UPI)

        # Plan advanced to next cycle (10 Nov 2026 -> 9 Dec 2026)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.current_period_start, date(2026, 11, 10))
        self.assertEqual(self.plan.current_period_end, date(2026, 12, 9))
        self.assertEqual(self.plan.next_billing_date, date(2026, 12, 10))
        self.assertEqual(self.plan.status, PlanStatus.ACTIVE)

    def test_duplicate_manual_payment_rejected(self):
        self.client.force_authenticate(user=self.superadmin)
        # 1. Pay once
        url = reverse('billing-manual-payment-list')
        self.client.post(url, {
            "invoice": self.invoice.id,
            "amount": "2500.00",
            "payment_method": PaymentMethod.CASH
        }, format='json')

        # 2. Try paying again
        response = self.client.post(url, {
            "invoice": self.invoice.id,
            "amount": "2500.00",
            "payment_method": PaymentMethod.CASH
        }, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already paid", str(response.data))

    def test_plan_change_next_cycle_schedules_terms(self):
        self.client.force_authenticate(user=self.superadmin)
        url = reverse('billing-plan-change-plan', kwargs={'pk': self.plan.id})
        payload = {
            "new_billing_type": BillingType.EVERY_3_MONTHS,
            "new_amount": "6000.00",
            "change_mode": "NEXT_CYCLE",
            "reason": "Student requested quarterly plan from next cycle"
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.plan.refresh_from_db()
        # Current terms untouched
        self.assertEqual(self.plan.billing_type, BillingType.MONTHLY)
        self.assertEqual(self.plan.amount, Decimal('2500.00'))
        # Pending terms populated
        self.assertEqual(self.plan.pending_billing_type, BillingType.EVERY_3_MONTHS)
        self.assertEqual(self.plan.pending_amount, Decimal('6000.00'))
        self.assertEqual(self.plan.pending_effective_date, date(2026, 11, 10))

        # Current invoice untouched
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.amount, Decimal('2500.00'))
        self.assertEqual(self.invoice.status, InvoiceStatus.ISSUED)

    def test_extend_access_action(self):
        self.client.force_authenticate(user=self.superadmin)
        url = reverse('billing-plan-extend-access-action', kwargs={'pk': self.plan.id})
        future_date = (date.today() + timedelta(days=14)).isoformat()
        payload = {
            "extended_until": future_date,
            "reason": "Approved medical emergency grace extension"
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(BillingAccessExtension.objects.filter(billing_plan=self.plan, is_active=True).exists())

    def test_student_self_service_cannot_see_other_students_data(self):
        # Authenticated student1 accessing /api/billing/my-billing/
        self.client.force_authenticate(user=self.student)
        url = reverse('billing-my-billing-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Sees own plan
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['student'], self.student.id)

        # Authenticated student2 accessing /api/billing/my-billing/
        self.client.force_authenticate(user=self.student2)
        response2 = self.client.get(url)
        self.assertEqual(response2.status_code, status.HTTP_200_OK)
        # Cannot see student1's plan
        self.assertEqual(len(response2.data), 0)

    def test_dashboard_summary_metrics(self):
        self.client.force_authenticate(user=self.superadmin)
        url = reverse('billing-plan-dashboard-summary')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        metrics = response.data['metrics']
        self.assertIn('active_plans', metrics)
        self.assertIn('due_payments', metrics)
        self.assertIn('grace_period', metrics)
        self.assertIn('overdue_restricted', metrics)
        self.assertIn('paid_this_month', metrics)
        self.assertIn('outstanding_amount', metrics)
        self.assertIn('recent_invoices', response.data)
        self.assertIn('recent_manual_payments', response.data)

    @patch('billing.views.get_razorpay_client')
    def test_generate_payment_link_calls_razorpay(self, mock_client_getter):
        mock_client = MagicMock()
        mock_client.order.create.return_value = {"id": "order_test_98765"}
        mock_client.payment_link.create.return_value = {"id": "plink_123", "short_url": "https://rzp.io/i/test123"}
        mock_client_getter.return_value = mock_client

        self.client.force_authenticate(user=self.superadmin)
        url = reverse('billing-invoice-generate-payment-link', kwargs={'pk': self.invoice.id})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['razorpay_order_id'], "order_test_98765")
        self.assertEqual(response.data['payment_url'], "https://rzp.io/i/test123")

        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.razorpay_order_id, "order_test_98765")
