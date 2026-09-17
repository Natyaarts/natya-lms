from decimal import Decimal

from rest_framework import serializers

from .models import Invoice, LedgerEntry, Payout, Refund


class MyEarningLedgerEntrySerializer(serializers.ModelSerializer):
    """
    Phase 3.5.3. GET /api/finance/my-earnings/ -- the authenticated
    teacher/mentor's OWN earnings only. The view's get_queryset already
    scopes this to request.user's CourseInstructor rows before this ever
    serializes anything (this serializer itself has no access-control
    logic, matching every other serializer in this codebase's established
    "scoping happens in the view" convention).

    Deliberately never exposes: razorpay ids (this model never stores
    any -- they live only on Purchase/Order/SubscriptionPayment, which
    this serializer never touches), the raw purchase/order_item/
    subscription_payment FK ids, webhook payload data, or another
    instructor's identity -- an instructor already knows these are their
    own earnings from the URL alone, so course_instructor/user are not
    restated here (unlike the admin serializer below, whose entire point
    is showing whose earnings these are).
    """
    course = serializers.SerializerMethodField()
    payout_status = serializers.SerializerMethodField()

    class Meta:
        model = LedgerEntry
        fields = [
            'id', 'course', 'entry_type', 'gross_amount', 'commission_rate',
            'commission_amount', 'net_amount', 'currency', 'payout_status', 'created_at',
        ]
        read_only_fields = fields

    def get_course(self, obj):
        course = obj.course_instructor.course
        return {'id': course.id, 'title': course.title}

    def get_payout_status(self, obj):
        # None = not yet batched into a payout (the only state possible
        # today, since no code creates a Payout yet -- Phase 3.5.4+).
        return obj.payout.status if obj.payout_id else None


class AdminLedgerEntrySerializer(serializers.ModelSerializer):
    """
    Phase 3.5.3. Admin-only ledger inspection
    (GET /api/finance/admin/ledger-entries/). Unlike
    MyEarningLedgerEntrySerializer, this exposes WHICH instructor/course
    an entry belongs to and its source TYPE -- the entire point of admin
    inspection -- but still never a Razorpay id, a webhook payload, or
    any payment-verification detail; those remain visible only through
    Purchase/Order/SubscriptionPayment's own existing admin pages, never
    duplicated here.
    """
    course = serializers.SerializerMethodField()
    instructor = serializers.SerializerMethodField()
    source_type = serializers.SerializerMethodField()
    payout_status = serializers.SerializerMethodField()

    class Meta:
        model = LedgerEntry
        fields = [
            'id', 'instructor', 'course', 'source_type', 'entry_type',
            'gross_amount', 'commission_rate', 'commission_amount', 'net_amount',
            'currency', 'payout', 'payout_status', 'created_at',
        ]
        read_only_fields = fields

    def get_course(self, obj):
        course = obj.course_instructor.course
        return {'id': course.id, 'title': course.title}

    def get_instructor(self, obj):
        user = obj.course_instructor.user
        return {'id': user.id, 'username': user.username}

    def get_source_type(self, obj):
        if obj.purchase_id:
            return 'PURCHASE'
        if obj.order_item_id:
            return 'ORDER_ITEM'
        if obj.subscription_payment_id:
            return 'SUBSCRIPTION_PAYMENT'
        return None  # unreachable in practice -- the model's own exactly-one-source constraint guarantees one of the above

    def get_payout_status(self, obj):
        return obj.payout.status if obj.payout_id else None


class PayoutRecipientSerializer(serializers.Serializer):
    """Minimal User shape for nesting inside Payout responses -- id/username
    only, mirroring AdminLedgerEntrySerializer.get_instructor's exact shape."""
    id = serializers.IntegerField()
    username = serializers.CharField()


class PayoutListSerializer(serializers.ModelSerializer):
    """
    Phase 3.5.4. GET /api/finance/admin/payouts/ -- admin-only list view.
    entry_count (not the full nested entry list -- that's
    PayoutDetailSerializer's job) keeps a list of many payouts cheap to
    render. No bank/UPI/payout-provider detail exists on this model at
    all (explicitly excluded from this phase), so there is nothing
    sensitive to withhold here beyond what LedgerEntry serializers
    already withhold.
    """
    recipient = PayoutRecipientSerializer()
    approved_by = PayoutRecipientSerializer(allow_null=True)
    entry_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Payout
        fields = [
            'id', 'recipient', 'period_start', 'period_end', 'gross_amount',
            'commission_amount', 'net_amount', 'status', 'method', 'reference_number',
            'approved_by', 'approved_at', 'paid_at', 'entry_count', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class PayoutDetailSerializer(PayoutListSerializer):
    """GET /api/finance/admin/payouts/<id>/ -- adds the full list of
    batched LedgerEntry rows (reusing AdminLedgerEntrySerializer
    unchanged, so a payout's entries are shown in exactly the same shape
    admin ledger inspection already uses)."""
    entries = AdminLedgerEntrySerializer(many=True, read_only=True)

    class Meta(PayoutListSerializer.Meta):
        fields = PayoutListSerializer.Meta.fields + ['entries']


class CreatePayoutInputSerializer(serializers.Serializer):
    """
    Phase 3.5.4. Input validation ONLY for POST /api/finance/admin/payouts/create/
    -- mirrors TranslatedAudioUploadSerializer's established precedent
    (a plain Serializer, not a ModelSerializer, since the actual Payout
    creation/validation logic lives in finance/services.py's
    create_payout_batch, not here). This serializer only shapes/type-checks
    the request body; it never touches a model directly.
    """
    recipient_id = serializers.IntegerField()
    ledger_entry_ids = serializers.ListField(child=serializers.IntegerField(), allow_empty=False)
    period_start = serializers.DateField()
    period_end = serializers.DateField()
    method = serializers.ChoiceField(choices=Payout.Method.choices, required=False, allow_blank=True, default='')
    reference_number = serializers.CharField(required=False, allow_blank=True, default='')
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        if attrs['period_end'] < attrs['period_start']:
            raise serializers.ValidationError("period_end cannot be before period_start.")
        return attrs


class MarkPayoutPaidInputSerializer(serializers.Serializer):
    """
    Input validation ONLY for POST /api/finance/admin/payouts/<id>/mark-paid/
    -- same plain-Serializer-not-ModelSerializer shape as
    CreatePayoutInputSerializer above (the actual transition/locking logic
    lives in finance/services.py's mark_payout_paid, not here). Both
    fields optional -- an admin may already have set method/
    reference_number at creation time, or may only now know the real bank
    UTR once the transfer has actually been sent.
    """
    method = serializers.ChoiceField(choices=Payout.Method.choices, required=False, allow_blank=True, default='')
    reference_number = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)


class MyInvoiceSerializer(serializers.ModelSerializer):
    """
    Phase 3.5.5. GET /api/finance/my-invoices/[<id>/] -- the
    authenticated user's own invoices/receipts. The view's get_queryset
    already scopes this to request.user before this ever serializes
    anything. No customer identity restated (implied by "my"), no
    Razorpay id (Invoice never stores one), no internal instructor/
    commission detail (that's LedgerEntry's concern, never this one's).
    """
    source_type = serializers.SerializerMethodField()
    source_label = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            'id', 'invoice_number', 'source_type', 'source_label', 'amount', 'currency',
            'payment_date', 'status', 'created_at',
        ]
        read_only_fields = fields

    def get_source_type(self, obj):
        if obj.purchase_id:
            return 'PURCHASE'
        if obj.order_id:
            return 'ORDER'
        if obj.subscription_payment_id:
            return 'SUBSCRIPTION_PAYMENT'
        return None  # unreachable in practice -- the model's own exactly-one-source constraint guarantees one of the above

    def get_source_label(self, obj):
        """
        Invoice visibility frontend gap fix: a human-readable "what was
        this for" label -- course title / order number / subscription
        plan name -- pulled directly from the already-existing related
        row, never computed or duplicated. No new business logic: these
        are the exact same display strings courses/orders APIs already
        expose elsewhere (Course.title, Order.order_number,
        SubscriptionPlan.name). Returns None if the underlying source row
        no longer exists (SET_NULL on delete) -- same "unreachable in
        practice" caveat as get_source_type above.
        """
        if obj.purchase_id:
            return obj.purchase.course.title
        if obj.order_id:
            return obj.order.order_number
        if obj.subscription_payment_id:
            return obj.subscription_payment.subscription.plan.name
        return None


class AdminInvoiceSerializer(MyInvoiceSerializer):
    """Phase 3.5.5. GET /api/finance/admin/invoices/ -- admin-only
    inspection. Unlike MyInvoiceSerializer, exposes WHOSE invoice this is
    -- the entire point of admin inspection -- but still nothing beyond
    that (no Razorpay id, no payment-verification detail, no webhook
    payload)."""
    customer = serializers.SerializerMethodField()

    class Meta(MyInvoiceSerializer.Meta):
        fields = MyInvoiceSerializer.Meta.fields + ['customer']

    def get_customer(self, obj):
        return {'id': obj.customer_id, 'username': obj.customer.username}


class MyRefundSerializer(serializers.ModelSerializer):
    """
    Phase 3.5.6. GET /api/finance/my-refunds/[<id>/] -- the authenticated
    user's own refund history/status. Deliberately never exposes:
    razorpay_payment_id/razorpay_refund_id (internal payment-processor
    identifiers, no more the customer's business here than they are on
    MyInvoiceSerializer), failure_reason (Razorpay's own internal error
    detail), or requested_by (which admin actioned it) -- a customer
    needs to know THAT a refund happened/is happening and for how much,
    not the internal machinery behind it.
    """
    source_type = serializers.SerializerMethodField()

    class Meta:
        model = Refund
        fields = ['id', 'source_type', 'amount', 'currency', 'status', 'reason', 'requested_at', 'processed_at']
        read_only_fields = fields

    def get_source_type(self, obj):
        if obj.purchase_id:
            return 'PURCHASE'
        if obj.order_id:
            return 'ORDER'
        if obj.subscription_payment_id:
            return 'SUBSCRIPTION_PAYMENT'
        return None  # unreachable in practice -- the model's own exactly-one-source constraint guarantees one of the above


class AdminRefundSerializer(MyRefundSerializer):
    """
    Phase 3.5.6. GET /api/finance/admin/refunds/[<id>/] -- admin-only
    inspection and the response shape for POST .../refunds/create/.
    Unlike MyRefundSerializer, exposes WHOSE refund this is, WHICH admin
    requested it, and the Razorpay identifiers/failure detail needed for
    real traceability -- the entire point of admin inspection -- but
    still never the RAZORPAY_KEY_SECRET/RAZORPAY_WEBHOOK_SECRET
    themselves (never stored on this model at all) or a raw webhook
    payload (that remains visible only via WebhookEvent's own existing
    admin page, never duplicated here).
    """
    customer = serializers.SerializerMethodField()
    requested_by = serializers.SerializerMethodField()

    class Meta(MyRefundSerializer.Meta):
        fields = MyRefundSerializer.Meta.fields + [
            'customer', 'requested_by', 'razorpay_payment_id', 'razorpay_refund_id', 'failure_reason',
        ]

    def get_customer(self, obj):
        return {'id': obj.customer_id, 'username': obj.customer.username}

    def get_requested_by(self, obj):
        return {'id': obj.requested_by_id, 'username': obj.requested_by.username}


class RefundRequestInputSerializer(serializers.Serializer):
    """
    Phase 3.5.6. Input validation ONLY for POST /api/finance/admin/refunds/create/
    -- mirrors CreatePayoutInputSerializer's established precedent (a plain
    Serializer, not a ModelSerializer; the actual refund validation/
    Razorpay-call logic lives in finance/services.py's
    create_and_process_refund, not here). Exactly one of purchase_id/
    order_id/subscription_payment_id is required -- validated here so an
    ambiguous or empty request is rejected before ever touching the
    database or Razorpay.
    """
    purchase_id = serializers.IntegerField(required=False, allow_null=True)
    order_id = serializers.IntegerField(required=False, allow_null=True)
    subscription_payment_id = serializers.IntegerField(required=False, allow_null=True)
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal('0.01'))
    reason = serializers.CharField(required=False, allow_blank=True, default='', max_length=255)

    def validate(self, attrs):
        provided = [
            attrs.get('purchase_id'), attrs.get('order_id'), attrs.get('subscription_payment_id'),
        ]
        if len([s for s in provided if s is not None]) != 1:
            raise serializers.ValidationError(
                "Exactly one of purchase_id, order_id, or subscription_payment_id must be provided."
            )
        return attrs


class RefundEligibilityQuerySerializer(serializers.Serializer):
    """Phase 3.5.6. Query-param validation for GET
    /api/finance/admin/refund-eligibility/ -- same exactly-one-of-three
    shape as RefundRequestInputSerializer above, since this is a read-only
    preview of the exact same source resolution create_and_process_refund
    performs before actually creating anything."""
    purchase_id = serializers.IntegerField(required=False, allow_null=True)
    order_id = serializers.IntegerField(required=False, allow_null=True)
    subscription_payment_id = serializers.IntegerField(required=False, allow_null=True)

    def validate(self, attrs):
        provided = [
            attrs.get('purchase_id'), attrs.get('order_id'), attrs.get('subscription_payment_id'),
        ]
        if len([s for s in provided if s is not None]) != 1:
            raise serializers.ValidationError(
                "Exactly one of purchase_id, order_id, or subscription_payment_id must be provided."
            )
        return attrs
