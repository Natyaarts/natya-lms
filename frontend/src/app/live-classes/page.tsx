"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { Video, Calendar as CalendarIcon, List as ListIcon, ExternalLink, PlayCircle, CheckCircle2 } from "lucide-react";
import NotificationBell from "@/components/NotificationBell";
import MonthCalendar from "@/components/live-classes/MonthCalendar";

// Phase 2: student-facing live classes -- upcoming/today/completed/
// cancelled, a join button, and per-session attendance/recording access.
// Reuses the same role-scoped LiveClassViewSet API as the admin page; the
// backend already restricts a student to only classes they're assigned to.

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const PROVIDER_LABEL: Record<string, string> = { ZOOM: "Zoom", GOOGLE_MEET: "Google Meet", TEAMS: "Teams", OTHER: "Other" };
const STATUS_STYLE: Record<string, string> = {
  SCHEDULED: "bg-zinc-700/50 text-zinc-300 border border-white/10",
  LIVE: "bg-green-500/10 text-green-400 border border-green-500/20",
  COMPLETED: "bg-blue-500/10 text-blue-400 border border-blue-500/20",
  CANCELLED: "bg-red-500/10 text-red-400 border border-red-500/20",
};

export default function StudentLiveClassesPage() {
  const [tab, setTab] = useState<"today" | "upcoming" | "completed" | "cancelled">("upcoming");
  const [view, setView] = useState<"list" | "calendar">("list");
  const [classes, setClasses] = useState<any[]>([]);
  const [calendarClasses, setCalendarClasses] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [attendanceById, setAttendanceById] = useState<Record<number, string>>({});

  const authedFetch = (path: string, init?: RequestInit) =>
    fetch(`${API}${path}`, { credentials: "include", ...init });

  const fetchClasses = async () => {
    setLoading(true);
    setError("");
    try {
      let path = "";
      if (tab === "today") path = "/api/courses/live-classes/today/?page_size=100";
      else if (tab === "upcoming") path = "/api/courses/live-classes/upcoming/?page_size=100";
      else if (tab === "completed") path = "/api/courses/live-classes/history/?page_size=100";
      else path = "/api/courses/live-classes/?status=CANCELLED&page_size=100";

      const res = await authedFetch(path);
      if (res.ok) {
        const data = await res.json();
        const list = Array.isArray(data) ? data : data.results || [];
        setClasses(list);
        if (tab === "completed") {
          list.forEach(async (lc: any) => {
            const aRes = await authedFetch(`/api/courses/live-classes/${lc.id}/attendance/`);
            if (aRes.ok) {
              const records = await aRes.json();
              if (records[0]) setAttendanceById((prev) => ({ ...prev, [lc.id]: records[0].status }));
            }
          });
        }
      } else {
        setError("Failed to load your live classes.");
      }
    } catch (err) {
      console.error(err);
      setError("Network error.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchClasses(); }, [tab]);

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
        setCalendarClasses([...(upData.results || upData), ...(histData.results || histData)]);
      } catch (err) {
        console.error(err);
      }
    })();
  }, [view]);

  const canJoin = (lc: any) => {
    if (lc.status === "LIVE") return true;
    if (lc.status !== "SCHEDULED") return false;
    const start = new Date(lc.scheduled_start).getTime();
    return Date.now() >= start - 10 * 60 * 1000; // joinable 10 min before start
  };

  return (
    <div className="min-h-screen bg-black text-white font-sans pb-24">
      <nav className="border-b border-white/10 bg-black/50 backdrop-blur-md fixed top-0 w-full z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <Link href="/" className="flex items-center">
            <Image src="/img/logo.png" alt="Natya LMS Logo" width={140} height={40} className="object-contain" />
          </Link>
          <div className="flex gap-4 items-center">
            <Link href="/dashboard" className="text-sm font-medium text-[#facc15] hover:text-white transition-colors">
              My Learning
            </Link>
            <NotificationBell />
          </div>
        </div>
      </nav>

      <div className="pt-32 px-6 max-w-5xl mx-auto">
        <div className="flex items-center gap-3 mb-8">
          <div className="w-10 h-10 rounded-full bg-[#facc15]/10 flex items-center justify-center text-[#facc15]">
            <Video className="w-5 h-5" />
          </div>
          <h1 className="text-3xl font-bold">Live Classes</h1>
        </div>

        <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
          <div className="flex gap-2 p-1 bg-zinc-950 border border-white/5 rounded-xl w-max">
            {(["today", "upcoming", "completed", "cancelled"] as const).map((t) => (
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
            <button onClick={() => setView("list")} className={`px-3 py-2 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-all ${view === "list" ? "bg-white/10 text-white" : "text-zinc-500 hover:text-white"}`}>
              <ListIcon className="w-3.5 h-3.5" /> List
            </button>
            <button onClick={() => setView("calendar")} className={`px-3 py-2 rounded-lg text-xs font-semibold flex items-center gap-1.5 transition-all ${view === "calendar" ? "bg-white/10 text-white" : "text-zinc-500 hover:text-white"}`}>
              <CalendarIcon className="w-3.5 h-3.5" /> Calendar
            </button>
          </div>
        </div>

        {view === "calendar" ? (
          <MonthCalendar classes={calendarClasses} />
        ) : loading ? (
          <div className="text-center py-20 text-zinc-500 text-sm">Loading...</div>
        ) : error ? (
          <div className="text-center py-20 text-red-400 text-sm">{error}</div>
        ) : classes.length === 0 ? (
          <div className="text-center py-20 bg-[#0a0a0a] border border-white/10 rounded-3xl text-zinc-400 text-sm">
            No {tab} live classes.
          </div>
        ) : (
          <div className="space-y-3">
            {classes.map((lc: any) => (
              <div key={lc.id} className="bg-[#0a0a0a] border border-white/10 rounded-2xl p-5 flex items-center justify-between flex-wrap gap-4">
                <div>
                  <div className="flex items-center gap-2 mb-1">
                    <h3 className="font-bold text-white">{lc.title}</h3>
                    <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${STATUS_STYLE[lc.status] || ""}`}>{lc.status}</span>
                    {tab === "completed" && attendanceById[lc.id] && (
                      <span className="px-2 py-0.5 text-[10px] font-bold rounded bg-[#facc15]/10 text-[#facc15] border border-[#facc15]/20 flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3" /> {attendanceById[lc.id]}
                      </span>
                    )}
                  </div>
                  <p className="text-zinc-400 text-sm">
                    {new Date(lc.scheduled_start).toLocaleString()} &middot; {lc.duration_minutes} min &middot; {PROVIDER_LABEL[lc.meeting_provider] || lc.meeting_provider}
                  </p>
                  {lc.status === "CANCELLED" && lc.cancellation_reason && (
                    <p className="text-red-400/80 text-xs mt-1">Reason: {lc.cancellation_reason}</p>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {(lc.status === "LIVE" || lc.status === "SCHEDULED") && lc.meeting_url && (
                    <a
                      href={canJoin(lc) ? lc.meeting_url : undefined}
                      target="_blank"
                      rel="noopener noreferrer"
                      className={`px-4 py-2 rounded-xl text-sm font-bold flex items-center gap-2 transition-colors ${
                        canJoin(lc) ? "bg-[#facc15] text-black hover:bg-yellow-400" : "bg-zinc-800 text-zinc-500 cursor-not-allowed pointer-events-none"
                      }`}
                    >
                      <PlayCircle className="w-4 h-4" /> {lc.status === "LIVE" ? "Join Now" : "Join"}
                    </a>
                  )}
                  {lc.status === "COMPLETED" && lc.recording_url && (
                    <a href={lc.recording_url} target="_blank" rel="noopener noreferrer" className="px-4 py-2 rounded-xl text-sm font-bold flex items-center gap-2 bg-white/5 border border-white/10 hover:bg-white/10 transition-colors">
                      <ExternalLink className="w-4 h-4" /> Recording
                    </a>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
