"use client";

import { useEffect, useState } from "react";

export default function AdminDashboard() {
  const [stats, setStats] = useState({
    total_students: 0,
    active_courses: 0,
    total_revenue: 0.00
  });
  const [loading, setLoading] = useState(true);

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

  return (
    <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Admin Overview</h1>
          <p className="text-zinc-400 text-sm mt-1">Monitor your platform's performance and recent activity.</p>
        </div>
      </div>
      
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-8">
        {/* Stat Card 1 */}
        <div className="bg-[#18181b] border border-white/5 p-5 rounded-xl shadow-sm hover:border-white/10 transition-colors">
          <div className="flex justify-between items-start mb-4">
            <div className="text-sm font-medium text-zinc-400 tracking-wide">Total Students</div>
            <div className="p-2 bg-blue-500/10 text-blue-400 rounded-lg">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
            </div>
          </div>
          <div className="text-3xl font-bold text-white tracking-tight">
            {loading ? "..." : stats.total_students.toLocaleString()}
          </div>
        </div>
        
        {/* Stat Card 2 */}
        <div className="bg-[#18181b] border border-white/5 p-5 rounded-xl shadow-sm hover:border-white/10 transition-colors">
          <div className="flex justify-between items-start mb-4">
            <div className="text-sm font-medium text-zinc-400 tracking-wide">Total Revenue</div>
            <div className="p-2 bg-emerald-500/10 text-emerald-400 rounded-lg">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="1" x2="12" y2="23"/><path d="M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
            </div>
          </div>
          <div className="text-3xl font-bold text-emerald-400 tracking-tight">
            ₹{loading ? "..." : stats.total_revenue.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}
          </div>
        </div>
        
        {/* Stat Card 3 */}
        <div className="bg-[#18181b] border border-white/5 p-5 rounded-xl shadow-sm hover:border-white/10 transition-colors">
          <div className="flex justify-between items-start mb-4">
            <div className="text-sm font-medium text-zinc-400 tracking-wide">Active Courses</div>
            <div className="p-2 bg-[#facc15]/10 text-[#facc15] rounded-lg">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></svg>
            </div>
          </div>
          <div className="text-3xl font-bold text-white tracking-tight">
            {loading ? "..." : stats.active_courses.toLocaleString()}
          </div>
        </div>
      </div>
      
      {/* Recent Activity Table */}
      <div className="bg-[#18181b] border border-white/5 rounded-xl overflow-hidden shadow-sm">
        <div className="px-6 py-5 border-b border-white/5">
          <h2 className="text-base font-semibold text-white">Recent Activity</h2>
        </div>
        <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
          <div className="w-12 h-12 bg-white/5 rounded-full flex items-center justify-center mb-4">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-zinc-500"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><line x1="10" y1="9" x2="8" y2="9"/></svg>
          </div>
          <h3 className="text-sm font-medium text-white mb-1">No recent activity</h3>
          <p className="text-xs text-zinc-500 max-w-sm">When students enroll or complete courses, their activity logs will appear here.</p>
        </div>
      </div>
    </div>
  );
}
