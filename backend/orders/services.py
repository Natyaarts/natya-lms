"""
Single source of truth for what happens when a Purchase becomes SUCCESS.

Before this existed, "successful payment -> enroll the student" was
duplicated across four call sites with inconsistent results: some enrolled
the student, some only marked the purchase paid and left them without
access. See ARCHITECTURE_PROPOSAL.md (Phase 0) for the audit that found
this. This module exists so every call site does the same thing.
"""

import logging

from courses.models import Enrollment
from notifications.services import NotificationService

logger = logging.getLogger(__name__)


def _grant_course_access(user, course):
    """
    The single place that turns "this user paid for this course" into
    actual access, for EVERY payment path in the app (legacy Purchase,
    Phase 3.3 Order/Bundle). Enrollment.get_or_create is what makes this
    safe to call more than once for the same (user, course) -- an already-
    enrolled student is a no-op, not a duplicate row or an error. This is
    also what makes "a bundle course the student already owns" (Phase 3.3
    requirement) just work: calling this for an already-owned course
    changes nothing and raises nothing.
    """
    Enrollment.objects.get_or_create(user=user, course=course)


def fulfill_purchase(purchase, previous_status):
    """
    Call this immediately after saving `purchase.status = 'SUCCESS'`
    (or right after creating a Purchase that is *already* SUCCESS),
    passing the status value from *before* that change as `previous_status`
    -- this is what the payment notification uses to fire only on a genuine
    transition into SUCCESS, not on every save. For a brand-new purchase,
    pass any non-'SUCCESS' value (e.g. 'PENDING').

    Idempotent / safe to call repeatedly and from multiple places:
    - Enrollment uses get_or_create, so re-fulfilling an already-enrolled
      purchase is a no-op.
    - The notification is deduped via NotificationService's idempotency_key
      (see NotificationService.trigger_payment_success), so it will not be
      sent twice for the same purchase.

    No-ops entirely if the purchase is not (or no longer) SUCCESS.
    """
    if purchase.status != 'SUCCESS':
        return

    _grant_course_access(purchase.user, purchase.course)
    NotificationService.trigger_payment_success(purchase, previous_status)

    # Phase 3.5.2: revenue attribution, hooked into this already-proven
    # success path. Never affects the enrollment/notification above --
    # see finance/services.py's own docstring for why this can never
    # raise back into this function.
    from finance.services import create_earning_entry_for_purchase, create_invoice_for_purchase
    create_earning_entry_for_purchase(purchase)
    # Phase 3.5.5: customer invoice/receipt -- an independent side effect
    # of this same success event, never affecting or affected by the
    # ledger entry above (see create_invoice_for_purchase's docstring).
    create_invoice_for_purchase(purchase)


def fulfill_order(order, previous_status):
    """
    Phase 3.3 sibling of fulfill_purchase(), same idempotent shape and same
    contract (call right after saving `order.status = Order.Status.PAID`,
    pass the pre-change status as `previous_status`). Reuses the exact same
    _grant_course_access() helper fulfill_purchase() does -- there is only
    ever one enrollment mechanism in this app, not two.

    A COURSE item grants that one course. A BUNDLE item grants every course
    inside the bundle (a course already owned by the student, whether from
    an earlier Purchase, a different Order, or direct admin assignment, is
    silently skipped by _grant_course_access's own get_or_create -- the
    whole order is never partially failed because of prior ownership).

    No-ops entirely if the order is not (or no longer) PAID.
    """
    if order.status != 'PAID':
        return

    # Phase 3.5.2: revenue attribution, hooked into this already-proven
    # success path -- one LedgerEntry attempt per item, same as the
    # existing per-item access-granting loop. A BUNDLE item's courses are
    # resolved to a single instructor only if the bundle contains exactly
    # one course; otherwise it's a safe no-op (see
    # finance/services.py's create_earning_entry_for_order_item /
    # _resolve_primary_instructor docstrings -- multi-instructor bundle
    # splitting is explicitly out of scope for this phase).
    from finance.services import create_earning_entry_for_order_item, create_invoice_for_order

    for item in order.items.select_related('course').prefetch_related('bundle__courses'):
        if item.item_type == 'COURSE' and item.course_id:
            _grant_course_access(order.user, item.course)
        elif item.item_type == 'BUNDLE' and item.bundle_id:
            for course in item.bundle.courses.all():
                _grant_course_access(order.user, course)
        create_earning_entry_for_order_item(item, order.currency)

    # Phase 3.5.5: ONE invoice per Order (the whole checkout), not per
    # item -- called once here, outside the per-item loop above.
    create_invoice_for_order(order)

    NotificationService.trigger_order_payment_success(order, previous_status)
