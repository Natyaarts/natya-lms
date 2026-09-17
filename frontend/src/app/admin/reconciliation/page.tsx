"use client";

import { useEffect, useState } from "react";
import { RefreshCw, ShieldAlert, Filter } from "lucide-react";

interface FinanceHealthSummary {
  generated_at: string;
  total_issues: number;
  critical: number;
  warnings: number;
  info: number;
  healthy_checks: number;
  total_checked: number;
  missing_ledger_count: number;
  missing_invoice_count: number;
  refund_clawback_mismatches: number;
  payout_mismatches: number;
  instructor_negative_balances: number;
  duplicate_records: number;
}

interface ReconciliationIssue {
  issue_type: string;
  severity: 'INFO' | 'WARNING' | 'CRITICAL';
  source_type: string;
  source_id: number;
  related_object_id: number | null;
  message: string;
  expected_value: string | null;
  actual_value: string | null;
  instructor_user_id: number | null;
  detected_at: string;
}

interface ReconciliationSummary {
  generated_at: string;
  total_issues: number;
  critical: number;
  warnings: number;
  info: number;
  healthy_checks: number;
  total_checked: number;
}

interface ReconciliationResponse {
  summary: ReconciliationSummary;
  issues: ReconciliationIssue[];
}

export default function ReconciliationPage() {
  const [health, setHealth] = useState<FinanceHealthSummary | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [healthError, setHealthError] = useState("");

  const [issues, setIssues] = useState<ReconciliationIssue[]>([]);
  const [issuesSummary, setIssuesSummary] = useState<ReconciliationSummary | null>(null);
  const [issuesLoading, setIssuesLoading] = useState(true);
  const [issuesError, setIssuesError] = useState("");

  const [severityFilter, setSeverityFilter] = useState("");
  const [issueTypeFilter, setIssueTypeFilter] = useState("");
  const [sourceTypeFilter, setSourceTypeFilter] = useState("");
  const [instructorIdFilter, setInstructorIdFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  const severityBadgeClass = (severity: string) => {
    switch (severity) {
      case 'CRITICAL': return 'bg-red-500/10 text-red-400 border border-red-500/20';
      case 'WARNING': return 'bg-yellow-500/10 text-[#facc15] border border-yellow-500/20';
      default: return 'bg-blue-500/10 text-blue-400 border border-blue-500/20'; // INFO
    }
  };

  const fetchHealth = async () => {
    setHealthLoading(true);
    setHealthError("");
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/finance/admin/finance-health/`,
        { credentials: "include" }
      );
      if (res.ok) {
        const data: FinanceHealthSummary = await res.json();
        setHealth(data);
      } else {
        setHealthError("Failed to fetch platform finance health.");
      }
    } catch (err) {
      setHealthError("Network error fetching platform finance health.");
    } finally {
      setHealthLoading(false);
    }
  };

  const fetchIssues = async () => {
    setIssuesLoading(true);
    setIssuesError("");
    try {
      const queryParams = new URLSearchParams();
      if (severityFilter) queryParams.set("severity", severityFilter);
      if (issueTypeFilter) queryParams.set("issue_type", issueTypeFilter);
      if (sourceTypeFilter) queryParams.set("source_type", sourceTypeFilter);
      if (instructorIdFilter) queryParams.set("instructor_id", instructorIdFilter);
      if (dateFrom) queryParams.set("date_from", dateFrom);
      if (dateTo) queryParams.set("date_to", dateTo);

      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/finance/admin/reconciliation/?${queryParams.toString()}`,
        { credentials: "include" }
      );
      if (res.ok) {
        const data: ReconciliationResponse = await res.json();
        setIssues(data.issues || []);
        setIssuesSummary(data.summary || null);
      } else {
        setIssuesError("Failed to fetch reconciliation issues.");
      }
    } catch (err) {
      setIssuesError("Network error fetching reconciliation issues.");
    } finally {
      setIssuesLoading(false);
    }
  };

  useEffect(() => {
    fetchHealth();
    fetchIssues();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleApplyFilters = (e: React.FormEvent) => {
    e.preventDefault();
    fetchIssues();
  };

  const handleRefreshAll = () => {
    fetchHealth();
    fetchIssues();
  };

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8 flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl font-bold">Finance Reconciliation</h1>
          <p className="text-zinc-400 text-sm mt-1">Live, computed-fresh checks across ledger entries, invoices, payouts, and refunds. Nothing here is stored or cached.</p>
        </div>
        <button
          onClick={handleRefreshAll}
          className="px-4 py-2.5 bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-sm font-semibold rounded-xl transition-all inline-flex items-center gap-2 shrink-0"
        >
          <RefreshCw className={`w-4 h-4 ${healthLoading || issuesLoading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {healthError && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{healthError}</div>
      )}

      {/* Platform-wide finance health stat cards */}
      <div className="mb-8">
        <h2 className="text-lg font-bold mb-1">Platform Finance Health</h2>
        <p className="text-zinc-500 text-xs mb-4">
          {health ? `Last checked ${new Date(health.generated_at).toLocaleString()}` : "Always unfiltered -- a whole-platform snapshot."}
        </p>

        {healthLoading ? (
          <div className="flex flex-col items-center justify-center p-16 gap-3">
            <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
            <span className="text-sm text-zinc-500">Running platform health scan...</span>
          </div>
        ) : health ? (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-4">
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Total Issues</div>
              <div className="text-2xl font-extrabold text-white">{health.total_issues}</div>
            </div>
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Critical</div>
              <div className="text-2xl font-extrabold text-red-400">{health.critical}</div>
            </div>
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Warnings</div>
              <div className="text-2xl font-extrabold text-[#facc15]">{health.warnings}</div>
            </div>
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Info</div>
              <div className="text-2xl font-extrabold text-blue-400">{health.info}</div>
            </div>
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Healthy Checks</div>
              <div className="text-2xl font-extrabold text-green-400">{health.healthy_checks} <span className="text-zinc-500 text-base font-semibold">/ {health.total_checked}</span></div>
            </div>
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Missing Ledger Entries</div>
              <div className="text-2xl font-extrabold text-white">{health.missing_ledger_count}</div>
            </div>
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Missing Invoices</div>
              <div className="text-2xl font-extrabold text-white">{health.missing_invoice_count}</div>
            </div>
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Refund/Clawback Mismatches</div>
              <div className="text-2xl font-extrabold text-white">{health.refund_clawback_mismatches}</div>
            </div>
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Payout Mismatches</div>
              <div className="text-2xl font-extrabold text-white">{health.payout_mismatches}</div>
            </div>
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Negative Instructor Balances</div>
              <div className="text-2xl font-extrabold text-white">{health.instructor_negative_balances}</div>
            </div>
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4">
              <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">Duplicate Records</div>
              <div className="text-2xl font-extrabold text-white">{health.duplicate_records}</div>
            </div>
          </div>
        ) : (
          <div className="p-16 text-center text-zinc-500 text-sm bg-zinc-900 border border-white/10 rounded-2xl">
            Finance health data unavailable.
          </div>
        )}
      </div>

      {/* Issues filter row + table */}
      <div>
        <div className="flex items-center justify-between mb-1 flex-wrap gap-2">
          <h2 className="text-lg font-bold">Reconciliation Issues</h2>
          {issuesSummary && (
            <span className="text-xs text-zinc-500">Last checked {new Date(issuesSummary.generated_at).toLocaleString()}</span>
          )}
        </div>
        <p className="text-zinc-500 text-xs mb-4">Filter and inspect individual detected issues. Recomputed fresh on every request.</p>

        {issuesError && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-4 text-sm">{issuesError}</div>
        )}

        <form onSubmit={handleApplyFilters} className="bg-zinc-900 border border-white/10 rounded-2xl p-4 mb-4">
          <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Severity</label>
              <select
                value={severityFilter}
                onChange={(e) => setSeverityFilter(e.target.value)}
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
              >
                <option value="">All Severities</option>
                <option value="INFO">Info</option>
                <option value="WARNING">Warning</option>
                <option value="CRITICAL">Critical</option>
              </select>
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Issue Type</label>
              <input
                type="text"
                value={issueTypeFilter}
                onChange={(e) => setIssueTypeFilter(e.target.value)}
                placeholder="e.g. PURCHASE_MISSING_LEDGER_ENTRY"
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Source Type</label>
              <input
                type="text"
                value={sourceTypeFilter}
                onChange={(e) => setSourceTypeFilter(e.target.value)}
                placeholder="PURCHASE / ORDER / PAYOUT / REFUND..."
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
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
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Date From</label>
              <input
                type="date"
                value={dateFrom}
                onChange={(e) => setDateFrom(e.target.value)}
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
            <div>
              <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Date To</label>
              <input
                type="date"
                value={dateTo}
                onChange={(e) => setDateTo(e.target.value)}
                className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
              />
            </div>
          </div>
          <p className="text-[10px] text-zinc-500 mt-2">
            Issue Type accepts any IssueType name, e.g. PURCHASE_MISSING_LEDGER_ENTRY, PURCHASE_MISSING_INVOICE, REFUND_MISSING_CLAWBACK, PAYOUT_TOTAL_MISMATCH, INSTRUCTOR_NEGATIVE_BALANCE, DUPLICATE_EARNING_ENTRY -- unrecognized values are ignored server-side.
          </p>

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
              onClick={() => {
                setSeverityFilter("");
                setIssueTypeFilter("");
                setSourceTypeFilter("");
                setInstructorIdFilter("");
                setDateFrom("");
                setDateTo("");
                setTimeout(() => fetchIssues(), 0);
              }}
              className="px-4 py-2 bg-white/5 hover:bg-white/10 text-zinc-300 text-xs font-semibold rounded-xl transition-all"
            >
              Reset
            </button>
          </div>
        </form>

        <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                  <th className="p-4 font-semibold text-center">Severity</th>
                  <th className="p-4 font-semibold">Issue Type</th>
                  <th className="p-4 font-semibold">Source</th>
                  <th className="p-4 font-semibold">Message</th>
                  <th className="p-4 font-semibold">Expected / Actual</th>
                  <th className="p-4 font-semibold">Detected At</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-zinc-300">
                {issuesLoading ? (
                  <tr>
                    <td colSpan={6} className="p-16 text-center text-zinc-500">
                      <div className="flex flex-col items-center justify-center gap-3">
                        <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                        <span className="text-sm">Running reconciliation scan...</span>
                      </div>
                    </td>
                  </tr>
                ) : issues.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                      <div className="flex flex-col items-center justify-center gap-2">
                        <ShieldAlert className="w-6 h-6 text-zinc-600" />
                        No reconciliation issues found for the current filters.
                      </div>
                    </td>
                  </tr>
                ) : (
                  issues.map((issue, idx) => (
                    <tr key={`${issue.source_type}-${issue.source_id}-${issue.issue_type}-${idx}`} className="hover:bg-white/5 transition-colors align-top">
                      <td className="p-4 text-center">
                        <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${severityBadgeClass(issue.severity)}`}>
                          {issue.severity}
                        </span>
                      </td>
                      <td className="p-4 font-semibold text-white">{issue.issue_type}</td>
                      <td className="p-4 text-zinc-400">{issue.source_type} #{issue.source_id}</td>
                      <td className="p-4 text-zinc-300 max-w-sm">{issue.message}</td>
                      <td className="p-4 text-zinc-400 font-mono text-[11px]">
                        {issue.expected_value !== null || issue.actual_value !== null ? (
                          <>
                            <div>Expected: <span className="text-zinc-300">{issue.expected_value ?? "—"}</span></div>
                            <div>Actual: <span className="text-zinc-300">{issue.actual_value ?? "—"}</span></div>
                          </>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="p-4 text-zinc-400">{new Date(issue.detected_at).toLocaleString()}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}
