"use client";

import { useEffect, useState, useRef } from "react";
import Link from "next/link";
import Image from "next/image";
import { Video, Calendar as CalendarIcon, List as ListIcon, ExternalLink, PlayCircle, CheckCircle2, Play, Film, X, Copy, Clock } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";
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
  const [attendanceById, setAttendanceById] = useState<Record<number, { status: string; duration_minutes: number }>>({});
  const [watchTarget, setWatchTarget] = useState<any>(null);
  const activeHeartbeats = useRef<Record<number, NodeJS.Timeout>>({});

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
            try {
              const aRes = await authedFetch(`/api/courses/live-classes/${lc.id}/attendance/`);
              if (aRes.ok) {
                const records = await aRes.json();
                if (records[0]) {
                  setAttendanceById((prev) => ({
                    ...prev,
                    [lc.id]: {
                      status: records[0].status,
                      duration_minutes: records[0].duration_minutes ?? 0,
                    },
                  }));
                }
              }
            } catch (e) {
              // ignore
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

  useEffect(() => {
    fetchClasses();
    const interval = setInterval(fetchClasses, 30000);
    return () => clearInterval(interval);
  }, [tab]);

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

  const handleJoinClass = async (lc: any) => {
    try {
      const res = await authedFetch(`/api/courses/live-classes/${lc.id}/join/`, {
        method: "POST",
      });
      if (res.ok) {
        const data = await res.json();
        if (!activeHeartbeats.current[lc.id]) {
          activeHeartbeats.current[lc.id] = setInterval(async () => {
            try {
              await authedFetch(`/api/courses/live-classes/${lc.id}/heartbeat/`, {
                method: "POST",
              });
            } catch (err) {
              console.error("Heartbeat error", err);
            }
          }, 60000);
        }
        const targetUrl = data.meeting_url || lc.host_url || lc.meeting_url;
        if (targetUrl) {
          window.open(targetUrl, "_blank", "noopener,noreferrer");
          return;
        }
      }
    } catch (e) {
      console.error("Join tracking error", e);
    }
    const fallbackUrl = lc.host_url || lc.meeting_url;
    if (fallbackUrl) {
      window.open(fallbackUrl, "_blank", "noopener,noreferrer");
    }
  };

  useEffect(() => {
    return () => {
      Object.values(activeHeartbeats.current).forEach(clearInterval);
    };
  }, []);

  return (
    <div className="min-h-screen bg-black text-white font-sans pb-24">
      <div className="pt-32 px-6 max-w-5xl mx-auto">
        <div className="flex items-center gap-3 mb-8">
          <div className="w-10 h-10 rounded-full bg-[#facc15]/10 flex items-center justify-center text-[#facc15]">
            <Video className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-3xl font-bold">Live Classes</h1>
            <p className="text-xs text-zinc-400 mt-0.5">Attend scheduled sessions and watch recordings of past classes</p>
          </div>
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
                {t === "completed" ? "History / Past" : t}
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
              <div key={lc.id} className="bg-[#0a0a0a] border border-white/10 rounded-2xl p-5 flex items-center justify-between flex-wrap gap-4 hover:border-white/20 transition-colors">
                <div>
                  <div className="flex items-center gap-2 mb-1 flex-wrap">
                    <h3 className="font-bold text-white text-base">{lc.title}</h3>
                    <span className={`px-2 py-0.5 text-[10px] font-bold rounded ${STATUS_STYLE[lc.status] || ""}`}>{lc.status}</span>
                    {tab === "completed" && attendanceById[lc.id] && (
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className={`px-2 py-0.5 text-[10px] font-bold rounded flex items-center gap-1 ${
                          attendanceById[lc.id].status === "ABSENT"
                            ? "bg-red-500/10 text-red-400 border border-red-500/20"
                            : "bg-green-500/10 text-green-400 border border-green-500/20"
                        }`}>
                          <CheckCircle2 className="w-3 h-3" /> Attendance: {attendanceById[lc.id].status === "ABSENT" ? "Absent" : "Present"}
                        </span>
                        {attendanceById[lc.id].status !== "ABSENT" && (
                          <span className="px-2 py-0.5 text-[10px] font-medium rounded bg-zinc-800 text-zinc-300 border border-white/10 flex items-center gap-1">
                            <Clock className="w-3 h-3 text-yellow-400" />
                            Attended: {attendanceById[lc.id].duration_minutes || 0} min
                          </span>
                        )}
                      </div>
                    )}
                    {lc.recording_url && (
                      <span className="px-2 py-0.5 text-[10px] font-bold rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 flex items-center gap-1">
                        <Film className="w-3 h-3" /> Recording Available
                      </span>
                    )}
                  </div>
                  {(lc.course_title || lc.batch_name) && (
                    <p className="text-xs text-zinc-300 font-medium mb-1">
                      {lc.course_title}{lc.course_title && lc.batch_name ? " · " : ""}{lc.batch_name}
                    </p>
                  )}
                  <p className="text-zinc-400 text-xs">
                    {new Date(lc.scheduled_start).toLocaleString()} &middot; {lc.duration_minutes} min &middot; {PROVIDER_LABEL[lc.meeting_provider] || lc.meeting_provider}
                  </p>
                  {lc.status === "CANCELLED" && lc.cancellation_reason && (
                    <p className="text-red-400/80 text-xs mt-1">Reason: {lc.cancellation_reason}</p>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {(lc.status === "LIVE" || lc.status === "SCHEDULED") && (lc.host_url || lc.meeting_url) && (
                    <button
                      type="button"
                      disabled={!canJoin(lc)}
                      onClick={() => handleJoinClass(lc)}
                      className={`px-4 py-2 rounded-xl text-sm font-bold flex items-center gap-2 transition-colors ${
                        canJoin(lc) ? "bg-[#facc15] text-black hover:bg-yellow-400 shadow-sm cursor-pointer" : "bg-zinc-800 text-zinc-500 cursor-not-allowed"
                      }`}
                    >
                      <PlayCircle className="w-4 h-4" /> {lc.status === "LIVE" ? (lc.host_url ? "Start as Host" : "Join Now") : (lc.host_url ? "Start Class" : "Join")}
                    </button>
                  )}
                  {lc.status === "COMPLETED" && lc.recording_url && (
                    <button
                      type="button"
                      onClick={() => setWatchTarget(lc)}
                      className="px-4 py-2 rounded-xl text-sm font-bold flex items-center gap-2 bg-emerald-500 text-black hover:bg-emerald-400 transition-colors shadow-sm"
                    >
                      <Play className="w-4 h-4 fill-current" /> Watch Recording
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ---------------- Watch Recording Video Player Modal ---------------- */}
      <AnimatePresence>
        {watchTarget && (
          <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4" onClick={() => setWatchTarget(null)}>
            <motion.div
              initial={{ opacity: 0, scale: 0.96 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.96 }}
              onClick={(e) => e.stopPropagation()}
              className="w-full max-w-3xl bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl flex flex-col"
            >
              <div className="flex items-center justify-between px-5 py-4 border-b border-white/5 shrink-0">
                <h3 className="font-bold text-white text-base truncate pr-4">
                  Session Recording -- {watchTarget.title}
                </h3>
                <button onClick={() => setWatchTarget(null)} className="p-1 rounded-lg hover:bg-white/5 text-zinc-400 hover:text-white">
                  <X className="w-5 h-5" />
                </button>
              </div>
              <div className="p-5 space-y-4">
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
                    <span className="font-bold text-white">Class Video Recording</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        navigator.clipboard.writeText(watchTarget.recording_url);
                        alert("Recording URL copied to clipboard!");
                      }}
                      className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/15 text-white font-medium text-xs flex items-center gap-1.5 transition-colors"
                    >
                      <Copy className="w-3.5 h-3.5" /> Copy Link
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
            </motion.div>
          </div>
        )}
      </AnimatePresence>
    </div>
  );
}
