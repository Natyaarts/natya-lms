from django.urls import path

from .views import (
    AdminInvoiceListView, AdminLedgerEntryListView, AdminRefundDetailView, AdminRefundListView,
    ApprovePayoutView, CreatePayoutView, CreateRefundView, EligibleLedgerEntriesView, FinanceHealthSummaryView,
    FinanceReconciliationView, MarkPayoutPaidView, MyEarningsSummaryView, MyEarningsView, MyInvoiceDetailView,
    MyInvoicesView, MyPayoutListView, MyRefundDetailView, MyRefundsView, PayoutDetailView, PayoutListView,
    RefundEligibilityView,
)

urlpatterns = [
    path('my-earnings/', MyEarningsView.as_view(), name='finance-my-earnings'),
    path('my-earnings-summary/', MyEarningsSummaryView.as_view(), name='finance-my-earnings-summary'),
    path('my-payouts/', MyPayoutListView.as_view(), name='finance-my-payouts'),
    path('admin/ledger-entries/', AdminLedgerEntryListView.as_view(), name='finance-admin-ledger-entries'),
    # Phase 3.5.4: payout batching/approval -- admin-only, no money ever moves.
    path('admin/eligible-ledger-entries/', EligibleLedgerEntriesView.as_view(), name='finance-eligible-ledger-entries'),
    path('admin/payouts/', PayoutListView.as_view(), name='finance-payout-list'),
    path('admin/payouts/create/', CreatePayoutView.as_view(), name='finance-payout-create'),
    path('admin/payouts/<int:pk>/', PayoutDetailView.as_view(), name='finance-payout-detail'),
    path('admin/payouts/<int:pk>/approve/', ApprovePayoutView.as_view(), name='finance-payout-approve'),
    path('admin/payouts/<int:pk>/mark-paid/', MarkPayoutPaidView.as_view(), name='finance-payout-mark-paid'),
    # Phase 3.5.5: customer invoices/receipts.
    path('my-invoices/', MyInvoicesView.as_view(), name='finance-my-invoices'),
    path('my-invoices/<int:pk>/', MyInvoiceDetailView.as_view(), name='finance-my-invoice-detail'),
    path('admin/invoices/', AdminInvoiceListView.as_view(), name='finance-admin-invoices'),
    # Phase 3.5.6: refunds + clawbacks -- admin-only creation/inspection,
    # customer-facing own-history only.
    path('admin/refund-eligibility/', RefundEligibilityView.as_view(), name='finance-refund-eligibility'),
    path('admin/refunds/', AdminRefundListView.as_view(), name='finance-admin-refunds'),
    path('admin/refunds/create/', CreateRefundView.as_view(), name='finance-refund-create'),
    path('admin/refunds/<int:pk>/', AdminRefundDetailView.as_view(), name='finance-admin-refund-detail'),
    path('my-refunds/', MyRefundsView.as_view(), name='finance-my-refunds'),
    path('my-refunds/<int:pk>/', MyRefundDetailView.as_view(), name='finance-my-refund-detail'),
    # Phase 3.5.7: finance reconciliation -- admin-only, read-only.
    path('admin/reconciliation/', FinanceReconciliationView.as_view(), name='finance-admin-reconciliation'),
    path('admin/finance-health/', FinanceHealthSummaryView.as_view(), name='finance-admin-health'),
]
