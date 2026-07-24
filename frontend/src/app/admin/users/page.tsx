"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { motion, AnimatePresence } from "framer-motion";

export default function AdminUsers() {
  const router = useRouter();
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [activeTab, setActiveTab] = useState<'students' | 'employees'>('students');

  const fetchUsers = async () => {
    try {
      const res = await fetch("http://localhost:8000/api/users/admin-users/", {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setUsers(data);
      } else {
        setError("Failed to fetch users");
      }
    } catch (err) {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const toggleRole = async (userId: number, role: 'is_student' | 'is_teacher' | 'is_superuser', currentValue: boolean) => {
    try {
      const res = await fetch(`http://localhost:8000/api/users/admin-users/${userId}/`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ [role]: !currentValue }),
        credentials: "include",
      });
      if (res.ok) {
        fetchUsers();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const students = users.filter(u => !u.is_teacher && !u.is_superuser);
  const employees = users.filter(u => u.is_teacher || u.is_superuser);

  const renderStudentsTable = (data: any[]) => (
    <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden mt-6 shadow-xl">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="bg-white/5 border-b border-white/10 text-sm text-zinc-400">
            <th className="p-4 font-medium">Learner</th>
            <th className="p-4 font-medium">Parents</th>
            <th className="p-4 font-medium">Courses</th>
            <th className="p-4 font-medium">Joined on</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {loading ? (
            <tr><td colSpan={4} className="p-12 text-center text-zinc-500">Loading...</td></tr>
          ) : data.length === 0 ? (
            <tr><td colSpan={4} className="p-12 text-center text-zinc-500">No students found.</td></tr>
          ) : (
            data.map(user => (
              <tr 
                key={user.id} 
                onClick={() => router.push(`/admin/users/${user.id}`)}
                className="hover:bg-white/5 transition-colors cursor-pointer"
              >
                <td className="p-4 font-medium">
                  {user.username} 
                  {user.email && <span className="text-xs text-zinc-500 block font-normal">{user.email}</span>}
                  {user.phone_number && <span className="text-xs text-zinc-500 block font-normal">{user.phone_number}</span>}
                </td>
                <td className="p-4 text-sm text-zinc-300">
                  {user.parent_name || <span className="text-zinc-600 italic">Not provided</span>}
                  {user.parent_phone && <span className="text-xs text-zinc-500 block">{user.parent_phone}</span>}
                </td>
                <td className="p-4">
                  <span className="px-2 py-1 bg-zinc-800 text-zinc-300 rounded text-xs font-semibold">
                    {user.courses_count || 0} Enrolled
                  </span>
                </td>
                <td className="p-4 text-sm text-zinc-400">{new Date(user.date_joined).toLocaleDateString()}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );

  const renderEmployeesTable = (data: any[]) => (
    <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden mt-6 shadow-xl">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="bg-white/5 border-b border-white/10 text-sm text-zinc-400">
            <th className="p-4 font-medium">Username / Identifier</th>
            <th className="p-4 font-medium">Joined</th>
            <th className="p-4 font-medium text-center">Student</th>
            <th className="p-4 font-medium text-center">Mentor (Teacher)</th>
            <th className="p-4 font-medium text-center">Super Admin</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/5">
          {loading ? (
            <tr><td colSpan={5} className="p-12 text-center text-zinc-500">Loading...</td></tr>
          ) : data.length === 0 ? (
            <tr><td colSpan={5} className="p-12 text-center text-zinc-500">No employees found.</td></tr>
          ) : (
            data.map(user => (
              <tr key={user.id} className="hover:bg-white/5 transition-colors">
                <td className="p-4 font-medium">
                  {user.username} 
                  {user.email && <span className="text-xs text-zinc-500 block font-normal">{user.email}</span>}
                </td>
                <td className="p-4 text-sm text-zinc-400">{new Date(user.date_joined).toLocaleDateString()}</td>
                
                {/* Student Toggle */}
                <td className="p-4 text-center">
                  <button 
                    onClick={() => toggleRole(user.id, 'is_student', user.is_student)}
                    className={`w-10 h-6 rounded-full relative transition-colors ${user.is_student ? 'bg-green-500' : 'bg-zinc-700'}`}
                  >
                    <div className={`w-4 h-4 bg-white rounded-full absolute top-1 transition-transform ${user.is_student ? 'translate-x-5' : 'translate-x-1'}`} />
                  </button>
                </td>
                
                {/* Mentor Toggle */}
                <td className="p-4 text-center">
                  <button 
                    onClick={() => toggleRole(user.id, 'is_teacher', user.is_teacher)}
                    className={`w-10 h-6 rounded-full relative transition-colors ${user.is_teacher ? 'bg-[#facc15]' : 'bg-zinc-700'}`}
                  >
                    <div className={`w-4 h-4 bg-white rounded-full absolute top-1 transition-transform ${user.is_teacher ? 'translate-x-5' : 'translate-x-1'}`} />
                  </button>
                </td>
                
                {/* Super Admin Toggle */}
                <td className="p-4 text-center">
                  <button 
                    onClick={() => toggleRole(user.id, 'is_superuser', user.is_superuser)}
                    className={`w-10 h-6 rounded-full relative transition-colors ${user.is_superuser ? 'bg-red-500' : 'bg-zinc-700'}`}
                  >
                    <div className={`w-4 h-4 bg-white rounded-full absolute top-1 transition-transform ${user.is_superuser ? 'translate-x-5' : 'translate-x-1'}`} />
                  </button>
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );

  return (
    <div className="max-w-6xl mx-auto pb-20">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-8 gap-4">
        <h1 className="text-3xl font-bold">Manage Users</h1>
      </div>
      
      {error && <div className="bg-red-500/10 border border-red-500 text-red-500 p-4 rounded-xl mb-6">{error}</div>}
      
      {/* Tabs */}
      <div className="flex gap-2 p-1 bg-zinc-900 border border-white/10 rounded-xl w-max">
        <button
          onClick={() => setActiveTab('students')}
          className={`px-6 py-2 rounded-lg text-sm font-semibold transition-all ${
            activeTab === 'students' 
              ? 'bg-zinc-800 text-white shadow-sm' 
              : 'text-zinc-400 hover:text-white'
          }`}
        >
          Students ({students.length})
        </button>
        <button
          onClick={() => setActiveTab('employees')}
          className={`px-6 py-2 rounded-lg text-sm font-semibold transition-all ${
            activeTab === 'employees' 
              ? 'bg-[#facc15] text-black shadow-sm' 
              : 'text-zinc-400 hover:text-white'
          }`}
        >
          Employees ({employees.length})
        </button>
      </div>

      {/* Table Content */}
      {activeTab === 'students' ? renderStudentsTable(students) : renderEmployeesTable(employees)}
    </div>
  );
}
