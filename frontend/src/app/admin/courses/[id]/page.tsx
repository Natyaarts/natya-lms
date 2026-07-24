"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";

export default function CourseManager() {
  const { id } = useParams();
  const [course, setCourse] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  // State for adding module
  const [showAddModule, setShowAddModule] = useState(false);
  const [moduleTitle, setModuleTitle] = useState("");
  const [moduleLoading, setModuleLoading] = useState(false);

  // State for adding lesson
  const [addingLessonToModule, setAddingLessonToModule] = useState<number | null>(null);
  const [lessonData, setLessonData] = useState({
    title: "",
    description: "",
    video_url: "",
    audio_hi_url: "",
    audio_ta_url: "",
    audio_ml_url: ""
  });
  const [lessonLoading, setLessonLoading] = useState(false);

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

  const fetchCourse = async () => {
    try {
      const res = await fetch(`http://localhost:8000/api/courses/${id}/`, {
        credentials: "include"
      });
      if (res.ok) {
        const data = await res.json();
        setCourse(data);
      } else {
        setError("Failed to fetch course details");
      }
    } catch (err) {
      setError("Network error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (id) fetchCourse();
  }, [id]);

  const handleAddModule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!moduleTitle.trim()) return;
    
    setModuleLoading(true);
    try {
      const res = await fetch("http://localhost:8000/api/courses/modules/", {
        method: "POST",
        headers: { 
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({
          title: moduleTitle,
          course: id,
          order: course?.modules?.length || 0
        }),
        credentials: "include"
      });

      if (res.ok) {
        setModuleTitle("");
        setShowAddModule(false);
        fetchCourse(); // refresh
      } else {
        const errData = await res.text();
        console.error("Module creation failed:", errData);
        alert("Failed to create module: " + errData);
      }
    } catch (err) {
      console.error(err);
      alert("Network error creating module");
    } finally {
      setModuleLoading(false);
    }
  };

  const handleAddLesson = async (e: React.FormEvent, moduleId: number) => {
    e.preventDefault();
    if (!lessonData.title.trim() || !lessonData.video_url.trim()) return;

    setLessonLoading(true);
    try {
      const moduleObj = course.modules.find((m: any) => m.id === moduleId);
      
      const res = await fetch("http://localhost:8000/api/courses/lessons/", {
        method: "POST",
        headers: { 
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({
          ...lessonData,
          module: moduleId,
          order: moduleObj?.lessons?.length || 0
        }),
        credentials: "include"
      });

      if (res.ok) {
        setAddingLessonToModule(null);
        setLessonData({
          title: "",
          description: "",
          video_url: "",
          audio_hi_url: "",
          audio_ta_url: "",
          audio_ml_url: ""
        });
        fetchCourse(); // refresh
      } else {
        const errData = await res.text();
        console.error("Lesson creation failed:", errData);
        alert("Failed to create lesson: " + errData);
      }
    } catch (err) {
      console.error(err);
      alert("Network error creating lesson");
    } finally {
      setLessonLoading(false);
    }
  };

  const handleThumbnailUpload = async (e: any) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const data = new FormData();
      data.append('thumbnail', file);

      const res = await fetch(`http://localhost:8000/api/courses/${id}/`, {
        method: "PATCH",
        headers: {
          "X-CSRFToken": getCsrfToken()
        },
        body: data,
        credentials: "include"
      });

      if (res.ok) {
        fetchCourse(); // refresh
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleTogglePublish = async () => {
    try {
      const res = await fetch(`http://localhost:8000/api/courses/${id}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ is_published: !course.is_published }),
        credentials: "include"
      });

      if (res.ok) {
        fetchCourse(); // refresh
      } else {
        alert("Failed to update course status.");
      }
    } catch (err) {
      console.error(err);
      alert("Network error.");
    }
  };

  if (loading) return <div className="text-zinc-500 p-8">Loading course details...</div>;
  if (!course) return <div className="text-red-500 p-8">Course not found</div>;

  return (
    <div className="max-w-4xl mx-auto pb-20">
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-4">
          <Link href="/admin/courses" className="w-10 h-10 bg-zinc-900 rounded-full flex items-center justify-center hover:bg-zinc-800 transition-colors">
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="19" y1="12" x2="5" y2="12"></line>
              <polyline points="12 19 5 12 12 5"></polyline>
            </svg>
          </Link>
          <h1 className="text-3xl font-bold">Course Manager</h1>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
        {/* Left Column: Course Details */}
        <div className="md:col-span-1 space-y-6">
          <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6 sticky top-6">
            <div className="aspect-video bg-black rounded-lg mb-4 overflow-hidden flex items-center justify-center border border-white/5 relative group">
              {course.thumbnail ? (
                <img src={course.thumbnail} alt={course.title} className="w-full h-full object-cover group-hover:opacity-50 transition-opacity" />
              ) : (
                <span className="text-zinc-600 text-sm group-hover:opacity-50 transition-opacity">No Thumbnail</span>
              )}
              
              <label className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity cursor-pointer">
                <input type="file" accept="image/*" className="hidden" onChange={handleThumbnailUpload} />
                <div className="bg-black/60 backdrop-blur-md px-4 py-2 rounded-full text-white text-sm font-medium border border-white/20 flex items-center gap-2">
                  <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                    <polyline points="17 8 12 3 7 8"></polyline>
                    <line x1="12" y1="3" x2="12" y2="15"></line>
                  </svg>
                  Upload
                </div>
              </label>
            </div>
            <h2 className="text-xl font-bold mb-2">{course.title}</h2>
            <p className="text-zinc-400 text-sm mb-4 line-clamp-3">{course.description}</p>
            <div className="flex items-center justify-between py-3 border-t border-white/10">
              <span className="text-zinc-500 text-sm">Price</span>
              <span className="font-semibold text-white">₹{parseFloat(course.price).toLocaleString()}</span>
            </div>
            <div className="flex items-center justify-between py-3 border-t border-white/10">
              <span className="text-zinc-500 text-sm">Status</span>
              <div className="flex items-center gap-3">
                <span className={`px-2 py-1 rounded-md text-xs font-medium ${course.is_published ? 'bg-green-500/20 text-green-400' : 'bg-zinc-800 text-zinc-400'}`}>
                  {course.is_published ? 'Published' : 'Draft'}
                </span>
                <button 
                  onClick={handleTogglePublish}
                  className="text-xs font-medium hover:text-white text-zinc-400 underline underline-offset-2"
                >
                  {course.is_published ? 'Unpublish' : 'Publish'}
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Right Column: Curriculum Builder */}
        <div className="md:col-span-2">
          <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6">
            <div className="flex items-center justify-between mb-8">
              <h2 className="text-xl font-bold">Curriculum Builder</h2>
              <button 
                onClick={() => setShowAddModule(true)}
                className="px-4 py-2 bg-[#facc15]/10 text-[#facc15] font-medium rounded-lg hover:bg-[#facc15]/20 transition-colors text-sm"
              >
                + Add Module
              </button>
            </div>

            {/* Add Module Inline Form */}
            <AnimatePresence>
              {showAddModule && (
                <motion.form 
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  onSubmit={handleAddModule}
                  className="bg-black border border-white/10 rounded-xl p-4 mb-6 overflow-hidden"
                >
                  <label className="block text-sm font-medium text-zinc-400 mb-2">Module Title</label>
                  <div className="flex gap-3">
                    <input 
                      type="text"
                      autoFocus
                      required
                      placeholder="e.g. Week 1: Introduction to Next.js"
                      value={moduleTitle}
                      onChange={(e) => setModuleTitle(e.target.value)}
                      className="flex-1 px-4 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white focus:outline-none focus:border-[#facc15] transition-colors"
                    />
                    <button 
                      type="button" 
                      onClick={() => setShowAddModule(false)}
                      className="px-4 py-2 text-zinc-400 hover:text-white"
                    >
                      Cancel
                    </button>
                    <button 
                      type="submit"
                      disabled={moduleLoading}
                      className="px-6 py-2 bg-[#facc15] text-black font-semibold rounded-lg hover:bg-[#eab308] transition-colors disabled:opacity-50"
                    >
                      Save
                    </button>
                  </div>
                </motion.form>
              )}
            </AnimatePresence>

            {/* Modules List */}
            <div className="space-y-6">
              {course.modules?.length === 0 && !showAddModule ? (
                <div className="text-center py-12 text-zinc-500 border border-dashed border-white/10 rounded-xl">
                  No modules yet. Click "Add Module" to start building your course.
                </div>
              ) : (
                course.modules?.map((module: any, idx: number) => (
                  <div key={module.id} className="border border-white/10 rounded-xl overflow-hidden">
                    <div className="bg-black/50 px-5 py-4 border-b border-white/10 flex items-center justify-between">
                      <h3 className="font-semibold flex items-center gap-3">
                        <span className="w-6 h-6 bg-white/10 text-xs flex items-center justify-center rounded-full text-zinc-400">
                          {idx + 1}
                        </span>
                        {module.title}
                      </h3>
                      <button 
                        onClick={() => setAddingLessonToModule(addingLessonToModule === module.id ? null : module.id)}
                        className="text-sm font-medium text-zinc-400 hover:text-white transition-colors"
                      >
                        + Add Video
                      </button>
                    </div>

                    <div className="p-4 space-y-3">
                      {module.lessons?.map((lesson: any, lIdx: number) => (
                        <div key={lesson.id} className="bg-zinc-900/50 border border-white/5 p-4 rounded-lg flex items-start gap-4 hover:border-white/10 transition-colors">
                          <div className="mt-1">
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-[#facc15]">
                              <polygon points="5 3 19 12 5 21 5 3"></polygon>
                            </svg>
                          </div>
                          <div>
                            <h4 className="font-medium text-sm">{lIdx + 1}. {lesson.title}</h4>
                            <div className="text-xs text-zinc-500 mt-1 line-clamp-1">{lesson.video_url}</div>
                            {(lesson.audio_hi_url || lesson.audio_ta_url || lesson.audio_ml_url) && (
                              <div className="flex gap-2 mt-2">
                                {lesson.audio_hi_url && <span className="px-2 py-0.5 bg-zinc-800 rounded text-[10px] font-medium text-zinc-400">Hindi</span>}
                                {lesson.audio_ta_url && <span className="px-2 py-0.5 bg-zinc-800 rounded text-[10px] font-medium text-zinc-400">Tamil</span>}
                                {lesson.audio_ml_url && <span className="px-2 py-0.5 bg-zinc-800 rounded text-[10px] font-medium text-zinc-400">Malayalam</span>}
                              </div>
                            )}
                          </div>
                        </div>
                      ))}

                      {module.lessons?.length === 0 && addingLessonToModule !== module.id && (
                        <div className="text-center py-6 text-zinc-600 text-sm">
                          Empty module
                        </div>
                      )}

                      {/* Add Lesson Inline Form */}
                      <AnimatePresence>
                        {addingLessonToModule === module.id && (
                          <motion.form 
                            initial={{ opacity: 0, height: 0 }}
                            animate={{ opacity: 1, height: 'auto' }}
                            exit={{ opacity: 0, height: 0 }}
                            onSubmit={(e) => handleAddLesson(e, module.id)}
                            className="bg-black border border-white/10 rounded-xl p-5 mt-4 space-y-4 overflow-hidden"
                          >
                            <h4 className="text-sm font-semibold text-[#facc15]">New Video Lesson</h4>
                            
                            <div>
                              <label className="block text-xs font-medium text-zinc-400 mb-1">Lesson Title *</label>
                              <input 
                                type="text"
                                required
                                value={lessonData.title}
                                onChange={(e) => setLessonData({...lessonData, title: e.target.value})}
                                className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white text-sm focus:outline-none focus:border-[#facc15]"
                              />
                            </div>
                            
                            <div>
                              <label className="block text-xs font-medium text-zinc-400 mb-1">Description</label>
                              <textarea 
                                rows={2}
                                value={lessonData.description}
                                onChange={(e) => setLessonData({...lessonData, description: e.target.value})}
                                className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white text-sm focus:outline-none focus:border-[#facc15] resize-none"
                              />
                            </div>

                            <div>
                              <label className="block text-xs font-medium text-zinc-400 mb-1">Original Video URL (English) *</label>
                              <input 
                                type="url"
                                required
                                placeholder="https://..."
                                value={lessonData.video_url}
                                onChange={(e) => setLessonData({...lessonData, video_url: e.target.value})}
                                className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white text-sm focus:outline-none focus:border-[#facc15]"
                              />
                            </div>

                            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 pt-2">
                              <div>
                                <label className="block text-xs font-medium text-zinc-400 mb-1">Hindi Audio URL</label>
                                <input 
                                  type="url"
                                  value={lessonData.audio_hi_url}
                                  onChange={(e) => setLessonData({...lessonData, audio_hi_url: e.target.value})}
                                  className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white text-sm focus:outline-none focus:border-[#facc15]"
                                />
                              </div>
                              <div>
                                <label className="block text-xs font-medium text-zinc-400 mb-1">Tamil Audio URL</label>
                                <input 
                                  type="url"
                                  value={lessonData.audio_ta_url}
                                  onChange={(e) => setLessonData({...lessonData, audio_ta_url: e.target.value})}
                                  className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white text-sm focus:outline-none focus:border-[#facc15]"
                                />
                              </div>
                              <div>
                                <label className="block text-xs font-medium text-zinc-400 mb-1">Malayalam Audio URL</label>
                                <input 
                                  type="url"
                                  value={lessonData.audio_ml_url}
                                  onChange={(e) => setLessonData({...lessonData, audio_ml_url: e.target.value})}
                                  className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white text-sm focus:outline-none focus:border-[#facc15]"
                                />
                              </div>
                            </div>

                            <div className="flex justify-end gap-3 pt-4 border-t border-white/10 mt-4">
                              <button 
                                type="button" 
                                onClick={() => setAddingLessonToModule(null)}
                                className="px-4 py-2 text-sm text-zinc-400 hover:text-white"
                              >
                                Cancel
                              </button>
                              <button 
                                type="submit"
                                disabled={lessonLoading}
                                className="px-5 py-2 text-sm bg-[#facc15] text-black font-semibold rounded-lg hover:bg-[#eab308] transition-colors disabled:opacity-50"
                              >
                                Save Lesson
                              </button>
                            </div>
                          </motion.form>
                        )}
                      </AnimatePresence>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
