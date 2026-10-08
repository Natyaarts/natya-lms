from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch, MagicMock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from courses.models import Course
from billing.models import (
    StudentBillingPlan, StudentInvoice, BillingPlanChangeLog,
    BillingAccessExtension, BillingNotificationLog,
    BillingType, PlanStatus, InvoiceStatus, NotificationEventType,
    PaymentMethod
)
from billing.services.lifecycle import (
    create_billing_plan, generate_cycle_invoice,
    update_plan_status_based_on_dates, apply_plan_change,
    extend_access, generate_upcoming_invoices,
    update_all_overdue_statuses, activate_scheduled_plan_changes
)
from billing.services.notification import (
    send_billing_notification, send_scheduled_payment_reminders
)
from billing.services.payment import (
    record_manual_payment, process_invoice_payment_success
)
from billing.services.access import student_has_billing_access
from billing.tasks import (
    billing_generate_upcoming_invoices,
    billing_update_overdue_statuses,
    billing_apply_scheduled_plan_changes,
    billing_send_payment_reminders,
    billing_daily_automation
)

User = get_user_model()


class BillingAutomationPhase4Tests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(
            username="student_auto",
            email="student_auto@example.com",
            phone_number="+919876543210"
        )
        self.student2 = User.objects.create_user(
            username="student_auto2",
            email="student_auto2@example.com",
            phone_number="+919876543211"
        )
        self.course = Course.objects.create(
            title="Bharatanatyam Masterclass",
            price=2000,
            is_published=True
        )

    # 1. Invoice generated exactly on advance_invoice_days date
    def test_invoice_generated_exactly_on_advance_invoice_days_date(self):
        # Start date: 10 Nov 2026, next billing date: 10 Dec 2026, advance_invoice_days: 7
        # Issue date: 10 Dec - 7 days = 3 Dec 2026
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            advance_invoice_days=7,
            issue_initial_invoice=True
        )
        # Advance invoice threshold is 2026-12-03
        result = generate_upcoming_invoices(as_of_date=date(2026, 12, 3))
        self.assertEqual(result["generated"], 1)

        # Verify upcoming invoice exists for next period (10 Dec to 9 Jan)
        upcoming_inv = StudentInvoice.objects.filter(
            billing_plan=plan,
            period_start=date(2026, 12, 10)
        ).first()
        self.assertIsNotNone(upcoming_inv)
        self.assertEqual(upcoming_inv.amount, Decimal("2000.00"))
        self.assertEqual(upcoming_inv.status, InvoiceStatus.ISSUED)

    # 2. Invoice not generated before issue date
    def test_invoice_not_generated_before_issue_date(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            advance_invoice_days=7,
            issue_initial_invoice=True
        )
        # One day before threshold: 2 Dec 2026
        result = generate_upcoming_invoices(as_of_date=date(2026, 12, 2))
        self.assertEqual(result["generated"], 0)

        upcoming_inv = StudentInvoice.objects.filter(
            billing_plan=plan,
            period_start=date(2026, 12, 10)
        ).first()
        self.assertIsNone(upcoming_inv)

    # 3. Invoice not generated twice
    def test_invoice_not_generated_twice(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            advance_invoice_days=7,
            issue_initial_invoice=True
        )
        # First execution on 3 Dec generates invoice
        res1 = generate_upcoming_invoices(as_of_date=date(2026, 12, 3))
        self.assertEqual(res1["generated"], 1)

        # Second execution on 4 Dec must not duplicate
        res2 = generate_upcoming_invoices(as_of_date=date(2026, 12, 4))
        self.assertEqual(res2["generated"], 0)
        self.assertEqual(res2["existing"], 1)

        count = StudentInvoice.objects.filter(
            billing_plan=plan,
            period_start=date(2026, 12, 10)
        ).count()
        self.assertEqual(count, 1)

    # 4. Concurrent invoice generation safe
    def test_concurrent_invoice_generation_safe(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date
        )
        inv1, created1 = generate_cycle_invoice(
            plan=plan,
            period_start=date(2026, 12, 10),
            period_end=date(2027, 1, 9),
            due_date=date(2026, 12, 10),
            issue_date=date(2026, 12, 3)
        )
        inv2, created2 = generate_cycle_invoice(
            plan=plan,
            period_start=date(2026, 12, 10),
            period_end=date(2027, 1, 9),
            due_date=date(2026, 12, 10),
            issue_date=date(2026, 12, 3)
        )
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(inv1.id, inv2.id)

    # 5. Due status transition
    def test_due_status_transition(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            issue_initial_invoice=True
        )
        # Check on due date (10 Nov)
        update_all_overdue_statuses(as_of_date=date(2026, 11, 10))
        plan.refresh_from_db()
        self.assertEqual(plan.status, PlanStatus.DUE)

    # 6. Grace period transition
    def test_grace_period_transition(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            grace_period_days=7,
            issue_initial_invoice=True
        )
        # Grace runs from 11 Nov to 17 Nov
        update_all_overdue_statuses(as_of_date=date(2026, 11, 12))
        plan.refresh_from_db()
        self.assertEqual(plan.status, PlanStatus.GRACE_PERIOD)

        # Student retains access during grace period
        self.assertTrue(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 11, 12)))

    # 7. Restriction after grace
    def test_restriction_after_grace(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            grace_period_days=7,
            auto_restrict_access=True,
            issue_initial_invoice=True
        )
        # Access restriction date is 18 Nov
        update_all_overdue_statuses(as_of_date=date(2026, 11, 18))
        plan.refresh_from_db()
        self.assertEqual(plan.status, PlanStatus.RESTRICTED)

        # Content access is restricted
        self.assertFalse(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 11, 18)))

    # 8. Active access extension prevents restriction
    def test_active_access_extension_prevents_restriction(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            grace_period_days=7,
            auto_restrict_access=True,
            issue_initial_invoice=True
        )
        # Past restriction date: 20 Nov
        update_all_overdue_statuses(as_of_date=date(2026, 11, 20))
        plan.refresh_from_db()

        # Grant access extension until 25 Nov
        extend_access(
            plan=plan,
            extended_until=date(2026, 11, 25),
            reason="Special permission from faculty head"
        )

        # Content access MUST remain available
        self.assertTrue(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 11, 22)))

    # 9. Access extension expiry restores normal restriction logic
    def test_access_extension_expiry_restores_normal_restriction_logic(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            grace_period_days=7,
            auto_restrict_access=True,
            issue_initial_invoice=True
        )
        extend_access(
            plan=plan,
            extended_until=date(2026, 11, 25),
            reason="Temporary grace"
        )
        # On 26 Nov (extension expired), normal restriction resumes
        self.assertFalse(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 11, 26)))

    # 10. Upcoming payment reminder
    def test_upcoming_payment_reminder(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            advance_invoice_days=7,
            auto_send_reminders=True,
            issue_initial_invoice=False
        )
        # Generate upcoming invoice on 3 Nov for due date 10 Nov
        inv, _ = generate_cycle_invoice(
            plan=plan,
            period_start=date(2026, 11, 10),
            period_end=date(2026, 12, 9),
            due_date=date(2026, 11, 10),
            issue_date=date(2026, 11, 3),
            status=InvoiceStatus.ISSUED
        )
        stats = send_scheduled_payment_reminders(as_of_date=date(2026, 11, 5))
        self.assertEqual(stats["sent"], 1)

        log = BillingNotificationLog.objects.filter(
            billing_plan=plan,
            notification_type=NotificationEventType.UPCOMING
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.status, 'SENT')

    # 11. Due reminder
    def test_due_reminder(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            auto_send_reminders=True,
            issue_initial_invoice=True
        )
        stats = send_scheduled_payment_reminders(as_of_date=date(2026, 11, 10))
        self.assertEqual(stats["sent"], 1)

        log = BillingNotificationLog.objects.filter(
            billing_plan=plan,
            notification_type=NotificationEventType.DUE_TODAY
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.scheduled_date, date(2026, 11, 10))

    # 12. Grace reminder
    def test_grace_reminder(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            grace_period_days=7,
            auto_send_reminders=True,
            issue_initial_invoice=True
        )
        # In grace: 12 Nov
        stats = send_scheduled_payment_reminders(as_of_date=date(2026, 11, 12))
        self.assertEqual(stats["sent"], 1)

        log = BillingNotificationLog.objects.filter(
            billing_plan=plan,
            notification_type=NotificationEventType.GRACE_STARTED
        ).first()
        self.assertIsNotNone(log)

    # 13. Final grace reminder
    def test_final_grace_reminder(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            grace_period_days=7,
            auto_send_reminders=True,
            issue_initial_invoice=True
        )
        # plan.grace_until is 17 Nov (day before 18 Nov restriction)
        stats = send_scheduled_payment_reminders(as_of_date=date(2026, 11, 17))
        self.assertEqual(stats["sent"], 1)

        log = BillingNotificationLog.objects.filter(
            billing_plan=plan,
            notification_type=NotificationEventType.GRACE_FINAL_WARNING
        ).first()
        self.assertIsNotNone(log)

    # 14. Restriction notification
    def test_restriction_notification(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            grace_period_days=7,
            auto_restrict_access=True,
            auto_send_reminders=True,
            issue_initial_invoice=True
        )
        # On restriction date: 18 Nov
        stats = send_scheduled_payment_reminders(as_of_date=date(2026, 11, 18))
        self.assertEqual(stats["sent"], 1)

        log = BillingNotificationLog.objects.filter(
            billing_plan=plan,
            notification_type=NotificationEventType.ACCESS_RESTRICTED
        ).first()
        self.assertIsNotNone(log)

    # 15. Duplicate reminder prevention
    def test_duplicate_reminder_prevention(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            auto_send_reminders=True,
            issue_initial_invoice=True
        )
        # First send on due date
        stats1 = send_scheduled_payment_reminders(as_of_date=date(2026, 11, 10))
        self.assertEqual(stats1["sent"], 1)

        # Second send on same due date must skip
        stats2 = send_scheduled_payment_reminders(as_of_date=date(2026, 11, 10))
        self.assertEqual(stats2["sent"], 0)
        self.assertEqual(stats2["skipped"], 1)

        # Count in database is exactly 1
        count = BillingNotificationLog.objects.filter(
            billing_plan=plan,
            notification_type=NotificationEventType.DUE_TODAY
        ).count()
        self.assertEqual(count, 1)

    # 16. Notification failure does not alter billing state
    @patch('notifications.services.NotificationService.create_notification', side_effect=Exception("Delivery connection timeout"))
    def test_notification_failure_does_not_alter_billing_state(self, mock_notify):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            auto_send_reminders=True,
            issue_initial_invoice=True
        )
        inv = StudentInvoice.objects.get(billing_plan=plan)

        # Run reminders sweep with failing notification delivery
        stats = send_scheduled_payment_reminders(as_of_date=date(2026, 11, 10))
        # Invoice status and plan state must remain intact
        inv.refresh_from_db()
        plan.refresh_from_db()
        self.assertEqual(inv.status, InvoiceStatus.ISSUED)
        self.assertEqual(plan.status, PlanStatus.ACTIVE)

    # 17. Payment success restores access
    def test_payment_success_restores_access(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            grace_period_days=7,
            auto_restrict_access=True,
            issue_initial_invoice=True
        )
        # Advance into restricted state on 18 Nov
        update_all_overdue_statuses(as_of_date=date(2026, 11, 18))
        plan.refresh_from_db()
        self.assertEqual(plan.status, PlanStatus.RESTRICTED)
        self.assertFalse(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 11, 18)))

        # Pay invoice
        inv = StudentInvoice.objects.get(billing_plan=plan)
        record_manual_payment(invoice=inv, amount=Decimal("2000.00"))
        plan.refresh_from_db()

        self.assertEqual(plan.status, PlanStatus.ACTIVE)
        self.assertTrue(student_has_billing_access(self.student, self.course, as_of_date=date(2026, 11, 18)))

    # 18. Payment success advances billing cycle once
    def test_payment_success_advances_billing_cycle_once(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            issue_initial_invoice=True
        )
        inv = StudentInvoice.objects.get(billing_plan=plan)
        record_manual_payment(invoice=inv, amount=Decimal("2000.00"))
        plan.refresh_from_db()

        self.assertEqual(plan.current_period_start, date(2026, 12, 10))
        self.assertEqual(plan.current_period_end, date(2027, 1, 9))
        self.assertEqual(plan.next_billing_date, date(2027, 1, 10))

    # 19. Duplicate payment callback safe
    def test_duplicate_payment_callback_safe(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            issue_initial_invoice=True
        )
        inv = StudentInvoice.objects.get(billing_plan=plan)

        # First callback
        _, first_proc = process_invoice_payment_success(inv, razorpay_payment_id="pay_12345")
        self.assertTrue(first_proc)
        plan.refresh_from_db()
        first_period_start = plan.current_period_start

        # Duplicate webhook / callback
        _, second_proc = process_invoice_payment_success(inv, razorpay_payment_id="pay_12345")
        self.assertFalse(second_proc)
        plan.refresh_from_db()

        # Did NOT double-advance
        self.assertEqual(plan.current_period_start, first_period_start)

    # 20. NEXT_CYCLE plan change activates at correct date
    def test_next_cycle_plan_change_activates_at_correct_date(self):
        start_date = date(2026, 10, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            issue_initial_invoice=True
        )
        # Current period: 10 Oct to 9 Nov. Next cycle: 10 Nov.
        apply_plan_change(
            plan=plan,
            new_billing_type=BillingType.EVERY_3_MONTHS,
            new_amount=Decimal("5000.00"),
            change_mode='NEXT_CYCLE'
        )
        plan.refresh_from_db()
        self.assertEqual(plan.pending_effective_date, date(2026, 11, 10))

        # Run plan changes activation on effective date: 10 Nov
        res = activate_scheduled_plan_changes(as_of_date=date(2026, 11, 10))
        self.assertEqual(res["activated"], 1)

        plan.refresh_from_db()
        self.assertEqual(plan.billing_type, BillingType.EVERY_3_MONTHS)
        self.assertEqual(plan.amount, Decimal("5000.00"))
        self.assertIsNone(plan.pending_billing_type)
        self.assertEqual(plan.current_period_start, date(2026, 11, 10))
        self.assertEqual(plan.current_period_end, date(2027, 2, 9))

    # 21. NEXT_CYCLE plan change does not activate early
    def test_next_cycle_plan_change_does_not_activate_early(self):
        start_date = date(2026, 10, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            issue_initial_invoice=True
        )
        apply_plan_change(
            plan=plan,
            new_billing_type=BillingType.EVERY_3_MONTHS,
            new_amount=Decimal("5000.00"),
            change_mode='NEXT_CYCLE'
        )
        # Attempt to run on 5 Nov (before 10 Nov effective date)
        res = activate_scheduled_plan_changes(as_of_date=date(2026, 11, 5))
        self.assertEqual(res["activated"], 0)

        plan.refresh_from_db()
        self.assertEqual(plan.billing_type, BillingType.MONTHLY)
        self.assertEqual(plan.amount, Decimal("2000.00"))
        self.assertIsNotNone(plan.pending_billing_type)

    # 22. One-time plan never generates recurring invoice
    def test_onetime_plan_never_generates_recurring_invoice(self):
        start_date = date(2026, 1, 1)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.ONE_TIME,
            amount=Decimal("15000.00"),
            start_date=start_date,
            issue_initial_invoice=True
        )
        # Run invoice generator 1 year later
        res = generate_upcoming_invoices(as_of_date=date(2027, 1, 1))
        self.assertEqual(res["generated"], 0)

        # Only 1 initial invoice should ever exist
        count = StudentInvoice.objects.filter(billing_plan=plan).count()
        self.assertEqual(count, 1)

    # 23. Paused plan does not generate invoice
    def test_paused_plan_does_not_generate_invoice(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            advance_invoice_days=7,
            issue_initial_invoice=True
        )
        plan.status = PlanStatus.PAUSED
        plan.save(update_fields=['status'])

        # Check on advance date: 3 Dec 2026
        res = generate_upcoming_invoices(as_of_date=date(2026, 12, 3))
        self.assertEqual(res["generated"], 0)

    # 24. Cancelled plan does not generate invoice
    def test_cancelled_plan_does_not_generate_invoice(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            advance_invoice_days=7,
            issue_initial_invoice=True
        )
        plan.status = PlanStatus.CANCELLED
        plan.save(update_fields=['status'])

        res = generate_upcoming_invoices(as_of_date=date(2026, 12, 3))
        self.assertEqual(res["generated"], 0)

    # 25. Resumed plan returns to automation
    def test_resumed_plan_returns_to_automation(self):
        start_date = date(2026, 11, 10)
        plan = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            advance_invoice_days=7,
            issue_initial_invoice=True
        )
        # Pause
        plan.status = PlanStatus.PAUSED
        plan.save(update_fields=['status'])

        res1 = generate_upcoming_invoices(as_of_date=date(2026, 12, 3))
        self.assertEqual(res1["generated"], 0)

        # Resume
        plan.status = PlanStatus.ACTIVE
        plan.save(update_fields=['status'])

        res2 = generate_upcoming_invoices(as_of_date=date(2026, 12, 3))
        self.assertEqual(res2["generated"], 1)

    # 26. Multiple students processed independently
    def test_multiple_students_processed_independently(self):
        start_date = date(2026, 11, 10)
        plan1 = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            advance_invoice_days=7
        )
        plan2 = create_billing_plan(
            student=self.student2,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2500.00"),
            start_date=start_date,
            advance_invoice_days=7
        )
        res = generate_upcoming_invoices(as_of_date=date(2026, 12, 3))
        self.assertEqual(res["generated"], 2)

    # 27. One student's failure does not stop others
    @patch('billing.services.lifecycle.generate_cycle_invoice')
    def test_one_students_failure_does_not_stop_others(self, mock_gen_invoice):
        start_date = date(2026, 11, 10)
        plan1 = create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            advance_invoice_days=7
        )
        plan2 = create_billing_plan(
            student=self.student2,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2500.00"),
            start_date=start_date,
            advance_invoice_days=7
        )

        # Simulate exception on first student, success on second
        mock_gen_invoice.side_effect = [
            Exception("Database deadlock for student 1"),
            (MagicMock(invoice_number="INV-2"), True)
        ]

        res = generate_upcoming_invoices(as_of_date=date(2026, 12, 3))
        self.assertEqual(res["errors"], 1)
        self.assertEqual(res["generated"], 1)

    # 28. Celery daily master task orchestrates all subtasks safely
    def test_celery_daily_master_task_orchestration(self):
        start_date = date(2026, 11, 10)
        create_billing_plan(
            student=self.student,
            course=self.course,
            billing_type=BillingType.MONTHLY,
            amount=Decimal("2000.00"),
            start_date=start_date,
            advance_invoice_days=7,
            issue_initial_invoice=True
        )
        # Execute master task directly
        result = billing_daily_automation.apply(args=["2026-11-10"]).get()
        self.assertIn("plan_changes", result)
        self.assertIn("upcoming_invoices", result)
        self.assertIn("overdue_statuses", result)
        self.assertIn("reminders", result)
