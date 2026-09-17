from django.contrib import admin

from .models import LedgerEntry, Payout, Refund


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    """
    Phase 3.5.2. LedgerEntry is a financial transaction record -- the
    fulfillment hooks (fulfill_purchase/fulfill_order/
    _record_subscription_charge, via finance/services.py) are its sole
    source of truth, exactly like SubscriptionAdmin/SubscriptionPaymentAdmin's
    existing precedent for Razorpay/webhook-authoritative records (Phase
    3.4.7). Every field read-only, creation and deletion both disabled --
    a manually created or edited LedgerEntry would be a fabricated or
    falsified financial record with no real transaction behind it, the
    same reasoning SubscriptionPaymentAdmin already applies.
    """
    list_display = (
        'id', 'entry_type', 'course_instructor', 'gross_amount', 'commission_rate',
        'commission_amount', 'net_amount', 'currency', 'payout', 'created_at',
    )
    list_filter = ('entry_type', 'currency', 'created_at')
    list_select_related = ('course_instructor', 'course_instructor__user', 'course_instructor__course', 'payout')
    search_fields = (
        'course_instructor__user__username', 'course_instructor__user__email',
        'course_instructor__course__title',
    )
    readonly_fields = (
        'course_instructor', 'purchase', 'order_item', 'subscription_payment',
        'entry_type', 'related_entry', 'refund', 'gross_amount', 'currency', 'commission_rate',
        'commission_amount', 'net_amount', 'payout', 'created_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    """
    Phase 3.5.2. Schema only -- no code creates a Payout row yet (batching
    is Phase 3.5.4+). Read-only for the same reason as LedgerEntry: once
    batching exists, a Payout must only ever be created by that trusted
    process, never fabricated by hand through admin. Until then this list
    will simply always be empty.
    """
    list_display = ('id', 'recipient', 'status', 'period_start', 'period_end', 'gross_amount', 'commission_amount', 'net_amount', 'method', 'created_at')
    list_filter = ('status', 'method', 'period_start')
    list_select_related = ('recipient', 'approved_by')
    search_fields = ('recipient__username', 'recipient__email', 'reference_number')
    readonly_fields = (
        'recipient', 'period_start', 'period_end', 'gross_amount', 'commission_amount',
        'net_amount', 'status', 'method', 'reference_number', 'approved_by', 'approved_at',
        'paid_at', 'notes', 'created_at', 'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    """
    Phase 3.5.6. Refund is a financial transaction record like LedgerEntry/
    Payout/Invoice above -- finance/services.py's create_and_process_refund
    (called only from the admin-only refund API, never from this admin
    site) is its sole source of truth. Read-only and add-disabled for the
    exact same reason as its siblings: a Refund created by hand through
    Django admin would bypass every safeguard create_and_process_refund
    enforces (amount validation against the real remaining balance, the
    actual Razorpay API call, idempotent clawback creation) and would be a
    fabricated financial record with no real refund behind it. Delete is
    also disabled -- a Refund, successful or failed, is permanent
    transaction history, the same principle already applied to every other
    model in this app.
    """
    list_display = ('id', 'customer', 'status', 'amount', 'currency', 'reason', 'requested_by', 'requested_at', 'processed_at')
    list_filter = ('status', 'currency', 'requested_at')
    list_select_related = ('customer', 'requested_by')
    search_fields = ('customer__username', 'customer__email', 'razorpay_payment_id', 'razorpay_refund_id')
    readonly_fields = (
        'customer', 'purchase', 'order', 'subscription_payment', 'amount', 'currency',
        'razorpay_payment_id', 'razorpay_refund_id', 'status', 'reason', 'failure_reason',
        'requested_by', 'requested_at', 'processed_at', 'updated_at',
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
