"use client";

import { useEffect, useState } from "react";
import { CalendarClock, Plus, Trash2 } from "lucide-react";

// Phase 2: a Teacher/Mentor's own weekly availability windows (feeds
// LiveClassSerializer's opt-in scheduling-conflict check). Self-service by
// default -- backend scopes GET/POST/DELETE to the caller; Admin can manage
// on any teacher/mentor's behalf via the `user` field.

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DAY_LABELS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const inputCls = "w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]";
const labelCls = "block text-[10px] font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide";

export default function AvailabilityPage() {
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [windows, setWindows] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [instructors, setInstructors] = useState<any[]>([]);
  const [selectedInstructorId, setSelectedInstructorId] = useState<string>("");

  const [showAdd, setShowAdd] = useState(false);
  const [dayOfWeek, setDayOfWeek] = useState(0);
  const [startTime, setStartTime] = useState("09:00");
  const [endTime, setEndTime] = useState("17:00");
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState<any>("");

  const authedFetch = (path: string, init?: RequestInit) =>
    fetch(`${API}${path}`, { credentials: "include", ...init });

  const isFullAdmin = !!(currentUser?.is_superuser || currentUser?.is_staff);

  useEffect(() => {
    authedFetch("/api/auth/user/").then(async (res) => {
      if (res.ok) setCurrentUser(await res.json());
    });
  }, []);

  useEffect(() => {
    if (!currentUser) return;
    if (currentUser.is_superuser || currentUser.is_staff) {
      authedFetch("/api/users/admin-users/").then(async (res) => {
        if (res.ok) {
          const all = await res.json();
          setInstructors(all.filter((u: any) => u.is_teacher || u.is_mentor));
        }
      });
    }
  }, [currentUser]);

  const fetchWindows = async () => {
    setLoading(true);
    setError("");
    try {
      const qs = isFullAdmin && selectedInstructorId ? `?user=${selectedInstructorId}` : "";
      const res = await authedFetch(`/api/courses/availability/${qs}`);
      if (res.ok) {
        const data = await res.json();
        setWindows(Array.isArray(data) ? data : data.results || []);
      } else {
        setError("Failed to load availability.");
      }
    } catch (err) {
      console.error(err);
      setError("Network error.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!currentUser) return;
    if (isFullAdmin && !selectedInstructorId) { setWindows([]); setLoading(false); return; }
    fetchWindows();
  }, [currentUser, selectedInstructorId]);

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setFormError("");
    try {
      const payload: any = { day_of_week: dayOfWeek, start_time: startTime, end_time: endTime };
      if (isFullAdmin && selectedInstructorId) payload.user = selectedInstructorId;
      const res = await authedFetch("/api/courses/availability/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) { setFormError(data); setSaving(false); return; }
      setShowAdd(false);
      fetchWindows();
    } catch (err) {
      console.error(err);
      setFormError("Network error.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    await authedFetch(`/api/courses/availability/${id}/`, { method: "DELETE" });
    fetchWindows();
  };

  const grouped = DAY_LABELS.map((label, idx) => ({
    label,
    windows: windows.filter((w) => w.day_of_week === idx).sort((a, b) => a.start_time.localeCompare(b.start_time)),
  }));

  return (
    <div className="max-w-3xl mx-auto pb-20">
      <div className="flex items-center justify-between mb-8 flex-wrap gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-[#facc15]/10 flex items-center justify-center text-[#facc15]">
            <CalendarClock className="w-5 h-5" />
          </div>
          <h1 className="text-3xl font-bold">Availability</h1>
        </div>
        <button onClick={() => setShowAdd((s) => !s)} disabled={isFullAdmin && !selectedInstructorId} className="px-5 py-2 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50 flex items-center gap-2">
          <Plus className="w-4 h-4" /> Add Window
        </button>
      </div>

      <p className="text-zinc-500 text-sm mb-6">
        Scheduling live classes outside these windows will be blocked. Leave everything empty and no restriction applies.
      </p>

      {isFullAdmin && (
        <div className="mb-6">
          <label className={labelCls}>Manage Availability For</label>
          <select value={selectedInstructorId} onChange={(e) => setSelectedInstructorId(e.target.value)} className={inputCls}>
            <option value="">Select a teacher or mentor</option>
            {instructors.map((u: any) => (
              <option key={u.id} value={u.id}>{(u.first_name || u.username)} {u.is_mentor ? "(Mentor)" : "(Teacher)"}</option>
            ))}
          </select>
        </div>
      )}

      {showAdd && (
        <form onSubmit={handleAdd} className="bg-zinc-900 border border-white/10 rounded-2xl p-5 mb-6 space-y-4">
          {formError && (
            <div className="px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-xs">
              {typeof formError === "string" ? formError : JSON.stringify(formError)}
            </div>
          )}
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className={labelCls}>Day</label>
              <select value={dayOfWeek} onChange={(e) => setDayOfWeek(Number(e.target.value))} className={inputCls}>
                {DAY_LABELS.map((d, idx) => <option key={d} value={idx}>{d}</option>)}
              </select>
            </div>
            <div>
              <label className={labelCls}>Start Time</label>
              <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} className={inputCls} />
            </div>
            <div>
              <label className={labelCls}>End Time</label>
              <input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} className={inputCls} />
            </div>
          </div>
          <p className="text-[10px] text-zinc-600">Add a second window on the same day (e.g. 09:00-12:00 and 13:00-17:00) to represent a break.</p>
          <div className="flex justify-end gap-3">
            <button type="button" onClick={() => setShowAdd(false)} className="px-4 py-2 text-zinc-400 hover:text-white text-sm">Cancel</button>
            <button type="submit" disabled={saving} className="px-5 py-2 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50">Save</button>
          </div>
        </form>
      )}

      {loading ? (
        <div className="text-center py-20 text-zinc-500 text-sm">Loading...</div>
      ) : error ? (
        <div className="text-center py-20 text-red-400 text-sm">{error}</div>
      ) : isFullAdmin && !selectedInstructorId ? (
        <div className="text-center py-20 text-zinc-500 text-sm">Select a teacher or mentor above to view or manage their availability.</div>
      ) : (
        <div className="space-y-3">
          {grouped.map(({ label, windows: dayWindows }) => (
            <div key={label} className="bg-zinc-900 border border-white/10 rounded-xl p-4 flex items-center justify-between">
              <span className="text-sm font-semibold text-white w-28 shrink-0">{label}</span>
              {dayWindows.length === 0 ? (
                <span className="text-xs text-zinc-600">Unavailable</span>
              ) : (
                <div className="flex flex-wrap gap-2 justify-end flex-1">
                  {dayWindows.map((w) => (
                    <div key={w.id} className="flex items-center gap-2 px-3 py-1.5 bg-black border border-white/10 rounded-lg text-xs text-zinc-300">
                      {w.start_time.slice(0, 5)} - {w.end_time.slice(0, 5)}
                      <button onClick={() => handleDelete(w.id)} className="text-zinc-600 hover:text-red-400"><Trash2 className="w-3 h-3" /></button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
