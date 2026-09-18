from django.contrib.auth import get_user_model
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.utils.dateparse import parse_date
from rest_framework import generics, permissions, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from users.permissions import IsSuperAdminOrAdmin, IsTeacherOrMentor

from .models import Invoice, LedgerEntry, Payout, Refund
from .reconciliation import IssueType, Severity, get_instructor_balance, run_reconciliation
from .serializers import (
    AdminInvoiceSerializer, AdminLedgerEntrySerializer, AdminRefundSerializer, CreatePayoutInputSerializer,
    MarkPayoutPaidInputSerializer, MyEarningLedgerEntrySerializer, MyInvoiceSerializer, MyRefundSerializer,
    PayoutDetailSerializer, PayoutListSerializer, RefundEligibilityQuerySerializer, RefundRequestInputSerializer,
)
from .services import (
    RefundGatewayError, approve_payout, create_and_process_refund, create_payout_batch,
    get_eligible_ledger_entries_queryset, get_refund_eligibility, mark_payout_paid,
)
from .throttles import RefundCreationRateThrottle

User = get_user_model()


class StandardResultsSetPagination(PageNumberPagination):
    # Mirrors orders/views.py's StandardResultsSetPagination exactly --
    # same page size/param conventions across every paginated list in
    # this project.
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 100


class MyEarningsView(generics.ListAPIView):
    """
    GET /api/finance/my-earnings/ -- Phase 3.5.3. The authenticated
    teacher/mentor's own LedgerEntry rows, scoped via their
    CourseInstructor rows. Never trusts any instructor/user id supplied
    by the client for ownership -- the queryset is derived entirely from
    request.user server-side; there is no "instructor_id" query param
    here at all (unlike the admin view below, where filtering by
    instructor is a legitimate admin capability, not an ownership check).

    IsTeacherOrMentor denies a plain student outright (403), rather than
    students merely seeing an empty list by construction -- a real
    permission boundary, matching the approved spec.
    """
    serializer_class = MyEarningLedgerEntrySerializer
    permission_classes = [permissions.IsAuthenticated, IsTeacherOrMentor]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return (
            LedgerEntry.objects
            .filter(course_instructor__user=self.request.user)
            .select_related('course_instructor__course', 'payout')
            .order_by('-created_at')
        )


class MyEarningsSummaryView(APIView):
    """
    GET /api/finance/my-earnings-summary/ -- final release audit gap fix
    (teacher/mentor earnings frontend). A thin wrapper around the
    EXISTING get_instructor_balance() aggregate (finance/reconciliation.py)
    -- already used internally to detect instructors with a negative
    balance for the admin reconciliation/finance-health endpoints, but
    never exposed to the instructor it's actually about. No new
    calculation of any kind happens here; this view only calls that same
    function with request.user and returns its dict as-is, so these
    totals (earned/paid/available/outstanding debt) are guaranteed to
    always agree with what admin reconciliation already reports for this
    instructor.

    Same permission/scoping reasoning as MyEarningsView directly above --
    IsTeacherOrMentor denies a plain student outright, and the balance is
    always computed for request.user, never a client-supplied id.
    """
    permission_classes = [permissions.IsAuthenticated, IsTeacherOrMentor]

    def get(self, request):
        balance = get_instructor_balance(request.user)
        return Response(balance.to_dict())


class MyPayoutListView(generics.ListAPIView):
    """
    GET /api/finance/my-payouts/ -- final release audit gap fix. The
    authenticated teacher/mentor's own payout batch history. Reuses
    PayoutListSerializer completely unchanged (the exact same shape the
    admin payout list already returns: id, period, gross/commission/net
    amounts, status, method, reference_number, approved_by, approved_at,
    paid_at, entry_count) -- only the queryset is scoped to
    recipient=request.user instead of every recipient. Mirrors
    MyRefundsView/MyInvoicesView's established "same serializer as the
    admin view, scoped to me" pattern exactly.

    Read-only -- there is deliberately no create/approve/mark-paid
    capability reachable from this view or its permission class. Those
    remain exclusively admin operations (CreatePayoutView/
    ApprovePayoutView/MarkPayoutPaidView, all IsSuperAdminOrAdmin,
    unchanged).
    """
    serializer_class = PayoutListSerializer
    permission_classes = [permissions.IsAuthenticated, IsTeacherOrMentor]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return (
            Payout.objects
            .filter(recipient=self.request.user)
            .select_related('recipient', 'approved_by')
            .annotate(entry_count=Count('entries'))
            .order_by('-created_at')
        )


class AdminLedgerEntryListView(generics.ListAPIView):
    """
    GET /api/finance/admin/ledger-entries/ -- Phase 3.5.3. Admin-only
    ledger inspection, with safe filtering by instructor/course/
    entry_type/source_type/payout/currency, all via explicit query-param
    whitelisting (no raw filter-string passthrough). Read-only by nature
    (ListAPIView) -- there is no create/update/delete endpoint for
    LedgerEntry anywhere, matching admin.py's own read-only enforcement.
    """
    serializer_class = AdminLedgerEntrySerializer
    permission_classes = [IsSuperAdminOrAdmin]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = (
            LedgerEntry.objects
            .select_related('course_instructor__user', 'course_instructor__course', 'payout')
            .order_by('-created_at')
        )
        params = self.request.query_params

        instructor_id = params.get('instructor_id')
        if instructor_id:
            qs = qs.filter(course_instructor__user_id=instructor_id)

        course_id = params.get('course_id')
        if course_id:
            qs = qs.filter(course_instructor__course_id=course_id)

        entry_type = params.get('entry_type')
        if entry_type in LedgerEntry.EntryType.values:
            qs = qs.filter(entry_type=entry_type)

        source_type = params.get('source_type')
        if source_type == 'PURCHASE':
            qs = qs.filter(purchase__isnull=False)
        elif source_type == 'ORDER_ITEM':
            qs = qs.filter(order_item__isnull=False)
        elif source_type == 'SUBSCRIPTION_PAYMENT':
            qs = qs.filter(subscription_payment__isnull=False)

        payout_param = params.get('payout')
        if payout_param == 'null':
            qs = qs.filter(payout__isnull=True)
        elif payout_param:
            qs = qs.filter(payout_id=payout_param)

        currency = params.get('currency')
        if currency:
            qs = qs.filter(currency=currency)

        return qs


class EligibleLedgerEntriesView(generics.ListAPIView):
    """
    GET /api/finance/admin/eligible-ledger-entries/ -- Phase 3.5.4.
    Admin-only: LedgerEntry rows currently eligible to be batched into a
    new Payout (see finance/services.py's get_eligible_ledger_entries_queryset
    for the exact eligibility rules). Reuses AdminLedgerEntrySerializer
    unchanged -- same shape as general ledger inspection, just pre-
    filtered to what's actually batchable right now. Optional
    instructor_id filter, same admin-capability-not-ownership-check
    reasoning as AdminLedgerEntryListView's.
    """
    serializer_class = AdminLedgerEntrySerializer
    permission_classes = [IsSuperAdminOrAdmin]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = get_eligible_ledger_entries_queryset().order_by('-created_at')
        instructor_id = self.request.query_params.get('instructor_id')
        if instructor_id:
            qs = qs.filter(course_instructor__user_id=instructor_id)
        return qs


class PayoutListView(generics.ListAPIView):
    """GET /api/finance/admin/payouts/ -- Phase 3.5.4. Admin-only list of
    payout batches, newest first."""
    serializer_class = PayoutListSerializer
    permission_classes = [IsSuperAdminOrAdmin]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return (
            Payout.objects
            .select_related('recipient', 'approved_by')
            .annotate(entry_count=Count('entries'))
            .order_by('-created_at')
        )


class PayoutDetailView(generics.RetrieveAPIView):
    """GET /api/finance/admin/payouts/<id>/ -- Phase 3.5.4. Admin-only
    single payout, including its full batched LedgerEntry list."""
    serializer_class = PayoutDetailSerializer
    permission_classes = [IsSuperAdminOrAdmin]

    def get_queryset(self):
        return (
            Payout.objects
            .select_related('recipient', 'approved_by')
            .annotate(entry_count=Count('entries'))
            .prefetch_related('entries__course_instructor__user', 'entries__course_instructor__course', 'entries__payout')
        )


class CreatePayoutView(APIView):
    """
    POST /api/finance/admin/payouts/create/ -- Phase 3.5.4. Admin-only.
    Batches the explicitly-selected LedgerEntry ids into a new DRAFT
    Payout for the given recipient. All the actual validation/locking/
    calculation happens in finance/services.py's create_payout_batch --
    this view only validates the request SHAPE (via
    CreatePayoutInputSerializer) and resolves recipient_id to a real
    User, then surfaces any ValueError from the service as a 400.
    """
    permission_classes = [IsSuperAdminOrAdmin]

    def post(self, request):
        input_serializer = CreatePayoutInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        recipient = get_object_or_404(User, pk=data['recipient_id'])

        try:
            payout = create_payout_batch(
                recipient=recipient,
                ledger_entry_ids=data['ledger_entry_ids'],
                period_start=data['period_start'],
                period_end=data['period_end'],
                method=data['method'],
                reference_number=data['reference_number'],
                notes=data['notes'],
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        payout.entry_count = payout.entries.count()
        return Response(PayoutDetailSerializer(payout).data, status=status.HTTP_201_CREATED)


class ApprovePayoutView(APIView):
    """
    POST /api/finance/admin/payouts/<id>/approve/ -- Phase 3.5.4.
    Admin-only. DRAFT -> APPROVED only (see approve_payout's docstring
    for why this never goes further). No request body needed/read.
    """
    permission_classes = [IsSuperAdminOrAdmin]

    def post(self, request, pk):
        payout = get_object_or_404(Payout, pk=pk)
        try:
            payout = approve_payout(payout, approved_by=request.user)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Phase 3.9: audit trail only -- see CreateRefundView's identical
        # note above (additive, never touches payout business logic).
        from users.models import AdminAuditLog
        AdminAuditLog.record(
            actor=request.user, action='PAYOUT_APPROVED', target_type='Payout', target_id=payout.id,
            description=f"Payout #{payout.id} approved for recipient #{payout.recipient_id}, net_amount={payout.net_amount}.",
            metadata={'recipient_id': payout.recipient_id, 'net_amount': str(payout.net_amount)},
        )

        payout.entry_count = payout.entries.count()
        return Response(PayoutDetailSerializer(payout).data)


class MarkPayoutPaidView(APIView):
    """
    POST /api/finance/admin/payouts/<id>/mark-paid/ -- final release audit
    gap fix. Admin-only. APPROVED -> PAID only (see mark_payout_paid's own
    docstring for why this never touches a LedgerEntry). Request body is
    entirely optional ({method, reference_number}, both blank-default) --
    an admin recording an already-known reference number, or simply
    confirming a plain "paid" with no additional detail, are equally
    valid calls.
    """
    permission_classes = [IsSuperAdminOrAdmin]

    def post(self, request, pk):
        input_serializer = MarkPayoutPaidInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        payout = get_object_or_404(Payout, pk=pk)
        try:
            payout = mark_payout_paid(payout, method=data['method'], reference_number=data['reference_number'])
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # Phase 3.9: audit trail only -- see ApprovePayoutView/CreateRefundView's
        # identical note above (additive, never touches payout business logic).
        from users.models import AdminAuditLog
        AdminAuditLog.record(
            actor=request.user, action='PAYOUT_MARKED_PAID', target_type='Payout', target_id=payout.id,
            description=f"Payout #{payout.id} marked PAID for recipient #{payout.recipient_id}, net_amount={payout.net_amount}.",
            metadata={'recipient_id': payout.recipient_id, 'net_amount': str(payout.net_amount), 'reference_number': payout.reference_number},
        )

        payout.entry_count = payout.entries.count()
        return Response(PayoutDetailSerializer(payout).data)


class MyInvoicesView(generics.ListAPIView):
    """
    GET /api/finance/my-invoices/ -- Phase 3.5.5. ANY authenticated
    user's own invoices/receipts -- unlike MyEarningsView, this is NOT
    role-gated to teachers/mentors, since any authenticated user
    (student, teacher, mentor, admin) can be a paying customer. Never
    trusts a client-supplied customer/user id -- scoped entirely to
    request.user server-side.
    """
    serializer_class = MyInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        # select_related covers get_source_label's new relation walk
        # (purchase__course / order / subscription_payment__subscription__plan)
        # so it costs no extra query per row beyond the existing 'customer' join.
        return (
            Invoice.objects.filter(customer=self.request.user)
            .select_related('purchase__course', 'order', 'subscription_payment__subscription__plan')
            .order_by('-created_at')
        )


class MyInvoiceDetailView(generics.RetrieveAPIView):
    """
    GET /api/finance/my-invoices/<id>/ -- Phase 3.5.5. A single invoice/
    receipt, own only. The queryset is pre-scoped to request.user, so a
    request for another user's invoice id resolves to a 404 (not a 403)
    -- matching this codebase's established isolation convention
    (VerifySubscriptionPaymentView's own ownership check does the same,
    "never reveal that a row belonging to another user even exists").
    """
    serializer_class = MyInvoiceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return (
            Invoice.objects.filter(customer=self.request.user)
            .select_related('purchase__course', 'order', 'subscription_payment__subscription__plan')
        )


class AdminInvoiceListView(generics.ListAPIView):
    """
    GET /api/finance/admin/invoices/ -- Phase 3.5.5. Admin-only
    inspection, filterable by customer_id/source_type/status via
    explicit query-param whitelisting (same pattern as
    AdminLedgerEntryListView).
    """
    serializer_class = AdminInvoiceSerializer
    permission_classes = [IsSuperAdminOrAdmin]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = (
            Invoice.objects.select_related(
                'customer', 'purchase__course', 'order', 'subscription_payment__subscription__plan',
            )
            .order_by('-created_at')
        )
        params = self.request.query_params

        customer_id = params.get('customer_id')
        if customer_id:
            qs = qs.filter(customer_id=customer_id)

        source_type = params.get('source_type')
        if source_type == 'PURCHASE':
            qs = qs.filter(purchase__isnull=False)
        elif source_type == 'ORDER':
            qs = qs.filter(order__isnull=False)
        elif source_type == 'SUBSCRIPTION_PAYMENT':
            qs = qs.filter(subscription_payment__isnull=False)

        status_param = params.get('status')
        if status_param in Invoice.Status.values:
            qs = qs.filter(status=status_param)

        return qs


# ---------------------------------------------------------------------------
# Phase 3.5.6: Refunds. Admin/superadmin-only for creation/inspection
# (IsSuperAdminOrAdmin, exactly like every other admin-only finance
# endpoint above) -- a teacher has no refund control, a student has no
# refund control, matching the approved brief's permission table exactly.
# MyRefundsView/MyRefundDetailView are the one customer-facing exception,
# scoped to request.user, mirroring MyInvoicesView's own "any authenticated
# user can be a customer" reasoning.
# ---------------------------------------------------------------------------

class CreateRefundView(APIView):
    """
    POST /api/finance/admin/refunds/create/ -- Phase 3.5.6. Admin-only.
    All the actual validation/locking/Razorpay-call/clawback logic lives
    in finance/services.py's create_and_process_refund; this view only
    validates the request SHAPE (via RefundRequestInputSerializer),
    resolves the given id to a real Purchase/Order/SubscriptionPayment,
    and translates that service's two distinct failure modes into the
    right HTTP status: a ValueError (bad input / not currently refundable
    / amount exceeds the remainder / unsupported multi-item partial) is a
    400; a RefundGatewayError (Razorpay itself rejected or failed the
    request, AFTER a Refund row was already durably created and marked
    FAILED -- see that exception's own docstring) is a 502.
    """
    permission_classes = [IsSuperAdminOrAdmin]
    # General API rate limiting gap fix -- see finance/throttles.py.
    throttle_classes = [RefundCreationRateThrottle]

    def post(self, request):
        from orders.models import Order, Purchase, SubscriptionPayment

        input_serializer = RefundRequestInputSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        data = input_serializer.validated_data

        purchase = get_object_or_404(Purchase, pk=data['purchase_id']) if data.get('purchase_id') else None
        order = get_object_or_404(Order, pk=data['order_id']) if data.get('order_id') else None
        subscription_payment = (
            get_object_or_404(SubscriptionPayment, pk=data['subscription_payment_id'])
            if data.get('subscription_payment_id') else None
        )

        try:
            refund = create_and_process_refund(
                purchase=purchase, order=order, subscription_payment=subscription_payment,
                amount=data['amount'], reason=data['reason'], requested_by=request.user,
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except RefundGatewayError as e:
            return Response({"error": str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        # Phase 3.9: audit trail only -- purely additive, observational,
        # logged AFTER create_and_process_refund has already fully
        # succeeded. Never touches finance business logic/validation
        # (unchanged, see Phase 3.5.6/3.5.8) and never blocks the response
        # if logging itself fails (AdminAuditLog.record never raises).
        from users.models import AdminAuditLog
        AdminAuditLog.record(
            actor=request.user, action='REFUND_CREATED', target_type='Refund', target_id=refund.id,
            description=f"Refund #{refund.id} created for {refund.amount} {refund.currency} (customer #{refund.customer_id}).",
            metadata={'amount': str(refund.amount), 'currency': refund.currency, 'customer_id': refund.customer_id, 'status': refund.status},
        )

        return Response(AdminRefundSerializer(refund).data, status=status.HTTP_201_CREATED)


class RefundEligibilityView(APIView):
    """
    GET /api/finance/admin/refund-eligibility/?purchase_id=<id> (or
    order_id=/subscription_payment_id=) -- Phase 3.5.6. Admin-only. The
    "list refundable transactions" capability from the approved brief,
    scoped to one already-identified transaction (see
    get_refund_eligibility's own docstring for why this is scoped rather
    than a platform-wide scan). A source that is not currently refundable
    at all (wrong status, no razorpay_payment_id, unsupported currency)
    is reported as a 400 with a clear reason, not a misleading "0
    remaining" success response.
    """
    permission_classes = [IsSuperAdminOrAdmin]

    def get(self, request):
        from orders.models import Order, Purchase, SubscriptionPayment

        query_serializer = RefundEligibilityQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)
        data = query_serializer.validated_data

        purchase = get_object_or_404(Purchase, pk=data['purchase_id']) if data.get('purchase_id') else None
        order = get_object_or_404(Order, pk=data['order_id']) if data.get('order_id') else None
        subscription_payment = (
            get_object_or_404(SubscriptionPayment, pk=data['subscription_payment_id'])
            if data.get('subscription_payment_id') else None
        )

        try:
            eligibility = get_refund_eligibility(purchase=purchase, order=order, subscription_payment=subscription_payment)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(eligibility)


class AdminRefundListView(generics.ListAPIView):
    """GET /api/finance/admin/refunds/ -- Phase 3.5.6. Admin-only list,
    filterable by status/source_type/customer_id (same explicit query-
    param whitelisting pattern as AdminLedgerEntryListView/
    AdminInvoiceListView)."""
    serializer_class = AdminRefundSerializer
    permission_classes = [IsSuperAdminOrAdmin]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        qs = Refund.objects.select_related('customer', 'requested_by').order_by('-requested_at')
        params = self.request.query_params

        status_param = params.get('status')
        if status_param in Refund.Status.values:
            qs = qs.filter(status=status_param)

        source_type = params.get('source_type')
        if source_type == 'PURCHASE':
            qs = qs.filter(purchase__isnull=False)
        elif source_type == 'ORDER':
            qs = qs.filter(order__isnull=False)
        elif source_type == 'SUBSCRIPTION_PAYMENT':
            qs = qs.filter(subscription_payment__isnull=False)

        customer_id = params.get('customer_id')
        if customer_id:
            qs = qs.filter(customer_id=customer_id)

        return qs


class AdminRefundDetailView(generics.RetrieveAPIView):
    """GET /api/finance/admin/refunds/<id>/ -- Phase 3.5.6. Admin-only
    single refund."""
    serializer_class = AdminRefundSerializer
    permission_classes = [IsSuperAdminOrAdmin]
    queryset = Refund.objects.select_related('customer', 'requested_by')


class MyRefundsView(generics.ListAPIView):
    """
    GET /api/finance/my-refunds/ -- Phase 3.5.6. The authenticated user's
    own refund history/status. NOT role-gated (any authenticated user can
    be a customer, matching MyInvoicesView's identical reasoning). Scoped
    entirely via request.user server-side -- there is no user id query
    param here at all.
    """
    serializer_class = MyRefundSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = StandardResultsSetPagination

    def get_queryset(self):
        return Refund.objects.filter(customer=self.request.user).order_by('-requested_at')


class MyRefundDetailView(generics.RetrieveAPIView):
    """
    GET /api/finance/my-refunds/<id>/ -- Phase 3.5.6. A single refund,
    own only. The queryset is pre-scoped to request.user, so a request
    for another user's refund id resolves to a 404 (not a 403) -- the
    same established isolation convention MyInvoiceDetailView already
    follows.
    """
    serializer_class = MyRefundSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Refund.objects.filter(customer=self.request.user)


# ---------------------------------------------------------------------------
# Phase 3.5.7: Finance reconciliation. Both views are strictly read-only --
# neither ever creates, updates, or deletes a financial record; each runs
# finance/reconciliation.py's run_reconciliation() fresh on every request
# (nothing is cached/persisted, so a result can never go stale). Admin-only,
# same IsSuperAdminOrAdmin permission every other admin finance endpoint
# uses. Never returns a Razorpay webhook payload, secret, or credential --
# only the same class of fields (ids, amounts, statuses, human-readable
# messages) every other finance serializer in this module already exposes.
# ---------------------------------------------------------------------------

def _run_reconciliation_from_query_params(params):
    """Shared query-param parsing for both reconciliation endpoints below.
    Invalid/unrecognized values are silently ignored (not 400ed) --
    matching AdminLedgerEntryListView's/AdminInvoiceListView's existing
    query-param whitelisting convention elsewhere in this file."""
    severity = params.get('severity')
    if severity not in Severity.CHOICES:
        severity = None

    issue_type = params.get('issue_type')
    if issue_type and not hasattr(IssueType, issue_type):
        issue_type = None

    source_type = params.get('source_type') or None

    instructor_id = params.get('instructor_id')
    try:
        instructor_id = int(instructor_id) if instructor_id else None
    except (TypeError, ValueError):
        instructor_id = None

    date_from = parse_date(params.get('date_from')) if params.get('date_from') else None
    date_to = parse_date(params.get('date_to')) if params.get('date_to') else None

    return run_reconciliation(
        severity=severity, issue_type=issue_type, source_type=source_type,
        instructor_id=instructor_id, date_from=date_from, date_to=date_to,
    )


class FinanceReconciliationView(APIView):
    """
    GET /api/finance/admin/reconciliation/ -- Phase 3.5.7. Admin-only.
    Runs the full reconciliation scan (finance/reconciliation.py's
    run_reconciliation) and returns every detected issue, filterable via
    severity/issue_type/source_type/instructor_id/date_from/date_to query
    params (date_from/date_to narrow which Purchase/Order/
    SubscriptionPayment rows are examined in the first place -- see
    run_reconciliation's own docstring for why). Read-only: this view has
    no POST/PUT/DELETE at all, and the service it calls never writes
    anything.
    """
    permission_classes = [IsSuperAdminOrAdmin]

    def get(self, request):
        report = _run_reconciliation_from_query_params(request.query_params)
        return Response({
            'summary': report.to_summary_dict(),
            'issues': [issue.to_dict() for issue in report.issues],
        })


class FinanceHealthSummaryView(APIView):
    """
    GET /api/finance/admin/finance-health/ -- Phase 3.5.7. Admin-only.
    The same reconciliation scan as FinanceReconciliationView (unfiltered
    -- a platform-wide health snapshot), reduced to aggregate counts only
    (no per-issue detail, no ids/messages) for a fast at-a-glance
    dashboard summary. Read-only.
    """
    permission_classes = [IsSuperAdminOrAdmin]

    def get(self, request):
        report = run_reconciliation()
        by_type = {}
        for issue in report.issues:
            by_type[issue.issue_type] = by_type.get(issue.issue_type, 0) + 1

        summary = report.to_summary_dict()
        summary.update({
            'missing_ledger_count': sum(by_type.get(t, 0) for t in (
                IssueType.PURCHASE_MISSING_LEDGER_ENTRY,
                IssueType.ORDER_ITEM_MISSING_LEDGER_ENTRY,
                IssueType.SUBSCRIPTION_PAYMENT_MISSING_LEDGER_ENTRY,
            )),
            'missing_invoice_count': sum(by_type.get(t, 0) for t in (
                IssueType.PURCHASE_MISSING_INVOICE,
                IssueType.ORDER_MISSING_INVOICE,
                IssueType.SUBSCRIPTION_PAYMENT_MISSING_INVOICE,
            )),
            'refund_clawback_mismatches': by_type.get(IssueType.REFUND_MISSING_CLAWBACK, 0),
            'payout_mismatches': sum(by_type.get(t, 0) for t in (
                IssueType.PAYOUT_TOTAL_MISMATCH,
                IssueType.PAYOUT_INELIGIBLE_ENTRY,
                IssueType.PAYOUT_RECIPIENT_MISMATCH,
                IssueType.PAYOUT_CURRENCY_INCONSISTENT,
                IssueType.PAYOUT_NEGATIVE_VALUE,
            )),
            'instructor_negative_balances': by_type.get(IssueType.INSTRUCTOR_NEGATIVE_BALANCE, 0),
            'duplicate_records': sum(by_type.get(t, 0) for t in (
                IssueType.DUPLICATE_EARNING_ENTRY,
                IssueType.DUPLICATE_INVOICE,
            )),
        })
        return Response(summary)
