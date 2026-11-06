"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  Search,
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from "lucide-react";

function buildPageItems(currentPage: number, totalPages: number): (number | "ellipsis")[] {
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const items: (number | "ellipsis")[] = [1];
  if (currentPage > 3) {
    items.push("ellipsis");
  }
  const start = Math.max(2, currentPage - 1);
  const end = Math.min(totalPages - 1, currentPage + 1);
  for (let i = start; i <= end; i++) {
    items.push(i);
  }
  if (currentPage < totalPages - 2) {
    items.push("ellipsis");
  }
  items.push(totalPages);
  return items;
}

export default function AdminCourses() {
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [courses, setCourses] = useState<any[]>([]);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState<"ALL" | "RECORDED" | "LIVE">("ALL");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "PUBLISHED" | "DRAFT">("ALL");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(8);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const isFullAdmin = !!(currentUser?.is_superuser || currentUser?.is_staff);

  const filteredCourses = useMemo(() => {
    return courses.filter((c: any) => {
      if (typeFilter !== "ALL" && (c.course_type || "RECORDED") !== typeFilter) {
        return false;
      }
      if (statusFilter === "PUBLISHED" && !c.is_published) {
        return false;
      }
      if (statusFilter === "DRAFT" && c.is_published) {
        return false;
      }
      if (!search.trim()) return true;
      const term = search.toLowerCase();
      return (
        (c.title || "").toLowerCase().includes(term) ||
        (c.description || "").toLowerCase().includes(term)
      );
    });
  }, [courses, search, typeFilter, statusFilter]);

  const totalCount = filteredCourses.length;
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  const currentPage = Math.min(page, totalPages);
  const paginatedCourses = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredCourses.slice(start, start + pageSize);
  }, [filteredCourses, currentPage, pageSize]);

  const pageItems = buildPageItems(currentPage, totalPages);
  const startItem = totalCount > 0 ? (currentPage - 1) * pageSize + 1 : 0;
  const endItem = Math.min(currentPage * pageSize, totalCount);

  const handleDeleteCourse = async (courseId: number, title: string) => {
    if (!window.confirm(`Are you sure you want to delete "${title}"? This will permanently delete the course, its modules, and lessons.`)) {
      return;
    }

    setDeletingId(courseId);
    try {
      const getCsrfToken = () => {
        if (typeof document === 'undefined') return '';
        const match = document.cookie.match(new RegExp('(^| )csrftoken=([^;]+)'));
        return match ? match[2] : '';
      };

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${courseId}/`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include"
      });

      if (res.ok || res.status === 204) {
        setCourses((prev) => prev.filter((c) => c.id !== courseId));
      } else {
        const data = await res.json().catch(() => ({}));
        alert(data.error || data.detail || "Failed to delete course.");
      }
    } catch (err) {
      console.error(err);
      alert("Network error deleting course.");
    } finally {
      setDeletingId(null);
    }
  };

  const fetchCourses = async () => {
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      if (typeof window !== "undefined") {
        try {
          const cached = sessionStorage.getItem("natya_admin_user");
          if (cached) setCurrentUser(JSON.parse(cached));
        } catch {}
      }

      const [userRes, res] = await Promise.all([
        fetch(`${apiBase}/api/auth/user/`, { credentials: "include" }),
        fetch(`${apiBase}/api/courses/?assigned_only=true`, { credentials: "include" }),
      ]);

      if (userRes.ok) {
        const userData = await userRes.json();
        setCurrentUser(userData);
        try {
          sessionStorage.setItem("natya_admin_user", JSON.stringify(userData));
        } catch {}
      }

      if (res.ok) {
        const data = await res.json();
        setCourses(Array.isArray(data) ? data : (data.results || []));
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
    <div className="animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-8 gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight flex items-center gap-2.5">
            {isFullAdmin || !currentUser ? "Manage Courses" : "My Assigned Courses"}
            {!loading && courses.length > 0 && (
              <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[#facc15]/10 text-[#facc15] border border-[#facc15]/20">
                {courses.length.toLocaleString()}
              </span>
            )}
          </h1>
          <p className="text-zinc-400 text-sm mt-1">
            {isFullAdmin || !currentUser
              ? "Create, edit, and manage your masterclass catalog."
              : `Viewing ${courses.length} course${courses.length === 1 ? "" : "s"} assigned to your account.`}
          </p>
        </div>
        {isFullAdmin && (
          <Link 
            href="/admin/courses/new"
            className="px-4 py-2 bg-white text-black text-sm font-medium rounded-lg hover:bg-zinc-200 transition-colors inline-flex items-center gap-2 shadow-sm"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="5" x2="12" y2="19"></line>
              <line x1="5" y1="12" x2="19" y2="12"></line>
            </svg>
            Create Course
          </Link>
        )}
      </div>

      {courses.length > 0 && (
        <div className="flex flex-col sm:flex-row gap-3 mb-6">
          <div className="relative flex-1 max-w-md">
            <Search className="w-4 h-4 text-zinc-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search masterclasses by title or description..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              className="w-full bg-[#18181b] border border-white/10 rounded-xl pl-10 pr-4 py-2.5 text-sm text-white placeholder-zinc-500 focus:outline-none focus:border-[#facc15] transition-colors"
            />
          </div>
          <select
            value={typeFilter}
            onChange={(e) => {
              setTypeFilter(e.target.value as "ALL" | "RECORDED" | "LIVE");
              setPage(1);
            }}
            className="bg-[#18181b] border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors"
          >
            <option value="ALL">All Course Types</option>
            <option value="RECORDED">Recorded Courses</option>
            <option value="LIVE">Live Interactive</option>
          </select>
          {isFullAdmin && (
            <select
              value={statusFilter}
              onChange={(e) => {
                setStatusFilter(e.target.value as "ALL" | "PUBLISHED" | "DRAFT");
                setPage(1);
              }}
              className="bg-[#18181b] border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors"
            >
              <option value="ALL">All Statuses</option>
              <option value="PUBLISHED">Published Only</option>
              <option value="DRAFT">Drafts Only</option>
            </select>
          )}
        </div>
      )}

      {error && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-lg mb-6 text-sm flex items-center gap-3">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-sm font-medium text-zinc-500 flex items-center gap-2">
          <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
          Loading courses...
        </div>
      ) : courses.length === 0 ? (
        <div className="bg-[#18181b] border border-white/5 rounded-xl p-16 text-center shadow-sm">
          <div className="w-16 h-16 bg-white/5 border border-white/5 rounded-full flex items-center justify-center mx-auto mb-5">
            <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-zinc-500">
              <rect x="2" y="3" width="20" height="14" rx="2" ry="2"></rect>
              <line x1="8" y1="21" x2="16" y2="21"></line>
              <line x1="12" y1="17" x2="12" y2="21"></line>
            </svg>
          </div>
          <h3 className="text-lg font-semibold text-white mb-2 tracking-tight">
            {isFullAdmin ? "No courses found" : "No assigned courses yet"}
          </h3>
          <p className="text-sm text-zinc-500 mb-6 max-w-sm mx-auto leading-relaxed">
            {isFullAdmin
              ? "You haven't created any masterclasses yet. Get started by setting up your first course and curriculum."
              : "No courses have been assigned to your account yet. Please contact the administrator if you believe this is an error."}
          </p>
          {isFullAdmin && (
            <Link 
              href="/admin/courses/new"
              className="px-5 py-2.5 bg-white text-black text-sm font-medium rounded-lg hover:bg-zinc-200 transition-colors shadow-sm inline-block"
            >
              Create your first course
            </Link>
          )}
        </div>
      ) : filteredCourses.length === 0 ? (
        <div className="bg-[#18181b] border border-white/5 rounded-xl p-12 text-center text-sm text-zinc-500">
          No courses match your current search or filter criteria.
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5">
            {paginatedCourses.map((course: any, idx: number) => (
              <motion.div
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ delay: idx * 0.04 }}
                key={course.id} 
                className="bg-[#18181b] border border-white/5 rounded-xl overflow-hidden hover:border-white/10 transition-colors group flex flex-col shadow-sm"
              >
                <div className="aspect-video bg-zinc-900 relative border-b border-white/5 overflow-hidden">
                  {course.thumbnail ? (
                    <img src={course.thumbnail} alt={course.title} className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500" />
                  ) : (
                    <div className="w-full h-full flex flex-col items-center justify-center text-zinc-600 gap-2">
                      <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21 15 16 10 5 21"/></svg>
                      <span className="text-xs font-medium">No Thumbnail</span>
                    </div>
                  )}
                  <div className="absolute top-2 right-2 bg-black/70 backdrop-blur-md px-2 py-0.5 rounded text-[10px] font-medium border border-white/10 text-white uppercase tracking-wider shadow-sm">
                    {course.modules?.length || 0} Modules
                  </div>
                </div>
                
                <div className="p-4 flex-1 flex flex-col">
                  <h3 className="text-base font-semibold text-white mb-1.5 leading-tight line-clamp-2">{course.title}</h3>
                  <p className="text-zinc-500 text-xs line-clamp-2 flex-1 mb-4 leading-relaxed">{course.description}</p>
                  
                  <div className="flex items-center justify-between pt-3 border-t border-white/5">
                    <div className="text-xs font-medium text-zinc-400 tracking-tight">
                      {isFullAdmin
                        ? `₹${parseFloat(course.price || 0).toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`
                        : (course.course_type === "LIVE" ? "Live Batch" : "Recorded Course")}
                    </div>
                    <div className="flex items-center gap-2">
                      <Link 
                        href={`/admin/courses/${course.id}`}
                        className="text-xs font-medium text-white hover:text-zinc-300 flex items-center gap-1 transition-colors bg-white/5 px-2.5 py-1.5 rounded hover:bg-white/10"
                      >
                        {isFullAdmin ? "Manage" : "View Course"}
                      </Link>
                      {isFullAdmin && (
                        <button 
                          type="button"
                          disabled={deletingId === course.id}
                          onClick={() => handleDeleteCourse(course.id, course.title)}
                          className="text-xs font-medium text-red-400 hover:text-red-300 transition-colors bg-red-500/10 hover:bg-red-500/20 px-2.5 py-1.5 rounded disabled:opacity-50"
                          title="Delete Course"
                        >
                          {deletingId === course.id ? "Deleting..." : "Delete"}
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              </motion.div>
            ))}
          </div>

          <div className="mt-8 flex flex-col lg:flex-row items-center justify-between px-6 py-4 rounded-2xl border border-white/10 bg-[#18181b] text-xs gap-4 shadow-lg">
            <div className="flex flex-wrap items-center gap-4 text-zinc-400">
              <span>
                Showing <span className="text-white font-semibold">{startItem.toLocaleString()}</span> to{" "}
                <span className="text-white font-semibold">{endItem.toLocaleString()}</span> of{" "}
                <span className="text-white font-semibold">{totalCount.toLocaleString()}</span> courses
              </span>
              <div className="flex items-center gap-2">
                <span className="text-zinc-500">Courses per page:</span>
                <select
                  value={pageSize}
                  onChange={(e) => {
                    setPageSize(Number(e.target.value));
                    setPage(1);
                  }}
                  className="bg-zinc-900 border border-white/10 rounded-lg px-2.5 py-1 text-xs text-white font-medium focus:outline-none focus:border-[#facc15] transition-colors cursor-pointer"
                >
                  {[4, 8, 12, 24, 48].map((size) => (
                    <option key={size} value={size}>
                      {size}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="flex items-center gap-1.5">
              <button
                type="button"
                onClick={() => setPage(1)}
                disabled={currentPage <= 1}
                title="First Page"
                className="p-1.5 bg-zinc-900 border border-white/10 rounded-lg text-zinc-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-zinc-800 hover:text-white transition-colors"
              >
                <ChevronsLeft className="w-4 h-4" />
              </button>
              <button
                type="button"
                onClick={() => setPage(Math.max(1, currentPage - 1))}
                disabled={currentPage <= 1}
                className="px-2.5 py-1.5 bg-zinc-900 border border-white/10 rounded-lg text-zinc-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-zinc-800 hover:text-white transition-colors inline-flex items-center gap-1 font-medium"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
                <span className="hidden sm:inline">Prev</span>
              </button>

              <div className="flex items-center gap-1 mx-1">
                {pageItems.map((item, idx) =>
                  item === "ellipsis" ? (
                    <span key={`ellipsis-${idx}`} className="px-2 py-1 text-zinc-500 select-none">
                      &hellip;
                    </span>
                  ) : (
                    <button
                      key={item}
                      type="button"
                      onClick={() => setPage(item)}
                      className={`min-w-[32px] h-8 px-2.5 rounded-lg text-xs font-semibold transition-all border ${
                        item === currentPage
                          ? "bg-[#facc15] text-black border-[#facc15] shadow-sm shadow-[#facc15]/20"
                          : "bg-zinc-900 border-white/10 text-zinc-300 hover:bg-zinc-800 hover:text-white"
                      }`}
                    >
                      {item}
                    </button>
                  )
                )}
              </div>

              <button
                type="button"
                onClick={() => setPage(Math.min(totalPages, currentPage + 1))}
                disabled={currentPage >= totalPages}
                className="px-2.5 py-1.5 bg-zinc-900 border border-white/10 rounded-lg text-zinc-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-zinc-800 hover:text-white transition-colors inline-flex items-center gap-1 font-medium"
              >
                <span className="hidden sm:inline">Next</span>
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                onClick={() => setPage(totalPages)}
                disabled={currentPage >= totalPages}
                title="Last Page"
                className="p-1.5 bg-zinc-900 border border-white/10 rounded-lg text-zinc-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-zinc-800 hover:text-white transition-colors"
              >
                <ChevronsRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
