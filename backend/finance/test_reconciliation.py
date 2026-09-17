"""
Phase 3.5.7. Tests for finance/reconciliation.py -- the read-only
reconciliation service, the instructor balance calculator, the two
admin-only reconciliation APIs, and the `reconcile_finance` management
command.

Two check functions in finance/reconciliation.py are documented as
DEFENSE-IN-DEPTH against conditions the database schema itself already
prevents at the constraint level:
- check_duplicate_financial_records()'s entire purpose (a genuine
  EARNING/Invoice duplicate for the same source)
- check_ledger_entry_invalid_source()'s "all three source FKs null"
  sub-check
Both are guarded by DB-level (Unique/Check)Constraints already covered by
finance/tests.py's own constraint tests (LedgerEntryConstraintTests) and
finance/test_refunds.py's (RefundModelConstraintTests) -- a genuine
violation cannot be constructed here via the ORM without deliberately
bypassing a working safeguard, which this suite does not do. Both
functions ARE exercised for the "no false positives in a healthy system"
path; their true-positive paths are verified by code review + the
constraints' own tests, not by an executable duplicate-row test.

True PostgreSQL-grade concurrent locking is NOT exercised here (SQLite
test backend) -- reconciliation itself is pure reads with no locking of
its own, so this only restates the pre-existing caveat already documented
against create_payout_batch/approve_payout/create_and_process_refund.
"""
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from courses.models import Course
from orders.models import Order, OrderItem, Subscription, SubscriptionPayment, SubscriptionPlan
from orders.services import fulfill_order, fulfill_purchase

from .models import Invoice, LedgerEntry, Payout, Refund
from .reconciliation import (
    IssueType, Severity, check_clawback_against_paid_payout, check_duplicate_financial_records,
    check_ledger_entry_calculation_mismatch, check_ledger_entry_invalid_source,
    check_order_missing_invoice, check_order_missing_ledger_entry, check_payout_total_mismatch,
    check_purchase_missing_invoice, check_purchase_missing_ledger_entry, check_refund_exceeds_source_amount,
    check_refund_missing_clawback, check_subscription_payment_missing_invoice,
    check_subscription_payment_missing_ledger_entry, get_instructor_balance,
    get_instructors_with_negative_balance, run_reconciliation,
)
from .services import create_and_process_refund, create_payout_batch
from .test_refunds import _make_course_instructor, _paid_purchase

User = get_user_model()


class HealthyPurchaseReconciliationTests(TestCase):
    """A correctly-fulfilled Purchase (ledger + invoice both present)
    must never be reported by any check."""

    def setUp(self):
        self.student = User.objects.create_user(username='recon_healthy_student', password='password123')
        self.teacher = User.objects.create_user(username='recon_healthy_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Reconciliation Healthy Course', description='x', price=Decimal('500.00'))
        _make_course_instructor(self.course, self.teacher)
        self.purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_recon_healthy_1')

    def test_no_missing_ledger_entry_issue(self):
        self.assertEqual(check_purchase_missing_ledger_entry(), [])

    def test_no_missing_invoice_issue(self):
        self.assertEqual(check_purchase_missing_invoice(), [])

    def test_run_reconciliation_reports_zero_critical(self):
        report = run_reconciliation()
        self.assertEqual(report.critical_count, 0)


class PurchaseMissingLedgerEntryTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='recon_missing_ledger_student', password='password123')

    def test_missing_ledger_entry_flagged_when_instructor_eligible(self):
        teacher = User.objects.create_user(username='recon_missing_ledger_teacher', password='password123', is_teacher=True)
        course = Course.objects.create(title='Missing Ledger Course', description='x', price=Decimal('400.00'))
        _make_course_instructor(course, teacher)
        from orders.models import Purchase
        # Bypass fulfill_purchase entirely -- simulates the exact "successful
        # payment/access but the ledger-creation hook's own broad except
        # Exception swallowed a genuine bug" scenario Objective 13 asks
        # reconciliation to detect.
        purchase = Purchase.objects.create(
            user=self.student, course=course, amount=Decimal('400.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_missing_ledger_1',
        )
        issues = check_purchase_missing_ledger_entry()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, IssueType.PURCHASE_MISSING_LEDGER_ENTRY)
        self.assertEqual(issues[0].severity, Severity.CRITICAL)
        self.assertEqual(issues[0].source_id, purchase.id)

    def test_no_eligible_instructor_is_not_reported(self):
        # No CourseInstructor at all -- _create_earning_entry's own
        # documented safe no-op. Must NOT be flagged as an issue.
        course = Course.objects.create(title='No Instructor Course', description='x', price=Decimal('300.00'))
        from orders.models import Purchase
        Purchase.objects.create(
            user=self.student, course=course, amount=Decimal('300.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_no_instructor_1',
        )
        self.assertEqual(check_purchase_missing_ledger_entry(), [])

    def test_no_commission_rate_is_not_reported(self):
        teacher = User.objects.create_user(username='recon_no_rate_teacher', password='password123', is_teacher=True)
        course = Course.objects.create(title='No Rate Course', description='x', price=Decimal('300.00'))
        _make_course_instructor(course, teacher, commission_rate=None)
        from orders.models import Purchase
        Purchase.objects.create(
            user=self.student, course=course, amount=Decimal('300.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_no_rate_1',
        )
        self.assertEqual(check_purchase_missing_ledger_entry(), [])

    def test_pending_purchase_not_reported(self):
        course = Course.objects.create(title='Pending Course', description='x', price=Decimal('300.00'))
        from orders.models import Purchase
        Purchase.objects.create(user=self.student, course=course, amount=Decimal('300.00'), status=Purchase.Status.PENDING)
        self.assertEqual(check_purchase_missing_ledger_entry(), [])


class PurchaseMissingInvoiceTests(TestCase):
    def test_missing_invoice_always_flagged(self):
        student = User.objects.create_user(username='recon_missing_invoice_student', password='password123')
        course = Course.objects.create(title='Missing Invoice Course', description='x', price=Decimal('250.00'))
        from orders.models import Purchase
        purchase = Purchase.objects.create(
            user=student, course=course, amount=Decimal('250.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_missing_invoice_1',
        )
        issues = check_purchase_missing_invoice()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, IssueType.PURCHASE_MISSING_INVOICE)
        self.assertEqual(issues[0].severity, Severity.WARNING)
        self.assertEqual(issues[0].source_id, purchase.id)


class OrderReconciliationTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='recon_order_student', password='password123')
        self.teacher = User.objects.create_user(username='recon_order_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Recon Order Course', description='x', price=Decimal('600.00'))
        _make_course_instructor(self.course, self.teacher)

    def _paid_order(self):
        return Order.objects.create(
            user=self.student, status=Order.Status.PAID, subtotal=Decimal('600.00'),
            total_amount=Decimal('600.00'), currency='INR', razorpay_payment_id='pay_recon_order_1',
        )

    def test_healthy_order_no_issues(self):
        order = self._paid_order()
        OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.COURSE, course=self.course,
            title_snapshot=self.course.title, unit_price=Decimal('600.00'), total_price=Decimal('600.00'),
        )
        fulfill_order(order, previous_status='PENDING')
        self.assertEqual(check_order_missing_ledger_entry(), [])
        self.assertEqual(check_order_missing_invoice(), [])

    def test_order_missing_ledger_entry_flagged(self):
        order = self._paid_order()
        item = OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.COURSE, course=self.course,
            title_snapshot=self.course.title, unit_price=Decimal('600.00'), total_price=Decimal('600.00'),
        )
        # No fulfill_order() call -- simulates the ledger-creation hook
        # having silently failed for an otherwise-real, paid order.
        issues = check_order_missing_ledger_entry()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, IssueType.ORDER_ITEM_MISSING_LEDGER_ENTRY)
        self.assertEqual(issues[0].source_id, item.id)
        self.assertEqual(issues[0].related_object_id, order.id)

    def test_order_missing_invoice_flagged(self):
        order = self._paid_order()
        OrderItem.objects.create(
            order=order, item_type=OrderItem.ItemType.COURSE, course=self.course,
            title_snapshot=self.course.title, unit_price=Decimal('600.00'), total_price=Decimal('600.00'),
        )
        issues = check_order_missing_invoice()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, IssueType.ORDER_MISSING_INVOICE)
        self.assertEqual(issues[0].source_id, order.id)


class SubscriptionPaymentReconciliationTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='recon_sub_student', password='password123')
        self.teacher = User.objects.create_user(username='recon_sub_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Recon Sub Course', description='x', price=Decimal('999.00'))
        _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('25.00'))
        self.plan = SubscriptionPlan.objects.create(
            name='Recon Test Plan', billing_interval='MONTHLY', price='999.00', razorpay_plan_id='plan_recon_test',
        )
        self.plan.courses.add(self.course)
        self.subscription = Subscription.objects.create(
            user=self.student, plan=self.plan, status=Subscription.Status.ACTIVE,
            razorpay_subscription_id='sub_recon_test_1', razorpay_plan_id='plan_recon_test',
        )

    def _success_payment(self, payment_id):
        return SubscriptionPayment.objects.create(
            subscription=self.subscription, razorpay_payment_id=payment_id,
            razorpay_subscription_id='sub_recon_test_1', amount=Decimal('999.00'), currency='INR',
            status=SubscriptionPayment.Status.SUCCESS,
        )

    def test_healthy_subscription_payment_no_issues(self):
        from .services import create_earning_entry_for_subscription_payment, create_invoice_for_subscription_payment
        sp = self._success_payment('pay_recon_sub_healthy_1')
        create_earning_entry_for_subscription_payment(sp)
        create_invoice_for_subscription_payment(sp)
        self.assertEqual(check_subscription_payment_missing_ledger_entry(), [])
        self.assertEqual(check_subscription_payment_missing_invoice(), [])

    def test_missing_ledger_entry_flagged(self):
        sp = self._success_payment('pay_recon_sub_missing_1')
        issues = check_subscription_payment_missing_ledger_entry()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, IssueType.SUBSCRIPTION_PAYMENT_MISSING_LEDGER_ENTRY)
        self.assertEqual(issues[0].source_id, sp.id)

    def test_multi_course_plan_not_reported(self):
        # A multi-course plan is the same documented "safe no-op" as a
        # multi-course Bundle -- must not be flagged.
        course2 = Course.objects.create(title='Recon Sub Course 2', description='x', price=Decimal('0'))
        self.plan.courses.add(course2)
        sp = self._success_payment('pay_recon_sub_multi_1')
        self.assertEqual(check_subscription_payment_missing_ledger_entry(), [])


class RefundClawbackReconciliationTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='recon_refund_student', password='password123')
        self.admin = User.objects.create_user(username='recon_refund_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='recon_refund_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Recon Refund Course', description='x', price=Decimal('500.00'))
        _make_course_instructor(self.course, self.teacher)

    def test_successful_refund_with_correct_clawback_not_flagged(self):
        from unittest.mock import patch
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_recon_refund_ok_1')
        with patch('orders.views.client') as mock_client:
            mock_client.payment.refund.return_value = {"id": "rfnd_recon_ok_1", "status": "processed"}
            create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)
        self.assertEqual(check_refund_missing_clawback(), [])

    def test_successful_refund_missing_clawback_flagged(self):
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_recon_refund_bad_1')
        # Manually construct a SUCCESS Refund WITHOUT going through
        # create_and_process_refund -- simulates _create_clawback_for_refund's
        # own broad except Exception having swallowed a genuine bug.
        Refund.objects.create(
            customer=self.student, purchase=purchase, amount=Decimal('500.00'),
            requested_by=self.admin, status=Refund.Status.SUCCESS, razorpay_payment_id='pay_recon_refund_bad_1',
        )
        issues = check_refund_missing_clawback()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, IssueType.REFUND_MISSING_CLAWBACK)
        self.assertEqual(issues[0].severity, Severity.CRITICAL)

    def test_refund_with_no_eligible_earning_not_flagged(self):
        # Purchase for a course with no CourseInstructor at all -- nothing
        # to claw back was ever possible, so a missing clawback here is
        # correct, not an issue.
        unowned_course = Course.objects.create(title='Recon Unowned Course', description='x', price=Decimal('200.00'))
        from orders.models import Purchase
        purchase = _paid_purchase(self.student, unowned_course, Decimal('200.00'), razorpay_payment_id='pay_recon_unowned_1')
        Refund.objects.create(
            customer=self.student, purchase=purchase, amount=Decimal('200.00'),
            requested_by=self.admin, status=Refund.Status.SUCCESS, razorpay_payment_id='pay_recon_unowned_1',
        )
        self.assertEqual(check_refund_missing_clawback(), [])


class DuplicateFinancialRecordDefenseTests(TestCase):
    """check_duplicate_financial_records() is defense-in-depth against a
    condition ledgerentry_unique_purchase_entry_type/invoice_unique_purchase
    (and their order_item/order/subscription_payment siblings) already
    prevent at the DB constraint level -- see this module's own docstring
    for why a genuine duplicate cannot be constructed here."""

    def test_no_false_positives_in_healthy_system(self):
        student = User.objects.create_user(username='recon_dup_student', password='password123')
        teacher = User.objects.create_user(username='recon_dup_teacher', password='password123', is_teacher=True)
        course = Course.objects.create(title='Recon Dup Course', description='x', price=Decimal('500.00'))
        _make_course_instructor(course, teacher)
        _paid_purchase(student, course, Decimal('500.00'), razorpay_payment_id='pay_recon_dup_1')
        self.assertEqual(check_duplicate_financial_records(), [])


class LedgerEntryInvalidSourceTests(TestCase):
    def test_course_mismatch_flagged(self):
        student = User.objects.create_user(username='recon_mismatch_student', password='password123')
        teacher = User.objects.create_user(username='recon_mismatch_teacher', password='password123', is_teacher=True)
        real_course = Course.objects.create(title='Real Course', description='x', price=Decimal('500.00'))
        wrong_course = Course.objects.create(title='Wrong Attributed Course', description='x', price=Decimal('500.00'))
        wrong_instructor = _make_course_instructor(wrong_course, teacher)

        from orders.models import Purchase
        purchase = Purchase.objects.create(
            user=student, course=real_course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_recon_mismatch_1',
        )
        # Manually construct a LedgerEntry whose course_instructor belongs
        # to a DIFFERENT course than the purchase's actual course -- no
        # existing constraint prevents this; it's exactly what this check
        # exists to catch.
        LedgerEntry.objects.create(
            course_instructor=wrong_instructor, purchase=purchase, entry_type=LedgerEntry.EntryType.EARNING,
            gross_amount=Decimal('500.00'), currency='INR', commission_rate=Decimal('30.00'),
            commission_amount=Decimal('150.00'), net_amount=Decimal('350.00'),
        )
        issues = check_ledger_entry_invalid_source()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, IssueType.LEDGER_ENTRY_INVALID_SOURCE)
        self.assertEqual(issues[0].severity, Severity.CRITICAL)

    def test_no_orphaned_or_mismatched_entries_in_healthy_system(self):
        student = User.objects.create_user(username='recon_valid_student', password='password123')
        teacher = User.objects.create_user(username='recon_valid_teacher', password='password123', is_teacher=True)
        course = Course.objects.create(title='Recon Valid Course', description='x', price=Decimal('500.00'))
        _make_course_instructor(course, teacher)
        _paid_purchase(student, course, Decimal('500.00'), razorpay_payment_id='pay_recon_valid_1')
        self.assertEqual(check_ledger_entry_invalid_source(), [])


class LedgerEntryCalculationMismatchTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='recon_calc_student', password='password123')
        self.teacher = User.objects.create_user(username='recon_calc_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Recon Calc Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

        from orders.models import Purchase
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_recon_calc_1',
        )

    def test_wrong_commission_amount_flagged(self):
        LedgerEntry.objects.create(
            course_instructor=self.instructor, purchase=self.purchase, entry_type=LedgerEntry.EntryType.EARNING,
            gross_amount=Decimal('500.00'), currency='INR', commission_rate=Decimal('30.00'),
            commission_amount=Decimal('999.00'),  # wrong -- should be 150.00
            net_amount=Decimal('-499.00'),
        )
        issues = check_ledger_entry_calculation_mismatch()
        self.assertTrue(any(i.issue_type == IssueType.LEDGER_ENTRY_CALCULATION_MISMATCH for i in issues))

    def test_wrong_net_amount_flagged(self):
        LedgerEntry.objects.create(
            course_instructor=self.instructor, purchase=self.purchase, entry_type=LedgerEntry.EntryType.EARNING,
            gross_amount=Decimal('500.00'), currency='INR', commission_rate=Decimal('30.00'),
            commission_amount=Decimal('150.00'), net_amount=Decimal('999.00'),  # wrong -- should be 350.00
        )
        issues = check_ledger_entry_calculation_mismatch()
        self.assertTrue(any(i.issue_type == IssueType.LEDGER_ENTRY_CALCULATION_MISMATCH for i in issues))

    def test_correct_entry_not_flagged(self):
        LedgerEntry.objects.create(
            course_instructor=self.instructor, purchase=self.purchase, entry_type=LedgerEntry.EntryType.EARNING,
            gross_amount=Decimal('500.00'), currency='INR', commission_rate=Decimal('30.00'),
            commission_amount=Decimal('150.00'), net_amount=Decimal('350.00'),
        )
        self.assertEqual(check_ledger_entry_calculation_mismatch(), [])


class PayoutReconciliationTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='recon_payout_student', password='password123')
        self.teacher = User.objects.create_user(username='recon_payout_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Recon Payout Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))
        self.purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_recon_payout_1')
        self.entry = LedgerEntry.objects.get(purchase=self.purchase)

    def test_healthy_payout_no_issues(self):
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[self.entry.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        self.assertEqual(check_payout_total_mismatch(), [])

    def test_tampered_payout_totals_flagged(self):
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[self.entry.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        # Simulate drift/tampering -- nothing in this codebase does this,
        # but reconciliation must catch it if it ever happens.
        Payout.objects.filter(pk=payout.pk).update(net_amount=Decimal('999999.00'))
        issues = check_payout_total_mismatch()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, IssueType.PAYOUT_TOTAL_MISMATCH)
        self.assertEqual(issues[0].severity, Severity.CRITICAL)

    def test_negative_payout_value_flagged(self):
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[self.entry.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        Payout.objects.filter(pk=payout.pk).update(net_amount=Decimal('-50.00'))
        issues = check_payout_total_mismatch()
        self.assertTrue(any(i.issue_type == IssueType.PAYOUT_NEGATIVE_VALUE for i in issues))

    def test_recipient_mismatch_flagged(self):
        other_teacher = User.objects.create_user(username='recon_payout_other_teacher', password='password123', is_teacher=True)
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[self.entry.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        Payout.objects.filter(pk=payout.pk).update(recipient=other_teacher)
        issues = check_payout_total_mismatch()
        self.assertTrue(any(i.issue_type == IssueType.PAYOUT_RECIPIENT_MISMATCH for i in issues))


class ClawbackAgainstPaidPayoutTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='recon_cb_payout_student', password='password123')
        self.admin = User.objects.create_user(username='recon_cb_payout_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='recon_cb_payout_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Recon CB Payout Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    def test_clawback_against_paid_payout_flagged_as_warning(self):
        from unittest.mock import patch
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_recon_cb_payout_1')
        entry = LedgerEntry.objects.get(purchase=purchase)
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[entry.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        Payout.objects.filter(pk=payout.pk).update(status=Payout.Status.PAID)

        with patch('orders.views.client') as mock_client:
            mock_client.payment.refund.return_value = {"id": "rfnd_recon_cb_payout_1", "status": "processed"}
            create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)

        issues = check_clawback_against_paid_payout()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.WARNING)
        self.assertEqual(issues[0].issue_type, IssueType.CLAWBACK_AGAINST_PAID_PAYOUT)

        # Neither the Payout nor the original EARNING entry were touched.
        payout.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(payout.status, Payout.Status.PAID)
        self.assertEqual(payout.net_amount, Decimal('350.00'))
        self.assertEqual(entry.net_amount, Decimal('350.00'))


class RefundExceedsSourceAmountTests(TestCase):
    def test_cumulative_refunds_exceeding_source_flagged(self):
        student = User.objects.create_user(username='recon_over_refund_student', password='password123')
        admin = User.objects.create_user(username='recon_over_refund_admin', password='password123', is_staff=True)
        course = Course.objects.create(title='Recon Over Refund Course', description='x', price=Decimal('500.00'))
        purchase = _paid_purchase(student, course, Decimal('500.00'), razorpay_payment_id='pay_recon_over_1')
        # Bypass create_and_process_refund's own locked validation entirely
        # -- simulates a scenario where the enforced invariant somehow
        # drifted (e.g. a future bug, manual DB edit).
        Refund.objects.create(customer=student, purchase=purchase, amount=Decimal('400.00'), requested_by=admin, status=Refund.Status.SUCCESS)
        Refund.objects.create(customer=student, purchase=purchase, amount=Decimal('300.00'), requested_by=admin, status=Refund.Status.SUCCESS)
        issues = check_refund_exceeds_source_amount()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].issue_type, IssueType.REFUND_EXCEEDS_SOURCE_AMOUNT)
        self.assertEqual(issues[0].severity, Severity.CRITICAL)

    def test_valid_cumulative_refunds_not_flagged(self):
        student = User.objects.create_user(username='recon_valid_refund_student', password='password123')
        admin = User.objects.create_user(username='recon_valid_refund_admin', password='password123', is_staff=True)
        course = Course.objects.create(title='Recon Valid Refund Course', description='x', price=Decimal('500.00'))
        purchase = _paid_purchase(student, course, Decimal('500.00'), razorpay_payment_id='pay_recon_valid_refund_1')
        Refund.objects.create(customer=student, purchase=purchase, amount=Decimal('200.00'), requested_by=admin, status=Refund.Status.SUCCESS)
        Refund.objects.create(customer=student, purchase=purchase, amount=Decimal('300.00'), requested_by=admin, status=Refund.Status.SUCCESS)
        self.assertEqual(check_refund_exceeds_source_amount(), [])


class InstructorBalanceTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='recon_balance_student', password='password123')
        self.admin = User.objects.create_user(username='recon_balance_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='recon_balance_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Recon Balance Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    def test_pure_earning_balance(self):
        _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_balance_1')
        balance = get_instructor_balance(self.teacher)
        self.assertEqual(balance.earned, Decimal('350.00'))
        self.assertEqual(balance.clawed_back, Decimal('0'))
        self.assertEqual(balance.paid, Decimal('0'))
        self.assertEqual(balance.available, Decimal('350.00'))
        self.assertEqual(balance.outstanding_debt, Decimal('0'))

    def test_earning_batched_and_paid(self):
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_balance_2')
        entry = LedgerEntry.objects.get(purchase=purchase)
        payout = create_payout_batch(recipient=self.teacher, ledger_entry_ids=[entry.id], period_start='2026-01-01', period_end='2026-01-31')
        Payout.objects.filter(pk=payout.pk).update(status=Payout.Status.PAID)

        balance = get_instructor_balance(self.teacher)
        self.assertEqual(balance.paid, Decimal('350.00'))
        self.assertEqual(balance.available, Decimal('0'))  # already batched -- no longer eligible

    def test_negative_balance_from_clawback_against_paid_payout(self):
        from unittest.mock import patch
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_balance_3')
        entry = LedgerEntry.objects.get(purchase=purchase)
        payout = create_payout_batch(recipient=self.teacher, ledger_entry_ids=[entry.id], period_start='2026-01-01', period_end='2026-01-31')
        Payout.objects.filter(pk=payout.pk).update(status=Payout.Status.PAID)

        with patch('orders.views.client') as mock_client:
            mock_client.payment.refund.return_value = {"id": "rfnd_balance_3", "status": "processed"}
            create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)

        balance = get_instructor_balance(self.teacher)
        self.assertEqual(balance.clawed_back, Decimal('-350.00'))
        self.assertEqual(balance.outstanding_debt, Decimal('350.00'))
        self.assertEqual(balance.outstanding_debt_from_paid_payouts, Decimal('350.00'))
        self.assertIn(self.teacher.id, [b.instructor_user_id for b in get_instructors_with_negative_balance()])

    def test_multiple_instructors_do_not_leak_into_each_others_balance(self):
        other_teacher = User.objects.create_user(username='recon_balance_other_teacher', password='password123', is_teacher=True)
        other_course = Course.objects.create(title='Recon Balance Other Course', description='x', price=Decimal('200.00'))
        _make_course_instructor(other_course, other_teacher, commission_rate=Decimal('50.00'))

        _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_balance_multi_1')
        _paid_purchase(self.student, other_course, Decimal('200.00'), razorpay_payment_id='pay_balance_multi_2')

        balance_a = get_instructor_balance(self.teacher)
        balance_b = get_instructor_balance(other_teacher)
        self.assertEqual(balance_a.earned, Decimal('350.00'))
        self.assertEqual(balance_b.earned, Decimal('100.00'))


class ReconciliationAPIPermissionTests(APITestCase):
    def setUp(self):
        self.student = User.objects.create_user(username='recon_api_student', password='password123')
        self.teacher = User.objects.create_user(username='recon_api_teacher', password='password123', is_teacher=True)
        self.admin = User.objects.create_user(username='recon_api_admin', password='password123', is_staff=True)
        self.reconciliation_url = reverse('finance-admin-reconciliation')
        self.health_url = reverse('finance-admin-health')

    def test_unauthenticated_denied(self):
        self.assertEqual(self.client.get(self.reconciliation_url).status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(self.client.get(self.health_url).status_code, status.HTTP_401_UNAUTHORIZED)

    def test_student_denied(self):
        self.client.force_authenticate(self.student)
        self.assertEqual(self.client.get(self.reconciliation_url).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(self.health_url).status_code, status.HTTP_403_FORBIDDEN)

    def test_teacher_denied(self):
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.client.get(self.reconciliation_url).status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self.client.get(self.health_url).status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_allowed(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get(self.reconciliation_url).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(self.health_url).status_code, status.HTTP_200_OK)


class ReconciliationAPIFilteringAndSafetyTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='recon_api_filter_admin', password='password123', is_staff=True)
        self.student = User.objects.create_user(username='recon_api_filter_student', password='password123')
        self.course = Course.objects.create(title='Recon API Course', description='x', price=Decimal('300.00'))
        from orders.models import Purchase
        self.purchase = Purchase.objects.create(
            user=self.student, course=self.course, amount=Decimal('300.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_recon_api_1',
        )  # missing ledger entry AND invoice -- deliberately unfulfilled
        self.order = Order.objects.create(
            user=self.student, status=Order.Status.PAID, subtotal=Decimal('300.00'),
            total_amount=Decimal('300.00'), currency='INR', razorpay_payment_id='pay_recon_api_order_1',
        )
        OrderItem.objects.create(
            order=self.order, item_type=OrderItem.ItemType.COURSE, course=self.course,
            title_snapshot=self.course.title, unit_price=Decimal('300.00'), total_price=Decimal('300.00'),
        )  # missing invoice -- deliberately unfulfilled
        self.reconciliation_url = reverse('finance-admin-reconciliation')
        self.health_url = reverse('finance-admin-health')

    def test_severity_filter(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.reconciliation_url, {'severity': 'WARNING'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(all(i['severity'] == 'WARNING' for i in response.data['issues']))
        self.assertTrue(any(i['issue_type'] == IssueType.PURCHASE_MISSING_INVOICE for i in response.data['issues']))

    def test_issue_type_filter(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.reconciliation_url, {'issue_type': IssueType.PURCHASE_MISSING_INVOICE})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(all(i['issue_type'] == IssueType.PURCHASE_MISSING_INVOICE for i in response.data['issues']))

    def test_source_type_filter(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.reconciliation_url, {'source_type': 'ORDER'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(len(response.data['issues']) >= 1)
        self.assertTrue(all(i['source_type'] == 'ORDER' for i in response.data['issues']))

    def test_date_range_filter_excludes_out_of_range_records(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.reconciliation_url, {'date_from': '2099-01-01'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(any(i['source_id'] == self.purchase.id for i in response.data['issues']))

    def test_invalid_filter_values_are_ignored_not_400ed(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.reconciliation_url, {'severity': 'NOT_A_REAL_SEVERITY'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_no_sensitive_fields_exposed(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.reconciliation_url)
        body = str(response.data).lower()
        self.assertNotIn('razorpay_key_secret', body)
        self.assertNotIn('webhook_secret', body)
        self.assertNotIn('payload', body)

    def test_finance_health_summary_shape(self):
        self.client.force_authenticate(self.admin)
        response = self.client.get(self.health_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for key in ('total_issues', 'critical', 'warnings', 'healthy_checks', 'missing_ledger_count', 'missing_invoice_count', 'payout_mismatches', 'instructor_negative_balances'):
            self.assertIn(key, response.data)
        self.assertGreaterEqual(response.data['missing_invoice_count'], 1)


class ReconcileFinanceCommandTests(TestCase):
    def test_clean_system_exits_zero(self):
        out = io.StringIO()
        call_command('reconcile_finance', stdout=out)
        output = out.getvalue()
        self.assertIn('Finance Reconciliation', output)
        self.assertIn('Critical: 0', output)

    def test_critical_issue_exits_nonzero(self):
        student = User.objects.create_user(username='recon_cmd_student', password='password123')
        teacher = User.objects.create_user(username='recon_cmd_teacher', password='password123', is_teacher=True)
        course = Course.objects.create(title='Recon Command Course', description='x', price=Decimal('500.00'))
        _make_course_instructor(course, teacher)
        from orders.models import Purchase
        Purchase.objects.create(
            user=student, course=course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_recon_cmd_1',
        )  # eligible instructor, no LedgerEntry -- guaranteed CRITICAL

        out = io.StringIO()
        with self.assertRaises(SystemExit) as cm:
            call_command('reconcile_finance', stdout=out)
        self.assertEqual(cm.exception.code, 1)
        output = out.getvalue()
        self.assertIn('CRITICAL:', output)
        self.assertIn('has no EARNING LedgerEntry', output)

    def test_command_never_creates_or_modifies_records(self):
        student = User.objects.create_user(username='recon_cmd_safe_student', password='password123')
        teacher = User.objects.create_user(username='recon_cmd_safe_teacher', password='password123', is_teacher=True)
        course = Course.objects.create(title='Recon Command Safe Course', description='x', price=Decimal('500.00'))
        _make_course_instructor(course, teacher)
        from orders.models import Purchase
        Purchase.objects.create(
            user=student, course=course, amount=Decimal('500.00'), status=Purchase.Status.SUCCESS,
            razorpay_payment_id='pay_recon_cmd_safe_1',
        )
        ledger_count_before = LedgerEntry.objects.count()
        invoice_count_before = Invoice.objects.count()

        out = io.StringIO()
        with self.assertRaises(SystemExit):
            call_command('reconcile_finance', stdout=out)

        self.assertEqual(LedgerEntry.objects.count(), ledger_count_before)
        self.assertEqual(Invoice.objects.count(), invoice_count_before)
