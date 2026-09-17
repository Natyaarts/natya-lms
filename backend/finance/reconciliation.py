"""
Phase 3.5.7: Finance Reconciliation & Production Hardening.

A dedicated, READ-ONLY reconciliation service. Every function in this
module only ever queries existing data -- nothing here creates, updates,
or deletes a Purchase/Order/SubscriptionPayment/LedgerEntry/Invoice/
Refund/Payout row. This is deliberate and absolute: the approved brief's
safety rules explicitly forbid automatically creating missing financial
records or automatically modifying historical ones. This module's whole
job is DETECT -> REPORT, never DETECT -> SILENTLY FIX.

No new Django model backs a "reconciliation issue" -- each run computes
its findings fresh, in memory, as plain dataclasses (ReconciliationIssue/
ReconciliationReport below), and nothing is ever persisted. This keeps
this phase migration-free (confirmed via `manage.py makemigrations
--check`) and means a reconciliation result can never itself go stale or
drift from reality the way a cached/stored finding could.

Architecture: one check_*() function per objective (A-O from the approved
brief), each returning a list[ReconciliationIssue]. run_reconciliation()
orchestrates all of them and applies the admin API's filters. Two derived
services sit alongside: get_instructor_balance() (Objective 3 -- an
instructor's EARNED/CLAWED_BACK/PAID/AVAILABLE/OUTSTANDING_DEBT position,
computed purely from existing immutable LedgerEntry rows) and
reconcile_payout() (Objective 5 -- the same payout-total checks
check_payout_total_mismatch() runs across every Payout, exposed here for
inspecting one Payout in isolation).

Key non-obvious design decision this module depends on throughout:
Objectives A/C/E ("successful transaction without LedgerEntry") are NOT
naive existence checks. finance/services.py's _create_earning_entry is a
documented, intentional safe no-op when a course has no is_primary
CourseInstructor, or that instructor has no commission_rate configured
(see that function's own docstring) -- that is CORRECT, existing
behavior, not a bug. Every missing-ledger-entry check below re-derives
instructor eligibility (the exact same is_primary + commission_rate not
null rule _resolve_primary_instructor uses) and only ever reports an
issue when an eligible instructor DID exist and the entry is still
missing -- i.e. the one class of gap the broad `except Exception` in
_create_earning_entry (see finance/services.py) could theoretically
produce (Objective 13's "successful payment/access BUT missing ledger"
concern). A purchase/order/subscription with no eligible instructor is
never reported as an issue at all. Invoice checks (B/D/F) have no such
nuance -- create_invoice_for_* is unconditional for any successful
transaction, so any gap there is always a genuine finding.

Payout interaction (Objective 4): as of Phase 3.5.8, the payout system
(finance/services.py's get_eligible_ledger_entries_queryset/
create_payout_batch) CAN settle an outstanding CLAWBACK into a future
payout batch (an explicit, admin-selected, floor-and-pairing-rule-guarded
mixed EARNING+CLAWBACK selection -- see create_payout_batch's own
docstring; still no automatic netting, still no money ever moves in
either direction). check_clawback_against_paid_payout() below now only
warns about a clawback that is STILL unsettled (payout IS NULL on the
clawback itself) -- once settled, it correctly stops appearing here, and
get_instructor_balance()'s outstanding_debt/outstanding_debt_from_paid_payouts/
available fields all shrink/adjust accordingly, purely as a side effect
of the same "unbatched" filter every other eligibility check already
uses. Nothing here (in finance/reconciliation.py) performs or triggers
that settlement -- this module remains read-only; create_payout_batch is
the only code that ever sets a CLAWBACK row's `payout` FK.

Structural notes:
- "No LedgerEntry is assigned to multiple payouts" (part of Objective 5)
  is not implemented as a check: LedgerEntry.payout is a single-valued
  ForeignKey, so a row can never simultaneously belong to two Payouts --
  this is a schema-level guarantee, not something that can drift, and is
  documented here rather than checked for.
- Every check function accepts an optional pre-filtered `queryset` (used
  by run_reconciliation() to apply the admin API's date-range filter to
  "which transactions get examined", not to a meaningless "when was this
  issue detected" value, since nothing here is persisted).
- Never includes a raw Razorpay webhook payload, a Razorpay
  secret/credential, or any field beyond what the existing admin-only
  finance serializers already expose (see finance/serializers.py's own
  "never expose Razorpay secrets/webhook payloads" precedent, upheld
  identically here).
"""
import dataclasses
import datetime
import logging
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from django.db.models import Count, QuerySet, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result format (Objective 2)
# ---------------------------------------------------------------------------

class Severity:
    INFO = 'INFO'
    WARNING = 'WARNING'
    CRITICAL = 'CRITICAL'
    CHOICES = (INFO, WARNING, CRITICAL)


class SourceType:
    PURCHASE = 'PURCHASE'
    ORDER = 'ORDER'
    ORDER_ITEM = 'ORDER_ITEM'
    SUBSCRIPTION_PAYMENT = 'SUBSCRIPTION_PAYMENT'
    LEDGER_ENTRY = 'LEDGER_ENTRY'
    INVOICE = 'INVOICE'
    PAYOUT = 'PAYOUT'
    REFUND = 'REFUND'
    INSTRUCTOR = 'INSTRUCTOR'


class IssueType:
    PURCHASE_MISSING_LEDGER_ENTRY = 'PURCHASE_MISSING_LEDGER_ENTRY'
    PURCHASE_MISSING_INVOICE = 'PURCHASE_MISSING_INVOICE'
    ORDER_ITEM_MISSING_LEDGER_ENTRY = 'ORDER_ITEM_MISSING_LEDGER_ENTRY'
    ORDER_MISSING_INVOICE = 'ORDER_MISSING_INVOICE'
    SUBSCRIPTION_PAYMENT_MISSING_LEDGER_ENTRY = 'SUBSCRIPTION_PAYMENT_MISSING_LEDGER_ENTRY'
    SUBSCRIPTION_PAYMENT_MISSING_INVOICE = 'SUBSCRIPTION_PAYMENT_MISSING_INVOICE'
    REFUND_MISSING_CLAWBACK = 'REFUND_MISSING_CLAWBACK'
    DUPLICATE_EARNING_ENTRY = 'DUPLICATE_EARNING_ENTRY'
    DUPLICATE_INVOICE = 'DUPLICATE_INVOICE'
    LEDGER_ENTRY_INVALID_SOURCE = 'LEDGER_ENTRY_INVALID_SOURCE'
    LEDGER_ENTRY_CALCULATION_MISMATCH = 'LEDGER_ENTRY_CALCULATION_MISMATCH'
    PAYOUT_TOTAL_MISMATCH = 'PAYOUT_TOTAL_MISMATCH'
    PAYOUT_INELIGIBLE_ENTRY = 'PAYOUT_INELIGIBLE_ENTRY'
    PAYOUT_RECIPIENT_MISMATCH = 'PAYOUT_RECIPIENT_MISMATCH'
    PAYOUT_CURRENCY_INCONSISTENT = 'PAYOUT_CURRENCY_INCONSISTENT'
    PAYOUT_NEGATIVE_VALUE = 'PAYOUT_NEGATIVE_VALUE'
    REFUND_EXCEEDS_SOURCE_AMOUNT = 'REFUND_EXCEEDS_SOURCE_AMOUNT'
    CLAWBACK_AGAINST_PAID_PAYOUT = 'CLAWBACK_AGAINST_PAID_PAYOUT'
    INSTRUCTOR_NEGATIVE_BALANCE = 'INSTRUCTOR_NEGATIVE_BALANCE'


@dataclasses.dataclass
class ReconciliationIssue:
    issue_type: str
    severity: str
    source_type: str
    source_id: int
    message: str
    related_object_id: Optional[int] = None
    expected_value: Optional[str] = None
    actual_value: Optional[str] = None
    instructor_user_id: Optional[int] = None
    detected_at: datetime.datetime = dataclasses.field(default_factory=timezone.now)

    def to_dict(self):
        return {
            'issue_type': self.issue_type,
            'severity': self.severity,
            'source_type': self.source_type,
            'source_id': self.source_id,
            'related_object_id': self.related_object_id,
            'message': self.message,
            'expected_value': self.expected_value,
            'actual_value': self.actual_value,
            'instructor_user_id': self.instructor_user_id,
            'detected_at': self.detected_at.isoformat(),
        }


@dataclasses.dataclass
class ReconciliationReport:
    issues: List[ReconciliationIssue]
    generated_at: datetime.datetime
    healthy_checks: int
    total_checked: int

    @property
    def critical_count(self):
        return sum(1 for i in self.issues if i.severity == Severity.CRITICAL)

    @property
    def warning_count(self):
        return sum(1 for i in self.issues if i.severity == Severity.WARNING)

    @property
    def info_count(self):
        return sum(1 for i in self.issues if i.severity == Severity.INFO)

    def to_summary_dict(self):
        return {
            'generated_at': self.generated_at.isoformat(),
            'total_issues': len(self.issues),
            'critical': self.critical_count,
            'warnings': self.warning_count,
            'info': self.info_count,
            'healthy_checks': self.healthy_checks,
            'total_checked': self.total_checked,
        }


# ---------------------------------------------------------------------------
# Objectives A/C/E + B/D/F: missing LedgerEntry / missing Invoice
# ---------------------------------------------------------------------------

def _eligible_course_ids(course_ids):
    """Bulk-resolve which of these course ids currently have an eligible
    primary instructor (is_primary=True, commission_rate configured) --
    the exact same rule finance/services.py's _resolve_primary_instructor
    applies at earning-creation time. One query regardless of how many
    course ids are passed."""
    from courses.models import CourseInstructor
    if not course_ids:
        return set()
    return set(
        CourseInstructor.objects.filter(course_id__in=course_ids, is_primary=True, commission_rate__isnull=False)
        .values_list('course_id', flat=True)
    )


def check_purchase_missing_ledger_entry(queryset: Optional[QuerySet] = None) -> List[ReconciliationIssue]:
    """Objective A. Only reports a Purchase whose course DID have an
    eligible instructor at check time -- a Purchase for a course with no
    CourseInstructor/no commission_rate configured is a correct,
    documented safe no-op (see this module's own docstring), never an
    issue."""
    from orders.models import Purchase
    from .models import LedgerEntry

    qs = queryset if queryset is not None else Purchase.objects.filter(status=Purchase.Status.SUCCESS)
    has_entry_ids = set(
        LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, purchase__isnull=False)
        .values_list('purchase_id', flat=True)
    )
    missing = list(qs.exclude(id__in=has_entry_ids).only('id', 'course_id'))
    if not missing:
        return []

    eligible_course_ids = _eligible_course_ids({p.course_id for p in missing})

    issues = []
    for purchase in missing:
        if purchase.course_id not in eligible_course_ids:
            continue
        issues.append(ReconciliationIssue(
            issue_type=IssueType.PURCHASE_MISSING_LEDGER_ENTRY,
            severity=Severity.CRITICAL,
            source_type=SourceType.PURCHASE,
            source_id=purchase.id,
            message=f"Purchase #{purchase.id} is SUCCESS with an eligible instructor configured, but has no EARNING LedgerEntry.",
            expected_value="1 EARNING LedgerEntry",
            actual_value="0 LedgerEntry",
        ))
    return issues


def check_purchase_missing_invoice(queryset: Optional[QuerySet] = None) -> List[ReconciliationIssue]:
    """Objective B. Unconditional -- Invoice creation has no eligibility
    concept (unlike LedgerEntry), so any successful Purchase without one
    is always a genuine gap."""
    from orders.models import Purchase
    from .models import Invoice

    qs = queryset if queryset is not None else Purchase.objects.filter(status=Purchase.Status.SUCCESS)
    has_invoice_ids = set(Invoice.objects.filter(purchase__isnull=False).values_list('purchase_id', flat=True))
    missing = qs.exclude(id__in=has_invoice_ids).only('id')
    return [
        ReconciliationIssue(
            issue_type=IssueType.PURCHASE_MISSING_INVOICE,
            severity=Severity.WARNING,
            source_type=SourceType.PURCHASE,
            source_id=p.id,
            message=f"Purchase #{p.id} is SUCCESS but has no Invoice.",
            expected_value="1 Invoice",
            actual_value="0 Invoice",
        )
        for p in missing
    ]


def check_order_missing_ledger_entry(queryset: Optional[QuerySet] = None) -> List[ReconciliationIssue]:
    """Objective C. Per-OrderItem, mirroring create_earning_entry_for_order_item's
    own granularity. A COURSE item is eligible via its own course; a
    BUNDLE item is eligible only when it resolves to exactly one course
    (the same _resolve_primary_instructor rule fulfill_order's real hook
    already applies) with an eligible instructor."""
    from orders.models import Order, OrderItem
    from .models import LedgerEntry

    order_qs = queryset if queryset is not None else Order.objects.filter(status=Order.Status.PAID)
    order_ids = list(order_qs.values_list('id', flat=True))
    if not order_ids:
        return []

    items = list(
        OrderItem.objects.filter(order_id__in=order_ids)
        .select_related('bundle')
        .only('id', 'order_id', 'item_type', 'course_id', 'bundle_id')
    )
    has_entry_item_ids = set(
        LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, order_item_id__in=[i.id for i in items])
        .values_list('order_item_id', flat=True)
    )
    missing_items = [i for i in items if i.id not in has_entry_item_ids]
    if not missing_items:
        return []

    course_item_ids = {i.course_id for i in missing_items if i.item_type == 'COURSE' and i.course_id}
    eligible_course_ids = _eligible_course_ids(course_item_ids)

    issues = []
    for item in missing_items:
        if item.item_type == 'COURSE':
            if item.course_id not in eligible_course_ids:
                continue
        else:  # BUNDLE
            bundle_courses = list(item.bundle.courses.all()) if item.bundle_id else []
            if len(bundle_courses) != 1:
                continue
            if not _eligible_course_ids({bundle_courses[0].id}):
                continue
        issues.append(ReconciliationIssue(
            issue_type=IssueType.ORDER_ITEM_MISSING_LEDGER_ENTRY,
            severity=Severity.CRITICAL,
            source_type=SourceType.ORDER_ITEM,
            source_id=item.id,
            related_object_id=item.order_id,
            message=f"OrderItem #{item.id} (Order #{item.order_id}) is part of a PAID order with an eligible instructor configured, but has no EARNING LedgerEntry.",
            expected_value="1 EARNING LedgerEntry",
            actual_value="0 LedgerEntry",
        ))
    return issues


def check_order_missing_invoice(queryset: Optional[QuerySet] = None) -> List[ReconciliationIssue]:
    """Objective D. Unconditional, one Invoice per Order (not per item)."""
    from orders.models import Order
    from .models import Invoice

    qs = queryset if queryset is not None else Order.objects.filter(status=Order.Status.PAID)
    has_invoice_ids = set(Invoice.objects.filter(order__isnull=False).values_list('order_id', flat=True))
    missing = qs.exclude(id__in=has_invoice_ids).only('id')
    return [
        ReconciliationIssue(
            issue_type=IssueType.ORDER_MISSING_INVOICE,
            severity=Severity.WARNING,
            source_type=SourceType.ORDER,
            source_id=o.id,
            message=f"Order #{o.id} is PAID but has no Invoice.",
            expected_value="1 Invoice",
            actual_value="0 Invoice",
        )
        for o in missing
    ]


def check_subscription_payment_missing_ledger_entry(queryset: Optional[QuerySet] = None) -> List[ReconciliationIssue]:
    """Objective E. Eligible only when the plan resolves to exactly one
    course with an eligible instructor -- same rule
    create_earning_entry_for_subscription_payment's real hook applies."""
    from orders.models import SubscriptionPayment
    from .models import LedgerEntry

    qs = queryset if queryset is not None else SubscriptionPayment.objects.filter(status=SubscriptionPayment.Status.SUCCESS)
    has_entry_ids = set(
        LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, subscription_payment__isnull=False)
        .values_list('subscription_payment_id', flat=True)
    )
    missing = list(qs.exclude(id__in=has_entry_ids).select_related('subscription__plan'))
    issues = []
    for sp in missing:
        courses = list(sp.subscription.plan.courses.all())
        if len(courses) != 1:
            continue
        if not _eligible_course_ids({courses[0].id}):
            continue
        issues.append(ReconciliationIssue(
            issue_type=IssueType.SUBSCRIPTION_PAYMENT_MISSING_LEDGER_ENTRY,
            severity=Severity.CRITICAL,
            source_type=SourceType.SUBSCRIPTION_PAYMENT,
            source_id=sp.id,
            message=f"SubscriptionPayment #{sp.id} is SUCCESS with an eligible instructor configured, but has no EARNING LedgerEntry.",
            expected_value="1 EARNING LedgerEntry",
            actual_value="0 LedgerEntry",
        ))
    return issues


def check_subscription_payment_missing_invoice(queryset: Optional[QuerySet] = None) -> List[ReconciliationIssue]:
    """Objective F. Unconditional."""
    from orders.models import SubscriptionPayment
    from .models import Invoice

    qs = queryset if queryset is not None else SubscriptionPayment.objects.filter(status=SubscriptionPayment.Status.SUCCESS)
    has_invoice_ids = set(Invoice.objects.filter(subscription_payment__isnull=False).values_list('subscription_payment_id', flat=True))
    missing = qs.exclude(id__in=has_invoice_ids).only('id')
    return [
        ReconciliationIssue(
            issue_type=IssueType.SUBSCRIPTION_PAYMENT_MISSING_INVOICE,
            severity=Severity.WARNING,
            source_type=SourceType.SUBSCRIPTION_PAYMENT,
            source_id=sp.id,
            message=f"SubscriptionPayment #{sp.id} is SUCCESS but has no Invoice.",
            expected_value="1 Invoice",
            actual_value="0 Invoice",
        )
        for sp in missing
    ]


# ---------------------------------------------------------------------------
# Objective G: refund success without a corresponding clawback
# ---------------------------------------------------------------------------

def check_refund_missing_clawback(queryset: Optional[QuerySet] = None) -> List[ReconciliationIssue]:
    """
    For every SUCCESS refund, checks that a CLAWBACK LedgerEntry linked to
    THIS refund (via the `refund` FK) exists for every EARNING entry its
    source is attributable to. Skips entirely when no EARNING entry
    exists for the source at all -- nothing to claw back, the same "safe
    no-op" precedent _create_clawback_for_refund itself already
    documents. For a multi-item Order, checks per OrderItem (a partial
    multi-item refund is not supported at all -- see finance/services.py
    -- so this only meaningfully fires for a full-order refund, where
    every item's EARNING, if any, should have a matching CLAWBACK).
    """
    from .models import LedgerEntry, Refund

    qs = queryset if queryset is not None else Refund.objects.filter(status=Refund.Status.SUCCESS)
    issues = []
    for refund in qs.select_related('purchase', 'order', 'subscription_payment'):
        if refund.purchase_id:
            entry = LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, purchase_id=refund.purchase_id).first()
            if entry is None:
                continue
            if not LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.CLAWBACK, refund_id=refund.id, related_entry_id=entry.id).exists():
                issues.append(_missing_clawback_issue(refund, entry))

        elif refund.subscription_payment_id:
            entry = LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, subscription_payment_id=refund.subscription_payment_id).first()
            if entry is None:
                continue
            if not LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.CLAWBACK, refund_id=refund.id, related_entry_id=entry.id).exists():
                issues.append(_missing_clawback_issue(refund, entry))

        elif refund.order_id:
            entries = list(LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, order_item__order_id=refund.order_id))
            for entry in entries:
                if not LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.CLAWBACK, refund_id=refund.id, related_entry_id=entry.id).exists():
                    issues.append(_missing_clawback_issue(refund, entry))
    return issues


def _missing_clawback_issue(refund, entry) -> ReconciliationIssue:
    return ReconciliationIssue(
        issue_type=IssueType.REFUND_MISSING_CLAWBACK,
        severity=Severity.CRITICAL,
        source_type=SourceType.REFUND,
        source_id=refund.id,
        related_object_id=entry.id,
        instructor_user_id=entry.course_instructor.user_id if entry.course_instructor_id else None,
        message=f"Refund #{refund.id} is SUCCESS; EARNING LedgerEntry #{entry.id} exists for its source, but no CLAWBACK entry linked to this refund reverses it.",
        expected_value="1 CLAWBACK LedgerEntry linked to this refund",
        actual_value="0",
    )


# ---------------------------------------------------------------------------
# Objective H: duplicate financial records
# ---------------------------------------------------------------------------

def check_duplicate_financial_records() -> List[ReconciliationIssue]:
    """
    Defensive-only: ledgerentry_unique_purchase_entry_type (and its
    order_item/subscription_payment siblings) and invoice_unique_purchase
    (and its order/subscription_payment siblings) already enforce "at
    most one EARNING/Invoice per source" at the DB constraint level (see
    finance/models.py) -- a true duplicate should be structurally
    impossible in a correctly-migrated database. This exists purely as a
    belt-and-suspenders check (e.g. against a constraint being bypassed
    via raw SQL, or a future migration drift), not because duplicates are
    expected.
    """
    from .models import Invoice, LedgerEntry

    issues = []
    for field in ('purchase', 'order_item', 'subscription_payment'):
        dupes = (
            LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, **{f'{field}__isnull': False})
            .values(field).annotate(c=Count('id')).filter(c__gt=1)
        )
        for row in dupes:
            issues.append(ReconciliationIssue(
                issue_type=IssueType.DUPLICATE_EARNING_ENTRY,
                severity=Severity.CRITICAL,
                source_type=SourceType.LEDGER_ENTRY,
                source_id=row[field],
                message=f"{row['c']} EARNING LedgerEntry rows exist for {field}={row[field]} (at most 1 expected).",
                expected_value="1",
                actual_value=str(row['c']),
            ))

    for field in ('purchase', 'order', 'subscription_payment'):
        dupes = (
            Invoice.objects.filter(**{f'{field}__isnull': False})
            .values(field).annotate(c=Count('id')).filter(c__gt=1)
        )
        for row in dupes:
            issues.append(ReconciliationIssue(
                issue_type=IssueType.DUPLICATE_INVOICE,
                severity=Severity.CRITICAL,
                source_type=SourceType.INVOICE,
                source_id=row[field],
                message=f"{row['c']} Invoice rows exist for {field}={row[field]} (at most 1 expected).",
                expected_value="1",
                actual_value=str(row['c']),
            ))
    return issues


# ---------------------------------------------------------------------------
# Objective I: LedgerEntry referencing a missing/invalid source
# ---------------------------------------------------------------------------

def check_ledger_entry_invalid_source() -> List[ReconciliationIssue]:
    """
    Two sub-checks:
    1. A LedgerEntry with all three source FKs null -- should be
       structurally impossible (ledgerentry_exactly_one_source
       CheckConstraint), checked defensively anyway.
    2. An EARNING entry whose course_instructor's own `course` doesn't
       match its source transaction's actual course (a data-integrity
       cross-check no DB constraint currently enforces). Only checked for
       a COURSE-granularity source (Purchase, or a COURSE-type OrderItem)
       -- a BUNDLE OrderItem or a SubscriptionPayment can legitimately
       span multiple courses, so there is no single "the" course to
       compare against for those.
    """
    from .models import LedgerEntry

    issues = []
    orphaned = LedgerEntry.objects.filter(purchase__isnull=True, order_item__isnull=True, subscription_payment__isnull=True)
    for e in orphaned:
        issues.append(ReconciliationIssue(
            issue_type=IssueType.LEDGER_ENTRY_INVALID_SOURCE,
            severity=Severity.CRITICAL,
            source_type=SourceType.LEDGER_ENTRY,
            source_id=e.id,
            message=f"LedgerEntry #{e.id} has no source (purchase/order_item/subscription_payment are all null).",
        ))

    earning_entries = (
        LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING)
        .select_related('course_instructor', 'purchase', 'order_item')
    )
    for e in earning_entries:
        expected_course_id = None
        if e.purchase_id:
            expected_course_id = e.purchase.course_id
        elif e.order_item_id and e.order_item.item_type == 'COURSE':
            expected_course_id = e.order_item.course_id

        if expected_course_id is not None and e.course_instructor.course_id != expected_course_id:
            issues.append(ReconciliationIssue(
                issue_type=IssueType.LEDGER_ENTRY_INVALID_SOURCE,
                severity=Severity.CRITICAL,
                source_type=SourceType.LEDGER_ENTRY,
                source_id=e.id,
                related_object_id=e.course_instructor_id,
                instructor_user_id=e.course_instructor.user_id,
                message=f"LedgerEntry #{e.id}'s course_instructor is attributed to course #{e.course_instructor.course_id}, but its source transaction's course is #{expected_course_id}.",
                expected_value=f"course_id={expected_course_id}",
                actual_value=f"course_id={e.course_instructor.course_id}",
            ))
    return issues


# ---------------------------------------------------------------------------
# Objective J: gross/commission/net calculation consistency
# ---------------------------------------------------------------------------

def check_ledger_entry_calculation_mismatch() -> List[ReconciliationIssue]:
    """
    EARNING: commission_amount must equal round(gross_amount * commission_rate / 100)
    (the exact formula _create_earning_entry uses) and net_amount must
    equal gross_amount - commission_amount. CLAWBACK/ADJUSTMENT: only the
    net = gross - commission identity is checked (their commission_rate
    is a snapshot of the ORIGINAL entry's rate, not independently
    re-derived from a fresh gross*rate calculation -- see
    _apply_clawback_to_entry).
    """
    from .models import LedgerEntry

    issues = []
    for e in LedgerEntry.objects.all():
        if e.entry_type == LedgerEntry.EntryType.EARNING:
            expected_commission = (e.gross_amount * e.commission_rate / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            if e.commission_amount != expected_commission:
                issues.append(ReconciliationIssue(
                    issue_type=IssueType.LEDGER_ENTRY_CALCULATION_MISMATCH,
                    severity=Severity.CRITICAL,
                    source_type=SourceType.LEDGER_ENTRY,
                    source_id=e.id,
                    instructor_user_id=e.course_instructor.user_id if e.course_instructor_id else None,
                    message=f"LedgerEntry #{e.id}: commission_amount {e.commission_amount} does not match gross_amount ({e.gross_amount}) * commission_rate ({e.commission_rate}%).",
                    expected_value=str(expected_commission),
                    actual_value=str(e.commission_amount),
                ))
        expected_net = e.gross_amount - e.commission_amount
        if e.net_amount != expected_net:
            issues.append(ReconciliationIssue(
                issue_type=IssueType.LEDGER_ENTRY_CALCULATION_MISMATCH,
                severity=Severity.CRITICAL,
                source_type=SourceType.LEDGER_ENTRY,
                source_id=e.id,
                instructor_user_id=e.course_instructor.user_id if e.course_instructor_id else None,
                message=f"LedgerEntry #{e.id}: net_amount {e.net_amount} does not equal gross_amount ({e.gross_amount}) - commission_amount ({e.commission_amount}).",
                expected_value=str(expected_net),
                actual_value=str(e.net_amount),
            ))
    return issues


# ---------------------------------------------------------------------------
# Objective K/L/M: Payout reconciliation
# ---------------------------------------------------------------------------

def reconcile_payout(payout) -> List[ReconciliationIssue]:
    """
    Objective 5. All the Payout-level checks for ONE Payout instance --
    also the function run_reconciliation()/check_payout_total_mismatch()
    call, once per Payout, for the platform-wide scan. Never modifies
    `payout` or any of its assigned LedgerEntry rows.

    "No LedgerEntry is assigned to multiple payouts" is NOT checked here:
    LedgerEntry.payout is a single-valued ForeignKey, so a row can never
    reference two Payouts at once -- a schema-level guarantee, not
    something reconciliation needs to verify.
    """
    from .models import LedgerEntry

    issues = []
    agg = payout.entries.aggregate(gross=Sum('gross_amount'), commission=Sum('commission_amount'), net=Sum('net_amount'))
    actual_gross = agg['gross'] or Decimal('0')
    actual_commission = agg['commission'] or Decimal('0')
    actual_net = agg['net'] or Decimal('0')

    if actual_gross != payout.gross_amount or actual_commission != payout.commission_amount or actual_net != payout.net_amount:
        issues.append(ReconciliationIssue(
            issue_type=IssueType.PAYOUT_TOTAL_MISMATCH,
            severity=Severity.CRITICAL,
            source_type=SourceType.PAYOUT,
            source_id=payout.id,
            instructor_user_id=payout.recipient_id,
            message=(
                f"Payout #{payout.id} stored totals (gross={payout.gross_amount}, commission={payout.commission_amount}, "
                f"net={payout.net_amount}) do not match the sum of its assigned LedgerEntry rows "
                f"(gross={actual_gross}, commission={actual_commission}, net={actual_net})."
            ),
            expected_value=f"gross={actual_gross}, commission={actual_commission}, net={actual_net}",
            actual_value=f"gross={payout.gross_amount}, commission={payout.commission_amount}, net={payout.net_amount}",
        ))

    entries = list(payout.entries.select_related('course_instructor').all())
    for entry in entries:
        if entry.entry_type != LedgerEntry.EntryType.EARNING:
            issues.append(ReconciliationIssue(
                issue_type=IssueType.PAYOUT_INELIGIBLE_ENTRY,
                severity=Severity.CRITICAL,
                source_type=SourceType.PAYOUT,
                source_id=payout.id,
                related_object_id=entry.id,
                message=f"Payout #{payout.id} includes LedgerEntry #{entry.id} of type {entry.entry_type}; only EARNING entries should ever be batched.",
            ))
        elif entry.course_instructor.user_id != payout.recipient_id:
            issues.append(ReconciliationIssue(
                issue_type=IssueType.PAYOUT_RECIPIENT_MISMATCH,
                severity=Severity.CRITICAL,
                source_type=SourceType.PAYOUT,
                source_id=payout.id,
                related_object_id=entry.id,
                message=f"Payout #{payout.id}'s recipient is user #{payout.recipient_id}, but LedgerEntry #{entry.id} belongs to instructor user #{entry.course_instructor.user_id}.",
            ))

    currencies = {entry.currency for entry in entries}
    if len(currencies) > 1:
        issues.append(ReconciliationIssue(
            issue_type=IssueType.PAYOUT_CURRENCY_INCONSISTENT,
            severity=Severity.CRITICAL,
            source_type=SourceType.PAYOUT,
            source_id=payout.id,
            message=f"Payout #{payout.id}'s assigned LedgerEntry rows span multiple currencies: {sorted(currencies)}.",
        ))

    if payout.gross_amount < 0 or payout.commission_amount < 0 or payout.net_amount < 0:
        issues.append(ReconciliationIssue(
            issue_type=IssueType.PAYOUT_NEGATIVE_VALUE,
            severity=Severity.CRITICAL,
            source_type=SourceType.PAYOUT,
            source_id=payout.id,
            message=f"Payout #{payout.id} has a negative amount (gross={payout.gross_amount}, commission={payout.commission_amount}, net={payout.net_amount}).",
        ))

    return issues


def check_payout_total_mismatch(queryset: Optional[QuerySet] = None) -> List[ReconciliationIssue]:
    """Platform-wide wrapper around reconcile_payout() -- covers
    Objectives K, L and M together (mismatched totals, ineligible/
    misattributed entries, currency drift, and negative values all come
    from the same per-Payout scan)."""
    from .models import Payout

    qs = queryset if queryset is not None else Payout.objects.all()
    issues = []
    for payout in qs:
        issues.extend(reconcile_payout(payout))
    return issues


# ---------------------------------------------------------------------------
# Objectives N/O: refund amount vs. refundable source amount
# ---------------------------------------------------------------------------

def check_refund_exceeds_source_amount(queryset: Optional[QuerySet] = None) -> List[ReconciliationIssue]:
    """
    Covers both N (a single refund whose amount alone exceeds the
    source) and O (several partial refunds whose CUMULATIVE amount
    exceeds the source) with one query shape -- the underlying
    invariant is identical either way: sum of REQUESTED+PROCESSING+
    SUCCESS refunds for a source must never exceed that source's total
    amount. This re-verifies, as a drift/sanity check, exactly what
    create_and_process_refund's own locked validation already enforces
    at creation time (see finance/services.py) -- it does not change
    that validation.
    """
    from .models import Refund

    qs = queryset if queryset is not None else Refund.objects.filter(
        status__in=[Refund.Status.REQUESTED, Refund.Status.PROCESSING, Refund.Status.SUCCESS],
    )
    issues = []
    seen_sources = set()
    for refund in qs.select_related('purchase', 'order', 'subscription_payment'):
        if refund.purchase_id:
            key = ('purchase', refund.purchase_id)
            total = refund.purchase.amount if refund.purchase else None
        elif refund.order_id:
            key = ('order', refund.order_id)
            total = refund.order.total_amount if refund.order else None
        elif refund.subscription_payment_id:
            key = ('subscription_payment', refund.subscription_payment_id)
            total = refund.subscription_payment.amount if refund.subscription_payment else None
        else:
            continue

        if key in seen_sources or total is None:
            continue
        seen_sources.add(key)

        source_field, source_id = key
        cumulative = (
            Refund.objects.filter(status__in=[Refund.Status.REQUESTED, Refund.Status.PROCESSING, Refund.Status.SUCCESS], **{source_field: source_id})
            .aggregate(t=Sum('amount'))['t'] or Decimal('0')
        )
        if cumulative > total:
            issues.append(ReconciliationIssue(
                issue_type=IssueType.REFUND_EXCEEDS_SOURCE_AMOUNT,
                severity=Severity.CRITICAL,
                source_type=source_field.upper(),
                source_id=source_id,
                message=f"Cumulative refunded amount ({cumulative}) for {source_field} #{source_id} exceeds its total amount ({total}).",
                expected_value=f"<= {total}",
                actual_value=str(cumulative),
            ))
    return issues


# ---------------------------------------------------------------------------
# Objective 4: clawback vs. payout state
# ---------------------------------------------------------------------------

def check_clawback_against_paid_payout(queryset: Optional[QuerySet] = None) -> List[ReconciliationIssue]:
    """
    Surfaces (WARNING, not CRITICAL -- this is expected, documented
    behavior per Phase 3.5.6, not a bug) every STILL-UNSETTLED CLAWBACK
    LedgerEntry whose original EARNING entry was already batched into a
    PAID Payout. This is exactly the "outstanding negative financial
    adjustment/debt record" Phase 3.5.6 created by design.

    Phase 3.5.8 correction: filtered to `payout__isnull=True` on the
    CLAWBACK row itself -- once create_payout_batch (finance/services.py)
    has settled a clawback into a (DRAFT/APPROVED/PAID) payout batch, it
    is no longer an unresolved condition and must stop being reported
    here, or every correctly-settled clawback would generate a
    permanent, stale false-positive WARNING forever. Never touches the
    Payout or either LedgerEntry -- this remains read-only.
    """
    from .models import LedgerEntry, Payout

    qs = queryset if queryset is not None else LedgerEntry.objects.filter(
        entry_type=LedgerEntry.EntryType.CLAWBACK, payout__isnull=True,
    )
    issues = []
    for cb in qs.select_related('related_entry__payout', 'course_instructor'):
        original = cb.related_entry
        if original and original.payout_id and original.payout.status == Payout.Status.PAID:
            issues.append(ReconciliationIssue(
                issue_type=IssueType.CLAWBACK_AGAINST_PAID_PAYOUT,
                severity=Severity.WARNING,
                source_type=SourceType.LEDGER_ENTRY,
                source_id=cb.id,
                related_object_id=original.payout_id,
                instructor_user_id=cb.course_instructor.user_id if cb.course_instructor_id else None,
                message=(
                    f"CLAWBACK LedgerEntry #{cb.id} (net={cb.net_amount}) corrects EARNING #{original.id}, already batched "
                    f"into PAID Payout #{original.payout_id}, and has not yet been settled into a later payout. This is "
                    f"an outstanding debt -- see get_instructor_balance() for the exact amount."
                ),
                expected_value="settled into a future payout (see Phase 3.5.8's create_payout_batch)",
                actual_value="standalone, unbatched (payout=None)",
            ))
    return issues


# ---------------------------------------------------------------------------
# Objective 3: instructor financial balance
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class InstructorBalance:
    instructor_user_id: int
    earned: Decimal
    clawed_back: Decimal
    adjustments: Decimal
    paid: Decimal
    available: Decimal
    outstanding_debt: Decimal
    outstanding_debt_from_paid_payouts: Decimal

    def to_dict(self):
        return dataclasses.asdict(self)


def get_instructor_balance(user) -> InstructorBalance:
    """
    Objective 3. Computed ENTIRELY from existing, immutable LedgerEntry
    rows via aggregate queries -- never mutates a single one.

    - EARNED: sum of net_amount across every EARNING entry ever created
      for this instructor (their all-time gross earning position).
    - CLAWED_BACK: sum of net_amount across every CLAWBACK entry
      (negative).
    - ADJUSTMENTS: sum of net_amount across every ADJUSTMENT entry (no
      code creates these yet -- always 0 today, included for schema
      completeness).
    - PAID: sum of net_amount across EARNING entries whose payout is
      PAID -- money that has actually, physically been sent.
    - AVAILABLE (Phase 3.5.8): max(0, sum of net_amount across entries
      get_eligible_ledger_entries_queryset() would currently let a new
      Payout batch include). Since Phase 3.5.8 that queryset spans BOTH
      unbatched EARNING (positive) and unbatched CLAWBACK (negative)
      entries, this sum is now the instructor's actual NET eligible
      position -- exactly what create_payout_batch's own floor rule
      requires to be strictly positive before it will create a Payout at
      all. Floored at 0 rather than ever reported negative: a negative
      net position isn't "available", it's OUTSTANDING_DEBT below. Reuses
      get_eligible_ledger_entries_queryset() unmodified rather than
      re-deriving the same eligibility rule a second way.
    - OUTSTANDING_DEBT: magnitude of unbatched CLAWBACK entries
      (payout IS NULL) -- clawback debt not yet settled into any payout.
      Naturally shrinks to 0 for a course/instructor's clawback the
      moment it's included in a settlement batch (create_payout_batch),
      the same way an EARNING entry stops being "available" the instant
      it's batched, regardless of that Payout's own status.
    - OUTSTANDING_DEBT_FROM_PAID_PAYOUTS: the subset of the above (still
      unbatched: payout IS NULL) whose ORIGINAL earning was already paid
      out -- money genuinely already sent to the instructor that a
      refund has since reversed, and that has not yet been settled into
      a later payout.
    """
    from .models import LedgerEntry, Payout
    from .services import get_eligible_ledger_entries_queryset

    entries = LedgerEntry.objects.filter(course_instructor__user=user)

    earned = entries.filter(entry_type=LedgerEntry.EntryType.EARNING).aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
    clawed_back = entries.filter(entry_type=LedgerEntry.EntryType.CLAWBACK).aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
    adjustments = entries.filter(entry_type=LedgerEntry.EntryType.ADJUSTMENT).aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
    paid = (
        entries.filter(entry_type=LedgerEntry.EntryType.EARNING, payout__status=Payout.Status.PAID)
        .aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
    )
    available_net = (
        get_eligible_ledger_entries_queryset().filter(course_instructor__user=user)
        .aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
    )
    available = available_net if available_net > 0 else Decimal('0')

    unbatched_clawback = (
        entries.filter(entry_type=LedgerEntry.EntryType.CLAWBACK, payout__isnull=True)
        .aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
    )
    outstanding_debt = -unbatched_clawback if unbatched_clawback < 0 else Decimal('0')

    debt_from_paid = (
        entries.filter(entry_type=LedgerEntry.EntryType.CLAWBACK, payout__isnull=True, related_entry__payout__status=Payout.Status.PAID)
        .aggregate(t=Sum('net_amount'))['t'] or Decimal('0')
    )
    outstanding_debt_from_paid_payouts = -debt_from_paid if debt_from_paid < 0 else Decimal('0')

    return InstructorBalance(
        instructor_user_id=user.id,
        earned=earned,
        clawed_back=clawed_back,
        adjustments=adjustments,
        paid=paid,
        available=available,
        outstanding_debt=outstanding_debt,
        outstanding_debt_from_paid_payouts=outstanding_debt_from_paid_payouts,
    )


def get_instructors_with_negative_balance() -> List[InstructorBalance]:
    """Every instructor (any user with at least one is_primary
    CourseInstructor row) whose outstanding_debt is greater than zero."""
    from django.contrib.auth import get_user_model

    from courses.models import CourseInstructor

    User = get_user_model()
    instructor_user_ids = CourseInstructor.objects.filter(is_primary=True).values_list('user_id', flat=True).distinct()
    negative = []
    for user in User.objects.filter(id__in=list(instructor_user_ids)):
        balance = get_instructor_balance(user)
        if balance.outstanding_debt > 0:
            negative.append(balance)
    return negative


def check_instructor_negative_balances() -> List[ReconciliationIssue]:
    return [
        ReconciliationIssue(
            issue_type=IssueType.INSTRUCTOR_NEGATIVE_BALANCE,
            severity=Severity.WARNING,
            source_type=SourceType.INSTRUCTOR,
            source_id=balance.instructor_user_id,
            instructor_user_id=balance.instructor_user_id,
            message=f"Instructor user #{balance.instructor_user_id} has an outstanding unbatched clawback debt of {balance.outstanding_debt} not yet netted against any payout.",
            actual_value=str(balance.outstanding_debt),
        )
        for balance in get_instructors_with_negative_balance()
    ]


# ---------------------------------------------------------------------------
# Orchestration (Objective 1 + Objective 6's filters)
# ---------------------------------------------------------------------------

def run_reconciliation(*, severity=None, issue_type=None, source_type=None, instructor_id=None,
                        date_from=None, date_to=None) -> ReconciliationReport:
    """
    Runs every check above and returns one ReconciliationReport.

    date_from/date_to (plain `date` objects, inclusive) narrow WHICH
    Purchase/Order/SubscriptionPayment rows are examined by created_at --
    a genuinely useful scope for a large, periodic reconciliation run,
    not a filter on when an issue was "detected" (meaningless here, since
    nothing is persisted -- every issue's detected_at is simply "now").
    Refund/Payout/LedgerEntry-wide checks (which have no natural
    per-transaction date scope of their own) are not date-filtered.

    severity/issue_type/source_type/instructor_id filter the RETURNED
    issue list only -- healthy_checks/total_checked always reflect the
    full, unfiltered picture, so the summary always represents overall
    system health regardless of what the caller chose to look at.
    """
    from orders.models import Order, Purchase, SubscriptionPayment
    from .models import Payout, Refund

    purchase_qs = Purchase.objects.filter(status=Purchase.Status.SUCCESS)
    order_qs = Order.objects.filter(status=Order.Status.PAID)
    subpay_qs = SubscriptionPayment.objects.filter(status=SubscriptionPayment.Status.SUCCESS)

    if date_from:
        purchase_qs = purchase_qs.filter(created_at__date__gte=date_from)
        order_qs = order_qs.filter(created_at__date__gte=date_from)
        subpay_qs = subpay_qs.filter(created_at__date__gte=date_from)
    if date_to:
        purchase_qs = purchase_qs.filter(created_at__date__lte=date_to)
        order_qs = order_qs.filter(created_at__date__lte=date_to)
        subpay_qs = subpay_qs.filter(created_at__date__lte=date_to)

    issues: List[ReconciliationIssue] = []
    issues += check_purchase_missing_ledger_entry(purchase_qs)
    issues += check_purchase_missing_invoice(purchase_qs)
    issues += check_order_missing_ledger_entry(order_qs)
    issues += check_order_missing_invoice(order_qs)
    issues += check_subscription_payment_missing_ledger_entry(subpay_qs)
    issues += check_subscription_payment_missing_invoice(subpay_qs)
    issues += check_refund_missing_clawback()
    issues += check_duplicate_financial_records()
    issues += check_ledger_entry_invalid_source()
    issues += check_ledger_entry_calculation_mismatch()
    issues += check_payout_total_mismatch()
    issues += check_refund_exceeds_source_amount()
    issues += check_clawback_against_paid_payout()
    issues += check_instructor_negative_balances()

    total_checked = (
        purchase_qs.count() + order_qs.count() + subpay_qs.count()
        + Refund.objects.filter(status=Refund.Status.SUCCESS).count()
        + Payout.objects.count()
    )
    flagged_keys = {(i.source_type, i.source_id) for i in issues}
    healthy_checks = max(total_checked - len(flagged_keys), 0)

    filtered = issues
    if severity:
        filtered = [i for i in filtered if i.severity == severity]
    if issue_type:
        filtered = [i for i in filtered if i.issue_type == issue_type]
    if source_type:
        filtered = [i for i in filtered if i.source_type == source_type]
    if instructor_id:
        filtered = [i for i in filtered if i.instructor_user_id == instructor_id]

    return ReconciliationReport(
        issues=filtered,
        generated_at=timezone.now(),
        healthy_checks=healthy_checks,
        total_checked=total_checked,
    )
