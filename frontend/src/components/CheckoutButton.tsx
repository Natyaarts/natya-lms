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
        const res = await fetch("http://localhost:8000/api/courses/my_courses/", {
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

  const handlePayment = async () => {
    setLoading(true);
    try {
      // 1. Create order on the backend
      const res = await fetch("http://localhost:8000/api/orders/create/", {
        method: "POST",
        credentials: "include", // Send auth cookies
        headers: {
          "Content-Type": "application/json",
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
          const verifyRes = await fetch("http://localhost:8000/api/orders/verify/", {
            method: "POST",
            credentials: "include",
            headers: {
              "Content-Type": "application/json",
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
