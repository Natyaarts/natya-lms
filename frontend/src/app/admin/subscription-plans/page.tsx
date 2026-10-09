"use client";

import { useEffect, useState, useMemo } from "react";
import {
  Layers,
  Plus,
  Search,
  Filter,
  Eye,
  Edit2,
  Trash2,
  Power,
  Users,
  BookOpen,
  DollarSign,
  AlertTriangle,
  CheckCircle2,
  X,
  RefreshCw,
  Sparkles,
  ChevronLeft,
  ChevronRight,
  ShieldAlert,
  Info
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface PlanCourse {
  id: number;
  title: string;
  thumbnail: string | null;
  price: string;
  course_type: string;
  is_published: boolean;
}

interface SubscriptionPlan {
  id: number;
  name: string;
  slug: string;
  description: string;
  billing_interval: "MONTHLY" | "YEARLY";
  price: string;
  currency: string;
  courses: PlanCourse[];
  is_active: boolean;
  subscriber_count?: number;
  created_at?: string;
  updated_at?: string;
}

interface AvailableCourse {
  id: number;
  title: string;
  price: string | number;
  course_type?: string;
  is_published?: boolean;
}

export default function SubscriptionPlansPage() {
  const [plans, setPlans] = useState<SubscriptionPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [successMsg, setSuccessMsg] = useState("");

  // Filters & Search
  const [searchTerm, setSearchTerm] = useState("");
  const [intervalFilter, setIntervalFilter] = useState<"ALL" | "MONTHLY" | "YEARLY">("ALL");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "ACTIVE" | "INACTIVE">("ALL");

  // Pagination
  const [currentPage, setCurrentPage] = useState(1);
  const pageSize = 8;

  // Available courses for bundle selection
  const [availableCourses, setAvailableCourses] = useState<AvailableCourse[]>([]);
  const [loadingCourses, setLoadingCourses] = useState(false);

  // Modals
  const [viewModalPlan, setViewModalPlan] = useState<SubscriptionPlan | null>(null);
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false);
  const [editModalPlan, setEditModalPlan] = useState<SubscriptionPlan | null>(null);
  const [deleteSafeguardPlan, setDeleteSafeguardPlan] = useState<SubscriptionPlan | null>(null);
  const [confirmDeletePlan, setConfirmDeletePlan] = useState<SubscriptionPlan | null>(null);

  // Form State (used for both Create and Edit)
  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formBillingInterval, setFormBillingInterval] = useState<"MONTHLY" | "YEARLY">("MONTHLY");
  const [formPrice, setFormPrice] = useState("");
  const [formIsActive, setFormIsActive] = useState(true);
  const [formSelectedCourseIds, setFormSelectedCourseIds] = useState<number[]>([]);
  const [formCourseSearch, setFormCourseSearch] = useState("");
  const [formSubmitting, setFormSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

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

  const fetchPlans = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/orders/subscription-plans/`, {
        credentials: "include"
      });
      if (res.ok) {
        const data = await res.json();
        setPlans(Array.isArray(data) ? data : data.results || []);
      } else {
        setError("Failed to fetch subscription plans");
      }
    } catch (err) {
      setError("Network error fetching subscription plans");
    } finally {
      setLoading(false);
    }
  };

  const fetchAvailableCourses = async () => {
    if (availableCourses.length > 0) return;
    setLoadingCourses(true);
    try {
      const res = await fetch(`${API_BASE}/api/courses/`, {
        credentials: "include"
      });
      if (res.ok) {
        const data = await res.json();
        setAvailableCourses(Array.isArray(data) ? data : data.results || []);
      }
    } catch (err) {
      console.error("Failed to load courses for bundle selection:", err);
    } finally {
      setLoadingCourses(false);
    }
  };

  useEffect(() => {
    fetchPlans();
  }, []);

  // Filtered & Paginated Plans
  const filteredPlans = useMemo(() => {
    return plans.filter(plan => {
      const matchesSearch =
        plan.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
        plan.slug.toLowerCase().includes(searchTerm.toLowerCase()) ||
        (plan.description && plan.description.toLowerCase().includes(searchTerm.toLowerCase()));

      const matchesInterval =
        intervalFilter === "ALL" || plan.billing_interval === intervalFilter;

      const matchesStatus =
        statusFilter === "ALL" ||
        (statusFilter === "ACTIVE" && plan.is_active) ||
        (statusFilter === "INACTIVE" && !plan.is_active);

      return matchesSearch && matchesInterval && matchesStatus;
    });
  }, [plans, searchTerm, intervalFilter, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredPlans.length / pageSize));
  const paginatedPlans = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredPlans.slice(start, start + pageSize);
  }, [filteredPlans, currentPage, pageSize]);

  // Metric Aggregates
  const totalPlansCount = plans.length;
  const activePlansCount = plans.filter(p => p.is_active).length;
  const totalBundledCourses = useMemo(() => {
    const courseIds = new Set<number>();
    plans.forEach(p => p.courses.forEach(c => courseIds.add(c.id)));
    return courseIds.size;
  }, [plans]);
  const totalSubscribersCount = useMemo(() => {
    return plans.reduce((acc, p) => acc + (p.subscriber_count || 0), 0);
  }, [plans]);

  // Open Create Modal
  const openCreateModal = () => {
    setFormName("");
    setFormDescription("");
    setFormBillingInterval("MONTHLY");
    setFormPrice("");
    setFormIsActive(true);
    setFormSelectedCourseIds([]);
    setFormCourseSearch("");
    setFormError("");
    fetchAvailableCourses();
    setIsCreateModalOpen(true);
  };

  // Open Edit Modal
  const openEditModal = (plan: SubscriptionPlan) => {
    setFormName(plan.name);
    setFormDescription(plan.description || "");
    setFormBillingInterval(plan.billing_interval);
    setFormPrice(plan.price);
    setFormIsActive(plan.is_active);
    setFormSelectedCourseIds(plan.courses.map(c => c.id));
    setFormCourseSearch("");
    setFormError("");
    fetchAvailableCourses();
    setEditModalPlan(plan);
  };

  // Submit Create Plan
  const handleCreateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    if (!formName.trim()) {
      setFormError("Plan name is required.");
      return;
    }
    const numPrice = parseFloat(formPrice);
    if (isNaN(numPrice) || numPrice < 0) {
      setFormError("Please enter a valid price of 0 or greater.");
      return;
    }

    setFormSubmitting(true);
    try {
      const res = await fetch(`${API_BASE}/api/orders/subscription-plans/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include",
        body: JSON.stringify({
          name: formName.trim(),
          description: formDescription.trim(),
          billing_interval: formBillingInterval,
          price: formPrice,
          currency: "INR",
          is_active: formIsActive,
          course_ids: formSelectedCourseIds
        })
      });

      const data = await res.json();
      if (res.ok) {
        setSuccessMsg(`Subscription plan "${data.name}" created successfully.`);
        setIsCreateModalOpen(false);
        fetchPlans();
      } else {
        setFormError(data.error || data.detail || JSON.stringify(data));
      }
    } catch (err: any) {
      setFormError(err.message || "Failed to create subscription plan.");
    } finally {
      setFormSubmitting(false);
    }
  };

  // Submit Edit Plan
  const handleEditSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editModalPlan) return;
    setFormError("");
    if (!formName.trim()) {
      setFormError("Plan name is required.");
      return;
    }
    const numPrice = parseFloat(formPrice);
    if (isNaN(numPrice) || numPrice < 0) {
      setFormError("Please enter a valid price of 0 or greater.");
      return;
    }

    setFormSubmitting(true);
    try {
      const res = await fetch(`${API_BASE}/api/orders/subscription-plans/${editModalPlan.id}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include",
        body: JSON.stringify({
          name: formName.trim(),
          description: formDescription.trim(),
          billing_interval: formBillingInterval,
          price: formPrice,
          is_active: formIsActive,
          course_ids: formSelectedCourseIds
        })
      });

      const data = await res.json();
      if (res.ok) {
        setSuccessMsg(`Plan "${data.name}" updated successfully.`);
        setEditModalPlan(null);
        fetchPlans();
      } else {
        setFormError(data.error || data.detail || JSON.stringify(data));
      }
    } catch (err: any) {
      setFormError(err.message || "Failed to update subscription plan.");
    } finally {
      setFormSubmitting(false);
    }
  };

  // Toggle Active Status
  const handleToggleActive = async (plan: SubscriptionPlan) => {
    try {
      const res = await fetch(`${API_BASE}/api/orders/subscription-plans/${plan.id}/toggle-active/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include"
      });
      const data = await res.json();
      if (res.ok) {
        setSuccessMsg(data.message || `Plan status changed.`);
        setPlans(prev => prev.map(p => (p.id === plan.id ? { ...p, is_active: !p.is_active } : p)));
        if (deleteSafeguardPlan && deleteSafeguardPlan.id === plan.id) {
          setDeleteSafeguardPlan(null);
        }
      } else {
        setError(data.error || "Failed to toggle plan status.");
      }
    } catch (err) {
      setError("Network error toggling plan status.");
    }
  };

  // Delete Click handler with safeguard
  const handleDeleteClick = (plan: SubscriptionPlan) => {
    const subscriberCount = plan.subscriber_count || 0;
    if (subscriberCount > 0) {
      setDeleteSafeguardPlan(plan);
    } else {
      setConfirmDeletePlan(plan);
    }
  };

  // Perform permanent delete (only allowed when 0 subscribers)
  const handleConfirmDelete = async () => {
    if (!confirmDeletePlan) return;
    try {
      const res = await fetch(`${API_BASE}/api/orders/subscription-plans/${confirmDeletePlan.id}/`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include"
      });

      if (res.ok) {
        setSuccessMsg(`Plan "${confirmDeletePlan.name}" permanently deleted.`);
        setConfirmDeletePlan(null);
        fetchPlans();
      } else {
        const data = await res.json();
        setError(data.error || "Failed to delete plan.");
      }
    } catch (err) {
      setError("Network error deleting plan.");
    }
  };

  // Course toggle helper for form
  const toggleCourseSelection = (courseId: number) => {
    setFormSelectedCourseIds(prev =>
      prev.includes(courseId) ? prev.filter(id => id !== courseId) : [...prev, courseId]
    );
  };

  const filteredAvailableCourses = useMemo(() => {
    return availableCourses.filter(c =>
      c.title.toLowerCase().includes(formCourseSearch.toLowerCase())
    );
  }, [availableCourses, formCourseSearch]);

  return (
    <div className="max-w-7xl mx-auto pb-24 font-sans text-white px-4 sm:px-6">
      {/* Header */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-3xl font-extrabold tracking-tight">Subscription Plans</h1>
            <span className="px-2.5 py-0.5 text-xs font-semibold rounded-full bg-[#facc15]/10 text-[#facc15] border border-[#facc15]/20">
              Commercial Plans & Bundles
            </span>
          </div>
          <p className="text-zinc-400 text-sm mt-1">
            Configure reusable subscription plans, course bundles, pricing, and student recurring access.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={fetchPlans}
            className="p-2.5 bg-zinc-900 hover:bg-zinc-800 border border-white/10 rounded-xl transition text-zinc-300 hover:text-white inline-flex items-center gap-1.5 text-xs font-semibold"
            title="Refresh list"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          <button
            onClick={openCreateModal}
            className="px-4 py-2.5 bg-[#facc15] hover:bg-[#eab308] text-black font-bold text-xs rounded-xl shadow-lg shadow-yellow-500/10 transition inline-flex items-center gap-2"
          >
            <Plus className="w-4 h-4" />
            Create Subscription Plan
          </button>
        </div>
      </div>

      {/* Metrics Bar */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mb-8">
        <div className="bg-zinc-900/80 border border-white/10 p-4 rounded-2xl">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-zinc-400">Total Plans</span>
            <Layers className="w-4 h-4 text-zinc-500" />
          </div>
          <div className="text-2xl font-black mt-2 text-white">{totalPlansCount}</div>
          <div className="text-[11px] text-zinc-500 mt-1">Configured catalog plans</div>
        </div>

        <div className="bg-zinc-900/80 border border-white/10 p-4 rounded-2xl">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-zinc-400">Active Plans</span>
            <CheckCircle2 className="w-4 h-4 text-green-400" />
          </div>
          <div className="text-2xl font-black mt-2 text-green-400">{activePlansCount}</div>
          <div className="text-[11px] text-zinc-500 mt-1">Available for subscription</div>
        </div>

        <div className="bg-zinc-900/80 border border-white/10 p-4 rounded-2xl">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-zinc-400">Bundled Courses</span>
            <BookOpen className="w-4 h-4 text-blue-400" />
          </div>
          <div className="text-2xl font-black mt-2 text-blue-400">{totalBundledCourses}</div>
          <div className="text-[11px] text-zinc-500 mt-1">Distinct bundled courses</div>
        </div>

        <div className="bg-zinc-900/80 border border-white/10 p-4 rounded-2xl">
          <div className="flex items-center justify-between">
            <span className="text-xs font-medium text-zinc-400">Active Students</span>
            <Users className="w-4 h-4 text-[#facc15]" />
          </div>
          <div className="text-2xl font-black mt-2 text-[#facc15]">{totalSubscribersCount}</div>
          <div className="text-[11px] text-zinc-500 mt-1">Current student subscriptions</div>
        </div>
      </div>

      {/* Notifications */}
      {error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-2xl mb-6 text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
          <button onClick={() => setError("")} className="text-red-400 hover:text-white text-xs">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {successMsg && (
        <div className="bg-green-500/10 border border-green-500/20 text-green-400 p-4 rounded-2xl mb-6 text-sm flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            <span>{successMsg}</span>
          </div>
          <button onClick={() => setSuccessMsg("")} className="text-green-400 hover:text-white text-xs">
            <X className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Search & Filter Controls */}
      <div className="bg-zinc-900 border border-white/10 p-4 rounded-2xl mb-6 flex flex-col md:flex-row gap-4 justify-between items-center">
        <div className="relative w-full md:w-80">
          <Search className="w-4 h-4 absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input
            type="text"
            placeholder="Search plans by name, slug or description..."
            value={searchTerm}
            onChange={e => {
              setSearchTerm(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full bg-black/40 border border-white/10 rounded-xl pl-10 pr-4 py-2 text-xs text-white placeholder-zinc-500 focus:outline-none focus:border-[#facc15]"
          />
        </div>

        <div className="flex flex-wrap items-center gap-3 w-full md:w-auto">
          {/* Interval Filter */}
          <div className="flex items-center gap-1.5 bg-black/40 border border-white/10 rounded-xl p-1 text-xs">
            <span className="text-zinc-500 px-2 text-[11px] font-medium">Interval:</span>
            {(["ALL", "MONTHLY", "YEARLY"] as const).map(interval => (
              <button
                key={interval}
                onClick={() => {
                  setIntervalFilter(interval);
                  setCurrentPage(1);
                }}
                className={`px-3 py-1 rounded-lg transition text-[11px] font-semibold ${
                  intervalFilter === interval
                    ? "bg-[#facc15] text-black shadow-sm"
                    : "text-zinc-400 hover:text-white"
                }`}
              >
                {interval === "ALL" ? "All" : interval}
              </button>
            ))}
          </div>

          {/* Status Filter */}
          <div className="flex items-center gap-1.5 bg-black/40 border border-white/10 rounded-xl p-1 text-xs">
            <span className="text-zinc-500 px-2 text-[11px] font-medium">Status:</span>
            {(["ALL", "ACTIVE", "INACTIVE"] as const).map(status => (
              <button
                key={status}
                onClick={() => {
                  setStatusFilter(status);
                  setCurrentPage(1);
                }}
                className={`px-3 py-1 rounded-lg transition text-[11px] font-semibold ${
                  statusFilter === status
                    ? "bg-[#facc15] text-black shadow-sm"
                    : "text-zinc-400 hover:text-white"
                }`}
              >
                {status === "ALL" ? "All" : status}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Plans Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Plan Details</th>
                <th className="p-4 font-semibold">Billing Interval</th>
                <th className="p-4 font-semibold">Price</th>
                <th className="p-4 font-semibold">Bundled Courses</th>
                <th className="p-4 font-semibold text-center">Subscribers</th>
                <th className="p-4 font-semibold text-center">Status</th>
                <th className="p-4 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading subscription plans...</span>
                    </div>
                  </td>
                </tr>
              ) : paginatedPlans.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-2">
                      <Layers className="w-8 h-8 text-zinc-600 mb-1" />
                      <span className="text-sm font-semibold text-zinc-400">No subscription plans found.</span>
                      <span className="text-xs text-zinc-600">
                        Try adjusting your search criteria or create a new subscription plan.
                      </span>
                    </div>
                  </td>
                </tr>
              ) : (
                paginatedPlans.map(plan => (
                  <tr
                    key={plan.id}
                    className="hover:bg-white/5 transition-colors cursor-pointer"
                    onClick={() => setViewModalPlan(plan)}
                  >
                    <td className="p-4">
                      <div className="font-bold text-white text-sm">{plan.name}</div>
                      <div className="text-zinc-500 text-[11px] mt-0.5 line-clamp-1">
                        {plan.description || plan.slug}
                      </div>
                    </td>

                    <td className="p-4">
                      <span className="px-2.5 py-1 text-[10px] font-bold rounded-lg bg-white/5 border border-white/10 text-zinc-300">
                        {plan.billing_interval}
                      </span>
                    </td>

                    <td className="p-4 font-bold text-[#facc15] text-sm">
                      {plan.currency} {parseFloat(plan.price).toLocaleString()}
                      <span className="text-[10px] text-zinc-500 font-normal ml-1">
                        / {plan.billing_interval.toLowerCase()}
                      </span>
                    </td>

                    <td className="p-4">
                      <div className="flex items-center gap-1.5">
                        <span className="px-2 py-0.5 text-[10px] font-bold rounded-md bg-blue-500/10 text-blue-400 border border-blue-500/20">
                          {plan.courses.length} {plan.courses.length === 1 ? "course" : "courses"}
                        </span>
                        {plan.courses.length > 0 && (
                          <span className="text-[11px] text-zinc-400 truncate max-w-[160px]">
                            {plan.courses.map(c => c.title).join(", ")}
                          </span>
                        )}
                      </div>
                    </td>

                    <td className="p-4 text-center">
                      <span className="px-2.5 py-0.5 text-[10px] font-bold rounded-full bg-zinc-800 text-zinc-300 border border-white/10 inline-flex items-center gap-1">
                        <Users className="w-3 h-3 text-[#facc15]" />
                        {plan.subscriber_count ?? 0}
                      </span>
                    </td>

                    <td className="p-4 text-center">
                      <span
                        className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${
                          plan.is_active
                            ? "bg-green-500/10 text-green-400 border border-green-500/20"
                            : "bg-red-500/10 text-red-400 border border-red-500/20"
                        }`}
                      >
                        {plan.is_active ? "ACTIVE" : "INACTIVE"}
                      </span>
                    </td>

                    <td className="p-4 text-right" onClick={e => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1.5">
                        {/* View Button */}
                        <button
                          onClick={() => setViewModalPlan(plan)}
                          className="p-1.5 bg-white/5 hover:bg-white/10 rounded-lg text-zinc-400 hover:text-white transition"
                          title="View Plan Details"
                        >
                          <Eye className="w-3.5 h-3.5" />
                        </button>

                        {/* Edit Button */}
                        <button
                          onClick={() => openEditModal(plan)}
                          className="p-1.5 bg-white/5 hover:bg-white/10 rounded-lg text-zinc-400 hover:text-[#facc15] transition"
                          title="Edit Plan"
                        >
                          <Edit2 className="w-3.5 h-3.5" />
                        </button>

                        {/* Toggle Active Button */}
                        <button
                          onClick={() => handleToggleActive(plan)}
                          className={`p-1.5 rounded-lg transition ${
                            plan.is_active
                              ? "bg-amber-500/10 hover:bg-amber-500/20 text-amber-400"
                              : "bg-green-500/10 hover:bg-green-500/20 text-green-400"
                          }`}
                          title={plan.is_active ? "Deactivate Plan" : "Activate Plan"}
                        >
                          <Power className="w-3.5 h-3.5" />
                        </button>

                        {/* Delete Button */}
                        <button
                          onClick={() => handleDeleteClick(plan)}
                          className="p-1.5 bg-red-500/10 hover:bg-red-500/20 rounded-lg text-red-400 hover:text-red-300 transition"
                          title="Delete Plan"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Footer */}
        {filteredPlans.length > pageSize && (
          <div className="p-4 border-t border-white/10 flex items-center justify-between text-xs text-zinc-400">
            <div>
              Showing {(currentPage - 1) * pageSize + 1} to{" "}
              {Math.min(currentPage * pageSize, filteredPlans.length)} of {filteredPlans.length} plans
            </div>
            <div className="flex items-center gap-2">
              <button
                disabled={currentPage <= 1}
                onClick={() => setCurrentPage(p => Math.max(1, p - 1))}
                className="p-1.5 bg-white/5 hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-zinc-300 transition"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <span className="font-semibold text-white px-2">
                Page {currentPage} of {totalPages}
              </span>
              <button
                disabled={currentPage >= totalPages}
                onClick={() => setCurrentPage(p => Math.min(totalPages, p + 1))}
                className="p-1.5 bg-white/5 hover:bg-white/10 disabled:opacity-40 disabled:cursor-not-allowed rounded-lg text-zinc-300 transition"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>

      {/* =========================================================================
          VIEW PLAN MODAL
          ========================================================================= */}
      {viewModalPlan && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-xl shadow-2xl relative text-sm max-h-[90vh] overflow-y-auto">
            <button
              onClick={() => setViewModalPlan(null)}
              className="absolute top-4 right-4 p-2 bg-white/5 hover:bg-white/10 rounded-full transition text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="flex items-center gap-2 mb-1">
              <h2 className="text-xl font-bold">{viewModalPlan.name}</h2>
              <span
                className={`px-2 py-0.5 text-[9px] font-bold rounded-full ${
                  viewModalPlan.is_active
                    ? "bg-green-500/10 text-green-400 border border-green-500/20"
                    : "bg-red-500/10 text-red-400 border border-red-500/20"
                }`}
              >
                {viewModalPlan.is_active ? "ACTIVE" : "INACTIVE"}
              </span>
            </div>
            <p className="text-zinc-500 text-xs mb-6 font-mono">{viewModalPlan.slug}</p>

            <div className="space-y-4">
              <div>
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">
                  Description
                </div>
                <div className="bg-black/40 p-3 rounded-xl border border-white/5 text-zinc-300 text-xs leading-relaxed">
                  {viewModalPlan.description || "No description provided."}
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div className="bg-black/40 p-3 rounded-xl border border-white/5">
                  <div className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">Price</div>
                  <div className="text-base font-bold text-[#facc15] mt-1">
                    {viewModalPlan.currency} {parseFloat(viewModalPlan.price).toLocaleString()}
                  </div>
                </div>

                <div className="bg-black/40 p-3 rounded-xl border border-white/5">
                  <div className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">Interval</div>
                  <div className="text-sm font-bold text-white mt-1">
                    {viewModalPlan.billing_interval}
                  </div>
                </div>

                <div className="bg-black/40 p-3 rounded-xl border border-white/5">
                  <div className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider">Subscribers</div>
                  <div className="text-sm font-bold text-zinc-200 mt-1 flex items-center gap-1">
                    <Users className="w-3.5 h-3.5 text-[#facc15]" />
                    {viewModalPlan.subscriber_count ?? 0} students
                  </div>
                </div>
              </div>

              <div className="pt-4 border-t border-white/5">
                <div className="text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-2 flex items-center justify-between">
                  <span>Bundled Courses ({viewModalPlan.courses.length})</span>
                  <span className="text-[11px] text-zinc-500 font-normal">
                    Granted to all subscribers of this plan
                  </span>
                </div>

                <div className="space-y-2 max-h-56 overflow-y-auto pr-1">
                  {viewModalPlan.courses.length === 0 ? (
                    <div className="text-zinc-600 italic text-xs p-4 text-center border border-dashed border-white/5 rounded-xl">
                      No courses bundled into this plan yet.
                    </div>
                  ) : (
                    viewModalPlan.courses.map(course => (
                      <div
                        key={course.id}
                        className="bg-black/40 p-3 rounded-xl border border-white/5 flex items-center justify-between gap-3"
                      >
                        <div>
                          <div className="font-semibold text-white text-xs">{course.title}</div>
                          <div className="text-zinc-500 text-[10px] mt-0.5">{course.course_type}</div>
                        </div>
                        <div className="text-right shrink-0 flex items-center gap-2">
                          <span className="font-bold text-[#facc15] text-xs">
                            ₹{parseFloat(course.price).toLocaleString()}
                          </span>
                          {course.is_published ? (
                            <span className="px-2 py-0.5 text-[9px] font-bold rounded-full bg-green-500/10 text-green-400 border border-green-500/20">
                              PUBLISHED
                            </span>
                          ) : (
                            <span className="px-2 py-0.5 text-[9px] font-bold rounded-full bg-zinc-500/10 text-zinc-400 border border-zinc-500/20">
                              DRAFT
                            </span>
                          )}
                        </div>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>

            <div className="mt-6 pt-4 border-t border-white/5 flex justify-end gap-2">
              <button
                onClick={() => {
                  const planToEdit = viewModalPlan;
                  setViewModalPlan(null);
                  openEditModal(planToEdit);
                }}
                className="px-4 py-2 bg-white/5 hover:bg-white/10 rounded-xl text-xs font-semibold text-white transition inline-flex items-center gap-1.5"
              >
                <Edit2 className="w-3.5 h-3.5 text-[#facc15]" />
                Edit Plan
              </button>
              <button
                onClick={() => setViewModalPlan(null)}
                className="px-4 py-2 bg-[#facc15] hover:bg-[#eab308] text-black font-bold text-xs rounded-xl transition"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* =========================================================================
          CREATE PLAN MODAL
          ========================================================================= */}
      {isCreateModalOpen && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-2xl shadow-2xl relative text-sm max-h-[90vh] overflow-y-auto">
            <button
              onClick={() => setIsCreateModalOpen(false)}
              className="absolute top-4 right-4 p-2 bg-white/5 hover:bg-white/10 rounded-full transition text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="flex items-center gap-2 mb-1">
              <h2 className="text-xl font-bold">Create Subscription Plan</h2>
              <span className="px-2.5 py-0.5 text-[10px] font-semibold rounded-full bg-[#facc15]/10 text-[#facc15] border border-[#facc15]/20">
                New Catalog Offering
              </span>
            </div>
            <p className="text-zinc-400 text-xs mb-6">
              Define a new recurring subscription product with price, cycle interval, and included course bundles.
            </p>

            {formError && (
              <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-xl mb-4 text-xs">
                {formError}
              </div>
            )}

            <form onSubmit={handleCreateSubmit} className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="sm:col-span-2">
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">
                    Plan Name *
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. Kathak Masterclass All-Access"
                    value={formName}
                    onChange={e => setFormName(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-[#facc15]"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">
                    Billing Interval *
                  </label>
                  <select
                    value={formBillingInterval}
                    onChange={e => setFormBillingInterval(e.target.value as any)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-[#facc15]"
                  >
                    <option value="MONTHLY">Monthly (recurring every month)</option>
                    <option value="YEARLY">Yearly (recurring every 12 months)</option>
                  </select>
                  <span className="text-[10px] text-zinc-500 mt-1 block">
                    Supported recurring cycles for commercial subscription plans.
                  </span>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">
                    Price (INR) *
                  </label>
                  <div className="relative">
                    <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-500 text-xs font-bold">
                      ₹
                    </span>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      required
                      placeholder="1499.00"
                      value={formPrice}
                      onChange={e => setFormPrice(e.target.value)}
                      className="w-full bg-black/40 border border-white/10 rounded-xl pl-8 pr-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                </div>

                <div className="sm:col-span-2">
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">
                    Description
                  </label>
                  <textarea
                    rows={3}
                    placeholder="Describe what access or features students receive under this plan..."
                    value={formDescription}
                    onChange={e => setFormDescription(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl p-3 text-xs text-white focus:outline-none focus:border-[#facc15]"
                  />
                </div>

                <div className="sm:col-span-2 flex items-center gap-2 p-3 bg-black/40 border border-white/5 rounded-xl">
                  <input
                    type="checkbox"
                    id="create-is-active"
                    checked={formIsActive}
                    onChange={e => setFormIsActive(e.target.checked)}
                    className="rounded border-zinc-700 text-[#facc15] focus:ring-[#facc15] w-4 h-4"
                  />
                  <label htmlFor="create-is-active" className="text-xs text-zinc-300 font-medium cursor-pointer">
                    Publish immediately (Active plan available for subscription)
                  </label>
                </div>
              </div>

              {/* Course Bundle Selection */}
              <div className="pt-4 border-t border-white/10">
                <div className="flex items-center justify-between mb-2">
                  <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                    Bundled Courses ({formSelectedCourseIds.length} selected)
                  </label>
                  <span className="text-[10px] text-zinc-500">
                    Courses automatically unlocked for plan subscribers
                  </span>
                </div>

                <div className="relative mb-3">
                  <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
                  <input
                    type="text"
                    placeholder="Filter courses to bundle..."
                    value={formCourseSearch}
                    onChange={e => setFormCourseSearch(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl pl-9 pr-3.5 py-1.5 text-xs text-white focus:outline-none focus:border-[#facc15]"
                  />
                </div>

                <div className="border border-white/10 rounded-xl max-h-48 overflow-y-auto divide-y divide-white/5 bg-black/20">
                  {loadingCourses ? (
                    <div className="p-4 text-center text-zinc-500 text-xs">Loading courses...</div>
                  ) : filteredAvailableCourses.length === 0 ? (
                    <div className="p-4 text-center text-zinc-500 text-xs">No matching courses found.</div>
                  ) : (
                    filteredAvailableCourses.map(course => {
                      const isSelected = formSelectedCourseIds.includes(course.id);
                      return (
                        <div
                          key={course.id}
                          onClick={() => toggleCourseSelection(course.id)}
                          className={`p-2.5 flex items-center justify-between gap-2 cursor-pointer transition text-xs ${
                            isSelected ? "bg-[#facc15]/10 text-white" : "hover:bg-white/5 text-zinc-300"
                          }`}
                        >
                          <div className="flex items-center gap-2.5">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => {}}
                              className="rounded border-zinc-700 text-[#facc15] focus:ring-[#facc15] w-3.5 h-3.5"
                            />
                            <div>
                              <div className="font-semibold text-xs">{course.title}</div>
                              <div className="text-[10px] text-zinc-500">{course.course_type || "Course"}</div>
                            </div>
                          </div>
                          <div className="text-right shrink-0">
                            <span className="font-bold text-[#facc15] text-[11px]">
                              ₹{parseFloat(String(course.price)).toLocaleString()}
                            </span>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              <div className="mt-6 pt-4 border-t border-white/10 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setIsCreateModalOpen(false)}
                  className="px-4 py-2.5 bg-white/5 hover:bg-white/10 rounded-xl text-xs font-semibold text-zinc-300 transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={formSubmitting}
                  className="px-5 py-2.5 bg-[#facc15] hover:bg-[#eab308] disabled:opacity-50 text-black font-bold text-xs rounded-xl shadow-lg shadow-yellow-500/10 transition inline-flex items-center gap-2"
                >
                  {formSubmitting && <div className="w-3.5 h-3.5 border-2 border-black border-t-transparent rounded-full animate-spin" />}
                  Create Plan
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* =========================================================================
          EDIT PLAN MODAL
          ========================================================================= */}
      {editModalPlan && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-2xl shadow-2xl relative text-sm max-h-[90vh] overflow-y-auto">
            <button
              onClick={() => setEditModalPlan(null)}
              className="absolute top-4 right-4 p-2 bg-white/5 hover:bg-white/10 rounded-full transition text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="flex items-center gap-2 mb-1">
              <h2 className="text-xl font-bold">Edit Plan: {editModalPlan.name}</h2>
            </div>
            <p className="text-zinc-400 text-xs mb-6">
              Update pricing, cycle intervals, and course bundle assignments for this plan.
            </p>

            {formError && (
              <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-xl mb-4 text-xs">
                {formError}
              </div>
            )}

            <form onSubmit={handleEditSubmit} className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="sm:col-span-2">
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">
                    Plan Name *
                  </label>
                  <input
                    type="text"
                    required
                    value={formName}
                    onChange={e => setFormName(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-[#facc15]"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">
                    Billing Interval *
                  </label>
                  <select
                    value={formBillingInterval}
                    onChange={e => setFormBillingInterval(e.target.value as any)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-[#facc15]"
                  >
                    <option value="MONTHLY">Monthly (recurring every month)</option>
                    <option value="YEARLY">Yearly (recurring every 12 months)</option>
                  </select>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">
                    Price (INR) *
                  </label>
                  <div className="relative">
                    <span className="absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-500 text-xs font-bold">
                      ₹
                    </span>
                    <input
                      type="number"
                      step="0.01"
                      min="0"
                      required
                      value={formPrice}
                      onChange={e => setFormPrice(e.target.value)}
                      className="w-full bg-black/40 border border-white/10 rounded-xl pl-8 pr-3.5 py-2.5 text-xs text-white focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                </div>

                <div className="sm:col-span-2">
                  <label className="block text-xs font-semibold text-zinc-400 uppercase tracking-wider mb-1">
                    Description
                  </label>
                  <textarea
                    rows={3}
                    value={formDescription}
                    onChange={e => setFormDescription(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl p-3 text-xs text-white focus:outline-none focus:border-[#facc15]"
                  />
                </div>

                <div className="sm:col-span-2 flex items-center gap-2 p-3 bg-black/40 border border-white/5 rounded-xl">
                  <input
                    type="checkbox"
                    id="edit-is-active"
                    checked={formIsActive}
                    onChange={e => setFormIsActive(e.target.checked)}
                    className="rounded border-zinc-700 text-[#facc15] focus:ring-[#facc15] w-4 h-4"
                  />
                  <label htmlFor="edit-is-active" className="text-xs text-zinc-300 font-medium cursor-pointer">
                    Plan Active (Available for student subscriptions)
                  </label>
                </div>
              </div>

              {/* Course Bundle Selection */}
              <div className="pt-4 border-t border-white/10">
                <div className="flex items-center justify-between mb-2">
                  <label className="text-xs font-semibold text-zinc-400 uppercase tracking-wider">
                    Bundled Courses ({formSelectedCourseIds.length} selected)
                  </label>
                  <span className="text-[10px] text-zinc-500">Modify courses assigned to this bundle</span>
                </div>

                <div className="relative mb-3">
                  <Search className="w-3.5 h-3.5 absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
                  <input
                    type="text"
                    placeholder="Filter courses to bundle..."
                    value={formCourseSearch}
                    onChange={e => setFormCourseSearch(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl pl-9 pr-3.5 py-1.5 text-xs text-white focus:outline-none focus:border-[#facc15]"
                  />
                </div>

                <div className="border border-white/10 rounded-xl max-h-48 overflow-y-auto divide-y divide-white/5 bg-black/20">
                  {loadingCourses ? (
                    <div className="p-4 text-center text-zinc-500 text-xs">Loading courses...</div>
                  ) : filteredAvailableCourses.length === 0 ? (
                    <div className="p-4 text-center text-zinc-500 text-xs">No matching courses found.</div>
                  ) : (
                    filteredAvailableCourses.map(course => {
                      const isSelected = formSelectedCourseIds.includes(course.id);
                      return (
                        <div
                          key={course.id}
                          onClick={() => toggleCourseSelection(course.id)}
                          className={`p-2.5 flex items-center justify-between gap-2 cursor-pointer transition text-xs ${
                            isSelected ? "bg-[#facc15]/10 text-white" : "hover:bg-white/5 text-zinc-300"
                          }`}
                        >
                          <div className="flex items-center gap-2.5">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => {}}
                              className="rounded border-zinc-700 text-[#facc15] focus:ring-[#facc15] w-3.5 h-3.5"
                            />
                            <div>
                              <div className="font-semibold text-xs">{course.title}</div>
                              <div className="text-[10px] text-zinc-500">{course.course_type || "Course"}</div>
                            </div>
                          </div>
                          <div className="text-right shrink-0">
                            <span className="font-bold text-[#facc15] text-[11px]">
                              ₹{parseFloat(String(course.price)).toLocaleString()}
                            </span>
                          </div>
                        </div>
                      );
                    })
                  )}
                </div>
              </div>

              <div className="mt-6 pt-4 border-t border-white/10 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setEditModalPlan(null)}
                  className="px-4 py-2.5 bg-white/5 hover:bg-white/10 rounded-xl text-xs font-semibold text-zinc-300 transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={formSubmitting}
                  className="px-5 py-2.5 bg-[#facc15] hover:bg-[#eab308] disabled:opacity-50 text-black font-bold text-xs rounded-xl shadow-lg shadow-yellow-500/10 transition inline-flex items-center gap-2"
                >
                  {formSubmitting && <div className="w-3.5 h-3.5 border-2 border-black border-t-transparent rounded-full animate-spin" />}
                  Save Changes
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* =========================================================================
          DELETE SAFEGUARD DIALOG (Subscribers > 0)
          ========================================================================= */}
      {deleteSafeguardPlan && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-amber-500/30 p-6 rounded-2xl w-full max-w-md shadow-2xl relative text-sm">
            <button
              onClick={() => setDeleteSafeguardPlan(null)}
              className="absolute top-4 right-4 p-2 bg-white/5 hover:bg-white/10 rounded-full transition text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="w-12 h-12 rounded-2xl bg-amber-500/10 border border-amber-500/20 flex items-center justify-center mb-4 text-amber-400">
              <ShieldAlert className="w-6 h-6" />
            </div>

            <h2 className="text-lg font-bold text-white mb-2">Plan Cannot Be Deleted</h2>
            <p className="text-zinc-300 text-xs leading-relaxed mb-4">
              <strong>{deleteSafeguardPlan.name}</strong> currently has{" "}
              <span className="text-[#facc15] font-bold">
                {deleteSafeguardPlan.subscriber_count} active or historical student subscription(s)
              </span>
              . Deleting this plan would compromise historical student billing records and payment audit history.
            </p>

            <div className="bg-black/40 border border-white/5 p-3 rounded-xl text-xs text-zinc-400 mb-6">
              <div className="flex items-start gap-2">
                <Info className="w-4 h-4 text-[#facc15] shrink-0 mt-0.5" />
                <span>
                  <strong>Safe Alternative:</strong> Deactivate the plan instead. This hides it from new student signups while safely preserving all existing student enrollments and invoice history.
                </span>
              </div>
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setDeleteSafeguardPlan(null)}
                className="px-4 py-2 bg-white/5 hover:bg-white/10 rounded-xl text-xs font-semibold text-zinc-300 transition"
              >
                Cancel
              </button>
              {deleteSafeguardPlan.is_active && (
                <button
                  onClick={() => handleToggleActive(deleteSafeguardPlan)}
                  className="px-4 py-2 bg-amber-500 hover:bg-amber-600 text-black font-bold text-xs rounded-xl shadow-lg transition inline-flex items-center gap-1.5"
                >
                  <Power className="w-3.5 h-3.5" />
                  Deactivate Plan Instead
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* =========================================================================
          CONFIRM DELETE DIALOG (Subscribers === 0)
          ========================================================================= */}
      {confirmDeletePlan && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-red-500/30 p-6 rounded-2xl w-full max-w-md shadow-2xl relative text-sm">
            <button
              onClick={() => setConfirmDeletePlan(null)}
              className="absolute top-4 right-4 p-2 bg-white/5 hover:bg-white/10 rounded-full transition text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <div className="w-12 h-12 rounded-2xl bg-red-500/10 border border-red-500/20 flex items-center justify-center mb-4 text-red-400">
              <Trash2 className="w-6 h-6" />
            </div>

            <h2 className="text-lg font-bold text-white mb-2">Delete Subscription Plan?</h2>
            <p className="text-zinc-300 text-xs leading-relaxed mb-6">
              Are you sure you want to delete <strong>{confirmDeletePlan.name}</strong>? This plan has no assigned subscribers. This action cannot be undone.
            </p>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => setConfirmDeletePlan(null)}
                className="px-4 py-2 bg-white/5 hover:bg-white/10 rounded-xl text-xs font-semibold text-zinc-300 transition"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDelete}
                className="px-4 py-2 bg-red-500 hover:bg-red-600 text-white font-bold text-xs rounded-xl shadow-lg transition"
              >
                Confirm Delete
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
