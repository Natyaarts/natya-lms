from rest_framework import serializers
from courses.models import Course, Bundle
from .models import Purchase, Order, OrderItem, SubscriptionPlan, Subscription, SubscriptionPayment

class AdminPurchaseSerializer(serializers.ModelSerializer):
    student_name = serializers.SerializerMethodField()
    student_email = serializers.EmailField(source='user.email', read_only=True)
    course_title = serializers.CharField(source='course.title', read_only=True)

    class Meta:
        model = Purchase
        fields = (
            'id',
            'student_name',
            'student_email',
            'course_title',
            'amount',
            'status',
            'razorpay_order_id',
            'razorpay_payment_id',
            'created_at'
        )
        read_only_fields = fields

    def get_student_name(self, obj):
        if obj.user.first_name or obj.user.last_name:
            return f"{obj.user.first_name} {obj.user.last_name}".strip()
        return obj.user.username


# ---------------------------------------------------------------------------
# Phase 3.3: Bundles + Orders. Order/OrderItem below are entirely
# read_only -- price/status/line items are never client-writable, only ever
# computed server-side (OrderViewSet.create()), matching
# AdminPurchaseSerializer's existing "read_only_fields = fields" convention
# above. BundleSerializer is the one exception: Bundle IS meant to be
# admin-writable through the API (POST/PATCH /api/orders/bundles/, gated by
# IsSuperAdminOrAdminOrReadOnly) alongside Django's own admin site -- see
# BundleViewSet in views.py.
# ---------------------------------------------------------------------------

class BundleCourseSerializer(serializers.ModelSerializer):
    """Minimal Course shape for nesting inside a Bundle response -- mirrors
    the fields CourseSerializer already exposes publicly, not a new shape."""
    class Meta:
        model = Course
        fields = ['id', 'title', 'thumbnail', 'price', 'course_type', 'is_published']
        read_only_fields = fields


class BundleSerializer(serializers.ModelSerializer):
    # Nested, read-only representation of the assigned courses.
    courses = BundleCourseSerializer(many=True, read_only=True)
    # Write side of the same relation -- a plain list of course PKs. DRF's
    # default ModelSerializer.create()/update() detects `source='courses'`
    # matches a model M2M field and calls Bundle.courses.set(...)
    # automatically; Django's M2M .set()/.add() are themselves idempotent
    # on duplicate PKs, so "prevent duplicate courses in a bundle" needs no
    # extra validation code here either.
    course_ids = serializers.PrimaryKeyRelatedField(
        source='courses', queryset=Course.objects.all(), many=True, write_only=True, required=False
    )
    is_purchasable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Bundle
        fields = [
            'id', 'name', 'slug', 'description', 'courses', 'course_ids', 'price', 'currency',
            'thumbnail', 'is_active', 'is_purchasable', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']


class OrderItemSerializer(serializers.ModelSerializer):
    course_title = serializers.CharField(source='course.title', read_only=True, allow_null=True)
    bundle_name = serializers.CharField(source='bundle.name', read_only=True, allow_null=True)

    class Meta:
        model = OrderItem
        fields = [
            'id', 'item_type', 'course', 'course_title', 'bundle', 'bundle_name',
            'title_snapshot', 'unit_price', 'quantity', 'total_price'
        ]
        read_only_fields = fields


class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    student_name = serializers.SerializerMethodField()
    student_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'order_number', 'student_name', 'student_email', 'status',
            'subtotal', 'discount_amount', 'total_amount', 'currency',
            'razorpay_order_id', 'items', 'created_at', 'updated_at'
        ]
        read_only_fields = fields

    def get_student_name(self, obj):
        if obj.user.first_name or obj.user.last_name:
            return f"{obj.user.first_name} {obj.user.last_name}".strip()
        return obj.user.username


class SubscriptionPlanSummarySerializer(serializers.ModelSerializer):
    """Minimal, nested-only shape for the plan a Subscription belongs to --
    no razorpay_plan_id (internal id, no reason for the client to have it),
    no `courses` (a plan's full course list isn't needed just to show "you
    are subscribed to X")."""
    class Meta:
        model = SubscriptionPlan
        fields = ['id', 'name', 'billing_interval', 'price', 'currency']
        read_only_fields = fields


class SubscriptionSerializer(serializers.ModelSerializer):
    """
    Phase 3.4.5. Read-only, safe subscription state for the authenticated
    owner only (SubscriptionMeView/CancelSubscriptionView both scope the
    lookup to request.user before this ever serializes anything -- this
    serializer itself has no access-control logic). Deliberately never
    exposes razorpay_subscription_id/razorpay_plan_id -- the client never
    needs Razorpay's own ids for anything (creation returns what checkout
    needs directly; cancellation is derived from request.user, no id ever
    accepted from the client) -- or any other internal/secret value.
    """
    plan = SubscriptionPlanSummarySerializer(read_only=True)
    effective_access_until = serializers.SerializerMethodField()

    class Meta:
        model = Subscription
        fields = [
            'id', 'status', 'plan', 'current_period_start', 'current_period_end',
            'access_until', 'cancel_at_period_end', 'cancelled_at', 'effective_access_until', 'created_at',
        ]
        read_only_fields = fields

    def get_effective_access_until(self, obj):
        # The SAME "access_until if set, else current_period_end" rule
        # courses/services/access.py's _valid_subscription_filter enforces
        # for actual access decisions -- computed here identically so what
        # the student is TOLD can never drift from what's actually
        # ENFORCED. Deliberately re-derived rather than imported from
        # courses.services.access, to avoid orders/serializers.py
        # depending on the courses app for a two-line computation; the
        # underlying field semantics are the single source of truth either
        # way (Subscription.access_until/current_period_end themselves),
        # not this display-only helper.
        return obj.access_until or obj.current_period_end


class SubscriptionPlanCourseSerializer(serializers.ModelSerializer):
    """Minimal Course shape for nesting inside a SubscriptionPlan's public
    response -- mirrors BundleCourseSerializer's exact precedent (the same
    fields CourseSerializer already exposes publicly), not a new shape."""
    class Meta:
        model = Course
        fields = ['id', 'title', 'thumbnail', 'price', 'course_type', 'is_published']
        read_only_fields = fields


class SubscriptionPlanSerializer(serializers.ModelSerializer):
    """
    Phase 3.4.6. Public catalog representation of a SubscriptionPlan --
    mirrors BundleSerializer's existing precedent (id/name/slug/description/
    price/currency/is_active + nested courses), used by the new
    SubscriptionPlanViewSet. Unlike BundleSerializer, this is entirely
    read-only: SubscriptionPlan management already has a working path
    (Django's own admin site, SubscriptionPlanAdmin, unchanged since Phase
    3.4.1) and adding API write access here would edge into "admin
    dashboard" territory this phase explicitly excludes -- so, unlike
    Bundle, no write support was added alongside the read path.

    Deliberately never exposes razorpay_plan_id -- there is no legitimate
    reason for a browsing/subscribing client to ever see Razorpay's own
    plan id (CreateSubscriptionView, unchanged, resolves it entirely
    server-side from the local `id` the client submits as plan_id).
    """
    courses = SubscriptionPlanCourseSerializer(many=True, read_only=True)

    class Meta:
        model = SubscriptionPlan
        fields = ['id', 'name', 'slug', 'description', 'billing_interval', 'price', 'currency', 'courses', 'is_active']
        read_only_fields = fields


class SubscriptionPaymentSerializer(serializers.ModelSerializer):
    """
    Phase 3.4.6. Safe payment-history shape for the authenticated owner
    only (SubscriptionPaymentHistoryView scopes the queryset to
    request.user before this ever serializes anything). Deliberately never
    exposes razorpay_payment_id/razorpay_subscription_id -- same reasoning
    as SubscriptionSerializer: no legitimate client need for Razorpay's own
    ids, and the raw webhook payload that produced this row
    (WebhookEvent.payload) is never referenced here at all.
    """
    plan_name = serializers.CharField(source='subscription.plan.name', read_only=True)

    class Meta:
        model = SubscriptionPayment
        fields = ['id', 'plan_name', 'amount', 'currency', 'status', 'paid_at', 'created_at']
        read_only_fields = fields
