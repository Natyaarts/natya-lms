"use client";

import { useEffect, useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { CheckCircle2, AlertCircle, Loader2, Play, LayoutDashboard, ArrowRight } from "lucide-react";

function PaymentCallbackContent() {
  const searchParams = useSearchParams();
  const courseId = searchParams.get("course_id");
  const purchaseId = searchParams.get("purchase_id");
  const paymentLinkStatus = searchParams.get("razorpay_payment_link_status");

  const [loading, setLoading] = useState(true);
  const [isEnrolled, setIsEnrolled] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    let timer: NodeJS.Timeout;

    const verifyStatus = async () => {
      try {
        const query = new URLSearchParams();
        if (courseId) query.set("course_id", courseId);
        if (purchaseId) query.set("purchase_id", purchaseId);

        const res = await fetch(
          `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/orders/check-status/?${query.toString()}`,
          { credentials: "include" }
        );

        if (res.ok) {
          const data = await res.json();
          if (data.is_enrolled) {
            setIsEnrolled(true);
            setLoading(false);
            return;
          }
        }

        // If not immediately confirmed, retry once after 2 seconds (in case Razorpay webhook is in transit)
        timer = setTimeout(async () => {
          try {
            const retryRes = await fetch(
              `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/orders/check-status/?${query.toString()}`,
              { credentials: "include" }
            );
            if (retryRes.ok) {
              const retryData = await retryRes.json();
              if (retryData.is_enrolled) {
                setIsEnrolled(true);
              } else {
                setErrorMsg(retryData.message || "Payment is still pending confirmation.");
              }
            } else {
              setErrorMsg("Payment verification could not be completed.");
            }
          } catch {
            setErrorMsg("Could not connect to payment verification server.");
          } finally {
            setLoading(false);
          }
        }, 2000);
      } catch (err: any) {
        console.error("Verification error:", err);
        setErrorMsg("Failed to verify payment status.");
        setLoading(false);
      }
    };

    verifyStatus();

    return () => {
      if (timer) clearTimeout(timer);
    };
  }, [courseId, purchaseId]);

  return (
    <div className="min-h-screen bg-black text-white flex items-center justify-center p-6 selection:bg-[#facc15] selection:text-black">
      <div className="relative w-full max-w-lg">
        {/* Ambient glow */}
        <div className="absolute -inset-1 bg-gradient-to-r from-[#facc15]/20 to-yellow-600/20 rounded-3xl blur-2xl pointer-events-none" />

        <div className="relative bg-[#0d0d0d] border border-white/10 rounded-3xl p-8 md:p-10 text-center shadow-2xl">
          {loading ? (
            <div className="py-12 space-y-4">
              <Loader2 className="w-12 h-12 text-[#facc15] animate-spin mx-auto" />
              <h2 className="text-xl font-bold">Verifying Razorpay Payment...</h2>
              <p className="text-sm text-zinc-400">
                Please wait while we confirm your payment and unlock your course curriculum.
              </p>
            </div>
          ) : isEnrolled ? (
            <div className="space-y-6">
              <div className="w-16 h-16 rounded-full bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 flex items-center justify-center mx-auto">
                <CheckCircle2 className="w-9 h-9" />
              </div>

              <div>
                <span className="inline-block px-3 py-1 rounded-full bg-emerald-500/15 text-emerald-400 text-xs font-semibold tracking-wide uppercase mb-3">
                  Payment Confirmed
                </span>
                <h1 className="text-3xl font-extrabold tracking-tight">Course Unlocked!</h1>
                <p className="text-sm text-zinc-400 mt-2">
                  Your Razorpay payment was successful. Full course access, video lessons, and curriculum are now active.
                </p>
              </div>

              <div className="pt-4 space-y-3">
                {courseId && (
                  <Link
                    href={`/courses/${courseId}/learn`}
                    className="w-full flex items-center justify-center gap-2 py-3.5 px-6 rounded-xl bg-[#facc15] text-black font-bold text-sm hover:bg-yellow-400 transition-all shadow-lg hover:shadow-[#facc15]/20"
                  >
                    <Play className="w-4 h-4 fill-black" />
                    Start Learning Now
                  </Link>
                )}

                <Link
                  href="/dashboard"
                  className="w-full flex items-center justify-center gap-2 py-3 px-6 rounded-xl bg-white/5 hover:bg-white/10 text-white font-medium text-sm transition-all border border-white/10"
                >
                  <LayoutDashboard className="w-4 h-4 text-zinc-400" />
                  Go to Student Dashboard
                </Link>

                <p className="text-xs text-zinc-500 pt-2">
                  Using the mobile app? You can return to the Natya LMS app anytime—your course is unlocked there too!
                </p>
              </div>
            </div>
          ) : (
            <div className="space-y-6">
              <div className="w-16 h-16 rounded-full bg-amber-500/10 border border-amber-500/30 text-amber-400 flex items-center justify-center mx-auto">
                <AlertCircle className="w-9 h-9" />
              </div>

              <div>
                <span className="inline-block px-3 py-1 rounded-full bg-amber-500/15 text-amber-400 text-xs font-semibold tracking-wide uppercase mb-3">
                  Confirmation Pending
                </span>
                <h1 className="text-2xl font-bold tracking-tight">Payment Verification Pending</h1>
                <p className="text-sm text-zinc-400 mt-2">
                  {errorMsg ||
                    "We have not yet received confirmation from Razorpay. Courses remain locked until payment is fully confirmed."}
                </p>
              </div>

              <div className="pt-4 space-y-3">
                <button
                  onClick={() => window.location.reload()}
                  className="w-full flex items-center justify-center gap-2 py-3.5 px-6 rounded-xl bg-white/10 hover:bg-white/15 text-white font-semibold text-sm transition-all border border-white/10"
                >
                  Re-check Payment Status
                </button>

                {courseId && (
                  <Link
                    href={`/courses/${courseId}`}
                    className="w-full flex items-center justify-center gap-2 py-3 px-6 rounded-xl text-zinc-400 hover:text-white font-medium text-sm transition-all"
                  >
                    Return to Course Page
                    <ArrowRight className="w-4 h-4" />
                  </Link>
                )}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default function PaymentCallbackPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-black text-white flex items-center justify-center">
          <Loader2 className="w-8 h-8 text-[#facc15] animate-spin" />
        </div>
      }
    >
      <PaymentCallbackContent />
    </Suspense>
  );
}
