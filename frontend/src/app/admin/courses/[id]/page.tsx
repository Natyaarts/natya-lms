"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { motion, AnimatePresence } from "framer-motion";
import { 
  ChevronUp, 
  ChevronDown, 
  Video, 
  Film, 
  Headphones, 
  Globe, 
  Pencil, 
  Trash2, 
  Plus, 
  Layers, 
  AlertCircle, 
  Zap, 
  Clock, 
  Sparkles,
  RefreshCw,
  Check,
  X
} from "lucide-react";

// Canonical language list for manual audio-track uploads. Kept in sync with
// backend/courses/languages.py (SUPPORTED_LANGUAGES). Base codes only -- the
// backend normalizes legacy regional codes (e.g. 'hi-IN') for display.
const SUPPORTED_LANGUAGES: { code: string; name: string }[] = [
  { code: "ml", name: "Malayalam" },
  { code: "hi", name: "Hindi" },
  { code: "ta", name: "Tamil" },
  { code: "te", name: "Telugu" },
  { code: "kn", name: "Kannada" },
  { code: "bn", name: "Bengali" },
  { code: "mr", name: "Marathi" },
  { code: "gu", name: "Gujarati" },
  { code: "pa", name: "Punjabi" },
  { code: "ar", name: "Arabic" },
  { code: "fr", name: "French" },
  { code: "de", name: "German" },
  { code: "es", name: "Spanish" },
  { code: "pt", name: "Portuguese" },
  { code: "it", name: "Italian" },
  { code: "ja", name: "Japanese" },
  { code: "ko", name: "Korean" },
  { code: "zh", name: "Chinese" },
  { code: "ru", name: "Russian" },
];

const languageDisplayName = (code: string) => {
  const base = code.split("-")[0].toLowerCase();
  return SUPPORTED_LANGUAGES.find(l => l.code === base)?.name || code;
};

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
    price: "",
    course_type: "RECORDED"
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
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);

  // State for editing lesson
  const [editingLessonId, setEditingLessonId] = useState<number | null>(null);
  const [editLessonData, setEditLessonData] = useState<{
    title: string;
    description: string;
    transcript: string;
    timed_transcript: string;
    moduleId: number | null;
  }>({
    title: "",
    description: "",
    transcript: "",
    timed_transcript: "",
    moduleId: null
  });
  const [editLessonLoading, setEditLessonLoading] = useState(false);

  // State for Audio Track management (manual multilingual audio upload)
  const [audioManagerLessonId, setAudioManagerLessonId] = useState<number | null>(null);
  const [showAddAudioForm, setShowAddAudioForm] = useState(false);
  const [newAudioLangCode, setNewAudioLangCode] = useState("");
  const [newAudioFile, setNewAudioFile] = useState<File | null>(null);
  const [audioUploadLoading, setAudioUploadLoading] = useState(false);
  const [audioUploadError, setAudioUploadError] = useState("");
  const [replacingAudioId, setReplacingAudioId] = useState<number | null>(null);
  const [deletingAudioId, setDeletingAudioId] = useState<number | null>(null);

  // State for Instructor management (Course -> CourseInstructor -> Teacher/Mentor/Assistant)
  const [instructors, setInstructors] = useState<any[]>([]);
  const [instructorsLoading, setInstructorsLoading] = useState(true);
  const [eligibleUsers, setEligibleUsers] = useState<any[]>([]);
  const [showAddInstructorForm, setShowAddInstructorForm] = useState(false);
  const [newInstructorUserId, setNewInstructorUserId] = useState("");
  const [newInstructorRole, setNewInstructorRole] = useState("TEACHER");
  const [newInstructorPrimary, setNewInstructorPrimary] = useState(false);
  const [instructorSaving, setInstructorSaving] = useState(false);
  const [instructorError, setInstructorError] = useState("");
  const [removingInstructorId, setRemovingInstructorId] = useState<number | null>(null);

  // Reordering visual feedback & toasts
  const [highlightedLessonId, setHighlightedLessonId] = useState<number | null>(null);
  const [toastMessage, setToastMessage] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToastMessage(msg);
    setTimeout(() => {
      setToastMessage((cur) => (cur === msg ? null : cur));
    }, 4000);
  };

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

  // --- Course Instructor management (separate from Enrollment) ---
  // CourseInstructor = "who is responsible for this course" (teacher/mentor/
  // assistant). Enrollment = "who has learner access to this course." These
  // are intentionally never mixed here.

  const fetchInstructors = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${id}/instructors/`, {
        credentials: "include"
      });
      if (res.ok) setInstructors(await res.json());
    } catch (err) {
      console.error(err);
    } finally {
      setInstructorsLoading(false);
    }
  };

  const fetchEligibleUsers = async () => {
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/admin-users/`, {
        credentials: "include"
      });
      if (res.ok) {
        const all = await res.json();
        setEligibleUsers(all.filter((u: any) => u.is_teacher || u.is_mentor));
      }
    } catch (err) {
      console.error(err);
    }
  };

  const handleAddInstructor = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newInstructorUserId || instructorSaving) return;

    setInstructorSaving(true);
    setInstructorError("");
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${id}/instructors/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify({
          user: newInstructorUserId,
          role: newInstructorRole,
          is_primary: newInstructorPrimary
        }),
        credentials: "include"
      });

      if (res.ok) {
        setShowAddInstructorForm(false);
        setNewInstructorUserId("");
        setNewInstructorRole("TEACHER");
        setNewInstructorPrimary(false);
        fetchInstructors();
      } else {
        const data = await res.json();
        const message = data.non_field_errors?.[0] || data.user?.[0] || data.detail || "Failed to add instructor.";
        setInstructorError(message);
      }
    } catch (err) {
      console.error(err);
      setInstructorError("Network error adding instructor.");
    } finally {
      setInstructorSaving(false);
    }
  };

  const handleRemoveInstructor = async (instructorId: number, name: string) => {
    if (!confirm(`Remove "${name}" from this course's instructors?`)) return;
    setRemovingInstructorId(instructorId);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${id}/instructors/${instructorId}/`, {
        method: "DELETE",
        headers: { "X-CSRFToken": getCsrfToken() },
        credentials: "include"
      });
      if (res.ok) {
        fetchInstructors();
      } else {
        alert("Failed to remove instructor.");
      }
    } catch (err) {
      console.error(err);
      alert("Network error removing instructor.");
    } finally {
      setRemovingInstructorId(null);
    }
  };

  useEffect(() => {
    if (id) {
      fetchInstructors();
      fetchEligibleUsers();
    }
  }, [id]);

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
          price: parseFloat(editCourseData.price) || 0,
          course_type: editCourseData.course_type || "RECORDED"
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
    if (targetIndex < 0 || targetIndex >= (course?.modules?.length || 0)) return;

    const reordered = [...course.modules];
    const [moved] = reordered.splice(index, 1);
    reordered.splice(targetIndex, 0, moved);

    // Optimistically update the UI instantly
    setCourse({ ...course, modules: reordered });
    showToast(`Moved Section "${moved.title}" to position #${targetIndex + 1}`);

    try {
      const updates = reordered.map((mod, newOrder) =>
        fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/modules/${mod.id}/`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken()
          },
          body: JSON.stringify({ order: newOrder }),
          credentials: "include"
        })
      );
      await Promise.all(updates);
      fetchCourse();
    } catch (err) {
      console.error(err);
      fetchCourse();
    }
  };

  const handleAddLesson = async (e: React.FormEvent, moduleId: number) => {
    e.preventDefault();
    if (!lessonData.title.trim() || !lessonData.video_file) return;

    if (lessonData.video_file.size > 2 * 1024 * 1024 * 1024) {
      alert(`File exceeds maximum allowed upload limit of 2 GB (${(lessonData.video_file.size / (1024 * 1024 * 1024)).toFixed(2)} GB). Please choose a video under 2 GB.`);
      return;
    }

    setLessonLoading(true);
    setUploadProgress(0);
    try {
      const moduleObj = course.modules.find((m: any) => m.id === moduleId);
      const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
      const csrf = getCsrfToken();

      // Step 1: Request presigned S3 PUT URL from backend
      const urlRes = await fetch(`${apiUrl}/api/courses/lessons/get-upload-url/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf
        },
        body: JSON.stringify({
          filename: lessonData.video_file.name,
          file_type: lessonData.video_file.type || "video/mp4"
        }),
        credentials: "include"
      });

      if (!urlRes.ok) {
        const errorData = await urlRes.json().catch(() => ({}));
        throw new Error(errorData.error || "Failed to authorize direct S3 upload from backend.");
      }

      const { upload_url, s3_key } = await urlRes.json();

      // Step 2: Upload video directly to AWS S3 (completely bypasses Cloudflare 100 MB proxy cutoff)
      await new Promise<void>((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open("PUT", upload_url);
        xhr.setRequestHeader("Content-Type", lessonData.video_file?.type || "video/mp4");

        xhr.upload.onprogress = (event) => {
          if (event.lengthComputable) {
            const percent = Math.round((event.loaded / event.total) * 100);
            setUploadProgress(percent);
          }
        };

        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            resolve();
          } else {
            reject(new Error(`S3 direct upload returned status ${xhr.status}`));
          }
        };

        xhr.onerror = () => {
          reject(new Error("Direct upload to AWS S3 was blocked by browser security (CORS). Please ensure CORS is enabled on the S3 bucket 'natyalms-media-2026' in AWS Console."));
        };

        xhr.send(lessonData.video_file);
      });

      // Step 3: Register the lesson in Django with the verified S3 key
      const createRes = await fetch(`${apiUrl}/api/courses/lessons/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": csrf
        },
        body: JSON.stringify({
          title: lessonData.title,
          description: lessonData.description,
          transcript: lessonData.transcript,
          timed_transcript: lessonData.timed_transcript,
          video_file: s3_key,
          module: moduleId,
          order: moduleObj?.lessons?.length || 0
        }),
        credentials: "include"
      });

      if (!createRes.ok) {
        const createErr = await createRes.text();
        throw new Error("Failed to create lesson record: " + createErr);
      }

      setAddingLessonToModule(null);
      setLessonData({
        title: "",
        description: "",
        transcript: "",
        timed_transcript: "",
        video_file: null
      });
      fetchCourse();
    } catch (err: any) {
      console.error(err);
      alert("Error creating lesson: " + (err?.message || "Network error"));
    } finally {
      setLessonLoading(false);
      setUploadProgress(null);
    }
  };

  const handleEditLessonSubmit = async (e: React.FormEvent, lessonId: number) => {
    e.preventDefault();
    if (!editLessonData.title.trim()) return;

    setEditLessonLoading(true);
    try {
      const payload: any = {
        title: editLessonData.title,
        description: editLessonData.description,
        transcript: editLessonData.transcript,
        timed_transcript: editLessonData.timed_transcript
      };
      if (editLessonData.moduleId) {
        payload.module = editLessonData.moduleId;
      }

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${lessonId}/`, {
        method: "PATCH",
        headers: { 
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken()
        },
        body: JSON.stringify(payload),
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

  const getCleanVideoFileName = (videoUrlOrPath: string | null | undefined): string => {
    if (!videoUrlOrPath) return "";
    try {
      const cleanUrl = videoUrlOrPath.split('?')[0];
      const rawFileName = cleanUrl.split('/').pop() || videoUrlOrPath;
      const decoded = decodeURIComponent(rawFileName);
      // Strip 12-char hex UUID prefix generated during direct S3 upload (e.g. "a1b2c3d4e5f6_")
      const cleaned = decoded.replace(/^[a-f0-9]{12}_/i, '');
      return cleaned;
    } catch (e) {
      return videoUrlOrPath;
    }
  };

  const handleMoveLessonToIndex = async (moduleIndex: number, currentLessonIndex: number, targetIndex: number) => {
    const moduleObj = course?.modules?.[moduleIndex];
    if (!moduleObj || !moduleObj.lessons) return;
    if (targetIndex < 0 || targetIndex >= moduleObj.lessons.length || targetIndex === currentLessonIndex) return;

    const reordered = [...moduleObj.lessons];
    const [moved] = reordered.splice(currentLessonIndex, 1);
    reordered.splice(targetIndex, 0, moved);

    // Optimistically update the UI instantly
    const updatedModules = [...course.modules];
    updatedModules[moduleIndex] = { ...moduleObj, lessons: reordered };
    setCourse({ ...course, modules: updatedModules });

    setHighlightedLessonId(moved.id);
    setTimeout(() => setHighlightedLessonId(null), 1800);

    showToast(`Moved "${moved.title}" to position #${targetIndex + 1}`);

    try {
      const updates = reordered.map((lesson, newOrder) =>
        fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${lesson.id}/`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCsrfToken()
          },
          body: JSON.stringify({ order: newOrder }),
          credentials: "include"
        })
      );
      await Promise.all(updates);
      fetchCourse();
    } catch (err) {
      console.error(err);
      fetchCourse();
    }
  };

  const handleMoveLesson = (moduleIndex: number, lessonIndex: number, direction: 'up' | 'down') => {
    const targetIndex = direction === 'up' ? lessonIndex - 1 : lessonIndex + 1;
    handleMoveLessonToIndex(moduleIndex, lessonIndex, targetIndex);
  };


  // --- Manual Audio Track management ---
  // The admin uploads audio dubbed/translated externally (AI service, human
  // voice artist, studio, etc). The LMS just stores and serves the file --
  // it never generates audio itself. See backend VideoLessonViewSet.upload_audio.

  const openAudioManager = (lessonId: number) => {
    setAudioManagerLessonId(lessonId);
    setShowAddAudioForm(false);
    setNewAudioLangCode("");
    setNewAudioFile(null);
    setAudioUploadError("");
  };

  const closeAudioManager = () => {
    setAudioManagerLessonId(null);
    setShowAddAudioForm(false);
    setReplacingAudioId(null);
  };

  const submitAudioUpload = async (e: React.FormEvent, lessonId: number, replaceId: number | null) => {
    e.preventDefault();
    if (!newAudioLangCode || !newAudioFile || audioUploadLoading) return;

    setAudioUploadLoading(true);
    setAudioUploadError("");
    try {
      const langMeta = SUPPORTED_LANGUAGES.find(l => l.code === newAudioLangCode);
      const formData = new FormData();
      formData.append("language_code", newAudioLangCode);
      formData.append("language_name", langMeta?.name || newAudioLangCode);
      formData.append("audio_file", newAudioFile);

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${lessonId}/audio/`, {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() },
        body: formData,
        credentials: "include"
      });

      const data = await res.json();
      if (res.ok) {
        if (data.duration_warning) {
          alert("Audio uploaded, but: " + data.duration_warning);
        }
        setShowAddAudioForm(false);
        setNewAudioLangCode("");
        setNewAudioFile(null);
        setReplacingAudioId(null);
        fetchCourse();
      } else {
        const message = data.error || data.audio_file?.[0] || data.language_code?.[0] || "Failed to upload audio.";
        setAudioUploadError(message);
      }
    } catch (err) {
      console.error(err);
      setAudioUploadError("Network error uploading audio");
    } finally {
      setAudioUploadLoading(false);
    }
  };

  const handleDeleteAudio = async (lessonId: number, audioId: number, langLabel: string) => {
    if (!confirm(`Remove the "${langLabel}" audio track? This cannot be undone.`)) return;
    setDeletingAudioId(audioId);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/lessons/${lessonId}/audio/${audioId}/`, {
        method: "DELETE",
        headers: { "X-CSRFToken": getCsrfToken() },
        credentials: "include"
      });
      if (res.ok) {
        fetchCourse();
      } else {
        alert("Failed to delete audio track");
      }
    } catch (err) {
      console.error(err);
      alert("Network error deleting audio track");
    } finally {
      setDeletingAudioId(null);
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

  const handleDeleteCourse = async () => {
    if (!course) return;
    if (!window.confirm(`Are you sure you want to delete "${course.title}"? This will permanently delete the course, its modules, and lessons.`)) {
      return;
    }

    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/${course.id}/`, {
        method: "DELETE",
        headers: {
          "X-CSRFToken": getCsrfToken()
        },
        credentials: "include"
      });

      if (res.ok || res.status === 204) {
        router.push("/admin/courses");
      } else {
        const data = await res.json().catch(() => ({}));
        alert(data.error || data.detail || "Failed to delete course.");
      }
    } catch (err) {
      console.error(err);
      alert("Network error deleting course.");
    }
  };

  if (loading) return <div className="text-zinc-500 p-8">Loading course details...</div>;
  if (!course) return <div className="text-red-500 p-8">Course not found</div>;

  return (
    <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 pb-20">
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

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8">
        {/* Left Column: Course Details */}
        <div className="lg:col-span-4 space-y-6">
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
                <div className="grid grid-cols-2 gap-3">
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
                  <div>
                    <label className="block text-xs font-semibold text-zinc-400 mb-1 uppercase tracking-wider">Course Type *</label>
                    <select
                      value={editCourseData.course_type}
                      onChange={e => setEditCourseData({ ...editCourseData, course_type: e.target.value })}
                      className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                    >
                      <option value="RECORDED">Recorded</option>
                      <option value="LIVE">Live Interactive</option>
                    </select>
                  </div>
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
                  <span className="text-zinc-500 text-sm">Course Type</span>
                  <span className={`px-2 py-0.5 rounded text-xs font-medium ${course.course_type === 'LIVE' ? 'bg-[#facc15]/20 text-[#facc15]' : 'bg-white/10 text-zinc-300'}`}>
                    {course.course_type === 'LIVE' ? 'Live Interactive' : 'Recorded'}
                  </span>
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
                      price: course.price,
                      course_type: course.course_type || "RECORDED"
                    });
                    setIsEditingCourse(true);
                  }}
                  className="w-full mt-4 py-2 bg-white/5 border border-white/10 hover:bg-white/10 text-white rounded-xl text-xs font-semibold transition-colors"
                >
                  Edit Course Details
                </button>
                <button
                  type="button"
                  onClick={handleDeleteCourse}
                  className="w-full mt-2 py-2 bg-red-500/10 border border-red-500/20 hover:bg-red-500/20 text-red-400 rounded-xl text-xs font-semibold transition-colors"
                >
                  Delete Course
                </button>
              </>
            )}
          </div>

          {/* Instructors: who is responsible for this course (Teacher/Mentor/
              Assistant). Separate from Enrollment (who has learner access). */}
          <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-lg font-bold">Instructors</h2>
              <button
                onClick={() => { setShowAddInstructorForm(true); setInstructorError(""); }}
                className="text-xs font-bold text-[#facc15] hover:text-yellow-400 transition-colors"
              >
                + Add
              </button>
            </div>

            {instructorsLoading ? (
              <div className="text-xs text-zinc-500 py-4">Loading instructors...</div>
            ) : instructors.length === 0 && !showAddInstructorForm ? (
              <div className="text-center py-6 text-zinc-500 text-xs border border-dashed border-white/10 rounded-xl">
                No instructors assigned yet.
              </div>
            ) : (
              <div className="space-y-2">
                {instructors.map((inst: any) => (
                  <div key={inst.id} className="flex items-center justify-between bg-zinc-950 border border-white/5 rounded-xl px-4 py-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-semibold text-white text-sm truncate">{inst.user_name}</span>
                        {inst.is_primary && (
                          <span className="px-1.5 py-0.5 bg-[#facc15]/20 text-[#facc15] text-[9px] font-bold rounded uppercase tracking-wide">Primary</span>
                        )}
                        {!inst.is_active_user && (
                          <span className="px-1.5 py-0.5 bg-red-500/20 text-red-400 text-[9px] font-bold rounded uppercase tracking-wide">Inactive</span>
                        )}
                      </div>
                      <div className="text-[11px] text-zinc-500 mt-0.5 truncate">
                        {inst.user_email || inst.user_phone || "No contact info"}
                      </div>
                      <span className={`inline-block mt-1.5 px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wide ${
                        inst.role === 'TEACHER' ? 'bg-blue-500/10 text-blue-400' :
                        inst.role === 'MENTOR' ? 'bg-purple-500/10 text-purple-400' :
                        'bg-zinc-700/50 text-zinc-400'
                      }`}>
                        {inst.role}
                      </span>
                    </div>
                    <button
                      onClick={() => handleRemoveInstructor(inst.id, inst.user_name)}
                      disabled={removingInstructorId === inst.id}
                      className="p-2 hover:bg-red-500/10 rounded-lg transition-colors text-zinc-500 hover:text-red-400 disabled:opacity-50 shrink-0"
                      title="Remove instructor"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
                    </button>
                  </div>
                ))}
              </div>
            )}

            <AnimatePresence>
              {showAddInstructorForm && (
                <motion.form
                  initial={{ opacity: 0, height: 0 }}
                  animate={{ opacity: 1, height: 'auto' }}
                  exit={{ opacity: 0, height: 0 }}
                  onSubmit={handleAddInstructor}
                  className="mt-4 bg-black border border-white/10 rounded-xl p-4 space-y-3 overflow-hidden"
                >
                  <div>
                    <label className="block text-[10px] font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Person</label>
                    <select
                      required
                      value={newInstructorUserId}
                      onChange={(e) => setNewInstructorUserId(e.target.value)}
                      className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                    >
                      <option value="" disabled>Select a teacher or mentor</option>
                      {eligibleUsers.map((u: any) => (
                        <option key={u.id} value={u.id}>
                          {(u.first_name || u.last_name) ? `${u.first_name} ${u.last_name}`.trim() : u.username} ({u.is_teacher ? 'Teacher' : ''}{u.is_teacher && u.is_mentor ? '/' : ''}{u.is_mentor ? 'Mentor' : ''})
                        </option>
                      ))}
                    </select>
                    {eligibleUsers.length === 0 && (
                      <p className="text-[10px] text-zinc-600 mt-1">No teacher/mentor accounts found -- create one under Users first.</p>
                    )}
                  </div>
                  <div>
                    <label className="block text-[10px] font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Role</label>
                    <select
                      value={newInstructorRole}
                      onChange={(e) => setNewInstructorRole(e.target.value)}
                      className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                    >
                      <option value="TEACHER">Teacher</option>
                      <option value="MENTOR">Mentor</option>
                      <option value="ASSISTANT">Assistant</option>
                    </select>
                  </div>
                  <label className="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={newInstructorPrimary}
                      onChange={(e) => setNewInstructorPrimary(e.target.checked)}
                      className="accent-[#facc15]"
                    />
                    Set as primary instructor
                  </label>
                  {instructorError && <p className="text-xs text-red-400">{instructorError}</p>}
                  <div className="flex justify-end gap-2 pt-1">
                    <button
                      type="button"
                      onClick={() => { setShowAddInstructorForm(false); setInstructorError(""); }}
                      className="px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
                    >
                      Cancel
                    </button>
                    <button
                      type="submit"
                      disabled={instructorSaving || !newInstructorUserId}
                      className="px-4 py-1.5 text-xs bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50"
                    >
                      {instructorSaving ? "Adding..." : "Add Instructor"}
                    </button>
                  </div>
                </motion.form>
              )}
            </AnimatePresence>
          </div>
        </div>

        {/* Right Column: Curriculum Builder */}
        <div className="lg:col-span-8">
          <div className="bg-[#0e0e12] border border-white/10 rounded-2xl p-6 shadow-xl">
            <div className="flex items-center justify-between mb-8 pb-5 border-b border-white/[0.08] flex-wrap gap-4">
              <div className="flex items-center gap-3">
                <div className="w-10 h-10 rounded-xl bg-[#facc15]/10 border border-[#facc15]/20 flex items-center justify-center text-[#facc15] shadow-sm">
                  <Layers className="w-5 h-5" />
                </div>
                <div>
                  <h2 className="text-xl font-bold text-white tracking-tight">Curriculum Builder</h2>
                  <p className="text-xs text-zinc-400 mt-0.5">Organize modules, upload video lessons, and manage audio tracks</p>
                </div>
              </div>
              <button 
                onClick={() => setShowAddModule(true)}
                className="px-4 py-2 bg-[#facc15] hover:bg-yellow-400 text-black font-semibold rounded-xl transition-all text-xs flex items-center gap-1.5 shadow-sm hover:shadow-yellow-500/20"
              >
                <Plus className="w-4 h-4 stroke-[2.5]" />
                Add Module
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
                <div className="text-center py-16 text-zinc-500 border border-dashed border-white/10 rounded-2xl bg-zinc-950/40">
                  <Layers className="w-8 h-8 text-zinc-600 mx-auto mb-2" />
                  <p className="text-sm font-medium text-zinc-400">No modules yet</p>
                  <p className="text-xs text-zinc-600 mt-1">Click "Add Module" to start structuring your course curriculum.</p>
                </div>
              ) : (
                course.modules?.map((module: any, idx: number) => (
                  <div key={module.id} className="bg-zinc-950/70 border border-white/10 rounded-2xl overflow-hidden shadow-sm">
                    {/* Module Top Bar */}
                    <div className="bg-[#141419] px-5 py-3.5 border-b border-white/[0.08] flex items-center justify-between flex-wrap gap-3">
                      {editingModuleId === module.id ? (
                        <div className="flex items-center gap-2 flex-1">
                          <input
                            type="text"
                            value={editModuleTitle}
                            onChange={(e) => setEditModuleTitle(e.target.value)}
                            className="bg-zinc-900 border border-white/20 rounded-xl px-3 py-1.5 text-sm text-white focus:outline-none focus:border-[#facc15] flex-1 max-w-md"
                            autoFocus
                          />
                          <button
                            onClick={() => handleRenameModule(module.id)}
                            className="text-xs font-bold px-3 py-1.5 bg-[#facc15] text-black rounded-lg hover:bg-yellow-400 transition-colors"
                          >
                            Save
                          </button>
                          <button
                            onClick={() => setEditingModuleId(null)}
                            className="text-xs text-zinc-400 hover:text-white px-2 py-1.5"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-3 min-w-0 flex-1">
                          <span className="px-2 py-0.5 rounded-md bg-white/[0.06] border border-white/10 text-[10px] font-mono font-bold text-zinc-300 uppercase tracking-wider shrink-0">
                            Module {idx + 1}
                          </span>
                          <h3 className="text-sm font-bold text-white tracking-wide truncate">
                            {module.title}
                          </h3>
                          <span className="text-[11px] text-zinc-500 font-medium shrink-0">
                            • {module.lessons?.length || 0} {module.lessons?.length === 1 ? 'lesson' : 'lessons'}
                          </span>

                          {/* Rename / Delete Module Triggers */}
                          <div className="flex items-center gap-0.5 ml-1">
                            <button
                              onClick={() => {
                                setEditingModuleId(module.id);
                                setEditModuleTitle(module.title);
                              }}
                              className="text-zinc-500 hover:text-[#facc15] p-1.5 transition-colors rounded-lg hover:bg-white/5"
                              title="Rename module"
                            >
                              <Pencil className="w-3.5 h-3.5" />
                            </button>
                            <button
                              onClick={() => handleDeleteModule(module.id, module.title)}
                              className="text-zinc-500 hover:text-red-400 p-1.5 transition-colors rounded-lg hover:bg-white/5"
                              title="Delete module"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        </div>
                      )}

                      <div className="flex items-center gap-2">
                        {/* Module Order Arrows */}
                        <div className="flex items-center bg-zinc-900 border border-white/10 rounded-lg p-0.5">
                          <button
                            type="button"
                            disabled={idx === 0}
                            onClick={() => handleMoveModule(idx, 'up')}
                            className="p-1 text-zinc-400 hover:text-white disabled:opacity-20 disabled:hover:text-zinc-600 transition-all"
                            title="Move Section Up"
                          >
                            <ChevronUp className="w-3.5 h-3.5" />
                          </button>
                          <div className="w-[1px] h-3 bg-white/10" />
                          <button
                            type="button"
                            disabled={idx === (course?.modules?.length || 1) - 1}
                            onClick={() => handleMoveModule(idx, 'down')}
                            className="p-1 text-zinc-400 hover:text-white disabled:opacity-20 disabled:hover:text-zinc-600 transition-all"
                            title="Move Section Down"
                          >
                            <ChevronDown className="w-3.5 h-3.5" />
                          </button>
                        </div>

                        <button 
                          onClick={() => setAddingLessonToModule(addingLessonToModule === module.id ? null : module.id)}
                          className="text-xs font-semibold px-3 py-1.5 bg-[#facc15]/10 text-[#facc15] hover:bg-[#facc15]/20 border border-[#facc15]/20 hover:border-[#facc15]/40 rounded-lg transition-all flex items-center gap-1.5 shadow-sm"
                        >
                          <Plus className="w-3.5 h-3.5 stroke-[2.5]" />
                          Add Video
                        </button>
                      </div>
                    </div>

                    <div className="p-3.5 space-y-2.5">
                      {module.lessons?.map((lesson: any, lIdx: number) => {
                        const isHighlighted = highlightedLessonId === lesson.id;
                        const cleanFileName = getCleanVideoFileName(lesson.video_file);
                        return (
                          <div 
                            key={lesson.id} 
                            className={`p-3.5 rounded-xl transition-all duration-200 group ${
                              isHighlighted 
                                ? 'bg-[#facc15]/10 border border-[#facc15] shadow-lg shadow-[#facc15]/10' 
                                : 'bg-[#101015] hover:bg-[#15151c] border border-white/[0.07] hover:border-white/[0.15]'
                            }`}
                          >
                            <div className="flex items-center justify-between gap-4">
                              {/* Left: Reorder Stepper + Media Thumbnail + Index + Title & Subtitle */}
                              <div className="flex items-center gap-3 min-w-0 flex-1">
                                {/* Compact Vertical Stepper */}
                                <div className="flex flex-col items-center bg-zinc-900 border border-white/10 rounded-md p-0.5 shrink-0">
                                  <button
                                    type="button"
                                    disabled={lIdx === 0}
                                    onClick={() => handleMoveLesson(idx, lIdx, 'up')}
                                    className="p-0.5 text-zinc-400 hover:text-[#facc15] disabled:opacity-20 disabled:hover:text-zinc-600 transition-colors"
                                    title="Move Lesson Up"
                                  >
                                    <ChevronUp className="w-3 h-3" />
                                  </button>
                                  <button
                                    type="button"
                                    disabled={lIdx === (module.lessons?.length || 1) - 1}
                                    onClick={() => handleMoveLesson(idx, lIdx, 'down')}
                                    className="p-0.5 text-zinc-400 hover:text-[#facc15] disabled:opacity-20 disabled:hover:text-zinc-600 transition-colors"
                                    title="Move Lesson Down"
                                  >
                                    <ChevronDown className="w-3 h-3" />
                                  </button>
                                </div>

                                {/* Professional Video Media Icon Badge */}
                                <div className="w-9 h-9 rounded-lg bg-zinc-900 border border-white/10 flex items-center justify-center text-[#facc15] shrink-0 group-hover:border-[#facc15]/30 transition-colors shadow-inner">
                                  <Video className="w-4 h-4 text-[#facc15]" />
                                </div>

                                {/* Lesson Number Pill */}
                                <span className="font-mono text-xs font-bold text-zinc-400 shrink-0 w-5 text-center">
                                  {lIdx + 1 < 10 ? `0${lIdx + 1}` : lIdx + 1}
                                </span>

                                {/* Title and Video Info */}
                                <div className="min-w-0 flex-1">
                                  <h4 className="font-semibold text-sm text-white group-hover:text-zinc-100 transition-colors truncate">
                                    {lesson.title || `Lesson ${lIdx + 1}`}
                                  </h4>

                                  {/* Professional Metadata Row */}
                                  <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                                    {/* Video File Name Pill */}
                                    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md bg-zinc-900 border border-white/[0.08] text-[11px] text-zinc-300 font-mono">
                                      <Film className="w-3 h-3 text-[#facc15]/80 shrink-0" />
                                      <span className="truncate max-w-[220px]" title={lesson.video_file || ""}>
                                        {cleanFileName || (lesson.video_file ? String(lesson.video_file).split('/').pop() : "No video file")}
                                      </span>
                                    </span>

                                    {/* Language Badge */}
                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-zinc-900 border border-white/[0.08] text-[10px] text-zinc-400 font-medium">
                                      <Globe className="w-2.5 h-2.5 text-zinc-500 shrink-0" />
                                      English · Original
                                    </span>

                                    {/* Audio Tracks Badge */}
                                    {lesson.translated_audios?.filter((a: any) => a.status === 'completed').length > 0 && (
                                      <span className="inline-flex items-center gap-1 px-2 py-0.5 bg-[#facc15]/10 border border-[#facc15]/25 rounded-md text-[10px] font-semibold text-[#facc15]">
                                        <Headphones className="w-2.5 h-2.5 shrink-0" />
                                        {lesson.translated_audios.filter((a: any) => a.status === 'completed').length} Audio Track{lesson.translated_audios.filter((a: any) => a.status === 'completed').length > 1 ? 's' : ''}
                                      </span>
                                    )}
                                  </div>
                                </div>
                              </div>

                              {/* Right: Clean, Balanced Action Buttons */}
                              <div className="flex items-center gap-2 shrink-0">
                                {/* Edit Button */}
                                <button
                                  type="button"
                                  onClick={() => {
                                    setEditingLessonId(editingLessonId === lesson.id ? null : lesson.id);
                                    setEditLessonData({
                                      title: lesson.title,
                                      description: lesson.description || "",
                                      transcript: lesson.transcript || "",
                                      timed_transcript: lesson.timed_transcript || "",
                                      moduleId: module.id
                                    });
                                  }}
                                  className="px-2.5 py-1.5 text-xs font-medium bg-zinc-900 hover:bg-zinc-800 text-zinc-200 hover:text-white rounded-lg border border-white/10 hover:border-white/20 transition-all flex items-center gap-1.5 shadow-sm"
                                >
                                  <Pencil className="w-3 h-3 text-zinc-400" />
                                  <span>Edit</span>
                                </button>

                                {/* Audio Tracks Button */}
                                <button
                                  type="button"
                                  onClick={() => audioManagerLessonId === lesson.id ? closeAudioManager() : openAudioManager(lesson.id)}
                                  className="px-2.5 py-1.5 text-xs font-medium bg-[#facc15]/10 hover:bg-[#facc15]/20 text-[#facc15] rounded-lg border border-[#facc15]/20 hover:border-[#facc15]/40 transition-all flex items-center gap-1.5 shadow-sm"
                                  title="Manage audio tracks"
                                >
                                  <Headphones className="w-3.5 h-3.5" />
                                  <span>Audio</span>
                                </button>

                                {/* Delete Button */}
                                <button
                                  type="button"
                                  onClick={() => handleDeleteLesson(lesson.id, lesson.title)}
                                  className="p-1.5 text-zinc-500 hover:text-red-400 hover:bg-red-500/10 border border-transparent hover:border-red-500/20 rounded-lg transition-all"
                                  title="Delete lesson"
                                >
                                  <Trash2 className="w-3.5 h-3.5" />
                                </button>
                              </div>
                            </div>

                          {/* Audio Track Manager Panel */}
                          <AnimatePresence>
                            {audioManagerLessonId === lesson.id && (
                              <motion.div
                                initial={{ opacity: 0, height: 0 }}
                                animate={{ opacity: 1, height: 'auto' }}
                                exit={{ opacity: 0, height: 0 }}
                                className="bg-[#09090c] border border-white/10 rounded-xl p-4 mt-3 space-y-3 overflow-hidden shadow-inner"
                              >
                                <div className="flex items-center justify-between">
                                  <div className="flex items-center gap-2">
                                    <div className="w-6 h-6 rounded-md bg-[#facc15]/10 border border-[#facc15]/20 flex items-center justify-center text-[#facc15]">
                                      <Headphones className="w-3 h-3" />
                                    </div>
                                    <h4 className="text-xs font-bold text-white uppercase tracking-wider">Multi-Language Audio Tracks</h4>
                                  </div>
                                </div>
                                <p className="text-[11px] text-zinc-400">
                                  Upload alternate-language audio tracks. Students can switch audio languages in the player while video playback continues seamlessly.
                                </p>

                                {/* English (always present, original video audio) */}
                                <div className="flex items-center justify-between bg-zinc-900/60 border border-white/5 rounded-lg px-3.5 py-2.5">
                                  <div className="flex items-center gap-2">
                                    <Globe className="w-3.5 h-3.5 text-blue-400" />
                                    <div>
                                      <div className="text-xs font-semibold text-white">English</div>
                                      <div className="text-[10px] text-zinc-500">Original video master audio</div>
                                    </div>
                                  </div>
                                  <span className="px-2 py-0.5 rounded bg-blue-500/10 text-blue-400 border border-blue-500/20 text-[10px] font-semibold">Primary</span>
                                </div>

                                {lesson.translated_audios?.map((audio: any) => (
                                  <div key={audio.id} className="flex items-center justify-between bg-zinc-900/60 border border-white/5 rounded-lg px-3.5 py-2.5">
                                    <div className="flex items-center gap-2">
                                      <Headphones className="w-3.5 h-3.5 text-[#facc15]" />
                                      <div>
                                        <div className="text-xs font-semibold text-white">
                                          {audio.language_name || languageDisplayName(audio.language_code)}
                                        </div>
                                        <div className="text-[10px] text-zinc-500">
                                          {audio.status === 'completed' ? 'Uploaded & active' : audio.status === 'processing' ? 'Processing audio…' : 'Upload failed'}
                                        </div>
                                      </div>
                                    </div>
                                    <div className="flex gap-2">
                                      <button
                                        type="button"
                                        onClick={() => {
                                          setReplacingAudioId(audio.id);
                                          setShowAddAudioForm(true);
                                          setNewAudioLangCode(audio.language_code.split('-')[0]);
                                          setNewAudioFile(null);
                                          setAudioUploadError("");
                                        }}
                                        className="text-xs font-medium px-2.5 py-1 bg-zinc-800 hover:bg-zinc-700 text-white rounded-md transition-colors flex items-center gap-1"
                                      >
                                        <RefreshCw className="w-3 h-3 text-zinc-400" />
                                        Replace
                                      </button>
                                      <button
                                        type="button"
                                        disabled={deletingAudioId === audio.id}
                                        onClick={() => handleDeleteAudio(lesson.id, audio.id, audio.language_name || languageDisplayName(audio.language_code))}
                                        className="text-xs font-medium px-2.5 py-1 bg-red-500/10 hover:bg-red-500/20 text-red-400 rounded-md transition-colors disabled:opacity-50 flex items-center gap-1"
                                      >
                                        <Trash2 className="w-3 h-3" />
                                        {deletingAudioId === audio.id ? "Removing…" : "Delete"}
                                      </button>
                                    </div>
                                  </div>
                                ))}

                                {!showAddAudioForm ? (
                                  <button
                                    type="button"
                                    onClick={() => { setShowAddAudioForm(true); setReplacingAudioId(null); setNewAudioLangCode(""); setNewAudioFile(null); setAudioUploadError(""); }}
                                    className="w-full py-2 border border-dashed border-white/15 hover:border-[#facc15]/50 text-zinc-400 hover:text-[#facc15] rounded-xl text-xs font-semibold transition-colors flex items-center justify-center gap-1.5"
                                  >
                                    <Plus className="w-3.5 h-3.5" />
                                    Add Audio Track
                                  </button>
                                ) : (
                                  <form
                                    onSubmit={(e) => submitAudioUpload(e, lesson.id, replacingAudioId)}
                                    className="bg-zinc-900/70 border border-[#facc15]/20 rounded-xl p-4 space-y-3"
                                  >
                                    <div className="flex items-center gap-2 text-xs font-bold text-white">
                                      <Headphones className="w-3.5 h-3.5 text-[#facc15]" />
                                      <span>{replacingAudioId ? "Replace Audio Track" : "Add New Audio Track"}</span>
                                    </div>
                                    <div>
                                      <label className="block text-[10px] font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Language</label>
                                      <select
                                        required
                                        disabled={!!replacingAudioId}
                                        value={newAudioLangCode}
                                        onChange={(e) => setNewAudioLangCode(e.target.value)}
                                        className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] disabled:opacity-60"
                                      >
                                        <option value="" disabled>Select Language</option>
                                        {SUPPORTED_LANGUAGES.map(l => (
                                          <option key={l.code} value={l.code}>{l.name}</option>
                                        ))}
                                      </select>
                                    </div>
                                    <div>
                                      <label className="block text-[10px] font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Audio File</label>
                                      <input
                                        type="file"
                                        required
                                        accept="audio/mpeg,audio/mp3,audio/wav,audio/x-m4a,audio/aac,audio/ogg,audio/flac,.mp3,.wav,.m4a,.aac,.ogg,.flac"
                                        onChange={(e) => setNewAudioFile(e.target.files?.[0] || null)}
                                        className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-sm text-zinc-400 focus:outline-none focus:border-[#facc15] file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-[#facc15] file:text-black hover:file:bg-yellow-500 cursor-pointer"
                                      />
                                    </div>
                                    {audioUploadError && (
                                      <p className="text-xs text-red-400 flex items-center gap-1">
                                        <AlertCircle className="w-3.5 h-3.5 shrink-0" /> {audioUploadError}
                                      </p>
                                    )}
                                    <div className="flex justify-end gap-2 pt-1">
                                      <button
                                        type="button"
                                        onClick={() => { setShowAddAudioForm(false); setReplacingAudioId(null); setAudioUploadError(""); }}
                                        className="px-3 py-1.5 text-xs text-zinc-400 hover:text-white"
                                      >
                                        Cancel
                                      </button>
                                      <button
                                        type="submit"
                                        disabled={audioUploadLoading || !newAudioLangCode || !newAudioFile}
                                        className="px-4 py-1.5 text-xs bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50"
                                      >
                                        {audioUploadLoading ? "Uploading…" : replacingAudioId ? "Replace Audio" : "Upload Audio"}
                                      </button>
                                    </div>
                                  </form>
                                )}
                              </motion.div>
                            )}
                          </AnimatePresence>

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

                                <div className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Lesson Content</div>

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
                                  <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Module (Section)</label>
                                  <select
                                    value={editLessonData.moduleId || module.id}
                                    onChange={(e) => setEditLessonData({...editLessonData, moduleId: Number(e.target.value)})}
                                    className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                                  >
                                    {course?.modules?.map((m: any, mIdx: number) => (
                                      <option key={m.id} value={m.id}>
                                        Module {mIdx + 1}: {m.title}
                                      </option>
                                    ))}
                                  </select>
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

                                {/* Legacy AI-dubbing fields: not part of the V1 manual audio-upload
                                    workflow (see Audio Tracks above). Kept collapsed so they
                                    don't confuse admins, but preserved for the old AI pipeline (V2). */}
                                <details className="group bg-zinc-900/40 border border-white/10 rounded-xl overflow-hidden">
                                  <summary className="cursor-pointer select-none list-none px-4 py-3 flex items-center justify-between text-xs font-semibold text-zinc-400 hover:text-zinc-200 transition-colors">
                                    <span>Advanced / Legacy AI Dubbing (V2)</span>
                                    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="transition-transform group-open:rotate-180"><polyline points="6 9 12 15 18 9"/></svg>
                                  </summary>
                                  <div className="px-4 pb-4 pt-1 space-y-4 border-t border-white/10">
                                    <p className="text-[10px] text-zinc-600 leading-relaxed">
                                      Not needed for V1 — audio for students is managed entirely via
                                      "Audio Tracks" above. These fields only feed the legacy AI
                                      auto-dubbing pipeline, kept here for future use.
                                    </p>

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

                                    <div className="bg-[#facc15]/5 border border-[#facc15]/20 rounded-xl p-4">
                                      <div className="flex items-center gap-2 mb-2">
                                        <Clock className="w-3.5 h-3.5 text-[#facc15]" />
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
                                      <p className="text-[10px] text-zinc-500 mt-2 flex items-center gap-1.5">
                                        <Sparkles className="w-3 h-3 text-[#facc15] shrink-0" />
                                        <span>Leave blank to let Whisper AI auto-detect timings (less accurate). Fill this in for perfect sync.</span>
                                      </p>
                                    </div>
                                  </div>
                                </details>

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
                      );
                    })}

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

                            <div className="text-[10px] font-bold text-zinc-500 uppercase tracking-widest">Lesson Content</div>

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

                            <div>
                              <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Upload Original Video (MP4) *</label>
                              <input
                                type="file"
                                accept="video/mp4,video/x-m4v,video/*"
                                required
                                onChange={(e) => setLessonData({...lessonData, video_file: e.target.files?.[0] || null})}
                                className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-sm text-zinc-400 focus:outline-none focus:border-[#facc15] file:mr-4 file:py-2 file:px-4 file:rounded-xl file:border-0 file:text-sm file:font-semibold file:bg-[#facc15] file:text-black hover:file:bg-yellow-500 cursor-pointer"
                              />
                              {lessonData.video_file && (
                                <div className="mt-2 text-xs flex flex-wrap items-center justify-between gap-2 bg-zinc-900/60 p-2.5 rounded-xl border border-white/5">
                                  <span className="text-zinc-300 flex items-center gap-1.5">
                                    <Video className="w-3.5 h-3.5 text-[#facc15] shrink-0" />
                                    <strong className="text-white">{lessonData.video_file.name}</strong> ({(lessonData.video_file.size / (1024 * 1024)).toFixed(1)} MB)
                                  </span>
                                  {lessonData.video_file.size > 2 * 1024 * 1024 * 1024 ? (
                                    <span className="text-red-400 bg-red-400/10 px-2 py-0.5 rounded text-[11px] border border-red-400/20 font-medium flex items-center gap-1">
                                      <AlertCircle className="w-3 h-3 shrink-0" />
                                      Exceeds 2 GB limit ({(lessonData.video_file.size / (1024 * 1024 * 1024)).toFixed(2)} GB). Please compress or select a video under 2 GB.
                                    </span>
                                  ) : (
                                    <span className="text-emerald-400 bg-emerald-400/10 px-2 py-0.5 rounded text-[11px] border border-emerald-400/20 font-medium flex items-center gap-1">
                                      <Zap className="w-3 h-3 shrink-0" />
                                      Direct S3 Upload enabled (up to 2 GB)
                                    </span>
                                  )}
                                </div>
                              )}
                              <p className="text-[10px] text-zinc-500 mt-2 flex items-center gap-1.5">
                                <Headphones className="w-3 h-3 text-zinc-400 shrink-0" />
                                <span>After saving, use "Audio" on the lesson to add translated/dubbed audio tracks.</span>
                              </p>
                            </div>

                            {/* Legacy AI-dubbing fields: not part of the V1 manual audio-upload
                                workflow (see Audio Tracks). Kept collapsed so they don't
                                confuse admins, but preserved for the old AI pipeline (V2). */}
                            <details className="group bg-zinc-900/40 border border-white/10 rounded-xl overflow-hidden">
                              <summary className="cursor-pointer select-none list-none px-4 py-3 flex items-center justify-between text-xs font-semibold text-zinc-400 hover:text-zinc-200 transition-colors">
                                <span>Advanced / Legacy AI Dubbing (V2)</span>
                                <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="transition-transform group-open:rotate-180"><polyline points="6 9 12 15 18 9"/></svg>
                              </summary>
                              <div className="px-4 pb-4 pt-1 space-y-4 border-t border-white/10">
                                <p className="text-[10px] text-zinc-600 leading-relaxed">
                                  Not needed for V1 — audio for students is managed entirely via
                                  "Audio" after this lesson is created. These fields only
                                  feed the legacy AI auto-dubbing pipeline, kept here for future use.
                                </p>

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

                                <div className="bg-[#facc15]/5 border border-[#facc15]/20 rounded-xl p-4">
                                  <div className="flex items-center gap-2 mb-2">
                                    <Clock className="w-3.5 h-3.5 text-[#facc15]" />
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
                                  <p className="text-[10px] text-zinc-500 mt-2 flex items-center gap-1.5">
                                    <Sparkles className="w-3 h-3 text-[#facc15] shrink-0" />
                                    <span>Leave blank to let Whisper AI auto-detect timings (less accurate). Fill this in for perfect sync.</span>
                                  </p>
                                </div>
                              </div>
                            </details>

                            {uploadProgress !== null && uploadProgress > 0 && (
                              <div className="mt-4 p-3 bg-zinc-900/80 border border-white/10 rounded-xl space-y-2">
                                <div className="flex justify-between text-xs font-semibold text-zinc-300">
                                  <span>Uploading video...</span>
                                  <span className="text-[#facc15]">{uploadProgress}%</span>
                                </div>
                                <div className="w-full bg-zinc-800 rounded-full h-2 overflow-hidden">
                                  <div 
                                    className="bg-[#facc15] h-full transition-all duration-150"
                                    style={{ width: `${uploadProgress}%` }}
                                  />
                                </div>
                              </div>
                            )}

                            <div className="flex justify-end gap-3 pt-4 border-t border-white/10 mt-4">
                              <button 
                                type="button" 
                                disabled={lessonLoading}
                                onClick={() => setAddingLessonToModule(null)}
                                className="px-4 py-2 text-sm text-zinc-400 hover:text-white disabled:opacity-50"
                              >
                                Cancel
                              </button>
                              <button 
                                type="submit"
                                disabled={lessonLoading}
                                className="px-5 py-2 text-sm bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50 flex items-center gap-2"
                              >
                                {lessonLoading ? (
                                  <>
                                    <span className="inline-block w-4 h-4 border-2 border-black border-t-transparent rounded-full animate-spin" />
                                    <span>{uploadProgress !== null && uploadProgress < 100 ? `Uploading ${uploadProgress}%...` : "Saving Lesson..."}</span>
                                  </>
                                ) : (
                                  "Save Lesson"
                                )}
                              </button>
                            </div>
                          </motion.form>
                        )}
                      </AnimatePresence>

                      {/* Phase 4.7: read-only assignments list -- authoring an
                          Assignment is a Django-admin job (no create/edit API
                          exists), so this is purely a "jump to the grading
                          queue" surface for whichever published assignments
                          already exist in this module. */}
                      {module.assignments && module.assignments.length > 0 && (
                        <div className="mt-4 pt-4 border-t border-white/5 space-y-2">
                          <h4 className="text-xs font-bold text-zinc-500 uppercase tracking-wider mb-1">Assignments</h4>
                          {module.assignments.map((assignment: any) => (
                            <div key={assignment.id} className="flex items-center justify-between bg-zinc-900/50 border border-white/5 p-3 rounded-xl">
                              <div className="min-w-0">
                                <div className="text-sm font-medium text-white truncate">{assignment.title}</div>
                                <div className="text-[11px] text-zinc-500">Max marks: {assignment.max_marks}</div>
                              </div>
                              <Link
                                href={`/admin/assignments/${assignment.id}`}
                                className="shrink-0 text-xs font-bold px-3 py-1.5 bg-[#facc15]/10 hover:bg-[#facc15]/20 text-[#facc15] rounded-xl transition-colors"
                              >
                                View Submissions
                              </Link>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
      {/* Toast Notification */}
      {toastMessage && (
        <div className="fixed bottom-6 right-6 z-50 flex items-center gap-3 px-4 py-3 bg-zinc-900/95 border border-[#facc15]/40 text-white rounded-2xl shadow-2xl shadow-black/80 text-sm backdrop-blur-md">
          <div className="w-5 h-5 rounded-full bg-[#facc15]/20 border border-[#facc15]/40 flex items-center justify-center text-[#facc15] shrink-0">
            <Check className="w-3 h-3 stroke-[3]" />
          </div>
          <span className="font-medium text-zinc-200">{toastMessage}</span>
          <button 
            type="button"
            onClick={() => setToastMessage(null)}
            className="ml-2 text-zinc-400 hover:text-white p-1 rounded-lg hover:bg-white/10 transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
