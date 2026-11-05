"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";
import { Search, Plus, X, ChevronLeft, ChevronRight, Check, Eye } from "lucide-react";
import CountrySelect from "@/components/CountrySelect";

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
  const [pageSize, setPageSize] = useState<number | 'all'>(8);

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
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setUsers(data);
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
  }, []);

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
    
    const formatPhone = (phone: string, code: string) => {
      if (!phone) return "";
      const trimmed = phone.trim();
      if (trimmed.startsWith("+")) return trimmed;
      const clean = trimmed.replace(/^0+/, "");
      return `${code}${clean}`;
    };

    const formattedPhone = formatPhone(formFields.phone_number, countryCode);
    const formattedParentPhone = formatPhone(formFields.parent_phone, parentCountryCode);

    // Formatting validation
    if (formattedPhone && !formattedPhone.startsWith('+')) {
      setFormError("Phone number must include country code (e.g. +919999999999).");
      return;
    }

    setSubmitting(true);
    
    const payload: any = {
      username: formFields.username,
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

    if (formFields.password) {
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
        if (typeof data === 'object') {
          const errors = Object.keys(data).map(key => `${key}: ${data[key]}`).join(" | ");
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

  // Filter lists based on Tab, Search, and Status dropdown
  const filteredUsers = users.filter(user => {
    // Role filter
    if (activeTab === 'students' && !user.is_student) return false;
    if (activeTab === 'teachers' && !user.is_teacher) return false;
    if (activeTab === 'mentors' && !user.is_mentor) return false;

    // Status filter
    if (statusFilter === 'active' && !user.is_active) return false;
    if (statusFilter === 'inactive' && user.is_active) return false;

    // Track filter for students
    if (activeTab === 'students' && trackFilter !== 'all' && user.student_type !== trackFilter) {
      return false;
    }

    // Search filter
    if (searchQuery) {
      const term = searchQuery.toLowerCase();
      const matchName = `${user.first_name || ""} ${user.last_name || ""}`.toLowerCase().includes(term);
      const matchUsername = (user.username || "").toLowerCase().includes(term);
      const matchEmail = (user.email || "").toLowerCase().includes(term);
      const matchPhone = (user.phone_number || "").toLowerCase().includes(term);
      const matchLocation = `${user.state || ""} ${user.district || ""}`.toLowerCase().includes(term);
      return matchName || matchUsername || matchEmail || matchPhone || matchLocation;
    }

    return true;
  });

  // Paginate list
  const isViewAll = pageSize === 'all';
  const effectivePageSize = isViewAll ? Math.max(1, filteredUsers.length) : pageSize;
  const totalPages = isViewAll ? 1 : Math.max(1, Math.ceil(filteredUsers.length / effectivePageSize));
  const displayedUsers = isViewAll
    ? filteredUsers
    : filteredUsers.slice((currentPage - 1) * effectivePageSize, currentPage * effectivePageSize);

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
                <th className="p-4 font-semibold">User details</th>
                <th className="p-4 font-semibold">Contact Info</th>
                <th className="p-4 font-semibold">{activeTab === 'students' ? 'Track & Location' : 'Parent details'}</th>
                <th className="p-4 font-semibold text-center">{activeTab === 'students' ? 'Courses & Status' : 'Role'}</th>
                <th className="p-4 font-semibold">Joined Date</th>
                <th className="p-4 font-semibold text-center">Status</th>
                <th className="p-4 font-semibold text-center">Actions</th>
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
                                🎓 Career Academy
                              </span>
                            ) : user.student_type === 'REGULAR' ? (
                              <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold bg-purple-500/15 text-[#A78BFA] border border-purple-500/30">
                                🧘 Regular Track
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
                    <td className="p-4 text-center">
                      {activeTab === 'students' ? (
                        <div className="flex flex-col items-center gap-0.5">
                          <span className="px-2 py-0.5 bg-zinc-800 text-zinc-200 rounded text-xs font-semibold">
                            {user.courses_count || 0} Enrolled
                          </span>
                          {user.is_onboarded ? (
                            <span className="text-[10px] text-green-400 font-medium">✓ Complete</span>
                          ) : (
                            <span className="text-[10px] text-amber-400/90 font-medium">⏳ Pending</span>
                          )}
                        </div>
                      ) : (
                        <span className="px-2 py-1 bg-zinc-800 text-[#facc15] rounded text-xs font-semibold">
                          {activeTab === 'mentors' ? 'Active Mentor' : 'Active Teacher'}
                        </span>
                      )}
                    </td>

                    {/* Joined Date */}
                    <td className="p-4 text-xs text-zinc-400">
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
        {filteredUsers.length > 0 && (
          <div className="flex flex-col sm:flex-row items-center justify-between gap-4 px-6 py-4 bg-white/5 border-t border-white/5 text-sm text-zinc-400">
            <div>
              {isViewAll ? (
                <span>
                  Showing all <span className="font-semibold text-white">{filteredUsers.length}</span> users
                </span>
              ) : (
                <span>
                  Showing <span className="font-semibold text-white">{(currentPage - 1) * effectivePageSize + 1}</span> to{" "}
                  <span className="font-semibold text-white">
                    {Math.min(currentPage * effectivePageSize, filteredUsers.length)}
                  </span>{" "}
                  of <span className="font-semibold text-white">{filteredUsers.length}</span> users
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
                  <option value={8}>8 per page</option>
                  <option value={20}>20 per page</option>
                  <option value={50}>50 per page</option>
                  <option value="all">View All ({filteredUsers.length})</option>
                </select>
              </div>

              {/* View All quick button */}
              <button
                type="button"
                onClick={() => {
                  setPageSize(prev => (prev === 'all' ? 8 : 'all'));
                  setCurrentPage(1);
                }}
                className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition-all ${
                  isViewAll
                    ? "bg-[#facc15] text-black border-[#facc15] shadow-sm font-bold"
                    : "bg-zinc-950 text-zinc-400 border-white/10 hover:text-white hover:border-white/20"
                }`}
              >
                {isViewAll ? "Paginate (8)" : "View All"}
              </button>

              {/* Page navigation buttons */}
              {!isViewAll && totalPages > 1 && (
                <div className="flex items-center gap-1.5">
                  <span className="text-xs text-zinc-500 mr-1 hidden sm:inline">
                    Page {currentPage} of {totalPages}
                  </span>
                  <button
                    onClick={() => setCurrentPage(prev => Math.max(1, prev - 1))}
                    disabled={currentPage === 1}
                    className="p-2 bg-zinc-950 border border-white/10 hover:border-white/20 rounded-xl disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    title="Previous page"
                  >
                    <ChevronLeft className="w-4 h-4" />
                  </button>
                  <button
                    onClick={() => setCurrentPage(prev => Math.min(totalPages, prev + 1))}
                    disabled={currentPage === totalPages}
                    className="p-2 bg-zinc-950 border border-white/10 hover:border-white/20 rounded-xl disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    title="Next page"
                  >
                    <ChevronRight className="w-4 h-4" />
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
          <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-lg shadow-2xl relative"
            >
              <button
                onClick={() => setShowAddModal(false)}
                className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
              >
                <X className="w-4 h-4" />
              </button>

              <h2 className="text-xl font-bold mb-1">Add New {modalType === 'student' ? 'Student' : modalType === 'mentor' ? 'Mentor' : 'Teacher'}</h2>
              <p className="text-zinc-400 text-xs mb-6">Create a profile in the database directory.</p>

              {formError && (
                <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-xl mb-4 text-xs font-normal">
                  {formError}
                </div>
              )}

              <form onSubmit={handleAddUser} className="space-y-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Username *</label>
                    <input
                      type="text"
                      required
                      placeholder="e.g. johndoe"
                      value={formFields.username}
                      onChange={e => setFormFields({ ...formFields, username: e.target.value })}
                      className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Password (Optional)</label>
                    <input
                      type="password"
                      placeholder="Leave blank for OTP-only"
                      value={formFields.password}
                      onChange={e => setFormFields({ ...formFields, password: e.target.value })}
                      className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Email Address</label>
                    <input
                      type="email"
                      placeholder="e.g. user@email.com"
                      value={formFields.email}
                      onChange={e => setFormFields({ ...formFields, email: e.target.value })}
                      className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Phone Number *</label>
                    <div className="flex gap-2 h-[42px]">
                      <CountrySelect
                        value={countryCode}
                        onChange={setCountryCode}
                      />
                      <input
                        type="tel"
                        required
                        placeholder="e.g. 8113900860"
                        value={formFields.phone_number}
                        onChange={e => setFormFields({ ...formFields, phone_number: e.target.value })}
                        className="flex-1 bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15] h-full"
                      />
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">First Name</label>
                    <input
                      type="text"
                      placeholder="e.g. John"
                      value={formFields.first_name}
                      onChange={e => setFormFields({ ...formFields, first_name: e.target.value })}
                      className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Last Name</label>
                    <input
                      type="text"
                      placeholder="e.g. Doe"
                      value={formFields.last_name}
                      onChange={e => setFormFields({ ...formFields, last_name: e.target.value })}
                      className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                </div>

                {modalType === 'student' && (
                  <div className="pt-4 border-t border-white/5 space-y-4">
                    <div>
                      <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Learning Track *</label>
                      <select
                        value={formFields.student_type}
                        onChange={e => setFormFields({ ...formFields, student_type: e.target.value })}
                        className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15] cursor-pointer"
                      >
                        <option value="CAREER_ACADEMY">🎓 Career Academy (Certified & Diploma)</option>
                        <option value="REGULAR">🧘 Regular Track (Flexible & Hobby)</option>
                      </select>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      <div>
                        <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">State</label>
                        <input
                          type="text"
                          placeholder="e.g. Karnataka"
                          value={formFields.state}
                          onChange={e => setFormFields({ ...formFields, state: e.target.value })}
                          className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">District</label>
                        <input
                          type="text"
                          placeholder="e.g. Bengaluru"
                          value={formFields.district}
                          onChange={e => setFormFields({ ...formFields, district: e.target.value })}
                          className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Pincode</label>
                        <input
                          type="text"
                          placeholder="e.g. 560001"
                          value={formFields.pincode}
                          onChange={e => setFormFields({ ...formFields, pincode: e.target.value })}
                          className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                        />
                      </div>
                    </div>

                    <h3 className="text-sm font-bold text-zinc-300 pt-2">Parent / Guardian Information</h3>
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div>
                        <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Parent Name</label>
                        <input
                          type="text"
                          placeholder="e.g. Arthur Doe"
                          value={formFields.parent_name}
                          onChange={e => setFormFields({ ...formFields, parent_name: e.target.value })}
                          className="w-full bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wider">Parent Phone</label>
                        <div className="flex gap-2 h-[42px]">
                          <CountrySelect
                            value={parentCountryCode}
                            onChange={setParentCountryCode}
                          />
                          <input
                            type="tel"
                            placeholder="e.g. 8888888888"
                            value={formFields.parent_phone}
                            onChange={e => setFormFields({ ...formFields, parent_phone: e.target.value })}
                            className="flex-1 bg-zinc-800 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15] h-full"
                          />
                        </div>
                      </div>
                    </div>
                  </div>
                )}

                <div className="flex justify-end gap-3 pt-6 border-t border-white/5">
                  <button
                    type="button"
                    onClick={() => setShowAddModal(false)}
                    className="px-4 py-2.5 text-sm font-semibold text-zinc-400 hover:text-white transition-colors"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    disabled={submitting}
                    className="px-5 py-2.5 bg-[#facc15] hover:bg-yellow-500 text-black font-bold rounded-xl transition-colors disabled:opacity-50 text-sm"
                  >
                    {submitting ? "Registering..." : "Create Account"}
                  </button>
                </div>
              </form>
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
