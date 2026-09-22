"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Filter, RefreshCw, Banknote, AlertTriangle, CheckCircle2 } from "lucide-react";

// Matches the ₹-prefixed money formatting already used everywhere else in
// this admin panel (Payments/Payouts) for the common INR case, while still
// respecting the serializer's own `currency` field for any other value.
const fmtMoney = (amount: string, currency: string) =>
  `${currency === 'INR' ? '₹' : `${currency} `}${parseFloat(amount).toLocaleString()}`;

interface LedgerRef {
  id: number;
  username: string;
}

interface CourseRef {
  id: number;
  title: string;
}

interface LedgerEntry {
  id: number;
  instructor: LedgerRef;
  course: CourseRef | null;
  source_type: 'PURCHASE' | 'ORDER_ITEM' | 'SUBSCRIPTION_PAYMENT' | null;
  entry_type: 'EARNING' | 'CLAWBACK' | 'ADJUSTMENT';
  gross_amount: string;
  commission_rate: string;
  commission_amount: string;
  net_amount: string;
  currency: string;
  payout: number | null;
  payout_status: string | null;
  created_at: string;
}

interface Payout {
  id: number;
  recipient: LedgerRef;
  period_start: string;
  period_end: string;
  gross_amount: string;
  commission_amount: string;
  net_amount: string;
  status: string;
  method: string;
  reference_number: string;
  approved_by: LedgerRef | null;
  approved_at: string | null;
  paid_at: string | null;
  entry_count: number;
  created_at: string;
  updated_at: string;
  entries: LedgerEntry[];
}

interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export default function EarningsLedger() {
  // Section A: ledger entries browser
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [entriesLoading, setEntriesLoading] = useState(true);
  const [entriesError, setEntriesError] = useState("");
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  const [instructorIdFilter, setInstructorIdFilter] = useState("");
  const [courseIdFilter, setCourseIdFilter] = useState("");
  const [entryTypeFilter, setEntryTypeFilter] = useState("");
  const [sourceTypeFilter, setSourceTypeFilter] = useState("");
  const [payoutFilter, setPayoutFilter] = useState("");
  const [currencyFilter, setCurrencyFilter] = useState("");

  // Section B: create payout batch
  const [recipientId, setRecipientId] = useState("");
  const [eligibleEntries, setEligibleEntries] = useState<LedgerEntry[]>([]);
  const [eligibleLoading, setEligibleLoading] = useState(false);
  const [eligibleError, setEligibleError] = useState("");
  const [eligibleLoadedForId, setEligibleLoadedForId] = useState<string | null>(null);
  const [selectedEntryIds, setSelectedEntryIds] = useState<number[]>([]);

  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [method, setMethod] = useState("");
  const [referenceNumber, setReferenceNumber] = useState("");
  const [notes, setNotes] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [createdPayout, setCreatedPayout] = useState<Payout | null>(null);

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

  const entryTypeBadgeClass = (entryType: LedgerEntry['entry_type']) => {
    switch (entryType) {
      case 'EARNING': return 'bg-green-500/10 text-green-400 border border-green-500/20';
      case 'CLAWBACK': return 'bg-red-500/10 text-red-400 border border-red-500/20';
      default: return 'bg-blue-500/10 text-blue-400 border border-blue-500/20'; // ADJUSTMENT
    }
  };

  // Mirrors PayoutsLedger's own statusBadgeClass exactly -- payout_status
  // here is just Payout.Status surfaced through the ledger entry.
  const payoutStatusBadgeClass = (status: string) => {
    switch (status) {
      case 'PAID': return 'bg-green-500/10 text-green-400 border border-green-500/20';
      case 'APPROVED': return 'bg-blue-500/10 text-blue-400 border border-blue-500/20';
      case 'DRAFT': return 'bg-yellow-500/10 text-[#facc15] border border-yellow-500/20';
      case 'PROCESSING': return 'bg-purple-500/10 text-purple-400 border border-purple-500/20';
      default: return 'bg-red-500/10 text-red-400 border border-red-500/20'; // FAILED / CANCELLED
    }
  };

  const fetchLedgerEntries = async () => {
    setEntriesLoading(true);
    setEntriesError("");
    try {
      const queryParams = new URLSearchParams();
      queryParams.set("page", page.toString());
      if (instructorIdFilter) queryParams.set("instructor_id", instructorIdFilter);
      if (courseIdFilter) queryParams.set("course_id", courseIdFilter);
      if (entryTypeFilter) queryParams.set("entry_type", entryTypeFilter);
      if (sourceTypeFilter) queryParams.set("source_type", sourceTypeFilter);
      if (payoutFilter) queryParams.set("payout", payoutFilter);
      if (currencyFilter) queryParams.set("currency", currencyFilter);

      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/finance/admin/ledger-entries/?${queryParams.toString()}`,
        { credentials: "include" }
      );

      if (res.ok) {
        const data: PaginatedResponse<LedgerEntry> = await res.json();
        setEntries(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setEntriesError("Failed to fetch ledger entries.");
      }
    } catch (err) {
      setEntriesError("Network error fetching ledger entries.");
    } finally {
      setEntriesLoading(false);
    }
  };

  useEffect(() => {
    fetchLedgerEntries();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, entryTypeFilter, sourceTypeFilter]);

  const handleApplyFilters = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchLedgerEntries();
  };

  const handleResetFilters = () => {
    setInstructorIdFilter("");
    setCourseIdFilter("");
    setEntryTypeFilter("");
    setSourceTypeFilter("");
    setPayoutFilter("");
    setCurrencyFilter("");
    setPage(1);
    // fetchLedgerEntries reads current state at call time, so it must run
    // after the state above has actually flushed -- deferring to the
    // effect (via the entryType/sourceType deps already covers those two)
    // is not guaranteed for the plain text fields, so fetch explicitly
    // next tick.
    setTimeout(() => fetchLedgerEntries(), 0);
  };

  const fetchEligibleEntries = async () => {
    if (!recipientId) {
      setEligibleError("Enter a recipient user ID first.");
      return;
    }
    setEligibleLoading(true);
    setEligibleError("");
    setCreatedPayout(null);
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/finance/admin/eligible-ledger-entries/?instructor_id=${encodeURIComponent(recipientId)}`,
        { credentials: "include" }
      );
      if (res.ok) {
        // EligibleLedgerEntriesView is paginated (StandardResultsSetPagination),
        // so the response is {count, next, previous, results: [...]}, never a
        // bare array -- reading the envelope itself as the array silently
        // stored the object and crashed the very next render on
        // eligibleEntries.map() for any instructor with eligible entries.
        const data: { results?: LedgerEntry[] } = await res.json();
        setEligibleEntries(data.results || []);
        setSelectedEntryIds([]);
        setEligibleLoadedForId(recipientId);
      } else {
        setEligibleError("Failed to load eligible ledger entries for this instructor.");
        setEligibleEntries([]);
      }
    } catch (err) {
      setEligibleError("Network error loading eligible ledger entries.");
      setEligibleEntries([]);
    } finally {
      setEligibleLoading(false);
    }
  };

  const toggleEntrySelected = (id: number) => {
    setSelectedEntryIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  };

  const canSubmit = selectedEntryIds.length > 0 && !!periodStart && !!periodEnd && !!recipientId && !submitting;

  const handleCreatePayout = async () => {
    if (!canSubmit) return;

    const confirmed = window.confirm(
      `Create a DRAFT payout batch for recipient #${recipientId} covering ${selectedEntryIds.length} selected ledger ` +
      `entr${selectedEntryIds.length === 1 ? "y" : "ies"}?\n\n` +
      `This creates a real financial record. It can be approved and marked paid afterward from the Payouts page. Continue?`
    );
    if (!confirmed) return;

    setSubmitting(true);
    setSubmitError("");
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/finance/admin/payouts/create/`,
        {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken(),
          },
          body: JSON.stringify({
            recipient_id: parseInt(recipientId, 10),
            ledger_entry_ids: selectedEntryIds,
            period_start: periodStart,
            period_end: periodEnd,
            method: method,
            reference_number: referenceNumber,
            notes: notes,
          }),
        }
      );
      const data = await res.json();
      if (res.ok) {
        setCreatedPayout(data as Payout);
        setSelectedEntryIds([]);
        // The just-batched entries are no longer eligible -- refresh the
        // eligible list for this same instructor so a re-selection can't
        // include entries that already belong to the new payout.
        await fetchEligibleEntries();
        await fetchLedgerEntries();
      } else {
        setSubmitError(data.error || "Failed to create payout batch.");
      }
    } catch (err) {
      setSubmitError("Network error creating payout batch.");
    } finally {
      setSubmitting(false);
    }
  };

  const totalPages = Math.ceil(totalCount / 10) || 1;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Earnings Ledger</h1>
        <p className="text-zinc-400 text-sm mt-1">Browse raw instructor earning entries and batch them into draft payouts.</p>
        <Link
          href="/admin/payouts"
          className="inline-flex items-center gap-1.5 text-xs text-[#facc15] hover:underline mt-3"
        >
          <Banknote className="w-3.5 h-3.5" />
          Go to Payouts to approve batches and mark them paid
        </Link>
      </div>

      {/* ---------------- Section A: Ledger entries browser ---------------- */}
      <div className="mb-6">
        <h2 className="text-lg font-bold mb-1">Ledger Entries</h2>
        <p className="text-zinc-500 text-xs mb-4">Every EARNING, CLAWBACK, and ADJUSTMENT entry recorded against instructors.</p>

        {entriesError && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-4 text-sm">{entriesError}</div>
        )}

        {/* Filter row */}
        <form onSubmit={handleApplyFilters} className="bg-zinc-900 border border-white/10 rounded-2xl p-4 mb-4">
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Instructor ID</label>
              <input
                type="number"
                value={instructorIdFilter}
                onChange={(e) => setInstructorIdFilter(e.target.value)}
                placeholder="e.g. 12"
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Course ID</label>
              <input
                type="number"
                value={courseIdFilter}
                onChange={(e) => setCourseIdFilter(e.target.value)}
                placeholder="e.g. 4"
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Entry Type</label>
              <select
                value={entryTypeFilter}
                onChange={(e) => { setEntryTypeFilter(e.target.value); setPage(1); }}
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
              >
                <option value="">All Types</option>
                <option value="EARNING">Earning</option>
                <option value="CLAWBACK">Clawback</option>
                <option value="ADJUSTMENT">Adjustment</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Source Type</label>
              <select
                value={sourceTypeFilter}
                onChange={(e) => { setSourceTypeFilter(e.target.value); setPage(1); }}
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
              >
                <option value="">All Sources</option>
                <option value="PURCHASE">Purchase</option>
                <option value="ORDER_ITEM">Order Item</option>
                <option value="SUBSCRIPTION_PAYMENT">Subscription Payment</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Payout ID (or &apos;null&apos; for un-batched)</label>
              <input
                type="text"
                value={payoutFilter}
                onChange={(e) => setPayoutFilter(e.target.value)}
                placeholder="e.g. 7 or null"
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Currency</label>
              <input
                type="text"
                value={currencyFilter}
                onChange={(e) => setCurrencyFilter(e.target.value)}
                placeholder="e.g. INR"
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
          </div>

          <div className="flex gap-2 mt-3">
            <button
              type="submit"
              className="px-4 py-2 bg-zinc-800 border border-white/10 hover:bg-zinc-700 text-xs font-semibold rounded-xl transition-all inline-flex items-center gap-2"
            >
              <Filter className="w-3.5 h-3.5" />
              Apply Filters
            </button>
            <button
              type="button"
              onClick={handleResetFilters}
              className="px-4 py-2 bg-white/5 hover:bg-white/10 text-zinc-300 text-xs font-semibold rounded-xl transition-all"
            >
              Reset
            </button>
          </div>
        </form>

        {/* Ledger entries table */}
        <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                  <th className="p-4 font-semibold">Instructor</th>
                  <th className="p-4 font-semibold">Course</th>
                  <th className="p-4 font-semibold text-center">Entry Type</th>
                  <th className="p-4 font-semibold">Net Amount</th>
                  <th className="p-4 font-semibold">Payout</th>
                  <th className="p-4 font-semibold">Created At</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-zinc-300">
                {entriesLoading ? (
                  <tr>
                    <td colSpan={6} className="p-16 text-center text-zinc-500">
                      <div className="flex flex-col items-center justify-center gap-3">
                        <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                        <span className="text-sm">Loading ledger entries...</span>
                      </div>
                    </td>
                  </tr>
                ) : entries.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                      No matching ledger entries found.
                    </td>
                  </tr>
                ) : (
                  entries.map((entry) => (
                    <tr key={entry.id} className="hover:bg-white/5 transition-colors">
                      <td className="p-4 font-bold text-white text-sm">{entry.instructor.username}</td>
                      <td className="p-4 text-zinc-300 max-w-xs truncate">{entry.course ? entry.course.title : "—"}</td>
                      <td className="p-4 text-center">
                        <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${entryTypeBadgeClass(entry.entry_type)}`}>
                          {entry.entry_type}
                        </span>
                      </td>
                      <td className="p-4 font-bold text-[#facc15] text-sm">
                        {fmtMoney(entry.net_amount, entry.currency)}
                      </td>
                      <td className="p-4">
                        {entry.payout ? (
                          <div className="flex items-center gap-2">
                            <span className="text-zinc-400">#{entry.payout}</span>
                            {entry.payout_status && (
                              <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full ${payoutStatusBadgeClass(entry.payout_status)}`}>
                                {entry.payout_status}
                              </span>
                            )}
                          </div>
                        ) : (
                          <span className="px-2 py-0.5 text-[9px] font-bold rounded-full bg-zinc-700/40 text-zinc-400 border border-white/5">
                            Unbatched
                          </span>
                        )}
                      </td>
                      <td className="p-4 text-zinc-400">{new Date(entry.created_at).toLocaleString()}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        {/* Pagination footer */}
        {!entriesLoading && totalPages > 1 && (
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

      {/* ---------------- Section B: Create Payout Batch ---------------- */}
      <div className="mt-10">
        <h2 className="text-lg font-bold mb-1">Create Payout Batch</h2>
        <p className="text-zinc-500 text-xs mb-4">Select an instructor&apos;s eligible ledger entries and batch them into a new DRAFT payout.</p>

        <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6 shadow-2xl space-y-6">
          {/* Step 1: recipient picker */}
          <div>
            <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Recipient User ID</label>
            <div className="flex gap-2">
              <input
                type="number"
                value={recipientId}
                onChange={(e) => setRecipientId(e.target.value)}
                placeholder="e.g. 12"
                className="flex-1 bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15]"
              />
              <button
                onClick={fetchEligibleEntries}
                disabled={eligibleLoading || !recipientId}
                className="px-4 py-2 bg-zinc-800 border border-white/10 hover:bg-zinc-700 text-xs font-semibold rounded-xl transition-all disabled:opacity-50 inline-flex items-center gap-2 whitespace-nowrap"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${eligibleLoading ? "animate-spin" : ""}`} />
                {eligibleLoading ? "Loading..." : "Load Eligible Entries"}
              </button>
            </div>
            <p className="text-[10px] text-zinc-500 mt-1">There is no instructor-search endpoint yet -- enter the recipient&apos;s numeric user ID directly.</p>
          </div>

          {eligibleError && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-xl text-xs flex items-start gap-2">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              {eligibleError}
            </div>
          )}

          {/* Step 2: eligible entries checkboxes */}
          {eligibleLoadedForId !== null && !eligibleError && (
            <div>
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">
                Eligible Ledger Entries ({eligibleEntries.length})
              </div>
              {eligibleEntries.length === 0 ? (
                <div className="p-6 text-center text-zinc-500 text-xs bg-black/40 rounded-xl border border-white/5">
                  No eligible (un-batched) ledger entries found for this instructor.
                </div>
              ) : (
                <div className="border border-white/5 rounded-xl divide-y divide-white/5 max-h-72 overflow-y-auto">
                  {eligibleEntries.map((entry) => (
                    <label
                      key={entry.id}
                      className="flex items-center gap-3 p-3 text-xs hover:bg-white/5 cursor-pointer transition-colors"
                    >
                      <input
                        type="checkbox"
                        checked={selectedEntryIds.includes(entry.id)}
                        onChange={() => toggleEntrySelected(entry.id)}
                        className="w-4 h-4 accent-[#facc15]"
                      />
                      <span className="text-zinc-500 w-12 shrink-0">#{entry.id}</span>
                      <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full shrink-0 ${entryTypeBadgeClass(entry.entry_type)}`}>
                        {entry.entry_type}
                      </span>
                      <span className="flex-1 truncate text-zinc-300">{entry.course ? entry.course.title : "—"}</span>
                      <span className="font-bold text-[#facc15] shrink-0">
                        {fmtMoney(entry.net_amount, entry.currency)}
                      </span>
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Step 3 + 4: batch details */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Period Start</label>
              <input
                type="date"
                value={periodStart}
                onChange={(e) => setPeriodStart(e.target.value)}
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Period End</label>
              <input
                type="date"
                value={periodEnd}
                onChange={(e) => setPeriodEnd(e.target.value)}
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Method (optional)</label>
              <select
                value={method}
                onChange={(e) => setMethod(e.target.value)}
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
              >
                <option value="">Not set</option>
                <option value="BANK_TRANSFER">Bank Transfer</option>
                <option value="UPI">UPI</option>
                <option value="OTHER">Other</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Reference Number (optional)</label>
              <input
                type="text"
                value={referenceNumber}
                onChange={(e) => setReferenceNumber(e.target.value)}
                placeholder="e.g. bank transfer UTR"
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
            <div className="md:col-span-2">
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Notes (optional)</label>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                rows={2}
                placeholder="Internal notes about this batch"
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15] resize-none"
              />
            </div>
          </div>

          {submitError && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-xl text-xs flex items-start gap-2">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              {submitError}
            </div>
          )}

          {createdPayout && (
            <div className="bg-green-500/10 border border-green-500/20 rounded-xl p-4 text-xs space-y-2">
              <div className="flex items-center gap-2 text-green-400 font-bold">
                <CheckCircle2 className="w-4 h-4" />
                Payout #{createdPayout.id} created
              </div>
              <div className="text-zinc-300">
                Net Amount: <span className="font-bold text-[#facc15]">₹{parseFloat(createdPayout.net_amount).toLocaleString()}</span>
                {" "}&middot; {createdPayout.entry_count} entries batched.
              </div>
              <Link href="/admin/payouts" className="inline-flex items-center gap-1.5 text-[#facc15] hover:underline font-semibold">
                <Banknote className="w-3.5 h-3.5" />
                Continue to Payouts to approve &amp; pay
              </Link>
            </div>
          )}

          <button
            onClick={handleCreatePayout}
            disabled={!canSubmit}
            className="w-full py-2.5 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-all flex items-center justify-center gap-2 text-xs disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Banknote className="w-4 h-4" />
            {submitting ? "Creating Payout Batch..." : "Create Payout Batch"}
          </button>
        </div>
      </div>
    </div>
  );
}
