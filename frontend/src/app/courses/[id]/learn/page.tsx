"use client";

import { useEffect, useState, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";

export default function CourseLearnPage() {
  const { id } = useParams();
  const [course, setCourse] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [activeLesson, setActiveLesson] = useState<any>(null);
  
  // Video & Audio Sync State
  const videoRef = useRef<HTMLVideoElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  
  const [activeLanguage, setActiveLanguage] = useState<string>('en'); // 'en', 'hi', 'ta', 'ml'
  const [showLanguageMenu, setShowLanguageMenu] = useState(false);

  useEffect(() => {
    const fetchCourse = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${id}/`, {
          credentials: "include"
        });
        if (res.ok) {
          const data = await res.json();
          setCourse(data);
          
          // Select first lesson by default
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

  // Sync Audio to Video
  const handlePlay = () => {
    if (activeLanguage !== 'en' && audioRef.current) {
      audioRef.current.play();
    }
  };

  const handlePause = () => {
    if (audioRef.current) {
      audioRef.current.pause();
    }
  };

  const handleSeek = () => {
    if (videoRef.current && audioRef.current) {
      audioRef.current.currentTime = videoRef.current.currentTime;
    }
  };

  const handleWaiting = () => {
    if (audioRef.current) audioRef.current.pause();
  };

  const handlePlaying = () => {
    if (activeLanguage !== 'en' && audioRef.current) {
      audioRef.current.play();
    }
  };

  // Change Language
  const changeLanguage = (langCode: string) => {
    setActiveLanguage(langCode);
    setShowLanguageMenu(false);
    
    // Pause briefly to switch tracks
    if (videoRef.current) {
      const wasPlaying = !videoRef.current.paused;
      
      if (langCode === 'en') {
        videoRef.current.muted = false;
        if (audioRef.current) audioRef.current.pause();
      } else {
        videoRef.current.muted = true;
        // Wait for the new audio source to load, then sync and play
        setTimeout(() => {
            if (audioRef.current && videoRef.current) {
                audioRef.current.currentTime = videoRef.current.currentTime;
                if (wasPlaying) {
                    audioRef.current.play().catch(e => console.log("Audio play blocked", e));
                    videoRef.current.play();
                }
            }
        }, 100);
      }
    }
  };

  // Get active audio URL based on language
  const getActiveAudioUrl = () => {
    if (!activeLesson || activeLanguage === 'en') return null;
    
    const audioTrack = activeLesson.translated_audios?.find((a: any) => a.language_code.startsWith(activeLanguage) && a.status === 'completed');
    return audioTrack ? audioTrack.audio_file : null;
  };

  const currentAudioUrl = getActiveAudioUrl();

  // Make sure video is muted if we are playing a dubbed track
  useEffect(() => {
    if (videoRef.current) {
      videoRef.current.muted = activeLanguage !== 'en';
    }
  }, [activeLanguage]);

  // Reset language to English when changing lessons
  useEffect(() => {
    setActiveLanguage('en');
  }, [activeLesson?.id]);


  if (loading) return <div className="min-h-screen bg-black flex items-center justify-center text-[#facc15]">Loading Player...</div>;
  if (!course) return <div className="min-h-screen bg-black text-white p-8">Course not found.</div>;

  return (
    <div className="min-h-screen bg-black text-white flex flex-col md:flex-row h-screen overflow-hidden">
      
      {/* Sidebar: Curriculum */}
      <div className="w-full md:w-80 bg-zinc-950 border-r border-white/10 flex flex-col h-1/3 md:h-full overflow-hidden">
        <div className="p-4 border-b border-white/10 shrink-0">
          <Link href="/dashboard" className="text-zinc-400 hover:text-white text-sm flex items-center gap-2 mb-4">
            ← Back to Dashboard
          </Link>
          <h2 className="font-bold text-lg leading-tight">{course.title}</h2>
        </div>
        
        <div className="overflow-y-auto flex-1 p-2">
          {course.modules?.map((module: any, idx: number) => (
            <div key={module.id} className="mb-4">
              <h3 className="text-sm font-semibold text-zinc-500 px-2 mb-2 uppercase tracking-wider">
                Module {idx + 1}: {module.title}
              </h3>
              <div className="space-y-1">
                {module.lessons?.map((lesson: any, lIdx: number) => (
                  <button
                    key={lesson.id}
                    onClick={() => setActiveLesson(lesson)}
                    className={`w-full text-left px-3 py-2 rounded-lg text-sm flex gap-3 transition-colors ${
                      activeLesson?.id === lesson.id 
                        ? 'bg-[#facc15]/10 text-[#facc15] font-medium border border-[#facc15]/20' 
                        : 'text-zinc-300 hover:bg-white/5'
                    }`}
                  >
                    <svg className="w-4 h-4 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <polygon points="5 3 19 12 5 21 5 3" />
                    </svg>
                    {lIdx + 1}. {lesson.title}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Main Content: Video Player */}
      <div className="flex-1 flex flex-col h-2/3 md:h-full relative bg-[#050505]">
        {activeLesson ? (
          <div className="w-full h-full flex flex-col">
            {/* The Custom Player Wrapper */}
            <div className="w-full aspect-video bg-black relative group">
              
              <video 
                ref={videoRef}
                src={activeLesson.video_file}
                className="w-full h-full object-contain"
                controls
                controlsList="nodownload"
                onPlay={handlePlay}
                onPause={handlePause}
                onSeeked={handleSeek}
                onWaiting={handleWaiting}
                onPlaying={handlePlaying}
                autoPlay
              />

              {/* The Hidden Audio Player for Dubs */}
              {currentAudioUrl && (
                <audio 
                  ref={audioRef}
                  src={currentAudioUrl}
                  className="hidden"
                />
              )}

              {/* Netflix-style Language Selector Overlay */}
              <div className="absolute top-4 right-4 z-10">
                <div className="relative">
                  <button 
                    onClick={() => setShowLanguageMenu(!showLanguageMenu)}
                    className="bg-black/60 backdrop-blur-md hover:bg-black/80 text-white border border-white/20 px-4 py-2 rounded-lg font-medium text-sm flex items-center gap-2 transition-all opacity-0 group-hover:opacity-100"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"></path>
                    </svg>
                    Audio: {activeLanguage === 'en' ? 'English' : activeLanguage === 'hi' ? 'Hindi' : activeLanguage === 'ta' ? 'Tamil' : activeLanguage === 'ml' ? 'Malayalam' : activeLanguage}
                  </button>

                  <AnimatePresence>
                    {showLanguageMenu && (
                      <motion.div 
                        initial={{ opacity: 0, y: -10 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -10 }}
                        className="absolute top-full right-0 mt-2 w-48 bg-zinc-900/90 backdrop-blur-xl border border-white/10 rounded-xl overflow-hidden shadow-2xl"
                      >
                        <div className="p-2">
                          <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider px-3 py-2">Audio Tracks</div>
                          
                          <button 
                            onClick={() => changeLanguage('en')}
                            className={`w-full text-left px-3 py-2 text-sm rounded-md mb-1 flex justify-between items-center ${activeLanguage === 'en' ? 'bg-[#facc15]/20 text-[#facc15]' : 'hover:bg-white/10 text-white'}`}
                          >
                            English (Original)
                            {activeLanguage === 'en' && <span className="text-[#facc15]">✓</span>}
                          </button>

                          {activeLesson.translated_audios?.filter((a:any) => a.status === 'completed').map((audio: any) => {
                            const langMap:any = {'hi-IN': 'Hindi', 'ta-IN': 'Tamil', 'ml-IN': 'Malayalam', 'hi': 'Hindi', 'ta': 'Tamil', 'ml': 'Malayalam'};
                            const langName = langMap[audio.language_code] || audio.language_code;
                            const isActive = activeLanguage === audio.language_code.split('-')[0];
                            
                            return (
                              <button 
                                key={audio.id}
                                onClick={() => changeLanguage(audio.language_code.split('-')[0])}
                                className={`w-full text-left px-3 py-2 text-sm rounded-md mb-1 flex justify-between items-center ${isActive ? 'bg-[#facc15]/20 text-[#facc15]' : 'hover:bg-white/10 text-white'}`}
                              >
                                {langName} (AI Dub)
                                {isActive && <span className="text-[#facc15]">✓</span>}
                              </button>
                            )
                          })}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
              </div>

            </div>

            {/* Lesson Details */}
            <div className="p-8 max-w-4xl overflow-y-auto">
              <h1 className="text-3xl font-bold mb-4">{activeLesson.title}</h1>
              <p className="text-zinc-400 leading-relaxed whitespace-pre-line">{activeLesson.description}</p>
            </div>
          </div>
        ) : (
          <div className="w-full h-full flex flex-col items-center justify-center text-zinc-500">
            <svg className="w-16 h-16 mb-4 opacity-20" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" />
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            <p>Select a lesson to start learning.</p>
          </div>
        )}
      </div>

    </div>
  );
}
