"use client";

import { useEffect, useState } from "react";
import { Search, Trash2, ChevronLeft, ChevronRight } from "lucide-react";

export default function EnrollmentsRegistry() {
  const [enrollments, setEnrollments] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // Filter and pagination states
  const [search, setSearch] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  
  const [revokingId, setRevokingId] = useState<number | null>(null);

  const getCsrfToken = () => {
    let csrfToken = "";
    if (typeof document !== 'undefined' && document.cookie) {
      const cookies = document.cookie.split(';');
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.startsWith('csrftoken=')) {
          csrfToken = decodeURIComponent(cookie.substring('csrftoken='.length));
          break;
        }
      }
    }
    return csrfToken;
  };

  const fetchEnrollments = async () => {
    setLoading(true);
    try {
      const queryParams = new URLSearchParams({
        page: page.toString(),
        search: search,
        source: sourceFilter
      });

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/enrollments-admin/?${queryParams.toString()}`, {
        credentials: "include"
      });

      if (res.ok) {
        const data = await res.json();
        setEnrollments(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setError("Failed to fetch enrollment records");
      }
    } catch (err) {
      setError("Network error fetching enrollment records");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchEnrollments();
  }, [page, sourceFilter]);

  const handleSearchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchEnrollments();
  };

  const handleRevokeAccess = async (enrollmentId: number, studentName: string, courseTitle: string) => {
    if (!confirm(`Are you sure you want to revoke access for student "${studentName}" from the course "${courseTitle}"?\n\nThis will immediately remove their course access. The historical payment record (if any) will remain untouched.`)) return;

    setRevokingId(enrollmentId);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/enrollments-admin/${enrollmentId}/`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include"
      });

      if (res.ok) {
        alert("Enrollment revoked successfully. Student loses classroom access.");
        fetchEnrollments();
      } else {
        alert("Failed to revoke enrollment.");
      }
    } catch (err) {
      console.error(err);
      alert("Error revoking enrollment.");
    } finally {
      setRevokingId(null);
    }
  };

  const totalPages = Math.ceil(totalCount / 10) || 1;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Enrollments Registry</h1>
        <p className="text-zinc-400 text-sm mt-1">Manage active student course access permissions and revoke classroom access safely.</p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Filters & Search Row */}
      <div className="flex flex-col md:flex-row gap-4 mb-6">
        <form onSubmit={handleSearchSubmit} className="flex-1 flex gap-2">
          <div className="relative flex-1">
            <Search className="w-4 h-4 text-zinc-500 absolute left-4 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search by student details, phone, or course title..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full bg-zinc-900 border border-white/5 rounded-xl pl-11 pr-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors placeholder:text-zinc-500"
            />
          </div>
          <button
            type="submit"
            className="px-5 py-3 bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-sm font-semibold rounded-xl transition-all"
          >
            Search
          </button>
        </form>

        <div className="w-full md:w-48">
          <select
            value={sourceFilter}
            onChange={(e) => {
              setSourceFilter(e.target.value);
              setPage(1);
            }}
            className="w-full bg-zinc-900 border border-white/5 rounded-xl p-3 text-sm text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
          >
            <option value="">All Sources</option>
            <option value="Paid">Paid (Checkout)</option>
            <option value="Manual">Manual / Free</option>
          </select>
        </div>
      </div>

      {/* Enrollments Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Student Name</th>
                <th className="p-4 font-semibold">Contact Info</th>
                <th className="p-4 font-semibold">Course Title</th>
                <th className="p-4 font-semibold">Enrolled Date</th>
                <th className="p-4 font-semibold text-center">Source</th>
                <th className="p-4 font-semibold text-center">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading enrollments...</span>
                    </div>
                  </td>
                </tr>
              ) : enrollments.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                    No active student enrollments found.
                  </td>
                </tr>
              ) : (
                enrollments.map(enrollment => (
                  <tr key={enrollment.id} className="hover:bg-white/5 transition-colors">
                    {/* Student Name */}
                    <td className="p-4">
                      <div className="font-bold text-white text-sm">{enrollment.student_name}</div>
                    </td>

                    {/* Contact Info */}
                    <td className="p-4">
                      <div className="text-zinc-300 font-medium">{enrollment.student_email}</div>
                      {enrollment.student_phone && (
                        <div className="text-zinc-500 text-[10px] mt-0.5">{enrollment.student_phone}</div>
                      )}
                    </td>

                    {/* Course Title */}
                    <td className="p-4 font-bold text-white max-w-xs truncate">
                      {enrollment.course_title}
                    </td>

                    {/* Enrollment Date */}
                    <td className="p-4 text-zinc-400">
                      {new Date(enrollment.enrolled_at).toLocaleString()}
                    </td>

                    {/* Source */}
                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${
                        enrollment.source === 'Paid'
                          ? 'bg-green-500/10 text-green-400 border border-green-500/20'
                          : 'bg-zinc-800 text-zinc-400 border border-white/5'
                      }`}>
                        {enrollment.source}
                      </span>
                    </td>

                    {/* Action */}
                    <td className="p-4 text-center">
                      <button
                        onClick={() => handleRevokeAccess(enrollment.id, enrollment.student_name, enrollment.course_title)}
                        disabled={revokingId === enrollment.id}
                        className="p-2 bg-white/5 hover:bg-red-500/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-red-500 disabled:opacity-50"
                        title="Revoke Course Access"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
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
            Showing Page <span className="font-semibold text-white">{page}</span> of <span className="font-semibold text-white">{totalPages}</span> ({totalCount} total entries)
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
