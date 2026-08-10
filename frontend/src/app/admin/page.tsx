"use client";

import { useEffect, useState } from "react";
import { DollarSign, Users, Award, BookOpen, Clock, Calendar, CheckCircle2 } from "lucide-react";

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
    recent_enrollments: []
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
          setStats(data);
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
  const maxRevenue = stats.revenue_breakdown.reduce((max: number, item: any) => (item.total > max ? item.total : max), 0) || 1;

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
                ₹{stats.total_revenue.toLocaleString()}
              </div>
              <div className="text-[10px] text-emerald-400 mt-2 flex items-center gap-1 font-medium">
                <span>₹{stats.current_month_revenue.toLocaleString()}</span>
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
                {stats.total_students.toLocaleString()}
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
                {stats.total_teachers.toLocaleString()}
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
                {stats.total_enrollments.toLocaleString()}
              </div>
              <div className="text-[10px] text-purple-400 mt-2 flex items-center gap-1 font-medium">
                <span>+{stats.new_enrollments_month} enrollments</span>
                <span className="text-zinc-500 font-normal">this month</span>
              </div>
            </div>
          </div>

          {/* Revenue and Enrollment charts row */}
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
            {/* Monthly Revenue Bar Chart (Left 2/3 width) */}
            <div className="lg:col-span-2 bg-zinc-900 border border-white/5 p-6 rounded-2xl shadow-xl">
              <h3 className="text-lg font-bold mb-1">Monthly Billing Trends</h3>
              <p className="text-zinc-500 text-xs mb-8">Completed checkouts logs aggregated monthly.</p>

              {stats.revenue_breakdown.length === 0 ? (
                <div className="h-48 flex items-center justify-center text-zinc-500 text-sm">
                  No billing history to display.
                </div>
              ) : (
                <div className="flex flex-col gap-5">
                  {stats.revenue_breakdown.map((item: any, idx: number) => {
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
                          ₹{item.total.toLocaleString()}
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
                {stats.top_courses.length === 0 ? (
                  <div className="p-8 text-center text-zinc-600 text-xs">No course enrollments logged.</div>
                ) : (
                  stats.top_courses.map((course: any, idx: number) => (
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
                      {stats.recent_registrations.length === 0 ? (
                        <div className="text-center py-8 text-zinc-500 text-xs">No recent student registrations.</div>
                      ) : (
                        stats.recent_registrations.map((u: any, idx: number) => (
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
                      {stats.recent_payments.length === 0 ? (
                        <div className="text-center py-8 text-zinc-500 text-xs">No recent purchase transactions.</div>
                      ) : (
                        stats.recent_payments.map((p: any, idx: number) => (
                          <div key={idx} className="flex items-center justify-between text-xs hover:bg-white/5 p-2 rounded-xl transition-colors">
                            <div className="min-w-0">
                              <div className="font-bold text-white">{p.student_name}</div>
                              <div className="text-zinc-500 text-[10px] mt-0.5 truncate max-w-[200px]">{p.course_title}</div>
                            </div>
                            <div className="text-right shrink-0">
                              <div className="font-bold text-[#facc15]">₹{p.amount.toLocaleString()}</div>
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
                      {stats.recent_enrollments.length === 0 ? (
                        <div className="text-center py-8 text-zinc-500 text-xs">No recent student course enrollments.</div>
                      ) : (
                        stats.recent_enrollments.map((e: any, idx: number) => (
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
        </div>
      )}
    </div>
  );
}
