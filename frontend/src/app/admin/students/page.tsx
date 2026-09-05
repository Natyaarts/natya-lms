"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Users } from "lucide-react";

// "My Students" -- self-service view for a Teacher or Mentor. Backed by
// GET /api/users/me/students/, which returns the correct relationship for
// whichever role the caller actually has: a teacher's roster comes from
// CourseInstructor + Enrollment, a mentor's comes from the explicit
// Mentorship model. Never fake data -- an empty roster shows as empty.
export default function MyStudentsPage() {
  const router = useRouter();
  const [students, setStudents] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const fetchStudents = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/me/students/`, {
          credentials: "include"
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

  return (
    <div className="max-w-5xl mx-auto pb-20">
      <div className="flex items-center gap-3 mb-8">
        <div className="w-10 h-10 rounded-full bg-[#facc15]/10 flex items-center justify-center text-[#facc15]">
          <Users className="w-5 h-5" />
        </div>
        <h1 className="text-3xl font-bold">My Students</h1>
      </div>

      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden">
        {loading ? (
          <div className="text-center py-20 text-zinc-500 text-sm">Loading...</div>
        ) : error ? (
          <div className="text-center py-20 text-red-400 text-sm">{error}</div>
        ) : students.length === 0 ? (
          <div className="text-center py-20 text-zinc-500 text-sm">
            No students assigned to you yet.
          </div>
        ) : (
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Student</th>
                <th className="p-4 font-semibold">Email</th>
                <th className="p-4 font-semibold">Phone</th>
                <th className="p-4 font-semibold">Joined</th>
                <th className="p-4 font-semibold text-center">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {students.map((s: any) => (
                <tr
                  key={s.id}
                  onClick={() => router.push(`/admin/users/${s.id}`)}
                  className="hover:bg-white/5 transition-colors cursor-pointer font-medium"
                >
                  <td className="p-4 text-white font-bold">{s.first_name || s.username} {s.last_name || ""}</td>
                  <td className="p-4">{s.email || <span className="text-zinc-600">None</span>}</td>
                  <td className="p-4">{s.phone_number || <span className="text-zinc-600">None</span>}</td>
                  <td className="p-4 text-zinc-400">{new Date(s.date_joined).toLocaleDateString()}</td>
                  <td className="p-4 text-center">
                    <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${s.is_active ? 'bg-green-500/10 text-green-400 border border-green-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'}`}>
                      {s.is_active ? 'ACTIVE' : 'SUSPENDED'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
