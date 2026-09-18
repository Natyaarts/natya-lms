"use client";

import { useEffect, useState } from "react";
import { Eye, X, AlertTriangle, CheckCircle2 } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

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
  billing_interval: 'MONTHLY' | 'YEARLY';
  price: string;
  currency: string;
  courses: PlanCourse[];
  is_active: boolean;
}

export default function SubscriptionPlansPage() {
  const [plans, setPlans] = useState<SubscriptionPlan[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [selectedPlan, setSelectedPlan] = useState<SubscriptionPlan | null>(null);

  const fetchPlans = async () => {
    setLoading(true);
    setError("");
    try {
      // GET /api/orders/subscription-plans/ -- bare array, not paginated.
      // Staff/superuser sees every plan (including inactive ones / ones
      // without a linked razorpay_plan_id); everyone else only sees
      // active+linked ones. This is the admin view, so nothing is
      // filtered out here.
      const res = await fetch(`${API_BASE}/api/orders/subscription-plans/`, {
        credentials: "include"
      });

      if (res.ok) {
        const data: SubscriptionPlan[] = await res.json();
        setPlans(Array.isArray(data) ? data : []);
      } else {
        setError("Failed to fetch subscription plans");
      }
    } catch (err) {
      setError("Network error fetching subscription plans");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPlans();
  }, []);

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Subscription Plans</h1>
        <p className="text-zinc-400 text-sm mt-1">All subscription plans and the courses bundled into each one.</p>
      </div>

      {/* Read-only banner -- this ViewSet is a ReadOnlyModelViewSet, there
          is no create/update/delete route at all. Matches this codebase's
          established convention (see SubscriptionPlanSerializer's own
          docstring) of saying so plainly on read-only-by-design pages. */}
      <div className="bg-blue-500/10 border border-blue-500/20 text-blue-300 p-4 rounded-xl mb-6 text-sm flex items-start gap-2">
        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
        Plan creation and editing is managed entirely through the Django admin site, not this dashboard. This page is read-only.
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Plans Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Plan Name</th>
                <th className="p-4 font-semibold">Billing Interval</th>
                <th className="p-4 font-semibold">Price</th>
                <th className="p-4 font-semibold">Linked Courses</th>
                <th className="p-4 font-semibold text-center">Status</th>
                <th className="p-4 font-semibold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading subscription plans...</span>
                    </div>
                  </td>
                </tr>
              ) : plans.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                    No subscription plans found.
                  </td>
                </tr>
              ) : (
                plans.map(plan => (
                  <tr key={plan.id} className="hover:bg-white/5 transition-colors cursor-pointer" onClick={() => setSelectedPlan(plan)}>
                    <td className="p-4">
                      <div className="font-bold text-white text-sm">{plan.name}</div>
                      <div className="text-zinc-500 text-[10px] mt-0.5">{plan.slug}</div>
                    </td>

                    <td className="p-4 text-zinc-400">{plan.billing_interval}</td>

                    <td className="p-4 font-bold text-[#facc15] text-sm">
                      {plan.currency} {parseFloat(plan.price).toLocaleString()}
                    </td>

                    <td className="p-4 text-zinc-300">{plan.courses.length}</td>

                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${
                        plan.is_active
                          ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                          : 'bg-red-500/10 text-red-400 border border-red-500/20'
                      }`}>
                        {plan.is_active ? 'ACTIVE' : 'INACTIVE'}
                      </span>
                    </td>

                    <td className="p-4">
                      <div className="flex items-center justify-center gap-2">
                        <button
                          onClick={(e) => { e.stopPropagation(); setSelectedPlan(plan); }}
                          className="p-2 bg-white/5 hover:bg-white/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-white"
                          title="View Linked Courses"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* No pagination footer -- this endpoint returns a bare array with no
          server-side pagination at all. */}

      {/* Detail Modal */}
      {selectedPlan && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-lg shadow-2xl relative text-sm max-h-[85vh] overflow-y-auto">
            <button
              onClick={() => setSelectedPlan(null)}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1">{selectedPlan.name}</h2>
            <p className="text-zinc-500 text-xs mb-6">{selectedPlan.description || "No description provided."}</p>

            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Price</div>
                  <div className="font-bold text-[#facc15] text-base">
                    {selectedPlan.currency} {parseFloat(selectedPlan.price).toLocaleString()} / {selectedPlan.billing_interval.toLowerCase()}
                  </div>
                </div>
                <div>
                  <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1">Status</div>
                  <div>
                    <span className={`px-2 py-0.5 text-[9px] font-bold rounded-full inline-block mt-0.5 ${
                      selectedPlan.is_active
                        ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                        : 'bg-red-500/10 text-red-400 border border-red-500/20'
                    }`}>
                      {selectedPlan.is_active ? 'ACTIVE' : 'INACTIVE'}
                    </span>
                  </div>
                </div>
              </div>

              <div className="pt-4 border-t border-white/5">
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-2">
                  Linked Courses ({selectedPlan.courses.length})
                </div>
                <div className="space-y-2">
                  {selectedPlan.courses.length === 0 ? (
                    <div className="text-zinc-600 italic text-xs">No courses linked to this plan.</div>
                  ) : (
                    selectedPlan.courses.map(course => (
                      <div key={course.id} className="bg-black/40 p-3 rounded-lg border border-white/5 flex items-center justify-between gap-2">
                        <div>
                          <div className="font-semibold text-white text-xs">{course.title}</div>
                          <div className="text-zinc-500 text-[10px] mt-0.5">{course.course_type}</div>
                        </div>
                        <div className="text-right shrink-0 flex items-center gap-2">
                          <span className="font-bold text-[#facc15] text-xs">₹{parseFloat(course.price).toLocaleString()}</span>
                          {course.is_published ? (
                            <span className="px-2 py-0.5 text-[9px] font-bold rounded-full bg-green-500/10 text-green-400 border border-green-500/20 inline-flex items-center gap-1">
                              <CheckCircle2 className="w-3 h-3" /> PUBLISHED
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
          </div>
        </div>
      )}
    </div>
  );
}
