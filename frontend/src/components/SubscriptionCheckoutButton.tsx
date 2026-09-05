"use client";

import { useState } from "react";
import Script from "next/script";

// Phase 3.4.2: mirrors CheckoutButton.tsx/BundleCheckoutButton.tsx's exact
// flow (create -> Razorpay modal -> verify -> redirect), against the new
// subscription endpoints. Razorpay's Subscription Checkout takes a
// subscription_id instead of order_id, and no amount/currency -- those are
// derived from the Plan already attached to the subscription on Razorpay's
// side.
//
// Deliberately just the checkout mechanism, not a plan-browsing page --
// there is no plan-listing API yet (out of scope for Phase 3.4.2), so this
// component takes the plan's id/name/price as props for whatever page ends
// up rendering a plan card in a later phase. No ownership/"already
// subscribed" check either, for the same reason CheckoutButton's
// enrollment check has no subscription equivalent yet -- there is no
// "my subscription" read API in this phase.

interface SubscriptionCheckoutButtonProps {
  planId: number;
  planName: string;
  price: string;
}

export default function SubscriptionCheckoutButton({ planId, planName, price }: SubscriptionCheckoutButtonProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  const handleSubscribe = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API}/api/orders/subscriptions/create/`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ plan_id: planId }),
      });
      const data = await res.json();

      if (!res.ok) {
        setError(data.error || "Failed to start subscription checkout.");
        setLoading(false);
        return;
      }

      const options = {
        key: data.razorpay_key_id,
        subscription_id: data.subscription_id,
        name: "Natya LMS",
        description: `${planName} Subscription`,
        handler: async function (response: any) {
          const verifyRes = await fetch(`${API}/api/orders/subscriptions/verify/`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_subscription_id: response.razorpay_subscription_id,
              razorpay_signature: response.razorpay_signature,
            }),
          });
          const verifyData = await verifyRes.json();
          if (verifyRes.ok) {
            window.location.href = "/dashboard";
          } else {
            setError(verifyData.error || "Subscription payment verification failed.");
          }
        },
        prefill: { name: "Student" },
        theme: { color: "#facc15" },
      };

      const rzp = new (window as any).Razorpay(options);
      rzp.on("payment.failed", function () {
        setError("Payment failed. Please try again.");
      });
      rzp.open();
    } catch (err) {
      console.error("Subscription checkout error:", err);
      setError("Something went wrong initializing the subscription.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <>
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="lazyOnload" />
      {error && (
        <div className="mt-4 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-xs">{error}</div>
      )}
      <button
        onClick={handleSubscribe}
        disabled={loading}
        className="w-full mt-8 py-4 bg-gradient-to-r from-[#facc15] to-[#a16207] text-black text-lg font-bold rounded-2xl shadow-lg hover:shadow-[#facc15]/20 hover:scale-[1.02] transition-all disabled:opacity-70 disabled:cursor-not-allowed"
      >
        {loading ? "Processing..." : `Subscribe for ₹${price}`}
      </button>
    </>
  );
}
