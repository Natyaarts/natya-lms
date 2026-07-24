"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

export default function UserDetailPage() {
  const { id } = useParams();
  const router = useRouter();
  
  const [activeTab, setActiveTab] = useState('courses');
  const [user, setUser] = useState<any>(null);
  const [loading, setLoading] = useState(true);

  // Tabs Data
  const [courses, setCourses] = useState<any[]>([]);
  const [purchases, setPurchases] = useState<any[]>([]);
  
  // Dummy Data for now
  const [sessions, setSessions] = useState<any[]>([]);
  const [logs, setLogs] = useState<any[]>([]);

  useEffect(() => {
    const fetchUserData = async () => {
      try {
        const res = await fetch(`http://localhost:8000/api/users/admin-users/${id}/`, {
          credentials: "include"
        });
        if (res.ok) {
          setUser(await res.json());
        }

        const coursesRes = await fetch(`http://localhost:8000/api/users/admin-users/${id}/courses/`, {
          credentials: "include"
        });
        if (coursesRes.ok) {
          setCourses(await coursesRes.json());
        }

        const purchasesRes = await fetch(`http://localhost:8000/api/users/admin-users/${id}/purchases/`, {
          credentials: "include"
        });
        if (purchasesRes.ok) {
          setPurchases(await purchasesRes.json());
        }
        
        // Mocking Interakt logs for now
        setLogs([
          { id: 1, message: "Welcome to the platform! Here is your OTP: 123456", date: "2026-07-24T10:00:00Z" }
        ]);

      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    
    if (id) fetchUserData();
  }, [id]);

  const [showAssignModal, setShowAssignModal] = useState(false);
  const [allCourses, setAllCourses] = useState<any[]>([]);
  const [selectedCourse, setSelectedCourse] = useState("");
  const [assignAmount, setAssignAmount] = useState(0);
  const [paymentStatus, setPaymentStatus] = useState("SUCCESS");
  const [assigning, setAssigning] = useState(false);

  useEffect(() => {
    // Fetch all courses for the assign modal
    const fetchAllCourses = async () => {
      try {
        const res = await fetch("http://localhost:8000/api/courses/", { credentials: "include" });
        if (res.ok) setAllCourses(await res.json());
      } catch (err) {}
    };
    fetchAllCourses();
  }, []);

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

  const handleAssignCourse = async () => {
    if (!selectedCourse) return;
    setAssigning(true);
    try {
      const res = await fetch(`http://localhost:8000/api/users/admin-users/${id}/assign_course/`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({ 
          course_id: selectedCourse,
          amount: assignAmount,
          payment_status: paymentStatus
        }),
        credentials: 'include'
      });
      if (res.ok) {
        // Refresh assigned courses
        const coursesRes = await fetch(`http://localhost:8000/api/users/admin-users/${id}/courses/`, { credentials: "include" });
        if (coursesRes.ok) setCourses(await coursesRes.json());
        
        // Refresh purchases (fees history)
        const purchasesRes = await fetch(`http://localhost:8000/api/users/admin-users/${id}/purchases/`, { credentials: "include" });
        if (purchasesRes.ok) setPurchases(await purchasesRes.json());
        
        setShowAssignModal(false);
        setSelectedCourse("");
        setAssignAmount(0);
        setPaymentStatus("SUCCESS");
        alert("Course successfully assigned!");
      } else {
        const data = await res.json();
        alert(data.error || "Failed to assign course.");
      }
    } catch (err) {
      console.error(err);
      alert("Error assigning course.");
    } finally {
      setAssigning(false);
    }
  };

  const handleMarkAsPaid = async (purchaseId: number) => {
    try {
      const res = await fetch(`http://localhost:8000/api/users/admin-users/${id}/mark_purchase_paid/`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({ purchase_id: purchaseId }),
        credentials: 'include'
      });
      if (res.ok) {
        const purchasesRes = await fetch(`http://localhost:8000/api/users/admin-users/${id}/purchases/`, { credentials: "include" });
        if (purchasesRes.ok) setPurchases(await purchasesRes.json());
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleUnassignCourse = async (courseId: number, courseTitle: string) => {
    if (!confirm(`Are you sure you want to unassign ${courseTitle}? This will delete the purchase record.`)) return;
    
    try {
      const res = await fetch(`http://localhost:8000/api/users/admin-users/${id}/unassign_course/`, {
        method: 'POST',
        headers: { 
          'Content-Type': 'application/json',
          'X-CSRFToken': getCsrfToken()
        },
        body: JSON.stringify({ course_id: courseId }),
        credentials: 'include'
      });
      if (res.ok) {
        // Refresh assigned courses
        const coursesRes = await fetch(`http://localhost:8000/api/users/admin-users/${id}/courses/`, { credentials: "include" });
        if (coursesRes.ok) setCourses(await coursesRes.json());
        
        // Refresh purchases (fees history)
        const purchasesRes = await fetch(`http://localhost:8000/api/users/admin-users/${id}/purchases/`, { credentials: "include" });
        if (purchasesRes.ok) setPurchases(await purchasesRes.json());
      } else {
        alert("Failed to unassign course.");
      }
    } catch (err) {
      console.error(err);
      alert("Error unassigning course.");
    }
  };

  if (loading) {
    return <div className="text-zinc-500">Loading user details...</div>;
  }

  if (!user) {
    return <div className="text-red-500">User not found</div>;
  }

  return (
    <div>
      {showAssignModal && (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-md">
            <h2 className="text-xl font-bold mb-4">Assign Course</h2>
            <div className="mb-4">
              <label className="block text-sm font-medium text-zinc-400 mb-2">Select Course</label>
              <select 
                value={selectedCourse}
                onChange={(e) => {
                  setSelectedCourse(e.target.value);
                  const course = allCourses.find(c => c.id.toString() === e.target.value);
                  if (course) setAssignAmount(parseFloat(course.price));
                }}
                className="w-full bg-zinc-800 border border-white/10 rounded-md p-3 text-white focus:outline-none focus:border-[#facc15]"
              >
                <option value="">-- Choose a course --</option>
                {allCourses.map(c => (
                  <option key={c.id} value={c.id}>{c.title}</option>
                ))}
              </select>
            </div>

            {selectedCourse && (
              <>
                <div className="mb-4">
                  <label className="block text-sm font-medium text-zinc-400 mb-2">Payment Status</label>
                  <div className="flex gap-4">
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input 
                        type="radio" 
                        name="payment_status" 
                        value="SUCCESS" 
                        checked={paymentStatus === 'SUCCESS'}
                        onChange={(e) => setPaymentStatus(e.target.value)}
                        className="accent-[#facc15]"
                      />
                      <span>Paid in Full</span>
                    </label>
                    <label className="flex items-center gap-2 cursor-pointer">
                      <input 
                        type="radio" 
                        name="payment_status" 
                        value="PENDING" 
                        checked={paymentStatus === 'PENDING'}
                        onChange={(e) => setPaymentStatus(e.target.value)}
                        className="accent-[#facc15]"
                      />
                      <span>Unpaid / Pending</span>
                    </label>
                  </div>
                </div>

                <div className="mb-6">
                  <label className="block text-sm font-medium text-zinc-400 mb-2">
                    {paymentStatus === 'SUCCESS' ? 'Amount Paid' : 'Amount Due'}
                  </label>
                  <div className="relative">
                    <span className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400">₹</span>
                    <input 
                      type="number" 
                      value={assignAmount}
                      onChange={(e) => setAssignAmount(parseFloat(e.target.value))}
                      className="w-full bg-zinc-800 border border-white/10 rounded-md py-3 pl-8 pr-3 text-white focus:outline-none focus:border-[#facc15]"
                    />
                  </div>
                </div>
              </>
            )}

            <div className="flex justify-end gap-3">
              <button 
                onClick={() => setShowAssignModal(false)}
                className="px-4 py-2 text-zinc-400 hover:text-white transition-colors"
              >
                Cancel
              </button>
              <button 
                onClick={handleAssignCourse}
                disabled={assigning || !selectedCourse}
                className="px-4 py-2 bg-[#facc15] text-black font-bold rounded-md hover:bg-yellow-500 disabled:opacity-50 transition-colors"
              >
                {assigning ? "Assigning..." : "Assign"}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex items-center gap-4 mb-8">
        <button 
          onClick={() => router.push('/admin/users')}
          className="p-2 hover:bg-white/5 rounded-full transition-colors"
        >
          <svg className="w-6 h-6 text-zinc-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
          </svg>
        </button>
        <div>
          <h1 className="text-3xl font-bold">{user.first_name || user.username}</h1>
          <div className="text-sm text-zinc-400 mt-1">
            {user.email && <span>{user.email} • </span>}
            {user.phone_number && <span>{user.phone_number} • </span>}
            Joined {new Date(user.date_joined).toLocaleDateString()}
          </div>
        </div>
      </div>

      <div className="flex gap-1 bg-white/5 p-1 rounded-lg w-max mb-6">
        <button 
          onClick={() => setActiveTab('courses')}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${activeTab === 'courses' ? 'bg-[#facc15] text-black' : 'text-zinc-400 hover:text-white'}`}
        >
          Courses
        </button>
        <button 
          onClick={() => setActiveTab('sessions')}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${activeTab === 'sessions' ? 'bg-[#facc15] text-black' : 'text-zinc-400 hover:text-white'}`}
        >
          Sessions
        </button>
        <button 
          onClick={() => setActiveTab('fees')}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${activeTab === 'fees' ? 'bg-[#facc15] text-black' : 'text-zinc-400 hover:text-white'}`}
        >
          Fees
        </button>
        <button 
          onClick={() => setActiveTab('communication')}
          className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${activeTab === 'communication' ? 'bg-[#facc15] text-black' : 'text-zinc-400 hover:text-white'}`}
        >
          Communication
        </button>
      </div>

      <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6 shadow-xl">
        {activeTab === 'courses' && (
          <div>
            <div className="flex justify-between items-center mb-6">
              <h2 className="text-xl font-semibold">Assigned Courses</h2>
              <button 
                onClick={() => setShowAssignModal(true)}
                className="px-4 py-2 bg-[#facc15] text-black text-sm font-bold rounded-md hover:bg-yellow-500 transition-colors"
              >
                Assign Course
              </button>
            </div>
            
            {courses.length === 0 ? (
              <div className="text-center py-12 text-zinc-500 border border-dashed border-white/10 rounded-xl">
                No courses assigned to this student.
              </div>
            ) : (
              <div className="space-y-4">
                {courses.map(course => (
                  <div key={course.id} className="flex justify-between items-center p-4 bg-white/5 border border-white/10 rounded-xl">
                    <div>
                      <div className="font-semibold">{course.title}</div>
                      <p className="text-sm text-zinc-500 mt-1">Assigned on: {new Date(course.assigned_at || Date.now()).toLocaleDateString()}</p>
                    </div>
                    <div className="flex gap-4 items-center">
                      <button 
                        onClick={() => handleUnassignCourse(course.course_id, course.title)}
                        className="text-sm font-medium text-red-500 hover:text-red-400 transition-colors underline"
                      >
                        Remove
                      </button>
                      <Link href={`/admin/courses/${course.course_id}`} className="text-sm font-medium text-zinc-300 hover:text-white transition-colors underline">
                        View Course
                      </Link>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {activeTab === 'sessions' && (
          <div>
            <h2 className="text-xl font-semibold mb-6">Sessions</h2>
            <div className="text-center py-12 text-zinc-500 border border-dashed border-white/10 rounded-xl">
              No upcoming or past sessions found.
            </div>
          </div>
        )}

        {activeTab === 'fees' && (
          <div>
            <h2 className="text-xl font-semibold mb-6">Payment & Fees History</h2>
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-white/5 border-b border-white/10 text-sm text-zinc-400">
                  <th className="p-4 font-medium">Course</th>
                  <th className="p-4 font-medium">Date</th>
                  <th className="p-4 font-medium">Amount Paid</th>
                  <th className="p-4 font-medium">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {purchases.length === 0 ? (
                  <tr><td colSpan={4} className="p-12 text-center text-zinc-500">No payment records found.</td></tr>
                ) : (
                  purchases.map((purchase: any) => (
                    <tr key={purchase.id}>
                      <td className="p-4 font-medium">{purchase.course_title}</td>
                      <td className="p-4 text-sm text-zinc-400">{new Date(purchase.created_at).toLocaleDateString()}</td>
                      <td className="p-4 text-[#facc15] font-semibold">₹{purchase.amount}</td>
                      <td className="p-4">
                        <div className="flex items-center gap-3">
                          <span className={`px-2 py-1 text-xs font-semibold rounded ${purchase.status === 'SUCCESS' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
                            {purchase.status === 'SUCCESS' ? 'PAID' : 'UNPAID'}
                          </span>
                          {purchase.status === 'PENDING' && (
                            <button 
                              onClick={() => handleMarkAsPaid(purchase.id)}
                              className="text-xs text-[#facc15] hover:text-yellow-400 font-medium underline"
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
        )}

        {activeTab === 'communication' && (
          <div>
            <h2 className="text-xl font-semibold mb-6">Interakt / WhatsApp History</h2>
            <div className="space-y-4">
              {logs.length === 0 ? (
                <div className="text-center py-12 text-zinc-500 border border-dashed border-white/10 rounded-xl">
                  No communication logs found.
                </div>
              ) : (
                logs.map(log => (
                  <div key={log.id} className="p-4 bg-white/5 border border-white/10 rounded-xl">
                    <div className="text-sm text-zinc-400 mb-2">{new Date(log.date).toLocaleString()}</div>
                    <div className="text-zinc-200">{log.message}</div>
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
