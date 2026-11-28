"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { Search, Plus, X, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Check, Eye } from "lucide-react";
import CountrySelect from "@/components/CountrySelect";
import { joinPhone } from "@/lib/phone";

// Add User form styling (shared by its fields).
const INPUT_CLASS =
  "w-full h-11 bg-zinc-800/80 border border-white/10 rounded-xl px-4 text-sm text-white placeholder:text-zinc-500 focus:outline-none focus:border-[#facc15] focus:ring-2 focus:ring-[#facc15]/20 transition-colors";
const SECTION_TITLE = "text-[11px] font-bold uppercase tracking-[0.14em] text-zinc-400";
const OPTIONAL_TAG = "normal-case tracking-normal font-medium text-zinc-500";
// Country code + number on one row; the country button (shared CountrySelect) is
// restyled here only, to match this form's inputs.
const PHONE_ROW_CLASS =
  "flex gap-2 h-11 min-w-0 [&>div>button]:bg-zinc-800/80 [&>div>button]:border-white/10 [&>div>button]:px-3";

const LEARNING_TRACKS = [
  { value: "CAREER_ACADEMY", title: "Career Academy", description: "Certified & Diploma programmes" },
  { value: "REGULAR", title: "Regular Track", description: "Flexible & hobby learning" },
];

function FormField({ label, htmlFor, required, optional, hint, children }: {
  label: string;
  htmlFor: string;
  required?: boolean;
  optional?: boolean;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <label htmlFor={htmlFor} className="flex items-baseline gap-1 text-sm font-medium text-zinc-200 mb-1.5">
        {label}
        {required && <span className="text-[#facc15]">*</span>}
        {optional && <span className="text-xs font-normal text-zinc-500">(optional)</span>}
      </label>
      {children}
      {hint && <p className="text-xs text-zinc-500 mt-1.5">{hint}</p>}
    </div>
  );
}

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

export default function AdminUsers() {
  const router = useRouter();
  
  // Data State
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  // Tabs & Filters State
  const [activeTab, setActiveTab] = useState<'students' | 'teachers' | 'mentors'>('students');
  const [searchQuery, setSearchQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'inactive'>('all');
  const [trackFilter, setTrackFilter] = useState<'all' | 'CAREER_ACADEMY' | 'REGULAR'>('all');

  // Pagination State
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState<number | 'all'>(50);
  const [totalCount, setTotalCount] = useState(0);
  const [debouncedSearch, setDebouncedSearch] = useState(searchQuery);

  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchQuery);
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Add Modals State
  const [showAddModal, setShowAddModal] = useState(false);
  const [modalType, setModalType] = useState<'student' | 'teacher' | 'mentor'>('student');
  const [submitting, setSubmitting] = useState(false);
  
  // Form State
  const [formFields, setFormFields] = useState({
    username: "",
    email: "",
    phone_number: "",
    first_name: "",
    last_name: "",
    password: "",
    parent_name: "",
    parent_phone: "",
    student_type: "CAREER_ACADEMY",
    state: "",
    district: "",
    pincode: ""
  });
  const [countryCode, setCountryCode] = useState("+91");
  const [parentCountryCode, setParentCountryCode] = useState("+91");
  const [formError, setFormError] = useState("");

  // Escape closes the Add form (not while it is being sent).
  useEffect(() => {
    if (!showAddModal) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !submitting) setShowAddModal(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showAddModal, submitting]);

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

  const fetchUsers = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      params.set('page', currentPage.toString());
      if (pageSize !== 'all') {
        params.set('page_size', pageSize.toString());
      } else {
        params.set('page_size', '100');
      }

      if (activeTab) {
        params.set('role', activeTab);
      }
      if (statusFilter !== 'all') {
        params.set('status', statusFilter);
      }
      if (activeTab === 'students' && trackFilter !== 'all') {
        params.set('student_type', trackFilter);
      }
      if (debouncedSearch.trim()) {
        params.set('search', debouncedSearch.trim());
      }

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/?${params.toString()}`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          setUsers(data);
          setTotalCount(data.length);
        } else {
          setUsers(data.results || []);
          setTotalCount(data.count || 0);
        }
      } else {
        setError("Failed to fetch users");
      }
    } catch (err) {
      setError("Network error fetching users");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, [currentPage, pageSize, activeTab, statusFilter, trackFilter, debouncedSearch]);

  const handleToggleStatus = async (userId: number, currentStatus: boolean, e: React.MouseEvent) => {
    e.stopPropagation(); // Prevent row click details navigation
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${userId}/`, {
        method: "PATCH",
        headers: { 
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ is_active: !currentStatus }),
        credentials: "include",
      });
      if (res.ok) {
        // Refresh local user list
        setUsers(prev => prev.map(u => u.id === userId ? { ...u, is_active: !currentStatus } : u));
      }
    } catch (err) {
      console.error("Failed to toggle status", err);
    }
  };

  const handleAddUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setFormError("");
    
    // "+<code><digits>": spaces, dashes and a trunk "0" (e.g. Irish 087...) removed.
    // The server validates the number for its country and rejects duplicates.
    const formattedPhone = joinPhone(countryCode, formFields.phone_number);
    const formattedParentPhone = joinPhone(parentCountryCode, formFields.parent_phone);

    // Students sign in with their mobile number and an OTP: they get no password, and
    // their (never typed) login name is their phone number -- the same as when a
    // student signs up by OTP themselves.
    const isStudent = modalType === 'student';
    const username = isStudent ? formattedPhone : formFields.username.trim();
    if (!username) {
      setFormError(isStudent ? "Enter the student's mobile number." : "Enter a username.");
      return;
    }

    setSubmitting(true);
    
    const payload: any = {
      username,
      email: formFields.email || undefined,
      phone_number: formattedPhone || undefined,
      first_name: formFields.first_name,
      last_name: formFields.last_name,
      is_student: modalType === 'student',
      is_teacher: modalType === 'teacher',
      is_mentor: modalType === 'mentor',
      is_superuser: false,
      is_active: true
    };

    if (!isStudent && formFields.password) {
      payload.password = formFields.password;
    }
    if (modalType === 'student') {
      payload.parent_name = formFields.parent_name || undefined;
      payload.parent_phone = formattedParentPhone || undefined;
      payload.student_type = formFields.student_type || undefined;
      payload.state = formFields.state || undefined;
      payload.district = formFields.district || undefined;
      payload.pincode = formFields.pincode || undefined;
    }

    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify(payload),
        credentials: "include"
      });

      const data = await res.json();
      if (res.ok) {
        setShowAddModal(false);
        setFormFields({
          username: "",
          email: "",
          phone_number: "",
          first_name: "",
          last_name: "",
          password: "",
          parent_name: "",
          parent_phone: "",
          student_type: "CAREER_ACADEMY",
          state: "",
          district: "",
          pincode: ""
        });
        fetchUsers();
      } else {
        // Display validation keys directly if dictionary returned
        if (data && typeof data === 'object') {
          const errorData = { ...data };
          // A student's login name is their phone number, so a clash there means the
          // number is already in use; say so once, in words the admin can act on.
          if (isStudent && errorData.username) {
            if (errorData.phone_number) delete errorData.username;
            else errorData.username = `This mobile number (${formattedPhone}) is already used by another account. Search for it in the user list.`;
          }
          const errors = Object.keys(errorData)
            .map(key => (isStudent && key === 'username') ? String(errorData[key]) : `${key}: ${errorData[key]}`)
            .join(" | ");
          setFormError(errors);
        } else {
          setFormError("Failed to register user. Check inputs.");
        }
      }
    } catch (err) {
      setFormError("Network error sending registration.");
    } finally {
      setSubmitting(false);
    }
  };

  // Server-side filtered and paginated user list
  const displayedUsers = users;
  const isViewAll = pageSize === 'all';
  const effectivePageSize = isViewAll ? Math.max(1, totalCount) : pageSize;
  const totalPages = isViewAll ? 1 : Math.max(1, Math.ceil(totalCount / effectivePageSize));

  // Reset page when switching filters/tabs or page size
  useEffect(() => {
    setCurrentPage(1);
  }, [activeTab, searchQuery, statusFilter, trackFilter, pageSize]);

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-8 gap-4">
        <div>
          <h1 className="text-3xl font-bold">Manage Users</h1>
          <p className="text-zinc-400 text-sm mt-1">Configure learner and mentor directory profiles.</p>
        </div>
        <button
          onClick={() => {
            setModalType(activeTab === 'students' ? 'student' : activeTab === 'mentors' ? 'mentor' : 'teacher');
            setFormError("");
            setShowAddModal(true);
          }}
          className="flex items-center gap-2 px-5 py-3 bg-[#facc15] hover:bg-yellow-500 text-black font-bold rounded-xl shadow-lg transition-colors text-sm"
        >
          <Plus className="w-4 h-4" /> Add {activeTab === 'students' ? 'Student' : activeTab === 'mentors' ? 'Mentor' : 'Teacher'}
        </button>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Tabs, Search & Filters Bar */}
      <div className="flex flex-col md:flex-row gap-4 justify-between items-start md:items-center mb-6">
        {/* Tabs */}
        <div className="flex gap-2 p-1 bg-zinc-950 border border-white/5 rounded-xl w-max">
          <button
            onClick={() => setActiveTab('students')}
            className={`px-6 py-2.5 rounded-lg text-sm font-semibold transition-all ${
              activeTab === 'students'
                ? 'bg-zinc-800 text-white shadow-sm'
                : 'text-zinc-400 hover:text-white'
            }`}
          >
            Students
          </button>
          <button
            onClick={() => setActiveTab('teachers')}
            className={`px-6 py-2.5 rounded-lg text-sm font-semibold transition-all ${
              activeTab === 'teachers'
                ? 'bg-[#facc15] text-black shadow-sm'
                : 'text-zinc-400 hover:text-white'
            }`}
          >
            Teachers
          </button>
          <button
            onClick={() => setActiveTab('mentors')}
            className={`px-6 py-2.5 rounded-lg text-sm font-semibold transition-all ${
              activeTab === 'mentors'
                ? 'bg-[#facc15] text-black shadow-sm'
                : 'text-zinc-400 hover:text-white'
            }`}
          >
            Mentors
          </button>
        </div>

        {/* Filters */}
        <div className="flex flex-col sm:flex-row gap-3 w-full md:w-auto">
          {/* Search bar */}
          <div className="relative flex-1 sm:w-64">
            <span className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500">
              <Search className="w-4 h-4" />
            </span>
            <input
              type="text"
              placeholder="Search by name, contact..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full bg-zinc-900 border border-white/15 rounded-xl py-2.5 pl-9 pr-4 text-sm focus:outline-none focus:border-[#facc15] transition-colors"
            />
          </div>

          {/* Track Filter for Students */}
          {activeTab === 'students' && (
            <select
              value={trackFilter}
              onChange={(e: any) => setTrackFilter(e.target.value)}
              className="bg-zinc-900 border border-white/15 rounded-xl px-4 py-2.5 text-sm text-zinc-300 focus:outline-none focus:border-[#facc15] cursor-pointer"
            >
              <option value="all">All Tracks</option>
              <option value="CAREER_ACADEMY">Career Academy</option>
              <option value="REGULAR">Regular Track</option>
            </select>
          )}

          {/* Status Dropdown */}
          <select
            value={statusFilter}
            onChange={(e: any) => setStatusFilter(e.target.value)}
            className="bg-zinc-900 border border-white/15 rounded-xl px-4 py-2.5 text-sm text-zinc-300 focus:outline-none focus:border-[#facc15] cursor-pointer"
          >
            <option value="all">All Status</option>
            <option value="active">Active Accounts</option>
            <option value="inactive">Suspended Accounts</option>
          </select>
        </div>
      </div>

      {/* Users Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-xs text-zinc-400 uppercase tracking-wider">
                <th className="px-3 py-3.5 font-semibold">User details</th>
                <th className="px-3 py-3.5 font-semibold">Contact Info</th>
                <th className="px-3 py-3.5 font-semibold">{activeTab === 'students' ? 'Track & Location' : 'Parent details'}</th>
                <th className="px-3 py-3.5 font-semibold text-center whitespace-nowrap">{activeTab === 'students' ? 'Courses & Status' : 'Role'}</th>
                <th className="px-3 py-3.5 font-semibold whitespace-nowrap">Joined Date</th>
                <th className="px-3 py-3.5 font-semibold text-center whitespace-nowrap">Status</th>
                <th className="px-3 py-3.5 font-semibold text-center whitespace-nowrap">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-sm text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span>Loading user lists...</span>
                    </div>
                  </td>
                </tr>
              ) : displayedUsers.length === 0 ? (
                <tr>
                  <td colSpan={7} className="p-16 text-center text-zinc-500">
                    No active {activeTab} accounts matched this request.
                  </td>
                </tr>
              ) : (
                displayedUsers.map(user => (
                  <tr 
                    key={user.id}
                    onClick={() => router.push(`/admin/users/${user.id}`)}
                    className="hover:bg-white/5 transition-colors cursor-pointer"
                  >
                    {/* User Profile */}
                    <td className="p-4 font-semibold">
                      <div className="flex flex-col">
                        <span className="text-white font-bold">{user.first_name || user.username} {user.last_name || ""}</span>
                        <span className="text-xs text-zinc-500 font-normal">@{user.username}</span>
                      </div>
                    </td>

                    {/* Contact Info */}
                    <td className="p-4 text-xs font-normal">
                      <div className="space-y-0.5">
                        {user.email && <div className="text-zinc-300">{user.email}</div>}
                        {user.phone_number && <div className="text-zinc-400">{user.phone_number}</div>}
                        {!user.email && !user.phone_number && <span className="text-zinc-600 italic">None</span>}
                      </div>
                    </td>

                    {/* Student: Track & Location | Others: Parent Info */}
                    <td className="p-4 text-xs">
                      {activeTab === 'students' ? (
                        <div className="space-y-1">
                          <div>
                            {user.student_type === 'CAREER_ACADEMY' ? (
                              <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold bg-[#facc15]/15 text-[#facc15] border border-[#facc15]/30">
                                Career Academy
                              </span>
                            ) : user.student_type === 'REGULAR' ? (
                              <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold bg-purple-500/15 text-[#A78BFA] border border-purple-500/30">
                                Regular Track
                              </span>
                            ) : (
                              <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-zinc-800 text-zinc-400 border border-zinc-700">
                                Pending Selection
                              </span>
                            )}
                          </div>
                          {(user.state || user.district) && (
                            <div className="text-zinc-400 text-[11px] truncate max-w-[180px]">
                              {[user.district, user.state].filter(Boolean).join(", ")}
                              {user.pincode ? ` (${user.pincode})` : ''}
                            </div>
                          )}
                        </div>
                      ) : (
                        user.parent_name ? (
                          <div>
                            <div className="text-zinc-300 font-medium">{user.parent_name}</div>
                            {user.parent_phone && <div className="text-zinc-500 mt-0.5">{user.parent_phone}</div>}
                          </div>
                        ) : (
                          <span className="text-zinc-600 italic">Not set</span>
                        )
                      )}
                    </td>

                    {/* Enrolled Count & Onboarded Status OR Teacher/Mentor Badge */}
                    <td className="px-3 py-3.5 text-center whitespace-nowrap">
                      {activeTab === 'students' ? (
                        <div className="flex flex-col items-center gap-1">
                          <span className="inline-flex items-center whitespace-nowrap px-2.5 py-0.5 bg-zinc-800 text-zinc-200 rounded-md text-xs font-semibold">
                            {user.courses_count || 0} Enrolled
                          </span>
                          {user.is_onboarded ? (
                            <span className="text-[10px] text-green-400 font-medium">Complete</span>
                          ) : (
                            <span className="text-[10px] text-amber-400/90 font-medium">Pending</span>
                          )}
                        </div>
                      ) : (
                        <span className="inline-flex items-center whitespace-nowrap px-2.5 py-1 bg-zinc-800 text-[#facc15] rounded-md text-xs font-semibold">
                          {activeTab === 'mentors' ? 'Active Mentor' : 'Active Teacher'}
                        </span>
                      )}
                    </td>

                    {/* Joined Date */}
                    <td className="px-3 py-3.5 text-xs text-zinc-400 whitespace-nowrap">
                      {new Date(user.date_joined).toLocaleDateString()}
                    </td>

                    {/* Toggle Status switch */}
                    <td className="p-4 text-center" onClick={e => e.stopPropagation()}>
                      <button
                        onClick={(e) => handleToggleStatus(user.id, user.is_active, e)}
                        className={`w-10 h-6 rounded-full relative transition-colors ${
                          user.is_active ? 'bg-green-500/80' : 'bg-zinc-700'
                        }`}
                      >
                        <div className={`w-4 h-4 bg-white rounded-full absolute top-1 transition-transform ${
                          user.is_active ? 'translate-x-5' : 'translate-x-1'
                        }`} />
                      </button>
                    </td>

                    {/* Eye Details Link */}
                    <td className="p-4 text-center" onClick={e => e.stopPropagation()}>
                      <button
                        onClick={() => router.push(`/admin/users/${user.id}`)}
                        className="p-2 bg-white/5 border border-white/10 hover:border-white/20 rounded-xl transition-colors inline-flex items-center justify-center"
                        title="View detailed dashboard"
                      >
                        <Eye className="w-4 h-4 text-zinc-400 hover:text-white" />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination bar */}
        {totalCount > 0 && (
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4 px-6 py-4 bg-white/5 border-t border-white/5 text-sm text-zinc-400">
            <div>
              {isViewAll ? (
                <span>
                  Showing all <span className="font-semibold text-white">{totalCount}</span> users
                </span>
              ) : (
                <span>
                  Showing <span className="font-semibold text-white">{(currentPage - 1) * effectivePageSize + 1}</span> to{" "}
                  <span className="font-semibold text-white">
                    {Math.min(currentPage * effectivePageSize, totalCount)}
                  </span>{" "}
                  of <span className="font-semibold text-white">{totalCount}</span> users
                </span>
              )}
            </div>

            <div className="flex items-center gap-3">
              {/* Rows per page selector with View All option */}
              <div className="flex items-center gap-2 text-xs">
                <span className="text-zinc-500">Show:</span>
                <select
                  value={pageSize}
                  onChange={(e) => {
                    const val = e.target.value === 'all' ? 'all' : Number(e.target.value);
                    setPageSize(val);
                    setCurrentPage(1);
                  }}
                  className="bg-zinc-950 border border-white/10 hover:border-white/20 text-white text-xs rounded-xl px-2.5 py-1.5 focus:outline-none focus:border-[#facc15] transition-colors cursor-pointer"
                >
                  <option value={20}>20 per page</option>
                  <option value={50}>50 per page</option>
                  <option value={100}>100 per page</option>
                  <option value="all">View All (up to 100)</option>
                </select>
              </div>

              {/* View All quick button */}
              <button
                type="button"
                onClick={() => {
                  setPageSize(prev => (prev === 'all' ? 50 : 'all'));
                  setCurrentPage(1);
                }}
                className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all ${
                  isViewAll
                    ? "bg-[#facc15] text-black border-[#facc15] shadow-sm font-bold"
                    : "bg-zinc-950 text-zinc-400 border-white/10 hover:text-white hover:border-white/20"
                }`}
              >
                {isViewAll ? "Paginate (50)" : "View All"}
              </button>

              {/* Page navigation buttons */}
              {!isViewAll && totalPages >= 1 && (
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => setCurrentPage(1)}
                    disabled={currentPage === 1}
                    className="p-1.5 bg-zinc-950 border border-white/10 hover:border-white/20 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-colors text-zinc-300 hover:text-white"
                    title="First page"
                  >
                    <ChevronsLeft className="w-4 h-4" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
                    disabled={currentPage === 1}
                    className="px-2.5 py-1.5 bg-zinc-950 border border-white/10 hover:border-white/20 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-colors text-xs font-medium text-zinc-300 hover:text-white inline-flex items-center gap-1"
                    title="Previous page"
                  >
                    <ChevronLeft className="w-3.5 h-3.5" />
                    <span className="hidden sm:inline">Prev</span>
                  </button>
                  <div className="flex items-center gap-1 mx-1">
                    {buildPageItems(currentPage, totalPages).map((item, idx) =>
                      item === "ellipsis" ? (
                        <span key={`ellipsis-${idx}`} className="px-2 py-1 text-zinc-500 text-xs select-none">
                          &hellip;
                        </span>
                      ) : (
                        <button
                          key={item}
                          type="button"
                          onClick={() => setCurrentPage(item)}
                          className={`min-w-[32px] h-8 px-2.5 rounded-lg text-xs font-semibold transition-all border ${
                            item === currentPage
                              ? "bg-[#facc15] text-black border-[#facc15] shadow-sm shadow-[#facc15]/20"
                              : "bg-zinc-950 border-white/10 text-zinc-300 hover:bg-zinc-800 hover:text-white"
                          }`}
                        >
                          {item}
                        </button>
                      )
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => setCurrentPage(prev => Math.min(totalPages, prev + 1))}
                    disabled={currentPage === totalPages}
                    className="px-2.5 py-1.5 bg-zinc-950 border border-white/10 hover:border-white/20 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-colors text-xs font-medium text-zinc-300 hover:text-white inline-flex items-center gap-1"
                    title="Next page"
                  >
                    <span className="hidden sm:inline">Next</span>
                    <ChevronRight className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setCurrentPage(totalPages)}
                    disabled={currentPage === totalPages}
                    className="p-1.5 bg-zinc-950 border border-white/10 hover:border-white/20 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-colors text-zinc-300 hover:text-white"
                    title="Last page"
                  >
                    <ChevronsRight className="w-4 h-4" />
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Add User Modal */}
      <AnimatePresence>
        {showAddModal && (
          <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex justify-center p-4 overflow-y-auto">
            <motion.div
              initial={{ scale: 0.97, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.97, opacity: 0 }}
              role="dialog"
              aria-modal="true"
              aria-labelledby="add-user-title"
              className="my-auto bg-zinc-900 border border-white/10 rounded-2xl w-full max-w-2xl shadow-2xl"
            >
              {/* Header */}
              <div className="flex items-start justify-between gap-4 px-6 sm:px-8 pt-6 pb-5 border-b border-white/5">
                <div className="min-w-0">
                  <h2 id="add-user-title" className="text-xl font-bold">
                    Add New {modalType === 'student' ? 'Student' : modalType === 'mentor' ? 'Mentor' : 'Teacher'}
                  </h2>
                  <p className="text-zinc-400 text-sm mt-1">
                    {modalType === 'student'
                      ? "Students sign in with mobile number and OTP — no username or password needed."
                      : `Create a ${modalType} profile with sign-in details.`}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setShowAddModal(false)}
                  disabled={submitting}
                  aria-label="Close"
                  className="p-2 -mr-2 rounded-full text-zinc-400 hover:text-white hover:bg-white/5 transition-colors disabled:opacity-40 shrink-0"
                >
                  <X className="w-5 h-5" />
                </button>
              </div>

              <form onSubmit={handleAddUser}>
                <div className="px-6 sm:px-8 py-6 space-y-7">
                  {formError && (
                    <div role="alert" className="bg-red-500/10 border border-red-500/20 text-red-400 px-4 py-3 rounded-xl text-sm">
                      {formError}
                    </div>
                  )}

                  {/* Basic details */}
                  <section className="space-y-4">
                    <h3 className={SECTION_TITLE}>Basic details</h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <FormField label="First name" htmlFor="add-user-first-name">
                        <input
                          id="add-user-first-name"
                          type="text"
                          autoComplete="off"
                          placeholder="e.g. John"
                          value={formFields.first_name}
                          onChange={e => setFormFields({ ...formFields, first_name: e.target.value })}
                          className={INPUT_CLASS}
                        />
                      </FormField>
                      <FormField label="Last name" htmlFor="add-user-last-name">
                        <input
                          id="add-user-last-name"
                          type="text"
                          autoComplete="off"
                          placeholder="e.g. Doe"
                          value={formFields.last_name}
                          onChange={e => setFormFields({ ...formFields, last_name: e.target.value })}
                          className={INPUT_CLASS}
                        />
                      </FormField>
                      <FormField
                        label="Mobile number"
                        htmlFor="add-user-phone"
                        required
                        hint="Used for OTP sign-in."
                      >
                        <div className={PHONE_ROW_CLASS}>
                          <CountrySelect value={countryCode} onChange={setCountryCode} />
                          <input
                            id="add-user-phone"
                            type="tel"
                            inputMode="tel"
                            required
                            autoComplete="off"
                            placeholder="e.g. 8113900860"
                            value={formFields.phone_number}
                            onChange={e => setFormFields({ ...formFields, phone_number: e.target.value })}
                            className={`${INPUT_CLASS} flex-1 min-w-0`}
                          />
                        </div>
                      </FormField>
                      <FormField label="Email address" htmlFor="add-user-email" optional>
                        <input
                          id="add-user-email"
                          type="email"
                          autoComplete="off"
                          placeholder="e.g. user@email.com"
                          value={formFields.email}
                          onChange={e => setFormFields({ ...formFields, email: e.target.value })}
                          className={INPUT_CLASS}
                        />
                      </FormField>
                    </div>
                  </section>

                  {/* Teachers / mentors only: sign-in details. autoComplete stops the browser
                      pasting the admin's own saved login into these fields. */}
                  {modalType !== 'student' && (
                    <section className="space-y-4">
                      <h3 className={SECTION_TITLE}>Sign-in details</h3>
                      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <FormField label="Username" htmlFor="add-user-username" required>
                          <input
                            id="add-user-username"
                            type="text"
                            required
                            autoComplete="off"
                            placeholder="e.g. johndoe"
                            value={formFields.username}
                            onChange={e => setFormFields({ ...formFields, username: e.target.value })}
                            className={INPUT_CLASS}
                          />
                        </FormField>
                        <FormField label="Password" htmlFor="add-user-password" optional>
                          <input
                            id="add-user-password"
                            type="password"
                            autoComplete="new-password"
                            placeholder="Leave blank for OTP-only"
                            value={formFields.password}
                            onChange={e => setFormFields({ ...formFields, password: e.target.value })}
                            className={INPUT_CLASS}
                          />
                        </FormField>
                      </div>
                    </section>
                  )}

                  {modalType === 'student' && (
                    <>
                      {/* Learning track */}
                      <section className="space-y-4">
                        <h3 className={SECTION_TITLE}>Learning track <span className="text-[#facc15]">*</span></h3>
                        <div role="radiogroup" aria-label="Learning track" className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                          {LEARNING_TRACKS.map(track => {
                            const checked = formFields.student_type === track.value;
                            return (
                              <label
                                key={track.value}
                                className={`flex items-start gap-3 p-4 rounded-xl border cursor-pointer transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-[#facc15]/40 ${
                                  checked
                                    ? "border-[#facc15]/70 bg-[#facc15]/10"
                                    : "border-white/10 bg-zinc-800/50 hover:border-white/20"
                                }`}
                              >
                                <input
                                  type="radio"
                                  name="learning-track"
                                  value={track.value}
                                  checked={checked}
                                  onChange={() => setFormFields({ ...formFields, student_type: track.value })}
                                  className="sr-only"
                                />
                                <span
                                  aria-hidden="true"
                                  className={`mt-0.5 w-4 h-4 rounded-full border-2 shrink-0 flex items-center justify-center ${
                                    checked ? "border-[#facc15]" : "border-zinc-500"
                                  }`}
                                >
                                  {checked && <span className="w-2 h-2 rounded-full bg-[#facc15]" />}
                                </span>
                                <span className="min-w-0">
                                  <span className="block text-sm font-semibold text-white">{track.title}</span>
                                  <span className="block text-xs text-zinc-400 mt-0.5">{track.description}</span>
                                </span>
                              </label>
                            );
                          })}
                        </div>
                      </section>

                      {/* Location */}
                      <section className="space-y-4">
                        <h3 className={SECTION_TITLE}>Location <span className={OPTIONAL_TAG}>(optional)</span></h3>
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                          <FormField label="State" htmlFor="add-user-state">
                            <input
                              id="add-user-state"
                              type="text"
                              autoComplete="off"
                              placeholder="e.g. Kerala"
                              value={formFields.state}
                              onChange={e => setFormFields({ ...formFields, state: e.target.value })}
                              className={INPUT_CLASS}
                            />
                          </FormField>
                          <FormField label="District" htmlFor="add-user-district">
                            <input
                              id="add-user-district"
                              type="text"
                              autoComplete="off"
                              placeholder="e.g. Thrissur"
                              value={formFields.district}
                              onChange={e => setFormFields({ ...formFields, district: e.target.value })}
                              className={INPUT_CLASS}
                            />
                          </FormField>
                          <FormField label="Pincode" htmlFor="add-user-pincode">
                            <input
                              id="add-user-pincode"
                              type="text"
                              inputMode="numeric"
                              autoComplete="off"
                              placeholder="e.g. 680001"
                              value={formFields.pincode}
                              onChange={e => setFormFields({ ...formFields, pincode: e.target.value })}
                              className={INPUT_CLASS}
                            />
                          </FormField>
                        </div>
                      </section>

                      {/* Parent / guardian */}
                      <section className="space-y-4">
                        <h3 className={SECTION_TITLE}>Parent / guardian <span className={OPTIONAL_TAG}>(optional)</span></h3>
                        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                          <FormField label="Parent name" htmlFor="add-user-parent-name">
                            <input
                              id="add-user-parent-name"
                              type="text"
                              autoComplete="off"
                              placeholder="e.g. Arthur Doe"
                              value={formFields.parent_name}
                              onChange={e => setFormFields({ ...formFields, parent_name: e.target.value })}
                              className={INPUT_CLASS}
                            />
                          </FormField>
                          <FormField label="Parent phone" htmlFor="add-user-parent-phone">
                            <div className={PHONE_ROW_CLASS}>
                              <CountrySelect value={parentCountryCode} onChange={setParentCountryCode} />
                              <input
                                id="add-user-parent-phone"
                                type="tel"
                                inputMode="tel"
                                autoComplete="off"
                                placeholder="e.g. 8888888888"
                                value={formFields.parent_phone}
                                onChange={e => setFormFields({ ...formFields, parent_phone: e.target.value })}
                                className={`${INPUT_CLASS} flex-1 min-w-0`}
                              />
                            </div>
                          </FormField>
                        </div>
                      </section>
                    </>
                  )}
                </div>

                {/* Footer */}
                <div className="flex flex-col-reverse sm:flex-row sm:items-center sm:justify-between gap-3 px-6 sm:px-8 py-4 border-t border-white/5 bg-black/20 rounded-b-2xl">
                  <p className="text-xs text-zinc-500"><span className="text-[#facc15]">*</span> Required</p>
                  <div className="flex justify-end gap-3">
                    <button
                      type="button"
                      onClick={() => setShowAddModal(false)}
                      disabled={submitting}
                      className="px-4 py-2.5 text-sm font-semibold text-zinc-400 hover:text-white transition-colors disabled:opacity-50"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={submitting}
                      className="px-6 py-2.5 bg-[#facc15] hover:bg-yellow-500 text-black font-bold rounded-xl transition-colors disabled:opacity-50 text-sm"
                    >
                      {submitting ? "Creating..." : "Create Account"}
                    </button>
                  </div>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
