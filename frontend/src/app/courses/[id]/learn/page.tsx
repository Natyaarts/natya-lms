"use client";

import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { 
  Play, Pause, Volume2, VolumeX, Maximize, Minimize, 
  Settings, Check, ChevronLeft 
} from "lucide-react";

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
            for (let module of data.modules) {
              if (module.lessons && module.lessons.length > 0) {
                setActiveLesson(module.lessons[0]);
                break;
              }
            }
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
    }
  };

  const handleLoadedMetadata = () => {
    if (videoRef.current) {
      setDurationStr(formatTime(videoRef.current.duration));
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

  // Reset language to English when changing lessons
  useEffect(() => {
    setActiveLanguage('en');
  }, [activeLesson?.id]);


  if (loading) return <div className="min-h-screen bg-black flex items-center justify-center text-[#facc15]">Loading Player...</div>;
  if (!course) return <div className="min-h-screen bg-black text-white p-8">Course not found.</div>;

  return (
    <div className="min-h-screen bg-[#050505] text-white flex flex-col md:flex-row h-screen overflow-hidden">
      
      {/* Sidebar: Curriculum */}
      <div className="w-full md:w-80 bg-zinc-950 border-r border-white/5 flex flex-col h-1/3 md:h-full overflow-hidden shrink-0">
        <div className="p-5 border-b border-white/5 shrink-0 bg-zinc-900/50">
          <Link href="/dashboard" className="text-zinc-400 hover:text-white text-sm flex items-center gap-2 mb-4 font-medium transition-colors">
            <ChevronLeft className="w-4 h-4" /> Back to Dashboard
          </Link>
          <h2 className="font-bold text-lg leading-tight tracking-tight">{course.title}</h2>
        </div>
        
        <div className="overflow-y-auto flex-1 p-3">
          {course.modules?.map((module: any, idx: number) => (
            <div key={module.id} className="mb-6">
              <h3 className="text-xs font-bold text-zinc-500 px-3 mb-3 uppercase tracking-widest">
                Module {idx + 1}: {module.title}
              </h3>
              <div className="space-y-1">
                {module.lessons?.map((lesson: any, lIdx: number) => (
                  <button
                    key={lesson.id}
                    onClick={() => setActiveLesson(lesson)}
                    className={`w-full text-left px-4 py-3 rounded-xl text-sm flex gap-3 transition-all ${
                      activeLesson?.id === lesson.id 
                        ? 'bg-gradient-to-r from-[#facc15]/20 to-[#facc15]/5 text-[#facc15] font-semibold shadow-inner' 
                        : 'text-zinc-300 hover:bg-white/5'
                    }`}
                  >
                    <Play className={`w-4 h-4 shrink-0 mt-0.5 ${activeLesson?.id === lesson.id ? 'fill-[#facc15] text-[#facc15]' : ''}`} />
                    <span className="leading-relaxed">{lIdx + 1}. {lesson.title}</span>
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Main Content: Video Player */}
      <div className="flex-1 flex flex-col h-2/3 md:h-full relative bg-black">
        {activeLesson ? (
          <div className="w-full h-full flex flex-col relative">
            
            {/* Custom Video Player Container */}
            <div 
              ref={containerRef}
              className="w-full aspect-video bg-black relative group flex items-center justify-center overflow-hidden"
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
                <div className="relative w-full h-1.5 bg-white/20 rounded-full group/progress cursor-pointer flex items-center">
                  <div 
                    className="absolute top-0 left-0 h-full bg-[#facc15] rounded-full" 
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
                    className="absolute h-4 w-4 bg-[#facc15] rounded-full shadow-lg opacity-0 group-hover/progress:opacity-100 transition-opacity"
                    style={{ left: `calc(${progress}% - 8px)` }}
                  />
                </div>

                {/* Bottom Controls Row */}
                <div className="flex items-center justify-between mt-1">
                  
                  {/* Left: Play/Pause, Volume, Time */}
                  <div className="flex items-center gap-6">
                    <button onClick={togglePlay} className="text-white hover:text-[#facc15] transition-colors">
                      {isPlaying ? <Pause className="w-6 h-6 fill-current" /> : <Play className="w-6 h-6 fill-current" />}
                    </button>
                    
                    <div className="flex items-center gap-3 group/volume">
                      <button onClick={toggleMute} className="text-white hover:text-[#facc15] transition-colors">
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

                    <div className="text-sm font-medium text-white/90 font-mono tracking-wider">
                      {currentTimeStr} <span className="text-white/40 mx-1">/</span> {durationStr}
                    </div>
                  </div>

                  {/* Right: Audio Menu & Fullscreen */}
                  <div className="flex items-center gap-5">
                    
                    {/* Netflix Style Audio Menu */}
                    <div className="relative">
                      <button 
                        onClick={() => setShowLanguageMenu(!showLanguageMenu)}
                        className="flex items-center gap-2 text-white/90 hover:text-white transition-colors"
                      >
                        <Settings className="w-5 h-5" />
                        <span className="text-sm font-medium">
                          {activeLanguage === 'en' ? 'English' : activeLanguage === 'hi' ? 'Hindi' : activeLanguage === 'ta' ? 'Tamil' : activeLanguage === 'ml' ? 'Malayalam' : activeLanguage}
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

                              {activeLesson.translated_audios?.filter((a:any) => a.status === 'completed').map((audio: any) => {
                                const langMap:any = {'hi-IN': 'Hindi', 'ta-IN': 'Tamil', 'ml-IN': 'Malayalam', 'hi': 'Hindi', 'ta': 'Tamil', 'ml': 'Malayalam'};
                                const langName = langMap[audio.language_code] || audio.language_code;
                                const isActive = activeLanguage === audio.language_code.split('-')[0];
                                
                                return (
                                  <button 
                                    key={audio.id}
                                    onClick={() => changeLanguage(audio.language_code.split('-')[0])}
                                    className={`w-full text-left px-3 py-2.5 text-sm rounded-xl mb-1 flex justify-between items-center transition-colors ${isActive ? 'bg-white/10 text-white font-medium' : 'hover:bg-white/5 text-zinc-300'}`}
                                  >
                                    {langName} (Dub)
                                    {isActive && <Check className="w-4 h-4 text-[#facc15]" />}
                                  </button>
                                )
                              })}
                            </div>
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>

                    <button onClick={toggleFullscreen} className="text-white hover:text-[#facc15] transition-colors">
                      {isFullscreen ? <Minimize className="w-5 h-5" /> : <Maximize className="w-5 h-5" />}
                    </button>
                  </div>

                </div>
              </div>

            </div>

            {/* Lesson Details */}
            <div className="p-8 max-w-4xl overflow-y-auto">
              <h1 className="text-3xl font-bold mb-4 tracking-tight">{activeLesson.title}</h1>
              <p className="text-zinc-400 leading-relaxed whitespace-pre-line text-lg">{activeLesson.description}</p>
            </div>
          </div>
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
