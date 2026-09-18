"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { Printer } from "lucide-react";

// Invoice visibility frontend gap fix -- Step 3 (print/download). No PDF
// generation library exists anywhere in this codebase (confirmed: no
// weasyprint/reportlab, and Invoice.pdf_file is an unpopulated schema-only
// placeholder) -- rather than building a new PDF-generation system, this
// is a plain, print-styled page: the browser's native Print dialog (which
// on every modern browser offers "Save as PDF") is the download mechanism,
// no new backend infrastructure required. Calls the EXISTING invoice
// detail endpoint (GET /api/finance/my-invoices/<id>/) -- reused exactly
// as-is, not duplicated.
const SOURCE_TYPE_LABEL: Record<string, string> = {
  PURCHASE: "Course Purchase",
  ORDER: "Order Checkout",
  SUBSCRIPTION_PAYMENT: "Subscription Payment",
};

export default function InvoicePrintPage() {
  const params = useParams();
  const id = params?.id as string;

  const [invoice, setInvoice] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  useEffect(() => {
    if (!id) return;
    fetch(`${API}/api/finance/my-invoices/${id}/`, { credentials: "include" })
      .then(async (res) => {
        if (res.ok) {
          setInvoice(await res.json());
        } else if (res.status === 404) {
          setError("Invoice not found.");
        } else {
          setError("Failed to load invoice.");
        }
      })
      .catch(() => setError("Network error loading invoice."))
      .finally(() => setLoading(false));
  }, [id]);

  return (
    <div className="min-h-screen bg-white text-black font-sans">
      <style>{`
        @media print {
          .no-print { display: none !important; }
          body { background: white !important; }
        }
      `}</style>

      <div className="no-print border-b border-zinc-200 sticky top-0 bg-white z-10">
        <div className="max-w-2xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link href="/invoices" className="text-sm font-medium text-zinc-600 hover:text-black transition-colors">
            &larr; Back to Invoices
          </Link>
          {invoice && (
            <button
              onClick={() => window.print()}
              className="px-4 py-2 bg-black text-white text-sm font-semibold rounded-lg hover:bg-zinc-800 transition-all flex items-center gap-2"
            >
              <Printer className="w-4 h-4" />
              Print / Save as PDF
            </button>
          )}
        </div>
      </div>

      <div className="max-w-2xl mx-auto px-6 py-16">
        {loading ? (
          <div className="text-center text-zinc-500 py-20">Loading invoice...</div>
        ) : error ? (
          <div className="text-center text-red-600 py-20">{error}</div>
        ) : invoice ? (
          <div>
            <div className="flex items-start justify-between mb-12 pb-8 border-b-2 border-black">
              <div>
                <h1 className="text-2xl font-bold tracking-tight">Natya</h1>
                <p className="text-zinc-500 text-sm mt-1">Payment Receipt</p>
              </div>
              <div className="text-right">
                <div className="text-xs text-zinc-500 uppercase tracking-wider">Invoice Number</div>
                <div className="font-mono font-semibold">{invoice.invoice_number}</div>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-8 mb-10">
              <div>
                <div className="text-xs text-zinc-500 uppercase tracking-wider mb-1">Payment Date</div>
                <div className="font-medium">{new Date(invoice.payment_date).toLocaleString()}</div>
              </div>
              <div>
                <div className="text-xs text-zinc-500 uppercase tracking-wider mb-1">Status</div>
                <div className="font-medium">{invoice.status}</div>
              </div>
            </div>

            <table className="w-full text-left border-collapse mb-10">
              <thead>
                <tr className="border-b-2 border-black text-xs uppercase tracking-wider text-zinc-500">
                  <th className="pb-3 font-semibold">Description</th>
                  <th className="pb-3 font-semibold">Type</th>
                  <th className="pb-3 font-semibold text-right">Amount</th>
                </tr>
              </thead>
              <tbody>
                <tr className="border-b border-zinc-200">
                  <td className="py-4 font-medium">{invoice.source_label || "—"}</td>
                  <td className="py-4 text-zinc-500">{SOURCE_TYPE_LABEL[invoice.source_type] || invoice.source_type}</td>
                  <td className="py-4 text-right font-medium">
                    {invoice.currency === 'INR' ? '₹' : `${invoice.currency} `}{parseFloat(invoice.amount).toLocaleString()}
                  </td>
                </tr>
              </tbody>
            </table>

            <div className="flex justify-end">
              <div className="w-56">
                <div className="flex justify-between py-3 border-t-2 border-black font-bold text-lg">
                  <span>Total Paid</span>
                  <span>{invoice.currency === 'INR' ? '₹' : `${invoice.currency} `}{parseFloat(invoice.amount).toLocaleString()}</span>
                </div>
              </div>
            </div>

            <div className="mt-16 pt-8 border-t border-zinc-200 text-xs text-zinc-400">
              This is a computer-generated receipt and does not require a signature.
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
