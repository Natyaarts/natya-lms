"use client";

import { useEffect, useState } from "react";
import { Wallet, TrendingUp, CheckCircle2, AlertTriangle, ChevronLeft, ChevronRight } from "lucide-react";

// Teacher/mentor-facing earnings + payout surface. Every total shown here
// comes directly from the backend (finance/reconciliation.py's
// get_instructor_balance() via /api/finance/my-earnings-summary/, and the
// same PayoutListSerializer the admin payout page uses via
// /api/finance/my-payouts/) -- nothing on this page recalculates a
// financial figure; it only renders what those APIs report. Read-only:
// there is no approve/mark-paid/create-batch action anywhere on this page
// -- those remain admin-only operations (see /admin/payouts).
export default function MyEarningsPage() {
  const [summary, setSummary] = useState<any>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState("");

  const [entries, setEntries] = useState<any[]>([]);
  const [entriesLoading, setEntriesLoading] = useState(true);
  const [entriesError, setEntriesError] = useState("");
  const [entriesPage, setEntriesPage] = useState(1);
  const [entriesTotalCount, setEntriesTotalCount] = useState(0);

  const [payouts, setPayouts] = useState<any[]>([]);
  const [payoutsLoading, setPayoutsLoading] = useState(true);
  const [payoutsError, setPayoutsError] = useState("");
  const [payoutsPage, setPayoutsPage] = useState(1);
  const [payoutsTotalCount, setPayoutsTotalCount] = useState(0);

  const apiBase = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  // Payout.Status -- only the values the backend actually returns are
  // ever rendered here; no frontend-only status is invented.
  const statusBadgeClass = (status: string) => {
    switch (status) {
      case 'PAID': return 'bg-green-500/10 text-green-400 border border-green-500/20';
      case 'APPROVED': return 'bg-blue-500/10 text-blue-400 border border-blue-500/20';
      case 'DRAFT': return 'bg-yellow-500/10 text-[#facc15] border border-yellow-500/20';
      case 'PROCESSING': return 'bg-purple-500/10 text-purple-400 border border-purple-500/20';
      default: return 'bg-red-500/10 text-red-400 border border-red-500/20'; // FAILED / CANCELLED
    }
  };

  const fetchSummary = async () => {
    setSummaryLoading(true);
    setSummaryError("");
    try {
      const res = await fetch(`${apiBase}/api/finance/my-earnings-summary/`, { credentials: "include" });
      if (res.ok) {
        setSummary(await res.json());
      } else {
        setSummaryError("Failed to load earnings summary.");
      }
    } catch (err) {
      setSummaryError("Network error loading earnings summary.");
    } finally {
      setSummaryLoading(false);
    }
  };

  const fetchEntries = async () => {
    setEntriesLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/finance/my-earnings/?page=${entriesPage}`, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setEntries(data.results || []);
        setEntriesTotalCount(data.count || 0);
      } else {
        setEntriesError("Failed to load earnings history.");
      }
    } catch (err) {
      setEntriesError("Network error loading earnings history.");
    } finally {
      setEntriesLoading(false);
    }
  };

  const fetchPayouts = async () => {
    setPayoutsLoading(true);
    try {
      const res = await fetch(`${apiBase}/api/finance/my-payouts/?page=${payoutsPage}`, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setPayouts(data.results || []);
        setPayoutsTotalCount(data.count || 0);
      } else {
        setPayoutsError("Failed to load payout history.");
      }
    } catch (err) {
      setPayoutsError("Network error loading payout history.");
    } finally {
      setPayoutsLoading(false);
    }
  };

  useEffect(() => { fetchSummary(); }, []);
  useEffect(() => { fetchEntries(); }, [entriesPage]);
  useEffect(() => { fetchPayouts(); }, [payoutsPage]);

  const entriesTotalPages = Math.ceil(entriesTotalCount / 10) || 1;
  const payoutsTotalPages = Math.ceil(payoutsTotalCount / 10) || 1;

  // Simple display arithmetic only (difference of two backend-authoritative
  // numbers) -- not a business calculation. "earned" and "paid" both come
  // straight from get_instructor_balance(); this never touches commission
  // rates, eligibility, or payout rules.
  const pendingAmount = summary ? (parseFloat(summary.earned) - parseFloat(summary.paid)) : null;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">My Earnings</h1>
        <p className="text-zinc-400 text-sm mt-1">Your course earnings, commission, and payout history.</p>
      </div>

      {/* Summary Cards */}
      {summaryError ? (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-8 text-sm flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          {summaryError}
        </div>
      ) : summaryLoading ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          {[0, 1, 2, 3].map(i => (
            <div key={i} className="bg-zinc-900 border border-white/10 rounded-2xl p-5 h-24 flex items-center justify-center">
              <div className="w-5 h-5 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
            </div>
          ))}
        </div>
      ) : summary ? (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-8">
          <div className="bg-zinc-900 border border-white/10 rounded-2xl p-5">
            <div className="flex items-center gap-2 text-zinc-500 text-xs font-semibold uppercase tracking-wider mb-2">
              <TrendingUp className="w-3.5 h-3.5" /> Total Earned
            </div>
            <div className="text-xl font-bold text-white">₹{parseFloat(summary.earned).toLocaleString()}</div>
          </div>
          <div className="bg-zinc-900 border border-white/10 rounded-2xl p-5">
            <div className="flex items-center gap-2 text-zinc-500 text-xs font-semibold uppercase tracking-wider mb-2">
              <CheckCircle2 className="w-3.5 h-3.5" /> Paid Out
            </div>
            <div className="text-xl font-bold text-green-400">₹{parseFloat(summary.paid).toLocaleString()}</div>
          </div>
          <div className="bg-zinc-900 border border-white/10 rounded-2xl p-5">
            <div className="flex items-center gap-2 text-zinc-500 text-xs font-semibold uppercase tracking-wider mb-2">
              <Wallet className="w-3.5 h-3.5" /> Pending (Not Yet Paid)
            </div>
            <div className="text-xl font-bold text-[#facc15]">₹{pendingAmount !== null ? pendingAmount.toLocaleString() : '--'}</div>
          </div>
          <div className="bg-zinc-900 border border-white/10 rounded-2xl p-5">
            <div className="flex items-center gap-2 text-zinc-500 text-xs font-semibold uppercase tracking-wider mb-2">
              <Wallet className="w-3.5 h-3.5" /> Available Balance
            </div>
            <div className="text-xl font-bold text-blue-400">₹{parseFloat(summary.available).toLocaleString()}</div>
          </div>
        </div>
      ) : null}

      {/* Outstanding debt -- only ever shown if non-zero (e.g. a refund
          clawback exceeding what's still unbatched). Not shown as an
          alarming error by default since most instructors will never see it. */}
      {summary && parseFloat(summary.outstanding_debt) > 0 && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-8 text-sm flex items-start gap-2">
          <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
          <div>
            <div className="font-bold">Outstanding balance: ₹{parseFloat(summary.outstanding_debt).toLocaleString()}</div>
            <div className="text-red-400/80 text-xs mt-0.5">
              This reflects refunded/clawed-back earnings not yet offset by a future payout. It will be automatically deducted from your next eligible payout.
            </div>
          </div>
        </div>
      )}

      {/* Payout History */}
      <div className="mb-8">
        <h2 className="text-lg font-bold mb-3">Payout History</h2>
        <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                  <th className="p-4 font-semibold">Period</th>
                  <th className="p-4 font-semibold">Net Amount</th>
                  <th className="p-4 font-semibold">Method / Reference</th>
                  <th className="p-4 font-semibold">Paid At</th>
                  <th className="p-4 font-semibold text-center">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-zinc-300">
                {payoutsError ? (
                  <tr><td colSpan={5} className="p-8 text-center text-red-400 text-xs">{payoutsError}</td></tr>
                ) : payoutsLoading ? (
                  <tr>
                    <td colSpan={5} className="p-12 text-center text-zinc-500">
                      <div className="flex flex-col items-center justify-center gap-3">
                        <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                        <span className="text-sm">Loading payouts...</span>
                      </div>
                    </td>
                  </tr>
                ) : payouts.length === 0 ? (
                  <tr><td colSpan={5} className="p-12 text-center text-zinc-500 text-sm">No payouts yet. Earnings appear here once an admin batches and processes a payout.</td></tr>
                ) : (
                  payouts.map(payout => (
                    <tr key={payout.id} className="hover:bg-white/5 transition-colors">
                      <td className="p-4 text-zinc-400">{payout.period_start} &rarr; {payout.period_end}</td>
                      <td className="p-4 font-bold text-[#facc15] text-sm">₹{parseFloat(payout.net_amount).toLocaleString()}</td>
                      <td className="p-4 text-zinc-400">
                        {payout.method ? (
                          <>
                            <div>{payout.method}</div>
                            {payout.reference_number && (
                              <div className="text-[10px] text-zinc-500 mt-0.5 font-mono">{payout.reference_number}</div>
                            )}
                          </>
                        ) : <span className="text-zinc-600 italic">Not set</span>}
                      </td>
                      <td className="p-4 text-zinc-400">{payout.paid_at ? new Date(payout.paid_at).toLocaleDateString() : <span className="text-zinc-600 italic">Not yet</span>}</td>
                      <td className="p-4 text-center">
                        <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${statusBadgeClass(payout.status)}`}>
                          {payout.status}
                        </span>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
        {!payoutsLoading && payoutsTotalPages > 1 && (
          <div className="flex items-center justify-between mt-4">
            <div className="text-xs text-zinc-500">Page <span className="font-semibold text-white">{payoutsPage}</span> of <span className="font-semibold text-white">{payoutsTotalPages}</span></div>
            <div className="flex gap-2">
              <button disabled={payoutsPage === 1} onClick={() => setPayoutsPage(payoutsPage - 1)} className="p-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all inline-flex items-center">
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button disabled={payoutsPage === payoutsTotalPages} onClick={() => setPayoutsPage(payoutsPage + 1)} className="p-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all inline-flex items-center">
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Earnings / Ledger History */}
      <div>
        <h2 className="text-lg font-bold mb-3">Earnings History</h2>
        <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                  <th className="p-4 font-semibold">Course</th>
                  <th className="p-4 font-semibold">Type</th>
                  <th className="p-4 font-semibold">Gross</th>
                  <th className="p-4 font-semibold">Commission</th>
                  <th className="p-4 font-semibold">Net</th>
                  <th className="p-4 font-semibold">Date</th>
                  <th className="p-4 font-semibold text-center">Payout Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-zinc-300">
                {entriesError ? (
                  <tr><td colSpan={7} className="p-8 text-center text-red-400 text-xs">{entriesError}</td></tr>
                ) : entriesLoading ? (
                  <tr>
                    <td colSpan={7} className="p-12 text-center text-zinc-500">
                      <div className="flex flex-col items-center justify-center gap-3">
                        <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                        <span className="text-sm">Loading earnings...</span>
                      </div>
                    </td>
                  </tr>
                ) : entries.length === 0 ? (
                  <tr><td colSpan={7} className="p-12 text-center text-zinc-500 text-sm">No earnings yet.</td></tr>
                ) : (
                  entries.map(entry => (
                    <tr key={entry.id} className="hover:bg-white/5 transition-colors">
                      <td className="p-4 font-semibold text-white max-w-xs truncate">{entry.course.title}</td>
                      <td className="p-4 text-zinc-400">{entry.entry_type}</td>
                      <td className="p-4 text-zinc-400">₹{parseFloat(entry.gross_amount).toLocaleString()}</td>
                      <td className="p-4 text-zinc-400">₹{parseFloat(entry.commission_amount).toLocaleString()} <span className="text-zinc-600">({parseFloat(entry.commission_rate)}%)</span></td>
                      <td className="p-4 font-bold text-[#facc15]">₹{parseFloat(entry.net_amount).toLocaleString()}</td>
                      <td className="p-4 text-zinc-400">{new Date(entry.created_at).toLocaleDateString()}</td>
                      <td className="p-4 text-center">
                        {entry.payout_status ? (
                          <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${statusBadgeClass(entry.payout_status)}`}>
                            {entry.payout_status}
                          </span>
                        ) : (
                          <span className="px-2.5 py-0.5 text-[9px] font-bold rounded-full bg-zinc-700/50 text-zinc-400 border border-white/5">UNBATCHED</span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
        {!entriesLoading && entriesTotalPages > 1 && (
          <div className="flex items-center justify-between mt-4">
            <div className="text-xs text-zinc-500">Page <span className="font-semibold text-white">{entriesPage}</span> of <span className="font-semibold text-white">{entriesTotalPages}</span> ({entriesTotalCount} total entries)</div>
            <div className="flex gap-2">
              <button disabled={entriesPage === 1} onClick={() => setEntriesPage(entriesPage - 1)} className="p-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all inline-flex items-center">
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button disabled={entriesPage === entriesTotalPages} onClick={() => setEntriesPage(entriesPage + 1)} className="p-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all inline-flex items-center">
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
