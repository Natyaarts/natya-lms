from datetime import date, timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase
from courses.models import Course, Enrollment
from billing.models import (
    StudentBillingPlan,
    StudentInvoice,
    BillingType,
    PlanStatus,
    InvoiceStatus,
)
from billing.services.lifecycle import (
    create_billing_plan,
    generate_cycle_invoice,
    update_plan_status_based_on_dates,
)

User = get_user_model()


class LifecycleAndInvoicingTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="student_lifecycle", email="student@example.com", password="password123"
        )
        self.course = Course.objects.create(
            title="Bharatanatyam Foundations", price=1500.00, is_published=True
        )
        self.enrollment = Enrollment.objects.create(
            user=self.student, course=self.course
        )

    def test_create_billing_plan_derives_all_dates_and_initial_invoice(self):
        start = date(2026, 10, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            enrollment=self.enrollment,
            billing_type=BillingType.MONTHLY,
            amount=Decimal('2000.00'),
            start_date=start,
            advance_invoice_days=7,
            grace_period_days=7,
            issue_initial_invoice=True
        )

        self.assertEqual(plan.start_date, start)
        self.assertEqual(plan.current_period_start, start)
        self.assertEqual(plan.current_period_end, date(2026, 11, 9))
        self.assertEqual(plan.next_billing_date, date(2026, 11, 10))
        self.assertEqual(plan.due_date, start)
        self.assertEqual(plan.grace_until, date(2026, 10, 17))
        self.assertEqual(plan.access_restriction_date, date(2026, 10, 18))
        self.assertEqual(plan.status, PlanStatus.ACTIVE)

        # Initial invoice verification
        invoices = StudentInvoice.objects.filter(billing_plan=plan)
        self.assertEqual(invoices.count(), 1)
        invoice = invoices.first()
        self.assertEqual(invoice.period_start, start)
        self.assertEqual(invoice.period_end, date(2026, 11, 9))
        self.assertEqual(invoice.due_date, start)
        self.assertEqual(invoice.amount, Decimal('2000.00'))
        self.assertEqual(invoice.status, InvoiceStatus.ISSUED)
        self.assertTrue(invoice.invoice_number.startswith("INV-NATYA-"))

    def test_generate_cycle_invoice_idempotency(self):
        start = date(2026, 10, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal('2000.00'),
            start_date=start,
            issue_initial_invoice=True
        )

        # Calling generate_cycle_invoice again for the same period should return existing invoice
        invoice, created = generate_cycle_invoice(
            plan=plan,
            period_start=plan.current_period_start,
            period_end=plan.current_period_end,
            due_date=plan.due_date,
            amount=plan.amount
        )
        self.assertFalse(created)
        self.assertEqual(StudentInvoice.objects.filter(billing_plan=plan).count(), 1)

    def test_database_constraint_prevents_duplicate_active_invoices(self):
        start = date(2026, 10, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal('2000.00'),
            start_date=start,
            issue_initial_invoice=True
        )

        # Attempting direct ORM creation with the exact same period should fail DB UniqueConstraint
        with self.assertRaises(IntegrityError):
            StudentInvoice.objects.create(
                billing_plan=plan,
                student=self.student,
                course=self.course,
                period_start=plan.current_period_start,
                period_end=plan.current_period_end,
                issue_date=start,
                due_date=plan.due_date,
                amount=plan.amount,
                status=InvoiceStatus.ISSUED
            )

    def test_status_transitions_based_on_dates(self):
        start = date(2026, 10, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal('2000.00'),
            start_date=start,
            grace_period_days=7,
            issue_initial_invoice=True
        )
        # due_date is 2026-10-10, grace_until is 2026-10-17, access_restriction_date is 2026-10-18

        # 1. Before due date -> ACTIVE
        status_before = update_plan_status_based_on_dates(plan, as_of_date=date(2026, 10, 9))
        self.assertEqual(status_before, PlanStatus.ACTIVE)

        # 2. On due date -> DUE
        status_on_due = update_plan_status_based_on_dates(plan, as_of_date=date(2026, 10, 10))
        self.assertEqual(status_on_due, PlanStatus.DUE)

        # 3. During grace period -> GRACE_PERIOD
        status_in_grace = update_plan_status_based_on_dates(plan, as_of_date=date(2026, 10, 15))
        self.assertEqual(status_in_grace, PlanStatus.GRACE_PERIOD)

        # 4. On or after access_restriction_date -> RESTRICTED
        status_restricted = update_plan_status_based_on_dates(plan, as_of_date=date(2026, 10, 18))
        self.assertEqual(status_restricted, PlanStatus.RESTRICTED)

        # If auto_restrict_access is False, status should be OVERDUE instead
        plan.auto_restrict_access = False
        plan.save()
        status_overdue = update_plan_status_based_on_dates(plan, as_of_date=date(2026, 10, 18))
        self.assertEqual(status_overdue, PlanStatus.OVERDUE)
