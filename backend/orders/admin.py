from django.contrib import admin
from .models import Purchase, WebhookEvent, Order, OrderItem, SubscriptionPlan, Subscription, SubscriptionPayment

@admin.register(Purchase)
class PurchaseAdmin(admin.ModelAdmin):
    list_display = ('user', 'course', 'amount', 'status', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__username', 'course__title', 'razorpay_order_id')


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'razorpay_event_id', 'status', 'purchase', 'received_at', 'processed_at')
    list_filter = ('status', 'event_type')
    search_fields = ('razorpay_event_id', 'purchase__razorpay_order_id', 'purchase__user__username')
    readonly_fields = ('razorpay_event_id', 'event_type', 'payload', 'received_at')


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('item_type', 'course', 'bundle', 'title_snapshot', 'unit_price', 'quantity', 'total_price')
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """Phase 3.3. View Bundle/course orders here -- deliberately just a
    ModelAdmin list+detail, not a finance dashboard."""
    list_display = ('order_number', 'user', 'status', 'total_amount', 'currency', 'created_at')
    list_filter = ('status', 'currency', 'created_at')
    search_fields = ('order_number', 'user__username', 'user__email', 'razorpay_order_id')
    readonly_fields = ('order_number', 'razorpay_order_id', 'razorpay_payment_id', 'razorpay_signature', 'created_at', 'updated_at')
    inlines = [OrderItemInline]


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    """
    Phase 3.4.1, hardened in Phase 3.4.7. No 'sync to Razorpay' action --
    razorpay_plan_id is intentionally still NOT read-only: no automated
    sync-to-Razorpay action exists, so this remains the only way to link
    an already-existing Razorpay Plan (created via their dashboard) to a
    local plan. This is the sole legitimate place razorpay_plan_id is ever
    visible/editable -- confirmed (Phase 3.4.6) that no public API exposes
    it; only staff with Django admin access ever see it.
    """
    list_display = (
        'name', 'billing_interval', 'price', 'currency', 'is_active',
        'razorpay_linked', 'razorpay_plan_id', 'course_count', 'created_at', 'updated_at',
    )
    list_filter = ('billing_interval', 'is_active', 'currency')
    list_editable = ('is_active',)
    search_fields = ('name', 'slug', 'description', 'razorpay_plan_id')
    filter_horizontal = ('courses',)
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at', 'updated_at')

    def course_count(self, obj):
        return obj.courses.count()
    course_count.short_description = 'Courses'

    def razorpay_linked(self, obj):
        # At-a-glance ✓/✗ status alongside the raw id (still shown
        # separately -- "operationally useful" per the brief, e.g. to
        # cross-reference against the Razorpay dashboard).
        return bool(obj.razorpay_plan_id)
    razorpay_linked.boolean = True
    razorpay_linked.short_description = 'Razorpay Linked'


class SubscriptionPaymentInline(admin.TabularInline):
    model = SubscriptionPayment
    extra = 0
    fields = ('razorpay_payment_id', 'razorpay_subscription_id', 'amount', 'currency', 'status', 'paid_at', 'created_at')
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        # Phase 3.4.7: a SubscriptionPayment only ever legitimately exists
        # because a real Razorpay charge happened (created by the
        # subscription.charged webhook handler, or by
        # VerifySubscriptionPaymentView for the first checkout payment) --
        # fabricating one here would be a fake financial record with no
        # real charge behind it, exactly the "inconsistent local state"
        # this phase is asked to guard against.
        return False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    """
    Phase 3.4.1, hardened in Phase 3.4.7. Deliberately just a ModelAdmin,
    not a finance dashboard or a place to drive subscription state --
    every field is Razorpay/webhook-authoritative (see the model's own
    docstring), so every field is read-only here and creation via the raw
    admin form is disabled entirely: the only legitimate ways a
    Subscription is ever created or changed are CreateSubscriptionView,
    VerifySubscriptionPaymentView, CancelSubscriptionView, and
    RazorpayWebhookView (all unchanged by this phase) -- never a manual
    admin edit, which could silently desync the local row from what
    Razorpay/courses/services/access.py's read-time access logic actually
    believes (e.g. an admin manually setting status=ACTIVE on a
    genuinely-cancelled-on-Razorpay's-side subscription would incorrectly
    grant course access).
    """
    list_display = (
        'user', 'plan', 'status', 'current_period_start', 'current_period_end',
        'access_until', 'cancel_at_period_end', 'cancelled_at', 'created_at',
    )
    list_filter = ('status', 'cancel_at_period_end', 'plan')
    list_select_related = ('user', 'plan')
    search_fields = ('user__username', 'user__email', 'razorpay_subscription_id', 'razorpay_plan_id')
    readonly_fields = (
        'user', 'plan', 'razorpay_subscription_id', 'razorpay_plan_id', 'status',
        'current_period_start', 'current_period_end', 'access_until', 'cancelled_at',
        'cancel_at_period_end', 'created_at', 'updated_at',
    )
    inlines = [SubscriptionPaymentInline]

    def has_add_permission(self, request):
        return False


@admin.register(SubscriptionPayment)
class SubscriptionPaymentAdmin(admin.ModelAdmin):
    """
    Phase 3.4.7: same "Razorpay/webhook-authoritative, inspect-only"
    treatment as SubscriptionAdmin -- every field read-only, creation
    disabled (a manually-added row would be a fake financial record with
    no real charge behind it), deletion disabled (unlike Subscription,
    which can legitimately have zero payments if abandoned before ever
    charging, every SubscriptionPayment row IS by definition a real
    financial record the instant it exists -- the model's own PROTECT on
    the subscription FK already stops deleting the PARENT Subscription
    out from under its payments; this stops deleting a payment row
    directly).
    """
    list_display = (
        'subscription', 'subscription_user', 'subscription_plan', 'razorpay_payment_id',
        'amount', 'currency', 'status', 'paid_at', 'created_at',
    )
    list_filter = ('status', 'currency', 'paid_at')
    list_select_related = ('subscription', 'subscription__user', 'subscription__plan')
    search_fields = (
        'razorpay_payment_id', 'razorpay_subscription_id',
        'subscription__user__username', 'subscription__user__email', 'subscription__plan__name',
    )
    readonly_fields = (
        'subscription', 'razorpay_payment_id', 'razorpay_subscription_id',
        'amount', 'currency', 'status', 'paid_at', 'created_at', 'updated_at',
    )

    def subscription_user(self, obj):
        return obj.subscription.user
    subscription_user.short_description = 'User'
    subscription_user.admin_order_field = 'subscription__user'

    def subscription_plan(self, obj):
        return obj.subscription.plan
    subscription_plan.short_description = 'Plan'
    subscription_plan.admin_order_field = 'subscription__plan'

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
