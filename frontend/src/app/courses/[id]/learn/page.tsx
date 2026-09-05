"use client";

import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import {
  Play, Pause, Volume2, VolumeX, Maximize, Minimize,
  Settings, Check, ChevronLeft, Lock
} from "lucide-react";

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
  
  // Video & Audio Sync State
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  
  const [activeLanguage, setActiveLanguage] = useState<string>('en'); 
  const [showLanguageMenu, setShowLanguageMenu] = useState(false);

  // Custom Player State
  const [isPlaying, setIsPlaying] = useState(true);
  const [progress, setProgress] = useState(0);
  const [currentTimeStr, setCurrentTimeStr] = useState("0:00");
  const [durationStr, setDurationStr] = useState("0:00");
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showControls, setShowControls] = useState(true);
  
  const controlsTimeoutRef = useRef<NodeJS.Timeout | null>(null);

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

  // Cleanup: save progress of previous lesson when switching or unmounting
  useEffect(() => {
    const lessonId = activeLesson?.id;

    return () => {
      if (lessonId && videoRef.current) {
        const current = videoRef.current.currentTime;
        const duration = videoRef.current.duration;
        if (duration > 0 && current > 0) {
          saveProgress(lessonId, current, duration, false);
        }
      }
    };
  }, [activeLesson?.id]);

  // Double protection: seek if progress arrives after metadata has loaded
  useEffect(() => {
    if (savedProgressPosition > 0 && videoRef.current) {
      const duration = videoRef.current.duration;
      if (duration && duration > 0 && savedProgressPosition < duration) {
        if (Math.abs(videoRef.current.currentTime - savedProgressPosition) > 1) {
          videoRef.current.currentTime = savedProgressPosition;
          if (activeLanguage !== 'en' && audioRef.current) {
            audioRef.current.currentTime = savedProgressPosition;
          }
        }
      }
    }
  }, [savedProgressPosition, activeLanguage]);

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

  // Handle Fullscreen Changes
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener('fullscreenchange', handleFullscreenChange);
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
  }, []);

  // Sync Audio to Video and Player Events
  const handlePlay = () => {
    setIsPlaying(true);
    if (activeLanguage !== 'en' && audioRef.current && videoRef.current) {
      if (Math.abs(audioRef.current.currentTime - videoRef.current.currentTime) > 0.3) {
        audioRef.current.currentTime = videoRef.current.currentTime;
      }
      audioRef.current.play().catch(console.error);
    }
  };

  const handlePause = () => {
    setIsPlaying(false);
    if (audioRef.current) {
      audioRef.current.pause();
    }

    // Save progress immediately on pause
    if (activeLesson?.id && videoRef.current) {
      const current = videoRef.current.currentTime;
      const duration = videoRef.current.duration;
      if (duration > 0) {
        saveProgress(activeLesson.id, current, duration, false);
      }
    }
  };

  const handleSeek = () => {
    if (activeLanguage !== 'en' && audioRef.current && videoRef.current) {
      audioRef.current.currentTime = videoRef.current.currentTime;
    }
  };

  const handleTimeUpdate = () => {
    if (videoRef.current) {
      const current = videoRef.current.currentTime;
      const duration = videoRef.current.duration;
      if (duration > 0) {
        setProgress((current / duration) * 100);
      }
      setCurrentTimeStr(formatTime(current));

      // Periodic save: Save progress approximately every 10 seconds.
      const lessonId = activeLesson?.id;
      if (lessonId && duration > 0) {
        const timeDiff = Math.abs(current - lastSavedTime.current);
        if (timeDiff >= 10) {
          saveProgress(lessonId, current, duration, false);
        }
      }
    }
  };

  // Duration display must always come from the VIDEO element -- never the
  // alternate translated-audio element, which can legitimately have a
  // different length than the video (see CustomVideoPlayer.tsx equivalent
  // note on mobile). Only accept finite, positive readings: some MP4s
  // (moov atom not at the start of the file, or a server that doesn't
  // fully support byte-range requests) report an inaccurate, too-short
  // duration on `loadedmetadata`, then correct it later via `durationchange`
  // once more of the file has been parsed. Skipping non-finite/zero values
  // here avoids ever displaying "Infinity:NaN" mid-buffer.
  const updateDurationDisplay = () => {
    const duration = videoRef.current?.duration;
    if (typeof duration === 'number' && Number.isFinite(duration) && duration > 0) {
      setDurationStr(formatTime(duration));
    }
  };

  const handleLoadedMetadata = () => {
    if (videoRef.current) {
      const duration = videoRef.current.duration;
      updateDurationDisplay();

      // Seek to saved position on loaded metadata
      if (savedProgressPosition > 0 && duration > 0 && savedProgressPosition < duration) {
        videoRef.current.currentTime = savedProgressPosition;
        if (activeLanguage !== 'en' && audioRef.current) {
          audioRef.current.currentTime = savedProgressPosition;
        }
      }
    }
  };

  // The browser corrects an inaccurate initial duration reading via this
  // event once it has buffered/parsed enough of the video file.
  const handleDurationChange = () => {
    updateDurationDisplay();
  };

  const handleEnded = () => {
    if (activeLesson?.id && videoRef.current) {
      const duration = videoRef.current.duration;
      if (duration > 0) {
        saveProgress(activeLesson.id, duration, duration, true);
      }
    }
  };


  const formatTime = (timeInSeconds: number) => {
    const m = Math.floor(timeInSeconds / 60);
    const s = Math.floor(timeInSeconds % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  // Custom Controls Handlers
  const togglePlay = () => {
    if (videoRef.current) {
      if (videoRef.current.paused) {
        videoRef.current.play().catch(console.error);
      } else {
        videoRef.current.pause();
      }
    }
  };

  const handleProgressChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newProgress = parseFloat(e.target.value);
    setProgress(newProgress);
    if (videoRef.current) {
      const newTime = (newProgress / 100) * videoRef.current.duration;
      videoRef.current.currentTime = newTime;
    }
  };

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newVol = parseFloat(e.target.value);
    setVolume(newVol);
    setIsMuted(newVol === 0);
    applyVolume(newVol, newVol === 0);
  };

  const toggleMute = () => {
    const newMutedState = !isMuted;
    setIsMuted(newMutedState);
    applyVolume(newMutedState ? 0 : volume, newMutedState);
  };

  const applyVolume = (vol: number, muted: boolean) => {
    if (activeLanguage === 'en') {
      if (videoRef.current) {
        videoRef.current.volume = vol;
        videoRef.current.muted = muted;
      }
    } else {
      if (audioRef.current) {
        audioRef.current.volume = vol;
        audioRef.current.muted = muted;
      }
    }
  };

  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      containerRef.current?.requestFullscreen().catch(console.error);
    } else {
      document.exitFullscreen();
    }
  };

  const handleMouseMove = () => {
    setShowControls(true);
    if (controlsTimeoutRef.current) clearTimeout(controlsTimeoutRef.current);
    controlsTimeoutRef.current = setTimeout(() => {
      if (isPlaying) setShowControls(false);
    }, 2500);
  };

  const handleMouseLeave = () => {
    if (isPlaying) setShowControls(false);
  };

  // Get audio URL for a specific language
  const getAudioUrlForLang = (langCode: string) => {
    if (!activeLesson || langCode === 'en') return null;
    const audioTrack = activeLesson.translated_audios?.find((a: any) => a.language_code.startsWith(langCode) && a.status === 'completed');
    return audioTrack ? audioTrack.audio_file : null;
  };

  // Change Language
  const changeLanguage = (langCode: string) => {
    setActiveLanguage(langCode);
    setShowLanguageMenu(false);

    if (langCode === 'en') {
      if (videoRef.current) videoRef.current.muted = isMuted;
      if (audioRef.current) audioRef.current.pause();
    } else {
      const newAudioUrl = getAudioUrlForLang(langCode);
      if (videoRef.current) videoRef.current.muted = true; // Video is always muted for dubs
      if (audioRef.current && newAudioUrl) {
        audioRef.current.src = newAudioUrl;
        audioRef.current.load();
        audioRef.current.volume = volume;
        audioRef.current.muted = isMuted;
        if (videoRef.current) {
          audioRef.current.currentTime = videoRef.current.currentTime;
        }
        if (videoRef.current && !videoRef.current.paused) {
          audioRef.current.play().catch(e => console.error("Audio play blocked", e));
        }
      }
    }
  };

  // Reset to English (original video audio) and stop any alternate-language
  // audio from the previous lesson whenever the active lesson changes.
  useEffect(() => {
    setActiveLanguage('en');
    setShowLanguageMenu(false);
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.removeAttribute('src');
      audioRef.current.load();
    }
    if (videoRef.current) {
      videoRef.current.muted = isMuted;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeLesson?.id]);


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
        </div>

        <div className="overflow-y-auto flex-1 p-3 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-white/10 [&::-webkit-scrollbar-thumb]:rounded-full">
          {course.modules?.map((module: any, idx: number) => (
            <div key={module.id} className="mb-6">
              <h3 className="flex items-center gap-2 text-xs font-bold text-zinc-500 px-3 mb-3 uppercase tracking-widest">
                <span className="w-1 h-3 bg-[#facc15]/40 rounded-full shrink-0" />
                Module {idx + 1}: {module.title}
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
                        isActive ? 'bg-[#facc15] text-black' : 'bg-white/5 text-zinc-500'
                      }`}>
                        {isActive ? <Play className="w-2.5 h-2.5 fill-black ml-0.5" /> : lIdx + 1}
                      </span>
                      <span className="leading-relaxed truncate flex-1">{lesson.title}</span>
                      {lesson.is_locked && <Lock className="w-3.5 h-3.5 text-zinc-600 shrink-0" />}
                    </button>
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

              {/* Custom Video Player Container */}
              <div
                ref={containerRef}
                className="w-full aspect-video bg-black relative group flex items-center justify-center overflow-hidden md:rounded-2xl md:ring-1 md:ring-white/10 md:shadow-[0_25px_70px_-20px_rgba(0,0,0,0.9)]"
                onMouseMove={handleMouseMove}
                onMouseLeave={handleMouseLeave}
                onDoubleClick={toggleFullscreen}
              >
              <video 
                ref={videoRef}
                src={activeLesson.video_file}
                className="w-full h-full object-contain cursor-pointer"
                onPlay={handlePlay}
                onPause={handlePause}
                onSeeked={handleSeek}
                onTimeUpdate={handleTimeUpdate}
                onLoadedMetadata={handleLoadedMetadata}
                onDurationChange={handleDurationChange}
                onEnded={handleEnded}
                onClick={togglePlay}
                autoPlay
              />

              <audio ref={audioRef} className="hidden" />

              {/* Big Play Button Overlay (when paused) */}
              <AnimatePresence>
                {!isPlaying && (
                  <motion.button
                    initial={{ opacity: 0, scale: 0.8 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.8 }}
                    onClick={togglePlay}
                    className="absolute inset-0 m-auto w-20 h-20 bg-[#facc15]/90 hover:bg-[#facc15] text-black rounded-full flex items-center justify-center transition-transform hover:scale-110 shadow-[0_0_40px_rgba(250,204,21,0.3)] z-20"
                  >
                    <Play className="w-8 h-8 ml-1 fill-black" />
                  </motion.button>
                )}
              </AnimatePresence>

              {/* Controls Gradient & Bar */}
              <div 
                className={`absolute bottom-0 left-0 right-0 px-6 pt-24 pb-6 bg-gradient-to-t from-black/90 via-black/50 to-transparent transition-opacity duration-300 z-30 flex flex-col gap-3 ${showControls || !isPlaying ? 'opacity-100' : 'opacity-0'}`}
              >
                {/* Progress Bar */}
                <div className="relative w-full h-1.5 group-hover/progress:h-2 bg-white/20 rounded-full group/progress cursor-pointer flex items-center transition-all">
                  <div
                    className="absolute top-0 left-0 h-full bg-[#facc15] rounded-full shadow-[0_0_10px_rgba(250,204,21,0.5)]"
                    style={{ width: `${progress}%` }}
                  />
                  <input
                    type="range"
                    min="0"
                    max="100"
                    step="0.1"
                    value={progress}
                    onChange={handleProgressChange}
                    className="absolute top-0 left-0 w-full h-full opacity-0 cursor-pointer"
                  />
                  {/* Thumb indicator on hover */}
                  <div
                    className="absolute h-3.5 w-3.5 bg-[#facc15] rounded-full shadow-lg ring-2 ring-black/40 opacity-0 group-hover/progress:opacity-100 transition-opacity"
                    style={{ left: `calc(${progress}% - 7px)` }}
                  />
                </div>

                {/* Bottom Controls Row */}
                <div className="flex items-center justify-between mt-1">

                  {/* Left: Play/Pause, Volume, Time */}
                  <div className="flex items-center gap-2">
                    <button onClick={togglePlay} className="text-white hover:text-[#facc15] p-2 rounded-full hover:bg-white/10 transition-colors">
                      {isPlaying ? <Pause className="w-5 h-5 fill-current" /> : <Play className="w-5 h-5 fill-current" />}
                    </button>

                    <div className="flex items-center gap-2 group/volume pl-1">
                      <button onClick={toggleMute} className="text-white hover:text-[#facc15] p-2 rounded-full hover:bg-white/10 transition-colors">
                        {isMuted || volume === 0 ? <VolumeX className="w-5 h-5" /> : <Volume2 className="w-5 h-5" />}
                      </button>
                      <input
                        type="range"
                        min="0"
                        max="1"
                        step="0.05"
                        value={isMuted ? 0 : volume}
                        onChange={handleVolumeChange}
                        className="w-0 group-hover/volume:w-20 opacity-0 group-hover/volume:opacity-100 transition-all duration-300 accent-[#facc15] h-1 bg-white/20 rounded-full appearance-none outline-none"
                      />
                    </div>

                    <div className="text-sm font-medium text-white/90 font-mono tracking-wider pl-2">
                      {currentTimeStr} <span className="text-white/40 mx-1">/</span> {durationStr}
                    </div>
                  </div>

                  {/* Right: Audio Menu & Fullscreen */}
                  <div className="flex items-center gap-2">

                    {/* Netflix Style Audio Menu */}
                    <div className="relative">
                      <button
                        onClick={() => setShowLanguageMenu(!showLanguageMenu)}
                        className={`flex items-center gap-2 text-sm font-medium pl-3 pr-2.5 py-2 rounded-full border transition-colors ${
                          showLanguageMenu
                            ? 'bg-white/15 border-white/20 text-white'
                            : 'bg-white/5 border-white/10 text-white/90 hover:bg-white/10 hover:text-white'
                        }`}
                      >
                        <Settings className="w-5 h-5" />
                        <span className="text-sm font-medium">
                          {activeLanguage === 'en'
                            ? 'English'
                            : languageDisplayName(
                                activeLesson.translated_audios?.find((a: any) => a.language_code.split('-')[0] === activeLanguage) || { language_code: activeLanguage }
                              )}
                        </span>
                      </button>

                      <AnimatePresence>
                        {showLanguageMenu && (
                          <motion.div 
                            initial={{ opacity: 0, y: 10, scale: 0.95 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, y: 10, scale: 0.95 }}
                            transition={{ duration: 0.15 }}
                            className="absolute bottom-full right-0 mb-4 w-56 bg-[#18181b]/95 backdrop-blur-xl border border-white/10 rounded-2xl overflow-hidden shadow-[0_10px_40px_rgba(0,0,0,0.5)]"
                          >
                            <div className="p-3">
                              <div className="text-[11px] font-bold text-zinc-400 uppercase tracking-widest px-3 py-2 mb-1">
                                Audio Tracks
                              </div>
                              
                              <button 
                                onClick={() => changeLanguage('en')}
                                className={`w-full text-left px-3 py-2.5 text-sm rounded-xl mb-1 flex justify-between items-center transition-colors ${activeLanguage === 'en' ? 'bg-white/10 text-white font-medium' : 'hover:bg-white/5 text-zinc-300'}`}
                              >
                                English (Original)
                                {activeLanguage === 'en' && <Check className="w-4 h-4 text-[#facc15]" />}
                              </button>

                              {activeLesson.translated_audios?.filter((a: any) => a.status === 'completed').map((audio: any) => {
                                const langName = languageDisplayName(audio);
                                const isActive = activeLanguage === audio.language_code.split('-')[0];

                                return (
                                  <button
                                    key={audio.id}
                                    onClick={() => changeLanguage(audio.language_code.split('-')[0])}
                                    className={`w-full text-left px-3 py-2.5 text-sm rounded-xl mb-1 flex justify-between items-center transition-colors ${isActive ? 'bg-white/10 text-white font-medium' : 'hover:bg-white/5 text-zinc-300'}`}
                                  >
                                    {langName}
                                    {isActive && <Check className="w-4 h-4 text-[#facc15]" />}
                                  </button>
                                )
                              })}
                            </div>
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>

                    <button onClick={toggleFullscreen} className="text-white hover:text-[#facc15] p-2 rounded-full hover:bg-white/10 transition-colors">
                      {isFullscreen ? <Minimize className="w-5 h-5" /> : <Maximize className="w-5 h-5" />}
                    </button>
                  </div>

                </div>
              </div>
              </div>
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
