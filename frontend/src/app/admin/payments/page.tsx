"use client";

import { useEffect, useState } from "react";
import { Search, Eye, CheckCircle2, ChevronLeft, ChevronRight, X } from "lucide-react";

export default function PaymentsLedger() {
  const [purchases, setPurchases] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Filter and pagination states
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  // Detail modal state
  const [selectedPurchase, setSelectedPurchase] = useState<any>(null);
  const [markingPaidId, setMarkingPaidId] = useState<number | null>(null);

  const getCsrfToken = () => {
    let csrfToken = "";
    if (typeof document !== 'undefined' && document.cookie) {
      const cookies = document.cookie.split(';');
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.startsWith('csrftoken=')) {
          csrfToken = decodeURIComponent(cookie.substring('csrftoken='.length));
          break;
        }
      }
    }
    return csrfToken;
  };

  const fetchPurchases = async () => {
    setLoading(true);
    try {
      const queryParams = new URLSearchParams({
        page: page.toString(),
        search: search,
        status: statusFilter
      });

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/orders/purchases-admin/?${queryParams.toString()}`, {
        credentials: "include"
      });

      if (res.ok) {
        const data = await res.json();
        // Since pagination class returns { count, next, previous, results }
        setPurchases(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setError("Failed to fetch payment records");
      }
    } catch (err) {
      setError("Network error fetching payment records");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPurchases();
  }, [page, statusFilter]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchPurchases();
  };

  const handleMarkAsPaid = async (purchaseId: number) => {
    if (!confirm("Are you sure you want to mark this pending purchase as PAID? This will automatically enroll the student in the course.")) return;

    setMarkingPaidId(purchaseId);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/orders/purchases-admin/${purchaseId}/mark_paid/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include"
      });

      if (res.ok) {
        alert("Transaction marked as paid successfully!");
        fetchPurchases();
        if (selectedPurchase && selectedPurchase.id === purchaseId) {
          setSelectedPurchase({ ...selectedPurchase, status: 'SUCCESS' });
        }
      } else {
        alert("Failed to mark transaction as paid.");
      }
    } catch (err) {
      console.error(err);
      alert("Error approving transaction.");
    } finally {
      setMarkingPaidId(null);
    }
  };

  const totalPages = Math.ceil(totalCount / 10) || 1;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Payments Ledger</h1>
        <p className="text-zinc-400 text-sm mt-1">Track online checkouts, verify gateway receipts, and manage pending manual collections.</p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Filters & Search Row */}
      <div className="flex flex-col md:flex-row gap-4 mb-6">
        <form onSubmit={handleSearchSubmit} className="flex-1 flex gap-2">
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-zinc-500 absolute left-4 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search by student, course, or Razorpay ID..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-zinc-900 border border-white/5 rounded-xl pl-11 pr-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors placeholder:text-zinc-500"
            />
          </div>
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
            <option value="SUCCESS">Success / Paid</option>
            <option value="PENDING">Pending</option>
            <option value="FAILED">Failed</option>
          </select>
        </div>
      </div>

      {/* Purchases Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Student Details</th>
                <th className="p-4 font-semibold">Course</th>
                <th className="p-4 font-semibold">Amount</th>
                <th className="p-4 font-semibold">Transaction Date</th>
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
                      <span className="text-sm">Loading transactions...</span>
                    </div>
                  </td>
                </tr>
              ) : purchases.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                    No matching payment records found.
                  </td>
                </tr>
              ) : (
                purchases.map(purchase => (
                  <tr key={purchase.id} className="hover:bg-white/5 transition-colors">
                    {/* Student Details */}
                    <td className="p-4">
                      <div className="font-bold text-white text-sm">{purchase.student_name}</div>
                      <div className="text-zinc-500 text-[10px] mt-0.5">{purchase.student_email}</div>
                    </td>

                    {/* Course Title */}
                    <td className="p-4 font-semibold text-white max-w-xs truncate">
                      {purchase.course_title}
                    </td>

                    {/* Amount */}
                    <td className="p-4 font-bold text-[#facc15] text-sm">
                      ₹{parseFloat(purchase.amount).toLocaleString()}
                    </td>

                    {/* Date */}
                    <td className="p-4 text-zinc-400">
                      {new Date(purchase.created_at).toLocaleString()}
                    </td>

                    {/* Status */}
                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${
                        purchase.status === 'SUCCESS'
                          ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                          : purchase.status === 'PENDING'
                          ? 'bg-yellow-500/10 text-[#facc15] border border-yellow-500/20'
                          : 'bg-red-500/10 text-red-400 border border-red-500/20'
                      }`}>
                        {purchase.status}
                      </span>
                    </td>

                    {/* Actions */}
                    <td className="p-4">
                      <div className="flex items-center justify-center gap-2">
                        <button
                          onClick={() => setSelectedPurchase(purchase)}
                          className="p-2 bg-white/5 hover:bg-white/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-white"
                          title="View Gateway IDs"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                        
                        {purchase.status === 'PENDING' && (
                          <button
                            onClick={() => handleMarkAsPaid(purchase.id)}
                            disabled={markingPaidId === purchase.id}
                            className="p-2 bg-[#facc15]/10 hover:bg-[#facc15]/20 rounded-xl transition-all inline-flex items-center justify-center text-[#facc15] disabled:opacity-50"
                            title="Mark as Paid"
                          >
                            <CheckCircle2 className="w-4 h-4" />
                          </button>
                        )}
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

      {/* Detail Modal */}
      {selectedPurchase && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-md shadow-2xl relative text-sm">
            <button
              onClick={() => setSelectedPurchase(null)}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1">Transaction Details</h2>
            <p className="text-zinc-500 text-xs mb-6">Payment metadata and gateway order tracking IDs.</p>

            <div className="space-y-4">
              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Student</div>
                <div className="font-bold text-white">{selectedPurchase.student_name}</div>
                <div className="text-xs text-zinc-400">{selectedPurchase.student_email}</div>
              </div>

              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Course</div>
                <div className="font-bold text-white">{selectedPurchase.course_title}</div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Amount</div>
                  <div className="font-bold text-[#facc15] text-base">₹{parseFloat(selectedPurchase.amount).toLocaleString()}</div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Status</div>
                  <div>
                    <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full inline-block mt-0.5 ${
                      selectedPurchase.status === 'SUCCESS'
                        ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                        : selectedPurchase.status === 'PENDING'
                        ? 'bg-yellow-500/10 text-[#facc15] border border-yellow-500/20'
                        : 'bg-red-500/10 text-red-400 border border-red-500/20'
                    }`}>
                      {selectedPurchase.status}
                    </span>
                  </div>
                </div>
              </div>

              <div className="pt-4 border-t border-white/5 space-y-3 font-mono text-xs">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1 font-sans">Razorpay Order ID</div>
                  <div className="bg-black/40 p-2.5 rounded-lg border border-white/5 text-zinc-300 select-all">
                    {selectedPurchase.razorpay_order_id || <span className="text-zinc-600 italic">None</span>}
                  </div>
                </div>

                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1 font-sans">Razorpay Payment ID</div>
                  <div className="bg-black/40 p-2.5 rounded-lg border border-white/5 text-zinc-300 select-all">
                    {selectedPurchase.razorpay_payment_id || <span className="text-zinc-600 italic">None</span>}
                  </div>
                </div>
              </div>

              {selectedPurchase.status === 'PENDING' && (
                <div className="pt-4 border-t border-white/5">
                  <button
                    onClick={() => handleMarkAsPaid(selectedPurchase.id)}
                    disabled={markingPaidId === selectedPurchase.id}
                    className="w-full py-2.5 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-all flex items-center justify-center gap-2 text-xs"
                  >
                    <CheckCircle2 className="w-4 h-4" />
                    {markingPaidId === selectedPurchase.id ? "Processing..." : "Approve & Mark as Paid"}
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
