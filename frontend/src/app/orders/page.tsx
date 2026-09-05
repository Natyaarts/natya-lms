"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";

// Phase 3.3: minimal student order history -- "H. Student ability to view
// purchased orders/bundles" from the business goals. Client component
// (unlike the bundle catalog pages) because GET /api/orders/orders/ is
// scoped to the logged-in user's own orders (IsAuthenticated + get_queryset
// filtering to request.user), so it needs the browser's session cookie --
// the same reason CheckoutButton.tsx is a client component.

const STATUS_STYLE: Record<string, string> = {
  PENDING: "bg-zinc-700/50 text-zinc-300 border border-white/10",
  PAID: "bg-green-500/10 text-green-400 border border-green-500/20",
  FAILED: "bg-red-500/10 text-red-400 border border-red-500/20",
  CANCELLED: "bg-zinc-800 text-zinc-500 border border-white/10",
};

// Phase 3.4.5: minimal subscription state + cancellation, added to this
// existing page rather than a new route -- "My Orders" is already this
// student's one purchase-history page, and there is no dedicated
// subscription dashboard yet (deliberately out of scope for this phase).
const SUB_STATUS_STYLE: Record<string, string> = {
  ACTIVE: "bg-green-500/10 text-green-400 border border-green-500/20",
  PENDING: "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20",
  HALTED: "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20",
  AUTHENTICATED: "bg-zinc-700/50 text-zinc-300 border border-white/10",
  PAUSED: "bg-zinc-700/50 text-zinc-300 border border-white/10",
};

function MySubscriptionSection() {
  const [subscription, setSubscription] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [cancelling, setCancelling] = useState(false);
  const [message, setMessage] = useState("");
  const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  useEffect(() => {
    fetch(`${API}/api/orders/subscriptions/me/`, { credentials: "include" })
      .then(async (res) => {
        if (res.ok) setSubscription(await res.json());
      })
      .finally(() => setLoading(false));
  }, []);

  const handleCancel = async () => {
    setCancelling(true);
    setMessage("");
    try {
      const res = await fetch(`${API}/api/orders/subscriptions/cancel/`, {
        method: "POST",
        credentials: "include",
      });
      const data = await res.json();
      if (res.ok) {
        setSubscription(data);
        setMessage("Your subscription will end at the close of the current billing period. You'll keep full access until then.");
      } else {
        setMessage(data.error || "Something went wrong cancelling your subscription.");
      }
    } catch {
      setMessage("Network error. Please try again.");
    } finally {
      setCancelling(false);
    }
  };

  if (loading || !subscription) return null;

  const accessUntil = subscription.effective_access_until
    ? new Date(subscription.effective_access_until).toLocaleDateString()
    : null;

  return (
    <div className="mb-10 bg-[#0a0a0a] border border-white/10 rounded-2xl p-6">
      <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
        <div>
          <div className="text-xs text-zinc-500 uppercase tracking-wide mb-1">My Subscription</div>
          <div className="text-lg font-semibold">{subscription.plan.name}</div>
        </div>
        <span className={`px-2.5 py-1 text-[10px] font-bold rounded ${SUB_STATUS_STYLE[subscription.status] || "bg-zinc-800 text-zinc-500 border border-white/10"}`}>
          {subscription.status}
        </span>
      </div>

      {accessUntil && (
        <p className="text-sm text-zinc-400 mb-4">
          {subscription.cancel_at_period_end
            ? `Your subscription is scheduled to end on ${accessUntil}. You'll keep access until then.`
            : `Your access is valid through ${accessUntil}.`}
        </p>
      )}

      {message && (
        <div className="mb-4 px-3 py-2 bg-white/5 border border-white/10 rounded-xl text-xs text-zinc-300">{message}</div>
      )}

      {!subscription.cancel_at_period_end && (
        <button
          onClick={handleCancel}
          disabled={cancelling}
          className="text-sm font-medium text-red-400 hover:text-red-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
        >
          {cancelling ? "Cancelling..." : "Cancel subscription"}
        </button>
      )}
    </div>
  );
}

export default function MyOrdersPage() {
  const [orders, setOrders] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    fetch(`${API}/api/orders/orders/`, { credentials: "include" })
      .then(async (res) => {
        if (!res.ok) {
          setError(res.status === 401 || res.status === 403 ? "Please sign in to view your orders." : "Failed to load orders.");
          return;
        }
        const data = await res.json();
        setOrders(Array.isArray(data) ? data : data.results || []);
      })
      .catch(() => setError("Network error loading orders."))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen bg-black text-white font-sans selection:bg-[#facc15] selection:text-black pb-24">
      <nav className="border-b border-white/10 bg-black/50 backdrop-blur-md fixed top-0 w-full z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <Link href="/" className="flex items-center">
            <Image src="/img/logo.png" alt="Natya LMS Logo" width={140} height={40} className="object-contain" />
          </Link>
          <div className="flex gap-4">
            <Link href="/bundles" className="text-sm font-medium hover:text-[#facc15] transition-colors">Bundles</Link>
            <Link href="/dashboard" className="text-sm font-medium hover:text-[#facc15] transition-colors">Dashboard</Link>
          </div>
        </div>
      </nav>

      <div className="pt-32 px-6 max-w-4xl mx-auto">
        <MySubscriptionSection />

        <h1 className="text-4xl font-bold mb-8">My Orders</h1>

        {loading ? (
          <div className="text-center py-20 text-zinc-500 text-sm">Loading...</div>
        ) : error ? (
          <div className="text-center py-20 bg-zinc-900/30 border border-white/10 rounded-2xl text-zinc-400">{error}</div>
        ) : orders.length === 0 ? (
          <div className="text-center py-20 bg-zinc-900/30 border border-white/10 rounded-2xl">
            <h3 className="text-xl font-medium text-zinc-300 mb-4">No bundle orders yet.</h3>
            <Link href="/bundles" className="text-[#facc15] font-medium hover:underline">Browse bundles</Link>
          </div>
        ) : (
          <div className="space-y-4">
            {orders.map((order: any) => (
              <div key={order.id} className="bg-[#0a0a0a] border border-white/10 rounded-2xl p-6">
                <div className="flex items-center justify-between mb-4 flex-wrap gap-2">
                  <div>
                    <div className="text-xs text-zinc-500 uppercase tracking-wide mb-1">{order.order_number}</div>
                    <div className="text-sm text-zinc-400">{new Date(order.created_at).toLocaleString()}</div>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className={`px-2.5 py-1 text-[10px] font-bold rounded ${STATUS_STYLE[order.status] || ""}`}>{order.status}</span>
                    <span className="text-xl font-bold">₹{parseFloat(order.total_amount).toLocaleString()}</span>
                  </div>
                </div>
                <div className="space-y-1 border-t border-white/5 pt-4">
                  {order.items.map((item: any) => (
                    <div key={item.id} className="flex items-center justify-between text-sm text-zinc-300">
                      <span>{item.title_snapshot} <span className="text-zinc-600">({item.item_type === "BUNDLE" ? "Bundle" : "Course"})</span></span>
                      <span className="text-zinc-500">₹{parseFloat(item.unit_price).toLocaleString()}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
