"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Play,
  Pause,
  Volume2,
  VolumeX,
  Maximize,
  Minimize,
  Settings,
  Check,
  RotateCcw,
  RotateCw,
  AlertCircle,
  RefreshCw,
  Gauge,
} from "lucide-react";

export interface AudioTrackOption {
  id: number | string;
  language_code: string;
  language_name?: string;
  audio_file: string;
  status?: string;
}

export interface WebVideoPlayerProps {
  videoUrl: string;
  poster?: string;
  title?: string;
  lessonId?: number;
  translatedAudios?: AudioTrackOption[];
  savedProgressPosition?: number;
  onSaveProgress?: (position: number, duration: number, completed: boolean) => Promise<void> | void;
  onEnded?: () => void;
}

const SPEED_OPTIONS = [0.5, 0.75, 1, 1.25, 1.5, 2];

const LANGUAGE_NAME_MAP: Record<string, string> = {
  ml: "Malayalam",
  hi: "Hindi",
  ta: "Tamil",
  te: "Telugu",
  kn: "Kannada",
  bn: "Bengali",
  mr: "Marathi",
  gu: "Gujarati",
  pa: "Punjabi",
  ar: "Arabic",
  fr: "French",
  de: "German",
  es: "Spanish",
  pt: "Portuguese",
  it: "Italian",
  ja: "Japanese",
  ko: "Korean",
  zh: "Chinese",
  ru: "Russian",
};

export const getLanguageDisplayName = (audio: { language_code: string; language_name?: string }) => {
  if (audio.language_name) return audio.language_name;
  const base = audio.language_code.split("-")[0].toLowerCase();
  return LANGUAGE_NAME_MAP[base] || audio.language_code;
};

const formatTime = (timeInSeconds: number) => {
  if (!Number.isFinite(timeInSeconds) || isNaN(timeInSeconds) || timeInSeconds < 0) {
    return "0:00";
  }
  const m = Math.floor(timeInSeconds / 60);
  const s = Math.floor(timeInSeconds % 60);
  return `${m}:${s < 10 ? "0" : ""}${s}`;
};

export default function WebVideoPlayer({
  videoUrl,
  poster,
  title,
  lessonId,
  translatedAudios = [],
  savedProgressPosition = 0,
  onSaveProgress,
  onEnded,
}: WebVideoPlayerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  // Playback state
  const [isPlaying, setIsPlaying] = useState(true);
  const [progressPercent, setProgressPercent] = useState(0);
  const [bufferedPercent, setBufferedPercent] = useState(0);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackSpeed, setPlaybackSpeed] = useState(1);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Buffering & Error state
  const [isBuffering, setIsBuffering] = useState(true);
  const [hasError, setHasError] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  // Menus & Controls UI
  const [showControls, setShowControls] = useState(true);
  const [showLanguageMenu, setShowLanguageMenu] = useState(false);
  const [showSpeedMenu, setShowSpeedMenu] = useState(false);
  const [activeLanguage, setActiveLanguage] = useState<string>("en");

  // Ref tracking
  const controlsTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const lastSavedTimeRef = useRef<number>(0);
  const hasAppliedInitialSeekRef = useRef(false);

  // Available completed alternate audio tracks
  const availableAudios = translatedAudios.filter(
    (a) => (!a.status || a.status === "completed") && a.audio_file
  );

  // Reset states when lesson or videoUrl changes
  useEffect(() => {
    setActiveLanguage("en");
    setShowLanguageMenu(false);
    setShowSpeedMenu(false);
    hasAppliedInitialSeekRef.current = false;
    lastSavedTimeRef.current = 0;
    setIsBuffering(true);
    setHasError(false);
    setErrorMessage(null);
    setProgressPercent(0);
    setBufferedPercent(0);
    setCurrentTime(0);
    setDuration(0);

    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.removeAttribute("src");
      audioRef.current.load();
    }
  }, [lessonId, videoUrl]);

  // Fullscreen event listener
  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };
    document.addEventListener("fullscreenchange", handleFullscreenChange);
    return () => document.removeEventListener("fullscreenchange", handleFullscreenChange);
  }, []);

  // Update buffer progress indicator
  const updateBufferedProgress = () => {
    if (videoRef.current && videoRef.current.duration > 0) {
      const vid = videoRef.current;
      const curTime = vid.currentTime;
      for (let i = 0; i < vid.buffered.length; i++) {
        if (vid.buffered.start(i) <= curTime && curTime <= vid.buffered.end(i)) {
          setBufferedPercent((vid.buffered.end(i) / vid.duration) * 100);
          break;
        }
      }
    }
  };

  // Synchronize audio and video playback speed
  const applyPlaybackSpeed = (speed: number) => {
    setPlaybackSpeed(speed);
    if (videoRef.current) {
      videoRef.current.playbackRate = speed;
    }
    if (audioRef.current) {
      audioRef.current.playbackRate = speed;
    }
    setShowSpeedMenu(false);
  };

  // Seek helper (safely handles boundaries)
  const seekTo = (seconds: number) => {
    if (!videoRef.current) return;
    const dur = videoRef.current.duration;
    const safeTime = Math.max(0, dur > 0 ? Math.min(seconds, dur) : seconds);
    videoRef.current.currentTime = safeTime;
    if (activeLanguage !== "en" && audioRef.current) {
      audioRef.current.currentTime = safeTime;
    }
    setCurrentTime(safeTime);
    if (dur > 0) {
      setProgressPercent((safeTime / dur) * 100);
    }
  };

  const seekRelative = (deltaSeconds: number) => {
    if (!videoRef.current) return;
    seekTo(videoRef.current.currentTime + deltaSeconds);
  };

  // Attempt initial seek to saved position once duration is known
  const tryInitialSeek = (targetPos: number, currentDuration: number) => {
    if (
      !hasAppliedInitialSeekRef.current &&
      targetPos > 0 &&
      currentDuration > 0 &&
      targetPos < currentDuration &&
      videoRef.current
    ) {
      hasAppliedInitialSeekRef.current = true;
      videoRef.current.currentTime = targetPos;
      if (activeLanguage !== "en" && audioRef.current) {
        audioRef.current.currentTime = targetPos;
      }
      setCurrentTime(targetPos);
      setProgressPercent((targetPos / currentDuration) * 100);
    }
  };

  // Trigger initial seek if savedProgressPosition updates after metadata loaded
  useEffect(() => {
    if (savedProgressPosition > 0 && duration > 0 && !hasAppliedInitialSeekRef.current) {
      tryInitialSeek(savedProgressPosition, duration);
    }
  }, [savedProgressPosition, duration]);

  // Video event handlers
  const handleLoadedMetadata = () => {
    if (videoRef.current) {
      const dur = videoRef.current.duration;
      if (Number.isFinite(dur) && dur > 0) {
        setDuration(dur);
        if (savedProgressPosition > 0) {
          tryInitialSeek(savedProgressPosition, dur);
        }
      }
      // Apply configured speed to video
      videoRef.current.playbackRate = playbackSpeed;
    }
    setIsBuffering(false);
  };

  const handleDurationChange = () => {
    if (videoRef.current) {
      const dur = videoRef.current.duration;
      if (Number.isFinite(dur) && dur > 0) {
        setDuration(dur);
      }
    }
  };

  const handlePlay = () => {
    setIsPlaying(true);
    setIsBuffering(false);
    if (activeLanguage !== "en" && audioRef.current && videoRef.current) {
      if (Math.abs(audioRef.current.currentTime - videoRef.current.currentTime) > 0.3) {
        audioRef.current.currentTime = videoRef.current.currentTime;
      }
      audioRef.current.playbackRate = playbackSpeed;
      audioRef.current.play().catch(console.error);
    }
  };

  const handlePause = () => {
    setIsPlaying(false);
    if (audioRef.current) {
      audioRef.current.pause();
    }
    // Save progress immediately on pause
    if (videoRef.current && videoRef.current.duration > 0) {
      const cur = videoRef.current.currentTime;
      const dur = videoRef.current.duration;
      onSaveProgress?.(cur, dur, false);
      lastSavedTimeRef.current = cur;
    }
  };

  const handleTimeUpdate = () => {
    if (!videoRef.current) return;
    const cur = videoRef.current.currentTime;
    const dur = videoRef.current.duration;

    setCurrentTime(cur);
    if (dur > 0) {
      setProgressPercent((cur / dur) * 100);
    }
    updateBufferedProgress();

    // Synchronize translated audio drift if active (> 0.3s)
    if (activeLanguage !== "en" && audioRef.current) {
      if (Math.abs(audioRef.current.currentTime - cur) > 0.3) {
        audioRef.current.currentTime = cur;
      }
    }

    // Periodic save every ~10s
    if (dur > 0 && Math.abs(cur - lastSavedTimeRef.current) >= 10) {
      lastSavedTimeRef.current = cur;
      onSaveProgress?.(cur, dur, false);
    }
  };

  const handleSeeked = () => {
    if (!videoRef.current) return;
    const cur = videoRef.current.currentTime;
    if (activeLanguage !== "en" && audioRef.current) {
      audioRef.current.currentTime = cur;
    }
  };

  const handleVideoEnded = () => {
    setIsPlaying(false);
    if (videoRef.current && videoRef.current.duration > 0) {
      const dur = videoRef.current.duration;
      onSaveProgress?.(dur, dur, true);
    }
    onEnded?.();
  };

  const handleVideoError = () => {
    setIsBuffering(false);
    setHasError(true);
    setErrorMessage("Unable to play video. Please check your internet connection or try again.");
  };

  const handleAudioError = () => {
    console.warn("Alternate audio failed to load. Falling back to original audio.");
    // Fallback cleanly to English original
    changeLanguage("en");
  };

  const handleRetry = () => {
    setHasError(false);
    setErrorMessage(null);
    setIsBuffering(true);
    if (videoRef.current) {
      videoRef.current.load();
      videoRef.current.play().catch(() => {});
    }
  };

  // Volume & Mute
  const applyVolume = (vol: number, muted: boolean) => {
    if (activeLanguage === "en") {
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

  const handleVolumeChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newVol = parseFloat(e.target.value);
    setVolume(newVol);
    const newMuted = newVol === 0;
    setIsMuted(newMuted);
    applyVolume(newVol, newMuted);
  };

  const toggleMute = () => {
    const nextMuted = !isMuted;
    setIsMuted(nextMuted);
    applyVolume(nextMuted ? 0 : volume, nextMuted);
  };

  // Play / Pause toggle
  const togglePlay = () => {
    if (!videoRef.current) return;
    if (videoRef.current.paused) {
      videoRef.current.play().catch(console.error);
    } else {
      videoRef.current.pause();
    }
  };

  // Fullscreen toggle
  const toggleFullscreen = () => {
    if (!document.fullscreenElement) {
      containerRef.current?.requestFullscreen().catch(console.error);
    } else {
      document.exitFullscreen().catch(console.error);
    }
  };

  // Audio track switching
  const changeLanguage = (langCode: string) => {
    setActiveLanguage(langCode);
    setShowLanguageMenu(false);

    if (langCode === "en") {
      if (videoRef.current) {
        videoRef.current.muted = isMuted;
      }
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current.removeAttribute("src");
      }
    } else {
      const audioTrack = availableAudios.find(
        (a) => a.language_code.split("-")[0] === langCode
      );
      if (videoRef.current) {
        videoRef.current.muted = true; // Video audio muted when alternate audio plays
      }
      if (audioRef.current && audioTrack?.audio_file) {
        audioRef.current.src = audioTrack.audio_file;
        audioRef.current.load();
        audioRef.current.volume = volume;
        audioRef.current.muted = isMuted;
        audioRef.current.playbackRate = playbackSpeed;

        if (videoRef.current) {
          audioRef.current.currentTime = videoRef.current.currentTime;
          if (!videoRef.current.paused) {
            audioRef.current.play().catch((err) => console.error("Audio playback blocked:", err));
          }
        }
      }
    }
  };

  // Controls visibility timeout
  const handleMouseMove = () => {
    setShowControls(true);
    if (controlsTimeoutRef.current) clearTimeout(controlsTimeoutRef.current);
    controlsTimeoutRef.current = setTimeout(() => {
      if (isPlaying) {
        setShowControls(false);
        setShowLanguageMenu(false);
        setShowSpeedMenu(false);
      }
    }, 2800);
  };

  const handleMouseLeave = () => {
    if (isPlaying) {
      setShowControls(false);
      setShowLanguageMenu(false);
      setShowSpeedMenu(false);
    }
  };

  // Keyboard shortcuts (ignoring inputs, textareas, contentEditable)
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (target) {
        const tag = target.tagName?.toLowerCase();
        if (
          tag === "input" ||
          tag === "textarea" ||
          tag === "select" ||
          target.isContentEditable ||
          target.getAttribute("role") === "textbox"
        ) {
          return;
        }
      }

      switch (e.key) {
        case " ":
        case "k":
        case "K":
          e.preventDefault();
          togglePlay();
          break;
        case "ArrowLeft":
        case "j":
        case "J":
          e.preventDefault();
          seekRelative(-10);
          break;
        case "ArrowRight":
        case "l":
        case "L":
          e.preventDefault();
          seekRelative(10);
          break;
        case "m":
        case "M":
          e.preventDefault();
          toggleMute();
          break;
        case "f":
        case "F":
          e.preventDefault();
          toggleFullscreen();
          break;
        case "ArrowUp":
          e.preventDefault();
          setVolume((prev) => {
            const next = Math.min(1, Math.round((prev + 0.1) * 10) / 10);
            applyVolume(next, false);
            setIsMuted(false);
            return next;
          });
          break;
        case "ArrowDown":
          e.preventDefault();
          setVolume((prev) => {
            const next = Math.max(0, Math.round((prev - 0.1) * 10) / 10);
            applyVolume(next, next === 0);
            if (next === 0) setIsMuted(true);
            return next;
          });
          break;
        default:
          break;
      }
    };

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [isPlaying, isMuted, volume, activeLanguage, playbackSpeed]);

  // Flush progress on unmount
  useEffect(() => {
    return () => {
      if (videoRef.current && videoRef.current.duration > 0) {
        const cur = videoRef.current.currentTime;
        const dur = videoRef.current.duration;
        if (cur > 0) {
          onSaveProgress?.(cur, dur, false);
        }
      }
    };
  }, [lessonId]);

  return (
    <div
      ref={containerRef}
      id="web-video-player"
      className="w-full aspect-video bg-black relative group flex items-center justify-center overflow-hidden md:rounded-2xl md:ring-1 md:ring-white/10 md:shadow-[0_25px_70px_-20px_rgba(0,0,0,0.9)] select-none"
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      onDoubleClick={toggleFullscreen}
    >
      {/* HTML5 Video Element */}
      <video
        ref={videoRef}
        src={videoUrl}
        poster={poster}
        className="w-full h-full object-contain cursor-pointer"
        onPlay={handlePlay}
        onPause={handlePause}
        onTimeUpdate={handleTimeUpdate}
        onLoadedMetadata={handleLoadedMetadata}
        onDurationChange={handleDurationChange}
        onSeeked={handleSeeked}
        onEnded={handleVideoEnded}
        onError={handleVideoError}
        onWaiting={() => setIsBuffering(true)}
        onPlaying={() => {
          setIsBuffering(false);
          setHasError(false);
        }}
        onClick={togglePlay}
        playsInline
        autoPlay
      />

      {/* Hidden Audio Element for translated voice-over */}
      <audio ref={audioRef} onError={handleAudioError} className="hidden" />

      {/* Buffering Spinner */}
      {isBuffering && !hasError && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none z-20 bg-black/20 backdrop-blur-[2px]">
          <div className="w-14 h-14 border-4 border-white/20 border-t-[#facc15] rounded-full animate-spin shadow-[0_0_25px_rgba(250,204,21,0.4)]" />
        </div>
      )}

      {/* Playback Error & Retry State Overlay */}
      {hasError && (
        <div className="absolute inset-0 bg-black/90 backdrop-blur-md flex flex-col items-center justify-center text-center p-6 z-40">
          <div className="w-16 h-16 rounded-full bg-red-500/10 border border-red-500/20 flex items-center justify-center mb-4 text-red-400">
            <AlertCircle className="w-8 h-8" />
          </div>
          <h3 className="text-lg font-bold text-white mb-2">Playback Error</h3>
          <p className="text-sm text-zinc-400 max-w-sm mb-6 leading-relaxed">
            {errorMessage || "Unable to play this video. Please check your connection and retry."}
          </p>
          <button
            onClick={handleRetry}
            className="flex items-center gap-2 px-5 py-2.5 bg-[#facc15] text-black font-semibold text-sm rounded-full hover:bg-yellow-400 active:scale-95 transition-all shadow-[0_0_20px_rgba(250,204,21,0.3)]"
          >
            <RefreshCw className="w-4 h-4" />
            <span>Retry Playback</span>
          </button>
        </div>
      )}

      {/* Big Play Button Overlay (when paused & no error) */}
      <AnimatePresence>
        {!isPlaying && !hasError && !isBuffering && (
          <motion.button
            initial={{ opacity: 0, scale: 0.8 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.8 }}
            onClick={togglePlay}
            aria-label="Play video"
            className="absolute inset-0 m-auto w-20 h-20 bg-[#facc15]/90 hover:bg-[#facc15] text-black rounded-full flex items-center justify-center transition-transform hover:scale-110 shadow-[0_0_40px_rgba(250,204,21,0.3)] z-20"
          >
            <Play className="w-8 h-8 ml-1 fill-black" />
          </motion.button>
        )}
      </AnimatePresence>

      {/* Controls Overlay Bar */}
      <div
        className={`absolute bottom-0 left-0 right-0 px-4 sm:px-6 pt-20 pb-4 sm:pb-6 bg-gradient-to-t from-black/95 via-black/60 to-transparent transition-opacity duration-300 z-30 flex flex-col gap-2.5 ${
          showControls || !isPlaying || showLanguageMenu || showSpeedMenu ? "opacity-100" : "opacity-0 pointer-events-none"
        }`}
      >
        {/* Seek Bar with buffer & thumb */}
        <div className="relative w-full h-1.5 hover:h-2.5 bg-white/20 rounded-full group/progress cursor-pointer flex items-center transition-all">
          {/* Buffered Progress */}
          <div
            className="absolute top-0 left-0 h-full bg-white/30 rounded-full transition-all duration-200"
            style={{ width: `${bufferedPercent}%` }}
          />

          {/* Played Progress */}
          <div
            className="absolute top-0 left-0 h-full bg-[#facc15] rounded-full shadow-[0_0_12px_rgba(250,204,21,0.6)]"
            style={{ width: `${progressPercent}%` }}
          />

          {/* Interactive Range Input */}
          <input
            type="range"
            min="0"
            max="100"
            step="0.1"
            value={progressPercent}
            onChange={(e) => {
              const val = parseFloat(e.target.value);
              setProgressPercent(val);
              if (videoRef.current && duration > 0) {
                seekTo((val / 100) * duration);
              }
            }}
            aria-label="Seek video position"
            className="absolute top-0 left-0 w-full h-full opacity-0 cursor-pointer"
          />

          {/* Thumb indicator on hover */}
          <div
            className="absolute h-3.5 w-3.5 bg-[#facc15] rounded-full shadow-lg ring-2 ring-black/40 opacity-0 group-hover/progress:opacity-100 transition-opacity pointer-events-none"
            style={{ left: `calc(${progressPercent}% - 7px)` }}
          />
        </div>

        {/* Bottom Controls Row */}
        <div className="flex items-center justify-between mt-1 text-white">
          {/* Left Controls: Play/Pause, -10s, +10s, Volume, Time */}
          <div className="flex items-center gap-1 sm:gap-2">
            <button
              onClick={togglePlay}
              aria-label={isPlaying ? "Pause" : "Play"}
              className="p-2 rounded-full hover:bg-white/10 hover:text-[#facc15] transition-colors"
            >
              {isPlaying ? <Pause className="w-5 h-5 fill-current" /> : <Play className="w-5 h-5 fill-current" />}
            </button>

            {/* 10s Rewind */}
            <button
              onClick={() => seekRelative(-10)}
              aria-label="Seek backward 10 seconds"
              title="Rewind 10s (Left Arrow)"
              className="p-2 rounded-full hover:bg-white/10 hover:text-[#facc15] transition-colors relative flex items-center justify-center"
            >
              <RotateCcw className="w-4 h-4" />
              <span className="text-[9px] font-mono font-bold absolute bottom-0.5 right-0.5">10</span>
            </button>

            {/* 10s Forward */}
            <button
              onClick={() => seekRelative(10)}
              aria-label="Seek forward 10 seconds"
              title="Forward 10s (Right Arrow)"
              className="p-2 rounded-full hover:bg-white/10 hover:text-[#facc15] transition-colors relative flex items-center justify-center"
            >
              <RotateCw className="w-4 h-4" />
              <span className="text-[9px] font-mono font-bold absolute bottom-0.5 right-0.5">10</span>
            </button>

            {/* Volume & Slider */}
            <div className="flex items-center gap-1 group/volume pl-1">
              <button
                onClick={toggleMute}
                aria-label={isMuted || volume === 0 ? "Unmute" : "Mute"}
                className="p-2 rounded-full hover:bg-white/10 hover:text-[#facc15] transition-colors"
              >
                {isMuted || volume === 0 ? <VolumeX className="w-5 h-5" /> : <Volume2 className="w-5 h-5" />}
              </button>
              <input
                type="range"
                min="0"
                max="1"
                step="0.05"
                value={isMuted ? 0 : volume}
                onChange={handleVolumeChange}
                aria-label="Volume"
                className="w-0 group-hover/volume:w-16 sm:group-hover/volume:w-20 opacity-0 group-hover/volume:opacity-100 transition-all duration-300 accent-[#facc15] h-1 bg-white/20 rounded-full appearance-none outline-none cursor-pointer"
              />
            </div>

            {/* Time Display */}
            <div className="text-xs sm:text-sm font-medium text-white/90 font-mono tracking-wider pl-1 sm:pl-2">
              <span>{formatTime(currentTime)}</span>
              <span className="text-white/40 mx-1">/</span>
              <span>{formatTime(duration)}</span>
            </div>
          </div>

          {/* Right Controls: Playback Speed, Audio Tracks, Fullscreen */}
          <div className="flex items-center gap-1.5 sm:gap-2">
            {/* Playback Speed Menu */}
            <div className="relative">
              <button
                onClick={() => {
                  setShowSpeedMenu(!showSpeedMenu);
                  setShowLanguageMenu(false);
                }}
                aria-label="Playback speed menu"
                className={`flex items-center gap-1 text-xs font-semibold px-2.5 py-1.5 rounded-full border transition-all ${
                  showSpeedMenu || playbackSpeed !== 1
                    ? "bg-[#facc15]/20 border-[#facc15]/40 text-[#facc15]"
                    : "bg-white/5 border-white/10 text-white/90 hover:bg-white/10 hover:text-white"
                }`}
              >
                <Gauge className="w-3.5 h-3.5" />
                <span>{playbackSpeed}x</span>
              </button>

              <AnimatePresence>
                {showSpeedMenu && (
                  <motion.div
                    initial={{ opacity: 0, y: 8, scale: 0.95 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: 8, scale: 0.95 }}
                    transition={{ duration: 0.12 }}
                    className="absolute bottom-full right-0 mb-3 w-36 bg-[#18181b]/95 backdrop-blur-xl border border-white/10 rounded-2xl overflow-hidden shadow-[0_10px_40px_rgba(0,0,0,0.6)] z-50 p-1.5"
                  >
                    <div className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest px-2.5 py-1.5">
                      Speed
                    </div>
                    {SPEED_OPTIONS.map((spd) => (
                      <button
                        key={spd}
                        onClick={() => applyPlaybackSpeed(spd)}
                        className={`w-full text-left px-2.5 py-1.5 text-xs rounded-lg flex items-center justify-between transition-colors ${
                          playbackSpeed === spd
                            ? "bg-[#facc15]/15 text-[#facc15] font-semibold"
                            : "hover:bg-white/5 text-zinc-300"
                        }`}
                      >
                        <span>{spd}x</span>
                        {playbackSpeed === spd && <Check className="w-3.5 h-3.5 text-[#facc15]" />}
                      </button>
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Audio Track Menu (if available) */}
            <div className="relative">
              <button
                onClick={() => {
                  setShowLanguageMenu(!showLanguageMenu);
                  setShowSpeedMenu(false);
                }}
                aria-label="Audio language menu"
                className={`flex items-center gap-1.5 text-xs font-medium px-2.5 sm:px-3 py-1.5 rounded-full border transition-all ${
                  showLanguageMenu || activeLanguage !== "en"
                    ? "bg-white/15 border-white/20 text-white"
                    : "bg-white/5 border-white/10 text-white/90 hover:bg-white/10 hover:text-white"
                }`}
              >
                <Settings className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
                <span className="max-w-[70px] sm:max-w-[100px] truncate">
                  {activeLanguage === "en"
                    ? "English"
                    : getLanguageDisplayName(
                        availableAudios.find((a) => a.language_code.split("-")[0] === activeLanguage) || {
                          language_code: activeLanguage,
                        }
                      )}
                </span>
              </button>

              <AnimatePresence>
                {showLanguageMenu && (
                  <motion.div
                    initial={{ opacity: 0, y: 8, scale: 0.95 }}
                    animate={{ opacity: 1, y: 0, scale: 1 }}
                    exit={{ opacity: 0, y: 8, scale: 0.95 }}
                    transition={{ duration: 0.12 }}
                    className="absolute bottom-full right-0 mb-3 w-52 bg-[#18181b]/95 backdrop-blur-xl border border-white/10 rounded-2xl overflow-hidden shadow-[0_10px_40px_rgba(0,0,0,0.6)] z-50 p-2"
                  >
                    <div className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest px-3 py-1.5">
                      Audio Tracks
                    </div>

                    {/* Original English */}
                    <button
                      onClick={() => changeLanguage("en")}
                      className={`w-full text-left px-3 py-2 text-xs sm:text-sm rounded-xl mb-1 flex justify-between items-center transition-colors ${
                        activeLanguage === "en"
                          ? "bg-white/10 text-white font-medium"
                          : "hover:bg-white/5 text-zinc-300"
                      }`}
                    >
                      <span>English (Original)</span>
                      {activeLanguage === "en" && <Check className="w-4 h-4 text-[#facc15]" />}
                    </button>

                    {/* Translated Tracks */}
                    {availableAudios.map((audio) => {
                      const langCode = audio.language_code.split("-")[0];
                      const langName = getLanguageDisplayName(audio);
                      const isActive = activeLanguage === langCode;

                      return (
                        <button
                          key={audio.id}
                          onClick={() => changeLanguage(langCode)}
                          className={`w-full text-left px-3 py-2 text-xs sm:text-sm rounded-xl mb-1 flex justify-between items-center transition-colors ${
                            isActive
                              ? "bg-white/10 text-white font-medium"
                              : "hover:bg-white/5 text-zinc-300"
                          }`}
                        >
                          <span>{langName}</span>
                          {isActive && <Check className="w-4 h-4 text-[#facc15]" />}
                        </button>
                      );
                    })}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>

            {/* Fullscreen Button */}
            <button
              onClick={toggleFullscreen}
              aria-label={isFullscreen ? "Exit Fullscreen" : "Enter Fullscreen"}
              className="p-2 rounded-full hover:bg-white/10 hover:text-[#facc15] transition-colors"
            >
              {isFullscreen ? <Minimize className="w-5 h-5" /> : <Maximize className="w-5 h-5" />}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
