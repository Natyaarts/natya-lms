"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Receipt, X, Printer } from "lucide-react";

// Invoice visibility frontend gap fix. Any authenticated user (student,
// teacher, mentor -- MyInvoicesView is not role-gated, see
// backend/finance/views.py) can be a paying customer and see their own
// invoices here. All amounts/statuses/labels come directly from the
// existing GET /api/finance/my-invoices/ API -- nothing here recalculates
// a total or invents a business rule.
const STATUS_STYLE: Record<string, string> = {
  ISSUED: "bg-green-500/10 text-green-400 border border-green-500/20",
  CANCELLED: "bg-zinc-800 text-zinc-500 border border-white/10",
};

const SOURCE_TYPE_LABEL: Record<string, string> = {
  PURCHASE: "Course Purchase",
  ORDER: "Order Checkout",
  SUBSCRIPTION_PAYMENT: "Subscription Payment",
};

export default function InvoicesPage() {
  const [invoices, setInvoices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [selected, setSelected] = useState<any>(null);

  const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/api/finance/my-invoices/?page=${page}`, { credentials: "include" })
      .then(async (res) => {
        if (res.ok) {
          const data = await res.json();
          setInvoices(data.results || []);
          setTotalCount(data.count || 0);
        } else {
          setError("Failed to load invoices.");
        }
      })
      .catch(() => setError("Network error loading invoices."))
      .finally(() => setLoading(false));
  }, [page]);

  const totalPages = Math.ceil(totalCount / 10) || 1;

  return (
    <div className="min-h-screen bg-black text-white font-sans selection:bg-[#facc15] selection:text-black pb-24">
      <div className="max-w-5xl mx-auto px-6 pt-32">
        <h1 className="text-4xl font-bold mb-2">Invoices</h1>
        <p className="text-zinc-400 text-sm mb-8">Receipts for every course, bundle, and subscription payment on your account.</p>

        {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

        <div className="bg-zinc-900/50 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-sm">
              <thead>
                <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider text-xs">
                  <th className="p-4 font-semibold">Invoice</th>
                  <th className="p-4 font-semibold">For</th>
                  <th className="p-4 font-semibold">Amount</th>
                  <th className="p-4 font-semibold">Date</th>
                  <th className="p-4 font-semibold text-center">Status</th>
                  <th className="p-4 font-semibold text-center">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-zinc-300">
                {loading ? (
                  <tr>
                    <td colSpan={6} className="p-16 text-center text-zinc-500">
                      <div className="flex flex-col items-center justify-center gap-3">
                        <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                        <span className="text-sm">Loading invoices...</span>
                      </div>
                    </td>
                  </tr>
                ) : invoices.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                      No invoices yet. A receipt appears here automatically after a successful payment.
                    </td>
                  </tr>
                ) : (
                  invoices.map((invoice) => (
                    <tr key={invoice.id} className="hover:bg-white/5 transition-colors">
                      <td className="p-4 font-mono text-xs text-zinc-300">{invoice.invoice_number}</td>
                      <td className="p-4">
                        <div className="font-semibold text-white">{invoice.source_label || SOURCE_TYPE_LABEL[invoice.source_type] || "—"}</div>
                        <div className="text-zinc-500 text-[10px] mt-0.5">{SOURCE_TYPE_LABEL[invoice.source_type] || invoice.source_type}</div>
                      </td>
                      <td className="p-4 font-bold text-[#facc15]">
                        {invoice.currency === 'INR' ? '₹' : `${invoice.currency} `}{parseFloat(invoice.amount).toLocaleString()}
                      </td>
                      <td className="p-4 text-zinc-400">{new Date(invoice.payment_date).toLocaleDateString()}</td>
                      <td className="p-4 text-center">
                        <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${STATUS_STYLE[invoice.status] || STATUS_STYLE.ISSUED}`}>
                          {invoice.status}
                        </span>
                      </td>
                      <td className="p-4">
                        <div className="flex items-center justify-center gap-2">
                          <button
                            onClick={() => setSelected(invoice)}
                            className="p-2 bg-white/5 hover:bg-white/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-white"
                            title="View Invoice"
                          >
                            <Receipt className="w-4 h-4" />
                          </button>
                          <Link
                            href={`/invoices/${invoice.id}/print`}
                            target="_blank"
                            className="p-2 bg-white/5 hover:bg-white/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-white"
                            title="Print / Save as PDF"
                          >
                            <Printer className="w-4 h-4" />
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {!loading && totalPages > 1 && (
          <div className="flex items-center justify-between mt-6">
            <div className="text-xs text-zinc-500">
              Page <span className="font-semibold text-white">{page}</span> of <span className="font-semibold text-white">{totalPages}</span> ({totalCount} total)
            </div>
            <div className="flex gap-2">
              <button disabled={page === 1} onClick={() => setPage(page - 1)} className="px-4 py-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all text-sm">
                Previous
              </button>
              <button disabled={page === totalPages} onClick={() => setPage(page + 1)} className="px-4 py-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all text-sm">
                Next
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Detail modal -- uses the same row data already fetched from the
          list (my-invoices/ and my-invoices/<id>/ return identical fields,
          see MyInvoiceSerializer), so no extra request is needed just to
          view it. The Print action separately opens the dedicated print
          page, which DOES call the detail endpoint -- see [id]/print. */}
      {selected && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-md shadow-2xl relative text-sm">
            <button
              onClick={() => setSelected(null)}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1">Invoice {selected.invoice_number}</h2>
            <p className="text-zinc-500 text-xs mb-6">{SOURCE_TYPE_LABEL[selected.source_type] || selected.source_type}</p>

            <div className="space-y-4">
              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">For</div>
                <div className="font-bold text-white">{selected.source_label || "—"}</div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Amount</div>
                  <div className="font-bold text-[#facc15] text-base">
                    {selected.currency === 'INR' ? '₹' : `${selected.currency} `}{parseFloat(selected.amount).toLocaleString()}
                  </div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Status</div>
                  <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full inline-block mt-0.5 ${STATUS_STYLE[selected.status] || STATUS_STYLE.ISSUED}`}>
                    {selected.status}
                  </span>
                </div>
              </div>

              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Payment Date</div>
                <div className="text-zinc-300">{new Date(selected.payment_date).toLocaleString()}</div>
              </div>

              <div className="pt-4 border-t border-white/5">
                <Link
                  href={`/invoices/${selected.id}/print`}
                  target="_blank"
                  className="w-full py-2.5 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-all flex items-center justify-center gap-2 text-xs"
                >
                  <Printer className="w-4 h-4" />
                  Print / Save as PDF
                </Link>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
