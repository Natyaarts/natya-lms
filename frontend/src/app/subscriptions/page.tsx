"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import SubscriptionCheckoutButton from "@/components/SubscriptionCheckoutButton";

// Phase 3.4.8: the dedicated student subscription experience anticipated
// (but explicitly deferred) by Phase 3.4.5's own comment on
// MySubscriptionSection in app/orders/page.tsx ("there is no dedicated
// subscription dashboard yet (deliberately out of scope for this phase)").
// This page is that dashboard -- plan catalog, my subscription, grace
// period, cancellation, and payment history, all reusing the Phase
// 3.4.2/3.4.6 APIs and the existing SubscriptionCheckoutButton unchanged.
// No new payment implementation, no client-side access-until math, no
// Razorpay ids ever touch this file (the serializers already never send
// them).
//
// MySubscriptionSection on /orders (Phase 3.4.5) is intentionally left as
// -- is a minimal, self-contained reused component, not duplicated logic;
// removing it from /orders is out of scope here since the brief for this
// phase is additive ("build the student-facing subscription experience"),
// not a refactor of the existing Orders page.

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const BILLING_LABEL: Record<string, string> = {
  MONTHLY: "Monthly",
  YEARLY: "Yearly",
};

const STATUS_STYLE: Record<string, string> = {
  ACTIVE: "bg-green-500/10 text-green-400 border border-green-500/20",
  PENDING: "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20",
  HALTED: "bg-yellow-500/10 text-yellow-400 border border-yellow-500/20",
  CREATED: "bg-zinc-700/50 text-zinc-300 border border-white/10",
  AUTHENTICATED: "bg-zinc-700/50 text-zinc-300 border border-white/10",
  PAUSED: "bg-zinc-700/50 text-zinc-300 border border-white/10",
};

const PAYMENT_STATUS_STYLE: Record<string, string> = {
  SUCCESS: "bg-green-500/10 text-green-400 border border-green-500/20",
  CREATED: "bg-zinc-700/50 text-zinc-300 border border-white/10",
  FAILED: "bg-red-500/10 text-red-400 border border-red-500/20",
  REFUNDED: "bg-zinc-800 text-zinc-500 border border-white/10",
};

// PENDING/HALTED with access_until still in the future is exactly the
// Phase 3.4.5 grace-period state -- displayed here, never recomputed.
const GRACE_STATUSES = new Set(["PENDING", "HALTED"]);

function formatDate(value: string | null) {
  if (!value) return null;
  return new Date(value).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}

function Nav() {
  return (
    <nav className="border-b border-white/10 bg-black/50 backdrop-blur-md fixed top-0 w-full z-50">
      <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
        <Link href="/" className="flex items-center">
          <Image src="/img/logo.png" alt="Natya LMS Logo" width={140} height={40} className="object-contain" />
        </Link>
        <div className="flex gap-4">
          <Link href="/courses" className="text-sm font-medium hover:text-[#facc15] transition-colors">Courses</Link>
          <Link href="/bundles" className="text-sm font-medium hover:text-[#facc15] transition-colors">Bundles</Link>
          <Link href="/orders" className="text-sm font-medium hover:text-[#facc15] transition-colors">Orders</Link>
          <Link href="/dashboard" className="text-sm font-medium hover:text-[#facc15] transition-colors">Dashboard</Link>
        </div>
      </div>
    </nav>
  );
}

export default function SubscriptionsPage() {
  const [plans, setPlans] = useState<any[]>([]);
  const [plansLoading, setPlansLoading] = useState(true);
  const [plansError, setPlansError] = useState("");

  const [subscription, setSubscription] = useState<any>(null);
  const [subLoading, setSubLoading] = useState(true);
  const [subError, setSubError] = useState("");
  const [signedIn, setSignedIn] = useState(true);

  const [payments, setPayments] = useState<any[]>([]);
  const [paymentsLoading, setPaymentsLoading] = useState(true);
  const [paymentsError, setPaymentsError] = useState("");

  const [cancelling, setCancelling] = useState(false);
  const [cancelMessage, setCancelMessage] = useState("");

  // Deliberately no synchronous setState at the top of this function --
  // it's called directly from the mount effect below, and setting state
  // synchronously inside an effect body (even indirectly through a called
  // function) triggers an extra cascading render. The initial subLoading/
  // subError values from useState already cover the "about to fetch"
  // state; everything here only sets state inside the fetch's own async
  // continuations, exactly like the plans/payments fetches alongside it.
  const loadSubscription = () => {
    fetch(`${API}/api/orders/subscriptions/me/`, { credentials: "include" })
      .then(async (res) => {
        if (res.status === 401 || res.status === 403) {
          setSignedIn(false);
          setSubscription(null);
          return;
        }
        setSignedIn(true);
        if (res.status === 404) {
          setSubscription(null);
          return;
        }
        if (!res.ok) {
          setSubError("Failed to load your subscription.");
          return;
        }
        setSubscription(await res.json());
      })
      .catch(() => setSubError("Network error loading your subscription."))
      .finally(() => setSubLoading(false));
  };

  useEffect(() => {
    fetch(`${API}/api/orders/subscription-plans/`)
      .then(async (res) => {
        if (!res.ok) {
          setPlansError("Failed to load subscription plans.");
          return;
        }
        const data = await res.json();
        setPlans(Array.isArray(data) ? data : data.results || []);
      })
      .catch(() => setPlansError("Network error loading subscription plans."))
      .finally(() => setPlansLoading(false));

    loadSubscription();

    fetch(`${API}/api/orders/subscriptions/payments/`, { credentials: "include" })
      .then(async (res) => {
        if (res.status === 401 || res.status === 403) {
          setPayments([]);
          return;
        }
        if (!res.ok) {
          setPaymentsError("Failed to load payment history.");
          return;
        }
        const data = await res.json();
        setPayments(Array.isArray(data) ? data : data.results || []);
      })
      .catch(() => setPaymentsError("Network error loading payment history."))
      .finally(() => setPaymentsLoading(false));
  }, []);

  // CSRF hardening fix: CancelSubscriptionView now enforces CSRF for
  // cookie-authenticated (browser) requests -- see backend/orders/views.py's
  // CSRFEnforcedJWTCookieAuthentication. The csrftoken cookie this reads is
  // already guaranteed present by the time a student can even see the
  // Cancel button: loadSubscription() above (GET subscriptions/me/, which
  // now carries @ensure_csrf_cookie) always runs first on mount and must
  // succeed with an active subscription before this button renders at all.
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

  const handleCancel = async () => {
    if (!window.confirm("Cancel your subscription? You'll keep access until the end of your current billing period.")) {
      return;
    }
    setCancelling(true);
    setCancelMessage("");
    try {
      const res = await fetch(`${API}/api/orders/subscriptions/cancel/`, {
        method: "POST",
        credentials: "include",
        headers: { "X-CSRFToken": getCsrfToken() },
      });
      const data = await res.json();
      if (res.ok) {
        setSubscription(data);
        setCancelMessage("Your subscription is scheduled to end at the close of the current billing period. You'll keep full access until then.");
      } else {
        setCancelMessage(data.error || "Something went wrong cancelling your subscription.");
      }
    } catch {
      setCancelMessage("Network error. Please try again.");
    } finally {
      setCancelling(false);
    }
  };

  const effectiveAccessUntil = subscription ? formatDate(subscription.effective_access_until) : null;
  const cancelledAt = subscription ? formatDate(subscription.cancelled_at) : null;
  const inGrace = subscription && GRACE_STATUSES.has(subscription.status) && subscription.effective_access_until;
  const hasLiveSubscription = Boolean(subscription);

  return (
    <div className="min-h-screen bg-black text-white font-sans selection:bg-[#facc15] selection:text-black pb-24">
      <Nav />

      <div className="pt-32 pb-12 px-6">
        <div className="max-w-5xl mx-auto">
          <h1 className="text-4xl md:text-5xl font-bold mb-4">Subscriptions</h1>
          <p className="text-zinc-400 text-lg">Unlock every recorded masterclass with a single plan.</p>
        </div>
      </div>

      <div className="px-6">
        <div className="max-w-5xl mx-auto space-y-14">

          {/* My Subscription */}
          <section>
            <h2 className="text-xs text-zinc-500 uppercase tracking-wide mb-4">My Subscription</h2>

            {subLoading ? (
              <div className="text-center py-10 bg-zinc-900/30 border border-white/10 rounded-2xl text-zinc-500 text-sm">Loading...</div>
            ) : !signedIn ? (
              <div className="text-center py-10 bg-zinc-900/30 border border-white/10 rounded-2xl">
                <p className="text-zinc-400">Sign in to view and manage your subscription.</p>
              </div>
            ) : subError ? (
              <div className="text-center py-10 bg-zinc-900/30 border border-white/10 rounded-2xl text-zinc-400">{subError}</div>
            ) : !subscription ? (
              <div className="text-center py-10 bg-zinc-900/30 border border-white/10 rounded-2xl">
                <p className="text-zinc-400">You don&apos;t have an active subscription yet. Choose a plan below to get started.</p>
              </div>
            ) : (
              <div className="bg-[#0a0a0a] border border-white/10 rounded-2xl p-6">
                <div className="flex items-center justify-between mb-3 flex-wrap gap-2">
                  <div>
                    <div className="text-lg font-semibold">{subscription.plan.name}</div>
                    <div className="text-xs text-zinc-500 mt-1">
                      {BILLING_LABEL[subscription.plan.billing_interval] || subscription.plan.billing_interval} &middot; ₹{parseFloat(subscription.plan.price).toLocaleString()} {subscription.plan.currency}
                    </div>
                  </div>
                  <span className={`px-2.5 py-1 text-[10px] font-bold rounded ${STATUS_STYLE[subscription.status] || "bg-zinc-800 text-zinc-500 border border-white/10"}`}>
                    {subscription.status}
                  </span>
                </div>

                {inGrace && (
                  <div className="mb-4 px-4 py-3 bg-yellow-500/10 border border-yellow-500/20 rounded-xl text-sm text-yellow-300">
                    We had trouble processing your last payment. Your access remains available until{" "}
                    <strong>{effectiveAccessUntil}</strong> while we retry -- no action needed unless it lapses.
                  </div>
                )}

                {!inGrace && effectiveAccessUntil && (
                  <p className="text-sm text-zinc-400 mb-4">
                    {subscription.cancel_at_period_end
                      ? `Your subscription is scheduled to end on ${effectiveAccessUntil}. You'll keep full access until then.`
                      : `Your access is valid through ${effectiveAccessUntil}.`}
                  </p>
                )}

                {subscription.cancel_at_period_end && cancelledAt && (
                  <p className="text-xs text-zinc-600 mb-4">Cancelled on {cancelledAt}.</p>
                )}

                {cancelMessage && (
                  <div className="mb-4 px-3 py-2 bg-white/5 border border-white/10 rounded-xl text-xs text-zinc-300">{cancelMessage}</div>
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
            )}
          </section>

          {/* Plan Catalog */}
          <section>
            <h2 className="text-xs text-zinc-500 uppercase tracking-wide mb-4">Plans</h2>

            {plansLoading ? (
              <div className="text-center py-10 bg-zinc-900/30 border border-white/10 rounded-2xl text-zinc-500 text-sm">Loading...</div>
            ) : plansError ? (
              <div className="text-center py-10 bg-zinc-900/30 border border-white/10 rounded-2xl text-zinc-400">{plansError}</div>
            ) : plans.length === 0 ? (
              <div className="text-center py-10 bg-zinc-900/30 border border-white/10 rounded-2xl">
                <h3 className="text-zinc-300">No subscription plans available right now.</h3>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {plans.map((plan: any) => {
                  const isCurrentPlan = hasLiveSubscription && subscription.plan.id === plan.id;
                  return (
                    <div key={plan.id} className="bg-[#0a0a0a] border border-white/10 rounded-3xl p-6 flex flex-col">
                      <div className="flex items-center gap-2 mb-3">
                        <span className="w-2 h-2 rounded-full bg-[#facc15]"></span>
                        <span className="text-xs font-medium text-zinc-400 uppercase tracking-wider">
                          {BILLING_LABEL[plan.billing_interval] || plan.billing_interval}
                        </span>
                        {isCurrentPlan && (
                          <span className="text-xs font-medium text-[#facc15] uppercase tracking-wider ml-auto">Current Plan</span>
                        )}
                      </div>
                      <h3 className="text-2xl font-bold mb-2">{plan.name}</h3>
                      {plan.description && <p className="text-zinc-400 text-sm mb-4">{plan.description}</p>}
                      <div className="text-3xl font-bold mb-4">
                        ₹{parseFloat(plan.price).toLocaleString()}
                        <span className="text-sm font-normal text-zinc-500"> {plan.currency} / {plan.billing_interval === "YEARLY" ? "year" : "month"}</span>
                      </div>

                      {plan.courses && plan.courses.length > 0 && (
                        <ul className="space-y-1 mb-4 flex-grow">
                          {plan.courses.map((c: any) => (
                            <li key={c.id} className="text-sm text-zinc-400 flex items-center gap-2">
                              <span className="text-[#facc15]">&#10003;</span> {c.title}
                            </li>
                          ))}
                        </ul>
                      )}

                      {isCurrentPlan ? (
                        <div className="mt-auto pt-4 text-sm text-zinc-500 text-center">You&apos;re subscribed to this plan.</div>
                      ) : hasLiveSubscription ? (
                        <div className="mt-auto pt-4 text-sm text-zinc-500 text-center">You already have an active subscription.</div>
                      ) : (
                        <div className="mt-auto">
                          <SubscriptionCheckoutButton planId={plan.id} planName={plan.name} price={plan.price} />
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          {/* Payment History */}
          <section>
            <h2 className="text-xs text-zinc-500 uppercase tracking-wide mb-4">Payment History</h2>

            {paymentsLoading ? (
              <div className="text-center py-10 bg-zinc-900/30 border border-white/10 rounded-2xl text-zinc-500 text-sm">Loading...</div>
            ) : paymentsError ? (
              <div className="text-center py-10 bg-zinc-900/30 border border-white/10 rounded-2xl text-zinc-400">{paymentsError}</div>
            ) : payments.length === 0 ? (
              <div className="text-center py-10 bg-zinc-900/30 border border-white/10 rounded-2xl">
                <h3 className="text-zinc-300">No payment history yet.</h3>
              </div>
            ) : (
              <div className="space-y-3">
                {payments.map((payment: any) => (
                  <div key={payment.id} className="bg-[#0a0a0a] border border-white/10 rounded-2xl p-5 flex items-center justify-between flex-wrap gap-3">
                    <div>
                      <div className="font-medium">{payment.plan_name}</div>
                      <div className="text-xs text-zinc-500 mt-1">
                        {payment.paid_at ? `Paid ${formatDate(payment.paid_at)}` : `Created ${formatDate(payment.created_at)}`}
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className={`px-2.5 py-1 text-[10px] font-bold rounded ${PAYMENT_STATUS_STYLE[payment.status] || "bg-zinc-800 text-zinc-500 border border-white/10"}`}>
                        {payment.status}
                      </span>
                      <span className="text-lg font-bold">₹{parseFloat(payment.amount).toLocaleString()} <span className="text-xs text-zinc-500 font-normal">{payment.currency}</span></span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

        </div>
      </div>
    </div>
  );
}
