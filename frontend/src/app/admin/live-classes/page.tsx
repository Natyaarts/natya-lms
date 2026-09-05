"use client";

import { useEffect, useMemo, useState } from "react";
import { Video, Plus, X, Repeat, Users as UsersIcon, Calendar as CalendarIcon, List as ListIcon } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
import MonthCalendar from "@/components/live-classes/MonthCalendar";

// Phase 2: full scheduling/management UI for Admin, Teacher and Mentor --
// the backend (LiveClassViewSet/LiveBatchViewSet) already scopes data and
// write access per-role, so this single page serves all three; it just
// adapts which affordances it shows (e.g. an instructor picker only appears
// for Admin, since Teacher/Mentor can only ever act as themselves -- the
// backend enforces that regardless of what this page renders).

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const PROVIDER_LABEL: Record<string, string> = {
  ZOOM: "Zoom", GOOGLE_MEET: "Google Meet", TEAMS: "Teams", OTHER: "Other",
};
const STATUS_STYLE: Record<string, string> = {
  SCHEDULED: "bg-zinc-700/50 text-zinc-300 border border-white/10",
  LIVE: "bg-green-500/10 text-green-400 border border-green-500/20",
  COMPLETED: "bg-blue-500/10 text-blue-400 border border-blue-500/20",
  CANCELLED: "bg-red-500/10 text-red-400 border border-red-500/20",
};
// Backend weekday convention (TeacherAvailability.Weekday / RecurrenceRule):
// Monday=0 .. Sunday=6 -- NOT JS Date.getDay()'s Sunday=0.
const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const jsDayToBackend = (jsDay: number) => (jsDay + 6) % 7;

function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) {
  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        onClick={(e) => e.stopPropagation()}
        className={`w-full ${wide ? "max-w-2xl" : "max-w-md"} bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden max-h-[85vh] flex flex-col`}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/5 shrink-0">
          <h3 className="font-bold text-white">{title}</h3>
          <button onClick={onClose} className="text-zinc-500 hover:text-white"><X className="w-4 h-4" /></button>
        </div>
        <div className="p-5 overflow-y-auto">{children}</div>
      </motion.div>
    </div>
  );
}

const inputCls = "w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]";
const labelCls = "block text-[10px] font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide";
const btnPrimary = "px-5 py-2 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50";
const btnGhost = "px-4 py-2 text-zinc-400 hover:text-white text-sm";

export default function LiveClassesPage() {
  const [currentUser, setCurrentUser] = useState<any>(null);
  const [tab, setTab] = useState<"today" | "upcoming" | "history" | "cancelled">("upcoming");
  const [view, setView] = useState<"list" | "calendar">("list");
  const [classes, setClasses] = useState<any[]>([]);
  const [calendarClasses, setCalendarClasses] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [banner, setBanner] = useState("");

  const isFullAdmin = !!(currentUser?.is_superuser || currentUser?.is_staff);

  const authedFetch = (path: string, init?: RequestInit) =>
    fetch(`${API}${path}`, { credentials: "include", ...init });

  useEffect(() => {
    authedFetch("/api/auth/user/").then(async (res) => {
      if (res.ok) setCurrentUser(await res.json());
    });
  }, []);

  const fetchClasses = async () => {
    setLoading(true);
    setError("");
    try {
      const path = tab === "cancelled"
        ? "/api/courses/live-classes/?status=CANCELLED&page_size=100"
        : `/api/courses/live-classes/${tab}/?page_size=100`;
      const res = await authedFetch(path);
      if (res.ok) {
        const data = await res.json();
        setClasses(Array.isArray(data) ? data : data.results || []);
      } else {
        setError("Failed to load live classes.");
      }
    } catch (err) {
      console.error(err);
      setError("Network error loading live classes.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchClasses(); }, [tab]);

  // Calendar view pulls a broader, unfiltered-by-tab window (upcoming +
  // history) so the month grid can show everything at a glance.
  useEffect(() => {
    if (view !== "calendar") return;
    (async () => {
      try {
        const [up, hist] = await Promise.all([
          authedFetch("/api/courses/live-classes/upcoming/?page_size=200"),
          authedFetch("/api/courses/live-classes/history/?page_size=200"),
        ]);
        const upData = up.ok ? await up.json() : { results: [] };
        const histData = hist.ok ? await hist.json() : { results: [] };
        const merged = [...(upData.results || upData), ...(histData.results || histData)];
        setCalendarClasses(merged);
      } catch (err) {
        console.error(err);
      }
    })();
  }, [view]);

  // ---- Schedule form state ----
  const [showSchedule, setShowSchedule] = useState(false);
  const [courses, setCourses] = useState<any[]>([]);
  const [batches, setBatches] = useState<any[]>([]);
  const [rosterStudents, setRosterStudents] = useState<any[]>([]);
  const [allUsers, setAllUsers] = useState<any[]>([]);
  const [scheduling, setScheduling] = useState(false);
  const [scheduleError, setScheduleError] = useState<any>("");

  const emptyForm = {
    courseId: "",
    batchChoice: "new" as "new" | string,
    batchType: "GROUP",
    maxParticipants: "",
    instructorId: "",
    studentIds: [] as number[],
    title: "",
    description: "",
    date: "",
    time: "",
    durationMinutes: 60,
    provider: "ZOOM",
    meetingUrl: "",
    recurrenceEnabled: false,
    frequency: "WEEKLY",
    weekdays: [] as number[],
    endDate: "",
    occurrenceCount: "",
  };
  const [form, setForm] = useState(emptyForm);

  const openSchedule = async () => {
    setForm(emptyForm);
    setScheduleError("");
    setShowSchedule(true);
    try {
      const cRes = await authedFetch("/api/courses/");
      if (cRes.ok) {
        const all = await cRes.json();
        setCourses((Array.isArray(all) ? all : all.results || []).filter((c: any) => c.course_type === "LIVE"));
      }
      const bRes = await authedFetch("/api/courses/live-batches/?page_size=200");
      if (bRes.ok) {
        const bAll = await bRes.json();
        setBatches(Array.isArray(bAll) ? bAll : bAll.results || []);
      }
      if (isFullAdmin) {
        const uRes = await authedFetch("/api/users/admin-users/");
        if (uRes.ok) setAllUsers(await uRes.json());
      } else {
        const rRes = await authedFetch("/api/users/me/students/");
        if (rRes.ok) {
          const rData = await rRes.json();
          setRosterStudents(Array.isArray(rData) ? rData : rData.results || []);
        }
      }
    } catch (err) {
      console.error(err);
    }
  };

  const eligibleInstructors = useMemo(
    () => allUsers.filter((u) => u.is_teacher || u.is_mentor),
    [allUsers]
  );
  const eligibleStudents = useMemo(
    () => (isFullAdmin ? allUsers.filter((u) => u.is_student) : rosterStudents),
    [allUsers, rosterStudents, isFullAdmin]
  );
  const batchesForCourse = useMemo(
    () => batches.filter((b) => String(b.course) === String(form.courseId)),
    [batches, form.courseId]
  );

  const toggleWeekday = (d: number) => {
    setForm((f) => ({
      ...f,
      weekdays: f.weekdays.includes(d) ? f.weekdays.filter((x) => x !== d) : [...f.weekdays, d],
    }));
  };
  const toggleStudent = (id: number) => {
    setForm((f) => ({
      ...f,
      studentIds: f.studentIds.includes(id) ? f.studentIds.filter((x) => x !== id) : [...f.studentIds, id],
    }));
  };

  const handleSchedule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (scheduling) return;
    setScheduling(true);
    setScheduleError("");
    try {
      let batchId = form.batchChoice !== "new" ? form.batchChoice : null;

      if (!batchId) {
        const batchPayload: any = { course: form.courseId, batch_type: form.batchType };
        if (form.batchType === "GROUP" && form.maxParticipants) batchPayload.max_participants = form.maxParticipants;
        if (isFullAdmin && form.instructorId) batchPayload.instructor = form.instructorId;
        const bRes = await authedFetch("/api/courses/live-batches/", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(batchPayload),
        });
        const bData = await bRes.json();
        if (!bRes.ok) { setScheduleError(bData); setScheduling(false); return; }
        batchId = bData.id;

        for (const studentId of form.studentIds) {
          await authedFetch(`/api/courses/live-batches/${batchId}/students/`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ student_id: studentId }),
          });
        }
      }

      const scheduledStart = new Date(`${form.date}T${form.time}`).toISOString();
      const payload: any = {
        title: form.title,
        description: form.description,
        batch: batchId,
        scheduled_start: scheduledStart,
        duration_minutes: form.durationMinutes,
        meeting_provider: form.provider,
        meeting_url: form.meetingUrl,
      };
      if (form.recurrenceEnabled && form.frequency !== "ONE_TIME") {
        payload.recurrence = {
          frequency: form.frequency,
          weekdays: form.frequency === "WEEKLY" ? form.weekdays : [],
          end_date: form.endDate || null,
          occurrence_count: form.occurrenceCount ? Number(form.occurrenceCount) : null,
        };
      }

      const res = await authedFetch("/api/courses/live-classes/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (!res.ok) { setScheduleError(data); setScheduling(false); return; }

      setShowSchedule(false);
      setBanner(Array.isArray(data) ? `Scheduled ${data.length} sessions.` : "Class scheduled.");
      fetchClasses();
    } catch (err) {
      console.error(err);
      setScheduleError("Network error.");
    } finally {
      setScheduling(false);
    }
  };

  // ---- Row actions ----
  const [busyId, setBusyId] = useState<number | null>(null);

  const startClass = async (lc: any) => {
    setBusyId(lc.id);
    await authedFetch(`/api/courses/live-classes/${lc.id}/start/`, { method: "POST" });
    setBusyId(null);
    fetchClasses();
  };
  const endClass = async (lc: any) => {
    setBusyId(lc.id);
    await authedFetch(`/api/courses/live-classes/${lc.id}/end/`, { method: "POST" });
    setBusyId(null);
    fetchClasses();
  };

  const [cancelTarget, setCancelTarget] = useState<any>(null);
  const [cancelReason, setCancelReason] = useState("");
  const [cancelSeries, setCancelSeries] = useState(false);
  const submitCancel = async () => {
    if (!cancelTarget) return;
    setBusyId(cancelTarget.id);
    const path = cancelSeries
      ? `/api/courses/live-classes/${cancelTarget.id}/cancel-series/`
      : `/api/courses/live-classes/${cancelTarget.id}/cancel/`;
    await authedFetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: cancelReason }),
    });
    setBusyId(null);
    setCancelTarget(null);
    setCancelReason("");
    setCancelSeries(false);
    fetchClasses();
  };

  const [rescheduleTarget, setRescheduleTarget] = useState<any>(null);
  const [rescheduleDate, setRescheduleDate] = useState("");
  const [rescheduleTime, setRescheduleTime] = useState("");
  const [rescheduleError, setRescheduleError] = useState<any>("");
  const submitReschedule = async () => {
    if (!rescheduleTarget) return;
    setRescheduleError("");
    setBusyId(rescheduleTarget.id);
    const newStart = new Date(`${rescheduleDate}T${rescheduleTime}`).toISOString();
    const res = await authedFetch(`/api/courses/live-classes/${rescheduleTarget.id}/reschedule/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ scheduled_start: newStart }),
    });
    const data = await res.json();
    setBusyId(null);
    if (!res.ok) { setRescheduleError(data); return; }
    setRescheduleTarget(null);
    fetchClasses();
  };

  const [attendanceTarget, setAttendanceTarget] = useState<any>(null);
  const [attendanceRoster, setAttendanceRoster] = useState<any[]>([]);
  const [attendanceRecords, setAttendanceRecords] = useState<Record<number, string>>({});
  const openAttendance = async (lc: any) => {
    setAttendanceTarget(lc);
    setAttendanceRecords({});
    if (lc.batch) {
      const res = await authedFetch(`/api/courses/live-batches/${lc.batch}/students/?page_size=200`);
      if (res.ok) {
        const data = await res.json();
        setAttendanceRoster(Array.isArray(data) ? data : data.results || []);
      }
    }
    const aRes = await authedFetch(`/api/courses/live-classes/${lc.id}/attendance/`);
    if (aRes.ok) {
      const existing = await aRes.json();
      const map: Record<number, string> = {};
      for (const rec of existing) map[rec.student] = rec.status;
      setAttendanceRecords(map);
    }
  };
  const submitAttendance = async () => {
    if (!attendanceTarget) return;
    const records = attendanceRoster.map((s) => ({
      student: s.student,
      status: attendanceRecords[s.student] || "ABSENT",
    }));
    await authedFetch(`/api/courses/live-classes/${attendanceTarget.id}/attendance/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(records),
    });
    setAttendanceTarget(null);
  };

  const [recordingTarget, setRecordingTarget] = useState<any>(null);
  const [recordingUrl, setRecordingUrl] = useState("");
  const submitRecording = async () => {
    if (!recordingTarget) return;
    setBusyId(recordingTarget.id);
    await authedFetch(`/api/courses/live-classes/${recordingTarget.id}/recording/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recording_url: recordingUrl }),
    });
    setBusyId(null);
    setRecordingTarget(null);
    setRecordingUrl("");
    fetchClasses();
  };

  return (
    <div className="max-w-6xl mx-auto pb-20">
      <div className="flex items-center justify-between mb-8 flex-wrap gap-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-full bg-[#facc15]/10 flex items-center justify-center text-[#facc15]">
            <Video className="w-5 h-5" />
          </div>
          <h1 className="text-3xl font-bold">Live Classes</h1>
        </div>
        <button onClick={openSchedule} disabled={!currentUser} className={`${btnPrimary} flex items-center gap-2`}>
          <Plus className="w-4 h-4" /> Schedule Class
        </button>
      </div>

      {banner && (
        <div className="mb-4 px-4 py-3 bg-green-500/10 border border-green-500/20 rounded-xl text-green-400 text-sm flex items-center justify-between">
          {banner}
          <button onClick={() => setBanner("")} className="text-green-400/70 hover:text-green-400"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div className="flex gap-2 p-1 bg-zinc-950 border border-white/5 rounded-xl w-max">
          {(["today", "upcoming", "history", "cancelled"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all capitalize ${
                tab === t ? "bg-[#facc15] text-black shadow-sm" : "text-zinc-400 hover:text-white"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
        <div className="flex gap-2 p-1 bg-zinc-950 border border-white/5 rounded-xl w-max">
          <button
            onClick={() => setView("list")}
            className={`px-3 py-2 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-all ${view === "list" ? "bg-white/10 text-white" : "text-zinc-500 hover:text-white"}`}
          >
            <ListIcon className="w-3.5 h-3.5" /> List
          </button>
          <button
            onClick={() => setView("calendar")}
            className={`px-3 py-2 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-all ${view === "calendar" ? "bg-white/10 text-white" : "text-zinc-500 hover:text-white"}`}
          >
            <CalendarIcon className="w-3.5 h-3.5" /> Calendar
          </button>
        </div>
      </div>

      {view === "calendar" ? (
        <MonthCalendar classes={calendarClasses} />
      ) : (
        <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden">
          {loading ? (
            <div className="text-center py-20 text-zinc-500 text-sm">Loading...</div>
          ) : error ? (
            <div className="text-center py-20 text-red-400 text-sm">{error}</div>
          ) : classes.length === 0 ? (
            <div className="text-center py-20 text-zinc-500 text-sm">No {tab} live classes.</div>
          ) : (
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                  <th className="p-4 font-semibold">Title</th>
                  <th className="p-4 font-semibold">Scheduled</th>
                  <th className="p-4 font-semibold">Duration</th>
                  <th className="p-4 font-semibold">Provider</th>
                  <th className="p-4 font-semibold text-center">Status</th>
                  <th className="p-4 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-zinc-300">
                {classes.map((lc: any) => (
                  <tr key={lc.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4 text-white font-bold">
                      {lc.title}
                      {lc.recurrence_rule && (
                        <span title="Part of a recurring series" className="ml-2 inline-flex items-center text-[#facc15]"><Repeat className="w-3 h-3" /></span>
                      )}
                    </td>
                    <td className="p-4 text-zinc-400">{new Date(lc.scheduled_start).toLocaleString()}</td>
                    <td className="p-4">{lc.duration_minutes} min</td>
                    <td className="p-4">{PROVIDER_LABEL[lc.meeting_provider] || lc.meeting_provider}</td>
                    <td className="p-4 text-center">
                      <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${STATUS_STYLE[lc.status] || ""}`}>{lc.status}</span>
                    </td>
                    <td className="p-4">
                      <div className="flex items-center justify-end gap-1.5 flex-wrap">
                        {lc.status === "SCHEDULED" && (
                          <>
                            <button disabled={busyId === lc.id} onClick={() => startClass(lc)} className="px-2.5 py-1 rounded-lg bg-green-500/10 text-green-400 border border-green-500/20 hover:bg-green-500/20 text-[10px] font-bold disabled:opacity-50">Start</button>
                            <button onClick={() => { setRescheduleTarget(lc); setRescheduleDate(""); setRescheduleTime(""); }} className="px-2.5 py-1 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-[10px] font-bold">Reschedule</button>
                            <button onClick={() => { setCancelTarget(lc); setCancelReason(""); setCancelSeries(false); }} className="px-2.5 py-1 rounded-lg bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 text-[10px] font-bold">Cancel</button>
                          </>
                        )}
                        {lc.status === "LIVE" && (
                          <button disabled={busyId === lc.id} onClick={() => endClass(lc)} className="px-2.5 py-1 rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/20 hover:bg-blue-500/20 text-[10px] font-bold disabled:opacity-50">End</button>
                        )}
                        <button onClick={() => openAttendance(lc)} className="px-2.5 py-1 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-[10px] font-bold flex items-center gap-1"><UsersIcon className="w-3 h-3" /> Attendance</button>
                        {lc.status === "COMPLETED" && (
                          <button onClick={() => { setRecordingTarget(lc); setRecordingUrl(lc.recording_url || ""); }} className="px-2.5 py-1 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-[10px] font-bold">
                            {lc.recording_url ? "Edit Recording" : "Add Recording"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* ---------------- Schedule Modal ---------------- */}
      <AnimatePresence>
        {showSchedule && (
          <Modal title="Schedule a Live Class" onClose={() => setShowSchedule(false)} wide>
            <form onSubmit={handleSchedule} className="space-y-4">
              {scheduleError && (
                <div className="px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-xs">
                  {typeof scheduleError === "string" ? scheduleError : JSON.stringify(scheduleError)}
                </div>
              )}

              <div>
                <label className={labelCls}>Course</label>
                <select required value={form.courseId} onChange={(e) => setForm((f) => ({ ...f, courseId: e.target.value, batchChoice: "new" }))} className={inputCls}>
                  <option value="" disabled>Select a live course</option>
                  {courses.map((c: any) => <option key={c.id} value={c.id}>{c.title}</option>)}
                </select>
              </div>

              {form.courseId && (
                <div>
                  <label className={labelCls}>Batch / Session Group</label>
                  <select value={form.batchChoice} onChange={(e) => setForm((f) => ({ ...f, batchChoice: e.target.value }))} className={inputCls}>
                    <option value="new">+ Create a new batch</option>
                    {batchesForCourse.map((b: any) => (
                      <option key={b.id} value={b.id}>{b.batch_type} -- {b.instructor_username} ({b.student_count} students)</option>
                    ))}
                  </select>
                </div>
              )}

              {form.courseId && form.batchChoice === "new" && (
                <div className="bg-black border border-white/10 rounded-xl p-4 space-y-3">
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className={labelCls}>Batch Type</label>
                      <select value={form.batchType} onChange={(e) => setForm((f) => ({ ...f, batchType: e.target.value }))} className={inputCls}>
                        <option value="GROUP">Group</option>
                        <option value="ONE_TO_ONE">One-to-One</option>
                      </select>
                    </div>
                    {form.batchType === "GROUP" && (
                      <div>
                        <label className={labelCls}>Max Participants</label>
                        <input type="number" min={1} value={form.maxParticipants} onChange={(e) => setForm((f) => ({ ...f, maxParticipants: e.target.value }))} placeholder="Unlimited" className={inputCls} />
                      </div>
                    )}
                  </div>

                  {isFullAdmin && (
                    <div>
                      <label className={labelCls}>Instructor</label>
                      <select required value={form.instructorId} onChange={(e) => setForm((f) => ({ ...f, instructorId: e.target.value }))} className={inputCls}>
                        <option value="" disabled>Select a teacher or mentor</option>
                        {eligibleInstructors.map((u: any) => (
                          <option key={u.id} value={u.id}>{(u.first_name || u.username)} {u.is_mentor ? "(Mentor)" : "(Teacher)"}</option>
                        ))}
                      </select>
                    </div>
                  )}

                  <div>
                    <label className={labelCls}>Students</label>
                    <div className="max-h-32 overflow-y-auto space-y-1 bg-zinc-950 border border-white/10 rounded-xl p-2">
                      {eligibleStudents.length === 0 ? (
                        <p className="text-[10px] text-zinc-600 p-2">No students available to assign yet.</p>
                      ) : eligibleStudents.map((s: any) => (
                        <label key={s.id} className="flex items-center gap-2 text-xs text-zinc-300 px-2 py-1 rounded hover:bg-white/5 cursor-pointer">
                          <input type="checkbox" checked={form.studentIds.includes(s.id)} onChange={() => toggleStudent(s.id)} />
                          {(s.first_name || s.username)} <span className="text-zinc-600">({s.email || s.username})</span>
                        </label>
                      ))}
                    </div>
                  </div>
                </div>
              )}

              <div>
                <label className={labelCls}>Session Title</label>
                <input required value={form.title} onChange={(e) => setForm((f) => ({ ...f, title: e.target.value }))} placeholder="e.g. Week 3 - Live Practice" className={inputCls} />
              </div>
              <div>
                <label className={labelCls}>Description (optional)</label>
                <textarea value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} className={inputCls} rows={2} />
              </div>

              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className={labelCls}>Date</label>
                  <input required type="date" value={form.date} onChange={(e) => setForm((f) => ({ ...f, date: e.target.value }))} className={inputCls} />
                </div>
                <div>
                  <label className={labelCls}>Time</label>
                  <input required type="time" value={form.time} onChange={(e) => setForm((f) => ({ ...f, time: e.target.value }))} className={inputCls} />
                </div>
                <div>
                  <label className={labelCls}>Duration (min)</label>
                  <input required type="number" min={1} value={form.durationMinutes} onChange={(e) => setForm((f) => ({ ...f, durationMinutes: Number(e.target.value) }))} className={inputCls} />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls}>Provider</label>
                  <select value={form.provider} onChange={(e) => setForm((f) => ({ ...f, provider: e.target.value }))} className={inputCls}>
                    {Object.entries(PROVIDER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
                <div>
                  <label className={labelCls}>Meeting URL</label>
                  <input required type="url" value={form.meetingUrl} onChange={(e) => setForm((f) => ({ ...f, meetingUrl: e.target.value }))} placeholder="https://..." className={inputCls} />
                </div>
              </div>

              <div className="bg-black border border-white/10 rounded-xl p-4 space-y-3">
                <label className="flex items-center gap-2 text-sm font-semibold text-white cursor-pointer">
                  <input type="checkbox" checked={form.recurrenceEnabled} onChange={(e) => setForm((f) => ({ ...f, recurrenceEnabled: e.target.checked, weekdays: form.date ? [jsDayToBackend(new Date(form.date).getDay())] : f.weekdays }))} />
                  <Repeat className="w-3.5 h-3.5 text-[#facc15]" /> Repeat this class
                </label>
                {form.recurrenceEnabled && (
                  <div className="space-y-3">
                    <div>
                      <label className={labelCls}>Frequency</label>
                      <select value={form.frequency} onChange={(e) => setForm((f) => ({ ...f, frequency: e.target.value }))} className={inputCls}>
                        <option value="DAILY">Daily</option>
                        <option value="WEEKLY">Weekly (select weekdays)</option>
                      </select>
                    </div>
                    {form.frequency === "WEEKLY" && (
                      <div className="flex gap-1.5 flex-wrap">
                        {WEEKDAY_LABELS.map((label, idx) => (
                          <button type="button" key={label} onClick={() => toggleWeekday(idx)} className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ${form.weekdays.includes(idx) ? "bg-[#facc15] text-black" : "bg-zinc-950 border border-white/10 text-zinc-400"}`}>
                            {label}
                          </button>
                        ))}
                      </div>
                    )}
                    <div className="grid grid-cols-2 gap-3">
                      <div>
                        <label className={labelCls}>End Date (optional)</label>
                        <input type="date" value={form.endDate} onChange={(e) => setForm((f) => ({ ...f, endDate: e.target.value }))} className={inputCls} />
                      </div>
                      <div>
                        <label className={labelCls}>Occurrence Count (optional)</label>
                        <input type="number" min={1} max={52} value={form.occurrenceCount} onChange={(e) => setForm((f) => ({ ...f, occurrenceCount: e.target.value }))} placeholder="e.g. 8" className={inputCls} />
                      </div>
                    </div>
                    <p className="text-[10px] text-zinc-600">Provide an end date, an occurrence count, or both (capped at 52 sessions).</p>
                  </div>
                )}
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={() => setShowSchedule(false)} className={btnGhost}>Cancel</button>
                <button type="submit" disabled={scheduling} className={btnPrimary}>{scheduling ? "Scheduling..." : "Schedule"}</button>
              </div>
            </form>
          </Modal>
        )}
      </AnimatePresence>

      {/* ---------------- Cancel Modal ---------------- */}
      <AnimatePresence>
        {cancelTarget && (
          <Modal title={`Cancel "${cancelTarget.title}"`} onClose={() => setCancelTarget(null)}>
            <div className="space-y-4">
              <div>
                <label className={labelCls}>Reason</label>
                <textarea value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} className={inputCls} rows={3} placeholder="Let attendees know why..." />
              </div>
              {cancelTarget.recurrence_rule && (
                <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer">
                  <input type="checkbox" checked={cancelSeries} onChange={(e) => setCancelSeries(e.target.checked)} />
                  Cancel the entire remaining series, not just this session
                </label>
              )}
              <div className="flex justify-end gap-3">
                <button onClick={() => setCancelTarget(null)} className={btnGhost}>Back</button>
                <button onClick={submitCancel} disabled={busyId === cancelTarget.id} className="px-5 py-2 bg-red-500 text-white font-bold rounded-xl hover:bg-red-600 transition-colors disabled:opacity-50">
                  {cancelSeries ? "Cancel Series" : "Cancel Class"}
                </button>
              </div>
            </div>
          </Modal>
        )}
      </AnimatePresence>

      {/* ---------------- Reschedule Modal ---------------- */}
      <AnimatePresence>
        {rescheduleTarget && (
          <Modal title={`Reschedule "${rescheduleTarget.title}"`} onClose={() => setRescheduleTarget(null)}>
            <div className="space-y-4">
              {rescheduleError && (
                <div className="px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-xs">
                  {typeof rescheduleError === "string" ? rescheduleError : JSON.stringify(rescheduleError)}
                </div>
              )}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls}>New Date</label>
                  <input type="date" value={rescheduleDate} onChange={(e) => setRescheduleDate(e.target.value)} className={inputCls} />
                </div>
                <div>
                  <label className={labelCls}>New Time</label>
                  <input type="time" value={rescheduleTime} onChange={(e) => setRescheduleTime(e.target.value)} className={inputCls} />
                </div>
              </div>
              <div className="flex justify-end gap-3">
                <button onClick={() => setRescheduleTarget(null)} className={btnGhost}>Back</button>
                <button onClick={submitReschedule} disabled={!rescheduleDate || !rescheduleTime || busyId === rescheduleTarget.id} className={btnPrimary}>Confirm</button>
              </div>
            </div>
          </Modal>
        )}
      </AnimatePresence>

      {/* ---------------- Attendance Modal ---------------- */}
      <AnimatePresence>
        {attendanceTarget && (
          <Modal title={`Attendance -- ${attendanceTarget.title}`} onClose={() => setAttendanceTarget(null)} wide>
            <div className="space-y-3">
              {attendanceRoster.length === 0 ? (
                <p className="text-zinc-500 text-sm">No students assigned to this batch.</p>
              ) : attendanceRoster.map((s: any) => (
                <div key={s.id} className="flex items-center justify-between bg-black border border-white/10 rounded-xl p-3">
                  <span className="text-sm text-white">{s.student_username}</span>
                  <div className="flex gap-1.5">
                    {["PRESENT", "LATE", "ABSENT", "EXCUSED"].map((st) => (
                      <button
                        key={st}
                        onClick={() => setAttendanceRecords((r) => ({ ...r, [s.student]: st }))}
                        className={`px-2.5 py-1 rounded-lg text-[10px] font-bold transition-colors ${
                          attendanceRecords[s.student] === st ? "bg-[#facc15] text-black" : "bg-zinc-950 border border-white/10 text-zinc-400"
                        }`}
                      >
                        {st}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
              <div className="flex justify-end gap-3 pt-2">
                <button onClick={() => setAttendanceTarget(null)} className={btnGhost}>Close</button>
                {attendanceRoster.length > 0 && <button onClick={submitAttendance} className={btnPrimary}>Save Attendance</button>}
              </div>
            </div>
          </Modal>
        )}
      </AnimatePresence>

      {/* ---------------- Recording Modal ---------------- */}
      <AnimatePresence>
        {recordingTarget && (
          <Modal title={`Recording -- ${recordingTarget.title}`} onClose={() => setRecordingTarget(null)}>
            <div className="space-y-4">
              <div>
                <label className={labelCls}>Recording URL</label>
                <input type="url" value={recordingUrl} onChange={(e) => setRecordingUrl(e.target.value)} placeholder="https://..." className={inputCls} />
              </div>
              <div className="flex justify-end gap-3">
                <button onClick={() => setRecordingTarget(null)} className={btnGhost}>Cancel</button>
                <button onClick={submitRecording} disabled={!recordingUrl || busyId === recordingTarget.id} className={btnPrimary}>Save</button>
              </div>
            </div>
          </Modal>
        )}
      </AnimatePresence>
    </div>
  );
}
