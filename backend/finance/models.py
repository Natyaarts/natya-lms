import uuid

from django.db import models

from users.models import User


class Payout(models.Model):
    """
    Phase 3.5.2. Schema only -- no batching, no processing, no API, no
    bank-detail storage, no automatic payout exists yet (all explicitly
    deferred per the Phase 3.5.1 audit's subphase plan, 3.5.4+). This
    model exists now purely so LedgerEntry.payout has somewhere to point
    once batching is eventually built, without needing a migration then.

    Unified for both Teacher and Mentor recipients (not split into
    TeacherPayout/MentorPayout) -- `recipient` is a plain FK to User;
    which role they earned under is determined by their CourseInstructor
    rows, not by which table the payout lives in. Payout mechanics
    (approval, transfer, status tracking) don't differ by role.

    No code in this phase ever creates a Payout row. Django admin for
    this model is read-only (see finance/admin.py) for the same reason
    LedgerEntry's is: a manually created/edited Payout would be a
    fabricated or falsified financial record with no real batching
    process behind it.
    """
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        APPROVED = 'APPROVED', 'Approved'
        PROCESSING = 'PROCESSING', 'Processing'
        PAID = 'PAID', 'Paid'
        FAILED = 'FAILED', 'Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    class Method(models.TextChoices):
        BANK_TRANSFER = 'BANK_TRANSFER', 'Bank Transfer'
        UPI = 'UPI', 'UPI'
        OTHER = 'OTHER', 'Other'

    recipient = models.ForeignKey(User, related_name='payouts', on_delete=models.PROTECT)
    period_start = models.DateField()
    period_end = models.DateField()
    # Sums of this Payout's linked LedgerEntry rows once batching exists --
    # never independently entered. Defaulted to 0 rather than left
    # required, since nothing populates them yet.
    gross_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    net_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    method = models.CharField(max_length=20, choices=Method.choices, blank=True)
    reference_number = models.CharField(max_length=255, blank=True, help_text="Bank transfer UTR or similar, once a payout is actually processed.")
    approved_by = models.ForeignKey(User, related_name='approved_payouts', null=True, blank=True, on_delete=models.SET_NULL)
    approved_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['recipient', 'status']),
        ]

    def __str__(self):
        return f"Payout #{self.id} - {self.recipient.username} - {self.status}"


class LedgerEntry(models.Model):
    """
    Phase 3.5.2. THE finance ledger -- one row per (revenue-eligible
    CourseInstructor, successful transaction) attribution. See
    PHASE3_PAYMENTS_FINANCE_PLAN.md and the Phase 3.5.1 audit report for
    the full architecture rationale (transaction/attribution ledger, not
    double-entry -- see the audit's Ledger Architecture section for why).

    Created ONLY by finance/services.py, called from exactly three
    already-proven fulfillment success paths (orders/services.py's
    fulfill_purchase/fulfill_order, orders/views.py's
    _record_subscription_charge) -- never from an API, never from Django
    admin, never decides whether a payment succeeded (it is only ever
    invoked after that's already been established by its caller).

    Immutable once created: every amount/rate field here is a snapshot,
    never recalculated or edited afterward (see commission_rate's own
    docstring below for why this matters most). A correction is always a
    NEW row (entry_type=ADJUSTMENT/CLAWBACK, related_entry pointing at
    what it corrects) -- never an UPDATE on an existing row.

    Phase 3.5.6: CLAWBACK entries are now actually created (by
    finance/services.py's refund handling, never anywhere else), reversing
    part or all of an original EARNING entry's net_amount when its source
    transaction is refunded. The original EARNING row is NEVER edited --
    a CLAWBACK is always a separate, negative-valued row with `related_entry`
    pointing back at what it corrects, exactly as this docstring always
    said it would work. No code anywhere creates an ADJUSTMENT entry yet
    (no use case has arisen for one) -- that choice exists for a still-
    later, still-undesigned need.
    """
    class EntryType(models.TextChoices):
        EARNING = 'EARNING', 'Earning'
        CLAWBACK = 'CLAWBACK', 'Clawback'
        ADJUSTMENT = 'ADJUSTMENT', 'Adjustment'

    course_instructor = models.ForeignKey(
        'courses.CourseInstructor', related_name='ledger_entries', on_delete=models.PROTECT,
        help_text="Who this entry attributes revenue to. PROTECT: financial history must survive a CourseInstructor row ever being removed.",
    )

    # Exactly one of these three is ever set -- enforced by the
    # ledgerentry_exactly_one_source CheckConstraint below, and by
    # construction in finance/services.py (each of the three creation
    # helpers passes exactly one). SET_NULL, not PROTECT/CASCADE: the
    # ledger entry itself remains the durable financial record even in
    # the extremely unlikely event its source row is ever removed --
    # matching Invoice/Refund's planned same treatment in the audit.
    purchase = models.ForeignKey('orders.Purchase', related_name='ledger_entries', null=True, blank=True, on_delete=models.SET_NULL)
    order_item = models.ForeignKey('orders.OrderItem', related_name='ledger_entries', null=True, blank=True, on_delete=models.SET_NULL)
    subscription_payment = models.ForeignKey('orders.SubscriptionPayment', related_name='ledger_entries', null=True, blank=True, on_delete=models.SET_NULL)

    entry_type = models.CharField(max_length=20, choices=EntryType.choices, default=EntryType.EARNING)
    related_entry = models.ForeignKey(
        'self', related_name='corrections', null=True, blank=True, on_delete=models.SET_NULL,
        help_text="What a CLAWBACK/ADJUSTMENT entry corrects. Set on every CLAWBACK entry (Phase 3.5.6); still unused for ADJUSTMENT, which no code creates.",
    )
    # Phase 3.5.6: which Refund caused this CLAWBACK, if any. Only ever set
    # on entry_type=CLAWBACK rows -- EARNING entries predate refunds
    # existing at all and are never retroactively linked to one. SET_NULL
    # (not PROTECT/CASCADE): this row remains the durable financial record
    # even if the Refund it resulted from were ever removed, matching
    # purchase/order_item/subscription_payment's own SET_NULL reasoning
    # above. Doubles as the idempotency key that stops the same refund
    # (a retried webhook delivery racing the synchronous API-response
    # path, for example) from ever creating a second clawback against the
    # same original entry -- see ledgerentry_unique_refund_related_entry
    # below.
    refund = models.ForeignKey(
        'Refund', related_name='clawback_entries', null=True, blank=True, on_delete=models.SET_NULL,
    )

    gross_amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')

    # A SNAPSHOT of CourseInstructor.commission_rate at the moment this
    # entry was created -- never re-read from CourseInstructor after the
    # fact. This is the single most important immutability rule in this
    # model: if an admin edits CourseInstructor.commission_rate next
    # month, every past LedgerEntry must keep showing the rate that was
    # actually in effect when that sale happened, or historical earnings
    # would silently be rewritten.
    commission_rate = models.DecimalField(max_digits=5, decimal_places=2)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2)
    net_amount = models.DecimalField(max_digits=10, decimal_places=2)

    payout = models.ForeignKey(Payout, related_name='entries', null=True, blank=True, on_delete=models.SET_NULL, help_text="Null = not yet batched into a payout.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            # Exactly one of purchase/order_item/subscription_payment is
            # ever set. Expressed as an explicit OR of the three valid
            # combinations (matching OrderItem's existing
            # orderitem_exactly_one_of_course_or_bundle precedent) rather
            # than boolean arithmetic on IS NULL checks, which is not
            # portable between SQLite (this project's test backend) and
            # PostgreSQL (production).
            models.CheckConstraint(
                condition=(
                    models.Q(purchase__isnull=False, order_item__isnull=True, subscription_payment__isnull=True) |
                    models.Q(purchase__isnull=True, order_item__isnull=False, subscription_payment__isnull=True) |
                    models.Q(purchase__isnull=True, order_item__isnull=True, subscription_payment__isnull=False)
                ),
                name='ledgerentry_exactly_one_source',
            ),
            # Idempotency at the DB level: a given source transaction can
            # have at most one EARNING entry ever. Three separate partial
            # UniqueConstraints (one per nullable source field) rather
            # than one unique_together across all three, because a plain
            # unique_together over nullable columns would not behave
            # correctly here -- both SQLite and PostgreSQL treat multiple
            # NULLs in a unique index as distinct, which is exactly why a
            # `condition=` partial constraint (this project's already-
            # established pattern for Subscription's
            # unique_active_subscription_per_user) is used instead.
            #
            # Phase 3.5.6 amendment: each condition now also pins
            # entry_type='EARNING' explicitly (originally just
            # purchase__isnull=False, etc., back when EARNING was the only
            # entry_type any code ever created). Narrowed, not loosened --
            # "at most one EARNING per source" is still enforced exactly
            # as before. This makes room for entry_type=CLAWBACK, which
            # now legitimately CAN repeat against the same source (one row
            # per partial refund) -- its own, differently-scoped
            # idempotency constraint is ledgerentry_unique_refund_related_entry
            # below, not this one.
            models.UniqueConstraint(
                fields=['purchase', 'entry_type'],
                condition=models.Q(purchase__isnull=False, entry_type='EARNING'),
                name='ledgerentry_unique_purchase_entry_type',
            ),
            models.UniqueConstraint(
                fields=['order_item', 'entry_type'],
                condition=models.Q(order_item__isnull=False, entry_type='EARNING'),
                name='ledgerentry_unique_orderitem_entry_type',
            ),
            models.UniqueConstraint(
                fields=['subscription_payment', 'entry_type'],
                condition=models.Q(subscription_payment__isnull=False, entry_type='EARNING'),
                name='ledgerentry_unique_subscriptionpayment_entry_type',
            ),
            # Phase 3.5.6: idempotency for CLAWBACK creation -- at most one
            # CLAWBACK entry per (refund, original entry it corrects) pair.
            # This is what makes _apply_clawback_to_entry's create-or-skip
            # logic safe under a race (the synchronous Razorpay-API-response
            # path and a refund.processed webhook delivery both trying to
            # confirm the same refund at nearly the same moment) -- the
            # second attempt hits this constraint and is treated as an
            # already-applied duplicate, exactly like every other
            # try/except-IntegrityError idempotency check in this codebase.
            # Does NOT block multiple *different* CLAWBACK rows against the
            # same original `related_entry` from *different* refunds (e.g.
            # two separate partial refunds of the same purchase) -- refund
            # is part of the uniqueness key precisely so that is allowed.
            models.UniqueConstraint(
                fields=['refund', 'related_entry'],
                condition=models.Q(refund__isnull=False),
                name='ledgerentry_unique_refund_related_entry',
            ),
        ]
        indexes = [
            # Matches the approved schema's "fast unpaid-balance query"
            # requirement -- "every EARNING entry for instructor X with
            # payout IS NULL" is exactly the query a future payout-
            # batching phase runs.
            models.Index(fields=['course_instructor', 'payout']),
        ]

    def __str__(self):
        return f"LedgerEntry #{self.id} - {self.entry_type} - {self.course_instructor_id} - {self.net_amount}"


class Invoice(models.Model):
    """
    Phase 3.5.5. Customer-facing invoice/receipt for a successful,
    completed transaction -- deliberately separate from LedgerEntry
    (instructor-facing revenue attribution, never customer-facing, and
    never cross-referenced with this model). One Invoice per
    TRANSACTION: for an Order this means one invoice covering the whole
    checkout (all its OrderItems), never one per item -- matching how a
    real receipt is issued per transaction, not per line item.
    LedgerEntry, by contrast, is deliberately per-OrderItem, since
    instructor attribution needs that finer granularity; these two
    models intentionally serve different granularities of the same
    underlying transaction.

    Lives in finance/ rather than orders/ (unlike the original Phase
    3.5.1 audit's initial sketch, written before this app existed) --
    every other model in this same Phase 3.5 arc (LedgerEntry, Payout)
    already lives here, and keeping invoicing alongside them is more
    consistent than splitting it across two apps.

    No tax_amount field, not even as an unused placeholder: "do not
    invent a tax/GST engine" and "tax fields ONLY if already supported"
    -- nothing in this codebase supports tax calculation anywhere, so
    there is nothing to snapshot. A tax_amount field is a trivial,
    genuinely additive migration whenever a real tax engine is actually
    built later.

    pdf_file exists as a schema-only placeholder -- no PDF generation
    library exists anywhere in requirements.txt today (confirmed by a
    fresh check before writing this), and adding one (WeasyPrint,
    ReportLab, etc.) is exactly the kind of "large PDF infrastructure"
    decision this phase's brief says to report rather than silently
    add. See the Phase 3.5.5 report's PDF section.
    """
    class Status(models.TextChoices):
        ISSUED = 'ISSUED', 'Issued'
        CANCELLED = 'CANCELLED', 'Cancelled'

    invoice_number = models.CharField(max_length=32, unique=True, editable=False, db_index=True)
    customer = models.ForeignKey(User, related_name='invoices', on_delete=models.PROTECT)

    # Exactly one of these three is ever set -- same enforced pattern as
    # LedgerEntry's purchase/order_item/subscription_payment trio (see
    # that model's own comment for the portability reasoning behind an
    # explicit OR-of-combinations CheckConstraint over boolean
    # arithmetic). Note this is `order`, NOT `order_item` -- one invoice
    # per whole checkout transaction, not per course within it.
    purchase = models.ForeignKey('orders.Purchase', related_name='invoices', null=True, blank=True, on_delete=models.SET_NULL)
    order = models.ForeignKey('orders.Order', related_name='invoices', null=True, blank=True, on_delete=models.SET_NULL)
    subscription_payment = models.ForeignKey('orders.SubscriptionPayment', related_name='invoices', null=True, blank=True, on_delete=models.SET_NULL)

    # Snapshots, taken once at issuance -- never re-derived from the
    # source transaction or from Course/SubscriptionPlan's current price
    # afterward, so a later price/plan change can never alter a
    # historical invoice (see create_invoice_for_*'s docstrings in
    # finance/services.py for exactly when each is captured).
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    payment_date = models.DateTimeField(help_text="When the underlying payment actually completed.")

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ISSUED)
    pdf_file = models.FileField(upload_to='invoices/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(purchase__isnull=False, order__isnull=True, subscription_payment__isnull=True) |
                    models.Q(purchase__isnull=True, order__isnull=False, subscription_payment__isnull=True) |
                    models.Q(purchase__isnull=True, order__isnull=True, subscription_payment__isnull=False)
                ),
                name='invoice_exactly_one_source',
            ),
            # Idempotency at the DB level: at most one Invoice can ever
            # exist per source transaction, regardless of how many times
            # fulfillment/verification/webhook delivery/admin mark-paid
            # runs for it.
            models.UniqueConstraint(fields=['purchase'], condition=models.Q(purchase__isnull=False), name='invoice_unique_purchase'),
            models.UniqueConstraint(fields=['order'], condition=models.Q(order__isnull=False), name='invoice_unique_order'),
            models.UniqueConstraint(fields=['subscription_payment'], condition=models.Q(subscription_payment__isnull=False), name='invoice_unique_subscription_payment'),
        ]

    def save(self, *args, **kwargs):
        # Mirrors Order.save()'s exact existing precedent (orders/models.py)
        # -- UUID4-based, not a sequential counter, so no coordination/
        # locking is needed between concurrent requests for "the next
        # number"; the unique constraint above is the DB-level backstop
        # either way. Only ever generated once: a re-save of an existing
        # invoice never touches an already-set invoice_number, matching
        # "immutable after issuance".
        if not self.invoice_number:
            self.invoice_number = f"INV-{uuid.uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Invoice {self.invoice_number} - {self.customer.username} - {self.status}"


class Refund(models.Model):
    """
    Phase 3.5.6. One row per refund REQUEST/ATTEMPT against a source
    transaction (Purchase, Order, or SubscriptionPayment) -- deliberately
    NOT one row per source transaction, since a source can legitimately be
    refunded more than once (multiple partial refunds). See
    finance/services.py's refund module docstring for the full
    architecture writeup: refundability rules per source type, the
    Razorpay integration, clawback creation, and the payout-interaction/
    Invoice/Enrollment decisions this phase deliberately did NOT implement
    automatically.

    Created ONLY by finance/services.py's create_and_process_refund(),
    called ONLY from the admin-only refund API (finance/views.py) --
    never from Django admin (see finance/admin.py's RefundAdmin, read-only
    for the same reason LedgerEntry/Payout's admin is), never by a
    teacher/student, and never inferred/guessed from a webhook alone (an
    incoming refund.* webhook for a razorpay_refund_id this table doesn't
    already know about is logged and left unmatched, not auto-created --
    see _reconcile_refund's docstring in orders/views.py).

    Exactly one of purchase/order/subscription_payment is ever set (same
    enforced CheckConstraint pattern as LedgerEntry/Invoice above) --
    but, UNLIKE those two models, there is deliberately NO uniqueness
    constraint on the source field(s) themselves: multiple Refund rows
    against the very same source are the expected shape for partial
    refunds, not a bug to prevent. Idempotency instead comes from
    razorpay_refund_id being unique (nullable, since it's only known once
    Razorpay actually responds) -- a duplicate refund.processed webhook
    delivery for the same Razorpay-side refund can never create a second
    local row for it.
    """
    class Status(models.TextChoices):
        REQUESTED = 'REQUESTED', 'Requested'
        PROCESSING = 'PROCESSING', 'Processing'
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'
        REJECTED = 'REJECTED', 'Rejected'

    # Denormalized, exactly like Invoice.customer -- makes "my own refund
    # history" (GET /api/finance/my-refunds/) a single indexed filter
    # rather than a 3-way OR across purchase__user/order__user/
    # subscription_payment__subscription__user. PROTECT for the same
    # "financial history must survive a user deletion" reasoning
    # Invoice.customer already established.
    customer = models.ForeignKey(User, related_name='refunds', on_delete=models.PROTECT)

    # Exactly one of these three is ever set -- see the class docstring
    # for why this trio has no accompanying uniqueness constraint, unlike
    # LedgerEntry/Invoice's identical-looking trio. Note this is `order`,
    # matching Invoice's granularity (one Razorpay payment per Order,
    # refunded as a whole against that payment) -- NOT `order_item`,
    # which is LedgerEntry's finer, per-instructor-attribution
    # granularity. See create_and_process_refund's docstring for exactly
    # how a multi-item Order's per-item clawback is derived from a single
    # order-level Refund row.
    purchase = models.ForeignKey('orders.Purchase', related_name='refunds', null=True, blank=True, on_delete=models.SET_NULL)
    order = models.ForeignKey('orders.Order', related_name='refunds', null=True, blank=True, on_delete=models.SET_NULL)
    subscription_payment = models.ForeignKey('orders.SubscriptionPayment', related_name='refunds', null=True, blank=True, on_delete=models.SET_NULL)

    # This refund's own amount -- NOT the source transaction's total, and
    # NOT a running balance. "How much is left refundable" is always
    # computed on demand (finance/services.py's get_refundable_remaining)
    # from the sum of this source's other Refund rows, never cached on
    # any row, so it can never drift out of sync with reality.
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')

    # Snapshot of which Razorpay payment this refund was requested
    # against -- captured at creation time from the source transaction's
    # own razorpay_payment_id, so this row remains self-describing even
    # if that source row's id were ever to change (it never does in
    # practice, but Purchase/Order/SubscriptionPayment all have no
    # immutability guarantee of their own on that field).
    razorpay_payment_id = models.CharField(max_length=255, blank=True)
    # Razorpay's own refund id (e.g. "rfnd_..."), captured from the
    # synchronous client.payment.refund() response as soon as it's known.
    # Null only for the brief REQUESTED window before that call is made
    # (or if it never successfully completes at all -- see FAILED below).
    razorpay_refund_id = models.CharField(max_length=255, null=True, blank=True, unique=True)

    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    # Free-text admin-supplied reason for the refund (e.g. "duplicate
    # charge", "student requested, course quality issue") -- never
    # Razorpay-sourced, this is why an admin says they're refunding.
    reason = models.CharField(max_length=255, blank=True)
    # Razorpay's own error detail when the refund attempt itself fails
    # (SDK exception message) -- distinct from `reason`, which is why the
    # refund was requested in the first place.
    failure_reason = models.TextField(blank=True)

    # The admin/superadmin who initiated this refund -- refund
    # creation/processing is admin-only (see finance/views.py's
    # CreateRefundView), so this is never null. PROTECT: an admin action
    # this consequential must remain attributable even if that admin
    # account is later deleted.
    requested_by = models.ForeignKey(User, related_name='refunds_requested', on_delete=models.PROTECT)
    requested_at = models.DateTimeField(auto_now_add=True)
    # When Razorpay (via its synchronous response or, authoritatively, the
    # refund.processed webhook) confirmed this refund as actually SUCCESS
    # or FAILED -- null while still REQUESTED/PROCESSING.
    processed_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-requested_at']
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(purchase__isnull=False, order__isnull=True, subscription_payment__isnull=True) |
                    models.Q(purchase__isnull=True, order__isnull=False, subscription_payment__isnull=True) |
                    models.Q(purchase__isnull=True, order__isnull=True, subscription_payment__isnull=False)
                ),
                name='refund_exactly_one_source',
            ),
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='refund_amount_positive'),
        ]
        indexes = [
            models.Index(fields=['customer', 'status']),
        ]

    def __str__(self):
        return f"Refund #{self.id} - {self.customer.username} - {self.amount} {self.currency} - {self.status}"
