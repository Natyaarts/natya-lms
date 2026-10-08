from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from courses.models import Course
from billing.models import (
    StudentBillingPlan,
    StudentInvoice,
    BillingPlanChangeLog,
    BillingType,
    PlanStatus,
    InvoiceStatus,
    PaymentMethod,
)
from billing.services.lifecycle import create_billing_plan, apply_plan_change
from billing.services.payment import record_manual_payment

User = get_user_model()


class PlanChangeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin_plan_change", email="admin@example.com", password="password123"
        )
        self.student = User.objects.create_user(
            username="student_plan_change", email="student@example.com", password="password123"
        )
        self.course = Course.objects.create(
            title="Kathak Intermediate", price=2000.00, is_published=True
        )
        self.start_date = date(2026, 10, 10)
        self.plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal('2000.00'),
            start_date=self.start_date,
            created_by=self.admin,
            issue_initial_invoice=True
        )

    def test_next_cycle_is_default_and_leaves_current_cycle_untouched(self):
        # Admin schedules a plan change: switch to EVERY_3_MONTHS at ₹5,000
        apply_plan_change(
            plan=self.plan,
            new_billing_type=BillingType.EVERY_3_MONTHS,
            new_amount=Decimal('5000.00'),
            change_mode='NEXT_CYCLE',
            actor=self.admin,
            reason="Student requested quarterly plan starting next quarter"
        )

        self.plan.refresh_from_db()

        # Current period, terms, and dates MUST remain untouched
        self.assertEqual(self.plan.billing_type, BillingType.MONTHLY)
        self.assertEqual(self.plan.amount, Decimal('2000.00'))
        self.assertEqual(self.plan.current_period_start, self.start_date)
        self.assertEqual(self.plan.current_period_end, date(2026, 11, 9))
        self.assertEqual(self.plan.next_billing_date, date(2026, 11, 10))

        # Pending fields are populated
        self.assertEqual(self.plan.pending_billing_type, BillingType.EVERY_3_MONTHS)
        self.assertEqual(self.plan.pending_amount, Decimal('5000.00'))
        self.assertEqual(self.plan.pending_effective_date, date(2026, 11, 10))

        # Current invoice is untouched
        current_inv = StudentInvoice.objects.get(billing_plan=self.plan)
        self.assertEqual(current_inv.amount, Decimal('2000.00'))
        self.assertEqual(current_inv.status, InvoiceStatus.ISSUED)

        # Audit log created
        change_log = BillingPlanChangeLog.objects.get(billing_plan=self.plan)
        self.assertEqual(change_log.change_mode, 'NEXT_CYCLE')
        self.assertEqual(change_log.old_billing_type, BillingType.MONTHLY)
        self.assertEqual(change_log.new_billing_type, BillingType.EVERY_3_MONTHS)
        self.assertEqual(change_log.old_amount, Decimal('2000.00'))
        self.assertEqual(change_log.new_amount, Decimal('5000.00'))
        self.assertEqual(change_log.effective_date, date(2026, 11, 10))
        self.assertEqual(change_log.actor, self.admin)

    def test_pending_plan_change_activates_upon_cycle_advancement(self):
        # 1. Schedule quarterly change
        apply_plan_change(
            plan=self.plan,
            new_billing_type=BillingType.EVERY_3_MONTHS,
            new_amount=Decimal('5000.00'),
            change_mode='NEXT_CYCLE',
            actor=self.admin
        )

        # 2. Student pays current monthly invoice
        current_inv = StudentInvoice.objects.get(billing_plan=self.plan)
        record_manual_payment(
            invoice=current_inv,
            amount=Decimal('2000.00'),
            payment_method=PaymentMethod.CASH,
            actor=self.admin
        )

        # 3. Plan should now be switched to EVERY_3_MONTHS with new 3-month cycle
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.billing_type, BillingType.EVERY_3_MONTHS)
        self.assertEqual(self.plan.amount, Decimal('5000.00'))
        self.assertIsNone(self.plan.pending_billing_type)
        self.assertIsNone(self.plan.pending_amount)

        # Period start: 10 Nov 2026 -> Period end for 3 months: 9 Feb 2027
        self.assertEqual(self.plan.current_period_start, date(2026, 11, 10))
        self.assertEqual(self.plan.current_period_end, date(2027, 2, 9))
        self.assertEqual(self.plan.next_billing_date, date(2027, 2, 10))

    def test_immediate_plan_change_cancels_unpaid_and_issues_new_invoice(self):
        # Admin applies IMMEDIATE plan change
        apply_plan_change(
            plan=self.plan,
            new_billing_type=BillingType.YEARLY,
            new_amount=Decimal('18000.00'),
            change_mode='IMMEDIATE',
            actor=self.admin,
            reason="Converted to yearly discount plan immediately"
        )

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.billing_type, BillingType.YEARLY)
        self.assertEqual(self.plan.amount, Decimal('18000.00'))

        # Old initial invoice cancelled
        old_inv = StudentInvoice.objects.get(amount=Decimal('2000.00'))
        self.assertEqual(old_inv.status, InvoiceStatus.CANCELLED)

        # Fresh yearly invoice issued
        new_inv = StudentInvoice.objects.get(amount=Decimal('18000.00'))
        self.assertEqual(new_inv.status, InvoiceStatus.ISSUED)
        self.assertEqual(new_inv.period_start, date.today())
