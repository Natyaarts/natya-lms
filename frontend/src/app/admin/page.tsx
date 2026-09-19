"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { DollarSign, Users, Award, BookOpen, Clock, Calendar, CheckCircle2, UserCog, Repeat, Banknote, Undo2, ClipboardCheck, Radio } from "lucide-react";

// Production incident fix: AdminStatsView's response is trusted to always
// include every numeric field, but a version-skewed deployment (frontend
// ahead of backend, or a future backend regression) can return a payload
// missing one -- e.g. total_mentors/active_subscriptions_count/etc. were
// added to AdminStatsView after this page started reading them, so an
// older backend build's 200 response simply omits them, leaving
// stats.total_mentors undefined and crashing on .toLocaleString(). This
// coerces any non-number (undefined, null, NaN) to 0 rather than throwing.
const fmtNum = (value: unknown): string => (typeof value === "number" && !isNaN(value) ? value : 0).toLocaleString();

export default function AdminDashboard() {
  const [stats, setStats] = useState<any>({
    total_students: 0,
    new_students_week: 0,
    new_students_month: 0,
    active_students: 0,
    inactive_students: 0,
    total_teachers: 0,
    
    total_courses: 0,
    active_courses: 0,
    draft_courses: 0,
    top_courses: [],
    
    total_revenue: 0.00,
    current_month_revenue: 0.00,
    success_payments: 0,
    pending_payments: 0,
    failed_payments: 0,
    revenue_breakdown: [],
    
    total_enrollments: 0,
    new_enrollments_month: 0,
    paid_enrollments_count: 0,
    manual_enrollments_count: 0,
    
    recent_registrations: [],
    recent_payments: [],
    recent_enrollments: [],

    // Custom Admin Dashboard Completion additions -- all computed live by
    // AdminStatsView from existing models, never hardcoded/mocked here.
    total_mentors: 0,
    active_subscriptions_count: 0,
    draft_payouts_count: 0,
    approved_payouts_pending_count: 0,
    pending_refunds_count: 0,
    pending_assignment_grading_count: 0,
    upcoming_live_classes_count: 0,
    recent_refunds: [],
    upcoming_live_classes: [],
  });
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState<"registrations" | "payments" | "enrollments">("registrations");

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-stats/`, {
          credentials: "include"
        });
        if (res.ok) {
          const data = await res.json();
          // Merge over the existing (safe-default) state instead of
          // replacing it outright -- a response missing a field (e.g. an
          // older backend build that predates a newer stat) must fall back
          // to its 0/[] default, not become undefined. See fmtNum above.
          setStats((prev: any) => ({ ...prev, ...data }));
        }
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchStats();
  }, []);

  // Calculate percentages for enrollment breakdown
  const paidCount = stats.paid_enrollments_count || 0;
  const manualCount = stats.manual_enrollments_count || 0;
  const totalEnrollments = paidCount + manualCount || 1; // avoid divide by zero
  const paidPct = Math.round((paidCount / totalEnrollments) * 100);
  const manualPct = 100 - paidPct;

  // Max value in monthly revenue for scaling bars
  const maxRevenue = (stats.revenue_breakdown || []).reduce((max: number, item: any) => (item.total > max ? item.total : max), 0) || 1;

  return (
    <div className="animate-in fade-in slide-in-from-bottom-4 duration-500 font-sans text-white pb-20 max-w-6xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Admin Overview</h1>
        <p className="text-zinc-400 text-sm mt-1">Real-time analytical summaries and masterclass registration ledgers.</p>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center p-32 gap-3">
          <div className="w-8 h-8 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
          <span className="text-sm text-zinc-500">Compiling analytics data...</span>
        </div>
      ) : (
        <div className="space-y-8">
          {/* Overview Stats Grid */}
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
            {/* Card 1: Revenue */}
            <div className="bg-zinc-900 border border-white/5 p-5 rounded-2xl shadow-xl hover:border-white/10 transition-colors">
              <div className="flex justify-between items-start mb-4">
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">Total Revenue</div>
                <div className="p-2 bg-emerald-500/10 text-emerald-400 rounded-xl">
                  <DollarSign className="w-4.5 h-4.5" />
                </div>
              </div>
              <div className="text-3xl font-extrabold text-white tracking-tight">
                ₹{fmtNum(stats.total_revenue)}
              </div>
              <div className="text-[10px] text-emerald-400 mt-2 flex items-center gap-1 font-medium">
                <span>₹{fmtNum(stats.current_month_revenue)}</span>
                <span className="text-zinc-500 font-normal">collected this month</span>
              </div>
            </div>

            {/* Card 2: Students */}
            <div className="bg-zinc-900 border border-white/5 p-5 rounded-2xl shadow-xl hover:border-white/10 transition-colors">
              <div className="flex justify-between items-start mb-4">
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">Total Students</div>
                <div className="p-2 bg-blue-500/10 text-blue-400 rounded-xl">
                  <Users className="w-4.5 h-4.5" />
                </div>
              </div>
              <div className="text-3xl font-extrabold text-white tracking-tight">
                {fmtNum(stats.total_students)}
              </div>
              <div className="text-[10px] text-blue-400 mt-2 flex items-center gap-1 font-medium">
                <span>+{stats.new_students_week} students</span>
                <span className="text-zinc-500 font-normal">joined this week</span>
              </div>
            </div>

            {/* Card 3: Teachers */}
            <div className="bg-zinc-900 border border-white/5 p-5 rounded-2xl shadow-xl hover:border-white/10 transition-colors">
              <div className="flex justify-between items-start mb-4">
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">Instructors</div>
                <div className="p-2 bg-[#facc15]/10 text-[#facc15] rounded-xl">
                  <Award className="w-4.5 h-4.5" />
                </div>
              </div>
              <div className="text-3xl font-extrabold text-white tracking-tight">
                {fmtNum(stats.total_teachers)}
              </div>
              <div className="text-[10px] text-zinc-500 mt-2 font-normal">
                Active teacher dashboards in database
              </div>
            </div>

            {/* Card 4: Enrollments */}
            <div className="bg-zinc-900 border border-white/5 p-5 rounded-2xl shadow-xl hover:border-white/10 transition-colors">
              <div className="flex justify-between items-start mb-4">
                <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider">Enrollments</div>
                <div className="p-2 bg-purple-500/10 text-purple-400 rounded-xl">
                  <BookOpen className="w-4.5 h-4.5" />
                </div>
              </div>
              <div className="text-3xl font-extrabold text-white tracking-tight">
                {fmtNum(stats.total_enrollments)}
              </div>
              <div className="text-[10px] text-purple-400 mt-2 flex items-center gap-1 font-medium">
                <span>+{stats.new_enrollments_month} enrollments</span>
                <span className="text-zinc-500 font-normal">this month</span>
              </div>
            </div>
          </div>

          {/* Operational Alerts Row -- Custom Admin Dashboard Completion
              additions. Every number here comes straight from
              AdminStatsView; nothing is hardcoded. Each chip links to the
              admin page where that item is actually actioned. */}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-3">
            <Link href="/admin/teachers-mentors" className="bg-zinc-900 border border-white/5 hover:border-white/10 p-4 rounded-2xl shadow-xl transition-colors">
              <div className="p-1.5 bg-indigo-500/10 text-indigo-400 rounded-lg w-fit mb-2"><UserCog className="w-3.5 h-3.5" /></div>
              <div className="text-xl font-extrabold text-white tracking-tight">{fmtNum(stats.total_mentors)}</div>
              <div className="text-[9px] text-zinc-500 uppercase tracking-wider font-semibold mt-1">Mentors</div>
            </Link>
            <Link href="/admin/subscriptions" className="bg-zinc-900 border border-white/5 hover:border-white/10 p-4 rounded-2xl shadow-xl transition-colors">
              <div className="p-1.5 bg-teal-500/10 text-teal-400 rounded-lg w-fit mb-2"><Repeat className="w-3.5 h-3.5" /></div>
              <div className="text-xl font-extrabold text-white tracking-tight">{fmtNum(stats.active_subscriptions_count)}</div>
              <div className="text-[9px] text-zinc-500 uppercase tracking-wider font-semibold mt-1">Active Subs</div>
            </Link>
            <Link href="/admin/payouts" className="bg-zinc-900 border border-white/5 hover:border-white/10 p-4 rounded-2xl shadow-xl transition-colors">
              <div className="p-1.5 bg-yellow-500/10 text-[#facc15] rounded-lg w-fit mb-2"><Banknote className="w-3.5 h-3.5" /></div>
              <div className="text-xl font-extrabold text-white tracking-tight">{fmtNum(stats.draft_payouts_count)}</div>
              <div className="text-[9px] text-zinc-500 uppercase tracking-wider font-semibold mt-1">Draft Payouts</div>
            </Link>
            <Link href="/admin/payouts" className="bg-zinc-900 border border-white/5 hover:border-white/10 p-4 rounded-2xl shadow-xl transition-colors">
              <div className="p-1.5 bg-blue-500/10 text-blue-400 rounded-lg w-fit mb-2"><Banknote className="w-3.5 h-3.5" /></div>
              <div className="text-xl font-extrabold text-white tracking-tight">{fmtNum(stats.approved_payouts_pending_count)}</div>
              <div className="text-[9px] text-zinc-500 uppercase tracking-wider font-semibold mt-1">Payouts to Pay</div>
            </Link>
            <Link href="/admin/refunds" className="bg-zinc-900 border border-white/5 hover:border-white/10 p-4 rounded-2xl shadow-xl transition-colors">
              <div className="p-1.5 bg-red-500/10 text-red-400 rounded-lg w-fit mb-2"><Undo2 className="w-3.5 h-3.5" /></div>
              <div className="text-xl font-extrabold text-white tracking-tight">{fmtNum(stats.pending_refunds_count)}</div>
              <div className="text-[9px] text-zinc-500 uppercase tracking-wider font-semibold mt-1">Pending Refunds</div>
            </Link>
            <Link href="/admin/assignments" className="bg-zinc-900 border border-white/5 hover:border-white/10 p-4 rounded-2xl shadow-xl transition-colors">
              <div className="p-1.5 bg-orange-500/10 text-orange-400 rounded-lg w-fit mb-2"><ClipboardCheck className="w-3.5 h-3.5" /></div>
              <div className="text-xl font-extrabold text-white tracking-tight">{fmtNum(stats.pending_assignment_grading_count)}</div>
              <div className="text-[9px] text-zinc-500 uppercase tracking-wider font-semibold mt-1">Needs Grading</div>
            </Link>
            <Link href="/admin/live-classes" className="bg-zinc-900 border border-white/5 hover:border-white/10 p-4 rounded-2xl shadow-xl transition-colors">
              <div className="p-1.5 bg-purple-500/10 text-purple-400 rounded-lg w-fit mb-2"><Radio className="w-3.5 h-3.5" /></div>
              <div className="text-xl font-extrabold text-white tracking-tight">{fmtNum(stats.upcoming_live_classes_count)}</div>
              <div className="text-[9px] text-zinc-500 uppercase tracking-wider font-semibold mt-1">Upcoming Classes</div>
            </Link>
          </div>

          {/* Revenue and Enrollment charts row */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Monthly Revenue Bar Chart (Left 2/3 width) */}
            <div className="lg:col-span-2 bg-zinc-900 border border-white/5 p-6 rounded-2xl shadow-xl">
              <h3 className="text-lg font-bold mb-1">Monthly Billing Trends</h3>
              <p className="text-zinc-500 text-xs mb-8">Completed checkouts logs aggregated monthly.</p>

              {(stats.revenue_breakdown || []).length === 0 ? (
                <div className="h-48 flex items-center justify-center text-zinc-500 text-sm">
                  No billing history to display.
                </div>
              ) : (
                <div className="flex flex-col gap-5">
                  {(stats.revenue_breakdown || []).map((item: any, idx: number) => {
                    const widthPct = Math.max(8, Math.round((item.total / maxRevenue) * 100));
                    return (
                      <div key={idx} className="flex items-center gap-4 text-xs">
                        <div className="w-28 text-zinc-400 font-medium truncate">{item.month}</div>
                        <div className="flex-1 bg-black/40 h-6 rounded-lg overflow-hidden border border-white/5 relative">
                          <div
                            style={{ width: `${widthPct}%` }}
                            className="bg-gradient-to-r from-emerald-500/80 to-emerald-400/80 h-full rounded-r-md transition-all duration-1000"
                          />
                        </div>
                        <div className="w-20 text-right font-bold text-white">
                          ₹{fmtNum(item.total)}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Registration Sources Card (Right 1/3 width) */}
            <div className="lg:col-span-1 bg-zinc-900 border border-white/5 p-6 rounded-2xl shadow-xl flex flex-col justify-between">
              <div>
                <h3 className="text-lg font-bold mb-1">Enrollment Sources</h3>
                <p className="text-zinc-500 text-xs mb-6">Distribution between checkout payments and free admin grants.</p>

                {/* Progress bar split */}
                <div className="space-y-4 pt-4">
                  {/* Paid */}
                  <div className="space-y-1.5 text-xs">
                    <div className="flex justify-between items-center text-zinc-400">
                      <span className="font-semibold text-green-400 flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full bg-green-500" />
                        Paid (Checkout)
                      </span>
                      <span className="font-bold text-white">{paidPct}% ({paidCount})</span>
                    </div>
                    <div className="w-full bg-black/40 h-3 border border-white/5 rounded-full overflow-hidden">
                      <div style={{ width: `${paidPct}%` }} className="bg-green-500 h-full transition-all duration-1000" />
                    </div>
                  </div>

                  {/* Manual */}
                  <div className="space-y-1.5 text-xs">
                    <div className="flex justify-between items-center text-zinc-400">
                      <span className="font-semibold text-zinc-400 flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full bg-zinc-600" />
                        Manual / Free
                      </span>
                      <span className="font-bold text-white">{manualPct}% ({manualCount})</span>
                    </div>
                    <div className="w-full bg-black/40 h-3 border border-white/5 rounded-full overflow-hidden">
                      <div style={{ width: `${manualPct}%` }} className="bg-zinc-600 h-full transition-all duration-1000" />
                    </div>
                  </div>
                </div>
              </div>

              <div className="mt-8 pt-4 border-t border-white/5 text-[10px] text-zinc-500">
                Calculated dynamically by analyzing transaction signatures.
              </div>
            </div>
          </div>

          {/* Top Courses and Recent Activity Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Popular Courses Leaderboard */}
            <div className="lg:col-span-1 bg-zinc-900 border border-white/5 p-6 rounded-2xl shadow-xl">
              <h3 className="text-lg font-bold mb-1">Top Masterclasses</h3>
              <p className="text-zinc-500 text-xs mb-6">Highest registered classes by student count.</p>

              <div className="space-y-4">
                {(stats.top_courses || []).length === 0 ? (
                  <div className="p-8 text-center text-zinc-600 text-xs">No course enrollments logged.</div>
                ) : (
                  (stats.top_courses || []).map((course: any, idx: number) => (
                    <div key={course.id} className="flex items-center justify-between text-xs">
                      <div className="flex items-center gap-3 min-w-0">
                        <span className="w-5 h-5 flex items-center justify-center font-bold bg-white/5 border border-white/10 rounded-md text-zinc-400 shrink-0">
                          {idx + 1}
                        </span>
                        <div className="font-semibold text-white truncate max-w-[180px]">{course.title}</div>
                      </div>
                      <div className="px-2 py-1 bg-purple-500/10 border border-purple-500/20 text-purple-400 font-bold rounded-lg shrink-0">
                        {course.enrollments} enrolls
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>

            {/* Recent Activity Tabbed Logger */}
            <div className="lg:col-span-2 bg-zinc-900 border border-white/5 rounded-2xl shadow-xl overflow-hidden flex flex-col justify-between">
              <div>
                <div className="px-6 py-5 border-b border-white/5 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
                  <div>
                    <h3 className="text-base font-bold text-white">Platform Logs Feed</h3>
                    <p className="text-zinc-500 text-[10px] mt-0.5">Real-time database triggers for latest events.</p>
                  </div>
                  <div className="flex bg-black/40 border border-white/5 rounded-xl p-1 text-xs shrink-0 self-start sm:self-auto">
                    <button
                      onClick={() => setActiveTab("registrations")}
                      className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
                        activeTab === "registrations" ? "bg-[#facc15] text-black font-bold" : "text-zinc-400 hover:text-white"
                      }`}
                    >
                      Signups
                    </button>
                    <button
                      onClick={() => setActiveTab("payments")}
                      className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
                        activeTab === "payments" ? "bg-[#facc15] text-black font-bold" : "text-zinc-400 hover:text-white"
                      }`}
                    >
                      Purchases
                    </button>
                    <button
                      onClick={() => setActiveTab("enrollments")}
                      className={`px-3 py-1.5 rounded-lg font-medium transition-all ${
                        activeTab === "enrollments" ? "bg-[#facc15] text-black font-bold" : "text-zinc-400 hover:text-white"
                      }`}
                    >
                      Enrolls
                    </button>
                  </div>
                </div>

                <div className="p-6">
                  {/* Tab 1: Registrations */}
                  {activeTab === "registrations" && (
                    <div className="space-y-4">
                      {(stats.recent_registrations || []).length === 0 ? (
                        <div className="text-center py-8 text-zinc-500 text-xs">No recent student registrations.</div>
                      ) : (
                        (stats.recent_registrations || []).map((u: any, idx: number) => (
                          <div key={idx} className="flex items-center justify-between text-xs hover:bg-white/5 p-2 rounded-xl transition-colors">
                            <div className="min-w-0">
                              <div className="font-bold text-white">{u.name}</div>
                              <div className="text-zinc-500 text-[10px] mt-0.5">{u.email}</div>
                            </div>
                            <div className="text-zinc-500 flex items-center gap-1 font-medium font-mono">
                              <Clock className="w-3.5 h-3.5" />
                              {new Date(u.date_joined).toLocaleDateString()}
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  )}

                  {/* Tab 2: Payments */}
                  {activeTab === "payments" && (
                    <div className="space-y-4">
                      {(stats.recent_payments || []).length === 0 ? (
                        <div className="text-center py-8 text-zinc-500 text-xs">No recent purchase transactions.</div>
                      ) : (
                        (stats.recent_payments || []).map((p: any, idx: number) => (
                          <div key={idx} className="flex items-center justify-between text-xs hover:bg-white/5 p-2 rounded-xl transition-colors">
                            <div className="min-w-0">
                              <div className="font-bold text-white">{p.student_name}</div>
                              <div className="text-zinc-500 text-[10px] mt-0.5 truncate max-w-[200px]">{p.course_title}</div>
                            </div>
                            <div className="text-right shrink-0">
                              <div className="font-bold text-[#facc15]">₹{fmtNum(p.amount)}</div>
                              <span className={`px-2 py-0.5 text-[8px] font-bold rounded-full inline-block mt-1 ${
                                p.status === 'SUCCESS'
                                  ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                                  : 'bg-yellow-500/10 text-[#facc15] border border-yellow-500/20'
                              }`}>
                                {p.status}
                              </span>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  )}

                  {/* Tab 3: Enrollments */}
                  {activeTab === "enrollments" && (
                    <div className="space-y-4">
                      {(stats.recent_enrollments || []).length === 0 ? (
                        <div className="text-center py-8 text-zinc-500 text-xs">No recent student course enrollments.</div>
                      ) : (
                        (stats.recent_enrollments || []).map((e: any, idx: number) => (
                          <div key={idx} className="flex items-center justify-between text-xs hover:bg-white/5 p-2 rounded-xl transition-colors">
                            <div className="min-w-0">
                              <div className="font-bold text-white">{e.student_name}</div>
                              <div className="text-zinc-500 text-[10px] mt-0.5 truncate max-w-[240px]">{e.course_title}</div>
                            </div>
                            <div className="text-zinc-500 flex items-center gap-1 font-medium font-mono shrink-0">
                              <CheckCircle2 className="w-3.5 h-3.5 text-purple-400" />
                              {new Date(e.enrolled_at).toLocaleDateString()}
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </div>
              </div>

              <div className="bg-white/5 px-6 py-4 flex items-center justify-between border-t border-white/5 text-[10px] text-zinc-500 font-medium uppercase tracking-wider">
                <span>Natya LMS Admin Panel Logs Feed</span>
                <span>Active</span>
              </div>
            </div>
          </div>

          {/* Recent Refunds / Upcoming Live Classes -- Custom Admin
              Dashboard Completion additions, same two-column layout as the
              row above. */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <div className="bg-zinc-900 border border-white/5 rounded-2xl shadow-xl overflow-hidden">
              <div className="px-6 py-5 border-b border-white/5">
                <h3 className="text-base font-bold text-white">Recent Refunds</h3>
                <p className="text-zinc-500 text-[10px] mt-0.5">Latest refund requests across all payment sources.</p>
              </div>
              <div className="p-6">
                {(stats.recent_refunds || []).length === 0 ? (
                  <div className="text-center py-8 text-zinc-500 text-xs">No refunds requested yet.</div>
                ) : (
                  <div className="space-y-4">
                    {(stats.recent_refunds || []).map((r: any) => (
                      <div key={r.id} className="flex items-center justify-between text-xs hover:bg-white/5 p-2 rounded-xl transition-colors">
                        <div className="min-w-0">
                          <div className="font-bold text-white">{r.customer_name}</div>
                          <div className="text-zinc-500 text-[10px] mt-0.5 font-mono">
                            {new Date(r.requested_at).toLocaleString()}
                          </div>
                        </div>
                        <div className="text-right shrink-0">
                          <div className="font-bold text-[#facc15]">₹{fmtNum(parseFloat(r.amount))}</div>
                          <span className={`px-2 py-0.5 text-[8px] font-bold rounded-full inline-block mt-1 ${
                            r.status === 'SUCCESS'
                              ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                              : r.status === 'FAILED' || r.status === 'REJECTED'
                              ? 'bg-red-500/10 text-red-400 border border-red-500/20'
                              : 'bg-yellow-500/10 text-[#facc15] border border-yellow-500/20'
                          }`}>
                            {r.status}
                          </span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="bg-white/5 px-6 py-3 border-t border-white/5">
                <Link href="/admin/refunds" className="text-[10px] font-semibold text-zinc-400 hover:text-white uppercase tracking-wider transition-colors">
                  View all refunds &rarr;
                </Link>
              </div>
            </div>

            <div className="bg-zinc-900 border border-white/5 rounded-2xl shadow-xl overflow-hidden">
              <div className="px-6 py-5 border-b border-white/5">
                <h3 className="text-base font-bold text-white">Upcoming Live Classes</h3>
                <p className="text-zinc-500 text-[10px] mt-0.5">Next scheduled sessions across all courses.</p>
              </div>
              <div className="p-6">
                {(stats.upcoming_live_classes || []).length === 0 ? (
                  <div className="text-center py-8 text-zinc-500 text-xs">No upcoming live classes scheduled.</div>
                ) : (
                  <div className="space-y-4">
                    {(stats.upcoming_live_classes || []).map((c: any) => (
                      <div key={c.id} className="flex items-center justify-between text-xs hover:bg-white/5 p-2 rounded-xl transition-colors">
                        <div className="min-w-0">
                          <div className="font-bold text-white truncate max-w-[220px]">{c.title}</div>
                          <div className="text-zinc-500 text-[10px] mt-0.5 truncate max-w-[220px]">{c.course_title}</div>
                        </div>
                        <div className="text-zinc-400 flex items-center gap-1 font-medium font-mono shrink-0">
                          <Calendar className="w-3.5 h-3.5 text-purple-400" />
                          {new Date(c.scheduled_start).toLocaleString()}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="bg-white/5 px-6 py-3 border-t border-white/5">
                <Link href="/admin/live-classes" className="text-[10px] font-semibold text-zinc-400 hover:text-white uppercase tracking-wider transition-colors">
                  View all live classes &rarr;
                </Link>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
