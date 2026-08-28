"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";

export default function CourseManager() {
  const { id } = useParams();
  const router = useRouter();
  
  const [course, setCourse] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  // State for Course Metadata Editing
  const [isEditingCourse, setIsEditingCourse] = useState(false);
  const [editCourseData, setEditCourseData] = useState({
    title: "",
    description: "",
    price: ""
  });

  // State for adding module
  const [showAddModule, setShowAddModule] = useState(false);
  const [moduleTitle, setModuleTitle] = useState("");
  const [moduleLoading, setModuleLoading] = useState(false);

  // State for renaming module
  const [editingModuleId, setEditingModuleId] = useState<number | null>(null);
  const [editModuleTitle, setEditModuleTitle] = useState("");

  // State for adding lesson
  const [addingLessonToModule, setAddingLessonToModule] = useState<number | null>(null);
  const [lessonData, setLessonData] = useState<{
    title: string;
    description: string;
    transcript: string;
    timed_transcript: string;
    video_file: File | null;
  }>({
    title: "",
    description: "",
    transcript: "",
    timed_transcript: "",
    video_file: null
  });
  const [lessonLoading, setLessonLoading] = useState(false);

  // State for editing lesson
  const [editingLessonId, setEditingLessonId] = useState<number | null>(null);
  const [editLessonData, setEditLessonData] = useState<{
    title: string;
    description: string;
    transcript: string;
    timed_transcript: string;
  }>({
    title: "",
    description: "",
    transcript: "",
    timed_transcript: ""
  });
  const [editLessonLoading, setEditLessonLoading] = useState(false);

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
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${id}/`, {
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

  const handleEditCourse = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${id}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({
          title: editCourseData.title,
          description: editCourseData.description,
          price: parseFloat(editCourseData.price) || 0
        }),
        credentials: "include"
      });

      if (res.ok) {
        setIsEditingCourse(false);
        fetchCourse();
      } else {
        alert("Failed to update course details");
      }
    } catch (err) {
      console.error(err);
      alert("Error updating course");
    }
  };

  const handleAddModule = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!moduleTitle.trim()) return;
    
    setModuleLoading(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/modules/`, {
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
        fetchCourse();
      } else {
        const errData = await res.text();
        alert("Failed to create module: " + errData);
      }
    } catch (err) {
      console.error(err);
      alert("Network error creating module");
    } finally {
      setModuleLoading(false);
    }
  };

  const handleRenameModule = async (moduleId: number) => {
    if (!editModuleTitle.trim()) return;
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/modules/${moduleId}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ title: editModuleTitle }),
        credentials: "include"
      });
      if (res.ok) {
        setEditingModuleId(null);
        fetchCourse();
      } else {
        alert("Failed to rename module");
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleDeleteModule = async (moduleId: number, title: string) => {
    if (!confirm(`Are you sure you want to delete module "${title}"? This will also delete all lessons inside it.`)) return;
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/modules/${moduleId}/`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include"
      });
      if (res.ok) {
        fetchCourse();
      } else {
        alert("Failed to delete module");
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleMoveModule = async (index: number, direction: 'up' | 'down') => {
    const targetIndex = direction === 'up' ? index - 1 : index + 1;
    if (targetIndex < 0 || targetIndex >= course.modules.length) return;
    
    const currentModule = course.modules[index];
    const swapModule = course.modules[targetIndex];
    
    try {
      // Swap order tags
      const p1 = fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/modules/${currentModule.id}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ order: swapModule.order }),
        credentials: "include"
      });
      
      const p2 = fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/modules/${swapModule.id}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ order: currentModule.order }),
        credentials: "include"
      });
      
      await Promise.all([p1, p2]);
      fetchCourse();
    } catch (err) {
      console.error(err);
    }
  };

  const handleAddLesson = async (e: React.FormEvent, moduleId: number) => {
    e.preventDefault();
    if (!lessonData.title.trim() || !lessonData.video_file) return;

    setLessonLoading(true);
    try {
      const moduleObj = course.modules.find((m: any) => m.id === moduleId);
      
      const formData = new FormData();
      formData.append("title", lessonData.title);
      formData.append("description", lessonData.description);
      formData.append("transcript", lessonData.transcript);
      formData.append("timed_transcript", lessonData.timed_transcript);
      formData.append("video_file", lessonData.video_file);
      formData.append("module", moduleId.toString());
      formData.append("order", (moduleObj?.lessons?.length || 0).toString());

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/`, {
        method: "POST",
        headers: { 
          "X-CSRFToken": getCsrfToken()
        },
        body: formData,
        credentials: "include"
      });

      if (res.ok) {
        setAddingLessonToModule(null);
        setLessonData({
          title: "",
          description: "",
          transcript: "",
          timed_transcript: "",
          video_file: null
        });
        fetchCourse();
      } else {
        const errData = await res.text();
        alert("Failed to create lesson: " + errData);
      }
    } catch (err) {
      console.error(err);
      alert("Network error creating lesson");
    } finally {
      setLessonLoading(false);
    }
  };

  const handleEditLessonSubmit = async (e: React.FormEvent, lessonId: number) => {
    e.preventDefault();
    if (!editLessonData.title.trim()) return;

    setEditLessonLoading(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${lessonId}/`, {
        method: "PATCH",
        headers: { 
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify(editLessonData),
        credentials: "include"
      });

      if (res.ok) {
        setEditingLessonId(null);
        fetchCourse();
      } else {
        const errData = await res.text();
        alert("Failed to edit lesson: " + errData);
      }
    } catch (err) {
      console.error(err);
      alert("Network error editing lesson");
    } finally {
      setEditLessonLoading(false);
    }
  };

  const handleDeleteLesson = async (lessonId: number, title: string) => {
    if (!confirm(`Are you sure you want to delete lesson "${title}"?`)) return;
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${lessonId}/`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include"
      });
      if (res.ok) {
        fetchCourse();
      } else {
        alert("Failed to delete lesson");
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleMoveLesson = async (moduleIndex: number, lessonIndex: number, direction: 'up' | 'down') => {
    const moduleObj = course.modules[moduleIndex];
    const targetIndex = direction === 'up' ? lessonIndex - 1 : lessonIndex + 1;
    if (targetIndex < 0 || targetIndex >= moduleObj.lessons.length) return;
    
    const currentLesson = moduleObj.lessons[lessonIndex];
    const swapLesson = moduleObj.lessons[targetIndex];
    
    try {
      const p1 = fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${currentLesson.id}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ order: swapLesson.order }),
        credentials: "include"
      });
      
      const p2 = fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${swapLesson.id}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ order: currentLesson.order }),
        credentials: "include"
      });
      
      await Promise.all([p1, p2]);
      fetchCourse();
    } catch (err) {
      console.error(err);
    }
  };

  const handleGenerateAudio = async (lessonId: number) => {
    const userLangs = window.prompt("Enter target languages (comma separated). Supported: hi, ta, ml, fr, de", "hi, ta, ml");
    if (userLangs === null) return;
    
    const target_languages = userLangs.split(",").map(s => s.trim()).filter(Boolean);
    if (target_languages.length === 0) return;

    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${lessonId}/generate_ai_audio/`, {
        method: "POST",
        headers: { 
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ target_languages }),
        credentials: "include"
      });

      const data = await res.json();
      if (res.ok) {
        alert("Success: " + data.message);
        fetchCourse();
      } else {
        alert("Error: " + data.error);
      }
    } catch (err) {
      console.error(err);
      alert("Network error generating audio");
    }
  };

  const handleThumbnailUpload = async (e: any) => {
    const file = e.target.files?.[0];
    if (!file) return;

    try {
      const data = new FormData();
      data.append('thumbnail', file);

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${id}/`, {
        method: "PATCH",
        headers: {
          "X-CSRFToken": getCsrfToken()
        },
        body: data,
        credentials: "include"
      });

      if (res.ok) {
        fetchCourse();
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleTogglePublish = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${id}/`, {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({ is_published: !course.is_published }),
        credentials: "include"
      });

      if (res.ok) {
        fetchCourse();
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

            {isEditingCourse ? (
              <form onSubmit={handleEditCourse} className="space-y-4">
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1 uppercase tracking-wider">Title *</label>
                  <input
                    type="text"
                    required
                    value={editCourseData.title}
                    onChange={e => setEditCourseData({ ...editCourseData, title: e.target.value })}
                    className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1 uppercase tracking-wider">Description *</label>
                  <textarea
                    rows={4}
                    required
                    value={editCourseData.description}
                    onChange={e => setEditCourseData({ ...editCourseData, description: e.target.value })}
                    className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] resize-none"
                  />
                </div>
                <div>
                  <label className="block text-xs font-semibold text-zinc-400 mb-1 uppercase tracking-wider">Price (₹) *</label>
                  <input
                    type="number"
                    step="0.01"
                    min="0"
                    required
                    value={editCourseData.price}
                    onChange={e => setEditCourseData({ ...editCourseData, price: e.target.value })}
                    className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                  />
                </div>
                <div className="flex gap-2 justify-end pt-2">
                  <button
                    type="button"
                    onClick={() => setIsEditingCourse(false)}
                    className="px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
                  >
                    Cancel
                  </button>
                  <button
                    type="submit"
                    className="px-4 py-1.5 bg-[#facc15] text-black font-bold text-xs rounded-xl hover:bg-yellow-500"
                  >
                    Save
                  </button>
                </div>
              </form>
            ) : (
              <>
                <h2 className="text-xl font-bold mb-2">{course.title}</h2>
                <p className="text-zinc-400 text-sm mb-4 line-clamp-3 leading-relaxed">{course.description}</p>
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
                <button
                  onClick={() => {
                    setEditCourseData({
                      title: course.title,
                      description: course.description,
                      price: course.price
                    });
                    setIsEditingCourse(true);
                  }}
                  className="w-full mt-4 py-2 bg-white/5 border border-white/10 hover:bg-white/10 text-white rounded-xl text-xs font-semibold transition-colors"
                >
                  Edit Course Details
                </button>
              </>
            )}
          </div>
        </div>

        {/* Right Column: Curriculum Builder */}
        <div className="md:col-span-2">
          <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6">
            <div className="flex items-center justify-between mb-8">
              <h2 className="text-xl font-bold">Curriculum Builder</h2>
              <button 
                onClick={() => setShowAddModule(true)}
                className="px-4 py-2 bg-[#facc15]/10 text-[#facc15] hover:bg-[#facc15]/20 font-bold rounded-xl transition-colors text-sm"
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
                      className="flex-1 px-4 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] transition-colors"
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
                      className="px-6 py-2 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50"
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
                    <div className="bg-black/50 px-5 py-4 border-b border-white/10 flex items-center justify-between flex-wrap gap-4">
                      {editingModuleId === module.id ? (
                        <div className="flex gap-2 items-center flex-1">
                          <input
                            type="text"
                            value={editModuleTitle}
                            onChange={e => setEditModuleTitle(e.target.value)}
                            className="flex-1 px-3 py-1.5 bg-zinc-900 border border-[#facc15]/30 rounded-xl text-sm text-white focus:outline-none focus:border-[#facc15]"
                          />
                          <button
                            onClick={() => handleRenameModule(module.id)}
                            className="px-3 py-1.5 bg-[#facc15] text-black text-xs font-bold rounded-xl hover:bg-yellow-500"
                          >
                            Save
                          </button>
                          <button
                            onClick={() => setEditingModuleId(null)}
                            className="px-2 py-1.5 text-xs text-zinc-400 hover:text-white"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <h3 className="font-semibold flex items-center gap-3">
                          <span className="w-6 h-6 bg-white/10 text-xs flex items-center justify-center rounded-full text-zinc-400">
                            {idx + 1}
                          </span>
                          <span className="text-sm font-bold text-white">{module.title}</span>
                          
                          {/* Rename / Delete Module Triggers */}
                          <div className="flex gap-1 items-center">
                            <button
                              onClick={() => {
                                setEditingModuleId(module.id);
                                setEditModuleTitle(module.title);
                              }}
                              className="text-zinc-500 hover:text-[#facc15] p-1 transition-colors"
                              title="Rename module"
                            >
                              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
                            </button>
                            <button
                              onClick={() => handleDeleteModule(module.id, module.title)}
                              className="text-zinc-500 hover:text-red-500 p-1 transition-colors"
                              title="Delete module"
                            >
                              <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
                            </button>
                          </div>
                        </h3>
                      )}
                      
                      <div className="flex items-center gap-4">
                        {/* Module Order Arrows */}
                        <div className="flex gap-1 items-center">
                          <button
                            disabled={idx === 0}
                            onClick={() => handleMoveModule(idx, 'up')}
                            className="text-zinc-500 hover:text-white disabled:opacity-30 disabled:hover:text-zinc-500 p-1"
                            title="Move Up"
                          >
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
                          </button>
                          <button
                            disabled={idx === (course.modules?.length || 1) - 1}
                            onClick={() => handleMoveModule(idx, 'down')}
                            className="text-zinc-500 hover:text-white disabled:opacity-30 disabled:hover:text-zinc-500 p-1"
                            title="Move Down"
                          >
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
                          </button>
                        </div>

                        <button 
                          onClick={() => setAddingLessonToModule(addingLessonToModule === module.id ? null : module.id)}
                          className="text-xs font-bold text-zinc-400 hover:text-white transition-colors"
                        >
                          + Add Video
                        </button>
                      </div>
                    </div>

                    <div className="p-4 space-y-3">
                      {module.lessons?.map((lesson: any, lIdx: number) => (
                        <div key={lesson.id} className="bg-zinc-900/50 border border-white/5 p-4 rounded-xl hover:border-white/10 transition-colors">
                          <div className="flex items-start gap-4">
                            <div className="mt-1 flex flex-col items-center gap-2">
                              <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-[#facc15]">
                                <polygon points="5 3 19 12 5 21 5 3"></polygon>
                              </svg>
                              
                              {/* Lesson Reordering Arrows */}
                              <div className="flex flex-col gap-0.5">
                                <button
                                  disabled={lIdx === 0}
                                  onClick={() => handleMoveLesson(idx, lIdx, 'up')}
                                  className="text-zinc-600 hover:text-white disabled:opacity-30 disabled:hover:text-zinc-600"
                                  title="Move Up"
                                >
                                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="18 15 12 9 6 15"/></svg>
                                </button>
                                <button
                                  disabled={lIdx === (module.lessons?.length || 1) - 1}
                                  onClick={() => handleMoveLesson(idx, lIdx, 'down')}
                                  className="text-zinc-600 hover:text-white disabled:opacity-30 disabled:hover:text-zinc-600"
                                  title="Move Down"
                                >
                                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
                                </button>
                              </div>
                            </div>
                            <div className="flex-1 min-w-0">
                              <h4 className="font-semibold text-sm truncate text-white">{lIdx + 1}. {lesson.title}</h4>
                              <div className="text-xs text-zinc-500 mt-1 truncate">{lesson.video_file || "No video file"}</div>
                              {lesson.translated_audios && lesson.translated_audios.length > 0 && (
                                <div className="flex gap-2 mt-2">
                                  {lesson.translated_audios.map((audio: any) => (
                                    <span key={audio.id} className="px-2 py-0.5 bg-zinc-800 rounded text-[10px] font-medium text-zinc-400 uppercase">
                                      {audio.language_code.split('-')[0]} {audio.status === 'completed' ? '✓' : '...'}
                                    </span>
                                  ))}
                                </div>
                              )}
                            </div>
                            <div className="flex flex-col gap-2 items-end">
                              <div className="flex gap-2">
                                <button 
                                  onClick={() => {
                                    setEditingLessonId(lesson.id);
                                    setEditLessonData({
                                      title: lesson.title,
                                      description: lesson.description || "",
                                      transcript: lesson.transcript || "",
                                      timed_transcript: lesson.timed_transcript || ""
                                    });
                                  }}
                                  className="text-xs font-bold px-3 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-white rounded-xl transition-colors"
                                >
                                  Edit
                                </button>
                                <button 
                                  onClick={() => handleDeleteLesson(lesson.id, lesson.title)}
                                  className="text-xs font-bold px-3 py-1.5 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-xl transition-colors"
                                >
                                  Delete
                                </button>
                              </div>
                              <button 
                                onClick={() => handleGenerateAudio(lesson.id)}
                                className="text-xs font-medium px-3 py-1.5 bg-purple-500/20 hover:bg-purple-500/30 text-purple-400 rounded-xl transition-colors flex items-center gap-1"
                                title="Generates Hindi, Tamil, and Malayalam audio tracks from transcript"
                              >
                                🪄 Generate AI Audio
                              </button>
                            </div>
                          </div>
                          
                          {/* Edit Lesson Inline Form */}
                          <AnimatePresence>
                            {editingLessonId === lesson.id && (
                              <motion.form 
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                onSubmit={(e) => handleEditLessonSubmit(e, lesson.id)}
                                className="bg-black border border-white/10 rounded-xl p-5 mt-4 space-y-4 overflow-hidden"
                              >
                                <h4 className="text-sm font-bold text-[#facc15]">Edit Video Lesson</h4>
                                
                                <div>
                                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Lesson Title *</label>
                                  <input 
                                    type="text"
                                    required
                                    value={editLessonData.title}
                                    onChange={(e) => setEditLessonData({...editLessonData, title: e.target.value})}
                                    className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                                  />
                                </div>
                                
                                <div>
                                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Description</label>
                                  <textarea 
                                    rows={2}
                                    value={editLessonData.description}
                                    onChange={(e) => setEditLessonData({...editLessonData, description: e.target.value})}
                                    className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] resize-none"
                                  />
                                </div>

                                {/* English Transcript */}
                                <div>
                                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">English Transcript (For AI Translation)</label>
                                  <textarea 
                                    rows={4}
                                    placeholder="Paste the spoken English text here. The AI will translate this."
                                    value={editLessonData.transcript}
                                    onChange={(e) => setEditLessonData({...editLessonData, transcript: e.target.value})}
                                    className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] resize-vertical"
                                  />
                                </div>

                                {/* Timing for Speaking */}
                                <div className="bg-[#facc15]/5 border border-[#facc15]/20 rounded-xl p-4">
                                  <div className="flex items-center gap-2 mb-2">
                                    <span className="text-sm">⏱</span>
                                    <label className="block text-xs font-semibold text-[#facc15] uppercase tracking-wide">Timing for Speaking (for Perfect AI Dubbing)</label>
                                  </div>
                                  <p className="text-[10px] text-zinc-500 mb-3 leading-relaxed">
                                    One line per spoken sentence. Format: <code className="bg-zinc-800 px-1 rounded text-zinc-300">MM:SS --&gt; Text spoken at that time</code><br/>
                                    Example:<br/>
                                    <code className="bg-zinc-800 px-1 rounded text-zinc-300">00:05 --&gt; Hello and welcome to this class</code><br/>
                                    <code className="bg-zinc-800 px-1 rounded text-zinc-300">00:12 --&gt; Today we will learn Carnatic music</code>
                                  </p>
                                  <textarea 
                                    rows={6}
                                    placeholder={"00:05 --> Hello and welcome\n00:12 --> Today we learn music\n00:20 --> Let us start with the notes"}
                                    value={editLessonData.timed_transcript}
                                    onChange={(e) => setEditLessonData({...editLessonData, timed_transcript: e.target.value})}
                                    className="w-full px-3 py-2 bg-zinc-900 border border-[#facc15]/30 rounded-xl text-white text-xs font-mono focus:outline-none focus:border-[#facc15] resize-vertical"
                                  />
                                  <p className="text-[10px] text-zinc-600 mt-2">💡 Leave blank to let Whisper AI auto-detect timings (less accurate). Fill this in for perfect sync.</p>
                                </div>

                                <div className="flex justify-end gap-3 pt-4 border-t border-white/10 mt-4">
                                  <button 
                                    type="button" 
                                    onClick={() => setEditingLessonId(null)}
                                    className="px-4 py-2 text-sm text-zinc-400 hover:text-white"
                                  >
                                    Cancel
                                  </button>
                                  <button 
                                    type="submit"
                                    disabled={editLessonLoading}
                                    className="px-5 py-2 text-sm bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50"
                                  >
                                    Save Changes
                                  </button>
                                </div>
                              </motion.form>
                            )}
                          </AnimatePresence>
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
                            <h4 className="text-sm font-bold text-[#facc15]">New Video Lesson</h4>
                            
                            <div>
                              <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Lesson Title *</label>
                              <input 
                                type="text"
                                required
                                value={lessonData.title}
                                onChange={(e) => setLessonData({...lessonData, title: e.target.value})}
                                className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                              />
                            </div>
                            
                            <div>
                              <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Description</label>
                              <textarea 
                                rows={2}
                                value={lessonData.description}
                                onChange={(e) => setLessonData({...lessonData, description: e.target.value})}
                                className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] resize-none"
                              />
                            </div>

                            {/* English Transcript */}
                            <div>
                              <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">English Transcript (For AI Translation)</label>
                              <textarea 
                                rows={4}
                                placeholder="Paste the spoken English text here. The AI will translate this."
                                value={lessonData.transcript}
                                onChange={(e) => setLessonData({...lessonData, transcript: e.target.value})}
                                className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] resize-vertical"
                              />
                            </div>

                            {/* Timing for Speaking */}
                            <div className="bg-[#facc15]/5 border border-[#facc15]/20 rounded-xl p-4">
                              <div className="flex items-center gap-2 mb-2">
                                <span className="text-sm">⏱</span>
                                <label className="block text-xs font-semibold text-[#facc15] uppercase tracking-wide">Timing for Speaking (for Perfect AI Dubbing)</label>
                              </div>
                              <p className="text-[10px] text-zinc-500 mb-3 leading-relaxed">
                                One line per spoken sentence. Format: <code className="bg-zinc-800 px-1 rounded text-zinc-300">MM:SS --&gt; Text spoken at that time</code><br/>
                                Example:<br/>
                                <code className="bg-zinc-800 px-1 rounded text-zinc-300">00:05 --&gt; Hello and welcome to this class</code><br/>
                                <code className="bg-zinc-800 px-1 rounded text-zinc-300">00:12 --&gt; Today we will learn Carnatic music</code>
                              </p>
                              <textarea 
                                rows={6}
                                placeholder={"00:05 --> Hello and welcome\n00:12 --> Today we learn music\n00:20 --> Let us start with the notes"}
                                value={lessonData.timed_transcript}
                                onChange={(e) => setLessonData({...lessonData, timed_transcript: e.target.value})}
                                className="w-full px-3 py-2 bg-zinc-900 border border-[#facc15]/30 rounded-xl text-white text-xs font-mono focus:outline-none focus:border-[#facc15] resize-vertical"
                              />
                              <p className="text-[10px] text-zinc-600 mt-2">💡 Leave blank to let Whisper AI auto-detect timings (less accurate). Fill this in for perfect sync.</p>
                            </div>

                            <div>
                              <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Upload Original Video (MP4) *</label>
                              <input 
                                type="file"
                                accept="video/mp4,video/x-m4v,video/*"
                                required
                                onChange={(e) => setLessonData({...lessonData, video_file: e.target.files?.[0] || null})}
                                className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-sm text-zinc-400 focus:outline-none focus:border-[#facc15] file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-sm file:font-semibold file:bg-[#facc15] file:text-black hover:file:bg-yellow-500 cursor-pointer"
                              />
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
                                className="px-5 py-2 text-sm bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50"
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
