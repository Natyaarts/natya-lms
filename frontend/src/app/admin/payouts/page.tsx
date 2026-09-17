"use client";

import { useEffect, useState } from "react";
import { Eye, CheckCircle2, ChevronLeft, ChevronRight, X, Banknote, AlertTriangle } from "lucide-react";

export default function PayoutsLedger() {
  const [payouts, setPayouts] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  // Detail modal state.
  const [selectedPayout, setSelectedPayout] = useState<any>(null);
  const [approvingId, setApprovingId] = useState<number | null>(null);

  // Mark-as-Paid workflow state -- scoped to whichever payout the detail
  // modal currently has open; reset whenever that changes. Both fields are
  // optional on the backend (MarkPayoutPaidInputSerializer) -- an admin may
  // already have set method/reference_number when the payout was created,
  // or may only now know the real bank UTR once the transfer has actually
  // been sent.
  const [showMarkPaidForm, setShowMarkPaidForm] = useState(false);
  const [markPaidMethod, setMarkPaidMethod] = useState("");
  const [markPaidReference, setMarkPaidReference] = useState("");
  const [markPaidSubmitting, setMarkPaidSubmitting] = useState(false);
  const [markPaidError, setMarkPaidError] = useState("");
  const [markPaidResult, setMarkPaidResult] = useState<any>(null);

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

  // Payout.Status: DRAFT -> APPROVED -> PAID is the only path this UI
  // drives; PROCESSING/FAILED/CANCELLED exist on the model but nothing in
  // this phase (or any prior one) ever sets them, so they're only ever
  // display states here.
  const statusBadgeClass = (status: string) => {
    switch (status) {
      case 'PAID': return 'bg-green-500/10 text-green-400 border border-green-500/20';
      case 'APPROVED': return 'bg-blue-500/10 text-blue-400 border border-blue-500/20';
      case 'DRAFT': return 'bg-yellow-500/10 text-[#facc15] border border-yellow-500/20';
      case 'PROCESSING': return 'bg-purple-500/10 text-purple-400 border border-purple-500/20';
      default: return 'bg-red-500/10 text-red-400 border border-red-500/20'; // FAILED / CANCELLED
    }
  };

  const fetchPayouts = async () => {
    setLoading(true);
    try {
      const queryParams = new URLSearchParams({ page: page.toString() });
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/finance/admin/payouts/?${queryParams.toString()}`,
        { credentials: "include" }
      );

      if (res.ok) {
        const data = await res.json();
        setPayouts(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setError("Failed to fetch payout records");
      }
    } catch (err) {
      setError("Network error fetching payout records");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPayouts();
  }, [page]);

  const openPayout = (payout: any) => {
    setSelectedPayout(payout);
    setShowMarkPaidForm(false);
    setMarkPaidError("");
    setMarkPaidResult(null);
    setMarkPaidMethod(payout.method || "");
    setMarkPaidReference(payout.reference_number || "");
  };

  const closePayoutModal = () => {
    setSelectedPayout(null);
    setShowMarkPaidForm(false);
    setMarkPaidError("");
    setMarkPaidResult(null);
  };

  // DRAFT -> APPROVED. No request body -- approve_payout only needs the
  // acting admin (request.user), already implied server-side.
  const handleApprove = async (payoutId: number) => {
    if (!confirm("Approve this payout batch? This locks in the amounts for payment but does not move any money yet.")) return;

    setApprovingId(payoutId);
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/finance/admin/payouts/${payoutId}/approve/`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
          credentials: "include",
        }
      );
      const data = await res.json();
      if (res.ok) {
        await fetchPayouts();
        if (selectedPayout && selectedPayout.id === payoutId) {
          setSelectedPayout(data);
        }
      } else {
        alert(data.error || "Failed to approve payout.");
      }
    } catch (err) {
      alert("Network error approving payout.");
    } finally {
      setApprovingId(null);
    }
  };

  // APPROVED -> PAID. The backend (mark_payout_paid) is the source of
  // truth for this transition -- it never touches a LedgerEntry, only
  // this Payout row's status/paid_at/method/reference_number, and rejects
  // anything not currently APPROVED (including an already-PAID payout, so
  // a double submission cannot create a duplicate financial effect).
  const handleMarkPaid = async () => {
    if (!selectedPayout) return;

    const confirmed = window.confirm(
      `Mark payout #${selectedPayout.id} (₹${parseFloat(selectedPayout.net_amount).toLocaleString()} to ${selectedPayout.recipient.username}) as PAID?\n\n` +
      `This should only be done once the bank transfer / UPI payment has actually been sent. Continue?`
    );
    if (!confirmed) return;

    setMarkPaidSubmitting(true);
    setMarkPaidError("");
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/finance/admin/payouts/${selectedPayout.id}/mark-paid/`,
        {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(),
          },
          body: JSON.stringify({
            method: markPaidMethod,
            reference_number: markPaidReference,
          }),
        }
      );
      const data = await res.json();
      if (res.ok) {
        setMarkPaidResult(data);
        setShowMarkPaidForm(false);
        setSelectedPayout(data);
        await fetchPayouts();
      } else {
        setMarkPaidError(data.error || "Failed to mark payout as paid.");
      }
    } catch (err) {
      setMarkPaidError("Network error marking payout as paid.");
    } finally {
      setMarkPaidSubmitting(false);
    }
  };

  const totalPages = Math.ceil(totalCount / 10) || 1;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Instructor Payouts</h1>
        <p className="text-zinc-400 text-sm mt-1">Review batched instructor earnings, approve payout batches, and record completed payments.</p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Payouts Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Recipient</th>
                <th className="p-4 font-semibold">Period</th>
                <th className="p-4 font-semibold">Net Amount</th>
                <th className="p-4 font-semibold">Method / Reference</th>
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
                      <span className="text-sm">Loading payouts...</span>
                    </div>
                  </td>
                </tr>
              ) : payouts.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                    No payout batches found.
                  </td>
                </tr>
              ) : (
                payouts.map(payout => (
                  <tr key={payout.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4">
                      <div className="font-bold text-white text-sm">{payout.recipient.username}</div>
                      <div className="text-zinc-500 text-[10px] mt-0.5">Payout #{payout.id} &middot; {payout.entry_count} entries</div>
                    </td>

                    <td className="p-4 text-zinc-400">
                      {payout.period_start} &rarr; {payout.period_end}
                    </td>

                    <td className="p-4 font-bold text-[#facc15] text-sm">
                      ₹{parseFloat(payout.net_amount).toLocaleString()}
                    </td>

                    <td className="p-4 text-zinc-400">
                      {payout.method ? (
                        <>
                          <div>{payout.method}</div>
                          {payout.reference_number && (
                            <div className="text-[10px] text-zinc-500 mt-0.5 font-mono">{payout.reference_number}</div>
                          )}
                        </>
                      ) : (
                        <span className="text-zinc-600 italic">Not set</span>
                      )}
                    </td>

                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${statusBadgeClass(payout.status)}`}>
                        {payout.status}
                      </span>
                    </td>

                    <td className="p-4">
                      <div className="flex items-center justify-center gap-2">
                        <button
                          onClick={() => openPayout(payout)}
                          className="p-2 bg-white/5 hover:bg-white/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-white"
                          title="View Payout Details"
                        >
                          <Eye className="w-4 h-4" />
                        </button>

                        {payout.status === 'DRAFT' && (
                          <button
                            onClick={() => handleApprove(payout.id)}
                            disabled={approvingId === payout.id}
                            className="p-2 bg-blue-500/10 hover:bg-blue-500/20 rounded-xl transition-all inline-flex items-center justify-center text-blue-400 disabled:opacity-50"
                            title="Approve Payout"
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
      {selectedPayout && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-md shadow-2xl relative text-sm">
            <button
              onClick={closePayoutModal}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1">Payout #{selectedPayout.id}</h2>
            <p className="text-zinc-500 text-xs mb-6">Batched instructor earnings for {selectedPayout.period_start} &rarr; {selectedPayout.period_end}.</p>

            <div className="space-y-4">
              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Recipient</div>
                <div className="font-bold text-white">{selectedPayout.recipient.username}</div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Net Amount</div>
                  <div className="font-bold text-[#facc15] text-base">₹{parseFloat(selectedPayout.net_amount).toLocaleString()}</div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Status</div>
                  <div>
                    <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full inline-block mt-0.5 ${statusBadgeClass(selectedPayout.status)}`}>
                      {selectedPayout.status}
                    </span>
                  </div>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Gross Amount</div>
                  <div className="text-zinc-300">₹{parseFloat(selectedPayout.gross_amount).toLocaleString()}</div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Commission</div>
                  <div className="text-zinc-300">₹{parseFloat(selectedPayout.commission_amount).toLocaleString()}</div>
                </div>
              </div>

              <div className="pt-4 border-t border-white/5 space-y-3 text-xs">
                <div className="flex justify-between">
                  <span className="text-zinc-500">Entries batched</span>
                  <span className="text-zinc-300 font-semibold">{selectedPayout.entry_count}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Approved by</span>
                  <span className="text-zinc-300 font-semibold">{selectedPayout.approved_by?.username || <span className="text-zinc-600 italic">Not yet</span>}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Approved at</span>
                  <span className="text-zinc-300 font-semibold">{selectedPayout.approved_at ? new Date(selectedPayout.approved_at).toLocaleString() : <span className="text-zinc-600 italic">Not yet</span>}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500">Paid at</span>
                  <span className="text-zinc-300 font-semibold">{selectedPayout.paid_at ? new Date(selectedPayout.paid_at).toLocaleString() : <span className="text-zinc-600 italic">Not yet</span>}</span>
                </div>
              </div>

              {selectedPayout.status === 'DRAFT' && (
                <div className="pt-4 border-t border-white/5">
                  <button
                    onClick={() => handleApprove(selectedPayout.id)}
                    disabled={approvingId === selectedPayout.id}
                    className="w-full py-2.5 bg-blue-500/90 hover:bg-blue-500 text-white font-bold rounded-xl transition-all flex items-center justify-center gap-2 text-xs disabled:opacity-50"
                  >
                    <CheckCircle2 className="w-4 h-4" />
                    {approvingId === selectedPayout.id ? "Approving..." : "Approve Payout"}
                  </button>
                </div>
              )}

              {/* Mark as Paid -- only ever relevant for an APPROVED payout;
                  the actual transition/idempotency rules are entirely the
                  backend's own (finance/services.py mark_payout_paid) --
                  this section only submits what an admin enters and
                  displays what that API reports. */}
              {(selectedPayout.status === 'APPROVED' || markPaidResult) && (
                <div className="pt-4 border-t border-white/5">
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Mark as Paid</div>

                  {markPaidResult ? (
                    // Success state -- takes priority over everything else,
                    // shown in place of the form until the modal is
                    // reopened, so a duplicate submission isn't even
                    // visually offered right after one just succeeded.
                    <div className="bg-green-500/10 border border-green-500/20 rounded-xl p-3 text-xs space-y-1.5">
                      <div className="flex items-center gap-2 text-green-400 font-bold">
                        <CheckCircle2 className="w-4 h-4" />
                        Payout marked PAID
                      </div>
                      <div className="text-zinc-300">Amount: ₹{parseFloat(markPaidResult.net_amount).toLocaleString()}</div>
                      {markPaidResult.reference_number && (
                        <div className="text-zinc-400 font-mono">Reference: <span className="select-all text-zinc-300">{markPaidResult.reference_number}</span></div>
                      )}
                    </div>
                  ) : !showMarkPaidForm ? (
                    <button
                      onClick={() => setShowMarkPaidForm(true)}
                      className="w-full py-2.5 border border-green-500/30 text-green-400 font-bold rounded-xl hover:bg-green-500/10 transition-all flex items-center justify-center gap-2 text-xs"
                    >
                      <Banknote className="w-4 h-4" />
                      Mark as Paid
                    </button>
                  ) : (
                    <div className="space-y-3">
                      <div>
                        <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">
                          Method (optional)
                        </label>
                        <select
                          value={markPaidMethod}
                          onChange={(e) => setMarkPaidMethod(e.target.value)}
                          className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
                        >
                          <option value="">Not set</option>
                          <option value="BANK_TRANSFER">Bank Transfer</option>
                          <option value="UPI">UPI</option>
                          <option value="OTHER">Other</option>
                        </select>
                      </div>
                      <div>
                        <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">
                          Reference Number (optional)
                        </label>
                        <input
                          type="text"
                          value={markPaidReference}
                          onChange={(e) => setMarkPaidReference(e.target.value)}
                          placeholder="e.g. bank transfer UTR"
                          className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
                        />
                      </div>

                      {markPaidError && (
                        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-2.5 rounded-xl text-xs flex items-start gap-2">
                          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                          {markPaidError}
                        </div>
                      )}

                      <div className="flex gap-2">
                        <button
                          onClick={() => { setShowMarkPaidForm(false); setMarkPaidError(""); }}
                          disabled={markPaidSubmitting}
                          className="flex-1 py-2.5 bg-white/5 hover:bg-white/10 text-zinc-300 font-semibold rounded-xl transition-all text-xs disabled:opacity-50"
                        >
                          Cancel
                        </button>
                        <button
                          onClick={handleMarkPaid}
                          disabled={markPaidSubmitting}
                          className="flex-1 py-2.5 bg-green-500/90 hover:bg-green-500 text-white font-bold rounded-xl transition-all flex items-center justify-center gap-2 text-xs disabled:opacity-50"
                        >
                          {markPaidSubmitting ? "Processing..." : "Confirm Paid"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
