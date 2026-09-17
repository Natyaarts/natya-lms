"use client";

/**
 * Admin Dashboard Completion. GET /api/orders/subscriptions-admin/ --
 * admin list/retrieve of every subscription, plus the one existing
 * mutating action this ViewSet has always had: POST
 * .../cancel-immediate/, which cuts off access right now rather than at
 * the end of the current paid period (unlike a normal end-of-period
 * cancellation elsewhere in the app). That endpoint's response is a
 * plain SubscriptionSerializer (no `user` field) -- so after a successful
 * call this page re-fetches the current page of the list instead of
 * trying to merge the partial response into local state.
 */

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight, Ban } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const PAGE_SIZE = 10;

const STATUS_OPTIONS = [
  "CREATED",
  "AUTHENTICATED",
  "ACTIVE",
  "PENDING",
  "HALTED",
  "PAUSED",
  "CANCELLED",
  "EXPIRED",
  "COMPLETED",
] as const;

const TERMINAL_STATUSES = new Set(["CANCELLED", "EXPIRED", "COMPLETED"]);

interface PlanSummary {
  id: number;
  name: string;
  billing_interval: "MONTHLY" | "YEARLY";
  price: string;
  currency: string;
}

interface SubUserRef {
  id: number;
  username: string;
}

interface AdminSubscription {
  id: number;
  status: string;
  plan: PlanSummary;
  current_period_start: string | null;
  current_period_end: string | null;
  access_until: string | null;
  cancel_at_period_end: boolean;
  cancelled_at: string | null;
  effective_access_until: string | null;
  created_at: string;
  user: SubUserRef;
}

interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

function getCsrfToken(): string {
  let csrfToken = "";
  if (typeof document !== "undefined" && document.cookie) {
    const cookies = document.cookie.split(";");
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.startsWith("csrftoken=")) {
        csrfToken = decodeURIComponent(cookie.substring("csrftoken=".length));
        break;
      }
    }
  }
  return csrfToken;
}

function statusBadgeClass(status: string): string {
  if (status === "ACTIVE") return "bg-green-500/10 text-green-400 border border-green-500/20";
  if (status === "CANCELLED" || status === "EXPIRED") return "bg-red-500/10 text-red-400 border border-red-500/20";
  if (status === "PENDING" || status === "HALTED" || status === "PAUSED")
    return "bg-yellow-500/10 text-[#facc15] border border-yellow-500/20";
  return "bg-blue-500/10 text-blue-400 border border-blue-500/20"; // CREATED / AUTHENTICATED / COMPLETED
}

export default function AdminSubscriptionsPage() {
  const [subscriptions, setSubscriptions] = useState<AdminSubscription[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  const [statusFilter, setStatusFilter] = useState("");
  const [planIdFilter, setPlanIdFilter] = useState("");
  const [userIdFilter, setUserIdFilter] = useState("");

  const [cancellingId, setCancellingId] = useState<number | null>(null);

  const fetchSubscriptions = async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ page: page.toString() });
      if (statusFilter) params.set("status", statusFilter);
      if (planIdFilter) params.set("plan_id", planIdFilter);
      if (userIdFilter) params.set("user_id", userIdFilter);

      const res = await fetch(`${API_BASE}/api/orders/subscriptions-admin/?${params.toString()}`, {
        credentials: "include",
      });
      if (res.ok) {
        const data: PaginatedResponse<AdminSubscription> = await res.json();
        setSubscriptions(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setError("Failed to fetch subscriptions.");
      }
    } catch {
      setError("Network error fetching subscriptions.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSubscriptions();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, statusFilter]);

  const handleFilterSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchSubscriptions();
  };

  const handleCancelImmediate = async (sub: AdminSubscription) => {
    if (
      !confirm(
        "Cancel this subscription IMMEDIATELY? Unlike a normal end-of-period cancellation, this cuts off access right now, not at the end of the paid period. This cannot be undone. Continue?"
      )
    )
      return;

    setCancellingId(sub.id);
    try {
      const res = await fetch(`${API_BASE}/api/orders/subscriptions-admin/${sub.id}/cancel-immediate/`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
      });
      if (res.ok) {
        // Response shape here is a plain SubscriptionSerializer (missing
        // `user`) -- re-fetch the full page rather than merging it in.
        await fetchSubscriptions();
      } else {
        const data = await res.json().catch(() => null);
        alert((data && (data.error || data.detail)) || "Failed to cancel subscription.");
      }
    } catch {
      alert("Network error cancelling subscription.");
    } finally {
      setCancellingId(null);
    }
  };

  const totalPages = Math.ceil(totalCount / PAGE_SIZE) || 1;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Subscriptions</h1>
        <p className="text-zinc-400 text-sm mt-1">Track recurring subscriptions and cancel access when needed.</p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Filters Row */}
      <form onSubmit={handleFilterSubmit} className="flex flex-col md:flex-row gap-4 mb-6">
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
            {STATUS_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
        <input
          type="number"
          placeholder="Filter by plan ID..."
          value={planIdFilter}
          onChange={(e) => setPlanIdFilter(e.target.value)}
          className="w-full md:w-48 bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-500"
        />
        <input
          type="number"
          placeholder="Filter by user ID..."
          value={userIdFilter}
          onChange={(e) => setUserIdFilter(e.target.value)}
          className="w-full md:w-48 bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-500"
        />
        <button
          type="submit"
          className="px-5 py-3 bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-sm font-semibold rounded-xl transition-all"
        >
          Apply Filters
        </button>
      </form>

      {/* Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">User</th>
                <th className="p-4 font-semibold">Plan</th>
                <th className="p-4 font-semibold text-center">Status</th>
                <th className="p-4 font-semibold">Access Until</th>
                <th className="p-4 font-semibold">Created At</th>
                <th className="p-4 font-semibold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading subscriptions...</span>
                    </div>
                  </td>
                </tr>
              ) : subscriptions.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                    No subscriptions found.
                  </td>
                </tr>
              ) : (
                subscriptions.map((sub) => (
                  <tr key={sub.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4 font-bold text-white text-sm">{sub.user.username}</td>
                    <td className="p-4 text-zinc-300">
                      <div className="font-semibold text-white">{sub.plan.name}</div>
                      <div className="text-zinc-500 text-[10px] mt-0.5">{sub.plan.billing_interval}</div>
                    </td>
                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${statusBadgeClass(sub.status)}`}>
                        {sub.status}
                      </span>
                    </td>
                    <td className="p-4 text-zinc-400">
                      {sub.effective_access_until ? new Date(sub.effective_access_until).toLocaleString() : <span className="text-zinc-600 italic">None</span>}
                    </td>
                    <td className="p-4 text-zinc-400">{new Date(sub.created_at).toLocaleString()}</td>
                    <td className="p-4">
                      <div className="flex items-center justify-center">
                        {!TERMINAL_STATUSES.has(sub.status) && (
                          <button
                            onClick={() => handleCancelImmediate(sub)}
                            disabled={cancellingId === sub.id}
                            className="p-2 bg-red-500/10 hover:bg-red-500/20 rounded-xl transition-all inline-flex items-center justify-center text-red-400 disabled:opacity-50"
                            title="Cancel Immediately"
                          >
                            <Ban className="w-4 h-4" />
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
            Showing Page <span className="font-semibold text-white">{page}</span> of{" "}
            <span className="font-semibold text-white">{totalPages}</span> ({totalCount} total entries)
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
