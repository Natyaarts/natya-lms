import uuid
from django.db import models
from django.core.exceptions import ValidationError
from users.models import User
from courses.models import Course, Bundle

class Purchase(models.Model):
    # Phase 3.1: was a bare CharField with only a comment listing the
    # allowed values -- nothing stopped an arbitrary string being saved.
    # Confirmed via a repo-wide search before adding this that PENDING,
    # SUCCESS and FAILED are the only three values ever read or written
    # anywhere (production code or tests) -- this is a validation-only
    # tightening, not a behavior or data change. REFUNDED is deliberately
    # NOT added here yet; that belongs to the Phase 3.7 Refunds sub-phase.
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'

    user = models.ForeignKey(User, related_name='purchases', on_delete=models.CASCADE)
    course = models.ForeignKey(Course, related_name='purchases', on_delete=models.CASCADE)
    razorpay_order_id = models.CharField(max_length=255, blank=True, null=True)
    razorpay_payment_id = models.CharField(max_length=255, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(max_length=50, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.course.title} - {self.status}"


class WebhookEvent(models.Model):
    """
    Phase 3.2: one row per inbound Razorpay webhook delivery, keyed on
    Razorpay's own event id (the `x-razorpay-event-id` header -- confirmed
    against Razorpay's current webhook documentation and the installed SDK
    source, not assumed from memory). This is the idempotency/replay-
    protection mechanism: Razorpay guarantees at-least-once delivery and
    will retry on anything but a fast 2xx, so the same event can and will
    arrive more than once in normal operation, not just as an edge case.

    Refined from the original Phase 3 plan sketch (PHASE3_PAYMENTS_FINANCE_PLAN.md
    Part E) in two ways, now that the real Razorpay payload shape is
    confirmed: added `purchase` (explicit payment/order-to-Purchase mapping,
    for admin traceability and to satisfy "payload storage requirements" ->
    "processing/error information" without re-deriving it from raw JSON
    every time) and `error_message` (so a FAILED row records *why*, not
    just that it failed) -- both were implied by this phase's requirements
    but not yet in the original model sketch. Also split IGNORED out from
    PROCESSED so "we understood this event type and deliberately took no
    action" is distinguishable from "we acted on it."
    """
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'Received'
        PROCESSED = 'PROCESSED', 'Processed'
        FAILED = 'FAILED', 'Failed'
        IGNORED = 'IGNORED', 'Ignored'

    razorpay_event_id = models.CharField(max_length=255, unique=True, db_index=True)
    event_type = models.CharField(max_length=100, blank=True)
    payload = models.JSONField()
    purchase = models.ForeignKey(
        Purchase, related_name='webhook_events', on_delete=models.SET_NULL,
        null=True, blank=True
    )
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.RECEIVED)
    error_message = models.TextField(blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-received_at']

    def __str__(self):
        return f"{self.event_type} ({self.razorpay_event_id}) - {self.status}"


class Order(models.Model):
    """
    Phase 3.3: a multi-item purchase (one or more Courses and/or Bundles in
    a single checkout). Deliberately a SEPARATE system from Purchase, not a
    generalization of it -- Purchase keeps working exactly as before for
    the single-course flow (CreateOrderView/VerifyPaymentView/
    fulfill_purchase, all untouched). See PHASE3_PAYMENTS_FINANCE_PLAN.md
    Part C: "Purchase stays untouched... a new, parallel Order/OrderItem
    pair (not a replacement) handles bundles."

    No tax_amount field -- Purchase, the only precedent in this codebase,
    has never had one, and the Phase 3.3 brief says to add it only if the
    current architecture requires it. Not invented here.
    """
    class Status(models.TextChoices):
        PENDING = 'PENDING', 'Pending'
        PAID = 'PAID', 'Paid'
        FAILED = 'FAILED', 'Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    user = models.ForeignKey(User, related_name='orders', on_delete=models.CASCADE)
    # Safe to expose publicly (e.g. in a receipt/URL) unlike the numeric PK.
    order_number = models.CharField(max_length=32, unique=True, editable=False, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    discount_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    razorpay_order_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    razorpay_payment_id = models.CharField(max_length=255, blank=True, null=True)
    razorpay_signature = models.CharField(max_length=255, blank=True, null=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(check=models.Q(subtotal__gte=0), name='order_subtotal_non_negative'),
            models.CheckConstraint(check=models.Q(discount_amount__gte=0), name='order_discount_non_negative'),
            models.CheckConstraint(check=models.Q(total_amount__gte=0), name='order_total_non_negative'),
        ]

    def __str__(self):
        return f"Order {self.order_number} - {self.user.username} - {self.status}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            self.order_number = f"ORD-{uuid.uuid4().hex[:12].upper()}"
        super().save(*args, **kwargs)


class OrderItem(models.Model):
    class ItemType(models.TextChoices):
        COURSE = 'COURSE', 'Course'
        BUNDLE = 'BUNDLE', 'Bundle'

    order = models.ForeignKey(Order, related_name='items', on_delete=models.CASCADE)
    item_type = models.CharField(max_length=10, choices=ItemType.choices)
    # PROTECT, not CASCADE: an OrderItem is financial history -- a Course or
    # Bundle that was once sold must not silently vanish from a past order
    # if it's later deleted. Exactly one of course/bundle is ever set,
    # enforced below.
    course = models.ForeignKey(Course, related_name='order_items', null=True, blank=True, on_delete=models.PROTECT)
    bundle = models.ForeignKey(Bundle, related_name='order_items', null=True, blank=True, on_delete=models.PROTECT)
    title_snapshot = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['id']
        constraints = [
            models.CheckConstraint(
                check=(
                    models.Q(item_type='COURSE', course__isnull=False, bundle__isnull=True) |
                    models.Q(item_type='BUNDLE', course__isnull=True, bundle__isnull=False)
                ),
                name='orderitem_exactly_one_of_course_or_bundle'
            ),
            models.CheckConstraint(check=models.Q(unit_price__gte=0), name='orderitem_unit_price_non_negative'),
            models.CheckConstraint(check=models.Q(total_price__gte=0), name='orderitem_total_price_non_negative'),
            models.CheckConstraint(check=models.Q(quantity__gte=1), name='orderitem_quantity_at_least_one'),
        ]

    def __str__(self):
        return f"{self.title_snapshot} x{self.quantity} ({self.order.order_number})"


# ---------------------------------------------------------------------------
# Phase 3.4.1: SUBSCRIPTIONS -- database foundation only.
#
# A THIRD, separate payment path alongside the legacy single-course Purchase
# and the Phase 3.3 multi-item Order -- none of the three are merged, and
# nothing on Purchase/Order/OrderItem/WebhookEvent above is touched by this
# change. Approved architecture (PHASE3_SUBSCRIPTIONS_AUDIT.md):
#
#     SubscriptionPlan -> Subscription -> SubscriptionPayment
#         -> Invoice (Phase 3.6, not built)
#             -> LedgerEntry (Phase 3.5, not built)
#                 -> Payout (later, not built)
#
# This migration adds ONLY the first three models and their constraints/
# indexes. No Razorpay API call, no webhook handling, no checkout, no access-
# control logic, no cancellation/grace-period workflow, and no frontend are
# part of this change -- all deferred to later Phase 3.4.x sub-phases per
# the approved audit's phased implementation plan.
#
# Placed in `orders` (not `courses`, unlike Bundle) because all three models
# are fundamentally billing/relationship records, not course-catalog data --
# `SubscriptionPlan.courses` is a cross-app FK into `courses.Course` the same
# way `Purchase.course`/`OrderItem.course` already are; keeping the three-
# model chain co-located in one app (rather than splitting SubscriptionPlan
# into `courses`) keeps this tightly-coupled chain easier to reason about as
# a unit. Noted explicitly since the original audit had sketched
# SubscriptionPlan as living alongside Bundle in `courses` -- this is a
# considered deviation, not an oversight.
# ---------------------------------------------------------------------------

class SubscriptionPlan(models.Model):
    """
    The catalog definition of a subscription offering: what it costs, how
    often it bills, and EXACTLY which courses it grants -- never "all
    courses" and never courses added to the catalog later unless an admin
    explicitly adds them to this plan's `courses` (approved business rule:
    additive, explicit, non-retroactive access only).

    `razorpay_plan_id` (added in a small Phase 3.4.1 follow-up, after being
    deliberately omitted from the initial pass) is nullable/blank -- this
    model still creates no Razorpay Plan and calls no Razorpay API; the
    field only gives Phase 3.4.2's eventual "sync this plan to Razorpay"
    action somewhere to write the resulting id, and lets an admin manually
    link an already-existing Razorpay Plan (created via the dashboard) in
    the meantime if ever needed.

    No bundle-expansion field either -- explicitly out of scope for this
    phase per your instruction; a plan only ever references `courses`
    directly for now.
    """
    class BillingInterval(models.TextChoices):
        MONTHLY = 'MONTHLY', 'Monthly'
        YEARLY = 'YEARLY', 'Yearly'

    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True, blank=True)
    description = models.TextField(blank=True)
    billing_interval = models.CharField(max_length=10, choices=BillingInterval.choices)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    # Nullable/blank: existing plans (and every plan created before Phase
    # 3.4.2's Razorpay integration exists) have no Razorpay Plan yet.
    # unique=True when present -- same nullable-unique pattern already used
    # by Subscription.razorpay_subscription_id/Order.razorpay_order_id
    # (multiple NULLs are allowed under a unique constraint; only real,
    # non-null values collide) -- and unique=True already creates the
    # backing DB index needed for lookups, matching those two fields'
    # exact precedent (no separate db_index=True alongside it there either).
    razorpay_plan_id = models.CharField(max_length=255, null=True, blank=True, unique=True)
    # A plan explicitly, individually lists the courses it grants -- no
    # is_published restriction enforced at this layer (a plan may reference
    # a course that isn't published yet, e.g. while being assembled ahead of
    # a course's launch; purchasability/visibility gating is access-control
    # logic, out of scope for this database-only phase, mirroring exactly
    # how Bundle.is_purchasable was kept separate from Bundle's own fields).
    courses = models.ManyToManyField(Course, related_name='subscription_plans', blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_active'], name='subplan_is_active_idx'),
        ]
        constraints = [
            models.CheckConstraint(check=models.Q(price__gte=0), name='subscriptionplan_price_non_negative'),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_billing_interval_display()})"

    def save(self, *args, **kwargs):
        # Identical auto-slug pattern to Bundle.save() -- same collision-
        # suffix loop, same "only generate if not already set" guard.
        if not self.slug:
            from django.utils.text import slugify
            base_slug = slugify(self.name) or 'plan'
            slug = base_slug
            suffix = 1
            while SubscriptionPlan.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                suffix += 1
                slug = f"{base_slug}-{suffix}"
            self.slug = slug
        super().save(*args, **kwargs)


class Subscription(models.Model):
    """
    One student's subscription to a SubscriptionPlan. Local mirror of
    Razorpay's own subscription lifecycle (`status`) plus LMS-owned access
    timing (`access_until`) -- NOT an Enrollment, and never creates one.
    Subscription-granted access is calculated later (Phase 3.4.4, per the
    approved audit) as "permanent Enrollment ownership OR a currently-valid
    Subscription whose plan includes this course" -- deliberately not
    implemented in this database-only phase.

    Status choices are Razorpay's own verified 9-state subscription
    lifecycle (PHASE3_SUBSCRIPTIONS_AUDIT.md Section 4, confirmed against
    Razorpay's current official docs and the installed SDK source, not
    guessed) -- reproduced in full here even though this phase doesn't yet
    drive any of the transitions, because the brief's instruction is for
    this model to be "capable of storing the Razorpay subscription lifecycle
    ... information" now, with the actual webhook-driven transitions wired
    up in a later sub-phase. CREATED and AUTHENTICATED are included (beyond
    the brief's "at minimum" list) because they are real, necessary,
    verified Razorpay states -- CREATED is literally the status Razorpay
    returns the instant a subscription is created (before any payment), and
    AUTHENTICATED is a distinct, real intermediate state (payment method
    validated, billing not yet started) that cannot be safely collapsed into
    ACTIVE. PAUSED is included for the same reason (a real Razorpay state,
    reachable even though no student-facing pause/resume UI exists in V1 --
    Razorpay itself, or a future admin action, could still report it).
    Deliberately NOT inventing an "unlimited/indefinite" status -- Razorpay
    has no such concept (every subscription is bounded by total_count or
    end_at); "until cancelled" is a total_count computed large enough to
    never be reached in practice, decided in Phase 3.4.2 where subscription
    creation actually happens, not represented as a status value here.
    """
    class Status(models.TextChoices):
        CREATED = 'CREATED', 'Created'
        AUTHENTICATED = 'AUTHENTICATED', 'Authenticated'
        ACTIVE = 'ACTIVE', 'Active'
        PENDING = 'PENDING', 'Pending'
        HALTED = 'HALTED', 'Halted'
        PAUSED = 'PAUSED', 'Paused'
        CANCELLED = 'CANCELLED', 'Cancelled'
        EXPIRED = 'EXPIRED', 'Expired'
        COMPLETED = 'COMPLETED', 'Completed'

    # Statuses that mean "this subscription is finished, it no longer blocks
    # the student from starting a new one" -- the same set is used both by
    # the application-level check below and the partial DB constraint, so
    # the two can never silently disagree with each other.
    TERMINAL_STATUSES = (Status.CANCELLED, Status.EXPIRED, Status.COMPLETED)

    user = models.ForeignKey(User, related_name='subscriptions', on_delete=models.CASCADE)
    # PROTECT (not CASCADE): a SubscriptionPlan is financial-history-bearing
    # once any Subscription references it -- matches OrderItem.course/bundle
    # and Purchase's existing "don't let deleting a catalog item silently
    # erase transaction history" precedent.
    plan = models.ForeignKey(SubscriptionPlan, related_name='subscriptions', on_delete=models.PROTECT)
    razorpay_subscription_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    # Denormalized snapshot of the plan's Razorpay plan id at the moment this
    # subscription was created, independent of whatever SubscriptionPlan
    # itself does or doesn't store about Razorpay (see the model-level note
    # on SubscriptionPlan) -- keeps this row fully self-describing for
    # reconciliation even if the plan's own Razorpay linkage ever changes.
    razorpay_plan_id = models.CharField(max_length=255, blank=True, null=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True)
    current_period_start = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)
    # The single field access-control logic will read in a later sub-phase --
    # "this student's subscription-granted access is valid through this
    # instant." Deliberately NOT derived from status alone (e.g. cancellation
    # must not revoke access immediately, per the approved business rules) --
    # left entirely unpopulated/unmanaged by this phase; only the column
    # exists so later phases don't need another migration to add it.
    access_until = models.DateTimeField(null=True, blank=True, db_index=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status'], name='subscription_status_idx'),
            models.Index(fields=['access_until'], name='subscription_access_until_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user'],
                condition=~models.Q(status__in=['CANCELLED', 'EXPIRED', 'COMPLETED']),
                name='unique_active_subscription_per_user',
            ),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.plan.name} - {self.status}"

    def clean(self):
        # Application-level enforcement of "one active subscription per
        # student" (the partial UniqueConstraint above is the database-level
        # safety net for the same rule -- both are required per the approved
        # spec, neither alone is sufficient: full_clean() is what actually
        # runs this on every save via the override below, while the DB
        # constraint is what protects against a raw bulk_create/bulk_update
        # that bypasses full_clean entirely).
        if self.status not in self.TERMINAL_STATUSES:
            conflicting = Subscription.objects.filter(user_id=self.user_id).exclude(
                status__in=self.TERMINAL_STATUSES
            )
            if self.pk:
                conflicting = conflicting.exclude(pk=self.pk)
            if conflicting.exists():
                raise ValidationError({"user": "This student already has an active subscription."})

    def save(self, *args, **kwargs):
        # Matches LiveBatchStudent.save()'s existing precedent in this
        # codebase (courses/models.py) -- calling full_clean() on every save
        # is what makes clean()'s check above actually run, not just be
        # available for a future serializer to opt into.
        self.full_clean()
        super().save(*args, **kwargs)


class SubscriptionPayment(models.Model):
    """
    One row per actual Razorpay recurring charge against a Subscription --
    deliberately NOT named SubscriptionInvoice (per your explicit
    instruction): a formal Invoice (Phase 3.6) will be a distinct concept
    (a billing/tax document, possibly referencing a SubscriptionPayment via
    a nullable FK, mirroring how Purchase/Order are referenced by the
    Invoice design already sketched in PHASE3_PAYMENTS_FINANCE_PLAN.md) --
    not the raw transaction record itself. This model is the subscription
    world's equivalent of Purchase/OrderItem: the actual movement of money.

    REFUNDED is included as a status VALUE (schema completeness, so a
    future Phase 3.7 refund can be recorded without a migration) without any
    refund LOGIC being implemented here -- no code in this phase ever sets
    status to REFUNDED.
    """
    class Status(models.TextChoices):
        CREATED = 'CREATED', 'Created'
        SUCCESS = 'SUCCESS', 'Success'
        FAILED = 'FAILED', 'Failed'
        REFUNDED = 'REFUNDED', 'Refunded'

    # PROTECT: a SubscriptionPayment is itself the financial record --
    # deleting the parent Subscription must never silently take payment
    # history with it. Matches the explicit "use PROTECT ... so historical
    # financial records are not accidentally deleted" instruction.
    subscription = models.ForeignKey(Subscription, related_name='payments', on_delete=models.PROTECT)
    razorpay_payment_id = models.CharField(max_length=255, blank=True, null=True, unique=True)
    # Denormalized snapshot of the subscription's Razorpay id at charge time
    # -- explicitly requested for traceability, lets this row be reconciled
    # against a Razorpay payment/subscription record without a join, and
    # survives being self-describing even under PROTECT (which in practice
    # means the parent row can never actually vanish, but this still avoids
    # a join for the common "which Razorpay subscription was this against"
    # lookup).
    razorpay_subscription_id = models.CharField(max_length=255, blank=True, null=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3, default='INR')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.CheckConstraint(check=models.Q(amount__gte=0), name='subscriptionpayment_amount_non_negative'),
        ]

    def __str__(self):
        return f"{self.subscription.user.username} - {self.amount} {self.currency} - {self.status}"
