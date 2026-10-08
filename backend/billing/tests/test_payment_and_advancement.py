from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import TestCase
from courses.models import Course
from billing.models import (
    StudentBillingPlan,
    StudentInvoice,
    ManualPaymentRecord,
    BillingType,
    PlanStatus,
    InvoiceStatus,
    PaymentMethod,
)
from billing.services.lifecycle import create_billing_plan
from billing.services.payment import record_manual_payment, process_invoice_payment_success

User = get_user_model()


class PaymentAndAdvancementTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="admin_billing", email="admin@example.com", password="password123"
        )
        self.student = User.objects.create_user(
            username="student_payment", email="student@example.com", password="password123"
        )
        self.course = Course.objects.create(
            title="Carnatic Vocal Level 1", price=2000.00, is_published=True
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
        self.invoice = StudentInvoice.objects.get(billing_plan=self.plan)

    def test_manual_payment_rejects_insufficient_amount(self):
        # Outstanding is 2000, attempting to pay 1500 must fail
        with self.assertRaises(ValidationError) as ctx:
            record_manual_payment(
                invoice=self.invoice,
                amount=Decimal('1500.00'),
                payment_method=PaymentMethod.CASH,
                actor=self.admin
            )
        self.assertIn("Full payment is required", str(ctx.exception))
        self.invoice.refresh_from_db()
        self.assertEqual(self.invoice.status, InvoiceStatus.ISSUED)

    def test_manual_payment_records_transaction_and_advances_cycle(self):
        # Set plan status to RESTRICTED before payment to test status restoration
        self.plan.status = PlanStatus.RESTRICTED
        self.plan.save()

        paid_invoice, processed = record_manual_payment(
            invoice=self.invoice,
            amount=Decimal('2000.00'),
            payment_method=PaymentMethod.UPI,
            reference_number="UPI/1234567890",
            actor=self.admin,
            notes="Received via Google Pay"
        )

        self.assertTrue(processed)
        self.assertEqual(paid_invoice.status, InvoiceStatus.PAID)
        self.assertEqual(paid_invoice.payment_method, PaymentMethod.UPI)
        self.assertIsNotNone(paid_invoice.paid_at)

        # Audit manual payment record created
        payment_record = ManualPaymentRecord.objects.get(invoice=self.invoice)
        self.assertEqual(payment_record.amount, Decimal('2000.00'))
        self.assertEqual(payment_record.payment_method, PaymentMethod.UPI)
        self.assertEqual(payment_record.reference_number, "UPI/1234567890")
        self.assertEqual(payment_record.recorded_by, self.admin)

        # Verify plan advanced to next cycle (10 Nov 2026 -> 9 Dec 2026)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.current_period_start, date(2026, 11, 10))
        self.assertEqual(self.plan.current_period_end, date(2026, 12, 9))
        self.assertEqual(self.plan.next_billing_date, date(2026, 12, 10))
        self.assertEqual(self.plan.due_date, date(2026, 11, 10))
        self.assertEqual(self.plan.grace_until, date(2026, 11, 17))
        self.assertEqual(self.plan.access_restriction_date, date(2026, 11, 18))
        self.assertEqual(self.plan.status, PlanStatus.ACTIVE)

    def test_payment_recording_idempotency(self):
        # First payment succeeds
        record_manual_payment(
            invoice=self.invoice,
            amount=Decimal('2000.00'),
            payment_method=PaymentMethod.CASH,
            actor=self.admin
        )
        self.plan.refresh_from_db()
        period_start_after_first = self.plan.current_period_start

        # Second payment on same invoice returns processed=False without advancing again
        inv_again, processed = record_manual_payment(
            invoice=self.invoice,
            amount=Decimal('2000.00'),
            payment_method=PaymentMethod.CASH,
            actor=self.admin
        )
        self.assertFalse(processed)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.current_period_start, period_start_after_first)

    def test_razorpay_payment_success_handler(self):
        paid_inv, processed = process_invoice_payment_success(
            invoice=self.invoice,
            razorpay_payment_id="pay_K98hY5L1m2N3",
            payment_method=PaymentMethod.RAZORPAY
        )
        self.assertTrue(processed)
        self.assertEqual(paid_inv.status, InvoiceStatus.PAID)
        self.assertEqual(paid_inv.razorpay_payment_id, "pay_K98hY5L1m2N3")
        self.assertEqual(paid_inv.payment_method, PaymentMethod.RAZORPAY)

        # Plan cycle advanced
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.current_period_start, date(2026, 11, 10))
        self.assertEqual(self.plan.status, PlanStatus.ACTIVE)

    def test_one_time_payment_does_not_advance_cycle(self):
        onetime_plan = create_billing_plan(
            student=self.student,
            course=Course.objects.create(title="Workshop", price=500.00, is_published=True),
            billing_type=BillingType.ONE_TIME,
            amount=Decimal('500.00'),
            start_date=self.start_date,
            issue_initial_invoice=True
        )
        onetime_inv = StudentInvoice.objects.get(billing_plan=onetime_plan)

        record_manual_payment(
            invoice=onetime_inv,
            amount=Decimal('500.00'),
            payment_method=PaymentMethod.CASH
        )

        onetime_plan.refresh_from_db()
        # Dates should not advance
        self.assertEqual(onetime_plan.current_period_start, self.start_date)
        self.assertIsNone(onetime_plan.next_billing_date)
        self.assertEqual(onetime_plan.status, PlanStatus.ACTIVE)
