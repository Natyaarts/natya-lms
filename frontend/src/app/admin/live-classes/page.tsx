"use client";

import { useEffect, useMemo, useState } from "react";
import { Video, Plus, X, Repeat, Users as UsersIcon, Calendar as CalendarIcon, List as ListIcon, ExternalLink, Trash2, Play, Film, Upload, RefreshCw, Copy, CheckCircle2, Clock } from "lucide-react";
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
  const [tab, setTab] = useState<"today" | "upcoming" | "history" | "cancelled" | "batches">("upcoming");
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

  const fetchBatches = async () => {
    try {
      const res = await authedFetch("/api/courses/live-batches/?page_size=200");
      if (res.ok) {
        const data = await res.json();
        setBatches(Array.isArray(data) ? data : data.results || []);
      }
    } catch (err) {
      console.error(err);
    }
  };

  const fetchClasses = async () => {
    setLoading(true);
    setError("");
    if (tab === "batches") {
      await fetchBatches();
      setLoading(false);
      return;
    }
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

  useEffect(() => {
    fetchClasses();
    const timer = setInterval(() => {
      fetchClasses();
    }, 30000);
    return () => clearInterval(timer);
  }, [tab]);
  useEffect(() => { fetchBatches(); }, []);

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
  const [generatingZoom, setGeneratingZoom] = useState(false);
  const [zoomSuccess, setZoomSuccess] = useState("");

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
    hostUrl: "",
    recurrenceEnabled: false,
    frequency: "WEEKLY",
    weekdays: [] as number[],
    endDate: "",
    occurrenceCount: "",
  };
  const [form, setForm] = useState(emptyForm);

  // ---- Batch modal state ----
  const [showBatchModal, setShowBatchModal] = useState(false);
  const [batchForm, setBatchForm] = useState<{
    courseId: string;
    batchType: "GROUP" | "ONE_TO_ONE";
    maxParticipants: string;
    instructorId: string;
    studentIds: number[];
  }>({
    courseId: "",
    batchType: "GROUP",
    maxParticipants: "20",
    instructorId: "",
    studentIds: [],
  });
  const [batchCreating, setBatchCreating] = useState(false);
  const [batchError, setBatchError] = useState<any>("");

  const loadDependencies = async () => {
    try {
      const cRes = await authedFetch("/api/courses/");
      if (cRes.ok) {
        const all = await cRes.json();
        setCourses(Array.isArray(all) ? all : all.results || []);
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

  const openCreateBatch = async () => {
    setBatchError("");
    await loadDependencies();
    setBatchForm({
      courseId: "",
      batchType: "GROUP",
      maxParticipants: "20",
      instructorId: currentUser?.id ? String(currentUser.id) : "",
      studentIds: [],
    });
    setShowBatchModal(true);
  };

  const handleCreateBatch = async (e: React.FormEvent) => {
    e.preventDefault();
    if (batchCreating) return;
    setBatchCreating(true);
    setBatchError("");
    try {
      const batchPayload: any = {
        course: batchForm.courseId,
        batch_type: batchForm.batchType,
      };
      if (batchForm.batchType === "GROUP" && batchForm.maxParticipants) {
        batchPayload.max_participants = Number(batchForm.maxParticipants);
      }
      if (isFullAdmin && batchForm.instructorId) {
        batchPayload.instructor = batchForm.instructorId;
      }
      const bRes = await authedFetch("/api/courses/live-batches/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(batchPayload),
      });
      const bData = await bRes.json();
      if (!bRes.ok) {
        setBatchError(typeof bData === "string" ? bData : JSON.stringify(bData));
        setBatchCreating(false);
        return;
      }

      for (const sId of batchForm.studentIds) {
        await authedFetch(`/api/courses/live-batches/${bData.id}/students/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ student_id: sId }),
        });
      }

      setBanner("Live class batch created successfully!");
      setShowBatchModal(false);
      const bFetch = await authedFetch("/api/courses/live-batches/?page_size=200");
      if (bFetch.ok) {
        const bList = await bFetch.json();
        setBatches(Array.isArray(bList) ? bList : bList.results || []);
      }
    } catch (err) {
      setBatchError("Failed to create batch. Please check fields.");
    } finally {
      setBatchCreating(false);
    }
  };

  const handleAutoGenerateZoom = async () => {
    setGeneratingZoom(true);
    setZoomSuccess("");
    try {
      let startTimeIso: string | undefined = undefined;
      if (form.date && form.time) {
        startTimeIso = new Date(`${form.date}T${form.time}`).toISOString();
      }

      const res = await authedFetch("/api/courses/admin/zoom/create-meeting/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          topic: form.title || "Natya LMS Live Session",
          start_time: startTimeIso,
          duration: form.durationMinutes || 60,
        }),
      });

      if (res.ok) {
        const data = await res.json();
        if (data.join_url) {
          setForm((f) => ({
            ...f,
            meetingUrl: data.join_url,
            hostUrl: data.start_url || "",
          }));
          setZoomSuccess(`✓ Meeting Created! ID: ${data.meeting_id} (Password: ${data.password || "Auto-set"})`);
          setTimeout(() => setZoomSuccess(""), 6000);
        }
      } else {
        const err = await res.json().catch(() => ({}));
        alert(err.error || "Failed to auto-generate Zoom meeting.");
      }
    } catch (e: any) {
      alert("Error connecting to Zoom API service.");
    } finally {
      setGeneratingZoom(false);
    }
  };

  const getNextAvailableSlot = () => {
    const d = new Date();
    d.setMinutes(d.getMinutes() + 15);
    const dateStr = d.toISOString().split("T")[0];
    const timeStr = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
    return { date: dateStr, time: timeStr };
  };

  const isTimeInPast = useMemo(() => {
    if (!form.date || !form.time) return false;
    const selected = new Date(`${form.date}T${form.time}`).getTime();
    return selected < Date.now();
  }, [form.date, form.time]);

  const openSchedule = async () => {
    const slot = getNextAvailableSlot();
    setForm({ ...emptyForm, date: slot.date, time: slot.time });
    setScheduleError("");
    setZoomSuccess("");
    setShowSchedule(true);
    await loadDependencies();
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
      let meetingUrl = form.meetingUrl;
      let hostUrl = form.hostUrl;
      if (form.provider === "ZOOM" && !meetingUrl) {
        try {
          const zRes = await authedFetch("/api/courses/admin/zoom/create-meeting/", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              topic: form.title || "Natya LMS Live Session",
              start_time: scheduledStart,
              duration: form.durationMinutes || 60,
            }),
          });
          if (zRes.ok) {
            const zData = await zRes.json();
            if (zData.join_url) {
              meetingUrl = zData.join_url;
              hostUrl = zData.start_url || "";
            }
          }
        } catch (zErr) {
          console.error("Zoom auto-generation error:", zErr);
        }
      }

      const payload: any = {
        title: form.title,
        description: form.description,
        batch: batchId,
        scheduled_start: scheduledStart,
        duration_minutes: form.durationMinutes,
        meeting_provider: form.provider,
        meeting_url: meetingUrl,
        host_url: hostUrl || form.hostUrl || "",
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
    const res = await authedFetch(`/api/courses/live-classes/${lc.id}/start/`, { method: "POST" });
    const updated = res.ok ? await res.json().catch(() => null) : null;
    setBusyId(null);
    fetchClasses();
    const hostLink = updated?.host_url || lc.host_url || updated?.meeting_url || lc.meeting_url;
    if (hostLink) {
      window.open(hostLink, "_blank");
    }
  };

  const regenerateZoom = async (lc: any) => {
    setBusyId(lc.id);
    const res = await authedFetch(`/api/courses/live-classes/${lc.id}/generate-zoom/`, { method: "POST" });
    setBusyId(null);
    if (res.ok) {
      const data = await res.json();
      setBanner("Zoom host link generated successfully!");
      fetchClasses();
      const hostLink = data.host_url || data.meeting_url;
      if (hostLink) {
        window.open(hostLink, "_blank");
      }
    } else {
      const err = await res.json().catch(() => ({}));
      alert(err.error || "Failed to generate Zoom meeting.");
    }
  };

  const openScheduleForBatch = (b: any) => {
    const slot = getNextAvailableSlot();
    setForm({
      ...emptyForm,
      courseId: String(b.course),
      batchChoice: String(b.id),
      instructorId: b.instructor ? String(b.instructor) : "",
      date: slot.date,
      time: slot.time,
    });
    setScheduleError("");
    setZoomSuccess("");
    setShowSchedule(true);
    loadDependencies();
  };

  const handleDeleteBatch = async (batchId: number) => {
    if (!confirm("Are you sure you want to delete this batch? All assigned student batch records will be unlinked.")) return;
    try {
      const res = await authedFetch(`/api/courses/live-batches/${batchId}/`, { method: "DELETE" });
      if (res.ok) {
        setBanner("Batch deleted successfully.");
        await fetchBatches();
      } else {
        alert("Failed to delete batch.");
      }
    } catch (err) {
      alert("Error deleting batch.");
    }
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
  const [attendanceDurations, setAttendanceDurations] = useState<Record<number, number>>({});
  const openAttendance = async (lc: any) => {
    setAttendanceTarget(lc);
    setAttendanceRecords({});
    setAttendanceDurations({});
    let roster: any[] = [];
    if (lc.batch) {
      const res = await authedFetch(`/api/courses/live-batches/${lc.batch}/students/?page_size=200`);
      if (res.ok) {
        const data = await res.json();
        roster = Array.isArray(data) ? data : data.results || [];
      }
    }
    const aRes = await authedFetch(`/api/courses/live-classes/${lc.id}/attendance/`);
    if (aRes.ok) {
      const existing = await aRes.json();
      const map: Record<number, string> = {};
      const durMap: Record<number, number> = {};
      const rosterIds = new Set(roster.map((r: any) => r.student));
      for (const rec of existing) {
        map[rec.student] = rec.status;
        durMap[rec.student] = rec.duration_minutes ?? 0;
        if (!rosterIds.has(rec.student)) {
          roster.push({
            id: rec.id,
            student: rec.student,
            student_username: rec.student_name || `Student #${rec.student}`,
          });
        }
      }
      for (const s of roster) {
        if (!map[s.student]) {
          map[s.student] = "ABSENT";
          durMap[s.student] = 0;
        }
      }
      setAttendanceRecords(map);
      setAttendanceDurations(durMap);
    }
    setAttendanceRoster(roster);
  };
  const submitAttendance = async () => {
    if (!attendanceTarget) return;
    const records = attendanceRoster.map((s) => ({
      student: s.student,
      status: attendanceRecords[s.student] || "ABSENT",
      duration_minutes: Number(attendanceDurations[s.student] || 0),
    }));
    const res = await authedFetch(`/api/courses/live-classes/${attendanceTarget.id}/attendance/`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(records),
    });
    if (res.ok) {
      setBanner("✓ Attendance updated successfully!");
      fetchClasses();
    }
    setAttendanceTarget(null);
  };

  const [recordingTarget, setRecordingTarget] = useState<any>(null);
  const [recordingUrl, setRecordingUrl] = useState("");
  const [watchTarget, setWatchTarget] = useState<any>(null);
  const [uploadingFile, setUploadingFile] = useState(false);

  const handleSyncZoomRecording = async (lc: any) => {
    setBusyId(lc.id);
    try {
      const res = await authedFetch(`/api/courses/live-classes/${lc.id}/sync-recording/`, {
        method: "POST",
      });
      const data = await res.json();
      if (res.ok) {
        setBanner("✓ Zoom recording downloaded and saved to AWS S3 successfully!");
        fetchClasses();
      } else {
        alert(data.error || "Zoom recording not ready yet. Please try again in a few minutes.");
      }
    } catch (e) {
      alert("Error connecting to Zoom recording sync service.");
    } finally {
      setBusyId(null);
    }
  };

  const handleFileUpload = async (file: File) => {
    if (!recordingTarget) return;
    setUploadingFile(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await authedFetch(`/api/courses/live-classes/${recordingTarget.id}/upload-recording/`, {
        method: "POST",
        body: formData,
      });
      if (res.ok) {
        setBanner("✓ Recording uploaded to AWS S3 successfully!");
        setRecordingTarget(null);
        fetchClasses();
      } else {
        const err = await res.json().catch(() => ({}));
        alert(err.error || "Failed to upload recording file.");
      }
    } catch (e) {
      alert("Error uploading file to S3.");
    } finally {
      setUploadingFile(false);
    }
  };

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
        <div className="flex items-center gap-3">
          <button onClick={openCreateBatch} disabled={!currentUser} className="px-4 py-2 bg-white/10 hover:bg-white/15 text-white font-semibold rounded-xl transition-colors flex items-center gap-2 text-sm border border-white/10 disabled:opacity-50">
            <UsersIcon className="w-4 h-4 text-[#facc15]" /> Create Batch
          </button>
          <button onClick={openSchedule} disabled={!currentUser} className={`${btnPrimary} flex items-center gap-2`}>
            <Plus className="w-4 h-4" /> Schedule Class
          </button>
        </div>
      </div>

      {banner && (
        <div className="mb-4 px-4 py-3 bg-green-500/10 border border-green-500/20 rounded-xl text-green-400 text-sm flex items-center justify-between">
          {banner}
          <button onClick={() => setBanner("")} className="text-green-400/70 hover:text-green-400"><X className="w-3.5 h-3.5" /></button>
        </div>
      )}

      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div className="flex gap-2 p-1 bg-zinc-950 border border-white/5 rounded-xl w-max">
          {(["today", "upcoming", "history", "cancelled", "batches"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-5 py-2.5 rounded-lg text-sm font-semibold transition-all capitalize ${
                tab === t ? "bg-[#facc15] text-black shadow-sm" : "text-zinc-400 hover:text-white"
              }`}
            >
              {t === "batches" ? `Batches (${batches.length})` : t}
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
          {tab === "batches" ? (
            loading ? (
              <div className="text-center py-20 text-zinc-500 text-sm">Loading batches...</div>
            ) : batches.length === 0 ? (
              <div className="text-center py-20 text-zinc-500 text-sm space-y-2">
                <p>No batches created yet.</p>
                <button onClick={openCreateBatch} className="px-4 py-2 bg-[#facc15] text-black font-bold text-xs rounded-xl hover:bg-yellow-500">
                  + Create Your First Batch
                </button>
              </div>
            ) : (
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                    <th className="p-4 font-semibold">Course</th>
                    <th className="p-4 font-semibold">Batch Type</th>
                    <th className="p-4 font-semibold">Instructor</th>
                    <th className="p-4 font-semibold">Students Enrolled</th>
                    <th className="p-4 font-semibold">Created Date</th>
                    <th className="p-4 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-zinc-300">
                  {batches.map((b: any) => (
                    <tr key={b.id} className="hover:bg-white/5 transition-colors">
                      <td className="p-4 text-white font-bold">{b.course_title || `Course #${b.course}`}</td>
                      <td className="p-4">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${b.batch_type === 'ONE_TO_ONE' ? 'bg-purple-500/20 text-purple-300' : 'bg-blue-500/20 text-blue-300'}`}>
                          {b.batch_type === 'ONE_TO_ONE' ? '1-on-1' : 'Group'}
                        </span>
                      </td>
                      <td className="p-4 text-zinc-300">{b.instructor_username || "Unassigned"}</td>
                      <td className="p-4">
                        <span className="font-semibold text-white">{b.student_count || 0}</span>
                        {b.max_participants ? <span className="text-zinc-500"> / {b.max_participants} max</span> : <span className="text-zinc-500"> enrolled</span>}
                      </td>
                      <td className="p-4 text-zinc-400">{new Date(b.created_at).toLocaleDateString()}</td>
                      <td className="p-4">
                        <div className="flex items-center justify-end gap-2">
                          <button
                            onClick={() => openScheduleForBatch(b)}
                            className="px-2.5 py-1.5 rounded-lg bg-[#facc15] text-black text-[10px] font-bold hover:bg-yellow-400 transition-colors flex items-center gap-1 shadow-sm"
                          >
                            <Plus className="w-3 h-3" /> Schedule Class
                          </button>
                          <button
                            onClick={() => handleDeleteBatch(b.id)}
                            className="px-2 py-1.5 rounded-lg bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 text-[10px] font-bold transition-colors"
                            title="Delete Batch"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          ) : loading ? (
            <div className="text-center py-20 text-zinc-500 text-sm">Loading...</div>
          ) : error ? (
            <div className="text-center py-20 text-red-400 text-sm">{error}</div>
          ) : classes.length === 0 ? (
            <div className="text-center py-20 text-zinc-500 text-sm">No {tab} live classes.</div>
          ) : (
            <table className="w-full text-left border-collapse text-xs">
              <thead>
                <tr className="bg-white/5 border-b border-white/5 text-zinc-400 uppercase tracking-wider">
                  <th className="p-4 font-semibold">Title & Course</th>
                  <th className="p-4 font-semibold">Scheduled</th>
                  <th className="p-4 font-semibold">Duration</th>
                  <th className="p-4 font-semibold">Provider</th>
                  <th className="p-4 font-semibold text-center">Status / Attendance</th>
                  <th className="p-4 font-semibold text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5 text-zinc-300">
                {classes.map((lc: any) => (
                  <tr key={lc.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4 text-white font-bold">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span>{lc.title}</span>
                        {lc.recurrence_rule && (
                          <span title="Part of a recurring series" className="inline-flex items-center text-[#facc15]"><Repeat className="w-3 h-3" /></span>
                        )}
                      </div>
                      {(lc.course_title || lc.batch_name) && (
                        <p className="text-[11px] font-normal text-zinc-400 mt-0.5">
                          {lc.course_title}{lc.course_title && lc.batch_name ? " · " : ""}{lc.batch_name}
                        </p>
                      )}
                      {lc.recording_url ? (
                        <div className="mt-1.5">
                          <button
                            type="button"
                            onClick={() => setWatchTarget(lc)}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 text-[10px] font-bold cursor-pointer hover:bg-emerald-500/25 transition-colors shadow-sm"
                            title="Saved on AWS S3 - Click to watch"
                          >
                            <Play className="w-2.5 h-2.5 fill-current" /> Watch Recording
                          </button>
                        </div>
                      ) : lc.status === "COMPLETED" ? (
                        <div className="mt-1.5">
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-800 text-zinc-400 text-[9px]">
                            <Film className="w-2.5 h-2.5" /> Recording pending
                          </span>
                        </div>
                      ) : null}
                    </td>
                    <td className="p-4 text-zinc-400">{new Date(lc.scheduled_start).toLocaleString()}</td>
                    <td className="p-4">{lc.duration_minutes} min</td>
                    <td className="p-4">{PROVIDER_LABEL[lc.meeting_provider] || lc.meeting_provider}</td>
                    <td className="p-4 text-center">
                      <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${STATUS_STYLE[lc.status] || ""}`}>{lc.status}</span>
                      {(lc.status === "COMPLETED" || (lc.attendance_total_count && lc.attendance_total_count > 0)) && (
                        <div className="mt-1.5 flex flex-col items-center">
                          <button
                            type="button"
                            onClick={() => openAttendance(lc)}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-green-500/10 text-green-400 border border-green-500/20 hover:bg-green-500/20 transition-colors"
                            title="Click to view/edit attendance"
                          >
                            <UsersIcon className="w-2.5 h-2.5" />
                            {lc.attendance_present_count ?? 0}/{lc.attendance_total_count || 1} Present
                          </button>
                          {lc.attendance_avg_duration > 0 && (
                            <span className="text-[10px] text-zinc-400 mt-0.5 flex items-center gap-1">
                              <Clock className="w-2.5 h-2.5 text-yellow-400/80" />
                              Avg {lc.attendance_avg_duration} min
                            </span>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="p-4">
                      <div className="flex items-center justify-end gap-1.5 flex-wrap">
                        {lc.status === "COMPLETED" ? (
                          <>
                            {lc.recording_url ? (
                              <>
                                <button
                                  type="button"
                                  onClick={() => setWatchTarget(lc)}
                                  className="px-3 py-1.5 rounded-lg bg-emerald-500 text-black font-bold text-[10px] flex items-center gap-1.5 hover:bg-emerald-400 transition-colors shadow-sm"
                                  title="Watch session recording"
                                >
                                  <Play className="w-3 h-3 fill-current" /> Watch Recording
                                </button>
                                <button
                                  type="button"
                                  onClick={() => {
                                    navigator.clipboard.writeText(lc.recording_url);
                                    setBanner("✓ Recording URL copied to clipboard!");
                                  }}
                                  className="px-2 py-1.5 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-zinc-300 text-[10px] font-medium transition-colors"
                                  title="Copy video link"
                                >
                                  Copy Link
                                </button>
                                <button
                                  type="button"
                                  onClick={() => { setRecordingTarget(lc); setRecordingUrl(lc.recording_url || ""); }}
                                  className="px-2 py-1.5 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-[10px] font-medium text-zinc-400 hover:text-white transition-colors"
                                  title="Edit recording link"
                                >
                                  Edit Link
                                </button>
                              </>
                            ) : (
                              <>
                                {lc.meeting_provider === "ZOOM" && (
                                  <button
                                    type="button"
                                    disabled={busyId === lc.id}
                                    onClick={() => handleSyncZoomRecording(lc)}
                                    className="px-2.5 py-1.5 rounded-lg bg-blue-500 text-white hover:bg-blue-400 text-[10px] font-bold disabled:opacity-50 flex items-center gap-1 transition-colors shadow-sm"
                                    title="Fetch cloud recording from Zoom and transfer to AWS S3"
                                  >
                                    <RefreshCw className={`w-3 h-3 ${busyId === lc.id ? "animate-spin" : ""}`} /> Sync to S3
                                  </button>
                                )}
                                <button
                                  type="button"
                                  onClick={() => { setRecordingTarget(lc); setRecordingUrl(""); }}
                                  className="px-2.5 py-1.5 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-[10px] font-bold text-zinc-300 flex items-center gap-1 transition-colors"
                                >
                                  <Upload className="w-3 h-3" /> Add Recording
                                </button>
                              </>
                            )}
                            <button
                              type="button"
                              onClick={() => openAttendance(lc)}
                              className="px-2.5 py-1.5 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-[10px] font-bold text-zinc-300 flex items-center gap-1 transition-colors"
                            >
                              <UsersIcon className="w-3 h-3" /> Attendance
                            </button>
                          </>
                        ) : (
                          <>
                            {(lc.host_url || lc.meeting_url) && (
                              <a
                                href={lc.host_url || lc.meeting_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="px-2.5 py-1 rounded-lg bg-[#facc15]/10 text-[#facc15] border border-[#facc15]/20 hover:bg-[#facc15]/20 text-[10px] font-bold flex items-center gap-1"
                                title={lc.host_url ? "Launch meeting directly with host privileges" : "Join meeting"}
                              >
                                <ExternalLink className="w-3 h-3" /> {lc.host_url ? "Start as Host (Zoom)" : `Join ${PROVIDER_LABEL[lc.meeting_provider] || "Zoom"}`}
                              </a>
                            )}
                            {!lc.host_url && lc.meeting_provider === "ZOOM" && (
                              <button
                                disabled={busyId === lc.id}
                                onClick={() => regenerateZoom(lc)}
                                className="px-2.5 py-1 rounded-lg bg-[#facc15]/20 text-[#facc15] border border-[#facc15]/40 hover:bg-[#facc15]/30 text-[10px] font-bold disabled:opacity-50 flex items-center gap-1"
                                title="Generate host link with ZAK token so you enter Zoom as host instead of waiting"
                              >
                                Get Host Link
                              </button>
                            )}
                            {lc.meeting_url && (
                              <button
                                type="button"
                                onClick={() => {
                                  navigator.clipboard.writeText(lc.meeting_url);
                                  alert("Student join link copied to clipboard!");
                                }}
                                className="px-2 py-1 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-zinc-300 text-[10px] font-medium"
                                title="Copy student join link"
                              >
                                Copy Link
                              </button>
                            )}
                            {lc.status === "SCHEDULED" && (
                              <>
                                <button disabled={busyId === lc.id} onClick={() => startClass(lc)} className="px-2.5 py-1 rounded-lg bg-green-500/10 text-green-400 border border-green-500/20 hover:bg-green-500/20 text-[10px] font-bold disabled:opacity-50">Start Class</button>
                                <button onClick={() => { setRescheduleTarget(lc); setRescheduleDate(""); setRescheduleTime(""); }} className="px-2.5 py-1 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-[10px] font-bold">Reschedule</button>
                                <button onClick={() => { setCancelTarget(lc); setCancelReason(""); setCancelSeries(false); }} className="px-2.5 py-1 rounded-lg bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 text-[10px] font-bold">Cancel</button>
                              </>
                            )}
                            {lc.status === "LIVE" && (
                              <button disabled={busyId === lc.id} onClick={() => endClass(lc)} className="px-2.5 py-1 rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/20 hover:bg-blue-500/20 text-[10px] font-bold disabled:opacity-50">End Class</button>
                            )}
                            <button onClick={() => openAttendance(lc)} className="px-2.5 py-1 rounded-lg bg-white/5 border border-white/10 hover:bg-white/10 text-[10px] font-bold flex items-center gap-1"><UsersIcon className="w-3 h-3" /> Attendance</button>
                          </>
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
                  <option value="" disabled>Select a course</option>
                  {courses.map((c: any) => (
                    <option key={c.id} value={c.id}>
                      {c.title}{c.course_type === "LIVE" ? " (Live)" : ""}
                    </option>
                  ))}
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
                  <div className="flex items-center justify-between mb-1.5">
                    <label className={`${labelCls} mb-0`}>Time</label>
                    {form.time && (
                      <span className="text-[10px] text-[#facc15] font-semibold">
                        {parseInt(form.time.split(":")[0], 10) >= 12 ? "PM (Afternoon/Eve)" : "AM (Night/Morning)"}
                      </span>
                    )}
                  </div>
                  <input required type="time" value={form.time} onChange={(e) => setForm((f) => ({ ...f, time: e.target.value }))} className={inputCls} />
                </div>
                <div>
                  <label className={labelCls}>Duration (min)</label>
                  <input required type="number" min={1} value={form.durationMinutes} onChange={(e) => setForm((f) => ({ ...f, durationMinutes: Number(e.target.value) }))} className={inputCls} />
                </div>
              </div>

              {/* Past time alert & auto-switch to PM helper */}
              {isTimeInPast && (
                <div className="p-3 bg-amber-500/10 border border-amber-500/25 rounded-xl text-amber-300 text-xs flex items-center justify-between gap-3">
                  <div>
                    <span className="font-bold">⚠️ Selected time is in the past!</span>
                    <p className="text-[11px] text-amber-400/80 mt-0.5">
                      {form.time} is interpreted as {parseInt(form.time.split(":")[0], 10) < 12 ? `${parseInt(form.time.split(":")[0], 10) || 12}:${form.time.split(":")[1]} AM` : form.time}.
                    </p>
                  </div>
                  {parseInt(form.time.split(":")[0], 10) < 12 && (
                    <button
                      type="button"
                      onClick={() => {
                        const [h, m] = form.time.split(":");
                        const numH = parseInt(h, 10);
                        const pmH = String(numH + 12).padStart(2, "0");
                        setForm((f) => ({ ...f, time: `${pmH}:${m}` }));
                      }}
                      className="px-3 py-1.5 bg-amber-400 hover:bg-amber-300 text-black text-xs font-bold rounded-lg transition-colors shrink-0 shadow-sm"
                    >
                      Switch to {parseInt(form.time.split(":")[0], 10) || 12}:{form.time.split(":")[1]} PM
                    </button>
                  )}
                </div>
              )}

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls}>Provider</label>
                  <select value={form.provider} onChange={(e) => setForm((f) => ({ ...f, provider: e.target.value }))} className={inputCls}>
                    {Object.entries(PROVIDER_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </select>
                </div>
                <div>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className={`${labelCls} mb-0`}>Meeting URL</label>
                    {form.provider === "ZOOM" && (
                      <button
                        type="button"
                        onClick={handleAutoGenerateZoom}
                        disabled={generatingZoom}
                        className="text-[11px] text-[#facc15] hover:text-[#fde047] font-semibold flex items-center gap-1 transition-colors disabled:opacity-50"
                      >
                        {generatingZoom ? "Generating..." : "⚡ Auto-Generate Zoom"}
                      </button>
                    )}
                  </div>
                  <input
                    required={form.provider !== "ZOOM"}
                    type="url"
                    value={form.meetingUrl}
                    onChange={(e) => setForm((f) => ({ ...f, meetingUrl: e.target.value }))}
                    placeholder={form.provider === "ZOOM" ? "Auto-generates if empty, or paste URL" : "https://..."}
                    className={inputCls}
                  />
                  {zoomSuccess && (
                    <p className="text-[11px] text-emerald-400 mt-1 font-medium">{zoomSuccess}</p>
                  )}
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

              {scheduleError && (
                <div className="p-3 bg-red-500/15 border border-red-500/30 rounded-xl text-red-300 text-xs font-semibold">
                  ⚠️ {typeof scheduleError === "string" ? scheduleError : (
                    scheduleError.scheduled_start?.[0] ||
                    scheduleError.batch?.[0] ||
                    scheduleError.batch ||
                    scheduleError.detail ||
                    scheduleError.non_field_errors?.[0] ||
                    JSON.stringify(scheduleError)
                  )}
                </div>
              )}

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
            <div className="space-y-4">
              {attendanceRoster.length > 0 && (
                <div className="flex items-center justify-between pb-3 border-b border-white/5 flex-wrap gap-2">
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-zinc-400">
                      {attendanceRoster.length} Student{attendanceRoster.length !== 1 ? "s" : ""}
                    </span>
                    <span className="text-zinc-600">·</span>
                    <span className="text-xs text-zinc-400 flex items-center gap-1">
                      <Clock className="w-3 h-3 text-yellow-400" />
                      Class Duration: {attendanceTarget.duration_minutes || 60} min
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        const all: Record<number, string> = {};
                        const allDur: Record<number, number> = {};
                        const defDur = attendanceTarget.duration_minutes || 60;
                        for (const s of attendanceRoster) {
                          all[s.student] = "PRESENT";
                          allDur[s.student] = defDur;
                        }
                        setAttendanceRecords(all);
                        setAttendanceDurations(allDur);
                      }}
                      className="px-2.5 py-1 rounded-lg bg-green-500/10 text-green-400 border border-green-500/20 hover:bg-green-500/20 text-[10px] font-bold transition-colors"
                    >
                      Mark All Present
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        const all: Record<number, string> = {};
                        const allDur: Record<number, number> = {};
                        for (const s of attendanceRoster) {
                          all[s.student] = "ABSENT";
                          allDur[s.student] = 0;
                        }
                        setAttendanceRecords(all);
                        setAttendanceDurations(allDur);
                      }}
                      className="px-2.5 py-1 rounded-lg bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 text-[10px] font-bold transition-colors"
                    >
                      Mark All Absent
                    </button>
                  </div>
                </div>
              )}
              {attendanceRoster.length === 0 ? (
                <p className="text-zinc-500 text-sm py-4 text-center">No students assigned to this batch.</p>
              ) : (
                <div className="space-y-2 max-h-[60vh] overflow-y-auto pr-1">
                  {attendanceRoster.map((s: any) => {
                    const currentStatus = attendanceRecords[s.student] || "ABSENT";
                    const isPresent = currentStatus === "PRESENT" || currentStatus === "LATE";
                    return (
                      <div
                        key={s.id || s.student}
                        className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-black border border-white/10 rounded-xl p-3.5 hover:border-white/20 transition-colors"
                      >
                        <div>
                          <span className="text-sm font-medium text-white block">{s.student_username}</span>
                          <span className="text-[11px] text-zinc-500">
                            {isPresent ? (
                              <span className="text-emerald-400 font-medium">Attended session</span>
                            ) : (
                              <span className="text-zinc-500">Not attended (Absent)</span>
                            )}
                          </span>
                        </div>
                        <div className="flex items-center gap-3 flex-wrap">
                          {/* Duration input */}
                          <div className="flex items-center gap-1.5 bg-zinc-900 border border-white/10 rounded-lg px-2.5 py-1" title="Duration student spent in class">
                            <Clock className="w-3.5 h-3.5 text-zinc-400" />
                            <span className="text-[10px] text-zinc-400 uppercase tracking-wider font-semibold">Duration:</span>
                            <input
                              type="number"
                              min="0"
                              max="600"
                              value={attendanceDurations[s.student] ?? 0}
                              onChange={(e) => {
                                const val = Math.max(0, parseInt(e.target.value) || 0);
                                setAttendanceDurations((d) => ({ ...d, [s.student]: val }));
                                if (val > 0 && currentStatus === "ABSENT") {
                                  setAttendanceRecords((r) => ({ ...r, [s.student]: "PRESENT" }));
                                } else if (val === 0 && currentStatus === "PRESENT") {
                                  setAttendanceRecords((r) => ({ ...r, [s.student]: "ABSENT" }));
                                }
                              }}
                              className="w-12 bg-transparent text-xs text-white text-right focus:outline-none font-mono"
                            />
                            <span className="text-[11px] text-zinc-400">min</span>
                          </div>

                          {/* Status Buttons */}
                          <div className="flex gap-1">
                            {["PRESENT", "LATE", "ABSENT", "EXCUSED"].map((st) => (
                              <button
                                key={st}
                                type="button"
                                onClick={() => {
                                  setAttendanceRecords((r) => ({ ...r, [s.student]: st }));
                                  if (st === "PRESENT" || st === "LATE") {
                                    if (!attendanceDurations[s.student] || attendanceDurations[s.student] === 0) {
                                      setAttendanceDurations((d) => ({
                                        ...d,
                                        [s.student]: attendanceTarget.duration_minutes || 60,
                                      }));
                                    }
                                  } else if (st === "ABSENT") {
                                    setAttendanceDurations((d) => ({ ...d, [s.student]: 0 }));
                                  }
                                }}
                                className={`px-2.5 py-1 rounded-lg text-[10px] font-bold transition-colors ${
                                  attendanceRecords[s.student] === st
                                    ? st === "PRESENT"
                                      ? "bg-green-500 text-black shadow-sm"
                                      : st === "LATE"
                                      ? "bg-yellow-400 text-black shadow-sm"
                                      : st === "ABSENT"
                                      ? "bg-red-500 text-white shadow-sm"
                                      : "bg-blue-500 text-white shadow-sm"
                                    : "bg-zinc-950 border border-white/10 text-zinc-400 hover:text-white"
                                }`}
                              >
                                {st}
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="flex justify-end gap-3 pt-3 border-t border-white/5">
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
          <Modal title={`Manage Recording -- ${recordingTarget.title}`} onClose={() => setRecordingTarget(null)} wide>
            <div className="space-y-5">
              {/* Option 1: File Upload to S3 */}
              <div className="p-4 rounded-xl bg-white/5 border border-white/10 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-white flex items-center gap-1.5">
                    <Upload className="w-4 h-4 text-[#facc15]" /> Upload MP4 Video Directly to AWS S3
                  </span>
                  <span className="text-[10px] text-zinc-400 font-mono">Max 2.5 GB</span>
                </div>
                <input
                  type="file"
                  accept="video/mp4,video/*"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleFileUpload(file);
                  }}
                  disabled={uploadingFile}
                  className="block w-full text-xs text-zinc-400 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-[#facc15] file:text-black hover:file:bg-yellow-400 cursor-pointer disabled:opacity-50"
                />
                {uploadingFile && (
                  <p className="text-xs text-[#facc15] animate-pulse flex items-center gap-1.5 font-medium">
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" /> Uploading recording directly to AWS S3 bucket... Please do not close this window.
                  </p>
                )}
              </div>

              {/* Option 2: Sync from Zoom Cloud */}
              {recordingTarget.meeting_provider === "ZOOM" && (
                <div className="p-4 rounded-xl bg-blue-500/5 border border-blue-500/20 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-xs font-bold text-white flex items-center gap-1.5">
                      <RefreshCw className="w-3.5 h-3.5 text-blue-400" /> Fetch from Zoom Cloud
                    </p>
                    <p className="text-[11px] text-zinc-400">Download the cloud recording from Zoom & automatically save it to AWS S3</p>
                  </div>
                  <button
                    disabled={busyId === recordingTarget.id || uploadingFile}
                    onClick={() => handleSyncZoomRecording(recordingTarget)}
                    className="px-3 py-1.5 rounded-lg bg-blue-500/10 text-blue-400 border border-blue-500/20 hover:bg-blue-500/20 text-xs font-bold disabled:opacity-50 flex items-center gap-1.5 shrink-0"
                  >
                    <RefreshCw className={`w-3.5 h-3.5 ${busyId === recordingTarget.id ? "animate-spin" : ""}`} /> Sync to S3
                  </button>
                </div>
              )}

              {/* Option 3: Manual URL */}
              <div className="space-y-2">
                <label className={labelCls}>Or Enter Direct S3 / Video URL</label>
                <input
                  type="url"
                  value={recordingUrl}
                  onChange={(e) => setRecordingUrl(e.target.value)}
                  placeholder="https://your-bucket.s3.ap-south-1.amazonaws.com/..."
                  className={inputCls}
                />
              </div>

              <div className="flex justify-end gap-3 pt-2 border-t border-white/5">
                <button onClick={() => setRecordingTarget(null)} className={btnGhost}>Cancel</button>
                <button
                  onClick={submitRecording}
                  disabled={!recordingUrl || busyId === recordingTarget.id || uploadingFile}
                  className={btnPrimary}
                >
                  Save URL
                </button>
              </div>
            </div>
          </Modal>
        )}
      </AnimatePresence>

      {/* ---------------- Watch Recording Video Player Modal ---------------- */}
      <AnimatePresence>
        {watchTarget && (
          <Modal title={`Session Recording -- ${watchTarget.title}`} onClose={() => setWatchTarget(null)} wide>
            <div className="space-y-4">
              <div className="relative rounded-2xl overflow-hidden bg-black aspect-video border border-white/10 shadow-2xl flex items-center justify-center">
                <video
                  src={watchTarget.recording_url}
                  controls
                  autoPlay
                  className="w-full h-full object-contain"
                >
                  Your browser does not support HTML5 video playback.
                </video>
              </div>
              <div className="flex items-center justify-between p-3.5 rounded-xl bg-white/5 border border-white/10 flex-wrap gap-2 text-xs">
                <div className="flex items-center gap-2">
                  <span className="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
                  <span className="font-bold text-white">AWS S3 Cloud Video</span>
                  <span className="text-zinc-500 font-mono text-[10px] truncate max-w-xs">{watchTarget.recording_url}</span>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => {
                      navigator.clipboard.writeText(watchTarget.recording_url);
                      alert("Recording S3 URL copied to clipboard!");
                    }}
                    className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/15 text-white font-medium text-xs flex items-center gap-1.5 transition-colors"
                  >
                    <Copy className="w-3.5 h-3.5" /> Copy S3 Link
                  </button>
                  <a
                    href={watchTarget.recording_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="px-3 py-1.5 rounded-lg bg-[#facc15] hover:bg-yellow-400 text-black font-bold text-xs flex items-center gap-1.5 transition-colors"
                  >
                    <ExternalLink className="w-3.5 h-3.5" /> Open in New Tab
                  </a>
                </div>
              </div>
            </div>
          </Modal>
        )}
      </AnimatePresence>

      {/* ---------------- Create Batch Modal ---------------- */}
      <AnimatePresence>
        {showBatchModal && (
          <Modal title="Create Live Class Batch" onClose={() => setShowBatchModal(false)} wide>
            <form onSubmit={handleCreateBatch} className="space-y-4">
              {batchError && (
                <div className="px-3 py-2 bg-red-500/10 border border-red-500/20 rounded-xl text-red-400 text-xs">
                  {typeof batchError === "string" ? batchError : JSON.stringify(batchError)}
                </div>
              )}

              <div>
                <label className={labelCls}>Course *</label>
                <select
                  required
                  value={batchForm.courseId}
                  onChange={(e) => setBatchForm(f => ({ ...f, courseId: e.target.value }))}
                  className={inputCls}
                >
                  <option value="" disabled>Select a course</option>
                  {courses.map((c: any) => (
                    <option key={c.id} value={c.id}>
                      {c.title}{c.course_type === "LIVE" ? " (Live)" : ""}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className={labelCls}>Batch Type *</label>
                  <select
                    value={batchForm.batchType}
                    onChange={(e: any) => setBatchForm(f => ({ ...f, batchType: e.target.value }))}
                    className={inputCls}
                  >
                    <option value="GROUP">Group Batch</option>
                    <option value="ONE_TO_ONE">One-to-One Batch</option>
                  </select>
                </div>
                {batchForm.batchType === "GROUP" && (
                  <div>
                    <label className={labelCls}>Max Students</label>
                    <input
                      type="number"
                      min={1}
                      value={batchForm.maxParticipants}
                      onChange={(e) => setBatchForm(f => ({ ...f, maxParticipants: e.target.value }))}
                      placeholder="e.g. 20"
                      className={inputCls}
                    />
                  </div>
                )}
              </div>

              {isFullAdmin && (
                <div>
                  <label className={labelCls}>Assigned Instructor *</label>
                  <select
                    required
                    value={batchForm.instructorId}
                    onChange={(e) => setBatchForm(f => ({ ...f, instructorId: e.target.value }))}
                    className={inputCls}
                  >
                    <option value="" disabled>Select an instructor</option>
                    {eligibleInstructors.map((u: any) => (
                      <option key={u.id} value={u.id}>
                        {(u.first_name ? `${u.first_name} ${u.last_name || ""}` : u.username)} {u.is_mentor ? "(Mentor)" : "(Teacher)"}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div>
                <label className={labelCls}>Assign Students ({batchForm.studentIds.length} selected)</label>
                <div className="max-h-40 overflow-y-auto space-y-1 bg-zinc-950 border border-white/10 rounded-xl p-2.5">
                  {eligibleStudents.length === 0 ? (
                    <p className="text-xs text-zinc-500 p-2">No students available to assign.</p>
                  ) : eligibleStudents.map((s: any) => {
                    const isChecked = batchForm.studentIds.includes(s.id);
                    return (
                      <label
                        key={s.id}
                        className={`flex items-center gap-2.5 text-xs px-2.5 py-1.5 rounded-lg cursor-pointer transition-colors ${
                          isChecked ? "bg-[#facc15]/10 text-white" : "text-zinc-300 hover:bg-white/5"
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={isChecked}
                          onChange={() => {
                            setBatchForm(f => ({
                              ...f,
                              studentIds: isChecked ? f.studentIds.filter(id => id !== s.id) : [...f.studentIds, s.id]
                            }));
                          }}
                          className="rounded border-zinc-700 text-[#facc15] focus:ring-[#facc15]"
                        />
                        <span className="font-medium text-white">{s.first_name ? `${s.first_name} ${s.last_name || ""}` : s.username}</span>
                        <span className="text-zinc-500 text-[11px]">@{s.username}</span>
                        {s.phone_number && <span className="text-zinc-500 text-[11px]">• {s.phone_number}</span>}
                      </label>
                    );
                  })}
                </div>
              </div>

              <div className="flex gap-2 pt-2 border-t border-white/5">
                <button
                  type="button"
                  onClick={() => setShowBatchModal(false)}
                  className="flex-1 py-2.5 bg-white/5 hover:bg-white/10 text-zinc-300 font-semibold rounded-xl transition-all text-xs"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={batchCreating}
                  className="flex-1 py-2.5 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-all text-xs disabled:opacity-50"
                >
                  {batchCreating ? "Creating..." : "Create Batch"}
                </button>
              </div>
            </form>
          </Modal>
        )}
      </AnimatePresence>
    </div>
  );
}
