"use client";

/**
 * Admin Dashboard Completion. Read-only, platform-wide list of every
 * Assignment (GET /api/courses/admin/assignments/), each pre-annotated
 * server-side with how many submissions are still pending grading --
 * lets an admin/teacher find where grading is backed up without already
 * knowing an assignment_id. Authoring (create/edit/publish) stays
 * Django-admin-only and is unchanged; grading itself happens at the
 * existing per-assignment queue page (assignments/[assignmentId]), which
 * this page only links to.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { ClipboardList, ChevronLeft, ChevronRight } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const PAGE_SIZE = 20;

interface AdminAssignment {
  id: number;
  title: string;
  course_id: number;
  course_title: string;
  module_id: number;
  module_title: string;
  max_marks: string;
  order: number;
  is_published: boolean;
  pending_submission_count: number;
}

interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export default function AdminAssignmentsPage() {
  const [assignments, setAssignments] = useState<AdminAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  const [courseIdFilter, setCourseIdFilter] = useState("");
  const [needsGradingOnly, setNeedsGradingOnly] = useState(false);

  const fetchAssignments = async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ page: page.toString() });
      if (courseIdFilter) params.set("course_id", courseIdFilter);
      if (needsGradingOnly) params.set("needs_grading", "true");

      const res = await fetch(`${API_BASE}/api/courses/admin/assignments/?${params.toString()}`, {
        credentials: "include",
      });
      if (res.ok) {
        const data: PaginatedResponse<AdminAssignment> = await res.json();
        setAssignments(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setError("Failed to fetch assignments.");
      }
    } catch {
      setError("Network error fetching assignments.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAssignments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, needsGradingOnly]);

  const handleFilterSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchAssignments();
  };

  const totalPages = Math.ceil(totalCount / PAGE_SIZE) || 1;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Assignments</h1>
        <p className="text-zinc-400 text-sm mt-1">
          Every assignment across every course, with how many submissions are still awaiting grading.
        </p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Filters Row */}
      <form onSubmit={handleFilterSubmit} className="flex flex-col md:flex-row gap-4 mb-6">
        <input
          type="number"
          placeholder="Filter by course ID..."
          value={courseIdFilter}
          onChange={(e) => setCourseIdFilter(e.target.value)}
          className="w-full md:w-56 bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-500"
        />
        <label className="flex items-center gap-2 bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-zinc-300 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={needsGradingOnly}
            onChange={(e) => {
              setNeedsGradingOnly(e.target.checked);
              setPage(1);
            }}
            className="accent-[#facc15] w-4 h-4"
          />
          Needs grading only
        </label>
        <button
          type="submit"
          className="px-5 py-3 bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-sm font-semibold rounded-xl transition-all"
        >
          Apply Filters
        </button>
      </form>

      {/* Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Title</th>
                <th className="p-4 font-semibold">Course</th>
                <th className="p-4 font-semibold">Module</th>
                <th className="p-4 font-semibold text-center">Max Marks</th>
                <th className="p-4 font-semibold text-center">Published</th>
                <th className="p-4 font-semibold text-center">Pending Grading</th>
                <th className="p-4 font-semibold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading assignments...</span>
                    </div>
                  </td>
                </tr>
              ) : assignments.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500 text-sm">
                    No assignments found.
                  </td>
                </tr>
              ) : (
                assignments.map((a) => (
                  <tr key={a.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4 font-bold text-white text-sm max-w-xs truncate">{a.title}</td>
                    <td className="p-4 text-zinc-400 max-w-xs truncate">{a.course_title}</td>
                    <td className="p-4 text-zinc-400 max-w-xs truncate">{a.module_title}</td>
                    <td className="p-4 text-center font-semibold text-white">{a.max_marks}</td>
                    <td className="p-4 text-center">
                      <span
                        className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${
                          a.is_published
                            ? "bg-green-500/10 text-green-400 border border-green-500/20"
                            : "bg-yellow-500/10 text-[#facc15] border border-yellow-500/20"
                        }`}
                      >
                        {a.is_published ? "PUBLISHED" : "DRAFT"}
                      </span>
                    </td>
                    <td className="p-4 text-center">
                      {a.pending_submission_count > 0 ? (
                        <span className="px-2.5 py-0.5 text-[9px] font-bold rounded-full bg-yellow-500/10 text-[#facc15] border border-yellow-500/20">
                          {a.pending_submission_count}
                        </span>
                      ) : (
                        <span className="text-zinc-600 text-xs">0</span>
                      )}
                    </td>
                    <td className="p-4">
                      <div className="flex items-center justify-center">
                        <Link
                          href={`/admin/assignments/${a.id}`}
                          className="p-2 bg-white/5 hover:bg-white/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-white"
                          title="Open Grading Queue"
                        >
                          <ClipboardList className="w-4 h-4" />
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

      {/* Pagination Footer */}
      {!loading && totalPages > 1 && (
        <div className="flex items-center justify-between mt-6">
          <div className="text-xs text-zinc-500">
            Showing Page <span className="font-semibold text-white">{page}</span> of{" "}
            <span className="font-semibold text-white">{totalPages}</span> ({totalCount} total entries)
          </div>
          <div className="flex gap-2">
            <button
              disabled={page === 1}
              onClick={() => setPage(page - 1)}
              className="p-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all inline-flex items-center"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              disabled={page === totalPages}
              onClick={() => setPage(page + 1)}
              className="p-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all inline-flex items-center"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
