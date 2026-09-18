"""
Phase 3.5.8: Clawback-Aware Payout Settlement.

Tests for the extensions to finance/services.py's
get_eligible_ledger_entries_queryset()/create_payout_batch() (mixed
EARNING+CLAWBACK selection, the floor rule, the pairing rule) and the
corresponding correction to finance/reconciliation.py's
check_clawback_against_paid_payout() (stops flagging a clawback once it
has been settled into any payout) and get_instructor_balance()'s
redefined AVAILABLE.

Scope, exactly as audited and approved:
- NEVER modifies historical EARNING LedgerEntry records or historical PAID
  Payout totals -- verified explicitly below (ClawbackAfterPaidPayoutSettlementTests).
- No new model, no migration -- LedgerEntry.payout is the same plain FK
  used since Phase 3.5.2; only its filtering/validation changed.
- No change to refund business rules or Razorpay webhook handling --
  nothing in this file touches orders/views.py or the refund creation
  path itself, only what happens to an already-created CLAWBACK entry
  afterward.
- True PostgreSQL-grade concurrent locking is NOT verified here (SQLite
  test backend, select_for_update() a documented no-op) -- the
  "duplicate/concurrent batch attempt" tests below use SEQUENTIAL calls,
  matching this codebase's established practice (see
  CreatePayoutBatchServiceTests.test_same_ledger_entry_cannot_belong_to_two_payouts
  in finance/tests.py, the precedent this class extends).
"""
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from courses.models import Course
from finance.models import LedgerEntry, Payout
from finance.reconciliation import check_clawback_against_paid_payout, get_instructor_balance
from finance.services import approve_payout, create_and_process_refund, create_payout_batch
from finance.test_refunds import _make_course_instructor, _paid_purchase

User = get_user_model()


def _entry_for(purchase):
    return LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.EARNING)


def _clawback_for(purchase):
    return LedgerEntry.objects.get(purchase=purchase, entry_type=LedgerEntry.EntryType.CLAWBACK)


class PayoutFloorRuleTests(TestCase):
    """net_amount must be strictly positive -- zero and negative are
    both rejected, and nothing gets batched when they are."""

    def setUp(self):
        self.student = User.objects.create_user(username='floor_student', password='password123')
        self.admin = User.objects.create_user(username='floor_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='floor_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Floor Rule Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    @patch('orders.views.client')
    def test_zero_net_batch_rejected_entries_remain_unbatched(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_floor_zero_1", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_floor_zero_1')
        earning = _entry_for(purchase)
        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)
        clawback = _clawback_for(purchase)
        self.assertEqual(earning.net_amount + clawback.net_amount, Decimal('0.00'))  # exact zero-net pairing

        with self.assertRaises(ValueError) as ctx:
            create_payout_batch(
                recipient=self.teacher, ledger_entry_ids=[earning.id, clawback.id],
                period_start='2026-01-01', period_end='2026-01-31',
            )
        self.assertIn('net_amount', str(ctx.exception).lower())

        earning.refresh_from_db()
        clawback.refresh_from_db()
        self.assertIsNone(earning.payout_id)
        self.assertIsNone(clawback.payout_id)
        self.assertEqual(Payout.objects.count(), 0)

    @patch('orders.views.client')
    def test_negative_net_batch_rejected(self, mock_client):
        # A large purchase, already batched and PAID, then fully
        # refunded -- its clawback is now standalone (-350.00) and
        # outweighs a much smaller, unrelated new earning.
        mock_client.payment.refund.return_value = {"id": "rfnd_floor_neg_paid", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_floor_neg_1')
        earning = _entry_for(purchase)
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[earning.id],
            period_start='2025-12-01', period_end='2025-12-31',
        )
        Payout.objects.filter(pk=payout.pk).update(status=Payout.Status.PAID)
        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)
        clawback = _clawback_for(purchase)
        self.assertEqual(clawback.net_amount, Decimal('-350.00'))

        small_purchase = _paid_purchase(self.student, self.course, Decimal('100.00'), razorpay_payment_id='pay_floor_neg_2')
        small_earning = _entry_for(small_purchase)

        with self.assertRaises(ValueError):
            create_payout_batch(
                recipient=self.teacher, ledger_entry_ids=[clawback.id, small_earning.id],
                period_start='2026-01-01', period_end='2026-01-31',
            )
        small_earning.refresh_from_db()
        clawback.refresh_from_db()
        self.assertIsNone(small_earning.payout_id)
        self.assertIsNone(clawback.payout_id)
        self.assertEqual(Payout.objects.count(), 1)  # only the original PAID one

    def test_positive_net_batch_still_works(self):
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_floor_pos_1')
        earning = _entry_for(purchase)
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[earning.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        self.assertEqual(payout.net_amount, Decimal('350.00'))
        self.assertGreater(payout.net_amount, Decimal('0'))


class PayoutPairingRuleTests(TestCase):
    """An EARNING entry with an outstanding, unbatched CLAWBACK cannot be
    batched without also including that CLAWBACK."""

    def setUp(self):
        self.student = User.objects.create_user(username='pair_student', password='password123')
        self.admin = User.objects.create_user(username='pair_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='pair_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Pairing Rule Course', description='x', price=Decimal('1000.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    @patch('orders.views.client')
    def test_full_refund_before_payout_requires_pairing(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_pair_full_1", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_pair_full_1')
        earning = _entry_for(purchase)
        create_and_process_refund(purchase=purchase, amount=Decimal('1000.00'), requested_by=self.admin)
        clawback = _clawback_for(purchase)

        # Earning alone: rejected (would pay out fully-reversed revenue).
        with self.assertRaises(ValueError) as ctx:
            create_payout_batch(
                recipient=self.teacher, ledger_entry_ids=[earning.id],
                period_start='2026-01-01', period_end='2026-01-31',
            )
        self.assertIn('required', str(ctx.exception).lower())
        earning.refresh_from_db()
        self.assertIsNone(earning.payout_id)

        # A second, unrelated healthy purchase makes the paired batch net-positive.
        other_purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_pair_full_2')
        other_earning = _entry_for(other_purchase)

        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[earning.id, clawback.id, other_earning.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        self.assertEqual(payout.net_amount, Decimal('700.00'))  # 700 (fully offset) + 700 - 700 = 700
        earning.refresh_from_db()
        clawback.refresh_from_db()
        self.assertEqual(earning.payout_id, payout.id)
        self.assertEqual(clawback.payout_id, payout.id)

    @patch('orders.views.client')
    def test_partial_refund_before_payout_requires_pairing(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_pair_partial_1", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_pair_partial_1')
        earning = _entry_for(purchase)
        create_and_process_refund(purchase=purchase, amount=Decimal('400.00'), requested_by=self.admin)  # partial
        clawback = _clawback_for(purchase)
        self.assertLess(clawback.net_amount, Decimal('0'))

        with self.assertRaises(ValueError):
            create_payout_batch(
                recipient=self.teacher, ledger_entry_ids=[earning.id],
                period_start='2026-01-01', period_end='2026-01-31',
            )

        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[earning.id, clawback.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        # 1000 * 0.70 = 700 net earning; 400 refunded (40%) -> clawback net = -280.
        self.assertEqual(payout.net_amount, Decimal('420.00'))

    @patch('orders.views.client')
    def test_multiple_partial_refund_clawbacks_all_required(self, mock_client):
        purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_pair_multi_1')
        earning = _entry_for(purchase)

        mock_client.payment.refund.return_value = {"id": "rfnd_pair_multi_a", "status": "processed"}
        create_and_process_refund(purchase=purchase, amount=Decimal('300.00'), requested_by=self.admin)
        mock_client.payment.refund.return_value = {"id": "rfnd_pair_multi_b", "status": "processed"}
        create_and_process_refund(purchase=purchase, amount=Decimal('200.00'), requested_by=self.admin)

        clawbacks = list(LedgerEntry.objects.filter(purchase=purchase, entry_type=LedgerEntry.EntryType.CLAWBACK))
        self.assertEqual(len(clawbacks), 2)

        # Including the earning with only ONE of its two clawbacks is rejected.
        with self.assertRaises(ValueError):
            create_payout_batch(
                recipient=self.teacher, ledger_entry_ids=[earning.id, clawbacks[0].id],
                period_start='2026-01-01', period_end='2026-01-31',
            )

        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[earning.id, clawbacks[0].id, clawbacks[1].id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        # 1000 * 0.70 = 700; 500 of 1000 refunded (50%) -> net clawback -350 total.
        self.assertEqual(payout.net_amount, Decimal('350.00'))

    def test_earning_with_no_clawback_needs_no_pairing(self):
        purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_pair_none_1')
        earning = _entry_for(purchase)
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[earning.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        self.assertEqual(payout.net_amount, Decimal('700.00'))


class ClawbackAfterPaidPayoutSettlementTests(TestCase):
    """The 'clawback after payout' scenario: a refund happens after the
    original EARNING was already batched into a PAID Payout. The
    resulting standalone CLAWBACK can be settled into a LATER, separate
    payout -- and the original PAID payout/EARNING must never be
    touched."""

    def setUp(self):
        self.student = User.objects.create_user(username='paid_settle_student', password='password123')
        self.admin = User.objects.create_user(username='paid_settle_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='paid_settle_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Paid Settlement Course', description='x', price=Decimal('1000.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    @patch('orders.views.client')
    def test_clawback_settled_into_later_payout_original_untouched(self, mock_client):
        purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_paid_settle_1')
        earning = _entry_for(purchase)

        original_payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[earning.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        Payout.objects.filter(pk=original_payout.pk).update(status=Payout.Status.PAID)
        original_totals_before = (original_payout.gross_amount, original_payout.commission_amount, original_payout.net_amount)
        earning_snapshot_before = (earning.gross_amount, earning.commission_rate, earning.commission_amount, earning.net_amount)

        mock_client.payment.refund.return_value = {"id": "rfnd_paid_settle_1", "status": "processed"}
        create_and_process_refund(purchase=purchase, amount=Decimal('1000.00'), requested_by=self.admin)
        clawback = _clawback_for(purchase)
        self.assertIsNone(clawback.payout_id)  # standalone, per Phase 3.5.6 design

        # Reconciliation flags it while unsettled.
        self.assertEqual(len(check_clawback_against_paid_payout()), 1)

        # A new earning for the same instructor -- large enough that
        # settling the clawback still leaves a strictly positive payout
        # (the clawback itself is -700; 700 alone would net to exactly
        # zero, which the floor rule rejects, so this uses 2000).
        new_purchase = _paid_purchase(self.student, self.course, Decimal('2000.00'), razorpay_payment_id='pay_paid_settle_2')
        new_earning = _entry_for(new_purchase)

        new_payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[clawback.id, new_earning.id],
            period_start='2026-02-01', period_end='2026-02-28',
        )
        self.assertEqual(new_payout.net_amount, Decimal('700.00'))  # 1400 new - 700 clawback

        # The ORIGINAL payout and EARNING must be byte-for-byte unchanged.
        original_payout.refresh_from_db()
        earning.refresh_from_db()
        self.assertEqual(original_payout.status, Payout.Status.PAID)
        self.assertEqual(
            (original_payout.gross_amount, original_payout.commission_amount, original_payout.net_amount),
            original_totals_before,
        )
        self.assertEqual(
            (earning.gross_amount, earning.commission_rate, earning.commission_amount, earning.net_amount),
            earning_snapshot_before,
        )
        self.assertEqual(earning.payout_id, original_payout.id)  # still assigned to the original, untouched

        # Reconciliation no longer flags it -- it's been settled.
        self.assertEqual(check_clawback_against_paid_payout(), [])


class MultipleClawbacksSettlementTests(TestCase):
    """Several independent clawbacks (different purchases/refunds) for
    one instructor, settled together or separately."""

    def setUp(self):
        self.student = User.objects.create_user(username='multi_cb_student', password='password123')
        self.admin = User.objects.create_user(username='multi_cb_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='multi_cb_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Multi Clawback Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    @patch('orders.views.client')
    def test_two_independent_full_refunds_both_settleable_together(self, mock_client):
        p1 = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_multi_cb_1')
        p2 = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_multi_cb_2')
        e1, e2 = _entry_for(p1), _entry_for(p2)

        mock_client.payment.refund.return_value = {"id": "rfnd_multi_cb_1", "status": "processed"}
        create_and_process_refund(purchase=p1, amount=Decimal('500.00'), requested_by=self.admin)
        mock_client.payment.refund.return_value = {"id": "rfnd_multi_cb_2", "status": "processed"}
        create_and_process_refund(purchase=p2, amount=Decimal('500.00'), requested_by=self.admin)
        cb1, cb2 = _clawback_for(p1), _clawback_for(p2)

        p3 = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_multi_cb_3')
        e3 = _entry_for(p3)

        payout = create_payout_batch(
            recipient=self.teacher,
            ledger_entry_ids=[e1.id, cb1.id, e2.id, cb2.id, e3.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        self.assertEqual(payout.net_amount, Decimal('350.00'))  # only e3's 350 survives


class MultiInstructorSettlementIsolationTests(TestCase):
    """One instructor's clawback must never affect another's eligibility,
    pairing requirement, or balance."""

    def setUp(self):
        self.student = User.objects.create_user(username='iso_student', password='password123')
        self.admin = User.objects.create_user(username='iso_admin', password='password123', is_staff=True)
        self.teacher_a = User.objects.create_user(username='iso_teacher_a', password='password123', is_teacher=True)
        self.teacher_b = User.objects.create_user(username='iso_teacher_b', password='password123', is_teacher=True)
        self.course_a = Course.objects.create(title='Isolation Course A', description='x', price=Decimal('500.00'))
        self.course_b = Course.objects.create(title='Isolation Course B', description='x', price=Decimal('500.00'))
        _make_course_instructor(self.course_a, self.teacher_a, commission_rate=Decimal('30.00'))
        _make_course_instructor(self.course_b, self.teacher_b, commission_rate=Decimal('40.00'))

    @patch('orders.views.client')
    def test_instructor_b_unaffected_by_instructor_a_clawback(self, mock_client):
        pa = _paid_purchase(self.student, self.course_a, Decimal('500.00'), razorpay_payment_id='pay_iso_a_1')
        mock_client.payment.refund.return_value = {"id": "rfnd_iso_a_1", "status": "processed"}
        create_and_process_refund(purchase=pa, amount=Decimal('500.00'), requested_by=self.admin)

        pb = _paid_purchase(self.student, self.course_b, Decimal('500.00'), razorpay_payment_id='pay_iso_b_1')
        eb = _entry_for(pb)

        # Instructor B's earning batches on its own -- no pairing requirement leaks across instructors.
        payout_b = create_payout_batch(
            recipient=self.teacher_b, ledger_entry_ids=[eb.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        self.assertEqual(payout_b.net_amount, Decimal('300.00'))  # 500 - (500 * 0.40 commission)

        balance_a = get_instructor_balance(self.teacher_a)
        balance_b = get_instructor_balance(self.teacher_b)
        self.assertEqual(balance_a.outstanding_debt, Decimal('350.00'))
        self.assertEqual(balance_b.outstanding_debt, Decimal('0'))
        self.assertEqual(balance_b.available, Decimal('0'))  # already batched


class ConcurrentDuplicateBatchAttemptTests(TestCase):
    """Sequential double-call verification (SQLite cannot exercise real
    PostgreSQL row-locking -- see this module's own docstring)."""

    def setUp(self):
        self.student = User.objects.create_user(username='conc_student', password='password123')
        self.admin = User.objects.create_user(username='conc_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='conc_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Concurrency Course', description='x', price=Decimal('1000.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    @patch('orders.views.client')
    def test_second_call_cannot_reuse_already_settled_clawback(self, mock_client):
        mock_client.payment.refund.return_value = {"id": "rfnd_conc_1", "status": "processed"}
        purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_conc_1')
        earning = _entry_for(purchase)
        create_and_process_refund(purchase=purchase, amount=Decimal('1000.00'), requested_by=self.admin)
        clawback = _clawback_for(purchase)

        other_purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_conc_2')
        other_earning = _entry_for(other_purchase)

        first_payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[earning.id, clawback.id, other_earning.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )

        third_purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_conc_3')
        third_earning = _entry_for(third_purchase)

        # A second, "concurrent" attempt trying to reuse the now-batched
        # clawback (e.g. two admins racing on the same outstanding debt)
        # must fail outright -- nothing partially batches.
        with self.assertRaises(ValueError):
            create_payout_batch(
                recipient=self.teacher, ledger_entry_ids=[clawback.id, third_earning.id],
                period_start='2026-02-01', period_end='2026-02-28',
            )
        third_earning.refresh_from_db()
        self.assertIsNone(third_earning.payout_id)
        clawback.refresh_from_db()
        self.assertEqual(clawback.payout_id, first_payout.id)


class InstructorAvailableBeforeAfterSettlementTests(TestCase):
    """get_instructor_balance().available before and after settlement --
    the exact worked example from the approved audit."""

    def setUp(self):
        self.student = User.objects.create_user(username='avail_student', password='password123')
        self.admin = User.objects.create_user(username='avail_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='avail_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Available Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    @patch('orders.views.client')
    def test_available_nets_against_outstanding_clawback_and_never_negative(self, mock_client):
        # EARNED so far: one already-paid 500 purchase (net 350, paid),
        # one new unbatched 500 purchase (net 350, available) -- mirrors
        # "EARNINGS=10,000, PAID=8,000" proportionally at smaller scale.
        paid_purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_avail_1')
        paid_entry = _entry_for(paid_purchase)
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[paid_entry.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        Payout.objects.filter(pk=payout.pk).update(status=Payout.Status.PAID)

        unbatched_purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_avail_2')
        unbatched_entry = _entry_for(unbatched_purchase)

        balance_before_refund = get_instructor_balance(self.teacher)
        self.assertEqual(balance_before_refund.available, Decimal('350.00'))
        self.assertEqual(balance_before_refund.outstanding_debt, Decimal('0'))

        # Refund the PAID purchase in full -> clawback net = -350, exactly
        # equal to the unbatched 350 available.
        mock_client.payment.refund.return_value = {"id": "rfnd_avail_1", "status": "processed"}
        create_and_process_refund(purchase=paid_purchase, amount=Decimal('500.00'), requested_by=self.admin)

        balance_after_refund = get_instructor_balance(self.teacher)
        self.assertEqual(balance_after_refund.outstanding_debt, Decimal('350.00'))
        # Net eligible position is 350 - 350 = 0 -- floored, never negative.
        self.assertEqual(balance_after_refund.available, Decimal('0.00'))

        # Settling: unbatched_entry has no clawback of its own, so it can
        # be batched alone -- but doing so alongside the standalone
        # clawback nets to exactly zero, which the floor rule rejects
        # (no zero-value payouts). The clawback must wait for MORE future
        # earnings, or the admin pays the 350 alone and leaves the debt
        # outstanding for next time.
        clawback = _clawback_for(paid_purchase)
        with self.assertRaises(ValueError):
            create_payout_batch(
                recipient=self.teacher, ledger_entry_ids=[unbatched_entry.id, clawback.id],
                period_start='2026-02-01', period_end='2026-02-28',
            )

        # A further purchase provides enough headroom to settle with a
        # strictly positive remainder.
        extra_purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_avail_3')
        extra_entry = _entry_for(extra_purchase)
        new_payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[unbatched_entry.id, extra_entry.id, clawback.id],
            period_start='2026-02-01', period_end='2026-02-28',
        )
        self.assertEqual(new_payout.net_amount, Decimal('350.00'))  # 350 + 350 - 350

        balance_after_settlement = get_instructor_balance(self.teacher)
        self.assertEqual(balance_after_settlement.outstanding_debt, Decimal('0'))
        self.assertEqual(balance_after_settlement.available, Decimal('0'))  # everything now batched


class ReconciliationSettledClawbackNoLongerFlaggedTests(TestCase):
    """check_clawback_against_paid_payout() must stop reporting a
    clawback once it has been settled into any payout (DRAFT/APPROVED/
    PAID), fixing the stale-false-positive gap identified in the audit."""

    def setUp(self):
        self.student = User.objects.create_user(username='recon_settle_student', password='password123')
        self.admin = User.objects.create_user(username='recon_settle_admin', password='password123', is_staff=True)
        self.teacher = User.objects.create_user(username='recon_settle_teacher', password='password123', is_teacher=True)
        self.course = Course.objects.create(title='Reconciliation Settlement Course', description='x', price=Decimal('500.00'))
        self.instructor = _make_course_instructor(self.course, self.teacher, commission_rate=Decimal('30.00'))

    @patch('orders.views.client')
    def test_settled_clawback_not_flagged_unsettled_still_flagged(self, mock_client):
        purchase = _paid_purchase(self.student, self.course, Decimal('500.00'), razorpay_payment_id='pay_recon_settle_1')
        earning = _entry_for(purchase)
        payout = create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[earning.id],
            period_start='2026-01-01', period_end='2026-01-31',
        )
        Payout.objects.filter(pk=payout.pk).update(status=Payout.Status.PAID)

        mock_client.payment.refund.return_value = {"id": "rfnd_recon_settle_1", "status": "processed"}
        create_and_process_refund(purchase=purchase, amount=Decimal('500.00'), requested_by=self.admin)
        clawback = _clawback_for(purchase)

        # Unsettled: flagged.
        issues = check_clawback_against_paid_payout()
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].source_id, clawback.id)

        # Settle it into a new payout -- needs strictly positive headroom
        # (clawback is -350; a matching 500-price/350-net purchase alone
        # would net to exactly zero, which the floor rule rejects, so
        # this uses a larger amount).
        new_purchase = _paid_purchase(self.student, self.course, Decimal('1000.00'), razorpay_payment_id='pay_recon_settle_2')
        new_earning = _entry_for(new_purchase)
        create_payout_batch(
            recipient=self.teacher, ledger_entry_ids=[clawback.id, new_earning.id],
            period_start='2026-02-01', period_end='2026-02-28',
        )

        # Settled: no longer flagged, even though the ORIGINAL payout is still PAID.
        self.assertEqual(check_clawback_against_paid_payout(), [])
