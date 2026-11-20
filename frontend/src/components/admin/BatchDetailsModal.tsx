"use client";

// Batch details view for the admin Live Classes > Batches tab: batch info,
// enrolled students (search / remove), adding students, and the batch's
// classes. All writes go through the existing LiveBatchViewSet endpoints
// (POST/DELETE /live-batches/<id>/students/), so the backend's own rules
// (capacity, one-to-one, inactive/teacher accounts, instructor/mentor-only
// access for non-admins) still apply; this component only surfaces them.

import { useCallback, useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  X,
  Search,
  UserPlus,
  Trash2,
  Loader2,
  Users as UsersIcon,
  Calendar as CalendarIcon,
  Plus,
  Pencil,
  CheckCircle2,
  AlertCircle,
} from "lucide-react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

type Batch = {
  id: number;
  course: number;
  course_title?: string;
  instructor?: number | null;
  instructor_name?: string;
  instructor_username?: string;
  mentor?: number | null;
  mentor_name?: string;
  mentor_username?: string;
  batch_type: "GROUP" | "ONE_TO_ONE" | string;
  max_participants?: number | null;
  student_count?: number;
  created_at?: string;
  updated_at?: string;
};

type BatchStudent = {
  id: number;
  student: number;
  student_username: string;
  student_name?: string;
  student_email?: string | null;
  student_phone?: string | null;
  purchase?: number | null;
  purchase_status?: string | null;
  created_at?: string;
};

type Candidate = {
  id: number;
  username: string;
  first_name?: string;
  last_name?: string;
  email?: string;
  phone_number?: string;
  is_active?: boolean;
};

type LiveClassRow = {
  id: number;
  batch: number | null;
  title?: string;
  scheduled_start: string;
  duration_minutes?: number;
  status: string;
};

type CurrentUser = { id?: number; is_superuser?: boolean; is_staff?: boolean } | null;

type ApiList<T> = T[] | { results?: T[]; next?: string | null };

const STATUS_STYLE: Record<string, string> = {
  SCHEDULED: "bg-[#facc15]/10 text-[#facc15] border border-[#facc15]/20",
  LIVE: "bg-red-500/15 text-red-400 border border-red-500/30",
  COMPLETED: "bg-zinc-800 text-zinc-300 border border-white/10",
  CANCELLED: "bg-red-500/10 text-red-400 border border-red-500/20",
};

const authedFetch = (path: string, init?: RequestInit) =>
  fetch(`${API}${path}`, { credentials: "include", ...init });

const asList = <T,>(data: ApiList<T> | null | undefined): T[] =>
  Array.isArray(data) ? data : data?.results || [];

// Follows DRF `next` links so a large batch isn't silently truncated.
async function fetchAll<T>(path: string): Promise<T[]> {
  const out: T[] = [];
  let url: string | null = `${API}${path}`;
  for (let i = 0; url && i < 50; i++) {
    const res: Response = await fetch(url, { credentials: "include" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data: ApiList<T> = await res.json();
    out.push(...asList(data));
    url = Array.isArray(data) ? null : data?.next || null;
  }
  return out;
}

const fullName = (u: { first_name?: string; last_name?: string; username?: string }) =>
  `${u.first_name || ""} ${u.last_name || ""}`.trim() || u.username || "";

const fmtDate = (iso?: string) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime()) ? "—" : d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" });
};

const fmtDateTime = (iso?: string) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d.getTime())
    ? "—"
    : d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: true });
};

const errorText = async (res: Response, fallback: string) => {
  try {
    const data = await res.json();
    if (typeof data === "string") return data;
    return data?.error || data?.detail || fallback;
  } catch {
    return fallback;
  }
};

export default function BatchDetailsModal({
  batch,
  currentUser,
  onClose,
  onChanged,
  onEditBatch,
  onScheduleClass,
}: {
  batch: Batch;
  currentUser: CurrentUser;
  onClose: () => void;
  onChanged?: () => void;
  onEditBatch?: (b: Batch) => void;
  onScheduleClass?: (b: Batch) => void;
}) {
  const isFullAdmin = !!(currentUser?.is_superuser || currentUser?.is_staff);
  const canManage =
    isFullAdmin || (currentUser?.id != null && (currentUser.id === batch.instructor || currentUser.id === batch.mentor));

  const [tab, setTab] = useState<"students" | "classes">("students");
  const [students, setStudents] = useState<BatchStudent[]>([]);
  const [studentsLoading, setStudentsLoading] = useState(true);
  const [studentsError, setStudentsError] = useState("");
  const [studentFilter, setStudentFilter] = useState("");
  const [removingId, setRemovingId] = useState<number | null>(null);
  const [notice, setNotice] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const [showAdd, setShowAdd] = useState(false);
  const [addQuery, setAddQuery] = useState("");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [rosterCache, setRosterCache] = useState<Candidate[] | null>(null);
  const [searching, setSearching] = useState(false);
  const [addingId, setAddingId] = useState<number | null>(null);

  const [classes, setClasses] = useState<LiveClassRow[]>([]);
  const [classesFetchedAt, setClassesFetchedAt] = useState<number | null>(null);
  const [classesError, setClassesError] = useState("");
  const classesLoaded = classesFetchedAt !== null;
  const classesLoading = tab === "classes" && !classesLoaded && !classesError;

  // Fetches without touching state synchronously, so it is safe to start
  // from an effect; callers decide how loading is shown.
  const fetchStudents = useCallback(async () => {
    try {
      const rows = await fetchAll<BatchStudent>(`/api/courses/live-batches/${batch.id}/students/?page_size=200`);
      setStudents(rows);
      setStudentsError("");
    } catch {
      setStudentsError("Failed to load enrolled students.");
    } finally {
      setStudentsLoading(false);
    }
  }, [batch.id]);

  const loadStudents = useCallback(async () => {
    setStudentsLoading(true);
    await fetchStudents();
  }, [fetchStudents]);

  useEffect(() => {
    let cancelled = false;
    fetchAll<BatchStudent>(`/api/courses/live-batches/${batch.id}/students/?page_size=200`)
      .then((rows) => {
        if (!cancelled) setStudents(rows);
      })
      .catch(() => {
        if (!cancelled) setStudentsError("Failed to load enrolled students.");
      })
      .finally(() => {
        if (!cancelled) setStudentsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [batch.id]);

  useEffect(() => {
    if (tab !== "classes" || classesLoaded || classesError) return;
    let cancelled = false;
    fetchAll<LiveClassRow>(`/api/courses/live-classes/?batch=${batch.id}&page_size=100`)
      .then((rows) => {
        if (cancelled) return;
        // Defensive: only keep rows for this batch in case the API ignores the filter.
        setClasses(rows.filter((c) => String(c.batch) === String(batch.id)));
        setClassesFetchedAt(Date.now());
      })
      .catch(() => {
        if (!cancelled) setClassesError("Failed to load classes for this batch.");
      });
    return () => {
      cancelled = true;
    };
  }, [tab, classesLoaded, classesError, batch.id]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const enrolledIds = useMemo(() => new Set(students.map((s) => Number(s.student))), [students]);

  // Candidate search: admins search all active students server-side; a
  // teacher/mentor picks from their own roster (the only students the
  // backend lets them see).
  useEffect(() => {
    if (!showAdd) return;
    const q = addQuery.trim();
    let cancelled = false;
    const timer = setTimeout(async () => {
      setSearching(true);
      try {
        let rows: Candidate[];
        if (isFullAdmin) {
          if (q.length < 2) {
            rows = [];
          } else {
            const res = await authedFetch(
              `/api/users/admin-users/?role=students&status=active&page_size=25&search=${encodeURIComponent(q)}`
            );
            rows = res.ok ? asList<Candidate>(await res.json()) : [];
          }
        } else {
          let roster = rosterCache;
          if (!roster) {
            const res = await authedFetch("/api/users/me/students/");
            roster = res.ok ? asList<Candidate>(await res.json()) : [];
            if (!cancelled) setRosterCache(roster);
          }
          const lq = q.toLowerCase();
          rows = (roster || []).filter((s) =>
            !lq
              ? true
              : fullName(s).toLowerCase().includes(lq) ||
                (s.username || "").toLowerCase().includes(lq) ||
                (s.email || "").toLowerCase().includes(lq) ||
                (s.phone_number || "").toLowerCase().includes(lq)
          );
        }
        if (!cancelled) setCandidates(rows);
      } catch {
        if (!cancelled) setCandidates([]);
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [addQuery, showAdd, isFullAdmin, rosterCache]);

  const isOneToOne = batch.batch_type === "ONE_TO_ONE";
  const capacity = isOneToOne ? 1 : batch.max_participants || null;
  const isFull = capacity != null && students.length >= capacity;

  const addStudent = async (c: Candidate) => {
    setAddingId(c.id);
    setNotice(null);
    try {
      const res = await authedFetch(`/api/courses/live-batches/${batch.id}/students/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ student_id: c.id }),
      });
      if (!res.ok) {
        setNotice({ type: "error", text: await errorText(res, "Could not add the student.") });
        return;
      }
      setNotice({ type: "success", text: `${fullName(c)} added to the batch.` });
      await loadStudents();
      onChanged?.();
    } catch {
      setNotice({ type: "error", text: "Network error while adding the student." });
    } finally {
      setAddingId(null);
    }
  };

  const removeStudent = async (s: BatchStudent) => {
    const name = s.student_name || s.student_username;
    if (!confirm(`Remove ${name} from this batch? They will lose access to this batch's live classes.`)) return;
    setRemovingId(s.student);
    setNotice(null);
    try {
      const res = await authedFetch(`/api/courses/live-batches/${batch.id}/students/${s.student}/`, { method: "DELETE" });
      if (!res.ok && res.status !== 204) {
        setNotice({ type: "error", text: await errorText(res, "Could not remove the student.") });
        return;
      }
      setNotice({ type: "success", text: `${name} removed from the batch.` });
      await loadStudents();
      onChanged?.();
    } catch {
      setNotice({ type: "error", text: "Network error while removing the student." });
    } finally {
      setRemovingId(null);
    }
  };

  const filteredStudents = useMemo(() => {
    const q = studentFilter.trim().toLowerCase();
    if (!q) return students;
    return students.filter(
      (s) =>
        (s.student_name || "").toLowerCase().includes(q) ||
        (s.student_username || "").toLowerCase().includes(q) ||
        (s.student_email || "").toLowerCase().includes(q) ||
        (s.student_phone || "").toLowerCase().includes(q)
    );
  }, [students, studentFilter]);

  const { upcoming, past } = useMemo(() => {
    const now = classesFetchedAt ?? 0;
    const up: LiveClassRow[] = [];
    const pa: LiveClassRow[] = [];
    for (const c of classes) {
      const t = new Date(c.scheduled_start).getTime();
      if (c.status !== "COMPLETED" && c.status !== "CANCELLED" && t >= now - 3 * 60 * 60 * 1000) up.push(c);
      else pa.push(c);
    }
    up.sort((a, b) => +new Date(a.scheduled_start) - +new Date(b.scheduled_start));
    pa.sort((a, b) => +new Date(b.scheduled_start) - +new Date(a.scheduled_start));
    return { upcoming: up, past: pa };
  }, [classes, classesFetchedAt]);

  const showContact = students.some((s) => s.student_email != null || s.student_phone != null);

  const info: { label: string; value: React.ReactNode }[] = [
    {
      label: "Batch type",
      value: (
        <span
          className={`px-2 py-0.5 rounded text-[10px] font-bold ${
            isOneToOne
              ? "bg-purple-500/20 text-purple-300 border border-purple-500/30"
              : "bg-blue-500/20 text-blue-300 border border-blue-500/30"
          }`}
        >
          {isOneToOne ? "1-on-1" : "Group"}
        </span>
      ),
    },
    { label: "Instructor", value: batch.instructor_name || batch.instructor_username || "Unassigned" },
    {
      label: "Assigned mentor",
      value: batch.mentor_name || batch.mentor_username ? (
        <span className="text-[#facc15]">{batch.mentor_name || batch.mentor_username}</span>
      ) : (
        <span className="text-zinc-500 italic">None assigned</span>
      ),
    },
    {
      label: "Students",
      value: (
        <span>
          <span className="text-white font-semibold">{studentsLoading ? "…" : students.length}</span>
          <span className="text-zinc-500">{capacity ? ` / ${capacity} max` : " enrolled"}</span>
        </span>
      ),
    },
    { label: "Created", value: fmtDate(batch.created_at) },
    { label: "Last updated", value: fmtDate(batch.updated_at) },
  ];

  return (
    <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={onClose}>
      <motion.div
        initial={{ opacity: 0, scale: 0.97 }}
        animate={{ opacity: 1, scale: 1 }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Batch details: ${batch.course_title || `Course #${batch.course}`}`}
        className="w-full max-w-4xl bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden max-h-[90vh] flex flex-col"
      >
        {/* Header */}
        <div className="flex items-start justify-between gap-4 px-5 sm:px-6 py-4 border-b border-white/5 shrink-0">
          <div className="min-w-0">
            <p className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold">Batch #{batch.id}</p>
            <h3 className="text-lg font-bold text-white truncate">{batch.course_title || `Course #${batch.course}`}</h3>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {canManage && onEditBatch && (
              <button
                onClick={() => onEditBatch(batch)}
                className="px-2.5 py-1.5 rounded-lg bg-white/10 text-white text-[11px] font-bold hover:bg-white/15 border border-white/10 transition-colors flex items-center gap-1"
              >
                <Pencil className="w-3 h-3" /> Edit batch
              </button>
            )}
            {canManage && onScheduleClass && (
              <button
                onClick={() => onScheduleClass(batch)}
                className="px-2.5 py-1.5 rounded-lg bg-[#facc15] text-black text-[11px] font-bold hover:bg-yellow-400 transition-colors flex items-center gap-1"
              >
                <Plus className="w-3 h-3 stroke-[2.5]" /> Schedule class
              </button>
            )}
            <button onClick={onClose} className="p-1.5 text-zinc-500 hover:text-white" aria-label="Close">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        <div className="overflow-y-auto p-5 sm:p-6 space-y-5">
          {/* Info grid */}
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
            {info.map((i) => (
              <div key={i.label} className="bg-zinc-950/60 border border-white/[0.06] rounded-xl px-3.5 py-2.5">
                <p className="text-[10px] uppercase tracking-wide text-zinc-500 font-semibold mb-1">{i.label}</p>
                <div className="text-sm text-zinc-200">{i.value}</div>
              </div>
            ))}
          </div>

          {/* Tabs */}
          <div className="flex items-center gap-1 border-b border-white/[0.06]">
            {(
              [
                ["students", `Enrolled students${studentsLoading ? "" : ` (${students.length})`}`, UsersIcon],
                ["classes", `Classes${classesLoaded ? ` (${classes.length})` : ""}`, CalendarIcon],
              ] as const
            ).map(([key, label, Icon]) => (
              <button
                key={key}
                onClick={() => setTab(key)}
                className={`px-3.5 py-2 text-xs font-semibold flex items-center gap-1.5 border-b-2 -mb-px transition-colors ${
                  tab === key ? "border-[#facc15] text-white" : "border-transparent text-zinc-400 hover:text-white"
                }`}
              >
                <Icon className="w-3.5 h-3.5" /> {label}
              </button>
            ))}
          </div>

          {notice && (
            <div
              className={`flex items-start gap-2 px-3 py-2 rounded-xl text-xs border ${
                notice.type === "success"
                  ? "bg-emerald-500/10 border-emerald-500/20 text-emerald-300"
                  : "bg-red-500/10 border-red-500/20 text-red-400"
              }`}
            >
              {notice.type === "success" ? <CheckCircle2 className="w-4 h-4 shrink-0" /> : <AlertCircle className="w-4 h-4 shrink-0" />}
              <span className="flex-1">{notice.text}</span>
              <button onClick={() => setNotice(null)} className="opacity-70 hover:opacity-100" aria-label="Dismiss">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          )}

          {tab === "students" && (
            <div className="space-y-3">
              <div className="flex flex-col sm:flex-row gap-2">
                <div className="relative flex-1">
                  <Search className="w-3.5 h-3.5 text-zinc-500 absolute left-3 top-2.5" />
                  <input
                    value={studentFilter}
                    onChange={(e) => setStudentFilter(e.target.value)}
                    placeholder="Search enrolled students by name, username, email or phone..."
                    className="w-full pl-8 pr-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-xs focus:outline-none focus:border-[#facc15]"
                  />
                </div>
                {canManage && (
                  <button
                    onClick={() => {
                      setShowAdd((v) => !v);
                      setAddQuery("");
                      setCandidates([]);
                    }}
                    className={`px-3.5 py-2 rounded-xl text-xs font-bold flex items-center justify-center gap-1.5 transition-colors ${
                      showAdd ? "bg-white/10 text-white hover:bg-white/15" : "bg-[#facc15] text-black hover:bg-yellow-400"
                    }`}
                  >
                    {showAdd ? <X className="w-3.5 h-3.5" /> : <UserPlus className="w-3.5 h-3.5" />}
                    {showAdd ? "Close" : "Add students"}
                  </button>
                )}
              </div>

              {/* Add students panel */}
              {canManage && showAdd && (
                <div className="bg-zinc-950/60 border border-[#facc15]/20 rounded-xl p-3.5 space-y-2.5">
                  {isFull && (
                    <p className="text-[11px] text-amber-300 flex items-center gap-1.5">
                      <AlertCircle className="w-3.5 h-3.5" />
                      {isOneToOne
                        ? "This one-to-one batch already has a student."
                        : `This batch is full (${capacity} max). Increase Max Students via Edit batch to add more.`}
                    </p>
                  )}
                  <div className="relative">
                    <Search className="w-3.5 h-3.5 text-zinc-500 absolute left-3 top-2.5" />
                    <input
                      autoFocus
                      value={addQuery}
                      onChange={(e) => setAddQuery(e.target.value)}
                      placeholder={
                        isFullAdmin
                          ? "Type at least 2 characters: name, username, email or phone..."
                          : "Filter your students by name, username, email or phone..."
                      }
                      className="w-full pl-8 pr-8 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-xs focus:outline-none focus:border-[#facc15]"
                    />
                    {searching && <Loader2 className="w-3.5 h-3.5 text-zinc-500 absolute right-3 top-2.5 animate-spin" />}
                  </div>
                  <div className="max-h-56 overflow-y-auto space-y-1">
                    {isFullAdmin && addQuery.trim().length < 2 ? (
                      <p className="text-[11px] text-zinc-500 px-1 py-2">Search for a student to add them to this batch.</p>
                    ) : !searching && candidates.length === 0 ? (
                      <p className="text-[11px] text-zinc-500 px-1 py-2">No matching students found.</p>
                    ) : (
                      candidates.map((c) => {
                        const already = enrolledIds.has(c.id);
                        return (
                          <div
                            key={c.id}
                            className="flex items-center gap-3 px-2.5 py-2 rounded-lg hover:bg-white/[0.03] text-xs"
                          >
                            <div className="min-w-0 flex-1">
                              <p className="text-white font-medium truncate">{fullName(c)}</p>
                              <p className="text-zinc-500 text-[11px] truncate">
                                @{c.username}
                                {c.email ? ` • ${c.email}` : ""}
                                {c.phone_number ? ` • ${c.phone_number}` : ""}
                              </p>
                            </div>
                            {already ? (
                              <span className="text-[10px] font-semibold text-emerald-400 flex items-center gap-1">
                                <CheckCircle2 className="w-3 h-3" /> Enrolled
                              </span>
                            ) : (
                              <button
                                onClick={() => addStudent(c)}
                                disabled={addingId !== null || isFull}
                                className="px-2.5 py-1 rounded-lg bg-[#facc15] text-black text-[10px] font-bold hover:bg-yellow-400 disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1"
                              >
                                {addingId === c.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3 stroke-[2.5]" />}
                                Add
                              </button>
                            )}
                          </div>
                        );
                      })
                    )}
                  </div>
                </div>
              )}

              {/* Enrolled students table */}
              <div className="border border-white/[0.06] rounded-xl overflow-x-auto">
                {studentsLoading ? (
                  <div className="py-12 text-center text-zinc-500 text-xs flex items-center justify-center gap-2">
                    <Loader2 className="w-4 h-4 animate-spin" /> Loading students...
                  </div>
                ) : studentsError ? (
                  <div className="py-10 text-center text-red-400 text-xs">
                    {studentsError}{" "}
                    <button onClick={loadStudents} className="underline">
                      Retry
                    </button>
                  </div>
                ) : students.length === 0 ? (
                  <div className="py-12 text-center text-zinc-500 text-xs">
                    No students enrolled in this batch yet.
                    {canManage && !showAdd && (
                      <button onClick={() => setShowAdd(true)} className="block mx-auto mt-2 text-[#facc15] font-semibold hover:underline">
                        Add the first student
                      </button>
                    )}
                  </div>
                ) : filteredStudents.length === 0 ? (
                  <div className="py-10 text-center text-zinc-500 text-xs">No enrolled students match “{studentFilter}”.</div>
                ) : (
                  <table className="w-full text-left text-xs min-w-[640px]">
                    <thead>
                      <tr className="bg-[#141419] text-zinc-400 uppercase tracking-wider text-[10px]">
                        <th className="px-3.5 py-2.5 font-semibold w-10">#</th>
                        <th className="px-3.5 py-2.5 font-semibold">Student</th>
                        {showContact && <th className="px-3.5 py-2.5 font-semibold">Email</th>}
                        {showContact && <th className="px-3.5 py-2.5 font-semibold">Phone</th>}
                        <th className="px-3.5 py-2.5 font-semibold">Enrolment</th>
                        <th className="px-3.5 py-2.5 font-semibold">Added</th>
                        {canManage && <th className="px-3.5 py-2.5 font-semibold text-right">Action</th>}
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/[0.05] text-zinc-300">
                      {filteredStudents.map((s, idx) => (
                        <tr key={s.id} className="hover:bg-white/[0.02]">
                          <td className="px-3.5 py-2.5 text-zinc-500">{idx + 1}</td>
                          <td className="px-3.5 py-2.5">
                            <p className="text-white font-medium">{s.student_name || s.student_username}</p>
                            <p className="text-zinc-500 text-[11px]">@{s.student_username}</p>
                          </td>
                          {showContact && <td className="px-3.5 py-2.5 break-all">{s.student_email || "—"}</td>}
                          {showContact && <td className="px-3.5 py-2.5 whitespace-nowrap">{s.student_phone || "—"}</td>}
                          <td className="px-3.5 py-2.5">
                            {s.purchase ? (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-emerald-500/10 text-emerald-300 border border-emerald-500/20">
                                Purchase{s.purchase_status ? ` · ${s.purchase_status}` : ""}
                              </span>
                            ) : (
                              <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-white/5 text-zinc-400 border border-white/10">
                                Manual
                              </span>
                            )}
                          </td>
                          <td className="px-3.5 py-2.5 text-zinc-400 whitespace-nowrap">{fmtDate(s.created_at)}</td>
                          {canManage && (
                            <td className="px-3.5 py-2.5 text-right">
                              <button
                                onClick={() => removeStudent(s)}
                                disabled={removingId !== null}
                                className="p-1.5 rounded-lg bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 disabled:opacity-40"
                                title="Remove from batch"
                                aria-label={`Remove ${s.student_name || s.student_username} from batch`}
                              >
                                {removingId === s.student ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                              </button>
                            </td>
                          )}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {tab === "classes" && (
            <div className="space-y-4">
              {classesLoading ? (
                <div className="py-12 text-center text-zinc-500 text-xs flex items-center justify-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading classes...
                </div>
              ) : classesError ? (
                <div className="py-10 text-center text-red-400 text-xs">
                  {classesError}{" "}
                  <button onClick={() => setClassesError("")} className="underline">
                    Retry
                  </button>
                </div>
              ) : classes.length === 0 ? (
                <div className="py-12 text-center text-zinc-500 text-xs">No classes have been scheduled for this batch yet.</div>
              ) : (
                ([
                  ["Upcoming", upcoming],
                  ["Past & cancelled", past],
                ] as const).map(([title, rows]) =>
                  rows.length === 0 ? null : (
                    <div key={title}>
                      <p className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold mb-2">
                        {title} ({rows.length})
                      </p>
                      <div className="border border-white/[0.06] rounded-xl divide-y divide-white/[0.05]">
                        {rows.map((c) => (
                          <div key={c.id} className="flex items-center gap-3 px-3.5 py-2.5 text-xs">
                            <div className="min-w-0 flex-1">
                              <p className="text-white font-medium truncate">{c.title || "Live class"}</p>
                              <p className="text-zinc-500 text-[11px]">
                                {fmtDateTime(c.scheduled_start)}
                                {c.duration_minutes ? ` • ${c.duration_minutes} min` : ""}
                              </p>
                            </div>
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-bold ${STATUS_STYLE[c.status] || "bg-white/5 text-zinc-300 border border-white/10"}`}
                            >
                              {c.status}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )
                )
              )}
            </div>
          )}
        </div>
      </motion.div>
    </div>
  );
}
