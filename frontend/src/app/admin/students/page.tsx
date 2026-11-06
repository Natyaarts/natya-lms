"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Users,
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

// "My Students" -- self-service view for a Teacher or Mentor. Backed by
// GET /api/users/me/students/, which returns the correct relationship for
// whichever role the caller actually has: a teacher's roster comes from
// CourseInstructor + Enrollment, a mentor's comes from the explicit
// Mentorship model. Never fake data -- an empty roster shows as empty.
export default function MyStudentsPage() {
  const [students, setStudents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);

  useEffect(() => {
    const fetchStudents = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/users/me/students/`, {
          credentials: "include",
        });
        if (res.ok) {
          setStudents(await res.json());
        } else {
          const data = await res.json().catch(() => ({}));
          setError(data.error || "Failed to load students.");
        }
      } catch (err) {
        console.error(err);
        setError("Network error loading students.");
      } finally {
        setLoading(false);
      }
    };
    fetchStudents();
  }, []);

  const filteredStudents = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return students;
    return students.filter((s) => {
      const fullName = `${s.first_name || ""} ${s.last_name || ""}`.toLowerCase();
      const uname = (s.username || "").toLowerCase();
      const email = (s.email || "").toLowerCase();
      const phone = (s.phone_number || "").toLowerCase();
      const courses = Array.isArray(s.enrolled_courses)
        ? s.enrolled_courses.join(" ").toLowerCase()
        : "";
      return (
        fullName.includes(q) ||
        uname.includes(q) ||
        email.includes(q) ||
        phone.includes(q) ||
        courses.includes(q)
      );
    });
  }, [students, search]);

  const totalCount = filteredStudents.length;
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize));
  const currentPage = Math.min(page, totalPages);
  const paginatedStudents = useMemo(() => {
    const start = (currentPage - 1) * pageSize;
    return filteredStudents.slice(start, start + pageSize);
  }, [filteredStudents, currentPage, pageSize]);

  const pageItems = buildPageItems(currentPage, totalPages);
  const startItem = totalCount > 0 ? (currentPage - 1) * pageSize + 1 : 0;
  const endItem = Math.min(currentPage * pageSize, totalCount);

  return (
    <div className="max-w-5xl mx-auto pb-20">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-[#facc15]/10 flex items-center justify-center text-[#facc15]">
            <Users className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-3xl font-bold flex items-center gap-2.5">
              My Students
              {!loading && students.length > 0 && (
                <span className="px-2.5 py-0.5 rounded-full text-xs font-semibold bg-[#facc15]/10 text-[#facc15] border border-[#facc15]/20">
                  {students.length.toLocaleString()}
                </span>
              )}
            </h1>
          </div>
        </div>

        {!loading && students.length > 0 && (
          <div className="relative w-full sm:w-80">
            <Search className="w-4 h-4 text-zinc-500 absolute left-3.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search student name, phone, course..."
              value={search}
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(1);
              }}
              className="w-full bg-zinc-900 border border-white/10 rounded-xl pl-10 pr-4 py-2.5 text-xs text-white focus:outline-none focus:border-[#facc15] transition-colors placeholder:text-zinc-500"
            />
          </div>
        )}
      </div>

      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        {loading ? (
          <div className="text-center py-20 text-zinc-500 text-sm">Loading...</div>
        ) : error ? (
          <div className="text-center py-20 text-red-400 text-sm">{error}</div>
        ) : filteredStudents.length === 0 ? (
          <div className="text-center py-20 text-zinc-500 text-sm">
            {students.length === 0
              ? "No students assigned to you yet."
              : `No students match "${search}".`}
          </div>
        ) : (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                    <th className="p-4 font-semibold">Student</th>
                    <th className="p-4 font-semibold">Assigned Courses</th>
                    <th className="p-4 font-semibold">Email</th>
                    <th className="p-4 font-semibold">Phone</th>
                    <th className="p-4 font-semibold">Joined</th>
                    <th className="p-4 font-semibold text-center">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-zinc-300">
                  {paginatedStudents.map((s: any) => (
                    <tr
                      key={s.id}
                      className="hover:bg-white/5 transition-colors font-medium"
                    >
                      <td className="p-4 text-white font-bold">
                        {s.first_name || s.username} {s.last_name || ""}
                      </td>
                      <td className="p-4">
                        {Array.isArray(s.enrolled_courses) && s.enrolled_courses.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {s.enrolled_courses.map((ct: string, idx: number) => (
                              <span
                                key={idx}
                                className="px-2 py-0.5 bg-[#facc15]/10 text-[#facc15] border border-[#facc15]/20 rounded text-[10px] font-semibold"
                              >
                                {ct}
                              </span>
                            ))}
                          </div>
                        ) : (
                          <span className="text-zinc-600">&mdash;</span>
                        )}
                      </td>
                      <td className="p-4">{s.email || <span className="text-zinc-600">None</span>}</td>
                      <td className="p-4">{s.phone_number || <span className="text-zinc-600">None</span>}</td>
                      <td className="p-4 text-zinc-400">{new Date(s.date_joined).toLocaleDateString()}</td>
                      <td className="p-4 text-center">
                        <span
                          className={`px-2 py-0.5 text-[10px] font-bold rounded ${
                            s.is_active
                              ? "bg-green-500/10 text-green-400 border border-green-500/20"
                              : "bg-red-500/10 text-red-400 border border-red-500/20"
                          }`}
                        >
                          {s.is_active ? "ACTIVE" : "SUSPENDED"}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="flex flex-col lg:flex-row items-center justify-between px-6 py-4 border-t border-white/5 bg-zinc-950/50 text-xs gap-4">
              <div className="flex flex-wrap items-center gap-4 text-zinc-400">
                <span>
                  Showing <span className="text-white font-semibold">{startItem.toLocaleString()}</span> to{" "}
                  <span className="text-white font-semibold">{endItem.toLocaleString()}</span> of{" "}
                  <span className="text-white font-semibold">{totalCount.toLocaleString()}</span> students
                </span>
                <div className="flex items-center gap-2">
                  <span className="text-zinc-500">Rows per page:</span>
                  <select
                    value={pageSize}
                    onChange={(e) => {
                      setPageSize(Number(e.target.value));
                      setPage(1);
                    }}
                    className="bg-zinc-900 border border-white/10 rounded-lg px-2.5 py-1 text-xs text-white font-medium focus:outline-none focus:border-[#facc15] transition-colors cursor-pointer"
                  >
                    {[10, 20, 50, 100].map((size) => (
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
    </div>
  );
}
