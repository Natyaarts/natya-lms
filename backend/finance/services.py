"""
Phase 3.5.2/3.5.4.

Phase 3.5.2 part: THE single place a LedgerEntry is ever created. Called
from exactly three already-proven fulfillment success paths --
orders/services.py's fulfill_purchase()/fulfill_order(), and
orders/views.py's RazorpayWebhookView._record_subscription_charge() --
each already the sole, idempotent, transactional point where "money
became revenue" for its flow (see the Phase 3.5.1 audit's Recommended
Finance Architecture section). This module never decides whether a
payment succeeded; every function here is only ever called after that has
already been established by its caller, and never raises an exception the
caller has to handle -- a ledger-side problem must never take down
enrollment/access fulfillment, which has already happened by the time
these functions run.

Phase 3.5.4 part: admin-controlled payout BATCHING (grouping already-
existing, immutable LedgerEntry rows into a Payout) and APPROVAL
(DRAFT -> APPROVED). Payouts are built ONLY from LedgerEntry rows, never
directly from Purchase/Order/SubscriptionPayment -- this module has no
knowledge of Razorpay, no money ever moves, no bank/UPI detail is ever
touched. Unlike the creation functions above, these two functions DO
raise (ValueError) on any validation failure -- they're admin-initiated,
synchronous actions where the caller (the view) is expected to surface
the error directly to the admin, not silently swallow it the way ledger
creation swallows failures during automated fulfillment.
"""

import logging
from decimal import Decimal, ROUND_HALF_UP

from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

logger = logging.getLogger(__name__)


def _resolve_primary_instructor(courses):
    """
    Revenue attribution is only ever assigned when a transaction maps to
    EXACTLY ONE course with an is_primary=True CourseInstructor.

    This deliberately covers three real situations the same way, all as
    a safe no-op (see the caller's logging for which):
    - a course with no CourseInstructor assigned at all (common today --
      is_primary is only backfilled for legacy teachers, per the audit)
    - a Bundle OrderItem or a multi-course SubscriptionPlan, where
      `courses` has more than one element -- splitting revenue across
      several instructors is explicitly deferred (Phase 3.5.1 audit,
      Risks section; excluded from this phase's approved scope)
    - an empty course set (a Bundle/Plan with zero courses somehow)

    `courses` is a plain list/iterable of Course instances -- callers
    pass either a single-element list (Purchase, COURSE-type OrderItem)
    or a bundle's/plan's full course set (BUNDLE-type OrderItem,
    SubscriptionPlan), and this function applies the exact same rule
    either way.
    """
    courses = list(courses)
    if len(courses) != 1:
        return None

    from courses.models import CourseInstructor
    return CourseInstructor.objects.filter(course=courses[0], is_primary=True).select_related('user').first()


def _create_earning_entry(*, courses, gross_amount, currency, purchase=None, order_item=None, subscription_payment=None):
    """
    Shared core: resolve attribution, compute commission/net, create the
    LedgerEntry. Never raises -- any failure (no eligible instructor, no
    commission_rate configured, a duplicate, or a genuine bug) is logged
    and swallowed here, exactly per "ledger creation must never decide
    whether payment succeeded."
    """
    source_desc = (
        f"purchase={purchase.id if purchase else None} "
        f"order_item={order_item.id if order_item else None} "
        f"subscription_payment={subscription_payment.id if subscription_payment else None}"
    )

    course_instructor = _resolve_primary_instructor(courses)
    if course_instructor is None:
        logger.info(
            f"Ledger: no single eligible primary instructor for {source_desc} "
            f"(course count={len(list(courses))}); no ledger entry created."
        )
        return

    if course_instructor.commission_rate is None:
        logger.info(
            f"Ledger: CourseInstructor {course_instructor.id} ({course_instructor.user.username}) has no "
            f"commission_rate configured; no ledger entry created for {source_desc}."
        )
        return

    rate = course_instructor.commission_rate
    commission_amount = (gross_amount * rate / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    net_amount = gross_amount - commission_amount

    from .models import LedgerEntry
    try:
        with transaction.atomic():
            LedgerEntry.objects.create(
                course_instructor=course_instructor,
                purchase=purchase,
                order_item=order_item,
                subscription_payment=subscription_payment,
                entry_type=LedgerEntry.EntryType.EARNING,
                gross_amount=gross_amount,
                currency=currency,
                commission_rate=rate,
                commission_amount=commission_amount,
                net_amount=net_amount,
            )
        logger.info(f"Ledger: entry created for {source_desc}, instructor={course_instructor.user.username}, net={net_amount} {currency}.")
    except IntegrityError:
        logger.info(f"Ledger: entry already exists for {source_desc}; skipped duplicate.")
    except Exception as e:
        logger.error(f"Ledger: unexpected error creating entry for {source_desc}: {e}", exc_info=True)


def create_earning_entry_for_purchase(purchase):
    """Called from orders/services.py's fulfill_purchase(), after
    _grant_course_access -- a Purchase always maps to exactly one course."""
    _create_earning_entry(
        courses=[purchase.course],
        gross_amount=purchase.amount,
        # Purchase has no currency field of its own (legacy, single-
        # course model, predates Order/Subscription's explicit currency
        # field) -- INR matches this codebase's project-wide hardcoded
        # default everywhere else money is stored without an explicit
        # currency value.
        currency='INR',
        purchase=purchase,
    )


def create_earning_entry_for_order_item(order_item, order_currency):
    """Called from orders/services.py's fulfill_order(), once per
    OrderItem, after that item's course access has been granted.
    order_currency is passed explicitly (the caller already has the
    parent Order in scope) rather than read via order_item.order.currency,
    avoiding an extra query per item."""
    if order_item.item_type == 'COURSE':
        courses = [order_item.course] if order_item.course_id else []
    else:  # BUNDLE
        courses = list(order_item.bundle.courses.all()) if order_item.bundle_id else []

    _create_earning_entry(
        courses=courses,
        gross_amount=order_item.total_price,
        currency=order_currency,
        order_item=order_item,
    )


def create_earning_entry_for_subscription_payment(subscription_payment):
    """Called from orders/views.py's RazorpayWebhookView._record_subscription_charge(),
    only for an actually-successful charge (is_successful_charge=True) --
    never for a merely-recorded or failed one."""
    courses = list(subscription_payment.subscription.plan.courses.all())
    _create_earning_entry(
        courses=courses,
        gross_amount=subscription_payment.amount,
        currency=subscription_payment.currency,
        subscription_payment=subscription_payment,
    )


# Only INR is supported anywhere in this codebase today (every money
# field defaults to 'INR', no multi-currency handling exists anywhere) --
# a LedgerEntry in any other currency is conservatively treated as not
# yet payout-eligible rather than guessing how to convert/handle it.
SUPPORTED_PAYOUT_CURRENCIES = ('INR',)


def get_eligible_ledger_entries_queryset():
    """
    Phase 3.5.4 / extended in Phase 3.5.8. A LedgerEntry is payout-eligible
    when it is not already assigned to a payout (payout IS NULL), its
    currency is one this platform actually supports paying out in, its
    recipient's account is still active, and ONE of:
    - entry_type = EARNING with net_amount > 0 (unchanged since 3.5.4), or
    - entry_type = CLAWBACK with net_amount < 0 (Phase 3.5.8: an
      instructor's outstanding, unbatched clawback debt is now visible
      and selectable for settlement alongside their positive earnings --
      see create_payout_batch's own docstring for how a mixed selection
      is validated/settled). ADJUSTMENT is still never eligible -- no
      code creates that entry_type yet.

    Two of the approved eligibility rules are deliberately NOT
    implemented as extra filters here, because the existing schema
    already makes them structurally impossible to violate for any row
    that exists at all:
    - "appropriate commission configuration exists" -- LedgerEntry.
      commission_rate is a NOT NULL field; create_earning_entry_for_*
      already refuses to create a row at all when a CourseInstructor has
      no commission_rate configured (see _create_earning_entry's
      docstring) -- so every existing LedgerEntry row already has one.
    - "incomplete ledger records" -- gross_amount/commission_amount/
      net_amount/currency are all NOT NULL fields; nothing in this
      codebase can produce a row missing any of them.
    """
    from .models import LedgerEntry
    return (
        LedgerEntry.objects
        .filter(
            Q(entry_type=LedgerEntry.EntryType.EARNING, net_amount__gt=0) |
            Q(entry_type=LedgerEntry.EntryType.CLAWBACK, net_amount__lt=0),
            payout__isnull=True,
            currency__in=SUPPORTED_PAYOUT_CURRENCIES,
            course_instructor__user__is_active=True,
        )
        .select_related('course_instructor__user', 'course_instructor__course')
    )


def create_payout_batch(*, recipient, ledger_entry_ids, period_start, period_end, method='', reference_number='', notes=''):
    """
    Phase 3.5.4, extended in Phase 3.5.8 for clawback-aware settlement.
    Admin-controlled payout batching. Groups the EXPLICITLY selected
    LedgerEntry rows (by id -- never an implicit "all eligible for this
    recipient" auto-selection, matching "select ledger entries
    explicitly") into one new Payout, status=DRAFT (never PAID -- no
    money has moved, this only records an admin's intent to batch these
    entries together).

    Phase 3.5.8: the selection may now be a MIX of EARNING (positive
    net_amount) and CLAWBACK (negative net_amount) rows for the same
    recipient -- see get_eligible_ledger_entries_queryset's own docstring.
    This is how an instructor's outstanding clawback debt actually gets
    settled: by being included, alongside enough EARNING entries to cover
    it, in a future payout. Two new rules enforce this safely:

    - PAIRING RULE: if any selected EARNING entry has one or more
      still-unbatched CLAWBACK entries whose `related_entry` points to
      it, ALL of those CLAWBACK entries must also be included in this
      SAME selection. This closes the gap where a course's revenue was
      refunded before its EARNING entry was ever batched -- that EARNING
      row is immutable and stays net_amount>0 forever (per LedgerEntry's
      own immutability design), so without this rule an admin could
      accidentally pay out revenue that has already been reversed. Not
      required in the other direction: a standalone CLAWBACK against an
      EARNING that was already paid out has no EARNING left in the
      eligible pool to pair with.
    - FLOOR RULE: the resulting net_amount (computed as the plain signed
      sum of every selected entry's net_amount, EARNING positive and
      CLAWBACK negative) must be STRICTLY POSITIVE. A Payout can never
      represent zero or negative money -- there is nothing to send, and
      recovering money FROM an instructor is explicitly out of scope
      (no automatic money movement exists, or will exist, in either
      direction). If a clawback's magnitude meets or exceeds the
      currently-eligible earnings, this call raises and NOTHING is
      batched -- the entries remain unbatched, still visible as
      outstanding via get_instructor_balance(), until enough future
      earnings accumulate to more than cover it.

    Concurrency: select_for_update() locks the requested rows (both
    EARNING and CLAWBACK, uniformly), and every eligibility check below
    is re-run INSIDE that lock against the freshly-locked rows -- not
    just against the caller-supplied ids -- so a second, concurrent call
    attempting to include any of the same entries in a different batch
    will either block until this transaction commits (then see payout_id
    already set and raise) or, if it started first, cause THIS call to
    see payout_id already set and raise. Either way, the same LedgerEntry
    can never end up in two Payouts. The pairing-rule lookup (a fresh
    query for unbatched CLAWBACK rows related to the locked EARNING
    entries) runs inside this same transaction, so under PostgreSQL's
    default READ COMMITTED isolation it also sees any clawback that
    concurrently committed just before this point -- not a perfect
    guarantee across every possible interleaving, but consistent with
    this codebase's existing level of rigor elsewhere. (SQLite, this
    project's test backend, does not enforce real row-level locking --
    select_for_update() is a documented no-op there -- so this is
    verified here via sequential calls, matching this codebase's
    established practice for testing concurrency protections that depend
    on PostgreSQL's real locking in production.)

    Never modifies gross_amount/commission_rate/commission_amount/
    net_amount on any LedgerEntry (EARNING or CLAWBACK) -- only ever sets
    its `payout` FK via a single bulk .update() (not a per-row .save(),
    so no custom validation is bypassed that wasn't already bypassed by
    design -- see financial immutability: LedgerEntry currently has no
    clean()/save() override at all, only DB-level constraints, all of
    which a plain `payout` reassignment still respects). Payout.
    gross_amount/commission_amount/net_amount are independently computed
    sums of the batched entries' own (untouched) amounts -- never
    modified again after creation, including by any LATER settlement
    (a later payout never rewrites an earlier, already-created Payout).

    Raises ValueError (never silently partial -- nothing is batched at
    all if any check fails) for: an empty selection, an id that doesn't
    resolve to a real row, an entry that is neither EARNING nor CLAWBACK,
    an EARNING with a non-positive net_amount, a CLAWBACK with a
    non-negative net_amount, one already assigned to a payout, one
    belonging to a different instructor than `recipient`, one in an
    unsupported currency, a required-but-missing paired CLAWBACK, or a
    selection whose net_amount would be zero or negative.
    """
    from .models import LedgerEntry, Payout

    ledger_entry_ids = list(dict.fromkeys(ledger_entry_ids or []))  # de-dupe, preserve order
    if not ledger_entry_ids:
        raise ValueError("At least one ledger entry must be selected.")

    with transaction.atomic():
        entries = list(
            LedgerEntry.objects
            .select_for_update()
            .filter(id__in=ledger_entry_ids)
            .select_related('course_instructor__user')
        )

        found_ids = {entry.id for entry in entries}
        missing_ids = set(ledger_entry_ids) - found_ids
        if missing_ids:
            raise ValueError(f"Ledger entries not found: {sorted(missing_ids)}.")

        for entry in entries:
            if entry.entry_type not in (LedgerEntry.EntryType.EARNING, LedgerEntry.EntryType.CLAWBACK):
                raise ValueError(
                    f"LedgerEntry {entry.id} is entry_type={entry.entry_type} and cannot be batched "
                    "(only EARNING and CLAWBACK entries are payout-eligible)."
                )
            if entry.payout_id is not None:
                raise ValueError(f"LedgerEntry {entry.id} is already assigned to Payout {entry.payout_id}.")
            if entry.entry_type == LedgerEntry.EntryType.EARNING and entry.net_amount <= 0:
                raise ValueError(f"LedgerEntry {entry.id} has a non-positive net_amount and is not payout-eligible.")
            if entry.entry_type == LedgerEntry.EntryType.CLAWBACK and entry.net_amount >= 0:
                raise ValueError(f"LedgerEntry {entry.id} is a CLAWBACK with a non-negative net_amount and is not payout-eligible.")
            if entry.course_instructor.user_id != recipient.id:
                raise ValueError(f"LedgerEntry {entry.id} does not belong to recipient {recipient.id}.")
            if entry.currency not in SUPPORTED_PAYOUT_CURRENCIES:
                raise ValueError(f"LedgerEntry {entry.id} is in an unsupported currency ({entry.currency}).")

        # Pairing rule: every still-unbatched CLAWBACK related to a
        # selected EARNING entry must also be part of this selection.
        earning_ids = [e.id for e in entries if e.entry_type == LedgerEntry.EntryType.EARNING]
        if earning_ids:
            required_clawback_ids = set(
                LedgerEntry.objects.filter(
                    entry_type=LedgerEntry.EntryType.CLAWBACK,
                    related_entry_id__in=earning_ids,
                    payout__isnull=True,
                ).values_list('id', flat=True)
            )
            missing_pairs = required_clawback_ids - found_ids
            if missing_pairs:
                raise ValueError(
                    "This selection includes an EARNING entry with an outstanding, unbatched CLAWBACK that "
                    f"must be settled in the same batch: LedgerEntry id(s) {sorted(missing_pairs)} are required "
                    "but were not included."
                )

        gross_total = sum((entry.gross_amount for entry in entries), Decimal('0'))
        commission_total = sum((entry.commission_amount for entry in entries), Decimal('0'))
        net_total = sum((entry.net_amount for entry in entries), Decimal('0'))

        # Floor rule: a Payout can never represent zero or negative
        # money. Nothing is batched -- every selected entry stays exactly
        # as it was (still unbatched, still outstanding) if this fails.
        if net_total <= 0:
            raise ValueError(
                f"This selection would result in a payout net_amount of {net_total}, which is not allowed -- "
                "a Payout must have a strictly positive net_amount. No entries were batched; they remain "
                "unbatched until a positive payable amount is selected."
            )

        payout = Payout.objects.create(
            recipient=recipient,
            period_start=period_start,
            period_end=period_end,
            gross_amount=gross_total,
            commission_amount=commission_total,
            net_amount=net_total,
            status=Payout.Status.DRAFT,
            method=method,
            reference_number=reference_number,
            notes=notes,
        )

        LedgerEntry.objects.filter(id__in=list(found_ids)).update(payout=payout)

    return payout


def approve_payout(payout, approved_by):
    """
    Phase 3.5.4. DRAFT -> APPROVED only. Deliberately goes no further --
    PROCESSING/PAID would imply money has actually moved, which nothing
    in this phase (or any phase so far) ever does. Raises ValueError if
    the payout isn't currently DRAFT (e.g. already approved, or in some
    other state) rather than silently overwriting whatever state it's
    actually in.
    """
    from .models import Payout

    with transaction.atomic():
        locked = Payout.objects.select_for_update().get(pk=payout.pk)
        if locked.status != Payout.Status.DRAFT:
            raise ValueError(f"Payout {locked.id} is not in DRAFT status (currently {locked.status}); cannot approve.")
        locked.status = Payout.Status.APPROVED
        locked.approved_by = approved_by
        locked.approved_at = timezone.now()
        locked.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])

    return locked


def mark_payout_paid(payout, *, method='', reference_number=''):
    """
    Final release audit gap fix: APPROVED -> PAID, the one transition
    approve_payout's own docstring explicitly deferred ("PROCESSING/PAID
    would imply money has actually moved, which nothing in this phase
    ... ever does"). This is that later phase -- an admin has now
    actually sent the money (bank transfer/UPI, outside this system) and
    is recording that fact.

    Mirrors approve_payout's exact shape: lock, verify the current
    status, transition, save. Raises ValueError if the payout isn't
    currently APPROVED (e.g. already PAID, still DRAFT, or CANCELLED/
    FAILED) -- same "never silently overwrite an unexpected state"
    contract, which is what makes this safe against a double submission
    or a concurrent second request: the first call to actually reach the
    lock wins and flips the status; a second call (whether a genuine
    double-click or two admins acting at once) finds status is no longer
    APPROVED and raises instead of marking anything paid twice.

    Deliberately touches ONLY this Payout row -- never a LedgerEntry.
    Every batched LedgerEntry already had its `payout` FK set once, back
    at create_payout_batch() time, and never needs to change again;
    finance/reconciliation.py's get_instructor_balance() computes its
    "paid" total by filtering LedgerEntry.payout__status=PAID, so this
    one field flip is what makes that balance correct, with zero other
    writes needed anywhere. No commission/clawback/refund logic is
    touched or re-derived here either -- those were already finalized
    (and remain untouched) at earning-creation and refund time
    respectively; a payout being marked PAID is purely "this batch of
    already-settled amounts was physically sent," not a recalculation.

    method/reference_number are optional overrides -- supplied by the
    admin at the moment of actually sending the money (e.g. a bank
    transfer UTR, only known once the transfer is made), matching this
    model's own reference_number field docstring ("once a payout is
    actually processed"). Left blank, whatever was already recorded at
    creation time (if anything) is preserved untouched -- this never
    blanks out an existing value.
    """
    from .models import Payout

    with transaction.atomic():
        locked = Payout.objects.select_for_update().get(pk=payout.pk)
        if locked.status != Payout.Status.APPROVED:
            raise ValueError(f"Payout {locked.id} is not in APPROVED status (currently {locked.status}); cannot mark paid.")
        locked.status = Payout.Status.PAID
        locked.paid_at = timezone.now()
        if method:
            locked.method = method
        if reference_number:
            locked.reference_number = reference_number
        locked.save(update_fields=['status', 'paid_at', 'method', 'reference_number', 'updated_at'])

    return locked


# ---------------------------------------------------------------------------
# Phase 3.5.5: customer invoice/receipt generation. Hooked into the SAME three
# already-proven fulfillment success paths as create_earning_entry_for_*
# above -- fulfill_purchase()/fulfill_order() (orders/services.py) and
# _record_subscription_charge() (orders/views.py) -- called alongside, never
# instead of, the ledger-creation calls already there. These are two
# independent side effects of the same success event, each idempotent and
# fault-isolated on its own: an invoice-creation failure must never affect
# enrollment or ledger creation (which have already happened by the time
# these run), and a ledger-creation failure must never affect invoice
# creation either. Never decides whether a payment succeeded; never raises
# back to its caller, for the exact same reasons _create_earning_entry
# doesn't (see that function's docstring).
#
# Reconciliation (documented, not implemented, per this phase's brief):
# comparing Purchase.objects.filter(status='SUCCESS').count() against
# Invoice.objects.filter(purchase__isnull=False).count() (and the
# equivalent for Order/status=PAID and SubscriptionPayment/status=SUCCESS)
# would surface any successful transaction that never got an invoice --
# the same class of gap already documented for LedgerEntry in Phase 3.5.2/
# 3.5.3's "financial consistency risks" sections. No reconciliation job/
# command exists in this codebase; this is left as a documented, future,
# well-scoped candidate exactly as the brief asks for.
# ---------------------------------------------------------------------------

def create_invoice_for_purchase(purchase):
    """
    Called from orders/services.py's fulfill_purchase(). payment_date is
    timezone.now() at the moment fulfillment confirms success -- Purchase
    has no dedicated "paid at" timestamp of its own (a legacy, single-
    course model; unlike SubscriptionPayment.paid_at below), and this
    hook runs synchronously immediately after the status flip, so "now"
    is an accurate proxy for "when payment completed" in practice.
    """
    from .models import Invoice
    try:
        with transaction.atomic():
            Invoice.objects.create(
                customer=purchase.user, purchase=purchase,
                amount=purchase.amount, currency='INR',  # Purchase has no currency field -- same INR-default reasoning as LedgerEntry
                payment_date=timezone.now(),
            )
        logger.info(f"Invoice: created for Purchase {purchase.id}.")
    except IntegrityError:
        logger.info(f"Invoice: already exists for Purchase {purchase.id}; skipped duplicate.")
    except Exception as e:
        logger.error(f"Invoice: unexpected error creating invoice for Purchase {purchase.id}: {e}", exc_info=True)


def create_invoice_for_order(order):
    """Called from orders/services.py's fulfill_order() -- ONE invoice per
    Order (the whole checkout transaction), not per OrderItem, regardless
    of how many courses/bundles it contains."""
    from .models import Invoice
    try:
        with transaction.atomic():
            Invoice.objects.create(
                customer=order.user, order=order,
                amount=order.total_amount, currency=order.currency,
                payment_date=timezone.now(),
            )
        logger.info(f"Invoice: created for Order {order.id}.")
    except IntegrityError:
        logger.info(f"Invoice: already exists for Order {order.id}; skipped duplicate.")
    except Exception as e:
        logger.error(f"Invoice: unexpected error creating invoice for Order {order.id}: {e}", exc_info=True)


def create_invoice_for_subscription_payment(subscription_payment):
    """Called from orders/views.py's RazorpayWebhookView._record_subscription_charge(),
    only for an actually-successful charge (same is_successful_charge gate
    create_earning_entry_for_subscription_payment already uses). Uses
    SubscriptionPayment.paid_at -- unlike Purchase/Order, this field
    already exists and is sourced from Razorpay's own payment entity
    timestamp, a more precise "when payment completed" than "now"."""
    from .models import Invoice
    try:
        with transaction.atomic():
            Invoice.objects.create(
                customer=subscription_payment.subscription.user, subscription_payment=subscription_payment,
                amount=subscription_payment.amount, currency=subscription_payment.currency,
                payment_date=subscription_payment.paid_at or timezone.now(),
            )
        logger.info(f"Invoice: created for SubscriptionPayment {subscription_payment.id}.")
    except IntegrityError:
        logger.info(f"Invoice: already exists for SubscriptionPayment {subscription_payment.id}; skipped duplicate.")
    except Exception as e:
        logger.error(f"Invoice: unexpected error creating invoice for SubscriptionPayment {subscription_payment.id}: {e}", exc_info=True)


# ---------------------------------------------------------------------------
# Phase 3.5.6: Refunds + Clawbacks.
#
# Three supported refund sources, each mapped to Razorpay's own refund API
# against that source's existing razorpay_payment_id -- Purchase, Order (as
# a WHOLE checkout transaction, matching Invoice's own per-Order, not
# per-OrderItem, granularity: Razorpay only ever knows about one payment per
# Order, never a per-item breakdown), and SubscriptionPayment. Nothing else
# is refundable -- there is no generic "refund any transaction" path.
#
# Multi-item Order partial refunds are explicitly NOT supported: a partial
# refund of a checkout containing 2+ OrderItems has no defined rule for
# which item(s) it applies to, and clawback needs exactly that to reverse
# the right instructor's LedgerEntry -- create_and_process_refund rejects
# this case outright with a clear error rather than guessing (see its own
# docstring). A FULL refund of a multi-item Order IS supported (unambiguous
# -- every item's earning is clawed back at 100%). A single-item Order is
# treated identically to a Purchase (full or partial).
#
# Order.status is deliberately NEVER touched by a refund, even a full one --
# unlike Purchase.Status.REFUNDED and SubscriptionPayment.Status.REFUNDED
# (both explicitly pre-reserved for this exact moment by their own
# docstrings, going all the way back to when those models were first
# written), Order.Status never anticipated a refund value at all. Adding
# one now would be inventing a status this phase's brief was never asked to
# design (what should it mean for an Order whose items each have
# independent enrollment/access state? does a partially-item-refunded
# Order -- impossible today, but a natural future ask -- need a DIFFERENT
# status again later?) -- exactly the kind of business-rule invention the
# brief says to document instead of guess at. The definitive source of
# truth for "has this Order been refunded, and how much" is, and remains,
# the Refund model itself (`order.refunds.filter(status='SUCCESS')`), which
# every API in this module already exposes.
#
# Razorpay integration: client.payment.refund(payment_id, {"amount": paise,
# "speed": "normal", "notes": {...}}) (confirmed against the currently
# installed razorpay SDK's source, razorpay==2.0.0, and Razorpay's own
# refund API documentation) -- normal-speed refunds do not settle
# instantly, so the synchronous response is only ever a first signal
# ("processed" if actually instant, otherwise "pending"/"processing"). Per
# Razorpay's own explicit guidance, the refund.processed webhook (handled
# in orders/views.py's RazorpayWebhookView, reusing its existing
# WebhookEvent dedup mechanism) is treated as the AUTHORITATIVE final
# status -- clawback creation and Purchase/SubscriptionPayment status
# updates happen identically from whichever path (synchronous response or
# webhook) first confirms SUCCESS, via the single, idempotent
# _mark_refund_success_locked. amount is never trusted from the client --
# every amount here is either server-computed (get_refundable_remaining) or
# validated against it before any Razorpay call is ever made.
#
# Clawback: the ORIGINAL EARNING LedgerEntry is never edited (see
# LedgerEntry's own immutability docstring) -- a refund only ever adds a
# new, negative-valued CLAWBACK row, linked via both `related_entry`
# (what it corrects) and `refund` (what caused it, and this row's own
# idempotency key -- see ledgerentry_unique_refund_related_entry).
# Clawback amount is computed from the ACTUAL refunded amount and the
# original entry's ACTUAL commission_rate snapshot -- never guessed, never
# a flat/assumed percentage (see _apply_clawback_to_entry). No clawback is
# created at all when no EARNING entry exists for the source (the same
# "no eligible instructor / no commission_rate configured" safe no-op
# _create_earning_entry already establishes) -- nothing to reverse.
#
# Payout interaction (CRITICAL per the approved brief): a CLAWBACK entry is
# ALWAYS created standalone (payout=None), REGARDLESS of whether the
# original EARNING entry's own `payout` is unset, DRAFT, APPROVED, or (in
# practice never yet reached by any code) PAID -- this phase never
# retroactively rewrites an existing Payout's cached gross_amount/
# commission_amount/net_amount (those are immutable snapshots taken once
# at batch-creation time; editing them after the fact would be exactly the
# "falsified financial record" PayoutAdmin's read-only enforcement already
# guards against). For a DRAFT/APPROVED payout (no money has moved yet)
# this is a reporting gap: an admin approving/finalizing a payout should
# cross-check its instructor for any CLAWBACK entries created since
# batching. For a PAID payout, this standalone negative LedgerEntry IS this
# phase's answer to "create an outstanding negative adjustment/debt record
# instead of pretending the money was recovered" -- the SCHEMA already
# safely represents this (that is exactly what entry_type=CLAWBACK with
# payout=None means: an unbatched debit awaiting a future payout to net
# against). What is explicitly NOT implemented is teaching
# get_eligible_ledger_entries_queryset()/create_payout_batch() to actually
# discover and net CLAWBACK rows into a future payout -- both still only
# ever select entry_type=EARNING. That extension is real, necessary future
# work and is called out as an open item in the Phase 3.5.6 report, not
# silently built here (payout batching logic is out of this phase's scope
# except where refund correctness strictly requires touching it, and it
# does not: an unbatched, correctly-valued CLAWBACK row is itself already a
# correct, truthful financial record on its own).
#
# Enrollment/course access: NEVER touched by any function in this module.
# A refund and access revocation are separate business decisions (per the
# approved brief) -- this codebase has no existing rule that a refund
# revokes a Purchase/Order-granted Enrollment, and inventing one here would
# be exactly the kind of unapproved behavior the brief says to avoid. This
# is enforced by simple omission: fulfill_purchase/fulfill_order's
# Enrollment.get_or_create calls are the only code that ever touches
# Enrollment for these flows, and nothing here calls or duplicates them.
# Subscription-based access (Subscription.access_until) is entirely
# separate machinery again (governed by cancellation/grace-period logic in
# orders/views.py, untouched by this module) -- a refunded
# SubscriptionPayment does not cancel or otherwise alter its parent
# Subscription's access.
#
# Invoice: NEVER mutated by any function in this module -- the original
# Invoice remains exactly as issued, a historical record of what was
# charged at the time. No credit-note/refund-document model is built here:
# a proper credit note (particularly for a GST-registered business in
# India) is a legal/tax document with rules this codebase defines nowhere
# (mirroring Invoice's own already-documented "no tax_amount field, tax
# rules not defined here" decision from Phase 3.5.5) -- documented as a
# dependency for a future phase once those rules exist, not built on a
# guess. The Refund model itself, and its customer-facing my-refunds API,
# is this phase's queryable record of what was returned.
#
# Reconciliation risk (per the approved brief, kept as previously
# documented, not redesigned): the same broad except Exception pattern
# already used by _create_earning_entry/create_invoice_for_* is used again
# below in _create_clawback_for_refund, for the identical reason -- a
# clawback-bookkeeping failure must never undo a refund confirmation that
# has already genuinely happened (the customer really did get their money
# back). A future reconciliation job should compare
# Refund.objects.filter(status='SUCCESS') against LedgerEntry CLAWBACK rows
# (via the `refund` FK) the same way Phase 3.5.2/3.5.3/3.5.5's own
# documented reconciliation candidates already compare successful
# transactions against their expected EARNING/Invoice rows -- no such job
# exists in this codebase; this is left as a documented, well-scoped
# candidate, exactly as the brief asks for, not implemented here.
#
# Explicitly NOT implemented anywhere in this module (per the approved
# brief's DO NOT IMPLEMENT list): Razorpay Route, bank/UPI detail storage,
# automatic instructor payouts, payout provider integration, a tax/GST
# engine, a credit-note tax engine, automatic access revocation,
# multi-instructor revenue splitting, coupons, gateway fee accounting,
# historical financial backfill, an automated reconciliation job/command.
# ---------------------------------------------------------------------------

# Same reasoning as SUPPORTED_PAYOUT_CURRENCIES above -- only INR exists
# anywhere in this codebase's data today.
SUPPORTED_REFUND_CURRENCIES = ('INR',)


class RefundGatewayError(Exception):
    """
    Raised by create_and_process_refund only when the Razorpay refund API
    call itself fails (network error, razorpay.errors.BadRequestError,
    etc.) -- AFTER a Refund row has already been durably created and
    marked FAILED, so the failed attempt remains a visible audit record,
    never silently lost. Distinct from a plain ValueError (used for every
    validation failure below, matching create_payout_batch's existing
    convention) so finance/views.py's CreateRefundView can surface a 502
    (gateway failure) rather than a 400 (bad request) for this case.
    """
    pass


def _resolve_refund_source(*, purchase=None, order=None, subscription_payment=None, lock=False):
    """
    Shared validation core for every refund-related read/write below.
    Resolves exactly one of purchase/order/subscription_payment to a dict
    of {total_amount, currency, razorpay_payment_id, customer, item_count}
    (item_count only meaningful for `order`), raising ValueError for any
    source that is not currently in a refundable state -- this is the
    single place "which transaction types/states are safely refundable"
    is decided, per the brief's "unsupported transaction types safely
    rejected" requirement.

    lock=True (only ever used by create_and_process_refund's actual
    reservation step) re-fetches the source row via select_for_update()
    inside the caller's open transaction, so a concurrent refund request
    against the SAME source serializes on this lock rather than both
    reading a stale "amount remaining" snapshot -- the exact protection
    create_payout_batch's own select_for_update() already established for
    LedgerEntry batching. lock=False (used by every read-only inspection
    path) uses the passed-in object directly, no extra query.
    """
    from orders.models import Order as OrderModel
    from orders.models import Purchase as PurchaseModel
    from orders.models import SubscriptionPayment as SubscriptionPaymentModel

    if purchase is not None:
        obj = PurchaseModel.objects.select_for_update().get(pk=purchase.pk) if lock else purchase
        if obj.status != PurchaseModel.Status.SUCCESS:
            raise ValueError(
                f"Purchase {obj.id} is not in a refundable state (status={obj.status}); "
                "only a SUCCESS purchase can be refunded."
            )
        if not obj.razorpay_payment_id:
            raise ValueError(f"Purchase {obj.id} has no razorpay_payment_id on record; cannot be refunded through Razorpay.")
        return {
            'total_amount': obj.amount, 'currency': 'INR',  # Purchase has no currency field -- same established INR-default reasoning as ledger/invoice creation above
            'razorpay_payment_id': obj.razorpay_payment_id, 'customer': obj.user, 'item_count': None,
        }

    if order is not None:
        obj = OrderModel.objects.select_for_update().get(pk=order.pk) if lock else order
        if obj.status != OrderModel.Status.PAID:
            raise ValueError(
                f"Order {obj.id} is not in a refundable state (status={obj.status}); "
                "only a PAID order can be refunded."
            )
        if not obj.razorpay_payment_id:
            raise ValueError(f"Order {obj.id} has no razorpay_payment_id on record; cannot be refunded through Razorpay.")
        if obj.currency not in SUPPORTED_REFUND_CURRENCIES:
            raise ValueError(f"Order {obj.id} is in an unsupported currency ({obj.currency}) for refunds.")
        return {
            'total_amount': obj.total_amount, 'currency': obj.currency,
            'razorpay_payment_id': obj.razorpay_payment_id, 'customer': obj.user,
            'item_count': obj.items.count(),
        }

    if subscription_payment is not None:
        obj = SubscriptionPaymentModel.objects.select_for_update().get(pk=subscription_payment.pk) if lock else subscription_payment
        if obj.status != SubscriptionPaymentModel.Status.SUCCESS:
            raise ValueError(
                f"SubscriptionPayment {obj.id} is not in a refundable state (status={obj.status}); "
                "only a SUCCESS charge can be refunded."
            )
        if not obj.razorpay_payment_id:
            raise ValueError(f"SubscriptionPayment {obj.id} has no razorpay_payment_id on record; cannot be refunded through Razorpay.")
        if obj.currency not in SUPPORTED_REFUND_CURRENCIES:
            raise ValueError(f"SubscriptionPayment {obj.id} is in an unsupported currency ({obj.currency}) for refunds.")
        return {
            'total_amount': obj.amount, 'currency': obj.currency,
            'razorpay_payment_id': obj.razorpay_payment_id, 'customer': obj.subscription.user, 'item_count': None,
        }

    raise ValueError("Exactly one of purchase, order, or subscription_payment must be provided.")


def get_refunded_amount(*, purchase=None, order=None, subscription_payment=None):
    """
    Sum of this source's Refund rows that count against its refundable
    total -- REQUESTED and PROCESSING are included alongside SUCCESS
    (not just SUCCESS) so an in-flight refund (already accepted by
    Razorpay, awaiting refund.processed) still reserves its amount and
    can never be double-spent by a second concurrent request; only
    FAILED/REJECTED refunds are excluded, since they returned no money
    and reserve nothing. Always computed fresh from Refund rows, never
    cached -- see Refund.amount's own docstring for why.
    """
    from .models import Refund
    qs = Refund.objects.filter(status__in=[Refund.Status.REQUESTED, Refund.Status.PROCESSING, Refund.Status.SUCCESS])
    if purchase is not None:
        qs = qs.filter(purchase=purchase)
    elif order is not None:
        qs = qs.filter(order=order)
    elif subscription_payment is not None:
        qs = qs.filter(subscription_payment=subscription_payment)
    else:
        raise ValueError("Exactly one of purchase, order, or subscription_payment must be provided.")
    return qs.aggregate(total=Sum('amount'))['total'] or Decimal('0')


def get_refundable_remaining(*, purchase=None, order=None, subscription_payment=None):
    """
    Read-only: how much of this source is still refundable right now.
    Raises ValueError (via _resolve_refund_source) for a source that
    isn't currently refundable at all (wrong status, no razorpay_payment_id,
    unsupported currency) rather than silently returning 0 -- callers that
    want a soft "0 means not refundable" behavior should catch ValueError
    themselves (see finance/views.py's RefundEligibilityView).
    """
    info = _resolve_refund_source(purchase=purchase, order=order, subscription_payment=subscription_payment, lock=False)
    refunded = get_refunded_amount(purchase=purchase, order=order, subscription_payment=subscription_payment)
    return info['total_amount'] - refunded


def get_refund_eligibility(*, purchase=None, order=None, subscription_payment=None):
    """
    Admin-facing "is this transaction still refundable, and by how much"
    lookup (finance/views.py's RefundEligibilityView) -- the "list
    refundable transactions" capability from the approved brief, scoped
    to one already-identified transaction at a time (an admin already
    finds the transaction itself via the existing Purchase/Order/
    SubscriptionPayment admin list views; this only adds the refund-
    specific computation those views don't have) rather than a platform-
    wide scan across three heterogeneous models, which no other feature
    in this app attempts either.
    """
    info = _resolve_refund_source(purchase=purchase, order=order, subscription_payment=subscription_payment, lock=False)
    refunded = get_refunded_amount(purchase=purchase, order=order, subscription_payment=subscription_payment)
    remaining = info['total_amount'] - refunded
    supports_partial = not (order is not None and (info['item_count'] or 0) > 1)
    return {
        'total_amount': info['total_amount'], 'currency': info['currency'],
        'already_refunded': refunded, 'remaining_refundable': remaining,
        'supports_partial_refund': supports_partial,
    }


def create_and_process_refund(*, purchase=None, order=None, subscription_payment=None, amount, reason='', requested_by):
    """
    THE single entry point for creating and immediately attempting a
    refund -- called only from finance/views.py's CreateRefundView
    (admin/superadmin-only, enforced by that view's permission_classes,
    never by a teacher or student). Never trusts `amount` beyond
    validating it here server-side against the actual computed remaining
    balance -- the caller (the view) has already deserialized it from the
    request, but every number that matters is re-checked against the
    database inside the lock below, not merely against whatever the
    client claimed.

    Two-phase, matching CreateOrderView's own established precedent of
    never holding a DB lock during a Razorpay network call:
      1. Inside one short atomic() transaction: lock the source row,
         recompute the true remaining refundable amount (REQUESTED/
         PROCESSING refunds count against it too -- see
         get_refunded_amount), validate the requested amount against it,
         and durably create the Refund row (status=REQUESTED). This is
         the concurrency-safe "reservation" -- two simultaneous requests
         for the same source serialize on the row lock, so neither can
         ever push the combined total past the source's real amount.
      2. Outside any lock: call Razorpay, then apply whatever it reports
         (see _apply_razorpay_refund_response) in its own short,
         separately-locked transaction.

    Raises ValueError for any validation failure (wrong source state,
    amount <= 0, amount exceeds the refundable remainder, or -- see
    below -- a partial refund of a multi-item Order) before ever
    creating a Refund row or calling Razorpay. Raises RefundGatewayError
    if the Razorpay call itself fails, AFTER the Refund row has already
    been created and marked FAILED (see that exception's own docstring).

    Multi-item Order partial refunds: explicitly rejected here, not
    silently mis-attributed. If `order` has 2+ OrderItems and the
    requested amount is less than the order's full total_amount (i.e. a
    genuine partial refund of a multi-item checkout), this raises
    ValueError describing the limitation -- there is no existing rule for
    which item(s) a partial multi-item refund applies to, and guessing
    one (e.g. "spread proportionally across all items") is exactly the
    kind of invented business rule the approved brief says to avoid. A
    FULL refund of a multi-item Order (amount == order.total_amount) is
    unambiguous and IS supported.
    """
    from .models import Refund

    sources_provided = [s for s in (purchase, order, subscription_payment) if s is not None]
    if len(sources_provided) != 1:
        raise ValueError("Exactly one of purchase, order, or subscription_payment must be provided.")

    if amount is None or amount <= 0:
        raise ValueError("Refund amount must be greater than zero.")

    with transaction.atomic():
        info = _resolve_refund_source(purchase=purchase, order=order, subscription_payment=subscription_payment, lock=True)
        already_refunded = get_refunded_amount(purchase=purchase, order=order, subscription_payment=subscription_payment)
        remaining = info['total_amount'] - already_refunded

        if amount > remaining:
            raise ValueError(
                f"Refund amount {amount} exceeds the refundable remainder ({remaining} {info['currency']})."
            )

        if order is not None and (info['item_count'] or 0) > 1 and (already_refunded + amount) < info['total_amount']:
            raise ValueError(
                f"Order {order.id} has {info['item_count']} items -- partial refunds are not supported for "
                "multi-item orders (the existing architecture cannot safely attribute a partial amount to "
                "specific items/instructors for clawback purposes). Only a full refund of the order's "
                f"total_amount ({info['total_amount']} {info['currency']}) is supported for this order. "
                "This is a documented limitation, not an error in your request -- see "
                "create_and_process_refund's docstring."
            )

        refund = Refund.objects.create(
            customer=info['customer'],
            purchase=purchase, order=order, subscription_payment=subscription_payment,
            amount=amount, currency=info['currency'],
            razorpay_payment_id=info['razorpay_payment_id'],
            status=Refund.Status.REQUESTED,
            reason=reason or '',
            requested_by=requested_by,
        )

    try:
        response = _call_razorpay_refund(refund)
    except Exception as e:
        with transaction.atomic():
            locked = Refund.objects.select_for_update().get(pk=refund.pk)
            if locked.status in (Refund.Status.REQUESTED, Refund.Status.PROCESSING):
                locked.status = Refund.Status.FAILED
                locked.failure_reason = str(e)[:2000]
                locked.processed_at = timezone.now()
                locked.save(update_fields=['status', 'failure_reason', 'processed_at', 'updated_at'])
        logger.error(f"Refund {refund.id}: Razorpay refund API call failed: {e}", exc_info=True)
        raise RefundGatewayError(f"Razorpay refund request failed: {e}") from e

    _apply_razorpay_refund_response(refund, response)
    refund.refresh_from_db()
    return refund


def _call_razorpay_refund(refund):
    """
    The sole place client.payment.refund() is ever called. Imports
    `client` from orders/views.py lazily (matching this module's
    established lazy-import convention for cross-app dependencies, and
    letting tests @patch('orders.views.client') exactly the same way
    every other Razorpay-calling test in this codebase already does --
    there is only ever one razorpay.Client instance in this project).
    amount is converted to integer paise the same way every other
    Razorpay amount in this codebase is (int(x * 100)) -- refund.amount
    has already been validated as <= the source's remaining refundable
    balance before this is ever called.
    """
    from orders.views import client
    amount_paise = int(refund.amount * 100)
    return client.payment.refund(refund.razorpay_payment_id, {
        "amount": amount_paise,
        "speed": "normal",
        "notes": {"refund_id": str(refund.id), "reason": (refund.reason or '')[:500]},
    })


def _apply_razorpay_refund_response(refund, response):
    """
    Applies the synchronous response from client.payment.refund(). Per
    Razorpay's own documentation, a "normal" speed refund's synchronous
    response is very often NOT its final state (status is typically
    "pending"/"processing", not "processed") -- this only ever treats the
    response as authoritative when it explicitly says "processed";
    otherwise the Refund is left PROCESSING, and refund.processed (see
    orders/views.py's _reconcile_refund) is what actually confirms it
    later. Locks the Refund row itself (not the source transaction) --
    the source-row lock from create_and_process_refund's reservation
    step has already been released by this point, which is correct: nothing
    about applying Razorpay's response needs the source locked again, only
    this Refund row, to stay consistent under a race with a refund.processed
    webhook that might arrive before this synchronous call even returns.
    """
    from .models import Refund

    razorpay_refund_id = response.get('id') if isinstance(response, dict) else None
    resp_status = response.get('status') if isinstance(response, dict) else None

    with transaction.atomic():
        locked = Refund.objects.select_for_update().get(pk=refund.pk)
        if locked.status not in (Refund.Status.REQUESTED, Refund.Status.PROCESSING):
            # Already resolved -- e.g. a refund.processed webhook raced
            # ahead of this synchronous response and already confirmed
            # (or failed) it. Idempotent no-op, never overwrite a
            # terminal state.
            return
        if razorpay_refund_id:
            locked.razorpay_refund_id = razorpay_refund_id
        if resp_status == 'processed':
            _mark_refund_success_locked(locked, processed_at=timezone.now())
        else:
            locked.status = Refund.Status.PROCESSING
            locked.save(update_fields=['razorpay_refund_id', 'status', 'updated_at'])
            logger.info(
                f"Refund {locked.id}: Razorpay accepted the refund request (status={resp_status!r}, "
                f"razorpay_refund_id={razorpay_refund_id}); awaiting refund.processed webhook for final confirmation."
            )


def _mark_refund_success_locked(refund, *, processed_at):
    """
    Assumes `refund` was already fetched via select_for_update() by the
    caller, inside the SAME still-open transaction -- this function locks
    nothing itself. Idempotent: a refund already SUCCESS is left
    completely untouched (never double-applies a clawback, never
    double-flips a source's status), which is what makes it safe to call
    identically from both the synchronous Razorpay-API-response path
    (_apply_razorpay_refund_response) and the refund.processed webhook
    path (confirm_refund_by_razorpay_id in orders/views.py), even if both
    fire for the same refund.
    """
    from .models import Refund

    if refund.status == Refund.Status.SUCCESS:
        return
    refund.status = Refund.Status.SUCCESS
    refund.processed_at = processed_at
    refund.save(update_fields=['status', 'razorpay_refund_id', 'processed_at', 'updated_at'])
    _update_source_status_if_fully_refunded(refund)
    _create_clawback_for_refund(refund)


def _update_source_status_if_fully_refunded(refund):
    """
    Called only from _mark_refund_success_locked, only once a refund has
    just been confirmed SUCCESS. Purchase/SubscriptionPayment: flips to
    REFUNDED once fully refunded (both Status enums explicitly reserved
    this exact value for this exact moment -- see their own docstrings in
    orders/models.py). Order.status is deliberately left untouched -- see
    this module's Phase 3.5.6 header comment for why.
    """
    from orders.models import Purchase as PurchaseModel
    from orders.models import SubscriptionPayment as SubscriptionPaymentModel

    if refund.purchase_id:
        total = refund.purchase.amount
        refunded = get_refunded_amount(purchase=refund.purchase)
        if refunded >= total:
            PurchaseModel.objects.filter(pk=refund.purchase_id).update(status=PurchaseModel.Status.REFUNDED)
            logger.info(f"Purchase {refund.purchase_id}: fully refunded, status set to REFUNDED.")
    elif refund.subscription_payment_id:
        total = refund.subscription_payment.amount
        refunded = get_refunded_amount(subscription_payment=refund.subscription_payment)
        if refunded >= total:
            SubscriptionPaymentModel.objects.filter(pk=refund.subscription_payment_id).update(status=SubscriptionPaymentModel.Status.REFUNDED)
            logger.info(f"SubscriptionPayment {refund.subscription_payment_id}: fully refunded, status set to REFUNDED.")
    # refund.order_id: no status change -- documented decision, see this module's header comment.


def _create_clawback_for_refund(refund):
    """
    Called only from _mark_refund_success_locked, only once, for a refund
    that has just been confirmed SUCCESS. See this module's Phase 3.5.6
    header comment for the full clawback/payout-interaction design.
    Never raises back to its caller -- a clawback-bookkeeping failure
    must never undo a refund confirmation that has already genuinely
    happened (the customer's money really was returned regardless).
    """
    from .models import LedgerEntry

    try:
        if refund.purchase_id:
            entry = LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, purchase_id=refund.purchase_id).first()
            if entry is None:
                logger.info(f"Refund {refund.id}: no EARNING entry for Purchase {refund.purchase_id}; nothing to claw back.")
                return
            total = refund.purchase.amount
            refunded_to_date = get_refunded_amount(purchase=refund.purchase)
            fraction = min(refunded_to_date / total, Decimal('1')) if total else Decimal('0')
            _apply_clawback_to_entry(entry, fraction=fraction, refund=refund)

        elif refund.subscription_payment_id:
            entry = LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, subscription_payment_id=refund.subscription_payment_id).first()
            if entry is None:
                logger.info(f"Refund {refund.id}: no EARNING entry for SubscriptionPayment {refund.subscription_payment_id}; nothing to claw back.")
                return
            total = refund.subscription_payment.amount
            refunded_to_date = get_refunded_amount(subscription_payment=refund.subscription_payment)
            fraction = min(refunded_to_date / total, Decimal('1')) if total else Decimal('0')
            _apply_clawback_to_entry(entry, fraction=fraction, refund=refund)

        elif refund.order_id:
            order = refund.order
            items = list(order.items.all())
            if len(items) == 1:
                item = items[0]
                entry = LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, order_item_id=item.id).first()
                if entry is None:
                    logger.info(f"Refund {refund.id}: no EARNING entry for OrderItem {item.id}; nothing to claw back.")
                    return
                total = item.total_price
                refunded_to_date = get_refunded_amount(order=order)
                fraction = min(refunded_to_date / total, Decimal('1')) if total else Decimal('0')
                _apply_clawback_to_entry(entry, fraction=fraction, refund=refund)
            else:
                # Multi-item order -- create_and_process_refund's own
                # validation only ever allows a FULL refund to reach this
                # point, so every item's EARNING entry (if any) is clawed
                # back at its full net_amount (fraction=1).
                for item in items:
                    entry = LedgerEntry.objects.filter(entry_type=LedgerEntry.EntryType.EARNING, order_item_id=item.id).first()
                    if entry is None:
                        continue
                    _apply_clawback_to_entry(entry, fraction=Decimal('1'), refund=refund)
    except Exception as e:
        logger.error(f"Refund {refund.id}: unexpected error creating clawback entry: {e}", exc_info=True)


def _apply_clawback_to_entry(entry, *, fraction, refund):
    """
    Creates exactly one new CLAWBACK LedgerEntry reversing `fraction` of
    `entry`'s original gross/commission amounts, net of whatever this
    SAME entry may already have been clawed back (across earlier partial
    refunds of the same source) -- a "cumulative target minus already-
    applied" computation, not a flat per-event proportional amount. This
    guarantees that once an entry is fully refunded (fraction reaches 1,
    whether via one refund or several partials), the sum of all its
    CLAWBACK rows exactly equals its original EARNING gross/commission/net
    amounts to the cent, with no rounding drift able to accumulate across
    multiple partial refunds the way independently-rounded per-event
    amounts could.

    net_amount = gross_amount - commission_amount is preserved for the
    clawback row too (computed from the clawed-back gross/commission
    magnitudes, not independently), matching the exact accounting
    identity the original EARNING entry itself used.

    Idempotent at the DB level via ledgerentry_unique_refund_related_entry
    (a duplicate call for the same (refund, entry) pair -- e.g. a
    refund.processed webhook racing this same confirmation from another
    path -- hits that constraint and is treated as an already-applied
    duplicate, the same try/except IntegrityError pattern used everywhere
    else in this codebase).
    """
    from .models import LedgerEntry

    already = LedgerEntry.objects.filter(
        entry_type=LedgerEntry.EntryType.CLAWBACK, related_entry=entry,
    ).aggregate(gross=Sum('gross_amount'), commission=Sum('commission_amount'))

    already_gross_magnitude = -(already['gross'] or Decimal('0'))
    already_commission_magnitude = -(already['commission'] or Decimal('0'))

    target_gross_magnitude = (entry.gross_amount * fraction).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    target_commission_magnitude = (entry.commission_amount * fraction).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

    this_gross_magnitude = target_gross_magnitude - already_gross_magnitude
    this_commission_magnitude = target_commission_magnitude - already_commission_magnitude
    this_net_magnitude = this_gross_magnitude - this_commission_magnitude

    if this_gross_magnitude <= 0 and this_commission_magnitude <= 0:
        logger.info(f"Refund {refund.id}: LedgerEntry {entry.id} already fully clawed back for this fraction; no new CLAWBACK row created.")
        return

    try:
        with transaction.atomic():
            LedgerEntry.objects.create(
                course_instructor=entry.course_instructor,
                purchase=entry.purchase, order_item=entry.order_item, subscription_payment=entry.subscription_payment,
                entry_type=LedgerEntry.EntryType.CLAWBACK,
                related_entry=entry,
                refund=refund,
                gross_amount=-this_gross_magnitude,
                currency=entry.currency,
                commission_rate=entry.commission_rate,
                commission_amount=-this_commission_magnitude,
                net_amount=-this_net_magnitude,
            )
        logger.info(f"Refund {refund.id}: CLAWBACK entry created against LedgerEntry {entry.id}, net={-this_net_magnitude} {entry.currency}.")
    except IntegrityError:
        logger.info(f"Refund {refund.id}: CLAWBACK entry for LedgerEntry {entry.id} already exists (duplicate call); skipped.")


def confirm_refund_by_razorpay_id(razorpay_refund_id, *, processed_at=None):
    """
    Called from orders/views.py's RazorpayWebhookView for a refund.processed
    event -- per Razorpay's own guidance, the authoritative final-status
    signal, not merely the synchronous API response (see this module's
    header comment). Looks up the local Refund purely by
    razorpay_refund_id and NEVER creates one -- an event for an id this
    table doesn't recognize (e.g. a refund initiated directly from the
    Razorpay dashboard, bypassing this app's admin-only refund API
    entirely) is a real, documented gap (see the Phase 3.5.6 report), not
    silently fabricated into a guessed local record. Idempotent via
    _mark_refund_success_locked -- safe even if this fires twice (a
    genuine Razorpay webhook retry) or races the synchronous response
    path for the same refund. Returns True if a matching Refund was
    found (processed or already-processed no-op), False if not.
    """
    from .models import Refund
    with transaction.atomic():
        refund = Refund.objects.select_for_update().filter(razorpay_refund_id=razorpay_refund_id).first()
        if refund is None:
            return False
        _mark_refund_success_locked(refund, processed_at=processed_at or timezone.now())
    return True


def mark_refund_failed_by_razorpay_id(razorpay_refund_id, *, failure_reason='', processed_at=None):
    """
    Called from orders/views.py's RazorpayWebhookView for a refund.failed
    event. Same lookup-only-never-create contract as
    confirm_refund_by_razorpay_id above. A refund already SUCCESS is left
    untouched rather than downgraded -- Razorpay's own documentation
    describes no path from "processed" back to "failed", but a completed
    financial outcome must never be silently reversed by a stale/
    out-of-order event even if one somehow arrived. Returns True if a
    matching Refund was found, False if not.
    """
    from .models import Refund
    with transaction.atomic():
        refund = Refund.objects.select_for_update().filter(razorpay_refund_id=razorpay_refund_id).first()
        if refund is None:
            return False
        if refund.status == Refund.Status.SUCCESS:
            logger.warning(f"Refund {refund.id}: refund.failed received but refund is already SUCCESS; ignored.")
            return True
        refund.status = Refund.Status.FAILED
        refund.failure_reason = (
            (refund.failure_reason + "\n") if refund.failure_reason else ""
        ) + (failure_reason or "Razorpay reported this refund as failed.")
        refund.processed_at = processed_at or timezone.now()
        refund.save(update_fields=['status', 'failure_reason', 'processed_at', 'updated_at'])
    return True
