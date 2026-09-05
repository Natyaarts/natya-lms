"use client";

import { useState, useEffect } from "react";
import Script from "next/script";

// Phase 3.3: mirrors CheckoutButton.tsx's exact flow (create -> Razorpay
// modal -> verify -> redirect), just against the new Order endpoints
// instead of the legacy Purchase ones. Deliberately not merged with
// CheckoutButton -- the two payment paths are intentionally parallel, not
// unified, per the Phase 3 architecture (Purchase stays untouched).

interface BundleCheckoutButtonProps {
  bundleId: number;
  price: string;
  isPurchasable: boolean;
}

export default function BundleCheckoutButton({ bundleId, price, isPurchasable }: BundleCheckoutButtonProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [isOwned, setIsOwned] = useState(false);
  const [checking, setChecking] = useState(true);

  const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

  useEffect(() => {
    const checkOwnership = async () => {
      try {
        const res = await fetch(`${API}/api/orders/orders/`, { credentials: "include" });
        if (res.ok) {
          const orders = await res.json();
          const list = Array.isArray(orders) ? orders : orders.results || [];
          const owned = list.some(
            (o: any) => o.status === "PAID" && o.items?.some((i: any) => i.item_type === "BUNDLE" && i.bundle === bundleId)
          );
          setIsOwned(owned);
        }
      } catch (err) {
        console.error("Failed to check bundle ownership", err);
      } finally {
        setChecking(false);
      }
    };
    checkOwnership();
  }, [bundleId]);

  const handlePayment = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API}/api/orders/orders/`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: [{ bundle_id: bundleId }] }),
      });
      const orderData = await res.json();

      if (!res.ok) {
        setError(orderData.error || "Failed to create order.");
        setLoading(false);
        return;
      }

      const options = {
        key: orderData.razorpay.key_id,
        amount: orderData.razorpay.amount,
        currency: orderData.razorpay.currency,
        name: "Natya LMS",
        description: "Bundle Purchase",
        order_id: orderData.razorpay.order_id,
        handler: async function (response: any) {
          const verifyRes = await fetch(`${API}/api/orders/orders/${orderData.id}/verify/`, {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature,
            }),
          });
          const verifyData = await verifyRes.json();
          if (verifyRes.ok) {
            window.location.href = "/dashboard";
          } else {
            setError(verifyData.error || "Payment verification failed.");
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
      console.error("Bundle checkout error:", err);
      setError("Something went wrong initializing the payment.");
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return <div className="w-full mt-8 py-4 bg-zinc-800 rounded-2xl animate-pulse text-transparent">Loading...</div>;
  }

  if (isOwned) {
    return (
      <a
        href="/dashboard"
        className="block text-center w-full mt-8 py-4 bg-gradient-to-r from-green-500 to-emerald-600 text-black text-lg font-bold rounded-2xl shadow-lg hover:scale-[1.02] transition-all"
      >
        Go to Dashboard
      </a>
    );
  }

  if (!isPurchasable) {
    return (
      <div className="w-full mt-8 py-4 bg-zinc-800 text-zinc-500 text-center text-lg font-bold rounded-2xl cursor-not-allowed">
        Coming Soon
      </div>
    );
  }

  return (
    <>
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="lazyOnload" />
      {error && (
        <div className="mt-4 px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-xs">{error}</div>
      )}
      <button
        onClick={handlePayment}
        disabled={loading}
        className="w-full mt-8 py-4 bg-gradient-to-r from-[#facc15] to-[#a16207] text-black text-lg font-bold rounded-2xl shadow-lg hover:shadow-[#facc15]/20 hover:scale-[1.02] transition-all disabled:opacity-70 disabled:cursor-not-allowed"
      >
        {loading ? "Processing..." : `Buy Bundle for ₹${price}`}
      </button>
    </>
  );
}
