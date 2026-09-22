"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  Play, Check, ChevronLeft, Lock, ClipboardList, Trophy, Clock,
  FileText, RotateCcw
} from "lucide-react";
import WebVideoPlayer from "@/components/player/WebVideoPlayer";

// Phase 4.3: assessment status -> badge label/color + CTA label/href,
// reusing the exact status values ModuleSerializer.get_assessments
// computes server-side (courses/serializers.py) -- never re-derived
// client-side.
function assessmentStatusMeta(assessment: any) {
  // Phase 4.4, Section 15: CTA wording only -- the status VALUES and
  // access rules are unchanged from Phase 4.3.
  switch (assessment.status) {
    case "IN_PROGRESS":
      return { label: "In Progress", badgeClass: "bg-blue-500/15 text-blue-400", cta: "Continue Assessment", href: `/assessments/attempt/${assessment.attempt_id}` };
    case "PASSED":
      return { label: "Passed", badgeClass: "bg-green-500/15 text-green-400", cta: "View Result", href: `/assessments/attempt/${assessment.attempt_id}` };
    case "FAILED":
      return { label: "Failed", badgeClass: "bg-red-500/15 text-red-400", cta: "Retry Assessment", href: `/assessments/${assessment.id}` };
    case "MAX_ATTEMPTS_REACHED":
      return { label: "Max Attempts Reached", badgeClass: "bg-zinc-700/40 text-zinc-400", cta: "View Result", href: `/assessments/attempt/${assessment.attempt_id}` };
    default:
      return { label: "Not Started", badgeClass: "bg-zinc-700/40 text-zinc-400", cta: "Start Assessment", href: `/assessments/${assessment.id}` };
  }
}

// Phase 4.7: assignment status -> badge label/color, reusing the exact
// status values ModuleSerializer.get_assignments computes server-side
// (courses/serializers.py's _serialize_assignment_summary_for_student)
// -- never re-derived client-side. Every status routes to the same
// detail/submit page; that page itself decides what to show (submit
// form, submitted-awaiting-grading, grade+feedback, or resubmit form).
function assignmentStatusMeta(assignment: any) {
  switch (assignment.status) {
    case "SUBMITTED":
      return { label: "Submitted", badgeClass: "bg-blue-500/15 text-blue-400" };
    case "GRADED":
      return { label: "Graded", badgeClass: "bg-green-500/15 text-green-400" };
    case "RETURNED_FOR_REVISION":
      return { label: "Revision Requested", badgeClass: "bg-orange-500/15 text-orange-400" };
    default:
      return { label: "Not Submitted", badgeClass: "bg-zinc-700/40 text-zinc-400" };
  }
}

// Canonical language list, kept in sync with backend/courses/languages.py.
// Used to display a friendly name when a track's language_name isn't set
// (e.g. legacy AI-generated rows created before that field existed).
const LANGUAGE_NAME_MAP: Record<string, string> = {
  ml: "Malayalam", hi: "Hindi", ta: "Tamil", te: "Telugu", kn: "Kannada",
  bn: "Bengali", mr: "Marathi", gu: "Gujarati", pa: "Punjabi", ar: "Arabic",
  fr: "French", de: "German", es: "Spanish", pt: "Portuguese", it: "Italian",
  ja: "Japanese", ko: "Korean", zh: "Chinese", ru: "Russian",
};

const languageDisplayName = (audio: { language_code: string; language_name?: string }) => {
  if (audio.language_name) return audio.language_name;
  const base = audio.language_code.split('-')[0].toLowerCase();
  return LANGUAGE_NAME_MAP[base] || audio.language_code;
};

export default function CourseLearnPage() {
  const { id } = useParams();
  const [course, setCourse] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [activeLesson, setActiveLesson] = useState<any>(null);

  // Lesson Progress State and Refs
  const [savedProgressPosition, setSavedProgressPosition] = useState<number>(0);
  const lastSavedTime = useRef<number>(0);
  const activeLessonIdRef = useRef<number | null>(null);

  const getCsrfToken = () => {
    let csrfToken = "";
    if (typeof document !== 'undefined' && document.cookie) {
      const cookies = document.cookie.split(';');
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.startsWith('csrftoken=')) {
          csrfToken = decodeURIComponent(cookie.substring('csrftoken='.length));
          break;
        }
      }
    }
    return csrfToken;
  };

  const saveProgress = async (lessonId: number, position: number, duration: number, completed: boolean) => {
    if (!lessonId) return;

    let finalPosition = position;
    if (duration > 0 && finalPosition > duration) {
      finalPosition = duration;
    }
    if (finalPosition < 0) {
      finalPosition = 0;
    }

    try {
      const csrfToken = getCsrfToken();
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (csrfToken) {
        headers["X-CSRFToken"] = csrfToken;
      }

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${lessonId}/progress/`, {
        method: "POST",
        headers: headers,
        body: JSON.stringify({
          last_watched_position: finalPosition,
          video_duration: duration,
          completed: completed
        }),
        credentials: "include"
      });

      if (res.ok) {
        // Update lastSavedTime only after a successful save
        lastSavedTime.current = finalPosition;
      }
    } catch (err) {
      console.error("Failed to save progress:", err);
    }
  };

  // Fetch progress for active lesson
  useEffect(() => {
    if (!activeLesson?.id) return;

    const lessonId = activeLesson.id;
    activeLessonIdRef.current = lessonId;

    // Reset temporary states
    setSavedProgressPosition(0);
    lastSavedTime.current = 0;

    const fetchProgress = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${lessonId}/progress/`, {
          credentials: "include"
        });
        if (res.ok) {
          const data = await res.json();
          // Protect against race conditions
          if (activeLessonIdRef.current === lessonId) {
            const position = data.last_watched_position || 0;
            setSavedProgressPosition(position);
            lastSavedTime.current = position;
          }
        }
      } catch (err) {
        console.error("Failed to load progress:", err);
      }
    };

    fetchProgress();
  }, [activeLesson?.id]);


  // Phase 4.5: refreshes just the course/module completion numbers
  // (re-fetches the same GET /api/courses/<id>/ the mount effect below
  // uses) WITHOUT touching activeLesson -- reused after a lesson is
  // marked complete or an assessment is submitted, per "progress should
  // eventually reflect the new backend state without a full browser
  // restart." No new data-fetching pattern: the exact same endpoint and
  // fetch call as the initial load.
  const refreshCourseProgress = useCallback(async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${id}/`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setCourse(data);
      }
    } catch (err) {
      console.error("Failed to refresh course progress:", err);
    }
  }, [id]);

  useEffect(() => {
    const fetchCourse = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${id}/`, {
          credentials: "include"
        });
        if (res.ok) {
          const data = await res.json();
          setCourse(data);
          
          if (data.modules && data.modules.length > 0) {
            // Prefer the first UNLOCKED lesson as the default view (an
            // is_locked lesson has no playable video_file) -- fall back to
            // the very first lesson overall so the course structure is
            // still shown if nothing is unlocked yet.
            let firstLesson: any = null;
            let firstUnlockedLesson: any = null;
            for (let module of data.modules) {
              if (module.lessons && module.lessons.length > 0) {
                if (!firstLesson) firstLesson = module.lessons[0];
                const unlocked = module.lessons.find((l: any) => !l.is_locked);
                if (unlocked) {
                  firstUnlockedLesson = unlocked;
                  break;
                }
              }
            }
            setActiveLesson(firstUnlockedLesson || firstLesson);
          }
        }
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    if (id) fetchCourse();
  }, [id]);



  if (loading) return (
    <div className="min-h-screen bg-[#050505] flex flex-col items-center justify-center gap-4 text-[#facc15]">
      <span className="h-8 w-8 border-2 border-[#facc15]/30 border-t-[#facc15] rounded-full animate-spin" />
      <span className="text-sm font-medium tracking-wide text-zinc-500">Loading player…</span>
    </div>
  );
  if (!course) return (
    <div className="min-h-screen bg-[#050505] text-white flex flex-col items-center justify-center gap-3 p-8">
      <p className="text-lg font-medium text-zinc-300">Course not found.</p>
      <Link href="/dashboard" className="text-sm text-[#facc15] hover:underline font-medium">Back to Dashboard</Link>
    </div>
  );

  // Derived, purely for display: flatten modules -> lessons so we can show a
  // "Module X · Lesson Y" breadcrumb and Previous/Next navigation. Read-only,
  // doesn't touch the video/audio playback or sync logic above.
  const flatLessons: { lesson: any; moduleTitle: string; moduleIdx: number; lessonIdx: number }[] = [];
  course.modules?.forEach((m: any, mIdx: number) => {
    m.lessons?.forEach((l: any, lIdx: number) => {
      flatLessons.push({ lesson: l, moduleTitle: m.title, moduleIdx: mIdx, lessonIdx: lIdx });
    });
  });
  const activeFlatIndex = flatLessons.findIndex(f => f.lesson.id === activeLesson?.id);
  const activeFlatEntry = activeFlatIndex >= 0 ? flatLessons[activeFlatIndex] : null;
  const prevEntry = activeFlatIndex > 0 ? flatLessons[activeFlatIndex - 1] : null;
  const nextEntry = activeFlatIndex >= 0 && activeFlatIndex < flatLessons.length - 1 ? flatLessons[activeFlatIndex + 1] : null;

  return (
    <div className="min-h-screen bg-[#050505] text-white flex flex-col md:flex-row h-screen overflow-hidden">
      
      {/* Sidebar: Curriculum */}
      <div className="w-full md:w-80 bg-gradient-to-b from-zinc-950 to-black border-r border-white/5 flex flex-col h-1/3 md:h-full overflow-hidden shrink-0">
        <div className="p-5 border-b border-white/5 shrink-0 bg-white/[0.02]">
          <Link href="/dashboard" className="inline-flex items-center gap-2 text-zinc-400 hover:text-white text-sm mb-4 font-medium transition-colors group">
            <span className="w-7 h-7 rounded-full bg-white/5 group-hover:bg-white/10 flex items-center justify-center transition-colors">
              <ChevronLeft className="w-4 h-4" />
            </span>
            Back to Dashboard
          </Link>
          <h2 className="font-bold text-lg leading-tight tracking-tight">{course.title}</h2>
          {flatLessons.length > 0 && (
            <p className="text-xs text-zinc-500 mt-1.5 font-medium">
              {flatLessons.length} lesson{flatLessons.length !== 1 ? 's' : ''}
            </p>
          )}
          {/* Phase 4.5: real, backend-derived course completion -- null
              (no course access, or anonymous) simply renders nothing,
              never a fabricated 0%. */}
          {typeof course.completion_percentage === "number" && (
            <div className="mt-3">
              <div className="flex items-center justify-between text-[11px] font-semibold mb-1">
                <span className={course.is_completed ? "text-green-400" : "text-zinc-400"}>
                  {course.is_completed ? "Course Completed" : "Course Progress"}
                </span>
                <span className="text-zinc-500">{course.completion_percentage}%</span>
              </div>
              <div className="h-1.5 w-full rounded-full bg-white/10 overflow-hidden">
                <div
                  className={`h-full rounded-full ${course.is_completed ? "bg-green-400" : "bg-[#facc15]"}`}
                  style={{ width: `${course.completion_percentage}%` }}
                />
              </div>
              {/* Phase 4.6: shown ONLY when the backend says this course
                  is complete -- never computed/guessed client-side. */}
              {course.is_completed && (
                <Link
                  href={`/certificates/${id}`}
                  className="mt-3 flex items-center justify-center gap-2 w-full py-2 rounded-full bg-green-500/15 text-green-400 text-xs font-semibold hover:bg-green-500/25 transition-colors"
                >
                  🎓 View Your Certificate
                </Link>
              )}
            </div>
          )}
        </div>

        <div className="overflow-y-auto flex-1 p-3 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-white/10 [&::-webkit-scrollbar-thumb]:rounded-full">
          {course.modules?.map((module: any, idx: number) => (
            <div key={module.id} className="mb-6">
              <h3 className="flex items-center gap-2 text-xs font-bold text-zinc-500 px-3 mb-3 uppercase tracking-widest">
                <span className="w-1 h-3 bg-[#facc15]/40 rounded-full shrink-0" />
                <span className="flex-1">Module {idx + 1}: {module.title}</span>
                {typeof module.completion_percentage === "number" && (
                  module.is_completed ? (
                    <Check className="w-3.5 h-3.5 text-green-400 normal-case" />
                  ) : (
                    <span className="normal-case text-zinc-600">{module.completion_percentage}%</span>
                  )
                )}
              </h3>
              <div className="space-y-1">
                {module.lessons?.map((lesson: any, lIdx: number) => {
                  const isActive = activeLesson?.id === lesson.id;
                  return (
                    <button
                      key={lesson.id}
                      onClick={() => setActiveLesson(lesson)}
                      className={`w-full text-left px-3 py-3 rounded-xl text-sm flex items-center gap-3 transition-all duration-200 ${
                        isActive
                          ? 'bg-gradient-to-r from-[#facc15]/20 to-[#facc15]/5 text-[#facc15] font-semibold shadow-inner ring-1 ring-[#facc15]/20'
                          : 'text-zinc-300 hover:bg-white/5 hover:translate-x-0.5'
                      }`}
                    >
                      <span className={`shrink-0 w-6 h-6 rounded-full flex items-center justify-center text-[11px] font-bold transition-colors ${
                        isActive
                          ? 'bg-[#facc15] text-black'
                          : lesson.is_completed
                            ? 'bg-green-500/20 text-green-400'
                            : 'bg-white/5 text-zinc-500'
                      }`}>
                        {isActive ? (
                          <Play className="w-2.5 h-2.5 fill-black ml-0.5" />
                        ) : lesson.is_completed ? (
                          <Check className="w-3 h-3" />
                        ) : (
                          lIdx + 1
                        )}
                      </span>
                      <span className="leading-relaxed truncate flex-1">{lesson.title}</span>
                      {lesson.is_locked && <Lock className="w-3.5 h-3.5 text-zinc-600 shrink-0" />}
                    </button>
                  );
                })}

                {/* Phase 4.3: assessment entries -- a separate destination
                    page (/assessments/...), not shown inline in the video
                    player, so these are plain nav links rather than
                    setActiveLesson buttons. Server-computed status/is_published
                    only -- no client-side guessing. */}
                {module.assessments?.map((assessment: any) => {
                  const meta = assessmentStatusMeta(assessment);
                  return (
                    <Link
                      key={`assessment-${assessment.id}`}
                      href={meta.href}
                      className="w-full text-left px-3 py-3 rounded-xl text-sm flex items-center gap-3 transition-all duration-200 text-zinc-300 hover:bg-white/5 hover:translate-x-0.5"
                    >
                      <span className="shrink-0 w-6 h-6 rounded-full flex items-center justify-center bg-white/5 text-[#facc15]">
                        {assessment.status === "PASSED" ? <Trophy className="w-3.5 h-3.5" /> : <ClipboardList className="w-3.5 h-3.5" />}
                      </span>
                      <span className="flex-1 min-w-0">
                        <span className="block leading-relaxed truncate font-medium">{assessment.title}</span>
                        <span className="flex items-center gap-2 text-[11px] text-zinc-500 mt-0.5">
                          <span>{assessment.question_count} question{assessment.question_count !== 1 ? 's' : ''}</span>
                          {assessment.time_limit_minutes && (
                            <span className="flex items-center gap-0.5"><Clock className="w-2.5 h-2.5" /> {assessment.time_limit_minutes}m</span>
                          )}
                        </span>
                      </span>
                      <span className={`shrink-0 text-[10px] font-semibold px-2 py-1 rounded-full ${meta.badgeClass}`}>
                        {meta.cta}
                      </span>
                    </Link>
                  );
                })}

                {/* Phase 4.7: assignment entries -- same "separate destination
                    page, server-computed status only" pattern as assessments
                    above. */}
                {module.assignments?.map((assignment: any) => {
                  const meta = assignmentStatusMeta(assignment);
                  return (
                    <Link
                      key={`assignment-${assignment.id}`}
                      href={`/assignments/${assignment.id}`}
                      className="w-full text-left px-3 py-3 rounded-xl text-sm flex items-center gap-3 transition-all duration-200 text-zinc-300 hover:bg-white/5 hover:translate-x-0.5"
                    >
                      <span className="shrink-0 w-6 h-6 rounded-full flex items-center justify-center bg-white/5 text-[#facc15]">
                        {assignment.status === "GRADED" ? (
                          <Check className="w-3.5 h-3.5" />
                        ) : assignment.status === "RETURNED_FOR_REVISION" ? (
                          <RotateCcw className="w-3.5 h-3.5" />
                        ) : (
                          <FileText className="w-3.5 h-3.5" />
                        )}
                      </span>
                      <span className="flex-1 min-w-0">
                        <span className="block leading-relaxed truncate font-medium">{assignment.title}</span>
                        <span className="flex items-center gap-2 text-[11px] text-zinc-500 mt-0.5">
                          <span>Max marks: {assignment.max_marks}</span>
                        </span>
                      </span>
                      <span className={`shrink-0 text-[10px] font-semibold px-2 py-1 rounded-full ${meta.badgeClass}`}>
                        {meta.label}
                      </span>
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Main Content: Video Player */}
      <div className="flex-1 flex flex-col h-2/3 md:h-full relative bg-black overflow-y-auto [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-white/10 [&::-webkit-scrollbar-thumb]:rounded-full">
        {activeLesson ? (
          activeLesson.is_locked ? (
            <div className="w-full h-full flex flex-col items-center justify-center text-zinc-600 bg-[#050505] p-6 text-center">
              <Lock className="w-14 h-14 mb-6 opacity-40" />
              <p className="text-lg font-medium text-zinc-300 mb-2">{activeLesson.title}</p>
              <p className="text-sm text-zinc-500 mb-6 max-w-sm">This lesson is locked. Enroll in this course or subscribe to a plan that includes it to watch.</p>
              <Link
                href={`/courses/${id}`}
                className="px-5 py-2.5 bg-[#facc15] text-black font-semibold text-sm rounded-full hover:bg-yellow-400 transition-colors"
              >
                View Course Options
              </Link>
            </div>
          ) : (
          <div className="w-full flex flex-col relative">

            <div className="relative bg-black md:p-6 md:pb-3">
              {/* Ambient glow behind the player frame */}
              <div className="hidden md:block absolute inset-6 bg-[#facc15]/5 blur-[80px] rounded-full pointer-events-none" />

              <WebVideoPlayer
                videoUrl={activeLesson.video_file}
                title={activeLesson.title}
                lessonId={activeLesson.id}
                translatedAudios={activeLesson.translated_audios}
                savedProgressPosition={savedProgressPosition}
                onSaveProgress={(pos, dur, comp) => saveProgress(activeLesson.id, pos, dur, comp)}
                onEnded={() => {
                  if (activeLesson?.id) {
                    saveProgress(activeLesson.id, activeLesson.video_duration || 0, activeLesson.video_duration || 0, true).then(refreshCourseProgress);
                  }
                }}
              />
            </div>

            {/* Lesson Details */}
            <div className="px-6 md:px-10 pt-6 pb-12 max-w-4xl">
              {activeFlatEntry && (
                <div className="flex items-center gap-2 text-xs font-bold text-[#facc15]/80 uppercase tracking-widest mb-3">
                  <span className="w-1.5 h-1.5 rounded-full bg-[#facc15]/60" />
                  Module {activeFlatEntry.moduleIdx + 1} · Lesson {activeFlatEntry.lessonIdx + 1}
                </div>
              )}
              <h1 className="text-2xl md:text-3xl font-bold mb-4 tracking-tight">{activeLesson.title}</h1>
              {activeLesson.description && (
                <p className="text-zinc-400 leading-relaxed whitespace-pre-line text-base md:text-lg">{activeLesson.description}</p>
              )}

              {/* Previous / Next lesson navigation */}
              {(prevEntry || nextEntry) && (
                <div className="flex items-center justify-between gap-4 mt-10 pt-6 border-t border-white/10">
                  <button
                    disabled={!prevEntry}
                    onClick={() => prevEntry && setActiveLesson(prevEntry.lesson)}
                    className="flex items-center gap-1.5 text-sm font-medium text-zinc-400 hover:text-white disabled:opacity-30 disabled:hover:text-zinc-400 disabled:cursor-not-allowed transition-colors"
                  >
                    <ChevronLeft className="w-4 h-4" /> Previous
                  </button>

                  {nextEntry ? (
                    <button
                      onClick={() => setActiveLesson(nextEntry.lesson)}
                      className="flex items-center gap-2 pl-5 pr-4 py-2.5 bg-[#facc15] text-black font-semibold text-sm rounded-full hover:bg-yellow-400 transition-colors shadow-[0_8px_24px_-8px_rgba(250,204,21,0.5)]"
                    >
                      <span className="max-w-[14rem] truncate">Next: {nextEntry.lesson.title}</span>
                      <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" className="shrink-0"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
                    </button>
                  ) : (
                    <span className="text-sm font-medium text-zinc-600">🎉 Last lesson in this course</span>
                  )}
                </div>
              )}
            </div>
          </div>
          )
        ) : (
          <div className="w-full h-full flex flex-col items-center justify-center text-zinc-600 bg-[#050505]">
            <Play className="w-16 h-16 mb-6 opacity-20" />
            <p className="text-lg font-medium">Select a lesson to start learning.</p>
          </div>
        )}
      </div>

    </div>
  );
}
