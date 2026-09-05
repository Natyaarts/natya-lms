"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ChevronLeft, Plus, Trash2, BookOpen, Users, DollarSign, Calendar, MessageSquare, Video, UserCircle } from "lucide-react";

export default function UserDetailPage() {
  const { id } = useParams();
  const router = useRouter();
  
  const [user, setUser] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [activeTab, setActiveTab] = useState('courses');

  // Courses and purchases lists
  const [courses, setCourses] = useState<any[]>([]);
  const [purchases, setPurchases] = useState<any[]>([]);
  const [teacherStudents, setTeacherStudents] = useState<any[]>([]);

  // Mentor: explicit student<->mentor assignments (Mentorship model) --
  // deliberately NOT derived from course enrollment.
  const [mentorships, setMentorships] = useState<any[]>([]);

  // Teacher/Mentor: live classes where this user is the assigned instructor.
  // Student: live classes they're assigned to via LiveBatchStudent.
  const [liveClasses, setLiveClasses] = useState<any[]>([]);
  const [liveClassesLoading, setLiveClassesLoading] = useState(false);

  // Teacher/Mentor: professional profile (TeacherProfile/MentorProfile --
  // kept separate from User identity/auth fields).
  const [profile, setProfile] = useState<any>(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileForm, setProfileForm] = useState<any>({});
  const [profileImageFile, setProfileImageFile] = useState<File | null>(null);
  const [profileSaving, setProfileSaving] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [profileSaved, setProfileSaved] = useState(false);

  // Dummy communication logs
  const [logs, setLogs] = useState<any[]>([]);

  // Assign Course Modal State
  const [showAssignModal, setShowAssignModal] = useState(false);
  const [allCourses, setAllCourses] = useState<any[]>([]);
  const [selectedCourse, setSelectedCourse] = useState("");
  const [assigning, setAssigning] = useState(false);

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

  const fetchUserData = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/`, {
        credentials: "include"
      });
      if (res.ok) {
        const userData = await res.json();
        setUser(userData);
        
        // Dynamic tab selection for teachers
        if (userData.is_teacher && !userData.is_student) {
          setActiveTab('courses');
        }
      }

      const coursesRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/courses/`, {
        credentials: "include"
      });
      if (coursesRes.ok) {
        setCourses(await coursesRes.json());
      }

      const purchasesRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/purchases/`, {
        credentials: "include"
      });
      if (purchasesRes.ok) {
        setPurchases(await purchasesRes.json());
      }
      
      // Mocking Interakt logs for reference
      setLogs([
        { id: 1, message: "Welcome to the platform! Here is your OTP: 123456", date: "2026-07-24T10:00:00Z" }
      ]);

    } catch (err) {
      console.error("Failed to load user details", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (id) fetchUserData();
  }, [id]);

  // Fetch teacher's student roster if role matches
  useEffect(() => {
    const fetchTeacherStudents = async () => {
      if (user && user.is_teacher) {
        try {
          const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/teacher-students/`, {
            credentials: "include"
          });
          if (res.ok) {
            setTeacherStudents(await res.json());
          }
        } catch (err) {
          console.error("Failed to load teacher students list", err);
        }
      }
    };
    fetchTeacherStudents();
  }, [user, id]);

  // Fetch mentor's assigned students (Mentorship model) if role matches
  useEffect(() => {
    const fetchMentorships = async () => {
      if (user && user.is_mentor) {
        try {
          const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/mentorships/?mentor=${id}`, {
            credentials: "include"
          });
          if (res.ok) setMentorships(await res.json());
        } catch (err) {
          console.error("Failed to load mentorships", err);
        }
      }
    };
    fetchMentorships();
  }, [user, id]);

  // Fetch live classes: instructor's own classes (teacher/mentor) or the
  // classes a student is assigned to -- reuses the existing LiveClass API's
  // ?instructor=/?student= filters, no fake data.
  useEffect(() => {
    const fetchLiveClasses = async () => {
      if (!user) return;
      const isInstructorRole = user.is_teacher || user.is_mentor;
      const param = isInstructorRole ? `instructor=${id}` : `student=${id}`;
      setLiveClassesLoading(true);
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/live-classes/?${param}&page_size=100`, {
          credentials: "include"
        });
        if (res.ok) {
          const data = await res.json();
          setLiveClasses(Array.isArray(data) ? data : (data.results || []));
        }
      } catch (err) {
        console.error("Failed to load live classes", err);
      } finally {
        setLiveClassesLoading(false);
      }
    };
    fetchLiveClasses();
  }, [user, id]);

  // Fetch Teacher/Mentor professional profile (created lazily server-side
  // on first access, so this never fails for an existing account).
  useEffect(() => {
    const fetchProfile = async () => {
      if (!user) return;
      const isTeacherRoleLocal = user.is_teacher && !user.is_student;
      const isMentorRoleLocal = user.is_mentor && !user.is_student && !isTeacherRoleLocal;
      if (!isTeacherRoleLocal && !isMentorRoleLocal) return;

      const endpoint = isTeacherRoleLocal ? 'teacher-profile' : 'mentor-profile';
      setProfileLoading(true);
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/${endpoint}/`, {
          credentials: "include"
        });
        if (res.ok) {
          const data = await res.json();
          setProfile(data);
          setProfileForm({
            bio: data.bio || "",
            specialization: data.specialization || "",
            qualifications: data.qualifications || "",
            experience_years: data.experience_years ?? "",
            languages: Array.isArray(data.languages) ? data.languages.join(", ") : "",
            short_intro: data.short_intro || "",
            availability_status: data.availability_status || "AVAILABLE",
            is_public: data.is_public ?? true,
            is_active: data.is_active ?? true,
          });
        }
      } catch (err) {
        console.error("Failed to load profile", err);
      } finally {
        setProfileLoading(false);
      }
    };
    fetchProfile();
  }, [user, id]);

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!user || profileSaving) return;
    const isTeacherRoleLocal = user.is_teacher && !user.is_student;
    const endpoint = isTeacherRoleLocal ? 'teacher-profile' : 'mentor-profile';

    setProfileSaving(true);
    setProfileError("");
    setProfileSaved(false);
    try {
      const formData = new FormData();
      formData.append("bio", profileForm.bio || "");
      formData.append("specialization", profileForm.specialization || "");
      formData.append("qualifications", profileForm.qualifications || "");
      if (profileForm.experience_years !== "") formData.append("experience_years", profileForm.experience_years);
      formData.append("languages", JSON.stringify(
        (profileForm.languages || "").split(",").map((s: string) => s.trim()).filter(Boolean)
      ));
      formData.append("is_public", String(!!profileForm.is_public));
      formData.append("is_active", String(!!profileForm.is_active));
      if (isTeacherRoleLocal) {
        formData.append("short_intro", profileForm.short_intro || "");
      } else {
        formData.append("availability_status", profileForm.availability_status || "AVAILABLE");
      }
      if (profileImageFile) formData.append("profile_image", profileImageFile);

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/${endpoint}/`, {
        method: "PATCH",
        headers: { "X-CSRFToken": getCsrfToken() },
        body: formData,
        credentials: "include"
      });
      if (res.ok) {
        const data = await res.json();
        setProfile(data);
        setProfileImageFile(null);
        setProfileSaved(true);
        setTimeout(() => setProfileSaved(false), 2500);
      } else {
        const data = await res.json().catch(() => ({}));
        setProfileError(typeof data === 'object' ? Object.values(data).flat().join(' ') : "Failed to save profile.");
      }
    } catch (err) {
      console.error(err);
      setProfileError("Network error saving profile.");
    } finally {
      setProfileSaving(false);
    }
  };

  // Fetch all courses list for the select box
  useEffect(() => {
    const fetchAllCourses = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/`, { credentials: "include" });
        if (res.ok) setAllCourses(await res.json());
      } catch (err) {}
    };
    fetchAllCourses();
  }, []);

  // Handle direct admin course enrollment
  const handleAssignCourse = async () => {
    if (!selectedCourse) return;
    setAssigning(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/enroll_course/`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({ course_id: selectedCourse }),
        credentials: 'include'
      });
      
      if (res.ok) {
        // Refresh assigned courses list
        const coursesRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/courses/`, { credentials: "include" });
        if (coursesRes.ok) setCourses(await coursesRes.json());
        
        // Refresh teacher students if teacher
        if (user?.is_teacher) {
          const studentsRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/teacher-students/`, { credentials: "include" });
          if (studentsRes.ok) setTeacherStudents(await studentsRes.json());
        }

        setShowAssignModal(false);
        setSelectedCourse("");
        alert("Course successfully enrolled!");
      } else {
        const data = await res.json();
        alert(data.error || "Failed to enroll user.");
      }
    } catch (err) {
      console.error(err);
      alert("Error enrolling course.");
    } finally {
      setAssigning(false);
    }
  };

  const handleUnassignCourse = async (courseId: number, courseTitle: string) => {
    if (!confirm(`Are you sure you want to unassign ${courseTitle}? This will remove classroom access.`)) return;
    
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/unassign_course/`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({ course_id: courseId }),
        credentials: 'include'
      });
      if (res.ok) {
        // Refresh assigned list
        const coursesRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/courses/`, { credentials: "include" });
        if (coursesRes.ok) setCourses(await coursesRes.json());
        
        // Refresh teacher students list if teacher
        if (user?.is_teacher) {
          const studentsRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/teacher-students/`, { credentials: "include" });
          if (studentsRes.ok) setTeacherStudents(await studentsRes.json());
        }
      } else {
        alert("Failed to unassign course.");
      }
    } catch (err) {
      console.error(err);
      alert("Error unassigning course.");
    }
  };

  const handleMarkAsPaid = async (purchaseId: number) => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/mark_purchase_paid/`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({ purchase_id: purchaseId }),
        credentials: 'include'
      });
      if (res.ok) {
        const purchasesRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/${id}/purchases/`, { credentials: "include" });
        if (purchasesRes.ok) setPurchases(await purchasesRes.json());
      }
    } catch (err) {
      console.error(err);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center text-[#facc15] font-sans">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
          <span>Loading profile detail...</span>
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen bg-black text-white p-8 font-sans">
        <div className="text-red-500 font-bold">User profile not found in directory.</div>
      </div>
    );
  }

  const isTeacherRole = user.is_teacher && !user.is_student;
  // Mentor is a distinct role from Teacher -- never treated as "another
  // name for Teacher." If a user somehow holds both flags, the Teacher tab
  // set takes precedence (matches admin/layout.tsx's nav role priority).
  const isMentorRole = user.is_mentor && !user.is_student && !isTeacherRole;

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Assign Course modal */}
      {showAssignModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-md shadow-2xl">
            <h2 className="text-xl font-bold mb-1">Assign Course</h2>
            <p className="text-zinc-400 text-xs mb-6">Create a direct student/teacher access enrollment.</p>
            
            <div className="mb-6">
              <label className="block text-xs font-semibold text-zinc-400 mb-2 uppercase tracking-wider">Select Course</label>
              <select 
                value={selectedCourse}
                onChange={(e) => setSelectedCourse(e.target.value)}
                className="w-full bg-zinc-800 border border-white/10 rounded-xl p-3 text-white text-sm focus:outline-none focus:border-[#facc15] cursor-pointer"
              >
                <option value="">-- Choose course from catalog --</option>
                {allCourses.map(c => (
                  <option key={c.id} value={c.id}>{c.title}</option>
                ))}
              </select>
            </div>

            <div className="flex justify-end gap-3 pt-4 border-t border-white/5">
              <button 
                onClick={() => setShowAssignModal(false)}
                className="px-4 py-2 text-sm font-semibold text-zinc-400 hover:text-white transition-colors"
              >
                Cancel
              </button>
              <button 
                onClick={handleAssignCourse}
                disabled={assigning || !selectedCourse}
                className="px-5 py-2 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 disabled:opacity-50 transition-colors text-sm"
              >
                {assigning ? "Assigning..." : "Assign Access"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <button 
          onClick={() => router.push('/admin/users')}
          className="p-3 bg-zinc-900 border border-white/5 hover:border-white/15 rounded-full transition-colors text-zinc-400 hover:text-white"
        >
          <ChevronLeft className="w-5 h-5" />
        </button>
        <div>
          <h1 className="text-3xl font-bold">{user.first_name || user.username} {user.last_name || ""}</h1>
          <div className="text-xs text-zinc-400 mt-1 flex flex-wrap gap-x-2 gap-y-1 items-center">
            {user.email && <span>{user.email} • </span>}
            {user.phone_number && <span>{user.phone_number} • </span>}
            <span>Joined {new Date(user.date_joined).toLocaleDateString()}</span>
            {user.parent_name && (
              <span className="ml-2 px-2 py-0.5 bg-zinc-800 text-zinc-400 rounded text-[10px]">
                Parent: {user.parent_name} ({user.parent_phone || "No phone"})
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-2 p-1 bg-zinc-950 border border-white/5 rounded-xl w-max mb-6">
        {(isTeacherRole || isMentorRole) && (
          <button
            onClick={() => setActiveTab('profile')}
            className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all flex items-center gap-2 ${
              activeTab === 'profile' ? 'bg-[#facc15] text-black shadow-sm' : 'text-zinc-400 hover:text-white'
            }`}
          >
            <UserCircle className="w-4 h-4" /> {isMentorRole ? 'Mentor Profile' : 'Teacher Profile'}
          </button>
        )}
        <button
          onClick={() => setActiveTab('courses')}
          className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all flex items-center gap-2 ${
            activeTab === 'courses' ? 'bg-zinc-800 text-white shadow-sm' : 'text-zinc-400 hover:text-white'
          }`}
        >
          <BookOpen className="w-4 h-4" /> Courses
        </button>
        
        {isTeacherRole ? (
          <>
            <button
              onClick={() => setActiveTab('students')}
              className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all flex items-center gap-2 ${
                activeTab === 'students' ? 'bg-[#facc15] text-black shadow-sm' : 'text-zinc-400 hover:text-white'
              }`}
            >
              <Users className="w-4 h-4" /> Enrolled Students
            </button>
            <button
              onClick={() => setActiveTab('live-classes')}
              className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all flex items-center gap-2 ${
                activeTab === 'live-classes' ? 'bg-[#facc15] text-black shadow-sm' : 'text-zinc-400 hover:text-white'
              }`}
            >
              <Video className="w-4 h-4" /> Live Classes
            </button>
          </>
        ) : isMentorRole ? (
          <>
            <button
              onClick={() => setActiveTab('students')}
              className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all flex items-center gap-2 ${
                activeTab === 'students' ? 'bg-[#facc15] text-black shadow-sm' : 'text-zinc-400 hover:text-white'
              }`}
            >
              <Users className="w-4 h-4" /> Assigned Students
            </button>
            <button
              onClick={() => setActiveTab('live-classes')}
              className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all flex items-center gap-2 ${
                activeTab === 'live-classes' ? 'bg-[#facc15] text-black shadow-sm' : 'text-zinc-400 hover:text-white'
              }`}
            >
              <Video className="w-4 h-4" /> Live Classes
            </button>
          </>
        ) : (
          <>
            <button
              onClick={() => setActiveTab('sessions')}
              className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all flex items-center gap-2 ${
                activeTab === 'sessions' ? 'bg-[#facc15] text-black shadow-sm' : 'text-zinc-400 hover:text-white'
              }`}
            >
              <Calendar className="w-4 h-4" /> Sessions
            </button>
            <button
              onClick={() => setActiveTab('fees')}
              className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all flex items-center gap-2 ${
                activeTab === 'fees' ? 'bg-[#facc15] text-black shadow-sm' : 'text-zinc-400 hover:text-white'
              }`}
            >
              <DollarSign className="w-4 h-4" /> Fees
            </button>
            <button
              onClick={() => setActiveTab('communication')}
              className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all flex items-center gap-2 ${
                activeTab === 'communication' ? 'bg-[#facc15] text-black shadow-sm' : 'text-zinc-400 hover:text-white'
              }`}
            >
              <MessageSquare className="w-4 h-4" /> Communication
            </button>
          </>
        )}
      </div>

      {/* Tab Panels */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6 shadow-2xl">
        {activeTab === 'profile' && (isTeacherRole || isMentorRole) && (
          <div>
            <h2 className="text-xl font-bold mb-1">{isMentorRole ? 'Mentor' : 'Teacher'} Profile</h2>
            <p className="text-zinc-400 text-xs mb-6">
              Professional/public-facing information, kept separate from the account's login identity.
            </p>

            {profileLoading ? (
              <div className="text-center py-16 text-zinc-500 text-sm">Loading...</div>
            ) : (
              <form onSubmit={handleSaveProfile} className="space-y-5 max-w-2xl">
                <div className="flex items-center gap-4">
                  <div className="w-16 h-16 rounded-full overflow-hidden bg-zinc-800 border border-white/10 shrink-0 flex items-center justify-center">
                    {profileImageFile ? (
                      <img src={URL.createObjectURL(profileImageFile)} alt="" className="w-full h-full object-cover" />
                    ) : profile?.profile_image ? (
                      <img src={profile.profile_image} alt="" className="w-full h-full object-cover" />
                    ) : (
                      <UserCircle className="w-8 h-8 text-zinc-600" />
                    )}
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Profile Photo</label>
                    <input
                      type="file"
                      accept="image/*"
                      onChange={(e) => setProfileImageFile(e.target.files?.[0] || null)}
                      className="text-xs text-zinc-400 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-[#facc15] file:text-black hover:file:bg-yellow-500 cursor-pointer"
                    />
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Specialization</label>
                    <input
                      type="text"
                      placeholder="e.g. Bharatanatyam, Carnatic Vocals"
                      value={profileForm.specialization}
                      onChange={(e) => setProfileForm({ ...profileForm, specialization: e.target.value })}
                      className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Experience (years)</label>
                    <input
                      type="number"
                      min="0"
                      value={profileForm.experience_years}
                      onChange={(e) => setProfileForm({ ...profileForm, experience_years: e.target.value })}
                      className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Languages</label>
                  <input
                    type="text"
                    placeholder="Comma-separated, e.g. English, Malayalam, Tamil"
                    value={profileForm.languages}
                    onChange={(e) => setProfileForm({ ...profileForm, languages: e.target.value })}
                    className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                  />
                </div>

                {isMentorRole && (
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Availability</label>
                    <select
                      value={profileForm.availability_status}
                      onChange={(e) => setProfileForm({ ...profileForm, availability_status: e.target.value })}
                      className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                    >
                      <option value="AVAILABLE">Available</option>
                      <option value="BUSY">Busy</option>
                      <option value="UNAVAILABLE">Unavailable</option>
                    </select>
                  </div>
                )}

                {isTeacherRole && (
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Short Intro</label>
                    <input
                      type="text"
                      maxLength={500}
                      placeholder="One-line intro shown on course/catalog pages (future use)"
                      value={profileForm.short_intro}
                      onChange={(e) => setProfileForm({ ...profileForm, short_intro: e.target.value })}
                      className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                )}

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Qualifications</label>
                  <textarea
                    rows={2}
                    value={profileForm.qualifications}
                    onChange={(e) => setProfileForm({ ...profileForm, qualifications: e.target.value })}
                    className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] resize-none"
                  />
                </div>

                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Bio</label>
                  <textarea
                    rows={4}
                    value={profileForm.bio}
                    onChange={(e) => setProfileForm({ ...profileForm, bio: e.target.value })}
                    className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] resize-vertical"
                  />
                </div>

                <div className="flex items-center gap-6">
                  <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={!!profileForm.is_public}
                      onChange={(e) => setProfileForm({ ...profileForm, is_public: e.target.checked })}
                      className="accent-[#facc15]"
                    />
                    Publicly visible profile (future use)
                  </label>
                  <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={!!profileForm.is_active}
                      onChange={(e) => setProfileForm({ ...profileForm, is_active: e.target.checked })}
                      className="accent-[#facc15]"
                    />
                    Profile active
                  </label>
                </div>

                {profileError && <p className="text-xs text-red-400">{profileError}</p>}
                {profileSaved && <p className="text-xs text-green-400">Profile saved.</p>}

                <div className="flex justify-end pt-4 border-t border-white/5">
                  <button
                    type="submit"
                    disabled={profileSaving}
                    className="px-5 py-2.5 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50 text-sm"
                  >
                    {profileSaving ? "Saving..." : "Save Profile"}
                  </button>
                </div>
              </form>
            )}
          </div>
        )}

        {activeTab === 'courses' && (
          <div>
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-xl font-bold">Assigned Courses</h2>
              <button 
                onClick={() => setShowAssignModal(true)}
                className="flex items-center gap-2 px-4 py-2 bg-[#facc15] hover:bg-yellow-500 text-black text-xs font-bold rounded-xl shadow-md transition-colors"
              >
                <Plus className="w-4 h-4" /> Assign Course
              </button>
            </div>
            
            {courses.length === 0 ? (
              <div className="text-center py-16 text-zinc-500 border border-dashed border-white/5 rounded-2xl text-sm">
                No courses assigned to this user.
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {courses.map(course => (
                  <div key={course.course_id} className="flex justify-between items-center p-4 bg-white/5 border border-white/10 rounded-2xl hover:border-white/15 transition-all">
                    <div className="flex gap-4 items-center">
                      <div className="w-16 h-12 rounded-xl overflow-hidden bg-black shrink-0 relative border border-white/5">
                        {course.thumbnail ? (
                          <img src={course.thumbnail} alt={course.title} className="w-full h-full object-cover" />
                        ) : (
                          <div className="w-full h-full bg-zinc-800 flex items-center justify-center text-xs text-zinc-500">No Image</div>
                        )}
                      </div>
                      <div>
                        <div className="font-bold text-white text-sm">{course.title}</div>
                        <p className="text-xs text-zinc-500 mt-1">Assigned on: {new Date(course.assigned_at || Date.now()).toLocaleDateString()}</p>
                      </div>
                    </div>
                    <div className="flex gap-3 items-center">
                      <button 
                        onClick={() => handleUnassignCourse(course.course_id, course.title)}
                        className="p-2 hover:bg-red-500/10 rounded-xl transition-colors text-red-500 hover:text-red-400 inline-flex items-center justify-center"
                        title="Revoke access"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                      <Link 
                        href={`/admin/courses/${course.course_id}`} 
                        className="text-xs text-[#facc15] hover:text-yellow-400 font-semibold underline"
                      >
                        View Course
                      </Link>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Teacher Tab: Enrolled Students List (from CourseInstructor + Enrollment) */}
        {activeTab === 'students' && isTeacherRole && (
          <div>
            <h2 className="text-xl font-bold mb-4">Students in Teacher's Courses</h2>
            <p className="text-zinc-400 text-xs mb-6">List of students enrolled in the courses taught by this instructor.</p>

            {teacherStudents.length === 0 ? (
              <div className="text-center py-16 text-zinc-500 border border-dashed border-white/5 rounded-2xl text-sm">
                No students are currently enrolled in this teacher's assigned courses.
              </div>
            ) : (
              <div className="bg-zinc-950 border border-white/5 rounded-2xl overflow-hidden">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                      <th className="p-4 font-semibold">Student Name</th>
                      <th className="p-4 font-semibold">Email</th>
                      <th className="p-4 font-semibold">Phone Number</th>
                      <th className="p-4 font-semibold">Joined Date</th>
                      <th className="p-4 font-semibold text-center">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 text-zinc-300">
                    {teacherStudents.map(student => (
                      <tr
                        key={student.id}
                        onClick={() => router.push(`/admin/users/${student.id}`)}
                        className="hover:bg-white/5 transition-colors cursor-pointer font-medium"
                      >
                        <td className="p-4 text-white font-bold">{student.first_name || student.username} {student.last_name || ""}</td>
                        <td className="p-4">{student.email || <span className="text-zinc-600">None</span>}</td>
                        <td className="p-4">{student.phone_number || <span className="text-zinc-600">None</span>}</td>
                        <td className="p-4 text-zinc-400">{new Date(student.date_joined).toLocaleDateString()}</td>
                        <td className="p-4 text-center">
                          <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${student.is_active ? 'bg-green-500/10 text-green-400 border border-green-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'}`}>
                            {student.is_active ? 'ACTIVE' : 'SUSPENDED'}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Mentor Tab: Assigned Students (from Mentorship model -- NOT enrollment) */}
        {activeTab === 'students' && isMentorRole && (
          <div>
            <h2 className="text-xl font-bold mb-4">Assigned Students</h2>
            <p className="text-zinc-400 text-xs mb-6">Students explicitly assigned to this mentor (independent of course enrollment).</p>

            {mentorships.length === 0 ? (
              <div className="text-center py-16 text-zinc-500 border border-dashed border-white/5 rounded-2xl text-sm">
                No students assigned to this mentor yet.
              </div>
            ) : (
              <div className="bg-zinc-950 border border-white/5 rounded-2xl overflow-hidden">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                      <th className="p-4 font-semibold">Student</th>
                      <th className="p-4 font-semibold">Assigned On</th>
                      <th className="p-4 font-semibold text-center">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 text-zinc-300">
                    {mentorships.map((m: any) => (
                      <tr
                        key={m.id}
                        onClick={() => router.push(`/admin/users/${m.student}`)}
                        className="hover:bg-white/5 transition-colors cursor-pointer font-medium"
                      >
                        <td className="p-4 text-white font-bold">{m.student_name}</td>
                        <td className="p-4 text-zinc-400">{new Date(m.assigned_at).toLocaleDateString()}</td>
                        <td className="p-4 text-center">
                          <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${m.status === 'ACTIVE' ? 'bg-green-500/10 text-green-400 border border-green-500/20' : 'bg-zinc-700/50 text-zinc-400 border border-white/10'}`}>
                            {m.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Teacher/Mentor Tab: Live Classes they instruct */}
        {activeTab === 'live-classes' && (isTeacherRole || isMentorRole) && (
          <div>
            <h2 className="text-xl font-bold mb-4">Live Classes</h2>
            <p className="text-zinc-400 text-xs mb-6">Sessions this {isMentorRole ? 'mentor' : 'teacher'} is scheduled to conduct.</p>

            {liveClassesLoading ? (
              <div className="text-center py-16 text-zinc-500 text-sm">Loading...</div>
            ) : liveClasses.length === 0 ? (
              <div className="text-center py-16 text-zinc-500 border border-dashed border-white/5 rounded-2xl text-sm">
                No live classes scheduled for this instructor.
              </div>
            ) : (
              <div className="bg-zinc-950 border border-white/5 rounded-2xl overflow-hidden">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                      <th className="p-4 font-semibold">Title</th>
                      <th className="p-4 font-semibold">Course</th>
                      <th className="p-4 font-semibold">Scheduled</th>
                      <th className="p-4 font-semibold text-center">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 text-zinc-300">
                    {liveClasses.map((lc: any) => (
                      <tr key={lc.id}>
                        <td className="p-4 text-white font-bold">{lc.title}</td>
                        <td className="p-4">{lc.course_title || lc.course}</td>
                        <td className="p-4 text-zinc-400">{new Date(lc.scheduled_start).toLocaleString()}</td>
                        <td className="p-4 text-center">
                          <span className="px-2 py-0.5 text-[10px] font-bold rounded bg-zinc-700/50 text-zinc-300 border border-white/10">
                            {lc.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {activeTab === 'sessions' && !isTeacherRole && !isMentorRole && (
          <div>
            <h2 className="text-xl font-bold mb-6">Upcoming & Past Sessions</h2>
            {liveClassesLoading ? (
              <div className="text-center py-16 text-zinc-500 text-sm">Loading...</div>
            ) : liveClasses.length === 0 ? (
              <div className="text-center py-16 text-zinc-500 border border-dashed border-white/5 rounded-2xl text-sm">
                No live coaching or interactive sessions scheduled.
              </div>
            ) : (
              <div className="bg-zinc-950 border border-white/5 rounded-2xl overflow-hidden">
                <table className="w-full text-left border-collapse text-xs">
                  <thead>
                    <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                      <th className="p-4 font-semibold">Title</th>
                      <th className="p-4 font-semibold">Course</th>
                      <th className="p-4 font-semibold">Scheduled</th>
                      <th className="p-4 font-semibold text-center">Status</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 text-zinc-300">
                    {liveClasses.map((lc: any) => (
                      <tr key={lc.id}>
                        <td className="p-4 text-white font-bold">{lc.title}</td>
                        <td className="p-4">{lc.course_title || lc.course}</td>
                        <td className="p-4 text-zinc-400">{new Date(lc.scheduled_start).toLocaleString()}</td>
                        <td className="p-4 text-center">
                          <span className="px-2 py-0.5 text-[10px] font-bold rounded bg-zinc-700/50 text-zinc-300 border border-white/10">
                            {lc.status}
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {activeTab === 'fees' && !isTeacherRole && !isMentorRole && (
          <div>
            <h2 className="text-xl font-bold mb-4">Payment & Fees History</h2>
            <p className="text-zinc-400 text-xs mb-6">Record of online Razorpay or manual admin billing transactions.</p>
            <div className="bg-zinc-950 border border-white/5 rounded-2xl overflow-hidden">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                    <th className="p-4 font-semibold">Course</th>
                    <th className="p-4 font-semibold">Purchase Date</th>
                    <th className="p-4 font-semibold">Amount Paid</th>
                    <th className="p-4 font-semibold">Receipt status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-zinc-300">
                  {purchases.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="p-16 text-center text-zinc-500">No transaction records found.</td>
                    </tr>
                  ) : (
                    purchases.map((purchase: any) => (
                      <tr key={purchase.id}>
                        <td className="p-4 text-white font-bold">{purchase.course_title}</td>
                        <td className="p-4 text-zinc-400">{new Date(purchase.created_at).toLocaleDateString()}</td>
                        <td className="p-4 text-[#facc15] font-bold">₹{purchase.amount}</td>
                        <td className="p-4">
                          <div className="flex items-center gap-3">
                            <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${purchase.status === 'SUCCESS' ? 'bg-green-500/10 text-green-400 border border-green-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'}`}>
                              {purchase.status === 'SUCCESS' ? 'PAID' : 'UNPAID'}
                            </span>
                            {purchase.status === 'PENDING' && (
                              <button 
                                onClick={() => handleMarkAsPaid(purchase.id)}
                                className="text-xs text-[#facc15] hover:text-yellow-400 font-semibold underline"
                              >
                                Mark as Paid
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
        )}

        {activeTab === 'communication' && !isTeacherRole && !isMentorRole && (
          <div>
            <h2 className="text-xl font-bold mb-4">Interakt WhatsApp Delivery Logs</h2>
            <p className="text-zinc-400 text-xs mb-6">Logs of template communication sent to phone numbers.</p>
            <div className="space-y-4">
              {logs.length === 0 ? (
                <div className="text-center py-16 text-zinc-500 border border-dashed border-white/5 rounded-2xl text-sm">
                  No messaging records found.
                </div>
              ) : (
                logs.map(log => (
                  <div key={log.id} className="p-4 bg-zinc-950 border border-white/5 rounded-2xl">
                    <div className="text-xs text-zinc-500 mb-2">{new Date(log.date).toLocaleString()}</div>
                    <div className="text-sm text-zinc-200 leading-relaxed">{log.message}</div>
                  </div>
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
