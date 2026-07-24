"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";

export default function AdminCourses() {
  const [courses, setCourses] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchCourses = async () => {
    try {
      const res = await fetch("http://localhost:8000/api/courses/", {
        credentials: "include"
      });
      if (res.ok) {
        const data = await res.json();
        setCourses(data);
      } else {
        setError("Failed to fetch courses");
      }
    } catch (err) {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCourses();
  }, []);

  return (
    <div>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-8 gap-4">
        <h1 className="text-3xl font-bold">Manage Courses</h1>
        <Link 
          href="/admin/courses/new"
          className="px-6 py-3 bg-[#facc15] text-black font-semibold rounded-xl hover:bg-[#eab308] transition-colors inline-flex items-center gap-2"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="12" y1="5" x2="12" y2="19"></line>
            <line x1="5" y1="12" x2="19" y2="12"></line>
          </svg>
          Create New Course
        </Link>
      </div>

      {error && (
        <div className="bg-red-500/10 border border-red-500 text-red-500 p-4 rounded-xl mb-6">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-zinc-500">Loading courses...</div>
      ) : courses.length === 0 ? (
        <div className="bg-zinc-900 border border-white/10 rounded-2xl p-12 text-center">
          <div className="w-16 h-16 bg-zinc-800 rounded-full flex items-center justify-center mx-auto mb-4">
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-zinc-500">
              <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
              <line x1="8" y1="21" x2="16" y2="21"></line>
              <line x1="12" y1="17" x2="12" y2="21"></line>
            </svg>
          </div>
          <h3 className="text-xl font-semibold mb-2">No courses yet</h3>
          <p className="text-zinc-400 mb-6 max-w-md mx-auto">Get started by creating your first course. You can add video modules and pricing details later.</p>
          <Link 
            href="/admin/courses/new"
            className="px-6 py-3 bg-white text-black font-medium rounded-full hover:bg-zinc-200 transition-colors"
          >
            Create Course
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
          {courses.map((course: any, idx: number) => (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: idx * 0.05 }}
              key={course.id} 
              className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden hover:border-white/20 transition-colors group flex flex-col"
            >
              <div className="aspect-video bg-zinc-800 relative">
                {course.thumbnail ? (
                  <img src={course.thumbnail} alt={course.title} className="w-full h-full object-cover" />
                ) : (
                  <div className="w-full h-full flex items-center justify-center text-zinc-600">
                    No Thumbnail
                  </div>
                )}
                <div className="absolute top-3 right-3 bg-black/60 backdrop-blur-md px-3 py-1 rounded-full text-xs font-medium border border-white/10">
                  {course.modules?.length || 0} Modules
                </div>
              </div>
              
              <div className="p-5 flex-1 flex flex-col">
                <h3 className="text-lg font-semibold mb-2 line-clamp-1">{course.title}</h3>
                <p className="text-zinc-400 text-sm mb-4 line-clamp-2 flex-1">{course.description}</p>
                
                <div className="flex items-center justify-between pt-4 border-t border-white/5">
                  <div className="text-white font-medium">₹{parseFloat(course.price).toLocaleString()}</div>
                  <Link 
                    href={`/admin/courses/${course.id}`}
                    className="text-sm font-medium text-[#facc15] hover:text-[#eab308] flex items-center gap-1"
                  >
                    Manage Course
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="5" y1="12" x2="19" y2="12"></line>
                      <polyline points="12 5 19 12 12 19"></polyline>
                    </svg>
                  </Link>
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      )}
    </div>
  );
}
