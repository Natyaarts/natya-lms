"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Filter, ClipboardList, X } from "lucide-react";

interface AuditActorRef {
  id: number;
  username: string;
}

interface AuditLogEntry {
  id: number;
  actor: AuditActorRef | null;
  action: string;
  target_type: string;
  target_id: string;
  description: string;
  metadata: Record<string, any>;
  created_at: string;
}

interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export default function AuditLogsPage() {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  const [actionFilter, setActionFilter] = useState("");
  const [targetTypeFilter, setTargetTypeFilter] = useState("");
  const [actorIdFilter, setActorIdFilter] = useState("");

  const [selectedLog, setSelectedLog] = useState<AuditLogEntry | null>(null);

  const fetchLogs = async () => {
    setLoading(true);
    setError("");
    try {
      const queryParams = new URLSearchParams();
      queryParams.set("page", page.toString());
      if (actionFilter) queryParams.set("action", actionFilter);
      if (targetTypeFilter) queryParams.set("target_type", targetTypeFilter);
      if (actorIdFilter) queryParams.set("actor_id", actorIdFilter);

      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin/audit-logs/?${queryParams.toString()}`,
        { credentials: "include" }
      );

      if (res.ok) {
        const data: PaginatedResponse<AuditLogEntry> = await res.json();
        setLogs(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setError("Failed to fetch audit logs.");
      }
    } catch (err) {
      setError("Network error fetching audit logs.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchLogs();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const handleApplyFilters = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchLogs();
  };

  const handleResetFilters = () => {
    setActionFilter("");
    setTargetTypeFilter("");
    setActorIdFilter("");
    setPage(1);
    setTimeout(() => fetchLogs(), 0);
  };

  const totalPages = Math.ceil(totalCount / 20) || 1;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Audit Logs</h1>
        <p className="text-zinc-400 text-sm mt-1">A read-only, append-only trail of administrative actions -- role changes, refunds, payout approvals, and more.</p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Filter row */}
      <form onSubmit={handleApplyFilters} className="bg-zinc-900 border border-white/10 rounded-2xl p-4 mb-6">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div>
            <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Action</label>
            <input
              type="text"
              value={actionFilter}
              onChange={(e) => setActionFilter(e.target.value)}
              placeholder="e.g. ROLE_CHANGE"
              className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
            />
          </div>
          <div>
            <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Target Type</label>
            <input
              type="text"
              value={targetTypeFilter}
              onChange={(e) => setTargetTypeFilter(e.target.value)}
              placeholder="e.g. User, Refund, Payout, CourseInstructor"
              className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]"
            />
          </div>
          <div>
            <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">Actor ID</label>
            <input
              type="number"
              value={actorIdFilter}
              onChange={(e) => setActorIdFilter(e.target.value)}
              placeholder="e.g. 3"
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

      {/* Audit log table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Created At</th>
                <th className="p-4 font-semibold">Actor</th>
                <th className="p-4 font-semibold">Action</th>
                <th className="p-4 font-semibold">Target</th>
                <th className="p-4 font-semibold">Description</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={5} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading audit logs...</span>
                    </div>
                  </td>
                </tr>
              ) : logs.length === 0 ? (
                <tr>
                  <td colSpan={5} className="p-16 text-center text-zinc-500 text-sm">
                    <div className="flex flex-col items-center justify-center gap-2">
                      <ClipboardList className="w-6 h-6 text-zinc-600" />
                      No matching audit log entries found.
                    </div>
                  </td>
                </tr>
              ) : (
                logs.map((log) => (
                  <tr
                    key={log.id}
                    onClick={() => setSelectedLog(log)}
                    className="hover:bg-white/5 transition-colors cursor-pointer"
                  >
                    <td className="p-4 text-zinc-400">{new Date(log.created_at).toLocaleString()}</td>
                    <td className="p-4 font-bold text-white">
                      {log.actor ? log.actor.username : <span className="text-zinc-500 italic font-normal">System / deleted account</span>}
                    </td>
                    <td className="p-4">
                      <span className="px-2.5 py-0.5 text-[9px] font-bold rounded-full bg-zinc-700/40 text-zinc-300 border border-white/5">
                        {log.action}
                      </span>
                    </td>
                    <td className="p-4 text-zinc-300">{log.target_type} #{log.target_id}</td>
                    <td className="p-4 text-zinc-400 max-w-sm truncate">{log.description}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination footer */}
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

      {/* Detail modal */}
      {selectedLog && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-lg shadow-2xl relative text-sm">
            <button
              onClick={() => setSelectedLog(null)}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1">Audit Log #{selectedLog.id}</h2>
            <p className="text-zinc-500 text-xs mb-6">{new Date(selectedLog.created_at).toLocaleString()}</p>

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Actor</div>
                  <div className="font-bold text-white">
                    {selectedLog.actor ? selectedLog.actor.username : <span className="text-zinc-500 italic font-normal">System / deleted account</span>}
                  </div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Action</div>
                  <span className="px-2 py-0.5 text-[9px] font-bold rounded-full inline-block mt-0.5 bg-zinc-700/40 text-zinc-300 border border-white/5">
                    {selectedLog.action}
                  </span>
                </div>
              </div>

              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Target</div>
                <div className="text-zinc-300">{selectedLog.target_type} #{selectedLog.target_id}</div>
              </div>

              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Description</div>
                <div className="text-zinc-300">{selectedLog.description}</div>
              </div>

              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Metadata</div>
                <pre className="bg-black/40 p-3 rounded-lg border border-white/5 text-zinc-300 text-[11px] font-mono overflow-x-auto whitespace-pre-wrap break-words max-h-64 overflow-y-auto">
                  {JSON.stringify(selectedLog.metadata, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
