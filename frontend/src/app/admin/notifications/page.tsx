"use client";

/**
 * Admin Dashboard Completion. Full CRUD over Announcements
 * (GET/POST /api/announcements/, PATCH/DELETE /api/announcements/{id}/).
 * Staff/superuser sees ALL announcements -- no query params needed, this
 * viewset's get_queryset reads none for a staff caller.
 *
 * Important: this is a completely separate, simpler mechanism from the
 * app's Notification/push system -- confirmed there is no signal, task,
 * or other connection between them. Publishing an announcement here does
 * NOT send a push notification or create a Notification row for anyone;
 * it only becomes visible the next time a student opens the app, and only
 * if is_published=true AND (course is null, shown to everyone) OR (the
 * student is enrolled in that course). The banner below states this
 * plainly so nobody mistakes this for the push/notification system.
 */

import { useEffect, useState } from "react";
import { Plus, X, AlertTriangle, Trash2, Megaphone, Pencil } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Announcement {
  id: number;
  sender: number | null;
  sender_name: string | null;
  course: number | null;
  title: string;
  content: string;
  created_at: string;
  updated_at: string;
  is_published: boolean;
}

function getCsrfToken(): string {
  let csrfToken = "";
  if (typeof document !== "undefined" && document.cookie) {
    const cookies = document.cookie.split(";");
    for (let i = 0; i < cookies.length; i++) {
      const cookie = cookies[i].trim();
      if (cookie.startsWith("csrftoken=")) {
        csrfToken = decodeURIComponent(cookie.substring("csrftoken=".length));
        break;
      }
    }
  }
  return csrfToken;
}

function extractErrorMessage(data: unknown): string {
  if (!data) return "Request failed.";
  if (typeof data === "string") return data;
  if (typeof data === "object") {
    const obj = data as Record<string, unknown>;
    const parts: string[] = [];
    for (const key of Object.keys(obj)) {
      const val = obj[key];
      const text = Array.isArray(val) ? val.join(" ") : String(val);
      parts.push(key === "detail" || key === "non_field_errors" ? text : `${key}: ${text}`);
    }
    if (parts.length) return parts.join(" ");
  }
  return "Request failed.";
}

export default function AdminNotificationsPage() {
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [showModal, setShowModal] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [formTitle, setFormTitle] = useState("");
  const [formContent, setFormContent] = useState("");
  const [formCourse, setFormCourse] = useState("");
  const [formIsPublished, setFormIsPublished] = useState(false);
  const [formSubmitting, setFormSubmitting] = useState(false);
  const [formError, setFormError] = useState("");

  const [deletingId, setDeletingId] = useState<number | null>(null);
  const [togglingId, setTogglingId] = useState<number | null>(null);

  const fetchAnnouncements = async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/announcements/`, { credentials: "include" });
      if (res.ok) {
        const data: Announcement[] = await res.json();
        setAnnouncements(Array.isArray(data) ? data : []);
      } else {
        setError("Failed to fetch announcements.");
      }
    } catch {
      setError("Network error fetching announcements.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAnnouncements();
  }, []);

  const openCreateModal = () => {
    setEditingId(null);
    setFormTitle("");
    setFormContent("");
    setFormCourse("");
    setFormIsPublished(false);
    setFormError("");
    setShowModal(true);
  };

  const openEditModal = (a: Announcement) => {
    setEditingId(a.id);
    setFormTitle(a.title);
    setFormContent(a.content);
    setFormCourse(a.course === null ? "" : String(a.course));
    setFormIsPublished(a.is_published);
    setFormError("");
    setShowModal(true);
  };

  const closeModal = () => {
    setShowModal(false);
    setFormError("");
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!formTitle.trim() || !formContent.trim()) {
      setFormError("Title and content are both required.");
      return;
    }

    setFormSubmitting(true);
    setFormError("");
    try {
      const body = {
        title: formTitle,
        content: formContent,
        course: formCourse ? parseInt(formCourse, 10) : null,
        is_published: formIsPublished,
      };

      const url = editingId ? `${API_BASE}/api/announcements/${editingId}/` : `${API_BASE}/api/announcements/`;
      const method = editingId ? "PATCH" : "POST";

      const res = await fetch(url, {
        method,
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
        body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => null);
      if (res.ok) {
        setShowModal(false);
        await fetchAnnouncements();
      } else {
        setFormError(extractErrorMessage(data));
      }
    } catch {
      setFormError("Network error saving announcement.");
    } finally {
      setFormSubmitting(false);
    }
  };

  const handleTogglePublish = async (a: Announcement) => {
    setTogglingId(a.id);
    try {
      const res = await fetch(`${API_BASE}/api/announcements/${a.id}/`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
        body: JSON.stringify({ is_published: !a.is_published }),
      });
      if (res.ok) {
        await fetchAnnouncements();
      } else {
        const data = await res.json().catch(() => null);
        alert(extractErrorMessage(data));
      }
    } catch {
      alert("Network error updating announcement.");
    } finally {
      setTogglingId(null);
    }
  };

  const handleDelete = async (a: Announcement) => {
    if (
      !confirm(
        "Delete this announcement permanently? Students who could currently see it will no longer be able to."
      )
    )
      return;

    setDeletingId(a.id);
    try {
      const res = await fetch(`${API_BASE}/api/announcements/${a.id}/`, {
        method: "DELETE",
        credentials: "include",
        headers: { "X-CSRFToken": getCsrfToken() },
      });
      if (res.ok || res.status === 204) {
        await fetchAnnouncements();
      } else {
        const data = await res.json().catch(() => null);
        alert(extractErrorMessage(data));
      }
    } catch {
      alert("Network error deleting announcement.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="max-w-6xl mx-auto pb-20 font-sans text-white">
      {/* Header */}
      <div className="mb-8 flex flex-col md:flex-row md:items-center md:justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold">Announcements</h1>
          <p className="text-zinc-400 text-sm mt-1">Post announcements shown to students inside the app.</p>
        </div>
        <button
          onClick={openCreateModal}
          className="px-5 py-3 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-all flex items-center gap-2 text-sm shrink-0"
        >
          <Plus className="w-4 h-4" />
          New Announcement
        </button>
      </div>

      {/* Honest mental-model banner -- this is deliberately NOT the push
          notification system, see file header comment. */}
      <div className="bg-blue-500/10 border border-blue-500/20 text-blue-300 p-4 rounded-xl mb-6 text-xs flex items-start gap-2">
        <Megaphone className="w-4 h-4 shrink-0 mt-0.5" />
        <span>
          Published announcements appear inside a student&apos;s app the next time they view it (only if the
          announcement targets everyone or a course they&apos;re enrolled in) &mdash; this does not send a push
          notification, and is completely separate from the app&apos;s Notification system.
        </span>
      </div>

      {error && <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">{error}</div>}

      {/* Table */}
      <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs">
            <thead>
              <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                <th className="p-4 font-semibold">Title</th>
                <th className="p-4 font-semibold">Audience</th>
                <th className="p-4 font-semibold text-center">Published</th>
                <th className="p-4 font-semibold">Sender</th>
                <th className="p-4 font-semibold">Created At</th>
                <th className="p-4 font-semibold text-center">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5 text-zinc-300">
              {loading ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                      <span className="text-sm">Loading announcements...</span>
                    </div>
                  </td>
                </tr>
              ) : announcements.length === 0 ? (
                <tr>
                  <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                    No announcements found.
                  </td>
                </tr>
              ) : (
                announcements.map((a) => (
                  <tr key={a.id} className="hover:bg-white/5 transition-colors">
                    <td className="p-4 font-bold text-white text-sm max-w-xs truncate">{a.title}</td>
                    <td className="p-4 text-zinc-400">
                      {a.course === null ? "All Students" : `Course #${a.course}`}
                    </td>
                    <td className="p-4 text-center">
                      <button
                        onClick={() => handleTogglePublish(a)}
                        disabled={togglingId === a.id}
                        title="Toggle published state"
                        className={`px-2.5 py-0.5 text-[9px] font-bold rounded-full disabled:opacity-50 ${
                          a.is_published
                            ? "bg-green-500/10 text-green-400 border border-green-500/20"
                            : "bg-yellow-500/10 text-[#facc15] border border-yellow-500/20"
                        }`}
                      >
                        {a.is_published ? "PUBLISHED" : "DRAFT"}
                      </button>
                    </td>
                    <td className="p-4 text-zinc-400">{a.sender_name || <span className="text-zinc-600 italic">System</span>}</td>
                    <td className="p-4 text-zinc-400">{new Date(a.created_at).toLocaleString()}</td>
                    <td className="p-4">
                      <div className="flex items-center justify-center gap-2">
                        <button
                          onClick={() => openEditModal(a)}
                          className="p-2 bg-white/5 hover:bg-white/10 rounded-xl transition-all inline-flex items-center justify-center text-zinc-400 hover:text-white"
                          title="Edit Announcement"
                        >
                          <Pencil className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleDelete(a)}
                          disabled={deletingId === a.id}
                          className="p-2 bg-red-500/10 hover:bg-red-500/20 rounded-xl transition-all inline-flex items-center justify-center text-red-400 disabled:opacity-50"
                          title="Delete Announcement"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Create / Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-zinc-900 border border-white/10 p-6 rounded-2xl w-full max-w-md shadow-2xl relative text-sm">
            <button
              onClick={closeModal}
              className="absolute top-4 right-4 p-2 bg-white/5 border border-white/5 hover:bg-white/10 rounded-full transition-colors text-zinc-400 hover:text-white"
            >
              <X className="w-4 h-4" />
            </button>

            <h2 className="text-xl font-bold mb-1">{editingId ? "Edit Announcement" : "New Announcement"}</h2>
            <p className="text-zinc-500 text-xs mb-6">
              {editingId ? "Update this announcement." : "Publishing does not send a push notification."}
            </p>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">
                  Title
                </label>
                <input
                  type="text"
                  value={formTitle}
                  onChange={(e) => setFormTitle(e.target.value)}
                  required
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15]"
                />
              </div>

              <div>
                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">
                  Content
                </label>
                <textarea
                  value={formContent}
                  onChange={(e) => setFormContent(e.target.value)}
                  required
                  rows={4}
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-[#facc15] resize-none"
                />
              </div>

              <div>
                <label className="text-[10px] font-semibold text-zinc-500 uppercase tracking-wider mb-1 block">
                  Course ID (optional)
                </label>
                <input
                  type="number"
                  value={formCourse}
                  onChange={(e) => setFormCourse(e.target.value)}
                  placeholder="Leave blank to show to ALL students"
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-3 py-2 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-600"
                />
                <p className="text-[10px] text-zinc-500 mt-1">
                  Leave blank to show to ALL students, or enter a course ID to scope it to that course&apos;s
                  enrolled students only.
                </p>
              </div>

              <label className="flex items-center gap-2 text-sm text-zinc-300 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={formIsPublished}
                  onChange={(e) => setFormIsPublished(e.target.checked)}
                  className="accent-[#facc15] w-4 h-4"
                />
                Published
              </label>

              {formError && (
                <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-2.5 rounded-xl text-xs flex items-start gap-2">
                  <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                  {formError}
                </div>
              )}

              <div className="flex gap-2 pt-2">
                <button
                  type="button"
                  onClick={closeModal}
                  disabled={formSubmitting}
                  className="flex-1 py-2.5 bg-white/5 hover:bg-white/10 text-zinc-300 font-semibold rounded-xl transition-all text-xs disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={formSubmitting}
                  className="flex-1 py-2.5 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-all flex items-center justify-center gap-2 text-xs disabled:opacity-50"
                >
                  {formSubmitting ? "Saving..." : editingId ? "Save Changes" : "Create Announcement"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
