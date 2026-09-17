"use client";

/**
 * Admin Dashboard Completion. Two separate but related surfaces on one
 * page:
 *
 *  1. Teachers / Mentors directory -- GET /api/users/admin-users/ has no
 *     server-side role filter (there is no such query param; the backend
 *     ignores anything sent), so the full user list is fetched once and
 *     split into "Teachers" / "Mentors" tabs entirely client-side, with a
 *     client-side search on top of whichever tab is active. Full user
 *     editing already exists at /admin/users/[id] -- this page only links
 *     there (Eye icon) rather than duplicating it.
 *
 *  2. Mentorship assignment panel -- GET/POST/PATCH
 *     /api/users/mentorships/. The backend (MentorshipSerializer.validate)
 *     is the sole authority on validity (student must actually be a
 *     student, mentor must actually be a mentor, student != mentor, only
 *     one ACTIVE mentorship per pair) -- this page only pre-filters the
 *     dropdowns by role as a convenience and surfaces whatever 400 the
 *     backend returns.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { Eye, Search, Plus, X, AlertTriangle, Ban, Users } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface AdminUser {
  id: number;
  username: string;
  email: string;
  phone_number: string | null;
  first_name: string;
  last_name: string;
  is_superuser: boolean;
  is_staff: boolean;
  is_teacher: boolean;
  is_student: boolean;
  is_mentor: boolean;
  is_active: boolean;
  date_joined: string;
  parent_name: string | null;
  parent_phone: string | null;
  courses_count: number;
}

interface Mentorship {
  id: number;
  student: number;
  student_name: string;
  mentor: number;
  mentor_name: string;
  assigned_by: number | null;
  assigned_by_name: string | null;
  status: "ACTIVE" | "INACTIVE";
  start_date: string | null;
  end_date: string | null;
  notes: string;
  assigned_at: string;
  updated_at: string;
}

function displayName(u: AdminUser): string {
  const name = `${u.first_name} ${u.last_name}`.trim();
  return name || u.username;
}

function getCsrfToken(): string {
  let csrfToken = "";
  if (typeof document !== "undefined" && document.cookie) {
    const cookies = document.cookie.split(";");
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.startsWith("csrftoken=")) {
        csrfToken = decodeURIComponent(cookie.substring("csrftoken=".length));
        break;
      }
    }
  }
  return csrfToken;
}

// Surfaces whatever shape a DRF ValidationError comes back as -- a plain
// {"detail": "..."}, a per-field {"student": ["..."]} / {"student": "..."},
// or a bare non_field_errors array from `raise ValidationError("...")` in
// MentorshipSerializer.validate -- without assuming any one particular shape.
function extractErrorMessage(data: unknown): string {
  if (!data) return "Request failed.";
  if (typeof data === "string") return data;
  if (typeof data === "object") {
    const obj = data as Record<string, unknown>;
    const parts: string[] = [];
    for (const key of Object.keys(obj)) {
      const val = obj[key];
      const text = Array.isArray(val) ? val.join(" ") : String(val);
      parts.push(key === "detail" || key === "non_field_errors" ? text : `${key}: ${text}`);
    }
    if (parts.length) return parts.join(" ");
  }
  return "Request failed.";
}

export default function TeachersMentorsPage() {
  const [allUsers, setAllUsers] = useState<AdminUser[]>([]);
  const [usersLoading, setUsersLoading] = useState(true);
  const [usersError, setUsersError] = useState("");

  const [activeTab, setActiveTab] = useState<"teachers" | "mentors">("teachers");
  const [search, setSearch] = useState("");

  const [mentorships, setMentorships] = useState<Mentorship[]>([]);
  const [mentorshipsLoading, setMentorshipsLoading] = useState(true);
  const [mentorshipsError, setMentorshipsError] = useState("");
  const [filterStudentId, setFilterStudentId] = useState("");
  const [filterMentorId, setFilterMentorId] = useState("");

  const [showAssignModal, setShowAssignModal] = useState(false);
  const [assignStudent, setAssignStudent] = useState("");
  const [assignMentor, setAssignMentor] = useState("");
  const [assignStart, setAssignStart] = useState("");
  const [assignEnd, setAssignEnd] = useState("");
  const [assignNotes, setAssignNotes] = useState("");
  const [assignSubmitting, setAssignSubmitting] = useState(false);
  const [assignError, setAssignError] = useState("");

  const [endingId, setEndingId] = useState<number | null>(null);

  const fetchUsers = async () => {
    setUsersLoading(true);
    setUsersError("");
    try {
      const res = await fetch(`${API_BASE}/api/users/admin-users/`, { credentials: "include" });
      if (res.ok) {
        const data: AdminUser[] = await res.json();
        setAllUsers(Array.isArray(data) ? data : []);
      } else {
        setUsersError("Failed to fetch users.");
      }
    } catch {
      setUsersError("Network error fetching users.");
    } finally {
      setUsersLoading(false);
    }
  };

  const fetchMentorships = async () => {
    setMentorshipsLoading(true);
    setMentorshipsError("");
    try {
      const params = new URLSearchParams();
      if (filterStudentId) params.set("student", filterStudentId);
      if (filterMentorId) params.set("mentor", filterMentorId);
      const qs = params.toString();
      const res = await fetch(`${API_BASE}/api/users/mentorships/${qs ? `?${qs}` : ""}`, {
        credentials: "include",
      });
      if (res.ok) {
        const data: Mentorship[] = await res.json();
        setMentorships(Array.isArray(data) ? data : []);
      } else {
        setMentorshipsError("Failed to fetch mentorship assignments.");
      }
    } catch {
      setMentorshipsError("Network error fetching mentorship assignments.");
    } finally {
      setMentorshipsLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
    fetchMentorships();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleMentorshipFilterSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    fetchMentorships();
  };

  const teachers = allUsers.filter((u) => u.is_teacher);
  const mentors = allUsers.filter((u) => u.is_mentor);
  const students = allUsers.filter((u) => u.is_student);
  const currentTabUsers = activeTab === "teachers" ? teachers : mentors;

  const searchLower = search.trim().toLowerCase();
  const filteredUsers = searchLower
    ? currentTabUsers.filter((u) =>
        [u.username, u.email, u.first_name, u.last_name].some((f) => (f || "").toLowerCase().includes(searchLower))
      )
    : currentTabUsers;

  const openAssignModal = () => {
    setAssignStudent("");
    setAssignMentor("");
    setAssignStart("");
    setAssignEnd("");
    setAssignNotes("");
    setAssignError("");
    setShowAssignModal(true);
  };

  const closeAssignModal = () => {
    setShowAssignModal(false);
    setAssignError("");
  };

  const handleCreateAssignment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!assignStudent || !assignMentor) {
      setAssignError("Select both a student and a mentor.");
      return;
    }

    setAssignSubmitting(true);
    setAssignError("");
    try {
      const body: Record<string, unknown> = {
        student: parseInt(assignStudent, 10),
        mentor: parseInt(assignMentor, 10),
      };
      if (assignStart) body.start_date = assignStart;
      if (assignEnd) body.end_date = assignEnd;
      if (assignNotes) body.notes = assignNotes;

      const res = await fetch(`${API_BASE}/api/users/mentorships/`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => null);
      if (res.ok) {
        setShowAssignModal(false);
        await fetchMentorships();
      } else {
        setAssignError(extractErrorMessage(data));
      }
    } catch {
      setAssignError("Network error creating mentorship assignment.");
    } finally {
      setAssignSubmitting(false);
    }
  };

  const handleEndMentorship = async (m: Mentorship) => {
    if (
      !confirm(
        `End the mentorship between ${m.student_name} and ${m.mentor_name}? This sets it to INACTIVE with today's date as the end date.`
      )
    )
      return;

    setEndingId(m.id);
    try {
      const today = new Date().toISOString().slice(0, 10);
      const res = await fetch(`${API_BASE}/api/users/mentorships/${m.id}/`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
        body: JSON.stringify({ status: "INACTIVE", end_date: today }),
      });
      if (res.ok) {
        await fetchMentorships();
      } else {
        const data = await res.json().catch(() => null);
        alert(extractErrorMessage(data));
      }
    } catch {
      alert("Network error ending mentorship.");
    } finally {
      setEndingId(null);
    }
  };

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-3xl font-bold">Teachers &amp; Mentors</h1>
        <p className="text-zinc-400 text-sm mt-1">
          Browse teacher and mentor accounts, and manage student&ndash;mentor assignments.
        </p>
      </div>

      {usersError && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">
          {usersError}
        </div>
      )}

      {/* Tabs & Search */}
      <div className="flex flex-col md:flex-row gap-4 mb-6">
        <div className="flex gap-2 bg-zinc-900 border border-white/10 rounded-xl p-1">
          <button
            onClick={() => setActiveTab("teachers")}
            className={`px-4 py-2 rounded-lg text-sm font-semibold transition-all ${
              activeTab === "teachers" ? "bg-[#facc15] text-black" : "text-zinc-400 hover:text-white"
            }`}
          >
            Teachers ({teachers.length})
          </button>
          <button
            onClick={() => setActiveTab("mentors")}
            className={`px-4 py-2 rounded-lg text-sm font-semibold transition-all ${
              activeTab === "mentors" ? "bg-[#facc15] text-black" : "text-zinc-400 hover:text-white"
            }`}
          >
            Mentors ({mentors.length})
          </button>
        </div>

        <div className="relative flex-1">
          <Search className="w-4 h-4 text-zinc-500 absolute left-4 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search by name, username, or email..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full bg-zinc-900 border border-white/5 rounded-xl pl-11 pr-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] transition-colors placeholder:text-zinc-500"
          />
        </div>
      </div>

      {/* Directory Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl mb-10">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Name</th>
                <th className="p-4 font-semibold">Email</th>
                <th className="p-4 font-semibold">Phone</th>
                <th className="p-4 font-semibold text-center">Courses</th>
                <th className="p-4 font-semibold text-center">Status</th>
                <th className="p-4 font-semibold">Joined</th>
                <th className="p-4 font-semibold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {usersLoading ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading users...</span>
                    </div>
                  </td>
                </tr>
              ) : filteredUsers.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500 text-sm">
                    No {activeTab} found.
                  </td>
                </tr>
              ) : (
                filteredUsers.map((u) => (
                  <tr key={u.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4 font-bold text-white text-sm">{displayName(u)}</td>
                    <td className="p-4 text-zinc-400">{u.email}</td>
                    <td className="p-4 text-zinc-400">{u.phone_number || <span className="text-zinc-600 italic">None</span>}</td>
                    <td className="p-4 text-center font-semibold text-[#facc15]">{u.courses_count}</td>
                    <td className="p-4 text-center">
                      <span
                        className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${
                          u.is_active
                            ? "bg-green-500/10 text-green-400 border border-green-500/20"
                            : "bg-red-500/10 text-red-400 border border-red-500/20"
                        }`}
                      >
                        {u.is_active ? "ACTIVE" : "INACTIVE"}
                      </span>
                    </td>
                    <td className="p-4 text-zinc-400">{new Date(u.date_joined).toLocaleDateString()}</td>
                    <td className="p-4">
                      <div className="flex items-center justify-center">
                        <Link
                          href={`/admin/users/${u.id}`}
                          className="p-2 bg-white/5 hover:bg-white/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-white"
                          title="View / Edit User"
                        >
                          <Eye className="w-4 h-4" />
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

      {/* Mentorship Assignment Panel */}
      <div className="mb-6 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h2 className="text-xl font-bold flex items-center gap-2">
            <Users className="w-5 h-5 text-[#facc15]" />
            Mentorship Assignments
          </h2>
          <p className="text-zinc-400 text-sm mt-1">Assign mentors to students and manage active assignments.</p>
        </div>
        <button
          onClick={openAssignModal}
          className="px-5 py-3 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-all flex items-center gap-2 text-sm shrink-0"
        >
          <Plus className="w-4 h-4" />
          New Assignment
        </button>
      </div>

      {/* Optional student/mentor id filters -- narrows the "see everything"
          admin queryset the same way ?student=/?mentor= narrow it server-side. */}
      <form onSubmit={handleMentorshipFilterSubmit} className="flex flex-col md:flex-row gap-4 mb-6">
        <input
          type="number"
          placeholder="Filter by student ID..."
          value={filterStudentId}
          onChange={(e) => setFilterStudentId(e.target.value)}
          className="w-full md:w-48 bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-500"
        />
        <input
          type="number"
          placeholder="Filter by mentor ID..."
          value={filterMentorId}
          onChange={(e) => setFilterMentorId(e.target.value)}
          className="w-full md:w-48 bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-500"
        />
        <button
          type="submit"
          className="px-5 py-3 bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-sm font-semibold rounded-xl transition-all"
        >
          Apply Filters
        </button>
      </form>

      {mentorshipsError && (
        <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">
          {mentorshipsError}
        </div>
      )}

      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Student</th>
                <th className="p-4 font-semibold">Mentor</th>
                <th className="p-4 font-semibold text-center">Status</th>
                <th className="p-4 font-semibold">Start / End</th>
                <th className="p-4 font-semibold">Assigned By</th>
                <th className="p-4 font-semibold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {mentorshipsLoading ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading mentorship assignments...</span>
                    </div>
                  </td>
                </tr>
              ) : mentorships.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                    No mentorship assignments found.
                  </td>
                </tr>
              ) : (
                mentorships.map((m) => (
                  <tr key={m.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4 font-bold text-white text-sm">{m.student_name}</td>
                    <td className="p-4 font-semibold text-white">{m.mentor_name}</td>
                    <td className="p-4 text-center">
                      <span
                        className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full ${
                          m.status === "ACTIVE"
                            ? "bg-green-500/10 text-green-400 border border-green-500/20"
                            : "bg-red-500/10 text-red-400 border border-red-500/20"
                        }`}
                      >
                        {m.status}
                      </span>
                    </td>
                    <td className="p-4 text-zinc-400">
                      {m.start_date || <span className="text-zinc-600 italic">Not set</span>}
                      {" → "}
                      {m.end_date || <span className="text-zinc-600 italic">Not set</span>}
                    </td>
                    <td className="p-4 text-zinc-400">
                      {m.assigned_by_name || <span className="text-zinc-600 italic">System</span>}
                    </td>
                    <td className="p-4">
                      <div className="flex items-center justify-center">
                        {m.status === "ACTIVE" && (
                          <button
                            onClick={() => handleEndMentorship(m)}
                            disabled={endingId === m.id}
                            className="p-2 bg-red-500/10 hover:bg-red-500/20 rounded-xl transition-all inline-flex items-center justify-center text-red-400 disabled:opacity-50"
                            title="End Mentorship"
                          >
                            <Ban className="w-4 h-4" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* New Assignment Modal */}
      {showAssignModal && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-md shadow-2xl relative text-sm">
            <button
              onClick={closeAssignModal}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1">New Mentorship Assignment</h2>
            <p className="text-zinc-500 text-xs mb-6">Assign a mentor to a student. Status defaults to ACTIVE.</p>

            <form onSubmit={handleCreateAssignment} className="space-y-4">
              <div>
                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">
                  Student
                </label>
                <select
                  value={assignStudent}
                  onChange={(e) => setAssignStudent(e.target.value)}
                  required
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
                >
                  <option value="">Select a student...</option>
                  {students.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.username}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">
                  Mentor
                </label>
                <select
                  value={assignMentor}
                  onChange={(e) => setAssignMentor(e.target.value)}
                  required
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
                >
                  <option value="">Select a mentor...</option>
                  {mentors.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.username}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">
                    Start Date (optional)
                  </label>
                  <input
                    type="date"
                    value={assignStart}
                    onChange={(e) => setAssignStart(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15]"
                  />
                </div>
                <div>
                  <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">
                    End Date (optional)
                  </label>
                  <input
                    type="date"
                    value={assignEnd}
                    onChange={(e) => setAssignEnd(e.target.value)}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15]"
                  />
                </div>
              </div>

              <div>
                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">
                  Notes (optional)
                </label>
                <textarea
                  value={assignNotes}
                  onChange={(e) => setAssignNotes(e.target.value)}
                  rows={3}
                  placeholder="Any context for this assignment..."
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15] resize-none"
                />
              </div>

              {assignError && (
                <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-2.5 rounded-xl text-xs flex items-start gap-2">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  {assignError}
                </div>
              )}

              <div className="flex gap-2 pt-2">
                <button
                  type="button"
                  onClick={closeAssignModal}
                  disabled={assignSubmitting}
                  className="flex-1 py-2.5 bg-white/5 hover:bg-white/10 text-zinc-300 font-semibold rounded-xl transition-all text-xs disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={assignSubmitting}
                  className="flex-1 py-2.5 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-all flex items-center justify-center gap-2 text-xs disabled:opacity-50"
                >
                  {assignSubmitting ? "Assigning..." : "Create Assignment"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
