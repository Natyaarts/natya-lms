"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Eye, ChevronLeft, ChevronRight, X, Undo2 } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Matches the ₹-prefixed money formatting already used everywhere else in
// this admin panel (Payments/Payouts) for the common INR case, while still
// respecting the serializer's own `currency` field for any other value.
const fmtMoney = (amount: string, currency: string) =>
  `${currency === 'INR' ? '₹' : `${currency} `}${parseFloat(amount).toLocaleString()}`;

interface RefundUserRef {
  id: number;
  username: string;
}

interface Refund {
  id: number;
  source_type: 'PURCHASE' | 'ORDER' | 'SUBSCRIPTION_PAYMENT' | null;
  amount: string;
  currency: string;
  status: 'REQUESTED' | 'PROCESSING' | 'SUCCESS' | 'FAILED' | 'REJECTED';
  reason: string;
  requested_at: string;
  processed_at: string | null;
  customer: RefundUserRef;
  requested_by: RefundUserRef;
  razorpay_payment_id: string | null;
  razorpay_refund_id: string | null;
  failure_reason: string | null;
}

export default function RefundsLedger() {
  const [refunds, setRefunds] = useState<Refund[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Filter and pagination state.
  const [statusFilter, setStatusFilter] = useState("");
  const [sourceTypeFilter, setSourceTypeFilter] = useState("");
  const [customerId, setCustomerId] = useState("");
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  const [selectedRefund, setSelectedRefund] = useState<Refund | null>(null);

  const statusBadgeClass = (status: Refund['status']) => {
    switch (status) {
      case 'SUCCESS': return 'bg-green-500/10 text-green-400 border border-green-500/20';
      case 'REQUESTED':
      case 'PROCESSING': return 'bg-yellow-500/10 text-[#facc15] border border-yellow-500/20';
      default: return 'bg-red-500/10 text-red-400 border border-red-500/20'; // FAILED / REJECTED
    }
  };

  const sourceTypeBadgeClass = 'bg-blue-500/10 text-blue-400 border border-blue-500/20';

  const fetchRefunds = async () => {
    setLoading(true);
    setError("");
    try {
      const queryParams = new URLSearchParams({ page: page.toString() });
      if (statusFilter) queryParams.set('status', statusFilter);
      if (sourceTypeFilter) queryParams.set('source_type', sourceTypeFilter);
      if (customerId) queryParams.set('customer_id', customerId);

      const res = await fetch(`${API_BASE}/api/finance/admin/refunds/?${queryParams.toString()}`, {
        credentials: "include"
      });

      if (res.ok) {
        const data = await res.json();
        setRefunds(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setError("Failed to fetch refund records");
      }
    } catch (err) {
      setError("Network error fetching refund records");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRefunds();
  }, [page, statusFilter, sourceTypeFilter]);

  const handleCustomerIdSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchRefunds();
  };

  const totalPages = Math.ceil(totalCount / 10) || 1;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Refunds</h1>
        <p className="text-zinc-400 text-sm mt-1">
          A read-only ledger of every refund ever created, across purchases, orders, and subscription payments.{" "}
          <Link href="/admin/payments" className="text-[#facc15] hover:underline">To issue a new refund, go to Payments.</Link>
        </p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Filters & Search Row */}
      <div className="flex flex-col md:flex-row gap-4 mb-6">
        <form onSubmit={handleCustomerIdSubmit} className="flex-1 flex gap-2">
          <input
            type="number"
            placeholder="Filter by Customer User ID..."
            value={customerId}
            onChange={(e) => setCustomerId(e.target.value)}
            className="w-full bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors placeholder:text-zinc-500"
          />
          <button
            type="submit"
            className="px-5 py-3 bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-sm font-semibold rounded-xl transition-all"
          >
            Search
          </button>
        </form>

        <div className="w-full md:w-48">
          <select
            value={statusFilter}
            onChange={(e) => {
              setStatusFilter(e.target.value);
              setPage(1);
            }}
            className="w-full bg-zinc-900 border border-white/5 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
          >
            <option value="">All Statuses</option>
            <option value="REQUESTED">Requested</option>
            <option value="PROCESSING">Processing</option>
            <option value="SUCCESS">Success</option>
            <option value="FAILED">Failed</option>
            <option value="REJECTED">Rejected</option>
          </select>
        </div>

        <div className="w-full md:w-48">
          <select
            value={sourceTypeFilter}
            onChange={(e) => {
              setSourceTypeFilter(e.target.value);
              setPage(1);
            }}
            className="w-full bg-zinc-900 border border-white/5 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
          >
            <option value="">All Sources</option>
            <option value="PURCHASE">Purchase</option>
            <option value="ORDER">Order</option>
            <option value="SUBSCRIPTION_PAYMENT">Subscription Payment</option>
          </select>
        </div>
      </div>

      {/* Refunds Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Customer</th>
                <th className="p-4 font-semibold">Source</th>
                <th className="p-4 font-semibold">Amount</th>
                <th className="p-4 font-semibold text-center">Status</th>
                <th className="p-4 font-semibold">Requested At</th>
                <th className="p-4 font-semibold">Requested By</th>
                <th className="p-4 font-semibold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading refunds...</span>
                    </div>
                  </td>
                </tr>
              ) : refunds.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500 text-sm">
                    No matching refund records found.
                  </td>
                </tr>
              ) : (
                refunds.map(refund => (
                  <tr key={refund.id} className="hover:bg-white/5 transition-colors cursor-pointer" onClick={() => setSelectedRefund(refund)}>
                    <td className="p-4 font-bold text-white text-sm">{refund.customer.username}</td>

                    <td className="p-4">
                      <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${sourceTypeBadgeClass}`}>
                        {refund.source_type || 'UNKNOWN'}
                      </span>
                    </td>

                    <td className="p-4 font-bold text-[#facc15] text-sm">
                      {fmtMoney(refund.amount, refund.currency)}
                    </td>

                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${statusBadgeClass(refund.status)}`}>
                        {refund.status}
                      </span>
                    </td>

                    <td className="p-4 text-zinc-400">
                      {new Date(refund.requested_at).toLocaleString()}
                    </td>

                    <td className="p-4 text-zinc-300">{refund.requested_by.username}</td>

                    <td className="p-4">
                      <div className="flex items-center justify-center gap-2">
                        <button
                          onClick={(e) => { e.stopPropagation(); setSelectedRefund(refund); }}
                          className="p-2 bg-white/5 hover:bg-white/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-white"
                          title="View Refund Details"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination Footer */}
      {!loading && totalPages > 1 && (
        <div className="flex items-center justify-between mt-6">
          <div className="text-xs text-zinc-500">
            Showing Page <span className="font-semibold text-white">{page}</span> of <span className="font-semibold text-white">{totalPages}</span> ({totalCount} total entries)
          </div>
          <div className="flex gap-2">
            <button
              disabled={page === 1}
              onClick={() => setPage(page - 1)}
              className="p-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all inline-flex items-center"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              disabled={page === totalPages}
              onClick={() => setPage(page + 1)}
              className="p-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all inline-flex items-center"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {/* Detail Modal -- purely a read/audit view, no mutating actions. */}
      {selectedRefund && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-md shadow-2xl relative text-sm">
            <button
              onClick={() => setSelectedRefund(null)}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1 flex items-center gap-2">
              <Undo2 className="w-5 h-5 text-[#facc15]" />
              Refund #{selectedRefund.id}
            </h2>
            <p className="text-zinc-500 text-xs mb-6">Refund audit details and gateway tracking IDs.</p>

            <div className="space-y-4">
              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Customer</div>
                <div className="font-bold text-white">{selectedRefund.customer.username}</div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Amount</div>
                  <div className="font-bold text-[#facc15] text-base">
                    {fmtMoney(selectedRefund.amount, selectedRefund.currency)}
                  </div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Status</div>
                  <div>
                    <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full inline-block mt-0.5 ${statusBadgeClass(selectedRefund.status)}`}>
                      {selectedRefund.status}
                    </span>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4 text-xs">
                <div>
                  <div className="text-zinc-500 mb-1">Source Type</div>
                  <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full inline-block ${sourceTypeBadgeClass}`}>
                    {selectedRefund.source_type || 'UNKNOWN'}
                  </span>
                </div>
                <div>
                  <div className="text-zinc-500 mb-1">Requested By</div>
                  <div className="text-zinc-300 font-semibold">{selectedRefund.requested_by.username}</div>
                </div>
              </div>

              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Reason</div>
                <div className="text-zinc-300 text-xs bg-black/40 p-2.5 rounded-lg border border-white/5">
                  {selectedRefund.reason || <span className="text-zinc-600 italic">No reason provided.</span>}
                </div>
              </div>

              {selectedRefund.failure_reason && (
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Failure Reason</div>
                  <div className="text-red-400 text-xs bg-red-500/10 border border-red-500/20 p-2.5 rounded-lg">
                    {selectedRefund.failure_reason}
                  </div>
                </div>
              )}

              <div className="pt-4 border-t border-white/5 space-y-3 font-mono text-xs">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1 font-sans">Razorpay Payment ID</div>
                  <div className="bg-black/40 p-2.5 rounded-lg border border-white/5 text-zinc-300 select-all">
                    {selectedRefund.razorpay_payment_id || <span className="text-zinc-600 italic">None</span>}
                  </div>
                </div>

                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1 font-sans">Razorpay Refund ID</div>
                  <div className="bg-black/40 p-2.5 rounded-lg border border-white/5 text-zinc-300 select-all">
                    {selectedRefund.razorpay_refund_id || <span className="text-zinc-600 italic">None</span>}
                  </div>
                </div>
              </div>

              <div className="pt-4 border-t border-white/5 flex justify-between text-xs">
                <span className="text-zinc-500">Processed At</span>
                <span className="text-zinc-300 font-semibold">
                  {selectedRefund.processed_at ? new Date(selectedRefund.processed_at).toLocaleString() : <span className="text-zinc-600 italic">Not yet</span>}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
