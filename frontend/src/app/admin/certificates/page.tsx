"use client";

/**
 * Admin Dashboard Completion. Read-only, platform-wide list of every
 * issued certificate (GET /api/courses/admin/certificates/). Certificates
 * are issued automatically by backend eligibility logic elsewhere -- this
 * page never creates/edits/deletes one, it only lists what already exists
 * so an admin can look one up (e.g. to help verify one externally via its
 * Verification ID).
 */

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const PAGE_SIZE = 10;

interface CertStudentRef {
  id: number;
  username: string;
}

interface Certificate {
  id: number;
  course_id: number;
  verification_id: string;
  learner_name_snapshot: string;
  course_title_snapshot: string;
  issued_at: string;
  student: CertStudentRef;
}

interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export default function AdminCertificatesPage() {
  const [certificates, setCertificates] = useState<Certificate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);

  const [studentIdFilter, setStudentIdFilter] = useState("");
  const [courseIdFilter, setCourseIdFilter] = useState("");

  const fetchCertificates = async () => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ page: page.toString() });
      if (studentIdFilter) params.set("student_id", studentIdFilter);
      if (courseIdFilter) params.set("course_id", courseIdFilter);

      const res = await fetch(`${API_BASE}/api/courses/admin/certificates/?${params.toString()}`, {
        credentials: "include",
      });
      if (res.ok) {
        const data: PaginatedResponse<Certificate> = await res.json();
        setCertificates(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setError("Failed to fetch certificates.");
      }
    } catch {
      setError("Network error fetching certificates.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchCertificates();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const handleFilterSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchCertificates();
  };

  const totalPages = Math.ceil(totalCount / PAGE_SIZE) || 1;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Certificates</h1>
        <p className="text-zinc-400 text-sm mt-1">
          Every certificate issued platform-wide. Certificates are issued automatically by course completion
          eligibility -- this list is read-only.
        </p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Filters Row */}
      <form onSubmit={handleFilterSubmit} className="flex flex-col md:flex-row gap-4 mb-6">
        <input
          type="number"
          placeholder="Filter by student ID..."
          value={studentIdFilter}
          onChange={(e) => setStudentIdFilter(e.target.value)}
          className="w-full md:w-56 bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-500"
        />
        <input
          type="number"
          placeholder="Filter by course ID..."
          value={courseIdFilter}
          onChange={(e) => setCourseIdFilter(e.target.value)}
          className="w-full md:w-56 bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-500"
        />
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
                <th className="p-4 font-semibold">Learner Name</th>
                <th className="p-4 font-semibold">Student Username</th>
                <th className="p-4 font-semibold">Course Title</th>
                <th className="p-4 font-semibold">Verification ID</th>
                <th className="p-4 font-semibold">Issued At</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={5} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading certificates...</span>
                    </div>
                  </td>
                </tr>
              ) : certificates.length === 0 ? (
                <tr>
                  <td colSpan={5} className="p-16 text-center text-zinc-500 text-sm">
                    No certificates found.
                  </td>
                </tr>
              ) : (
                certificates.map((c) => (
                  <tr key={c.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4 font-bold text-white text-sm">{c.learner_name_snapshot}</td>
                    <td className="p-4 text-zinc-400">{c.student.username}</td>
                    <td className="p-4 text-zinc-400 max-w-xs truncate">{c.course_title_snapshot}</td>
                    <td className="p-4 font-mono text-xs text-[#facc15] select-all">{c.verification_id}</td>
                    <td className="p-4 text-zinc-400">{new Date(c.issued_at).toLocaleString()}</td>
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
