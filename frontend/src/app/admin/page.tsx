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
        const res = await fetch("http://localhost:8000/api/users/admin-stats/", {
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
    <div>
      <h1 className="text-3xl font-bold mb-8">Admin Overview</h1>
      
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl">
          <div className="text-zinc-400 mb-2">Total Students</div>
          <div className="text-4xl font-bold text-white">
            {loading ? "..." : stats.total_students.toLocaleString()}
          </div>
        </div>
        
        <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl">
          <div className="text-zinc-400 mb-2">Total Revenue</div>
          <div className="text-4xl font-bold text-[#facc15]">
            ₹{loading ? "..." : stats.total_revenue.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 2 })}
          </div>
        </div>
        
        <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl">
          <div className="text-zinc-400 mb-2">Active Courses</div>
          <div className="text-4xl font-bold text-white">
            {loading ? "..." : stats.active_courses.toLocaleString()}
          </div>
        </div>
      </div>
      
      <div className="mt-12 bg-zinc-900 border border-white/10 rounded-2xl p-6">
        <h2 className="text-xl font-semibold mb-6">Recent Activity</h2>
        <div className="text-center py-12 text-zinc-500">
          Analytics & activity logs will appear here.
        </div>
      </div>
    </div>
  );
}
