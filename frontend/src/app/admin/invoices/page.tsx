"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Matches the ₹-prefixed money formatting already used everywhere else in
// this admin panel (Payments/Payouts) for the common INR case, while still
// respecting the serializer's own `currency` field for any other value.
const fmtMoney = (amount: string, currency: string) =>
  `${currency === 'INR' ? '₹' : `${currency} `}${parseFloat(amount).toLocaleString()}`;

interface InvoiceUserRef {
  id: number;
  username: string;
}

interface Invoice {
  id: number;
  invoice_number: string;
  source_type: 'PURCHASE' | 'ORDER' | 'SUBSCRIPTION_PAYMENT' | null;
  source_label: string | null;
  amount: string;
  currency: string;
  payment_date: string | null;
  status: 'ISSUED' | 'CANCELLED';
  created_at: string;
  customer: InvoiceUserRef;
}

export default function InvoicesLedger() {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Filter and pagination state.
  const [customerId, setCustomerId] = useState("");
  const [sourceTypeFilter, setSourceTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  const statusBadgeClass = (status: Invoice['status']) => {
    return status === 'ISSUED'
      ? 'bg-green-500/10 text-green-400 border border-green-500/20'
      : 'bg-red-500/10 text-red-400 border border-red-500/20';
  };

  const sourceTypeBadgeClass = 'bg-blue-500/10 text-blue-400 border border-blue-500/20';

  const fetchInvoices = async () => {
    setLoading(true);
    setError("");
    try {
      const queryParams = new URLSearchParams({ page: page.toString() });
      if (customerId) queryParams.set('customer_id', customerId);
      if (sourceTypeFilter) queryParams.set('source_type', sourceTypeFilter);
      if (statusFilter) queryParams.set('status', statusFilter);

      const res = await fetch(`${API_BASE}/api/finance/admin/invoices/?${queryParams.toString()}`, {
        credentials: "include"
      });

      if (res.ok) {
        const data = await res.json();
        setInvoices(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setError("Failed to fetch invoice records");
      }
    } catch (err) {
      setError("Network error fetching invoice records");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchInvoices();
  }, [page, sourceTypeFilter, statusFilter]);

  const handleCustomerIdSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchInvoices();
  };

  const totalPages = Math.ceil(totalCount / 10) || 1;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Invoices</h1>
        <p className="text-zinc-400 text-sm mt-1">Every invoice issued platform-wide, across purchases, orders, and subscription payments. Invoices are generated automatically elsewhere in the backend -- this page is read-only.</p>
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
            <option value="ISSUED">Issued</option>
            <option value="CANCELLED">Cancelled</option>
          </select>
        </div>
      </div>

      {/* Invoices Table -- intentionally a flat, dense list with no detail
          modal: every field worth showing already fits in the row, and
          there's no nested data (line items, gateway ids) to reveal, unlike
          Orders/Refunds. */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Invoice Number</th>
                <th className="p-4 font-semibold">Customer</th>
                <th className="p-4 font-semibold">Source</th>
                <th className="p-4 font-semibold">Source Label</th>
                <th className="p-4 font-semibold">Amount</th>
                <th className="p-4 font-semibold text-center">Status</th>
                <th className="p-4 font-semibold">Payment Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading invoices...</span>
                    </div>
                  </td>
                </tr>
              ) : invoices.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500 text-sm">
                    No matching invoice records found.
                  </td>
                </tr>
              ) : (
                invoices.map(invoice => (
                  <tr key={invoice.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4 font-bold text-white text-sm">{invoice.invoice_number}</td>

                    <td className="p-4 text-zinc-300">{invoice.customer.username}</td>

                    <td className="p-4">
                      <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${sourceTypeBadgeClass}`}>
                        {invoice.source_type || 'UNKNOWN'}
                      </span>
                    </td>

                    <td className="p-4 max-w-xs truncate">
                      {invoice.source_label || <span className="text-zinc-600 italic">None</span>}
                    </td>

                    <td className="p-4 font-bold text-[#facc15] text-sm">
                      {fmtMoney(invoice.amount, invoice.currency)}
                    </td>

                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${statusBadgeClass(invoice.status)}`}>
                        {invoice.status}
                      </span>
                    </td>

                    <td className="p-4 text-zinc-400">
                      {invoice.payment_date ? new Date(invoice.payment_date).toLocaleString() : <span className="text-zinc-600 italic">None</span>}
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
    </div>
  );
}
