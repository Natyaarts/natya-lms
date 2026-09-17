"use client";

import { useState, useEffect } from "react";
import Script from "next/script";

interface CheckoutButtonProps {
  courseId: number;
  price: string;
}

export default function CheckoutButton({ courseId, price }: CheckoutButtonProps) {
  const [loading, setLoading] = useState(false);
  const [isEnrolled, setIsEnrolled] = useState(false);
  const [checking, setChecking] = useState(true);
  
  useEffect(() => {
    const checkEnrollment = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/my_courses/`, {
          credentials: "include"
        });
        if (res.ok) {
          const courses = await res.json();
          setIsEnrolled(courses.some((c: any) => c.id === courseId));
        }
      } catch (err) {
        console.error("Failed to check enrollment", err);
      } finally {
        setChecking(false);
      }
    };
    checkEnrollment();
  }, [courseId]);

  // CSRF hardening fix: CreateOrderView/VerifyPaymentView now enforce CSRF
  // for cookie-authenticated (browser) requests -- see
  // backend/orders/views.py's CSRFEnforcedJWTCookieAuthentication. Same
  // read-the-cookie-send-as-header pattern already used elsewhere in this
  // frontend (e.g. courses/[id]/learn/page.tsx's own getCsrfToken).
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

  // The csrftoken cookie is only ever set as a side effect of GET
  // api/users/me/ (see backend/users/views.py's CurrentUserView,
  // @ensure_csrf_cookie) -- guarantee it exists before the POST below
  // needs to echo it back, rather than assuming some earlier page in this
  // session already triggered it.
  const ensureCsrfCookie = async () => {
    if (getCsrfToken()) return;
    await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/me/`, { credentials: "include" });
  };

  const handlePayment = async () => {
    setLoading(true);
    try {
      await ensureCsrfCookie();
      // 1. Create order on the backend
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/orders/create-order/`, {
        method: "POST",
        credentials: "include", // Send auth cookies
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify({ course_id: courseId }),
      });

      const orderData = await res.json();

      if (!res.ok) {
        alert(orderData.error || "Failed to create order.");
        setLoading(false);
        return;
      }

      // 2. Configure Razorpay options
      const options = {
        key: orderData.key_id,
        amount: orderData.amount,
        currency: orderData.currency,
        name: "Natya LMS",
        description: "Course Purchase",
        order_id: orderData.order_id,
        handler: async function (response: any) {
          // 3. Verify payment on the backend
          const verifyRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/orders/verify-payment/`, {
            method: "POST",
            credentials: "include",
            headers: {
              "Content-Type": "application/json",
              "X-CSRFToken": getCsrfToken(),
            },
            body: JSON.stringify({
              razorpay_payment_id: response.razorpay_payment_id,
              razorpay_order_id: response.razorpay_order_id,
              razorpay_signature: response.razorpay_signature,
            }),
          });

          const verifyData = await verifyRes.json();
          if (verifyRes.ok) {
            alert("Payment successful! Welcome to the course.");
            window.location.href = "/dashboard";
          } else {
            alert(verifyData.error || "Payment verification failed.");
          }
        },
        prefill: {
          name: "Student",
        },
        theme: {
          color: "#facc15", // The yellow brand color
        },
      };

      // 4. Open Razorpay Modal
      const rzp = new (window as any).Razorpay(options);
      
      rzp.on("payment.failed", function (response: any) {
        alert("Payment failed. Please try again.");
      });

      rzp.open();
    } catch (error) {
      console.error("Payment Error:", error);
      alert("Something went wrong initializing the payment.");
    } finally {
      setLoading(false);
    }
  };

  if (checking) {
    return (
      <div className="w-full mt-8 py-4 bg-zinc-800 rounded-2xl animate-pulse text-transparent">
        Loading...
      </div>
    );
  }

  if (isEnrolled) {
    return (
      <a 
        href="/dashboard"
        className="block text-center w-full mt-8 py-4 bg-gradient-to-r from-green-500 to-emerald-600 text-black text-lg font-bold rounded-2xl shadow-lg hover:scale-[1.02] transition-all"
      >
        Go to Course Dashboard
      </a>
    );
  }

  return (
    <>
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="lazyOnload" />
      <button
        onClick={handlePayment}
        disabled={loading}
        className="w-full mt-8 py-4 bg-gradient-to-r from-[#facc15] to-[#a16207] text-black text-lg font-bold rounded-2xl shadow-lg hover:shadow-[#facc15]/20 hover:scale-[1.02] transition-all disabled:opacity-70 disabled:cursor-not-allowed"
      >
        {loading ? "Processing..." : `Enroll Now for ₹${price}`}
      </button>
    </>
  );
}
