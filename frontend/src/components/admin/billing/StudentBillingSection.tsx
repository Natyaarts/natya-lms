"use client";

import React, { useState, useEffect } from "react";
import {
  Calendar,
  CreditCard,
  AlertCircle,
  CheckCircle2,
  Clock,
  Lock,
  Plus,
  RefreshCw,
  ShieldAlert,
  Sliders,
  DollarSign,
  Copy,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  FileText
} from "lucide-react";

interface Props {
  studentId: string | number;
  studentName: string;
  studentEmail?: string;
  enrolledCourses: any[];
  legacyPurchases: any[];
  onRefreshData?: () => void;
}

export default function StudentBillingSection({
  studentId,
  studentName,
  studentEmail,
  enrolledCourses,
  legacyPurchases,
  onRefreshData
}: Props) {
  const [plans, setPlans] = useState<any[]>([]);
  const [invoices, setInvoices] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  // Modals state
  const [showCreatePlanModal, setShowCreatePlanModal] = useState(false);
  const [selectedCourseForNewPlan, setSelectedCourseForNewPlan] = useState<any>(null);
  const [showChangePlanModal, setShowChangePlanModal] = useState(false);
  const [activePlanForChange, setActivePlanForChange] = useState<any>(null);
  const [showManualPaymentModal, setShowManualPaymentModal] = useState(false);
  const [selectedInvoiceForPayment, setSelectedInvoiceForPayment] = useState<any>(null);
  const [showExtendAccessModal, setShowExtendAccessModal] = useState(false);
  const [activePlanForExtend, setActivePlanForExtend] = useState<any>(null);

  // Form states
  const [createPlanForm, setCreatePlanForm] = useState({
    course: "",
    billing_type: "MONTHLY",
    amount: "",
    start_date: new Date().toISOString().split("T")[0],
    billing_interval_value: "30",
    billing_interval_unit: "DAYS",
    advance_invoice_days: 7,
    grace_period_days: 7,
    auto_generate_invoice: true,
    auto_restrict_access: true,
    notes: ""
  });

  const [changePlanForm, setChangePlanForm] = useState({
    new_billing_type: "EVERY_3_MONTHS",
    new_amount: "",
    change_mode: "NEXT_CYCLE",
    reason: ""
  });

  const [paymentForm, setPaymentForm] = useState({
    amount: "",
    payment_method: "CASH",
    reference_number: "",
    notes: ""
  });

  const [extendAccessForm, setExtendAccessForm] = useState({
    extended_until: "",
    reason: ""
  });

  const [paymentLinkResult, setPaymentLinkResult] = useState<{
    invoiceId: number;
    orderId?: string;
    url?: string;
  } | null>(null);

  const getCsrfToken = () => {
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
  };

  const fetchBillingData = async () => {
    setLoading(true);
    setErrorMsg("");
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const [plansRes, invoicesRes] = await Promise.all([
        fetch(`${baseUrl}/api/billing/plans/?student=${studentId}`, {
          credentials: "include"
        }),
        fetch(`${baseUrl}/api/billing/invoices/?student=${studentId}`, {
          credentials: "include"
        })
      ]);

      if (plansRes.ok) {
        const plansData = await plansRes.json();
        setPlans(plansData);
      }
      if (invoicesRes.ok) {
        const invoicesData = await invoicesRes.json();
        setInvoices(invoicesData);
      }
    } catch (err: any) {
      console.error("Failed to load student billing data:", err);
      setErrorMsg("Failed to load billing information.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (studentId) {
      fetchBillingData();
    }
  }, [studentId]);

  const handleCreatePlanSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setActionLoading(true);
    setErrorMsg("");
    setSuccessMsg("");

    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const payload: any = {
        student: Number(studentId),
        course: Number(createPlanForm.course),
        billing_type: createPlanForm.billing_type,
        amount: createPlanForm.amount,
        start_date: createPlanForm.start_date,
        advance_invoice_days: Number(createPlanForm.advance_invoice_days) || 7,
        grace_period_days: Number(createPlanForm.grace_period_days) || 7,
        auto_generate_invoice: createPlanForm.auto_generate_invoice,
        auto_restrict_access: createPlanForm.auto_restrict_access,
        notes: createPlanForm.notes,
        issue_initial_invoice: true
      };

      if (createPlanForm.billing_type === "CUSTOM") {
        payload.billing_interval_value = Number(createPlanForm.billing_interval_value);
        payload.billing_interval_unit = createPlanForm.billing_interval_unit;
      }

      const res = await fetch(`${baseUrl}/api/billing/plans/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include",
        body: JSON.stringify(payload)
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || JSON.stringify(data));
      }

      setSuccessMsg("Billing plan created successfully with initial cycle invoice.");
      setShowCreatePlanModal(false);
      fetchBillingData();
      if (onRefreshData) onRefreshData();
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to create billing plan");
    } finally {
      setActionLoading(false);
    }
  };

  const handleChangePlanSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activePlanForChange) return;
    setActionLoading(true);
    setErrorMsg("");
    setSuccessMsg("");

    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${baseUrl}/api/billing/plans/${activePlanForChange.id}/change-plan/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include",
        body: JSON.stringify(changePlanForm)
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || JSON.stringify(data));
      }

      setSuccessMsg(data.message || "Plan change scheduled successfully.");
      setShowChangePlanModal(false);
      fetchBillingData();
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to apply plan change");
    } finally {
      setActionLoading(false);
    }
  };

  const handleManualPaymentSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedInvoiceForPayment) return;
    setActionLoading(true);
    setErrorMsg("");
    setSuccessMsg("");

    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${baseUrl}/api/billing/manual-payments/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include",
        body: JSON.stringify({
          invoice: selectedInvoiceForPayment.id,
          amount: paymentForm.amount,
          payment_method: paymentForm.payment_method,
          reference_number: paymentForm.reference_number,
          notes: paymentForm.notes
        })
      });

      const data = await res.json();
      if (!res.ok) {
        const errorDetail = data.amount || data.detail || JSON.stringify(data);
        throw new Error(errorDetail);
      }

      setSuccessMsg("Payment recorded. Invoice marked PAID and billing cycle advanced.");
      setShowManualPaymentModal(false);
      fetchBillingData();
      if (onRefreshData) onRefreshData();
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to record manual payment");
    } finally {
      setActionLoading(false);
    }
  };

  const handleExtendAccessSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!activePlanForExtend) return;
    setActionLoading(true);
    setErrorMsg("");
    setSuccessMsg("");

    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${baseUrl}/api/billing/plans/${activePlanForExtend.id}/extend-access/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include",
        body: JSON.stringify(extendAccessForm)
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || JSON.stringify(data));
      }

      setSuccessMsg(data.message || "Access extension granted successfully.");
      setShowExtendAccessModal(false);
      fetchBillingData();
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to extend access");
    } finally {
      setActionLoading(false);
    }
  };

  const handlePausePlan = async (planId: number) => {
    if (!confirm("Are you sure you want to pause recurring billing for this plan?")) return;
    setActionLoading(true);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${baseUrl}/api/billing/plans/${planId}/pause/`, {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() },
        credentials: "include"
      });
      if (res.ok) {
        setSuccessMsg("Billing plan paused.");
        fetchBillingData();
      }
    } catch (err: any) {
      setErrorMsg("Failed to pause plan.");
    } finally {
      setActionLoading(false);
    }
  };

  const handleResumePlan = async (planId: number) => {
    setActionLoading(true);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${baseUrl}/api/billing/plans/${planId}/resume/`, {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() },
        credentials: "include"
      });
      if (res.ok) {
        setSuccessMsg("Billing plan resumed.");
        fetchBillingData();
      }
    } catch (err: any) {
      setErrorMsg("Failed to resume plan.");
    } finally {
      setActionLoading(false);
    }
  };

  const handleCancelPlan = async (planId: number) => {
    if (!confirm("Are you sure you want to cancel this billing plan? Past invoices will remain intact.")) return;
    setActionLoading(true);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${baseUrl}/api/billing/plans/${planId}/cancel/`, {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() },
        credentials: "include"
      });
      if (res.ok) {
        setSuccessMsg("Billing plan cancelled.");
        fetchBillingData();
      }
    } catch (err: any) {
      setErrorMsg("Failed to cancel plan.");
    } finally {
      setActionLoading(false);
    }
  };

  const handleGenerateInvoice = async (planId: number) => {
    setActionLoading(true);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${baseUrl}/api/billing/plans/${planId}/generate-invoice/`, {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() },
        credentials: "include"
      });
      const data = await res.json();
      if (res.ok) {
        setSuccessMsg(data.message || "Invoice ready.");
        fetchBillingData();
      }
    } catch (err: any) {
      setErrorMsg("Failed to generate cycle invoice.");
    } finally {
      setActionLoading(false);
    }
  };

  const handleGeneratePaymentLink = async (invoiceId: number) => {
    setActionLoading(true);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const res = await fetch(`${baseUrl}/api/billing/invoices/${invoiceId}/generate-payment-link/`, {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() },
        credentials: "include"
      });
      const data = await res.json();
      if (res.ok) {
        setPaymentLinkResult({
          invoiceId,
          orderId: data.razorpay_order_id,
          url: data.payment_url
        });
        setSuccessMsg("Razorpay order & payment link generated.");
        fetchBillingData();
      } else {
        setErrorMsg(data.error || "Failed to generate payment link.");
      }
    } catch (err: any) {
      setErrorMsg("Error communicating with Razorpay.");
    } finally {
      setActionLoading(false);
    }
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case "ACTIVE":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">ACTIVE</span>;
      case "DUE":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">DUE</span>;
      case "GRACE_PERIOD":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-orange-500/10 text-orange-400 border border-orange-500/20">GRACE PERIOD</span>;
      case "RESTRICTED":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/20">RESTRICTED</span>;
      case "OVERDUE":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/20">OVERDUE</span>;
      case "PAUSED":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-zinc-500/10 text-zinc-400 border border-zinc-500/20">PAUSED</span>;
      case "CANCELLED":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-zinc-600/10 text-zinc-500 border border-zinc-600/20">CANCELLED</span>;
      case "PAID":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">PAID</span>;
      case "ISSUED":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20">ISSUED</span>;
      case "PENDING":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">PENDING</span>;
      default:
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-zinc-500/10 text-zinc-400 border border-white/10">{status}</span>;
    }
  };

  const getBillingTypeLabel = (type: string) => {
    switch (type) {
      case "MONTHLY": return "Monthly";
      case "EVERY_2_MONTHS": return "Every 2 Months";
      case "EVERY_3_MONTHS": return "Every 3 Months";
      case "EVERY_6_MONTHS": return "Every 6 Months";
      case "YEARLY": return "Yearly";
      case "ONE_TIME": return "One Time";
      case "CUSTOM": return "Custom Interval";
      default: return type;
    }
  };

  return (
    <div className="space-y-8">
      {/* Header and Quick Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-white/5">
        <div>
          <h2 className="text-xl font-bold text-white flex items-center gap-2">
            <DollarSign className="w-5 h-5 text-[#facc15]" />
            Student Payment & Dynamic Fee Billing
          </h2>
          <p className="text-zinc-400 text-xs mt-1">
            Manage course fee arrangements, cycle invoicing, manual payments, and access extensions.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setSelectedCourseForNewPlan(null);
              setCreatePlanForm({
                ...createPlanForm,
                course: enrolledCourses.length > 0 ? String(enrolledCourses[0].course_id) : ""
              });
              setShowCreatePlanModal(true);
            }}
            className="flex items-center gap-2 px-4 py-2 bg-[#facc15] hover:bg-yellow-500 text-black text-xs font-bold rounded-xl transition-all shadow-md"
          >
            <Plus className="w-4 h-4" /> Configure Billing Plan
          </button>
          <button
            onClick={fetchBillingData}
            disabled={loading || actionLoading}
            className="p-2 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-xl transition-colors border border-white/5"
            title="Refresh billing data"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>

      {/* Messages */}
      {errorMsg && (
        <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-400 text-xs flex items-center gap-3">
          <AlertCircle className="w-4 h-4 shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}
      {successMsg && (
        <div className="p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-emerald-400 text-xs flex items-center gap-3">
          <CheckCircle2 className="w-4 h-4 shrink-0" />
          <span>{successMsg}</span>
        </div>
      )}

      {/* SECTION 1: Configured Billing Plans */}
      <div className="space-y-4">
        <h3 className="text-sm font-bold text-zinc-300 uppercase tracking-wider">
          Course Fee Plans ({plans.length})
        </h3>

        {loading ? (
          <div className="p-8 text-center text-zinc-500 text-xs">Loading billing plans...</div>
        ) : plans.length === 0 ? (
          <div className="p-8 text-center bg-zinc-950/60 border border-dashed border-white/10 rounded-2xl">
            <p className="text-zinc-400 text-xs mb-3">No active billing arrangements configured for this student yet.</p>
            <button
              onClick={() => {
                setCreatePlanForm({
                  ...createPlanForm,
                  course: enrolledCourses.length > 0 ? String(enrolledCourses[0].course_id) : ""
                });
                setShowCreatePlanModal(true);
              }}
              className="px-4 py-2 bg-zinc-800 hover:bg-zinc-700 text-white text-xs font-semibold rounded-xl border border-white/10 transition-colors"
            >
              + Create First Billing Plan
            </button>
          </div>
        ) : (
          <div className="space-y-6">
            {plans.map((plan: any) => (
              <div
                key={plan.id}
                className="bg-zinc-950 border border-white/10 rounded-2xl p-6 transition-all hover:border-white/20 space-y-6 shadow-xl"
              >
                {/* Plan Header */}
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-white/5">
                  <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-[#facc15]/10 border border-[#facc15]/20 flex items-center justify-center text-[#facc15] font-bold text-sm">
                      ₹
                    </div>
                    <div>
                      <h4 className="font-bold text-white text-base">
                        {plan.course_details?.title || "Course"}
                      </h4>
                      <p className="text-xs text-zinc-400 mt-0.5">
                        Arrangement: <span className="text-[#facc15] font-semibold">{getBillingTypeLabel(plan.billing_type)}</span> • ₹{plan.amount} / cycle
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    {getStatusBadge(plan.status)}
                    {!plan.is_active && (
                      <span className="px-2 py-0.5 text-[10px] font-bold rounded bg-zinc-800 text-zinc-500">INACTIVE</span>
                    )}
                  </div>
                </div>

                {/* Cycle & Restriction Details Grid */}
                <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 text-xs">
                  <div className="bg-white/5 p-3 rounded-xl border border-white/5">
                    <div className="text-[10px] text-zinc-500 uppercase font-semibold">Amount</div>
                    <div className="text-white font-bold text-sm mt-1">₹{plan.amount}</div>
                  </div>
                  <div className="bg-white/5 p-3 rounded-xl border border-white/5">
                    <div className="text-[10px] text-zinc-500 uppercase font-semibold">Start Date</div>
                    <div className="text-zinc-300 font-medium mt-1">{new Date(plan.start_date).toLocaleDateString()}</div>
                  </div>
                  <div className="bg-white/5 p-3 rounded-xl border border-white/5 col-span-2 sm:col-span-1">
                    <div className="text-[10px] text-zinc-500 uppercase font-semibold">Current Period</div>
                    <div className="text-zinc-300 font-medium mt-1">
                      {new Date(plan.current_period_start).toLocaleDateString()} → {new Date(plan.current_period_end).toLocaleDateString()}
                    </div>
                  </div>
                  <div className="bg-white/5 p-3 rounded-xl border border-white/5">
                    <div className="text-[10px] text-zinc-500 uppercase font-semibold">Next Billing</div>
                    <div className="text-zinc-300 font-medium mt-1">
                      {plan.next_billing_date ? new Date(plan.next_billing_date).toLocaleDateString() : "One-Time"}
                    </div>
                  </div>
                  <div className="bg-white/5 p-3 rounded-xl border border-white/5">
                    <div className="text-[10px] text-zinc-500 uppercase font-semibold">Due Date</div>
                    <div className="text-amber-400 font-medium mt-1">{new Date(plan.due_date).toLocaleDateString()}</div>
                  </div>
                  <div className="bg-white/5 p-3 rounded-xl border border-white/5">
                    <div className="text-[10px] text-zinc-500 uppercase font-semibold">Grace Until</div>
                    <div className="text-orange-400 font-medium mt-1">
                      {plan.grace_until ? new Date(plan.grace_until).toLocaleDateString() : "N/A"}
                    </div>
                  </div>
                  <div className="bg-white/5 p-3 rounded-xl border border-white/5">
                    <div className="text-[10px] text-zinc-500 uppercase font-semibold">Access Restriction</div>
                    <div className="text-rose-400 font-semibold mt-1">
                      {plan.access_restriction_date ? new Date(plan.access_restriction_date).toLocaleDateString() : "N/A"}
                    </div>
                  </div>
                </div>

                {/* Overrides / Pending banners */}
                {plan.active_extension && (
                  <div className="p-3 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-xs flex items-center justify-between text-emerald-300">
                    <div className="flex items-center gap-2">
                      <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-400" />
                      <span>
                        <strong>Active Access Override:</strong> Access guaranteed through{" "}
                        <span className="font-bold underline">{new Date(plan.active_extension.extended_until).toLocaleDateString()}</span>{" "}
                        (Reason: {plan.active_extension.reason})
                      </span>
                    </div>
                  </div>
                )}

                {plan.pending_billing_type && (
                  <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-xs flex items-center gap-2 text-amber-300">
                    <Clock className="w-4 h-4 shrink-0 text-amber-400" />
                    <span>
                      <strong>Scheduled Plan Change:</strong> Terms switch to{" "}
                      <span className="font-bold">{getBillingTypeLabel(plan.pending_billing_type)}</span> (₹{plan.pending_amount}) starting on next billing cycle ({plan.pending_effective_date}). Current paid coverage remains completely untouched.
                    </span>
                  </div>
                )}

                {/* Action Buttons Bar */}
                <div className="flex flex-wrap items-center gap-2 pt-2 border-t border-white/5">
                  <button
                    onClick={() => {
                      setActivePlanForChange(plan);
                      setChangePlanForm({
                        new_billing_type: plan.billing_type,
                        new_amount: String(plan.amount),
                        change_mode: "NEXT_CYCLE",
                        reason: ""
                      });
                      setShowChangePlanModal(true);
                    }}
                    className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-semibold rounded-lg transition-colors border border-white/5"
                  >
                    Change Plan
                  </button>

                  <button
                    onClick={() => {
                      setActivePlanForExtend(plan);
                      const defaultExtend = new Date();
                      defaultExtend.setDate(defaultExtend.getDate() + 14);
                      setExtendAccessForm({
                        extended_until: defaultExtend.toISOString().split("T")[0],
                        reason: ""
                      });
                      setShowExtendAccessModal(true);
                    }}
                    className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-semibold rounded-lg transition-colors border border-white/5"
                  >
                    Extend Access
                  </button>

                  <button
                    onClick={() => handleGenerateInvoice(plan.id)}
                    disabled={actionLoading}
                    className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-semibold rounded-lg transition-colors border border-white/5"
                  >
                    Generate Invoice
                  </button>

                  {plan.status === "PAUSED" ? (
                    <button
                      onClick={() => handleResumePlan(plan.id)}
                      disabled={actionLoading}
                      className="px-3 py-1.5 bg-emerald-600/20 hover:bg-emerald-600/30 text-emerald-400 text-xs font-semibold rounded-lg transition-colors border border-emerald-500/20"
                    >
                      Resume Billing
                    </button>
                  ) : (
                    <button
                      onClick={() => handlePausePlan(plan.id)}
                      disabled={actionLoading || plan.status === "CANCELLED"}
                      className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 text-xs font-semibold rounded-lg transition-colors border border-white/5 disabled:opacity-40"
                    >
                      Pause Billing
                    </button>
                  )}

                  {plan.status !== "CANCELLED" && (
                    <button
                      onClick={() => handleCancelPlan(plan.id)}
                      disabled={actionLoading}
                      className="px-3 py-1.5 bg-rose-500/10 hover:bg-rose-500/20 text-rose-400 text-xs font-semibold rounded-lg transition-colors border border-rose-500/20"
                    >
                      Cancel Plan
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* SECTION 2: Invoices List */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="text-sm font-bold text-zinc-300 uppercase tracking-wider">
            Student Invoices ({invoices.length})
          </h3>
        </div>

        <div className="bg-zinc-950 border border-white/10 rounded-2xl overflow-hidden shadow-xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider font-semibold">
                  <th className="p-4">Invoice #</th>
                  <th className="p-4">Course</th>
                  <th className="p-4">Billing Period</th>
                  <th className="p-4">Due Date</th>
                  <th className="p-4">Amount</th>
                  <th className="p-4 text-center">Status</th>
                  <th className="p-4">Payment</th>
                  <th className="p-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-zinc-300">
                {invoices.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="p-12 text-center text-zinc-500">
                      No invoices issued for this student yet.
                    </td>
                  </tr>
                ) : (
                  invoices.map((inv: any) => (
                    <tr key={inv.id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="p-4 font-mono font-bold text-white">{inv.invoice_number}</td>
                      <td className="p-4 font-semibold text-zinc-200">{inv.course_details?.title || "Course"}</td>
                      <td className="p-4 text-zinc-400">
                        {new Date(inv.period_start).toLocaleDateString()} → {new Date(inv.period_end).toLocaleDateString()}
                      </td>
                      <td className="p-4 text-amber-400 font-medium">
                        {new Date(inv.due_date).toLocaleDateString()}
                      </td>
                      <td className="p-4 text-[#facc15] font-bold text-sm">₹{inv.amount}</td>
                      <td className="p-4 text-center">{getStatusBadge(inv.status)}</td>
                      <td className="p-4 text-zinc-400">
                        {inv.status === "PAID" ? (
                          <div className="text-[11px]">
                            <span className="text-emerald-400 font-medium">{inv.payment_method || "Paid"}</span>
                            {inv.paid_at && <div className="text-zinc-500 text-[10px]">{new Date(inv.paid_at).toLocaleDateString()}</div>}
                          </div>
                        ) : (
                          <span className="text-zinc-500 italic">Outstanding</span>
                        )}
                      </td>
                      <td className="p-4 text-right">
                        <div className="flex items-center justify-end gap-2">
                          {inv.status !== "PAID" && inv.status !== "CANCELLED" && (
                            <>
                              <button
                                onClick={() => {
                                  setSelectedInvoiceForPayment(inv);
                                  setPaymentForm({
                                    amount: String(inv.amount),
                                    payment_method: "CASH",
                                    reference_number: "",
                                    notes: ""
                                  });
                                  setShowManualPaymentModal(true);
                                }}
                                className="px-2.5 py-1 bg-[#facc15] hover:bg-yellow-500 text-black text-[11px] font-bold rounded-lg transition-colors"
                              >
                                Record Payment
                              </button>
                              <button
                                onClick={() => handleGeneratePaymentLink(inv.id)}
                                disabled={actionLoading}
                                className="p-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors border border-white/5"
                                title="Generate Razorpay payment link"
                              >
                                <CreditCard className="w-3.5 h-3.5" />
                              </button>
                            </>
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

        {/* Razorpay Link Result Display */}
        {paymentLinkResult && (
          <div className="p-4 bg-zinc-900 border border-[#facc15]/30 rounded-2xl flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div>
              <div className="text-xs text-zinc-400 font-semibold uppercase tracking-wider">Razorpay Gateway Order</div>
              <div className="text-sm font-mono text-[#facc15] font-bold mt-0.5">
                Order ID: {paymentLinkResult.orderId}
              </div>
              {paymentLinkResult.url && (
                <div className="text-xs text-zinc-300 mt-1 flex items-center gap-2">
                  <span>Link: {paymentLinkResult.url}</span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              {paymentLinkResult.url && (
                <a
                  href={paymentLinkResult.url}
                  target="_blank"
                  rel="noreferrer"
                  className="px-3 py-1.5 bg-[#facc15] text-black text-xs font-bold rounded-xl hover:bg-yellow-500 transition-colors flex items-center gap-1.5"
                >
                  <ExternalLink className="w-3.5 h-3.5" /> Open Link
                </a>
              )}
              <button
                onClick={() => setPaymentLinkResult(null)}
                className="px-3 py-1.5 bg-zinc-800 text-zinc-400 hover:text-white text-xs font-semibold rounded-xl"
              >
                Dismiss
              </button>
            </div>
          </div>
        )}
      </div>

      {/* SECTION 3: Legacy Purchases & Checkout Receipts */}
      <div className="space-y-4 pt-4 border-t border-white/5">
        <h3 className="text-sm font-bold text-zinc-400 uppercase tracking-wider">
          Storefront Checkout Purchases ({legacyPurchases.length})
        </h3>
        <p className="text-zinc-500 text-xs">
          Direct storefront one-time purchases and receipts from website catalog checkout.
        </p>

        <div className="bg-zinc-950 border border-white/5 rounded-2xl overflow-hidden">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Course</th>
                <th className="p-4 font-semibold">Purchase Date</th>
                <th className="p-4 font-semibold">Amount Paid</th>
                <th className="p-4 font-semibold">Receipt status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {legacyPurchases.length === 0 ? (
                <tr>
                  <td colSpan={4} className="p-8 text-center text-zinc-500">
                    No storefront checkout records found.
                  </td>
                </tr>
              ) : (
                legacyPurchases.map((purchase: any) => (
                  <tr key={purchase.id}>
                    <td className="p-4 text-white font-bold">{purchase.course_title}</td>
                    <td className="p-4 text-zinc-400">{new Date(purchase.created_at).toLocaleDateString()}</td>
                    <td className="p-4 text-[#facc15] font-bold">₹{purchase.amount}</td>
                    <td className="p-4">
                      <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${purchase.status === "SUCCESS" ? "bg-emerald-500/10 text-emerald-400 border border-emerald-500/20" : "bg-rose-500/10 text-rose-400 border border-rose-500/20"}`}>
                        {purchase.status === "SUCCESS" ? "PAID" : "UNPAID"}
                      </span>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* MODAL 1: Create Billing Plan */}
      {showCreatePlanModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 rounded-2xl max-w-lg w-full p-6 shadow-2xl max-h-[90vh] overflow-y-auto">
            <h3 className="text-lg font-bold text-white mb-1">Configure Student Billing Plan</h3>
            <p className="text-zinc-400 text-xs mb-6">Set up a recurring or one-time fee arrangement for this student.</p>

            <form onSubmit={handleCreatePlanSubmit} className="space-y-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                  Target Course *
                </label>
                <select
                  required
                  value={createPlanForm.course}
                  onChange={(e) => setCreatePlanForm({ ...createPlanForm, course: e.target.value })}
                  className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-sm focus:outline-none focus:border-[#facc15]"
                >
                  <option value="">-- Select Course --</option>
                  {enrolledCourses.map((c: any) => (
                    <option key={c.course_id} value={c.course_id}>
                      {c.title}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                    Billing Frequency *
                  </label>
                  <select
                    value={createPlanForm.billing_type}
                    onChange={(e) => setCreatePlanForm({ ...createPlanForm, billing_type: e.target.value })}
                    className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-sm focus:outline-none focus:border-[#facc15]"
                  >
                    <option value="MONTHLY">Monthly</option>
                    <option value="EVERY_2_MONTHS">Every 2 Months</option>
                    <option value="EVERY_3_MONTHS">Every 3 Months</option>
                    <option value="EVERY_6_MONTHS">Every 6 Months</option>
                    <option value="YEARLY">Yearly</option>
                    <option value="ONE_TIME">One Time</option>
                    <option value="CUSTOM">Custom</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                    Fee Amount (₹) *
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    required
                    placeholder="e.g. 2000.00"
                    value={createPlanForm.amount}
                    onChange={(e) => setCreatePlanForm({ ...createPlanForm, amount: e.target.value })}
                    className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-sm focus:outline-none focus:border-[#facc15]"
                  />
                </div>
              </div>

              {createPlanForm.billing_type === "CUSTOM" && (
                <div className="grid grid-cols-2 gap-3 p-3 bg-white/5 rounded-xl border border-white/5">
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                      Interval Value *
                    </label>
                    <input
                      type="number"
                      min="1"
                      required
                      value={createPlanForm.billing_interval_value}
                      onChange={(e) => setCreatePlanForm({ ...createPlanForm, billing_interval_value: e.target.value })}
                      className="w-full bg-zinc-950 border border-white/10 rounded-xl p-2.5 text-white text-xs"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                      Interval Unit *
                    </label>
                    <select
                      value={createPlanForm.billing_interval_unit}
                      onChange={(e) => setCreatePlanForm({ ...createPlanForm, billing_interval_unit: e.target.value })}
                      className="w-full bg-zinc-950 border border-white/10 rounded-xl p-2.5 text-white text-xs"
                    >
                      <option value="DAYS">Days</option>
                      <option value="WEEKS">Weeks</option>
                      <option value="MONTHS">Months</option>
                      <option value="YEARS">Years</option>
                    </select>
                  </div>
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                    Effective Start Date *
                  </label>
                  <input
                    type="date"
                    required
                    value={createPlanForm.start_date}
                    onChange={(e) => setCreatePlanForm({ ...createPlanForm, start_date: e.target.value })}
                    className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-sm focus:outline-none focus:border-[#facc15]"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                    Grace Period (Days)
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={createPlanForm.grace_period_days}
                    onChange={(e) => setCreatePlanForm({ ...createPlanForm, grace_period_days: Number(e.target.value) })}
                    className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-sm focus:outline-none focus:border-[#facc15]"
                  />
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                  Internal Notes
                </label>
                <textarea
                  rows={2}
                  placeholder="Optional admin notes regarding agreed fee or terms"
                  value={createPlanForm.notes}
                  onChange={(e) => setCreatePlanForm({ ...createPlanForm, notes: e.target.value })}
                  className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-xs focus:outline-none focus:border-[#facc15] resize-none"
                />
              </div>

              <div className="flex justify-end gap-3 pt-4 border-t border-white/5">
                <button
                  type="button"
                  onClick={() => setShowCreatePlanModal(false)}
                  className="px-4 py-2 text-zinc-400 hover:text-white text-xs font-semibold transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  className="px-5 py-2.5 bg-[#facc15] hover:bg-yellow-500 text-black text-xs font-bold rounded-xl transition-all shadow-md disabled:opacity-50"
                >
                  {actionLoading ? "Configuring..." : "Save & Issue Initial Invoice"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 2: Change Plan */}
      {showChangePlanModal && activePlanForChange && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 rounded-2xl max-w-md w-full p-6 shadow-2xl">
            <h3 className="text-lg font-bold text-white mb-1">Modify Billing Arrangement</h3>
            <p className="text-zinc-400 text-xs mb-6">
              Adjust fee frequency or rate. By default takes effect on the next cycle.
            </p>

            <form onSubmit={handleChangePlanSubmit} className="space-y-4">
              <div className="p-3 bg-white/5 rounded-xl border border-white/5 text-xs">
                <div className="text-zinc-500 uppercase font-semibold text-[10px]">Current Plan Terms</div>
                <div className="text-white font-bold mt-1">
                  {getBillingTypeLabel(activePlanForChange.billing_type)} — ₹{activePlanForChange.amount}
                </div>
                <div className="text-zinc-400 text-[11px] mt-0.5">
                  Current period: {activePlanForChange.current_period_start} → {activePlanForChange.current_period_end}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                    New Billing Type *
                  </label>
                  <select
                    value={changePlanForm.new_billing_type}
                    onChange={(e) => setChangePlanForm({ ...changePlanForm, new_billing_type: e.target.value })}
                    className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-xs focus:outline-none focus:border-[#facc15]"
                  >
                    <option value="MONTHLY">Monthly</option>
                    <option value="EVERY_2_MONTHS">Every 2 Months</option>
                    <option value="EVERY_3_MONTHS">Every 3 Months</option>
                    <option value="EVERY_6_MONTHS">Every 6 Months</option>
                    <option value="YEARLY">Yearly</option>
                    <option value="ONE_TIME">One Time</option>
                  </select>
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                    New Amount (₹) *
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    required
                    placeholder="e.g. 5000.00"
                    value={changePlanForm.new_amount}
                    onChange={(e) => setChangePlanForm({ ...changePlanForm, new_amount: e.target.value })}
                    className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-xs focus:outline-none focus:border-[#facc15]"
                  />
                </div>
              </div>

              <div className="p-3 bg-amber-500/10 border border-amber-500/20 rounded-xl text-xs text-amber-300">
                <p className="font-semibold">
                  Effective From Next Cycle ({activePlanForChange.next_billing_date || "Next Cycle"})
                </p>
                <p className="text-[11px] text-amber-400/80 mt-1">
                  The current coverage period and current invoice remain completely untouched. No overlapping charges or duplicate invoices.
                </p>
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                  Change Reason (Mandatory Audit) *
                </label>
                <textarea
                  rows={2}
                  required
                  placeholder="e.g. Student requested quarterly fee package starting next month"
                  value={changePlanForm.reason}
                  onChange={(e) => setChangePlanForm({ ...changePlanForm, reason: e.target.value })}
                  className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-xs focus:outline-none focus:border-[#facc15] resize-none"
                />
              </div>

              <div className="flex justify-end gap-3 pt-4 border-t border-white/5">
                <button
                  type="button"
                  onClick={() => setShowChangePlanModal(false)}
                  className="px-4 py-2 text-zinc-400 hover:text-white text-xs font-semibold transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  className="px-5 py-2.5 bg-[#facc15] hover:bg-yellow-500 text-black text-xs font-bold rounded-xl transition-all shadow-md disabled:opacity-50"
                >
                  {actionLoading ? "Applying..." : "Schedule Plan Change"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 3: Record Manual Payment */}
      {showManualPaymentModal && selectedInvoiceForPayment && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 rounded-2xl max-w-md w-full p-6 shadow-2xl">
            <h3 className="text-lg font-bold text-white mb-1">Record Manual Payment</h3>
            <p className="text-zinc-400 text-xs mb-6">Record offline cash, bank transfer, or direct UPI settlement.</p>

            <form onSubmit={handleManualPaymentSubmit} className="space-y-4">
              <div className="p-3 bg-white/5 rounded-xl border border-white/5 text-xs space-y-1">
                <div className="flex justify-between">
                  <span className="text-zinc-500 font-semibold">Invoice Number:</span>
                  <span className="font-mono font-bold text-white">{selectedInvoiceForPayment.invoice_number}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500 font-semibold">Course:</span>
                  <span className="text-zinc-200">{selectedInvoiceForPayment.course_details?.title}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500 font-semibold">Period:</span>
                  <span className="text-zinc-400">
                    {selectedInvoiceForPayment.period_start} → {selectedInvoiceForPayment.period_end}
                  </span>
                </div>
                <div className="flex justify-between pt-1 border-t border-white/5">
                  <span className="text-zinc-400 font-semibold">Outstanding Amount:</span>
                  <span className="text-[#facc15] font-bold text-sm">₹{selectedInvoiceForPayment.amount}</span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                    Payment Amount (₹) *
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    required
                    value={paymentForm.amount}
                    onChange={(e) => setPaymentForm({ ...paymentForm, amount: e.target.value })}
                    className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-sm font-bold focus:outline-none focus:border-[#facc15]"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                    Payment Method *
                  </label>
                  <select
                    value={paymentForm.payment_method}
                    onChange={(e) => setPaymentForm({ ...paymentForm, payment_method: e.target.value })}
                    className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-xs focus:outline-none focus:border-[#facc15]"
                  >
                    <option value="CASH">Cash</option>
                    <option value="UPI">UPI</option>
                    <option value="BANK_TRANSFER">Bank Transfer (NEFT/RTGS)</option>
                    <option value="OTHER">Other Offline</option>
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                  Reference / Transaction ID
                </label>
                <input
                  type="text"
                  placeholder="e.g. Bank UTR, UPI transaction ref, Receipt #"
                  value={paymentForm.reference_number}
                  onChange={(e) => setPaymentForm({ ...paymentForm, reference_number: e.target.value })}
                  className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-xs focus:outline-none focus:border-[#facc15]"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                  Payment Notes
                </label>
                <textarea
                  rows={2}
                  placeholder="Optional audit remarks regarding receipt handover"
                  value={paymentForm.notes}
                  onChange={(e) => setPaymentForm({ ...paymentForm, notes: e.target.value })}
                  className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-xs focus:outline-none focus:border-[#facc15] resize-none"
                />
              </div>

              <div className="flex justify-end gap-3 pt-4 border-t border-white/5">
                <button
                  type="button"
                  onClick={() => setShowManualPaymentModal(false)}
                  className="px-4 py-2 text-zinc-400 hover:text-white text-xs font-semibold transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  className="px-5 py-2.5 bg-emerald-500 hover:bg-emerald-400 text-black text-xs font-bold rounded-xl transition-all shadow-md disabled:opacity-50"
                >
                  {actionLoading ? "Recording..." : "Confirm & Advance Cycle"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL 4: Extend Access Override */}
      {showExtendAccessModal && activePlanForExtend && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 rounded-2xl max-w-md w-full p-6 shadow-2xl">
            <h3 className="text-lg font-bold text-white mb-1">Grant Access Extension</h3>
            <p className="text-zinc-400 text-xs mb-6">
              Temporarily grant content access without altering historical invoices or marking them paid.
            </p>

            <form onSubmit={handleExtendAccessSubmit} className="space-y-4">
              <div className="p-3 bg-white/5 rounded-xl border border-white/5 text-xs space-y-1">
                <div className="flex justify-between">
                  <span className="text-zinc-500 font-semibold">Student:</span>
                  <span className="text-white font-medium">{studentName}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500 font-semibold">Current Status:</span>
                  <span>{getStatusBadge(activePlanForExtend.status)}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500 font-semibold">Restriction Date:</span>
                  <span className="text-rose-400 font-semibold">{activePlanForExtend.access_restriction_date || "N/A"}</span>
                </div>
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                  Extend Access Until *
                </label>
                <input
                  type="date"
                  required
                  value={extendAccessForm.extended_until}
                  onChange={(e) => setExtendAccessForm({ ...extendAccessForm, extended_until: e.target.value })}
                  className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-sm focus:outline-none focus:border-[#facc15]"
                />
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                  Extension Reason (Mandatory Audit) *
                </label>
                <textarea
                  rows={3}
                  required
                  placeholder="e.g. Student requested 10 days grace extension due to exam travel"
                  value={extendAccessForm.reason}
                  onChange={(e) => setExtendAccessForm({ ...extendAccessForm, reason: e.target.value })}
                  className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-xs focus:outline-none focus:border-[#facc15] resize-none"
                />
              </div>

              <div className="p-3 bg-zinc-800/60 rounded-xl text-[11px] text-zinc-400 border border-white/5">
                Note: This creates an explicit audit record in BillingAccessExtension. Historical invoice dates are preserved.
              </div>

              <div className="flex justify-end gap-3 pt-4 border-t border-white/5">
                <button
                  type="button"
                  onClick={() => setShowExtendAccessModal(false)}
                  className="px-4 py-2 text-zinc-400 hover:text-white text-xs font-semibold transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={actionLoading}
                  className="px-5 py-2.5 bg-[#facc15] hover:bg-yellow-500 text-black text-xs font-bold rounded-xl transition-all shadow-md disabled:opacity-50"
                >
                  {actionLoading ? "Granting..." : "Grant Access Override"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
