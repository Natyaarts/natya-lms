"use client";

/**
 * Historical recordings (sessions imported from the previous LMS), shown inside
 * Admin -> Live Classes -> History and on the student/teacher Live Classes page
 * (History / Past -> Earlier recordings). All permission decisions are made by the
 * backend (courses/historical_recording_access.py); this component only renders
 * what the API returns and asks for a fresh signed URL when a video is played.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Search, X, Play, Film, FileText, ChevronLeft, ChevronRight, Loader2, Lock } from "lucide-react";
import WebVideoPlayer from "@/components/player/WebVideoPlayer";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TZ = "Asia/Kolkata";
const PAGE_SIZE = 25;

type RecordingFile = { id: number; file_type: "MP4" | "VTT"; filename: string; file_size_bytes: number | null };
type Recording = {
  id: number;
  title: string;
  course: { id: number; title: string };
  class_name: string;
  teacher_name: string;
  actual_start: string | null;
  actual_end: string | null;
  duration: string;
  files: RecordingFile[];
};
type ListResponse = {
  count: number;
  next: string | null;
  previous: string | null;
  results: Recording[];
  access?: { scope: "all" | "mapped"; mapping_pending: boolean };
};
type Filters = {
  total: number;
  courses: { id: number; title: string; count: number }[];
  teachers: { name: string; count: number }[];
  mapping_pending: boolean;
};
type Playback = { url: string; subtitle_url: string | null; expires_in: number; session: { title: string; course: string; teacher_name: string; actual_start: string | null } };

const fmtDate = (iso: string | null) =>
  iso ? new Date(iso).toLocaleDateString("en-IN", { timeZone: TZ, day: "2-digit", month: "short", year: "numeric" }) : "—";
const fmtTime = (iso: string | null) =>
  iso ? new Date(iso).toLocaleTimeString("en-IN", { timeZone: TZ, hour: "2-digit", minute: "2-digit" }) : "—";
const fmtSize = (bytes: number | null) => {
  if (!bytes) return "";
  const mb = bytes / (1024 * 1024);
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
};

export default function HistoricalRecordingsPanel() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [course, setCourse] = useState("");
  const [teacher, setTeacher] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [videoOnly, setVideoOnly] = useState(false);
  const [page, setPage] = useState(1);

  const [filters, setFilters] = useState<Filters | null>(null);
  const [data, setData] = useState<ListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [player, setPlayer] = useState<{ fileId: number; recording: Recording; playback: Playback | null; error: string } | null>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 350);
    return () => clearTimeout(t);
  }, [q]);

  useEffect(() => {
    setPage(1);
  }, [debouncedQ, course, teacher, dateFrom, dateTo, videoOnly]);

  useEffect(() => {
    fetch(`${API}/api/courses/historical-recordings/filters/`, { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && setFilters(d))
      .catch(() => {});
  }, []);

  const query = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (debouncedQ) p.set("q", debouncedQ);
    if (course) p.set("course", course);
    if (teacher) p.set("teacher", teacher);
    if (dateFrom) p.set("date_from", dateFrom);
    if (dateTo) p.set("date_to", dateTo);
    if (videoOnly) p.set("has_video", "1");
    return p.toString();
  }, [page, debouncedQ, course, teacher, dateFrom, dateTo, videoOnly]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    fetch(`${API}/api/courses/historical-recordings/?${query}`, { credentials: "include" })
      .then(async (r) => {
        if (!r.ok) throw new Error(r.status === 401 ? "Please sign in again." : "Could not load historical recordings.");
        return r.json();
      })
      .then((d: ListResponse) => !cancelled && setData(d))
      .catch((e: Error) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [query]);

  const fetchPlayback = useCallback(async (fileId: number): Promise<Playback> => {
    const r = await fetch(`${API}/api/courses/historical-recordings/files/${fileId}/playback/`, { credentials: "include" });
    if (!r.ok) throw new Error(r.status === 404 ? "This recording is not available to your account." : "Could not start playback.");
    return r.json();
  }, []);

  const openPlayer = async (recording: Recording, fileId: number) => {
    setPlayer({ fileId, recording, playback: null, error: "" });
    try {
      const playback = await fetchPlayback(fileId);
      setPlayer((p) => (p && p.fileId === fileId ? { ...p, playback } : p));
    } catch (e) {
      setPlayer((p) => (p && p.fileId === fileId ? { ...p, error: (e as Error).message } : p));
    }
  };

  const total = data?.count ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pending = data?.access?.mapping_pending ?? filters?.mapping_pending ?? false;
  const hasFilters = !!(q || course || teacher || dateFrom || dateTo || videoOnly);
  const inputCls = "bg-zinc-950 border border-white/10 rounded-lg px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15]";

  return (
    <div className="p-4 sm:p-5 space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-white font-bold text-sm flex items-center gap-2"><Film className="w-4 h-4 text-[#facc15]" /> Historical recordings</h3>
          <p className="text-[11px] text-zinc-500 mt-0.5">Class recordings imported from the previous LMS. Times shown in IST.</p>
        </div>
        <span className="text-xs text-zinc-400">
          {loading ? "Loading…" : `${total.toLocaleString("en-IN")} recording${total === 1 ? "" : "s"}`}
          {!loading && filters && total !== filters.total && ` of ${filters.total.toLocaleString("en-IN")}`}
        </span>
      </div>

      {pending ? (
        <div className="flex items-start gap-3 p-4 rounded-xl border border-white/10 bg-white/[0.03] text-sm text-zinc-300">
          <Lock className="w-4 h-4 mt-0.5 text-zinc-500 shrink-0" />
          <div>
            <p className="font-semibold text-white">Not available for your account yet</p>
            <p className="text-xs text-zinc-400 mt-1">
              Earlier recordings appear here once they are confirmed for your batch or for sessions you taught.
            </p>
          </div>
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="w-3.5 h-3.5 text-zinc-500 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                aria-label="Search recordings"
                placeholder="Search title, course, teacher or class"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                className={`${inputCls} w-full pl-8`}
              />
            </div>
            <select aria-label="Course" value={course} onChange={(e) => setCourse(e.target.value)} className={`${inputCls} max-w-[220px]`}>
              <option value="">All courses</option>
              {filters?.courses.map((c) => (
                <option key={c.id} value={c.id}>{c.title} ({c.count})</option>
              ))}
            </select>
            <select aria-label="Teacher" value={teacher} onChange={(e) => setTeacher(e.target.value)} className={`${inputCls} max-w-[200px]`}>
              <option value="">All teachers</option>
              {filters?.teachers.map((t) => (
                <option key={t.name} value={t.name}>{t.name} ({t.count})</option>
              ))}
            </select>
            <input aria-label="From date" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className={inputCls} />
            <span className="text-zinc-600 text-xs">to</span>
            <input aria-label="To date" type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className={inputCls} />
            <label className="flex items-center gap-1.5 text-xs text-zinc-300 px-2 cursor-pointer select-none">
              <input type="checkbox" checked={videoOnly} onChange={(e) => setVideoOnly(e.target.checked)} className="accent-[#facc15]" />
              With video
            </label>
            {hasFilters && (
              <button
                type="button"
                onClick={() => { setQ(""); setCourse(""); setTeacher(""); setDateFrom(""); setDateTo(""); setVideoOnly(false); }}
                className="text-xs text-zinc-400 hover:text-white flex items-center gap-1 px-2 py-2"
              >
                <X className="w-3.5 h-3.5" /> Clear
              </button>
            )}
          </div>

          {error ? (
            <div className="p-4 rounded-xl border border-red-500/20 bg-red-500/10 text-sm text-red-300">{error}</div>
          ) : (
            <div className="overflow-x-auto rounded-xl border border-white/10">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[10px] uppercase tracking-wider text-zinc-500 border-b border-white/10 bg-white/[0.02]">
                    <th className="px-4 py-3">Session</th>
                    <th className="px-4 py-3">Course · class</th>
                    <th className="px-4 py-3">Teacher</th>
                    <th className="px-4 py-3">Date</th>
                    <th className="px-4 py-3">Time</th>
                    <th className="px-4 py-3">Duration</th>
                    <th className="px-4 py-3">Files</th>
                    <th className="px-4 py-3 text-right">Play</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {loading && !data ? (
                    <tr><td colSpan={8} className="px-4 py-12 text-center text-zinc-500"><Loader2 className="w-4 h-4 animate-spin inline mr-2" />Loading recordings…</td></tr>
                  ) : (data?.results.length ?? 0) === 0 ? (
                    <tr><td colSpan={8} className="px-4 py-12 text-center text-zinc-500">{hasFilters ? "No recordings match these filters." : "No historical recordings."}</td></tr>
                  ) : (
                    data!.results.map((r) => {
                      const video = r.files.find((f) => f.file_type === "MP4");
                      const hasVtt = r.files.some((f) => f.file_type === "VTT");
                      return (
                        <tr key={r.id} className={`hover:bg-white/[0.03] ${loading ? "opacity-60" : ""}`}>
                          <td className="px-4 py-3 text-white font-medium max-w-[260px]"><span className="line-clamp-2">{r.title || "Untitled session"}</span></td>
                          <td className="px-4 py-3 text-zinc-300 max-w-[240px]">
                            <span className="block truncate">{r.course.title}</span>
                            {r.class_name && <span className="block text-[11px] text-zinc-500 truncate">{r.class_name}</span>}
                          </td>
                          <td className="px-4 py-3 text-zinc-300 whitespace-nowrap">{r.teacher_name || "—"}</td>
                          <td className="px-4 py-3 text-zinc-300 whitespace-nowrap">{fmtDate(r.actual_start)}</td>
                          <td className="px-4 py-3 text-zinc-400 whitespace-nowrap text-xs">{fmtTime(r.actual_start)} – {fmtTime(r.actual_end)}</td>
                          <td className="px-4 py-3 text-zinc-400 whitespace-nowrap text-xs">{r.duration || "—"}</td>
                          <td className="px-4 py-3">
                            <div className="flex gap-1">
                              {video && <span className="px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-300 text-[10px] font-semibold" title={fmtSize(video.file_size_bytes)}>MP4</span>}
                              {hasVtt && <span className="px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-300 text-[10px] font-semibold flex items-center gap-0.5"><FileText className="w-3 h-3" />VTT</span>}
                              {!video && !hasVtt && <span className="text-[11px] text-zinc-600">None verified</span>}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-right">
                            {video ? (
                              <button
                                type="button"
                                onClick={() => openPlayer(r, video.id)}
                                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#facc15] hover:bg-yellow-400 text-black text-xs font-bold"
                              >
                                <Play className="w-3.5 h-3.5 fill-current" /> Play
                              </button>
                            ) : (
                              <span className="text-[11px] text-zinc-600">—</span>
                            )}
                          </td>
                        </tr>
                      );
                    })
                  )}
                </tbody>
              </table>
            </div>
          )}

          {total > PAGE_SIZE && (
            <div className="flex items-center justify-between text-xs text-zinc-400">
              <span>Page {page} of {pages}</span>
              <div className="flex gap-1.5">
                <button type="button" disabled={page <= 1 || loading} onClick={() => setPage((p) => p - 1)} className="p-1.5 rounded-lg border border-white/10 disabled:opacity-30 hover:bg-white/5" aria-label="Previous page"><ChevronLeft className="w-4 h-4" /></button>
                <button type="button" disabled={page >= pages || loading} onClick={() => setPage((p) => p + 1)} className="p-1.5 rounded-lg border border-white/10 disabled:opacity-30 hover:bg-white/5" aria-label="Next page"><ChevronRight className="w-4 h-4" /></button>
              </div>
            </div>
          )}
        </>
      )}

      {player && (
        <div className="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4" onClick={() => setPlayer(null)}>
          <div className="w-full max-w-4xl bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-white/5">
              <div className="min-w-0">
                <h3 className="font-bold text-white text-sm truncate">{player.recording.title || "Untitled session"}</h3>
                <p className="text-[11px] text-zinc-500 mt-0.5 truncate">
                  {player.recording.course.title}{player.recording.teacher_name ? ` · ${player.recording.teacher_name}` : ""} · {fmtDate(player.recording.actual_start)} {fmtTime(player.recording.actual_start)}
                </p>
              </div>
              <button type="button" onClick={() => setPlayer(null)} className="p-1 rounded-lg text-zinc-400 hover:text-white hover:bg-white/5" aria-label="Close player"><X className="w-5 h-5" /></button>
            </div>
            <div className="p-4">
              {player.error ? (
                <div className="aspect-video flex items-center justify-center text-sm text-red-300 bg-black rounded-xl">{player.error}</div>
              ) : !player.playback ? (
                <div className="aspect-video flex items-center justify-center text-sm text-zinc-500 bg-black rounded-xl"><Loader2 className="w-5 h-5 animate-spin mr-2" />Preparing secure playback…</div>
              ) : (
                <WebVideoPlayer
                  key={player.fileId}
                  videoUrl={player.playback.url}
                  title={player.recording.title}
                  expiresIn={player.playback.expires_in}
                  downloadEnabled={false}
                  watermark={{ enabled: false }}
                  onRefreshUrls={async () => {
                    try {
                      const fresh = await fetchPlayback(player.fileId);
                      return { videoUrl: fresh.url, expiresIn: fresh.expires_in };
                    } catch {
                      return null;
                    }
                  }}
                />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
