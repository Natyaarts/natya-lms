"use client";

import { useEffect, useState, useCallback } from "react";
import Script from "next/script";
import Link from "next/link";
import {
  CreditCard,
  AlertTriangle,
  AlertCircle,
  CheckCircle2,
  Clock,
  Calendar,
  ShieldCheck,
  FileText,
  Printer,
  ChevronRight,
  RefreshCw,
  ExternalLink,
  X,
  BookOpen,
  Info
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";

interface CourseDetails {
  id: number;
  title: string;
  price: number | string;
  thumbnail?: string;
  course_type?: string;
}

interface ActiveExtension {
  plan_id: number;
  course_id: number;
  course_title: string;
  extended_until: string;
  reason: string;
}

interface GraceCourseAlert {
  plan_id: number;
  course_id: number;
  course_title: string;
  grace_until: string;
  days_left: number;
  amount: string;
  invoice_id?: number | null;
  invoice_number?: string | null;
  restriction_date?: string | null;
}

interface RestrictedCourseAlert {
  plan_id: number;
  course_id: number;
  course_title: string;
  amount: string;
  invoice_id?: number | null;
  invoice_number?: string | null;
  due_date?: string | null;
}

interface BillingAlerts {
  has_restricted_access: boolean;
  restricted_courses: RestrictedCourseAlert[];
  has_grace_warning: boolean;
  grace_courses: GraceCourseAlert[];
  active_extensions: ActiveExtension[];
  total_outstanding_amount: string;
  unpaid_invoices_count: number;
}

interface StudentBillingPlan {
  id: number;
  course: number;
  course_details: CourseDetails;
  billing_type: string;
  amount: string;
  currency: string;
  billing_interval_value?: number | null;
  billing_interval_unit?: string | null;
  start_date: string;
  current_period_start: string;
  current_period_end: string;
  next_billing_date?: string | null;
  due_date?: string | null;
  grace_until?: string | null;
  access_restriction_date?: string | null;
  status: "ACTIVE" | "UPCOMING" | "DUE" | "GRACE_PERIOD" | "OVERDUE" | "RESTRICTED" | "PAUSED" | "CANCELLED" | "COMPLETED";
  pending_billing_type?: string | null;
  pending_amount?: string | null;
  pending_effective_date?: string | null;
  is_active: boolean;
}

interface StudentInvoice {
  id: number;
  invoice_number: string;
  course: number;
  course_details: CourseDetails;
  period_start: string;
  period_end: string;
  issue_date: string;
  due_date: string;
  amount: string;
  currency: string;
  status: "DRAFT" | "ISSUED" | "PENDING" | "PAID" | "OVERDUE" | "CANCELLED" | "WAIVED";
  payment_method?: string | null;
  paid_at?: string | null;
  razorpay_payment_id?: string | null;
  notes?: string;
}

interface ReceiptData {
  invoice_number: string;
  issue_date: string;
  due_date: string;
  paid_at?: string | null;
  amount: string;
  currency: string;
  status: string;
  payment_method?: string | null;
  razorpay_payment_id?: string | null;
  student_details: {
    username: string;
    email: string;
    full_name?: string;
  };
  course_details: CourseDetails;
  billing_plan_details: {
    billing_type: string;
    billing_type_display: string;
  };
  period_start: string;
  period_end: string;
  academy_details: {
    name: string;
    legal_name: string;
    address: string;
    gstin?: string;
    pan?: string;
    email: string;
    phone: string;
    website: string;
    is_tax_profile_configured?: boolean;
  };
}

export default function StudentBillingPortalPage() {
  const { user, loading: authLoading } = useAuth();
  const [plans, setPlans] = useState<StudentBillingPlan[]>([]);
  const [alerts, setAlerts] = useState<BillingAlerts | null>(null);
  const [invoices, setInvoices] = useState<StudentInvoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [invoiceFilter, setInvoiceFilter] = useState<"ALL" | "UNPAID" | "PAID">("ALL");
  const [payingInvoiceId, setPayingInvoiceId] = useState<number | null>(null);
  const [receiptModal, setReceiptModal] = useState<ReceiptData | null>(null);
  const [receiptLoading, setReceiptLoading] = useState(false);
  const [successBanner, setSuccessBanner] = useState<string | null>(null);
  const [errorBanner, setErrorBanner] = useState<string | null>(null);

  const getCsrfToken = () => {
    if (typeof document === "undefined") return "";
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  };

  const fetchBillingData = useCallback(async () => {
    setLoading(true);
    setErrorBanner(null);
    try {
      const rawApiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const apiUrl = rawApiUrl.replace(/\/+$/, "");

      // 1. Fetch plans & alerts (no-store prevents browser cache sharing)
      const plansRes = await fetch(`${apiUrl}/api/billing/my-billing/`, {
        credentials: "include",
        cache: "no-store",
        headers: { "Content-Type": "application/json" }
      });
      if (plansRes.ok) {
        const plansData = await plansRes.json();
        setPlans(plansData.plans || []);
        setAlerts(plansData.alerts || null);
      }

      // 2. Fetch invoices (no-store prevents cross-user cache leaks)
      const invRes = await fetch(`${apiUrl}/api/billing/my-billing/invoices/`, {
        credentials: "include",
        cache: "no-store",
        headers: { "Content-Type": "application/json" }
      });
      if (invRes.ok) {
        const invData = await invRes.json();
        setInvoices(Array.isArray(invData) ? invData : (invData.results || []));
      }
    } catch (err: any) {
      setErrorBanner("Failed to load billing records. Please check your connection.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!authLoading && !user) {
      window.location.href = "/login";
      return;
    }
    if (user) {
      fetchBillingData();
    }
  }, [user, authLoading, fetchBillingData]);

  // Handle Razorpay Checkout
  const handlePayInvoice = async (invoiceId: number) => {
    setPayingInvoiceId(invoiceId);
    setErrorBanner(null);
    setSuccessBanner(null);

    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

    try {
      const res = await fetch(`${apiUrl}/api/billing/my-billing/invoices/${invoiceId}/pay/`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        }
      });

      const orderData = await res.json();
      if (!res.ok) {
        setErrorBanner(orderData.error || "Unable to initiate payment gateway.");
        setPayingInvoiceId(null);
        return;
      }

      // Launch Razorpay Modal
      const options = {
        key: orderData.razorpay_key_id,
        amount: orderData.amount_in_paise,
        currency: orderData.currency || "INR",
        name: "Natya Arts Academy",
        description: `Fee Payment - ${orderData.course_title}`,
        order_id: orderData.razorpay_order_id,
        prefill: {
          name: orderData.student_name,
          email: orderData.student_email,
          contact: orderData.student_phone
        },
        theme: {
          color: "#facc15" // Brand Gold
        },
        handler: async function (response: any) {
          // Verify on backend
          try {
            const verifyRes = await fetch(`${apiUrl}/api/billing/my-billing/invoices/${invoiceId}/verify/`, {
              method: "POST",
              credentials: "include",
              headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": getCsrfToken()
              },
              body: JSON.stringify({
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_order_id: response.razorpay_order_id,
                razorpay_signature: response.razorpay_signature
              })
            });

            const verifyData = await verifyRes.json();
            if (verifyRes.ok) {
              setSuccessBanner("Payment confirmed! Your course access is fully active.");
              fetchBillingData();
            } else {
              setErrorBanner(verifyData.error || "Payment verification failed.");
            }
          } catch (e: any) {
            setErrorBanner("Network error during payment verification.");
          } finally {
            setPayingInvoiceId(null);
          }
        },
        modal: {
          ondismiss: function () {
            setPayingInvoiceId(null);
          }
        }
      };

      const rzp = new (window as any).Razorpay(options);
      rzp.on("payment.failed", function (failResp: any) {
        setErrorBanner(`Payment failed: ${failResp.error?.description || "Transaction cancelled"}`);
        setPayingInvoiceId(null);
      });
      rzp.open();
    } catch (err: any) {
      setErrorBanner("An error occurred starting payment checkout.");
      setPayingInvoiceId(null);
    }
  };

  // Open Official Printable Receipt
  const handleViewReceipt = async (invoiceId: number) => {
    setReceiptLoading(true);
    const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    try {
      const res = await fetch(`${apiUrl}/api/billing/my-billing/invoices/${invoiceId}/receipt/`, {
        credentials: "include",
        cache: "no-store",
        headers: { "Content-Type": "application/json" }
      });
      if (res.ok) {
        const data = await res.json();
        setReceiptModal(data);
      } else {
        alert("Unable to fetch invoice receipt.");
      }
    } catch (e) {
      alert("Error loading receipt details.");
    } finally {
      setReceiptLoading(false);
    }
  };

  const filteredInvoices = invoices.filter((inv) => {
    if (invoiceFilter === "PAID") return inv.status === "PAID";
    if (invoiceFilter === "UNPAID") return ["ISSUED", "PENDING", "OVERDUE", "DRAFT"].includes(inv.status);
    return true;
  });

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "ACTIVE":
      case "PAID":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-emerald-500/15 text-emerald-400 border border-emerald-500/30"><CheckCircle2 className="w-3.5 h-3.5" /> Paid</span>;
      case "DUE":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-blue-500/15 text-blue-400 border border-blue-500/30"><Clock className="w-3.5 h-3.5" /> Due Today</span>;
      case "GRACE_PERIOD":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/15 text-amber-400 border border-amber-500/30"><AlertTriangle className="w-3.5 h-3.5" /> Grace Period</span>;
      case "RESTRICTED":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-rose-500/15 text-rose-400 border border-rose-500/30"><AlertCircle className="w-3.5 h-3.5" /> Restricted</span>;
      case "OVERDUE":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-rose-500/15 text-rose-400 border border-rose-500/30"><AlertCircle className="w-3.5 h-3.5" /> Overdue</span>;
      case "PAUSED":
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-zinc-500/15 text-zinc-400 border border-zinc-500/30">Paused</span>;
      default:
        return <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-zinc-500/15 text-zinc-300 border border-white/10">{status}</span>;
    }
  };

  const formatBillingType = (type: string, val?: number | null, unit?: string | null) => {
    switch (type) {
      case "MONTHLY": return "Monthly";
      case "EVERY_2_MONTHS": return "Every 2 Months";
      case "EVERY_3_MONTHS": return "Quarterly (3 Months)";
      case "EVERY_6_MONTHS": return "Semi-Annual (6 Months)";
      case "YEARLY": return "Annual (1 Year)";
      case "ONE_TIME": return "One-Time Payment";
      case "CUSTOM": return `Every ${val || 1} ${unit || "cycles"}`;
      default: return type;
    }
  };

  return (
    <>
      <Script src="https://checkout.razorpay.com/v1/checkout.js" strategy="lazyOnload" />

      <div className="min-h-screen bg-[#0a0a0a] text-zinc-100 py-10 px-4 sm:px-6 lg:px-8 font-sans">
        <div className="max-w-6xl mx-auto space-y-8">

          {/* Header */}
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-white/10 pb-6">
            <div>
              <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-widest text-[#facc15] mb-1">
                <CreditCard className="w-4 h-4" />
                <span>Student Billing & Fee Portal</span>
              </div>
              <h1 className="text-3xl font-extrabold tracking-tight text-white">Course Fees & Invoices</h1>
              <p className="text-sm text-zinc-400 mt-1">
                Monitor your fee schedule, pay pending renewals securely via Razorpay, and download official receipts.
              </p>
            </div>

            <button
              onClick={fetchBillingData}
              disabled={loading}
              className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-white/5 border border-white/10 text-xs font-medium text-zinc-300 hover:bg-white/10 hover:text-white transition-all self-start sm:self-auto"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
          </div>

          {/* Toast / Global Notifications */}
          {successBanner && (
            <div className="flex items-center justify-between p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-sm">
              <div className="flex items-center gap-3">
                <CheckCircle2 className="w-5 h-5 text-emerald-400 flex-shrink-0" />
                <span>{successBanner}</span>
              </div>
              <button onClick={() => setSuccessBanner(null)} className="text-emerald-400 hover:text-emerald-200">
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {errorBanner && (
            <div className="flex items-center justify-between p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-300 text-sm">
              <div className="flex items-center gap-3">
                <AlertCircle className="w-5 h-5 text-rose-400 flex-shrink-0" />
                <span>{errorBanner}</span>
              </div>
              <button onClick={() => setErrorBanner(null)} className="text-rose-400 hover:text-rose-200">
                <X className="w-4 h-4" />
              </button>
            </div>
          )}

          {/* 1. URGENCY ALERT BANNERS */}
          {alerts?.has_restricted_access && alerts.restricted_courses.length > 0 && (
            <div className="space-y-3">
              {alerts.restricted_courses.map((c) => (
                <div
                  key={c.course_id}
                  className="p-5 rounded-2xl bg-gradient-to-r from-rose-950/40 via-rose-900/20 to-black border border-rose-500/40 shadow-xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4"
                >
                  <div className="flex items-start gap-3.5">
                    <div className="p-2.5 rounded-xl bg-rose-500/20 border border-rose-500/30 text-rose-400 flex-shrink-0 mt-0.5">
                      <AlertCircle className="w-6 h-6" />
                    </div>
                    <div>
                      <div className="text-xs font-bold uppercase tracking-wider text-rose-400">Content Access Restricted</div>
                      <h3 className="text-base font-bold text-white mt-0.5">
                        Course access for <span className="text-rose-200">{c.course_title}</span> is temporarily suspended.
                      </h3>
                      <p className="text-xs text-zinc-400 mt-1">
                        Outstanding fee: <span className="font-semibold text-white">₹{c.amount}</span>. Settle payment now to immediately unlock all video lessons and assignments.
                      </p>
                    </div>
                  </div>

                  {c.invoice_id && (
                    <button
                      onClick={() => handlePayInvoice(c.invoice_id!)}
                      disabled={payingInvoiceId === c.invoice_id}
                      className="px-5 py-2.5 rounded-xl bg-gradient-to-r from-[#facc15] to-amber-500 text-black font-bold text-xs uppercase tracking-wider shadow-lg hover:brightness-110 active:scale-95 transition-all flex items-center gap-2 flex-shrink-0"
                    >
                      {payingInvoiceId === c.invoice_id ? "Processing..." : `Pay ₹${c.amount} to Restore`}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {alerts?.has_grace_warning && alerts.grace_courses.length > 0 && (
            <div className="space-y-3">
              {alerts.grace_courses.map((g) => (
                <div
                  key={g.course_id}
                  className="p-5 rounded-2xl bg-gradient-to-r from-amber-950/40 via-amber-900/20 to-black border border-amber-500/40 shadow-xl flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4"
                >
                  <div className="flex items-start gap-3.5">
                    <div className="p-2.5 rounded-xl bg-amber-500/20 border border-amber-500/30 text-amber-400 flex-shrink-0 mt-0.5">
                      <AlertTriangle className="w-6 h-6" />
                    </div>
                    <div>
                      <div className="text-xs font-bold uppercase tracking-wider text-amber-400">
                        Grace Period Active &bull; {g.days_left} {g.days_left === 1 ? "day" : "days"} remaining
                      </div>
                      <h3 className="text-base font-bold text-white mt-0.5">
                        Your fee payment of ₹{g.amount} for <span className="text-amber-200">{g.course_title}</span> is overdue.
                      </h3>
                      <p className="text-xs text-zinc-400 mt-1">
                        You retain full course access through <span className="text-white font-semibold">{g.grace_until}</span>. Access will be suspended if payment is not completed before restriction date.
                      </p>
                    </div>
                  </div>

                  {g.invoice_id && (
                    <button
                      onClick={() => handlePayInvoice(g.invoice_id!)}
                      disabled={payingInvoiceId === g.invoice_id}
                      className="px-5 py-2.5 rounded-xl bg-[#facc15] text-black font-bold text-xs uppercase tracking-wider shadow-lg hover:brightness-110 active:scale-95 transition-all flex items-center gap-2 flex-shrink-0"
                    >
                      {payingInvoiceId === g.invoice_id ? "Processing..." : `Pay ₹${g.amount} Now`}
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}

          {/* Active Access Extension Override Notices */}
          {alerts?.active_extensions && alerts.active_extensions.length > 0 && (
            <div className="space-y-2">
              {alerts.active_extensions.map((ext) => (
                <div
                  key={ext.plan_id}
                  className="p-4 rounded-xl bg-sky-950/30 border border-sky-500/30 text-sky-200 text-xs flex items-center gap-3"
                >
                  <ShieldCheck className="w-5 h-5 text-sky-400 flex-shrink-0" />
                  <div>
                    <span className="font-semibold text-white">Access Extension Active for {ext.course_title}:</span> Content access is guaranteed through{" "}
                    <span className="font-bold text-sky-300">{ext.extended_until}</span>. Reason: {ext.reason}
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* 2. ACTIVE BILLING PLANS */}
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-[#facc15]" />
                Enrolled Course Billing Plans
              </h2>
              <span className="text-xs text-zinc-500">{plans.length} configured {plans.length === 1 ? "plan" : "plans"}</span>
            </div>

            {loading ? (
              <div className="p-8 text-center text-zinc-500 text-sm">Loading billing plans...</div>
            ) : plans.length === 0 ? (
              <div className="p-8 rounded-2xl bg-[#111] border border-white/5 text-center space-y-2">
                <p className="text-sm text-zinc-400">No active billing arrangements found for your account.</p>
                <p className="text-xs text-zinc-600">Standard course purchases with lifetime access do not require recurring fee plans.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {plans.map((plan) => (
                  <div
                    key={plan.id}
                    className="p-5 rounded-2xl bg-[#121212] border border-white/10 hover:border-white/20 transition-all flex flex-col justify-between space-y-4"
                  >
                    <div>
                      <div className="flex items-start justify-between gap-3">
                        <div>
                          <h3 className="font-bold text-white text-base leading-snug">{plan.course_details?.title}</h3>
                          <div className="text-xs text-zinc-400 mt-1">
                            Frequency: <span className="text-zinc-200 font-medium">{formatBillingType(plan.billing_type, plan.billing_interval_value, plan.billing_interval_unit)}</span>
                          </div>
                        </div>
                        {getStatusBadge(plan.status)}
                      </div>

                      {/* Fee & Period Grid */}
                      <div className="grid grid-cols-2 gap-3 mt-4 p-3 rounded-xl bg-white/[0.03] border border-white/5 text-xs">
                        <div>
                          <span className="text-zinc-500 block">Fee Amount:</span>
                          <span className="font-bold text-white text-sm">₹{plan.amount}</span>
                        </div>
                        <div>
                          <span className="text-zinc-500 block">Current Cycle:</span>
                          <span className="text-zinc-300">{plan.current_period_start} &rarr; {plan.current_period_end}</span>
                        </div>
                        {plan.next_billing_date && (
                          <div>
                            <span className="text-zinc-500 block">Next Renewal:</span>
                            <span className="text-zinc-300 font-medium">{plan.next_billing_date}</span>
                          </div>
                        )}
                        {plan.grace_until && plan.status === "GRACE_PERIOD" && (
                          <div>
                            <span className="text-amber-500 block">Grace Ends:</span>
                            <span className="text-amber-300 font-bold">{plan.grace_until}</span>
                          </div>
                        )}
                      </div>

                      {/* Pending Next-Cycle Plan Change Banner */}
                      {plan.pending_billing_type && plan.pending_effective_date && (
                        <div className="mt-3 p-2.5 rounded-lg bg-yellow-500/10 border border-yellow-500/20 text-xs text-yellow-300 flex items-center gap-2">
                          <Info className="w-3.5 h-3.5 flex-shrink-0" />
                          <span>
                            Scheduled upgrade: <b>{formatBillingType(plan.pending_billing_type)}</b> (₹{plan.pending_amount}) starting {plan.pending_effective_date}.
                          </span>
                        </div>
                      )}
                    </div>

                    <div className="pt-2 border-t border-white/5 flex items-center justify-between">
                      <Link
                        href={`/courses/${plan.course}/learn`}
                        className="text-xs font-semibold text-[#facc15] hover:underline flex items-center gap-1"
                      >
                        Go to Course Classroom <ChevronRight className="w-3.5 h-3.5" />
                      </Link>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* 3. INVOICES & PAYMENT HISTORY */}
          <div className="space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
              <h2 className="text-lg font-bold text-white flex items-center gap-2">
                <FileText className="w-4 h-4 text-[#facc15]" />
                Fee Invoices & Payment History
              </h2>

              <div className="flex items-center gap-1.5 p-1 rounded-xl bg-white/5 border border-white/10 self-start sm:self-auto text-xs">
                <button
                  onClick={() => setInvoiceFilter("ALL")}
                  className={`px-3 py-1 rounded-lg font-medium transition-all ${invoiceFilter === "ALL" ? "bg-[#facc15] text-black font-bold" : "text-zinc-400 hover:text-white"}`}
                >
                  All ({invoices.length})
                </button>
                <button
                  onClick={() => setInvoiceFilter("UNPAID")}
                  className={`px-3 py-1 rounded-lg font-medium transition-all ${invoiceFilter === "UNPAID" ? "bg-[#facc15] text-black font-bold" : "text-zinc-400 hover:text-white"}`}
                >
                  Unpaid ({invoices.filter((i) => ["ISSUED", "PENDING", "OVERDUE", "DRAFT"].includes(i.status)).length})
                </button>
                <button
                  onClick={() => setInvoiceFilter("PAID")}
                  className={`px-3 py-1 rounded-lg font-medium transition-all ${invoiceFilter === "PAID" ? "bg-[#facc15] text-black font-bold" : "text-zinc-400 hover:text-white"}`}
                >
                  Paid ({invoices.filter((i) => i.status === "PAID").length})
                </button>
              </div>
            </div>

            {loading ? (
              <div className="p-8 text-center text-zinc-500 text-sm">Loading invoices...</div>
            ) : filteredInvoices.length === 0 ? (
              <div className="p-8 rounded-2xl bg-[#111] border border-white/5 text-center text-sm text-zinc-500">
                No invoices found under the selected filter.
              </div>
            ) : (
              <div className="overflow-x-auto rounded-2xl border border-white/10 bg-[#111]">
                <table className="w-full text-left text-sm text-zinc-300">
                  <thead className="bg-white/5 text-xs uppercase font-semibold text-zinc-400 tracking-wider border-b border-white/10">
                    <tr>
                      <th className="py-3 px-4">Invoice #</th>
                      <th className="py-3 px-4">Course</th>
                      <th className="py-3 px-4">Coverage Period</th>
                      <th className="py-3 px-4">Due Date</th>
                      <th className="py-3 px-4">Amount</th>
                      <th className="py-3 px-4">Status</th>
                      <th className="py-3 px-4 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 text-xs">
                    {filteredInvoices.map((inv) => {
                      const isUnpaid = ["ISSUED", "PENDING", "OVERDUE", "DRAFT"].includes(inv.status);
                      const isPaid = inv.status === "PAID";

                      return (
                        <tr key={inv.id} className="hover:bg-white/[0.02] transition-colors">
                          <td className="py-3.5 px-4 font-mono font-bold text-white">{inv.invoice_number}</td>
                          <td className="py-3.5 px-4 font-medium text-zinc-200">{inv.course_details?.title}</td>
                          <td className="py-3.5 px-4 text-zinc-400">{inv.period_start} &rarr; {inv.period_end}</td>
                          <td className="py-3.5 px-4 text-zinc-300">{inv.due_date}</td>
                          <td className="py-3.5 px-4 font-bold text-white text-sm">₹{inv.amount}</td>
                          <td className="py-3.5 px-4">{getStatusBadge(inv.status)}</td>
                          <td className="py-3.5 px-4 text-right">
                            {isUnpaid && (
                              <button
                                onClick={() => handlePayInvoice(inv.id)}
                                disabled={payingInvoiceId === inv.id}
                                className="px-3.5 py-1.5 rounded-lg bg-[#facc15] text-black font-bold text-xs uppercase tracking-wider hover:brightness-110 active:scale-95 transition-all inline-flex items-center gap-1.5"
                              >
                                {payingInvoiceId === inv.id ? "Opening..." : "Pay Now"}
                              </button>
                            )}

                            {isPaid && (
                              <button
                                onClick={() => handleViewReceipt(inv.id)}
                                disabled={receiptLoading}
                                className="px-3 py-1.5 rounded-lg bg-white/5 border border-white/10 text-zinc-300 hover:text-white hover:bg-white/10 font-medium text-xs transition-all inline-flex items-center gap-1.5"
                              >
                                <Printer className="w-3.5 h-3.5 text-[#facc15]" />
                                Receipt
                              </button>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </div>

        </div>
      </div>

      {/* 4. OFFICIAL PRINTABLE RECEIPT MODAL */}
      {receiptModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/80 backdrop-blur-sm overflow-y-auto">
          <div className="relative w-full max-w-2xl bg-white text-black rounded-2xl shadow-2xl p-6 sm:p-8 my-8 print:p-0 print:m-0 print:shadow-none print:w-full">

            {/* Modal Controls (Hidden in Print) */}
            <div className="flex items-center justify-between border-b pb-4 mb-6 print:hidden">
              <span className="text-xs uppercase tracking-widest font-bold text-zinc-500">Official Payment Receipt</span>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => window.print()}
                  className="px-4 py-2 rounded-xl bg-zinc-900 text-white font-semibold text-xs hover:bg-zinc-800 transition-colors flex items-center gap-2"
                >
                  <Printer className="w-4 h-4 text-[#facc15]" /> Print / Save PDF
                </button>
                <button
                  onClick={() => setReceiptModal(null)}
                  className="p-2 rounded-xl hover:bg-zinc-100 text-zinc-600 transition-colors"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>
            </div>

            {/* Printable Receipt Body */}
            <div className="space-y-6 text-sm">
              {/* Academy Header */}
              <div className="flex justify-between items-start border-b pb-6">
                <div>
                  <h2 className="text-2xl font-black tracking-tight text-zinc-900">{receiptModal.academy_details.name}</h2>
                  <p className="text-xs text-zinc-500 mt-1">{receiptModal.academy_details.legal_name}</p>
                  <p className="text-xs text-zinc-500 max-w-xs mt-0.5">{receiptModal.academy_details.address}</p>
                  <div className="text-xs text-zinc-600 mt-2 space-y-0.5">
                    {receiptModal.academy_details.is_tax_profile_configured ? (
                      <>
                        {receiptModal.academy_details.gstin && <div><b>GSTIN:</b> {receiptModal.academy_details.gstin}</div>}
                        {receiptModal.academy_details.pan && <div><b>PAN:</b> {receiptModal.academy_details.pan}</div>}
                      </>
                    ) : (
                      <div className="text-[11px] text-zinc-500 italic">
                        Official Tax &amp; GSTIN Registration on Record
                      </div>
                    )}
                  </div>
                </div>

                <div className="text-right">
                  <div className="inline-block px-3 py-1 rounded bg-emerald-100 text-emerald-800 font-bold text-xs uppercase tracking-wider mb-2">
                    PAID RECEIPT
                  </div>
                  <div className="text-xs text-zinc-500">Invoice Number:</div>
                  <div className="font-mono font-bold text-zinc-900 text-base">{receiptModal.invoice_number}</div>
                  <div className="text-xs text-zinc-500 mt-1">Paid At: {receiptModal.paid_at ? new Date(receiptModal.paid_at).toLocaleString() : receiptModal.issue_date}</div>
                </div>
              </div>

              {/* Student & Course Details */}
              <div className="grid grid-cols-2 gap-4 py-2">
                <div>
                  <div className="text-xs uppercase font-bold text-zinc-400">Billed To (Student):</div>
                  <div className="font-bold text-zinc-900 text-sm mt-0.5">{receiptModal.student_details?.full_name || receiptModal.student_details?.username}</div>
                  <div className="text-xs text-zinc-600">{receiptModal.student_details?.email}</div>
                </div>

                <div>
                  <div className="text-xs uppercase font-bold text-zinc-400">Payment Details:</div>
                  <div className="text-xs text-zinc-800 mt-0.5"><b>Method:</b> {receiptModal.payment_method || "Razorpay Online"}</div>
                  {receiptModal.razorpay_payment_id && (
                    <div className="text-xs text-zinc-600"><b>Ref ID:</b> {receiptModal.razorpay_payment_id}</div>
                  )}
                </div>
              </div>

              {/* Fee Line Item */}
              <div className="border rounded-xl overflow-hidden mt-4">
                <table className="w-full text-left text-xs">
                  <thead className="bg-zinc-100 text-zinc-700 font-bold border-b">
                    <tr>
                      <th className="py-2.5 px-4">Description</th>
                      <th className="py-2.5 px-4">Period</th>
                      <th className="py-2.5 px-4 text-right">Amount</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-100">
                    <tr>
                      <td className="py-3 px-4 font-semibold text-zinc-900">
                        {receiptModal.course_details?.title}
                        <span className="block text-[11px] font-normal text-zinc-500">
                          {receiptModal.billing_plan_details?.billing_type_display} Fee
                        </span>
                      </td>
                      <td className="py-3 px-4 text-zinc-600">
                        {receiptModal.period_start} to {receiptModal.period_end}
                      </td>
                      <td className="py-3 px-4 text-right font-bold text-zinc-900">
                        ₹{receiptModal.amount}
                      </td>
                    </tr>
                  </tbody>
                  <tfoot className="bg-zinc-50 font-bold border-t">
                    <tr>
                      <td colSpan={2} className="py-3 px-4 text-right text-zinc-700">Total Paid:</td>
                      <td className="py-3 px-4 text-right text-sm text-zinc-950 font-black">₹{receiptModal.amount}</td>
                    </tr>
                  </tfoot>
                </table>
              </div>

              {/* Footer Note */}
              <div className="border-t pt-4 text-[11px] text-zinc-500 text-center">
                This is a computer-generated official receipt for course fee payment at Natya Arts Academy. Valid for educational reimbursement and income tax claims.
              </div>
            </div>

          </div>
        </div>
      )}
    </>
  );
}
