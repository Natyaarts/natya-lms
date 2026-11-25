"use client";

/**
 * Teacher Salary (admin). Two tabs:
 *  - Monthly Salary: calculate each teacher's pay for a month from completed
 *    live classes (Zoom time x hourly rate by number of attending students
 *    + per-class + per-student amounts), review/correct each class, then
 *    approve and record the payment.
 *  - Rate Cards: each teacher's rates, per course or as a default.
 * All rules live in the backend (finance/teacher_payroll.py); this page only
 * displays and edits.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BadgeIndianRupee,
  Calculator,
  CheckCircle2,
  Clock,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  Users,
  X,
} from "lucide-react";

const API = `${process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000"}/api/finance/admin/teacher-salary`;

// ---------------------------------------------------------------- types
interface Teacher { id: number; name: string; username: string; is_active: boolean; rate_cards: number }
interface CourseRef { id: number; title: string }
interface Tier { min_students: number; max_students: number | null; hourly_rate: string }
interface RateCard {
  id: number; teacher_id: number; teacher_name: string; course_id: number | null; course_title: string;
  per_class_amount: string; per_student_amount: string; currency: string; is_active: boolean; notes: string;
  tiers: Tier[]; updated_at: string;
}
type StatementStatus = "DRAFT" | "APPROVED" | "PAID" | "CANCELLED";
interface Statement {
  id: number; teacher_id: number; teacher_name: string; month: string; period_start: string; period_end: string;
  status: StatementStatus; currency: string; class_count: number; total_minutes: number; classes_amount: string;
  adjustment_amount: string; adjustment_reason: string; net_amount: string; needs_review: number; notes: string;
  method: string; reference_number: string; generated_at: string | null; approved_at: string | null;
  approved_by_name: string; paid_at: string | null; paid_by_name: string;
}
interface Line {
  id: number; live_class_id: number | null; class_title: string; course_title: string; scheduled_start: string;
  is_substitute: boolean; scheduled_minutes: number; zoom_minutes: number | null; attendee_count: number;
  attendance_finalized: boolean; minutes_override: number | null; attendee_count_override: number | null;
  is_excluded: boolean; admin_note: string; per_class_amount: string; per_student_amount: string;
  paid_minutes: number; students_counted: number; hourly_rate: string; tier_label: string; hours_amount: string;
  class_amount: string; student_amount: string; amount: string; flags: string[]; blocking: boolean;
}
interface StatementDetail extends Statement { lines: Line[] }
interface GenerateResult { teacher_id: number; statement_id: number | null; result: string; reason?: string }

// ---------------------------------------------------------------- helpers
function csrf() {
  if (typeof document === "undefined") return "";
  const m = document.cookie.match(/csrftoken=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", "X-CSRFToken": csrf(), ...(init?.headers || {}) },
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg =
      data.error || data.detail ||
      Object.entries(data).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(" ") : JSON.stringify(v)}`).join(" · ") ||
      "Request failed.";
    throw new Error(msg);
  }
  return data as T;
}

const money = (v: string | number) =>
  `₹${Number(v || 0).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
const hours = (minutes: number) => `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
const istDate = (iso: string) =>
  new Date(iso).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
const currentMonth = () => {
  const now = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
};
const monthLabel = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, (mo || 1) - 1, 1).toLocaleString("en-IN", { month: "long", year: "numeric" });
};

const FLAG_LABELS: Record<string, { label: string; tone: "red" | "amber" | "zinc" }> = {
  NO_RATE_CARD: { label: "No rate card", tone: "red" },
  NO_ZOOM_TIME: { label: "No Zoom time – enter minutes", tone: "red" },
  NO_TIER_FOR_STUDENTS: { label: "No rate for this student count", tone: "red" },
  ATTENDANCE_NOT_FINALIZED: { label: "Attendance not finalized", tone: "amber" },
  ZOOM_TIME_CAPPED: { label: "Capped at schedule", tone: "zinc" },
  NO_STUDENTS: { label: "No students", tone: "amber" },
  MINUTES_OVERRIDDEN: { label: "Minutes edited", tone: "zinc" },
  STUDENTS_OVERRIDDEN: { label: "Students edited", tone: "zinc" },
  SUBSTITUTE: { label: "Substitute", tone: "zinc" },
  EXCLUDED: { label: "Excluded", tone: "zinc" },
};
const TONE = {
  red: "bg-red-500/10 text-red-400 border-red-500/20",
  amber: "bg-amber-500/10 text-amber-400 border-amber-500/20",
  zinc: "bg-white/5 text-zinc-400 border-white/10",
};
const STATUS_BADGE: Record<StatementStatus, string> = {
  DRAFT: "bg-yellow-500/10 text-[#facc15] border-yellow-500/20",
  APPROVED: "bg-blue-500/10 text-blue-400 border-blue-500/20",
  PAID: "bg-green-500/10 text-green-400 border-green-500/20",
  CANCELLED: "bg-red-500/10 text-red-400 border-red-500/20",
};

const inputCls =
  "bg-zinc-950 border border-white/10 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-600";
const btn = "inline-flex items-center justify-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold transition-all disabled:opacity-40";
const btnPrimary = `${btn} bg-[#facc15] text-black hover:bg-yellow-300`;
const btnGhost = `${btn} bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-zinc-200`;

// ================================================================= page
export default function TeacherSalaryPage() {
  const [tab, setTab] = useState<"salary" | "rates">("salary");
  const [teachers, setTeachers] = useState<Teacher[]>([]);
  const [courses, setCourses] = useState<CourseRef[]>([]);
  const [loadError, setLoadError] = useState("");
  const [teachersVersion, setTeachersVersion] = useState(0);
  const loadTeachers = useCallback(() => setTeachersVersion((v) => v + 1), []);

  useEffect(() => {
    let cancelled = false;
    api<{ teachers: Teacher[]; courses: CourseRef[] }>("/teachers/")
      .then((data) => {
        if (cancelled) return;
        setTeachers(data.teachers);
        setCourses(data.courses);
        setLoadError("");
      })
      .catch((e) => !cancelled && setLoadError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [teachersVersion]);

  return (
    <div className="max-w-7xl mx-auto pb-20 font-sans text-white">
      <div className="mb-6 flex flex-col md:flex-row md:items-end md:justify-between gap-4">
        <div>
          <h1 className="text-2xl md:text-3xl font-bold">Teacher Salary</h1>
          <p className="text-zinc-400 text-sm mt-1">
            Monthly teaching pay from completed live classes: Zoom time × hourly rate (by number of students) + per-class
            and per-student amounts.
          </p>
        </div>
        <div className="flex p-1 bg-zinc-900 border border-white/10 rounded-xl self-start">
          {(["salary", "rates"] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2 rounded-lg text-sm font-semibold transition-all ${
                tab === t ? "bg-[#facc15] text-black" : "text-zinc-400 hover:text-white"
              }`}
            >
              {t === "salary" ? "Monthly Salary" : "Rate Cards"}
            </button>
          ))}
        </div>
      </div>

      {loadError && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{loadError}</div>}

      {tab === "salary" ? (
        <MonthlySalary teachers={teachers} />
      ) : (
        <RateCards teachers={teachers} courses={courses} onChanged={loadTeachers} />
      )}
    </div>
  );
}

// ================================================================= monthly salary
function MonthlySalary({ teachers }: { teachers: Teacher[] }) {
  const [month, setMonth] = useState(currentMonth());
  // Statements are tagged with the month (and reload version) they were fetched for,
  // so "loading" is derived instead of being set inside the effect.
  const [listed, setListed] = useState<{ key: string; rows: Statement[] } | null>(null);
  const [version, setVersion] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [openId, setOpenId] = useState<number | null>(null);
  const listKey = `${month}#${version}`;
  const load = useCallback(() => setVersion((v) => v + 1), []);

  useEffect(() => {
    let cancelled = false;
    api<Statement[]>(`/statements/?month=${month}`)
      .then((rows) => {
        if (cancelled) return;
        setListed({ key: listKey, rows });
        setError("");
      })
      .catch((e) => {
        if (cancelled) return;
        setListed({ key: listKey, rows: [] });
        setError((e as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, [month, listKey]);

  const loading = listed?.key !== listKey;
  const statements = useMemo(() => listed?.rows ?? [], [listed]);

  const generate = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const data = await api<{ results: GenerateResult[] }>("/statements/generate/", {
        method: "POST",
        body: JSON.stringify({ month }),
      });
      const made = data.results.filter((r) => r.statement_id).length;
      const skipped = data.results.filter((r) => !r.statement_id);
      const name = (id: number) => teachers.find((t) => t.id === id)?.name || `Teacher #${id}`;
      setNotice(
        data.results.length === 0
          ? `No completed classes found for ${monthLabel(month)}.`
          : `Calculated ${made} statement(s) for ${monthLabel(month)}.` +
              (skipped.length ? ` Skipped: ${skipped.map((s) => `${name(s.teacher_id)} (${s.reason})`).join("; ")}` : "")
      );
      load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const totals = useMemo(() => {
    const live = statements.filter((s) => s.status !== "CANCELLED");
    return {
      teachers: live.length,
      classes: live.reduce((a, s) => a + s.class_count, 0),
      minutes: live.reduce((a, s) => a + s.total_minutes, 0),
      net: live.reduce((a, s) => a + Number(s.net_amount), 0),
      review: live.reduce((a, s) => a + (s.status === "DRAFT" ? s.needs_review : 0), 0),
    };
  }, [statements]);

  return (
    <div className="space-y-6">
      <div className="flex flex-col sm:flex-row gap-3 sm:items-center">
        <label className="text-sm text-zinc-400 font-semibold" htmlFor="salary-month">Month</label>
        <input
          id="salary-month"
          type="month"
          value={month}
          onChange={(e) => e.target.value && setMonth(e.target.value)}
          className={`${inputCls} w-full sm:w-48`}
        />
        <button onClick={generate} disabled={busy} className={btnPrimary}>
          <Calculator className="w-4 h-4" />
          {busy ? "Calculating..." : `Calculate ${monthLabel(month)}`}
        </button>
        <p className="text-xs text-zinc-500 sm:ml-2">
          Creates or refreshes draft statements. Approved and paid statements are never changed.
        </p>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl text-sm">{error}</div>}
      {notice && <div className="bg-green-500/10 border border-green-500/20 text-green-400 p-4 rounded-xl text-sm">{notice}</div>}

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <Tile icon={<Users className="w-4 h-4" />} label="Teachers" value={String(totals.teachers)} />
        <Tile icon={<CheckCircle2 className="w-4 h-4" />} label="Classes paid" value={String(totals.classes)} />
        <Tile icon={<Clock className="w-4 h-4" />} label="Hours taught" value={hours(totals.minutes)} />
        <Tile icon={<BadgeIndianRupee className="w-4 h-4" />} label="Total salary" value={money(totals.net)} highlight />
        <Tile
          icon={<AlertTriangle className="w-4 h-4" />}
          label="Classes to review"
          value={String(totals.review)}
          warn={totals.review > 0}
        />
      </div>

      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs min-w-[760px]">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Teacher</th>
                <th className="p-4 font-semibold text-right">Classes</th>
                <th className="p-4 font-semibold text-right">Hours</th>
                <th className="p-4 font-semibold text-right">Classes pay</th>
                <th className="p-4 font-semibold text-right">Bonus / deduction</th>
                <th className="p-4 font-semibold text-right">Net salary</th>
                <th className="p-4 font-semibold text-center">Status</th>
                <th className="p-4 font-semibold text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr><td colSpan={8} className="p-12 text-center text-zinc-500">Loading...</td></tr>
              ) : statements.length === 0 ? (
                <tr>
                  <td colSpan={8} className="p-12 text-center text-zinc-500 text-sm">
                    No salary statements for {monthLabel(month)} yet. Set up rate cards, then click
                    &nbsp;<span className="text-[#facc15] font-semibold">Calculate</span>.
                  </td>
                </tr>
              ) : (
                statements.map((s) => (
                  <tr key={s.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4">
                      <div className="font-bold text-white text-sm">{s.teacher_name}</div>
                      <div className="text-zinc-500 text-[10px] mt-0.5">Statement #{s.id}</div>
                    </td>
                    <td className="p-4 text-right">{s.class_count}</td>
                    <td className="p-4 text-right">{hours(s.total_minutes)}</td>
                    <td className="p-4 text-right">{money(s.classes_amount)}</td>
                    <td className={`p-4 text-right ${Number(s.adjustment_amount) < 0 ? "text-red-400" : ""}`}>
                      {Number(s.adjustment_amount) ? money(s.adjustment_amount) : "—"}
                    </td>
                    <td className="p-4 text-right font-bold text-[#facc15] text-sm">{money(s.net_amount)}</td>
                    <td className="p-4 text-center">
                      <span className={`px-2.5 py-0.5 text-[10px] font-bold rounded-full border ${STATUS_BADGE[s.status]}`}>{s.status}</span>
                      {s.status === "DRAFT" && s.needs_review > 0 && (
                        <div className="text-[10px] text-red-400 mt-1">{s.needs_review} to review</div>
                      )}
                    </td>
                    <td className="p-4 text-right">
                      <button onClick={() => setOpenId(s.id)} className={`${btnGhost} px-3 py-1.5 text-xs`}>
                        {s.status === "DRAFT" ? "Review" : "View"}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {openId !== null && (
        <StatementPanel id={openId} onClose={() => setOpenId(null)} onChanged={load} />
      )}
    </div>
  );
}

function Tile({ icon, label, value, highlight, warn }: { icon: React.ReactNode; label: string; value: string; highlight?: boolean; warn?: boolean }) {
  return (
    <div className={`bg-zinc-900 border rounded-2xl p-4 ${warn ? "border-red-500/30" : "border-white/10"}`}>
      <div className={`flex items-center gap-2 text-xs font-semibold ${warn ? "text-red-400" : "text-zinc-400"}`}>{icon}{label}</div>
      <div className={`mt-2 text-lg md:text-xl font-bold ${highlight ? "text-[#facc15]" : warn ? "text-red-400" : "text-white"}`}>{value}</div>
    </div>
  );
}

// ================================================================= statement panel
function StatementPanel({ id, onClose, onChanged }: { id: number; onClose: () => void; onChanged: () => void }) {
  const [st, setSt] = useState<StatementDetail | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [adjAmount, setAdjAmount] = useState("0");
  const [adjReason, setAdjReason] = useState("");
  const [payMethod, setPayMethod] = useState("BANK_TRANSFER");
  const [payRef, setPayRef] = useState("");

  const apply = useCallback((d: StatementDetail) => {
    setSt(d);
    setAdjAmount(d.adjustment_amount);
    setAdjReason(d.adjustment_reason);
  }, []);

  useEffect(() => {
    api<StatementDetail>(`/statements/${id}/`).then(apply).catch((e) => setError((e as Error).message));
  }, [id, apply]);

  const run = async (fn: () => Promise<StatementDetail>, confirmText?: string) => {
    if (confirmText && !confirm(confirmText)) return;
    setBusy(true);
    setError("");
    try {
      apply(await fn());
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const post = (action: string, body?: object) => () =>
    api<StatementDetail>(`/statements/${id}/${action}/`, { method: "POST", body: JSON.stringify(body || {}) });

  const isDraft = st?.status === "DRAFT";
  const blocking = st ? st.lines.filter((l) => l.blocking).length : 0;

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/70" role="dialog" aria-modal="true" onClick={onClose}>
      <div
        className="h-full w-full max-w-6xl overflow-y-auto bg-zinc-950 border-l border-white/10 p-4 md:p-8"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 mb-6">
          <div>
            <h2 className="text-xl md:text-2xl font-bold">{st ? st.teacher_name : "Loading..."}</h2>
            {st && (
              <p className="text-zinc-400 text-sm mt-1">
                {monthLabel(st.month)} · Statement #{st.id} ·{" "}
                <span className={`px-2 py-0.5 text-[10px] font-bold rounded-full border ${STATUS_BADGE[st.status]}`}>{st.status}</span>
                {st.approved_at && <> · Approved by {st.approved_by_name || "admin"}</>}
                {st.paid_at && <> · Paid {new Date(st.paid_at).toLocaleDateString("en-IN")} {st.method && `(${st.method}${st.reference_number ? ` ${st.reference_number}` : ""})`}</>}
              </p>
            )}
          </div>
          <button onClick={onClose} className="p-2 rounded-lg hover:bg-white/10 text-zinc-400" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-4 text-sm">{error}</div>}

        {st && (
          <>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
              <Tile icon={<CheckCircle2 className="w-4 h-4" />} label="Classes paid" value={String(st.class_count)} />
              <Tile icon={<Clock className="w-4 h-4" />} label="Hours" value={hours(st.total_minutes)} />
              <Tile icon={<BadgeIndianRupee className="w-4 h-4" />} label="Classes pay" value={money(st.classes_amount)} />
              <Tile icon={<BadgeIndianRupee className="w-4 h-4" />} label="Net salary" value={money(st.net_amount)} highlight />
            </div>

            {isDraft && blocking > 0 && (
              <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-4 text-sm flex gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
                {blocking} class(es) can&apos;t be paid yet (red labels). Enter the minutes, fix the teacher&apos;s rate card and
                click Recalculate, or exclude the class.
              </div>
            )}
            {isDraft && st.lines.some((l) => !l.attendance_finalized) && (
              <div className="bg-amber-500/10 border border-amber-500/20 text-amber-400 p-3 rounded-xl mb-4 text-xs">
                Some classes don&apos;t have finalized Zoom attendance yet. Check the student counts and correct them where needed.
              </div>
            )}

            <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden mb-6">
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse text-xs min-w-[980px]">
                  <thead>
                    <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                      <th className="p-3 font-semibold">Class</th>
                      <th className="p-3 font-semibold text-right">Scheduled</th>
                      <th className="p-3 font-semibold text-right">Zoom</th>
                      <th className="p-3 font-semibold text-right">Paid minutes</th>
                      <th className="p-3 font-semibold text-right">Students</th>
                      <th className="p-3 font-semibold">Rate</th>
                      <th className="p-3 font-semibold text-right">Amount</th>
                      <th className="p-3 font-semibold">Notes</th>
                      {isDraft && <th className="p-3 font-semibold text-right">Edit</th>}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/5 text-zinc-300">
                    {st.lines.length === 0 ? (
                      <tr><td colSpan={9} className="p-10 text-center text-zinc-500">No classes on this statement.</td></tr>
                    ) : (
                      st.lines.map((l) => (
                        <LineRow key={l.id} line={l} editable={isDraft} onSaved={apply} onChanged={onChanged} />
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="grid md:grid-cols-2 gap-4">
              <div className="bg-zinc-900 border border-white/10 rounded-2xl p-5">
                <h3 className="font-semibold mb-3">Bonus / deduction</h3>
                <div className="flex flex-col sm:flex-row gap-2">
                  <input
                    type="number"
                    step="0.01"
                    value={adjAmount}
                    disabled={!isDraft}
                    onChange={(e) => setAdjAmount(e.target.value)}
                    className={`${inputCls} sm:w-36`}
                    aria-label="Bonus or deduction amount"
                  />
                  <input
                    value={adjReason}
                    disabled={!isDraft}
                    onChange={(e) => setAdjReason(e.target.value)}
                    placeholder="Reason (required if not zero)"
                    className={`${inputCls} flex-1`}
                    aria-label="Reason"
                  />
                </div>
                <p className="text-[11px] text-zinc-500 mt-2">Use a negative amount for a deduction.</p>
                {isDraft && (
                  <button
                    disabled={busy}
                    className={`${btnGhost} mt-3`}
                    onClick={() =>
                      run(() =>
                        api<StatementDetail>(`/statements/${id}/`, {
                          method: "PATCH",
                          body: JSON.stringify({ adjustment_amount: adjAmount || "0", adjustment_reason: adjReason }),
                        })
                      )
                    }
                  >
                    Save adjustment
                  </button>
                )}
              </div>

              <div className="bg-zinc-900 border border-white/10 rounded-2xl p-5 space-y-3">
                <h3 className="font-semibold">Actions</h3>
                {isDraft && (
                  <div className="flex flex-wrap gap-2">
                    <button disabled={busy} className={btnGhost} onClick={() => run(post("regenerate"))}>
                      <RefreshCw className="w-4 h-4" /> Recalculate
                    </button>
                    <button
                      disabled={busy || blocking > 0}
                      className={btnPrimary}
                      onClick={() => run(post("approve"), `Approve ${money(st.net_amount)} for ${st.teacher_name}? The statement will be locked.`)}
                    >
                      <CheckCircle2 className="w-4 h-4" /> Approve
                    </button>
                  </div>
                )}
                {st.status === "APPROVED" && (
                  <div className="space-y-2">
                    <div className="flex flex-col sm:flex-row gap-2">
                      <select value={payMethod} onChange={(e) => setPayMethod(e.target.value)} className={inputCls} aria-label="Payment method">
                        <option value="BANK_TRANSFER">Bank transfer</option>
                        <option value="UPI">UPI</option>
                        <option value="OTHER">Other</option>
                      </select>
                      <input value={payRef} onChange={(e) => setPayRef(e.target.value)} placeholder="UTR / reference" className={`${inputCls} flex-1`} aria-label="Payment reference" />
                    </div>
                    <button
                      disabled={busy}
                      className={btnPrimary}
                      onClick={() =>
                        run(post("mark-paid", { method: payMethod, reference_number: payRef }), `Record that ${money(st.net_amount)} was paid to ${st.teacher_name}?`)
                      }
                    >
                      <BadgeIndianRupee className="w-4 h-4" /> Mark as paid
                    </button>
                  </div>
                )}
                {(st.status === "DRAFT" || st.status === "APPROVED") && (
                  <button
                    disabled={busy}
                    className={`${btn} bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20`}
                    onClick={() => {
                      const reason = prompt("Reason for cancelling this statement?");
                      if (reason === null) return;
                      run(post("cancel", { reason }));
                    }}
                  >
                    Cancel statement
                  </button>
                )}
                {st.status === "PAID" && <p className="text-sm text-green-400">Paid and locked.</p>}
                {st.status === "CANCELLED" && <p className="text-sm text-zinc-400">Cancelled. Calculate the month again to create a new draft.</p>}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function LineRow({ line, editable, onSaved, onChanged }: { line: Line; editable: boolean; onSaved: (d: StatementDetail) => void; onChanged: () => void }) {
  const [editing, setEditing] = useState(false);
  const [minutes, setMinutes] = useState(line.minutes_override?.toString() ?? "");
  const [students, setStudents] = useState(line.attendee_count_override?.toString() ?? "");
  const [note, setNote] = useState(line.admin_note);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const save = async (body: object) => {
    setSaving(true);
    setError("");
    try {
      onSaved(await api<StatementDetail>(`/lines/${line.id}/`, { method: "PATCH", body: JSON.stringify(body) }));
      onChanged();
      setEditing(false);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const muted = line.is_excluded ? "opacity-50" : "";
  return (
    <>
      <tr className={`align-top ${line.blocking ? "bg-red-500/5" : ""}`}>
        <td className={`p-3 ${muted}`}>
          <div className="font-semibold text-white text-[13px]">{line.class_title}</div>
          <div className="text-zinc-500 text-[11px]">{line.course_title} · {istDate(line.scheduled_start)}</div>
        </td>
        <td className={`p-3 text-right ${muted}`}>{line.scheduled_minutes}m</td>
        <td className={`p-3 text-right ${muted}`}>{line.zoom_minutes === null ? <span className="text-red-400">—</span> : `${line.zoom_minutes}m`}</td>
        <td className={`p-3 text-right font-semibold ${muted}`}>{line.paid_minutes}m</td>
        <td className={`p-3 text-right ${muted}`}>
          <span className="font-semibold">{line.students_counted}</span>
          {line.attendee_count_override !== null && <div className="text-[10px] text-zinc-500">Zoom: {line.attendee_count}</div>}
        </td>
        <td className={`p-3 ${muted}`}>
          {line.tier_label ? (
            <>
              <div>{money(line.hourly_rate)}/hr</div>
              <div className="text-[10px] text-zinc-500">{line.tier_label}</div>
            </>
          ) : (
            <span className="text-zinc-500">—</span>
          )}
          {(Number(line.per_class_amount) > 0 || Number(line.per_student_amount) > 0) && (
            <div className="text-[10px] text-zinc-500">
              {Number(line.per_class_amount) > 0 && `+${money(line.per_class_amount)}/class `}
              {Number(line.per_student_amount) > 0 && `+${money(line.per_student_amount)}/student`}
            </div>
          )}
        </td>
        <td className="p-3 text-right">
          <div className={`font-bold text-sm ${line.is_excluded ? "text-zinc-500 line-through" : "text-[#facc15]"}`}>{money(line.amount)}</div>
          {!line.is_excluded && Number(line.amount) > 0 && (
            <div className="text-[10px] text-zinc-500">
              {money(line.hours_amount)} + {money(line.class_amount)} + {money(line.student_amount)}
            </div>
          )}
        </td>
        <td className="p-3 max-w-[220px]">
          <div className="flex flex-wrap gap-1">
            {line.flags.map((f) => {
              const meta = FLAG_LABELS[f] || { label: f, tone: "zinc" as const };
              return (
                <span key={f} className={`px-1.5 py-0.5 rounded border text-[10px] font-semibold ${TONE[meta.tone]}`}>{meta.label}</span>
              );
            })}
          </div>
          {line.admin_note && <div className="text-[11px] text-zinc-400 mt-1 italic">{line.admin_note}</div>}
        </td>
        {editable && (
          <td className="p-3 text-right whitespace-nowrap">
            <button onClick={() => setEditing((v) => !v)} className="p-1.5 rounded-lg hover:bg-white/10 text-zinc-300" aria-label={`Edit ${line.class_title}`}>
              <Pencil className="w-3.5 h-3.5" />
            </button>
            <button
              disabled={saving}
              onClick={() => save({ is_excluded: !line.is_excluded })}
              className="ml-1 px-2 py-1 rounded-lg text-[11px] font-semibold border border-white/10 hover:bg-white/10 text-zinc-300"
            >
              {line.is_excluded ? "Include" : "Exclude"}
            </button>
          </td>
        )}
      </tr>
      {editable && editing && (
        <tr className="bg-white/5">
          <td colSpan={9} className="p-3">
            <div className="flex flex-col md:flex-row md:items-end gap-3">
              <label className="text-[11px] text-zinc-400">
                Paid minutes (blank = Zoom time)
                <input type="number" min={0} max={1440} value={minutes} onChange={(e) => setMinutes(e.target.value)} placeholder={String(line.zoom_minutes ?? "")} className={`${inputCls} mt-1 w-full md:w-40 block`} />
              </label>
              <label className="text-[11px] text-zinc-400">
                Students (blank = attendance count {line.attendee_count})
                <input type="number" min={0} max={1000} value={students} onChange={(e) => setStudents(e.target.value)} placeholder={String(line.attendee_count)} className={`${inputCls} mt-1 w-full md:w-40 block`} />
              </label>
              <label className="text-[11px] text-zinc-400 flex-1">
                Note
                <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why it was changed" className={`${inputCls} mt-1 w-full block`} />
              </label>
              <div className="flex gap-2">
                <button
                  disabled={saving}
                  className={btnPrimary}
                  onClick={() =>
                    save({
                      minutes_override: minutes === "" ? null : Number(minutes),
                      attendee_count_override: students === "" ? null : Number(students),
                      admin_note: note,
                    })
                  }
                >
                  {saving ? "Saving..." : "Save"}
                </button>
                <button className={btnGhost} onClick={() => setEditing(false)}>Close</button>
              </div>
            </div>
            {error && <div className="text-red-400 text-xs mt-2">{error}</div>}
          </td>
        </tr>
      )}
      {!editing && error && (
        <tr><td colSpan={9} className="px-3 pb-3 text-red-400 text-xs">{error}</td></tr>
      )}
    </>
  );
}

// ================================================================= rate cards
const EMPTY_TIERS: Tier[] = [
  { min_students: 1, max_students: 1, hourly_rate: "" },
  { min_students: 2, max_students: 5, hourly_rate: "" },
  { min_students: 6, max_students: null, hourly_rate: "" },
];

interface CardForm {
  id: number | null;
  course_id: string; // "" = default
  per_class_amount: string;
  per_student_amount: string;
  is_active: boolean;
  notes: string;
  tiers: { min: string; max: string; rate: string }[];
}

const toForm = (c?: RateCard): CardForm => ({
  id: c?.id ?? null,
  course_id: c?.course_id ? String(c.course_id) : "",
  per_class_amount: c ? c.per_class_amount : "0",
  per_student_amount: c ? c.per_student_amount : "0",
  is_active: c ? c.is_active : true,
  notes: c?.notes ?? "",
  tiers: (c ? c.tiers : EMPTY_TIERS).map((t) => ({
    min: String(t.min_students),
    max: t.max_students === null ? "" : String(t.max_students),
    rate: t.hourly_rate,
  })),
});

function RateCards({ teachers, courses, onChanged }: { teachers: Teacher[]; courses: CourseRef[]; onChanged: () => void }) {
  const [teacherId, setTeacherId] = useState<number | null>(null);
  const [cards, setCards] = useState<RateCard[]>([]);
  const [form, setForm] = useState<CardForm | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [search, setSearch] = useState("");

  const [cardsVersion, setCardsVersion] = useState(0);
  const reloadCards = () => setCardsVersion((v) => v + 1);

  useEffect(() => {
    if (teacherId === null) return;
    let cancelled = false;
    api<RateCard[]>(`/rate-cards/?teacher_id=${teacherId}`)
      .then((rows) => {
        if (cancelled) return;
        setCards(rows);
        setError("");
      })
      .catch((e) => !cancelled && setError((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [teacherId, cardsVersion]);

  const teacher = teachers.find((t) => t.id === teacherId);
  const filtered = teachers.filter((t) => `${t.name} ${t.username}`.toLowerCase().includes(search.toLowerCase()));

  const save = async () => {
    if (!form || teacherId === null) return;
    setSaving(true);
    setError("");
    try {
      const body = {
        teacher_id: teacherId,
        course_id: form.course_id ? Number(form.course_id) : null,
        per_class_amount: form.per_class_amount || "0",
        per_student_amount: form.per_student_amount || "0",
        is_active: form.is_active,
        notes: form.notes,
        tiers: form.tiers
          .filter((t) => t.min !== "" || t.rate !== "")
          .map((t) => ({ min_students: Number(t.min), max_students: t.max === "" ? null : Number(t.max), hourly_rate: t.rate || "0" })),
      };
      await api<RateCard>(form.id ? `/rate-cards/${form.id}/` : "/rate-cards/", {
        method: form.id ? "PUT" : "POST",
        body: JSON.stringify(body),
      });
      setForm(null);
      reloadCards();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const remove = async (card: RateCard) => {
    if (!confirm(`Delete the rates for ${card.course_title}? Existing statements keep the rates they used.`)) return;
    try {
      await api(`/rate-cards/${card.id}/`, { method: "DELETE" });
      reloadCards();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const setTier = (i: number, key: "min" | "max" | "rate", value: string) =>
    setForm((f) => f && { ...f, tiers: f.tiers.map((t, j) => (j === i ? { ...t, [key]: value } : t)) });

  return (
    <div className="grid lg:grid-cols-[300px_1fr] gap-6">
      <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4 h-fit">
        <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search teachers" className={`${inputCls} w-full mb-3`} aria-label="Search teachers" />
        <div className="space-y-1 max-h-[60vh] overflow-y-auto">
          {filtered.map((t) => (
            <button
              key={t.id}
              onClick={() => { setTeacherId(t.id); setForm(null); setCards([]); }}
              className={`w-full text-left px-3 py-2 rounded-lg text-sm flex items-center justify-between gap-2 transition-colors ${
                t.id === teacherId ? "bg-[#facc15] text-black font-semibold" : "hover:bg-white/5 text-zinc-300"
              }`}
            >
              <span className="truncate">{t.name}{!t.is_active && " (inactive)"}</span>
              <span className={`text-[10px] shrink-0 ${t.id === teacherId ? "text-black/70" : t.rate_cards ? "text-zinc-500" : "text-red-400"}`}>
                {t.rate_cards ? `${t.rate_cards} card${t.rate_cards > 1 ? "s" : ""}` : "no rates"}
              </span>
            </button>
          ))}
          {filtered.length === 0 && <p className="text-zinc-500 text-sm px-3 py-2">No teachers found.</p>}
        </div>
      </div>

      <div className="space-y-4">
        {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl text-sm">{error}</div>}
        {!teacher ? (
          <div className="bg-zinc-900 border border-white/10 rounded-2xl p-10 text-center text-zinc-500 text-sm">
            Choose a teacher to set their rates.
          </div>
        ) : (
          <>
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <h2 className="text-lg font-bold">{teacher.name}</h2>
                <p className="text-xs text-zinc-500">
                  Course rates apply to that course; the default applies to every other course.
                </p>
              </div>
              {!form && (
                <button className={btnPrimary} onClick={() => setForm(toForm())}>
                  <Plus className="w-4 h-4" /> Add rates
                </button>
              )}
            </div>

            {form && (
              <div className="bg-zinc-900 border border-[#facc15]/30 rounded-2xl p-5 space-y-4">
                <div className="grid md:grid-cols-3 gap-3">
                  <label className="text-xs text-zinc-400">
                    Course
                    <select value={form.course_id} onChange={(e) => setForm({ ...form, course_id: e.target.value })} className={`${inputCls} mt-1 w-full block`}>
                      <option value="">Default – all other courses</option>
                      {courses.map((c) => (
                        <option key={c.id} value={c.id}>{c.title}</option>
                      ))}
                    </select>
                  </label>
                  <label className="text-xs text-zinc-400">
                    Per class (₹)
                    <input type="number" min={0} step="0.01" value={form.per_class_amount} onChange={(e) => setForm({ ...form, per_class_amount: e.target.value })} className={`${inputCls} mt-1 w-full block`} />
                  </label>
                  <label className="text-xs text-zinc-400">
                    Per attending student, per class (₹)
                    <input type="number" min={0} step="0.01" value={form.per_student_amount} onChange={(e) => setForm({ ...form, per_student_amount: e.target.value })} className={`${inputCls} mt-1 w-full block`} />
                  </label>
                </div>

                <div>
                  <div className="text-xs text-zinc-400 font-semibold mb-2">Hourly rate by number of students attending</div>
                  <div className="space-y-2">
                    {form.tiers.map((t, i) => (
                      <div key={i} className="flex flex-wrap items-center gap-2 text-sm">
                        <input type="number" min={0} value={t.min} onChange={(e) => setTier(i, "min", e.target.value)} className={`${inputCls} w-20`} aria-label="From students" />
                        <span className="text-zinc-500">to</span>
                        <input type="number" min={0} value={t.max} onChange={(e) => setTier(i, "max", e.target.value)} placeholder="and up" className={`${inputCls} w-24`} aria-label="To students" />
                        <span className="text-zinc-500">students →</span>
                        <span className="text-zinc-400">₹</span>
                        <input type="number" min={0} step="0.01" value={t.rate} onChange={(e) => setTier(i, "rate", e.target.value)} placeholder="per hour" className={`${inputCls} w-32`} aria-label="Hourly rate" />
                        <span className="text-zinc-500">/ hour</span>
                        <button
                          onClick={() => setForm({ ...form, tiers: form.tiers.filter((_, j) => j !== i) })}
                          className="p-1.5 rounded-lg hover:bg-white/10 text-zinc-500"
                          aria-label="Remove tier"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))}
                  </div>
                  <button
                    onClick={() => setForm({ ...form, tiers: [...form.tiers, { min: "", max: "", rate: "" }] })}
                    className="mt-2 text-xs font-semibold text-[#facc15] hover:underline"
                  >
                    + Add tier
                  </button>
                  <p className="text-[11px] text-zinc-500 mt-1">
                    Leave &quot;to&quot; empty on the last tier for &quot;and above&quot;. Add a 0-student tier if teachers should be
                    paid when nobody attends.
                  </p>
                </div>

                <div className="flex flex-col md:flex-row gap-3 md:items-center">
                  <input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} placeholder="Notes (optional)" className={`${inputCls} flex-1`} aria-label="Notes" />
                  <label className="flex items-center gap-2 text-sm text-zinc-300">
                    <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
                    Active
                  </label>
                </div>

                <div className="flex gap-2">
                  <button disabled={saving} className={btnPrimary} onClick={save}>{saving ? "Saving..." : "Save rates"}</button>
                  <button className={btnGhost} onClick={() => setForm(null)}>Cancel</button>
                </div>
              </div>
            )}

            {cards.length === 0 && !form ? (
              <div className="bg-zinc-900 border border-white/10 rounded-2xl p-8 text-center text-zinc-500 text-sm">
                No rates yet. Classes taught by {teacher.name} will be flagged &quot;No rate card&quot; until rates are added.
              </div>
            ) : (
              <div className="grid md:grid-cols-2 gap-4">
                {cards.map((c) => (
                  <div key={c.id} className={`bg-zinc-900 border rounded-2xl p-5 ${c.is_active ? "border-white/10" : "border-white/5 opacity-60"}`}>
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="font-bold">{c.course_title}</div>
                        {!c.is_active && <div className="text-[11px] text-zinc-500">Inactive</div>}
                      </div>
                      <div className="flex gap-1">
                        <button onClick={() => setForm(toForm(c))} className="p-1.5 rounded-lg hover:bg-white/10 text-zinc-300" aria-label={`Edit rates for ${c.course_title}`}>
                          <Pencil className="w-4 h-4" />
                        </button>
                        <button onClick={() => remove(c)} className="p-1.5 rounded-lg hover:bg-white/10 text-red-400" aria-label={`Delete rates for ${c.course_title}`}>
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                    <div className="mt-3 space-y-1 text-sm">
                      {c.tiers.length === 0 && <div className="text-zinc-500 text-xs">No hourly rate</div>}
                      {c.tiers.map((t, i) => (
                        <div key={i} className="flex justify-between">
                          <span className="text-zinc-400">
                            {t.max_students === null ? `${t.min_students}+` : t.min_students === t.max_students ? t.min_students : `${t.min_students}–${t.max_students}`}{" "}
                            student{t.max_students === 1 && t.min_students === 1 ? "" : "s"}
                          </span>
                          <span className="font-semibold">{money(t.hourly_rate)}/hr</span>
                        </div>
                      ))}
                      <div className="flex justify-between border-t border-white/5 pt-1 mt-2">
                        <span className="text-zinc-400">Per class</span><span>{money(c.per_class_amount)}</span>
                      </div>
                      <div className="flex justify-between">
                        <span className="text-zinc-400">Per student</span><span>{money(c.per_student_amount)}</span>
                      </div>
                      {c.notes && <div className="text-[11px] text-zinc-500 pt-1">{c.notes}</div>}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
