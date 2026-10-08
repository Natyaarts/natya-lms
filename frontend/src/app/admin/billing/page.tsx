"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import {
  DollarSign,
  Search,
  Filter,
  RefreshCw,
  CreditCard,
  CheckCircle2,
  AlertCircle,
  Clock,
  ShieldAlert,
  ArrowUpRight,
  ExternalLink,
  ChevronRight,
  FileText,
  User,
  Calendar,
  Layers
} from "lucide-react";

export default function BillingDashboard() {
  const [loading, setLoading] = useState(true);
  const [metrics, setMetrics] = useState<any>({
    active_plans: 0,
    due_payments: 0,
    grace_period: 0,
    overdue_restricted: 0,
    paid_this_month: "0.00",
    outstanding_amount: "0.00"
  });

  const [activeTab, setActiveTab] = useState<"invoices" | "plans" | "payments">("invoices");

  // Data lists
  const [invoices, setInvoices] = useState<any[]>([]);
  const [plans, setPlans] = useState<any[]>([]);
  const [manualPayments, setManualPayments] = useState<any[]>([]);

  // Filters
  const [search, setSearch] = useState("");
  const [planStatusFilter, setPlanStatusFilter] = useState("");
  const [invoiceStatusFilter, setInvoiceStatusFilter] = useState("");
  const [billingTypeFilter, setBillingTypeFilter] = useState("");

  // Modals / Action State
  const [selectedInvoiceForPayment, setSelectedInvoiceForPayment] = useState<any>(null);
  const [paymentAmount, setPaymentAmount] = useState("");
  const [paymentMethod, setPaymentMethod] = useState("CASH");
  const [paymentRef, setPaymentRef] = useState("");
  const [paymentNotes, setPaymentNotes] = useState("");
  const [submittingPayment, setSubmittingPayment] = useState(false);
  const [paymentLinkResult, setPaymentLinkResult] = useState<{
    invoiceId: number;
    url?: string;
    orderId?: string;
  } | null>(null);

  const [notification, setNotification] = useState<{ type: "success" | "error"; text: string } | null>(null);

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

  const fetchDashboardData = async () => {
    setLoading(true);
    setNotification(null);
    try {
      const baseUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

      // 1. Fetch summary metrics
      const summaryRes = await fetch(`${baseUrl}/api/billing/plans/dashboard-summary/`, {
        credentials: "include"
      });
      if (summaryRes.ok) {
        const summaryData = await summaryRes.json();
        setMetrics(summaryData.metrics || {});
      }

      // 2. Fetch invoices with filters
      let invoiceUrl = `${baseUrl}/api/billing/invoices/?`;
      if (search) invoiceUrl += `search=${encodeURIComponent(search)}&`;
      if (invoiceStatusFilter) invoiceUrl += `status=${encodeURIComponent(invoiceStatusFilter)}&`;

      // 3. Fetch plans with filters
      let planUrl = `${baseUrl}/api/billing/plans/?`;
      if (search) planUrl += `search=${encodeURIComponent(search)}&`;
      if (planStatusFilter) planUrl += `status=${encodeURIComponent(planStatusFilter)}&`;
      if (billingTypeFilter) planUrl += `billing_type=${encodeURIComponent(billingTypeFilter)}&`;

      // 4. Fetch manual payments
      const paymentsUrl = `${baseUrl}/api/billing/manual-payments/`;

      const [invRes, plRes, payRes] = await Promise.all([
        fetch(invoiceUrl, { credentials: "include" }),
        fetch(planUrl, { credentials: "include" }),
        fetch(paymentsUrl, { credentials: "include" })
      ]);

      if (invRes.ok) setInvoices(await invRes.json());
      if (plRes.ok) setPlans(await plRes.json());
      if (payRes.ok) setManualPayments(await payRes.json());
    } catch (err) {
      console.error("Failed to load billing dashboard:", err);
      setNotification({ type: "error", text: "Failed to load billing dashboard data." });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDashboardData();
  }, [invoiceStatusFilter, planStatusFilter, billingTypeFilter]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    fetchDashboardData();
  };

  const handleManualPaymentSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedInvoiceForPayment) return;
    setSubmittingPayment(true);
    setNotification(null);

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
          amount: paymentAmount,
          payment_method: paymentMethod,
          reference_number: paymentRef,
          notes: paymentNotes
        })
      });

      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.amount || data.detail || JSON.stringify(data));
      }

      setNotification({
        type: "success",
        text: `Payment of ₹${paymentAmount} recorded for ${selectedInvoiceForPayment.invoice_number}. Invoice marked PAID and cycle advanced.`
      });
      setSelectedInvoiceForPayment(null);
      fetchDashboardData();
    } catch (err: any) {
      setNotification({ type: "error", text: err.message || "Failed to record manual payment." });
    } finally {
      setSubmittingPayment(false);
    }
  };

  const handleGeneratePaymentLink = async (invoiceId: number) => {
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
        setNotification({ type: "success", text: "Razorpay order & payment link generated." });
        fetchDashboardData();
      } else {
        setNotification({ type: "error", text: data.error || "Failed to generate payment link." });
      }
    } catch (err) {
      setNotification({ type: "error", text: "Failed to communicate with Razorpay." });
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
      case "OVERDUE":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-rose-500/10 text-rose-400 border border-rose-500/20">{status}</span>;
      case "PAID":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">PAID</span>;
      case "ISSUED":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-blue-500/10 text-blue-400 border border-blue-500/20">ISSUED</span>;
      case "PENDING":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20">PENDING</span>;
      case "CANCELLED":
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-zinc-600/10 text-zinc-500 border border-zinc-600/20">CANCELLED</span>;
      default:
        return <span className="px-2.5 py-1 text-[10px] font-bold rounded-full bg-zinc-500/10 text-zinc-400 border border-white/10">{status}</span>;
    }
  };

  return (
    <div className="max-w-7xl mx-auto pb-20 font-sans text-white space-y-8">
      {/* Top Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold flex items-center gap-3">
            <DollarSign className="w-8 h-8 text-[#facc15]" />
            Student Billing & Fee Management
          </h1>
          <p className="text-xs text-zinc-400 mt-1">
            Dynamic per-student course fee arrangements, cycle invoicing, manual payments, and access controls.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={fetchDashboardData}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-2.5 bg-zinc-900 border border-white/10 hover:border-white/20 rounded-xl text-xs font-semibold text-zinc-300 transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} /> Refresh
          </button>
        </div>
      </div>

      {/* Notifications */}
      {notification && (
        <div
          className={`p-4 rounded-xl text-xs flex items-center gap-3 border ${
            notification.type === "success"
              ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-400"
              : "bg-rose-500/10 border-rose-500/20 text-rose-400"
          }`}
        >
          {notification.type === "success" ? (
            <CheckCircle2 className="w-4 h-4 shrink-0" />
          ) : (
            <AlertCircle className="w-4 h-4 shrink-0" />
          )}
          <span>{notification.text}</span>
        </div>
      )}

      {/* Metric Summary Cards */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4 shadow-xl">
          <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">Active Plans</div>
          <div className="text-2xl font-bold text-white mt-2">{metrics.active_plans ?? 0}</div>
          <div className="text-[10px] text-emerald-400 mt-1 flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3" /> In good standing
          </div>
        </div>

        <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4 shadow-xl">
          <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">Due Payments</div>
          <div className="text-2xl font-bold text-amber-400 mt-2">{metrics.due_payments ?? 0}</div>
          <div className="text-[10px] text-zinc-500 mt-1">Pending payment deadline</div>
        </div>

        <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4 shadow-xl">
          <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">Grace Period</div>
          <div className="text-2xl font-bold text-orange-400 mt-2">{metrics.grace_period ?? 0}</div>
          <div className="text-[10px] text-orange-400/80 mt-1">Access retained</div>
        </div>

        <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4 shadow-xl">
          <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">Overdue / Locked</div>
          <div className="text-2xl font-bold text-rose-400 mt-2">{metrics.overdue_restricted ?? 0}</div>
          <div className="text-[10px] text-rose-400/80 mt-1">Content access restricted</div>
        </div>

        <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4 shadow-xl">
          <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">Paid This Month</div>
          <div className="text-2xl font-bold text-emerald-400 mt-2">₹{metrics.paid_this_month ?? "0"}</div>
          <div className="text-[10px] text-zinc-500 mt-1">Current calendar month</div>
        </div>

        <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4 shadow-xl">
          <div className="text-[11px] font-semibold text-zinc-400 uppercase tracking-wider">Outstanding</div>
          <div className="text-2xl font-bold text-[#facc15] mt-2">₹{metrics.outstanding_amount ?? "0"}</div>
          <div className="text-[10px] text-zinc-500 mt-1">Total pending fees</div>
        </div>
      </div>

      {/* Filter Toolbar */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4 shadow-xl">
        <form onSubmit={handleSearchSubmit} className="flex flex-wrap items-center gap-3">
          <div className="flex-1 min-w-[220px] relative">
            <Search className="w-4 h-4 text-zinc-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search student username, email, invoice #, course title..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-zinc-950 border border-white/10 rounded-xl pl-9 pr-4 py-2 text-xs text-white placeholder-zinc-500 focus:outline-none focus:border-[#facc15]"
            />
          </div>

          <div className="flex items-center gap-2">
            <select
              value={invoiceStatusFilter}
              onChange={(e) => setInvoiceStatusFilter(e.target.value)}
              className="bg-zinc-950 border border-white/10 rounded-xl px-3 py-2 text-xs text-zinc-300 focus:outline-none focus:border-[#facc15]"
            >
              <option value="">All Invoice Statuses</option>
              <option value="ISSUED">Issued</option>
              <option value="PENDING">Pending</option>
              <option value="PAID">Paid</option>
              <option value="OVERDUE">Overdue</option>
              <option value="CANCELLED">Cancelled</option>
            </select>

            <select
              value={planStatusFilter}
              onChange={(e) => setPlanStatusFilter(e.target.value)}
              className="bg-zinc-950 border border-white/10 rounded-xl px-3 py-2 text-xs text-zinc-300 focus:outline-none focus:border-[#facc15]"
            >
              <option value="">All Plan Statuses</option>
              <option value="ACTIVE">Active</option>
              <option value="DUE">Due</option>
              <option value="GRACE_PERIOD">Grace Period</option>
              <option value="RESTRICTED">Restricted</option>
              <option value="PAUSED">Paused</option>
              <option value="CANCELLED">Cancelled</option>
            </select>

            <select
              value={billingTypeFilter}
              onChange={(e) => setBillingTypeFilter(e.target.value)}
              className="bg-zinc-950 border border-white/10 rounded-xl px-3 py-2 text-xs text-zinc-300 focus:outline-none focus:border-[#facc15]"
            >
              <option value="">All Frequencies</option>
              <option value="MONTHLY">Monthly</option>
              <option value="EVERY_2_MONTHS">Every 2 Months</option>
              <option value="EVERY_3_MONTHS">Every 3 Months</option>
              <option value="EVERY_6_MONTHS">Every 6 Months</option>
              <option value="YEARLY">Yearly</option>
              <option value="ONE_TIME">One Time</option>
            </select>

            <button
              type="submit"
              className="px-4 py-2 bg-[#facc15] text-black font-bold text-xs rounded-xl hover:bg-yellow-500 transition-colors"
            >
              Filter
            </button>
          </div>
        </form>
      </div>

      {/* Tabs Bar */}
      <div className="flex gap-2 p-1 bg-zinc-950 border border-white/10 rounded-xl w-max">
        <button
          onClick={() => setActiveTab("invoices")}
          className={`px-5 py-2.5 rounded-lg text-xs font-semibold transition-all flex items-center gap-2 ${
            activeTab === "invoices" ? "bg-[#facc15] text-black shadow-sm" : "text-zinc-400 hover:text-white"
          }`}
        >
          <FileText className="w-4 h-4" /> Invoices ({invoices.length})
        </button>
        <button
          onClick={() => setActiveTab("plans")}
          className={`px-5 py-2.5 rounded-lg text-xs font-semibold transition-all flex items-center gap-2 ${
            activeTab === "plans" ? "bg-[#facc15] text-black shadow-sm" : "text-zinc-400 hover:text-white"
          }`}
        >
          <Layers className="w-4 h-4" /> Student Plans ({plans.length})
        </button>
        <button
          onClick={() => setActiveTab("payments")}
          className={`px-5 py-2.5 rounded-lg text-xs font-semibold transition-all flex items-center gap-2 ${
            activeTab === "payments" ? "bg-[#facc15] text-black shadow-sm" : "text-zinc-400 hover:text-white"
          }`}
        >
          <CreditCard className="w-4 h-4" /> Manual Payments ({manualPayments.length})
        </button>
      </div>

      {/* Active Tab Content */}
      {activeTab === "invoices" && (
        <div className="bg-zinc-950 border border-white/10 rounded-2xl overflow-hidden shadow-xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider font-semibold">
                  <th className="p-4">Invoice #</th>
                  <th className="p-4">Student</th>
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
                    <td colSpan={9} className="p-16 text-center text-zinc-500">
                      No invoices found matching criteria.
                    </td>
                  </tr>
                ) : (
                  invoices.map((inv: any) => (
                    <tr key={inv.id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="p-4 font-mono font-bold text-white">{inv.invoice_number}</td>
                      <td className="p-4">
                        <Link
                          href={`/admin/users/${inv.student}`}
                          className="font-semibold text-white hover:text-[#facc15] transition-colors flex items-center gap-1.5"
                        >
                          <User className="w-3.5 h-3.5 text-zinc-400" />
                          {inv.student_details?.full_name || inv.student_details?.username}
                        </Link>
                        <div className="text-[10px] text-zinc-500">{inv.student_details?.email}</div>
                      </td>
                      <td className="p-4 font-medium text-zinc-200">{inv.course_details?.title}</td>
                      <td className="p-4 text-zinc-400">
                        {new Date(inv.period_start).toLocaleDateString()} → {new Date(inv.period_end).toLocaleDateString()}
                      </td>
                      <td className="p-4 text-amber-400 font-medium">{new Date(inv.due_date).toLocaleDateString()}</td>
                      <td className="p-4 text-[#facc15] font-bold text-sm">₹{inv.amount}</td>
                      <td className="p-4 text-center">{getStatusBadge(inv.status)}</td>
                      <td className="p-4">
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
                                  setPaymentAmount(String(inv.amount));
                                  setPaymentMethod("CASH");
                                  setPaymentRef("");
                                  setPaymentNotes("");
                                }}
                                className="px-2.5 py-1 bg-[#facc15] hover:bg-yellow-500 text-black text-[11px] font-bold rounded-lg transition-colors"
                              >
                                Record Payment
                              </button>
                              <button
                                onClick={() => handleGeneratePaymentLink(inv.id)}
                                className="p-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors border border-white/5"
                                title="Generate Razorpay payment link"
                              >
                                <CreditCard className="w-3.5 h-3.5" />
                              </button>
                            </>
                          )}
                          <Link
                            href={`/admin/users/${inv.student}`}
                            className="p-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-400 hover:text-white rounded-lg transition-colors"
                            title="Open Student Profile"
                          >
                            <ChevronRight className="w-3.5 h-3.5" />
                          </Link>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === "plans" && (
        <div className="bg-zinc-950 border border-white/10 rounded-2xl overflow-hidden shadow-xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider font-semibold">
                  <th className="p-4">Student</th>
                  <th className="p-4">Course</th>
                  <th className="p-4">Billing Arrangement</th>
                  <th className="p-4">Fee Amount</th>
                  <th className="p-4">Current Period</th>
                  <th className="p-4">Next Billing</th>
                  <th className="p-4 text-center">Status</th>
                  <th className="p-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-zinc-300">
                {plans.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="p-16 text-center text-zinc-500">
                      No billing plans found matching criteria.
                    </td>
                  </tr>
                ) : (
                  plans.map((plan: any) => (
                    <tr key={plan.id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="p-4">
                        <Link
                          href={`/admin/users/${plan.student}`}
                          className="font-semibold text-white hover:text-[#facc15] transition-colors flex items-center gap-1.5"
                        >
                          <User className="w-3.5 h-3.5 text-zinc-400" />
                          {plan.student_details?.full_name || plan.student_details?.username}
                        </Link>
                        <div className="text-[10px] text-zinc-500">{plan.student_details?.email}</div>
                      </td>
                      <td className="p-4 font-medium text-zinc-200">{plan.course_details?.title}</td>
                      <td className="p-4">
                        <span className="font-semibold text-[#facc15]">{plan.billing_type}</span>
                      </td>
                      <td className="p-4 text-white font-bold text-sm">₹{plan.amount}</td>
                      <td className="p-4 text-zinc-400">
                        {new Date(plan.current_period_start).toLocaleDateString()} → {new Date(plan.current_period_end).toLocaleDateString()}
                      </td>
                      <td className="p-4 text-zinc-300 font-medium">
                        {plan.next_billing_date ? new Date(plan.next_billing_date).toLocaleDateString() : "One-Time"}
                      </td>
                      <td className="p-4 text-center">{getStatusBadge(plan.status)}</td>
                      <td className="p-4 text-right">
                        <Link
                          href={`/admin/users/${plan.student}`}
                          className="px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-200 text-xs font-semibold rounded-lg transition-colors border border-white/5 inline-flex items-center gap-1"
                        >
                          Manage Plan <ChevronRight className="w-3 h-3" />
                        </Link>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {activeTab === "payments" && (
        <div className="bg-zinc-950 border border-white/10 rounded-2xl overflow-hidden shadow-xl">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider font-semibold">
                  <th className="p-4">Date</th>
                  <th className="p-4">Invoice #</th>
                  <th className="p-4">Amount Paid</th>
                  <th className="p-4">Method</th>
                  <th className="p-4">Reference / UTR</th>
                  <th className="p-4">Recorded By</th>
                  <th className="p-4">Notes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-zinc-300">
                {manualPayments.length === 0 ? (
                  <tr>
                    <td colSpan={7} className="p-16 text-center text-zinc-500">
                      No manual offline payments recorded yet.
                    </td>
                  </tr>
                ) : (
                  manualPayments.map((pay: any) => (
                    <tr key={pay.id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="p-4 text-zinc-400">{new Date(pay.payment_date).toLocaleString()}</td>
                      <td className="p-4 font-mono font-bold text-white">{pay.invoice_number}</td>
                      <td className="p-4 text-emerald-400 font-bold text-sm">₹{pay.amount}</td>
                      <td className="p-4 font-semibold text-zinc-200">{pay.payment_method}</td>
                      <td className="p-4 font-mono text-zinc-400">{pay.reference_number || "—"}</td>
                      <td className="p-4 text-zinc-400">{pay.recorded_by_name || "Admin"}</td>
                      <td className="p-4 text-zinc-400 max-w-xs truncate">{pay.notes || "—"}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Manual Payment Modal */}
      {selectedInvoiceForPayment && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 rounded-2xl max-w-md w-full p-6 shadow-2xl">
            <h3 className="text-lg font-bold text-white mb-1">Record Offline Payment</h3>
            <p className="text-zinc-400 text-xs mb-6">Process full settlement for invoice {selectedInvoiceForPayment.invoice_number}.</p>

            <form onSubmit={handleManualPaymentSubmit} className="space-y-4">
              <div className="p-3 bg-white/5 rounded-xl border border-white/5 text-xs space-y-1">
                <div className="flex justify-between">
                  <span className="text-zinc-500 font-semibold">Student:</span>
                  <span className="text-white font-medium">
                    {selectedInvoiceForPayment.student_details?.full_name || selectedInvoiceForPayment.student_details?.username}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-zinc-500 font-semibold">Course:</span>
                  <span className="text-zinc-200">{selectedInvoiceForPayment.course_details?.title}</span>
                </div>
                <div className="flex justify-between pt-1 border-t border-white/5">
                  <span className="text-zinc-400 font-semibold">Outstanding Invoice Amount:</span>
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
                    value={paymentAmount}
                    onChange={(e) => setPaymentAmount(e.target.value)}
                    className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-sm font-bold focus:outline-none focus:border-[#facc15]"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wide mb-1.5">
                    Payment Method *
                  </label>
                  <select
                    value={paymentMethod}
                    onChange={(e) => setPaymentMethod(e.target.value)}
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
                  value={paymentRef}
                  onChange={(e) => setPaymentRef(e.target.value)}
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
                  value={paymentNotes}
                  onChange={(e) => setPaymentNotes(e.target.value)}
                  className="w-full bg-zinc-950 border border-white/10 rounded-xl p-3 text-white text-xs focus:outline-none focus:border-[#facc15] resize-none"
                />
              </div>

              <div className="flex justify-end gap-3 pt-4 border-t border-white/5">
                <button
                  type="button"
                  onClick={() => setSelectedInvoiceForPayment(null)}
                  className="px-4 py-2 text-zinc-400 hover:text-white text-xs font-semibold transition-colors"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={submittingPayment}
                  className="px-5 py-2.5 bg-emerald-500 hover:bg-emerald-400 text-black text-xs font-bold rounded-xl transition-all shadow-md disabled:opacity-50"
                >
                  {submittingPayment ? "Recording..." : "Confirm & Advance Cycle"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
