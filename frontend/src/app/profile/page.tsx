"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { User, CheckCircle2, AlertTriangle } from "lucide-react";

// Profile/account editing gap fix. Reuses the EXISTING, already-live
// dj-rest-auth endpoint (GET/PATCH /api/auth/user/, backed by
// CustomUserDetailsSerializer) -- no new backend endpoint. Backend is the
// source of truth for which fields are actually editable: this page only
// ever sends the fields it renders as editable inputs, and the backend's
// own read_only_fields is what actually enforces the boundary (role/
// staff/superuser/email/phone_number are rejected server-side regardless
// of what a client sends).
interface ProfileData {
  pk: number;
  username: string;
  email: string;
  phone_number: string | null;
  first_name: string;
  last_name: string;
  parent_name: string | null;
  parent_phone: string | null;
}

export default function ProfilePage() {
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [form, setForm] = useState({ username: "", first_name: "", last_name: "", parent_name: "", parent_phone: "" });
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [success, setSuccess] = useState(false);

  const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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

  const populateForm = (data: ProfileData) => {
    setForm({
      username: data.username || "",
      first_name: data.first_name || "",
      last_name: data.last_name || "",
      parent_name: data.parent_name || "",
      parent_phone: data.parent_phone || "",
    });
  };

  const fetchProfile = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const res = await fetch(`${API}/api/auth/user/`, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        setProfile(data);
        populateForm(data);
      } else if (res.status === 401 || res.status === 403) {
        window.location.href = "/login";
      } else {
        setLoadError("Failed to load your profile.");
      }
    } catch (err) {
      setLoadError("Network error loading your profile.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchProfile();
  }, []);

  const handleReset = () => {
    if (profile) populateForm(profile);
    setFieldErrors({});
    setSaveError("");
    setSuccess(false);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaveError("");
    setFieldErrors({});
    setSuccess(false);
    try {
      const res = await fetch(`${API}/api/auth/user/`, {
        method: "PATCH",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify({
          username: form.username,
          first_name: form.first_name,
          last_name: form.last_name,
          parent_name: form.parent_name,
          parent_phone: form.parent_phone,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setProfile(data);
        populateForm(data);
        setSuccess(true);
      } else if (typeof data === "object" && data !== null) {
        // DRF's standard validation-error shape: {field: [messages]}.
        const errors: Record<string, string> = {};
        for (const key of Object.keys(data)) {
          if (Array.isArray(data[key])) errors[key] = data[key].join(" ");
        }
        if (Object.keys(errors).length > 0) {
          setFieldErrors(errors);
        } else {
          setSaveError(data.detail || "Failed to update your profile.");
        }
      } else {
        setSaveError("Failed to update your profile.");
      }
    } catch (err) {
      setSaveError("Network error updating your profile.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="min-h-screen bg-black text-white font-sans selection:bg-[#facc15] selection:text-black pb-24">
      <div className="max-w-xl mx-auto px-6 pt-32">
        <h1 className="text-4xl font-bold mb-2 flex items-center gap-3">
          <User className="w-8 h-8 text-[#facc15]" />
          My Profile
        </h1>
        <p className="text-zinc-400 text-sm mb-8">Update your account details.</p>

        {loading ? (
          <div className="flex items-center justify-center py-20">
            <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
          </div>
        ) : loadError ? (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl text-sm flex flex-col items-start gap-3">
            <span>{loadError}</span>
            <button onClick={fetchProfile} className="px-4 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-white text-xs font-semibold rounded-lg border border-white/10 transition-colors">
              Retry
            </button>
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="bg-zinc-900/50 border border-white/10 rounded-2xl p-6 space-y-5">
            {success && (
              <div className="bg-green-500/10 border border-green-500/20 text-green-400 p-3 rounded-xl text-sm flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 shrink-0" />
                Profile updated successfully.
              </div>
            )}
            {saveError && (
              <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-xl text-sm flex items-center gap-2">
                <AlertTriangle className="w-4 h-4 shrink-0" />
                {saveError}
              </div>
            )}

            <div>
              <label className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1.5 block">Username</label>
              <input
                type="text"
                value={form.username}
                onChange={(e) => setForm({ ...form, username: e.target.value })}
                className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
              />
              {fieldErrors.username && <p className="text-red-400 text-xs mt-1">{fieldErrors.username}</p>}
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1.5 block">First Name</label>
                <input
                  type="text"
                  value={form.first_name}
                  onChange={(e) => setForm({ ...form, first_name: e.target.value })}
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                />
                {fieldErrors.first_name && <p className="text-red-400 text-xs mt-1">{fieldErrors.first_name}</p>}
              </div>
              <div>
                <label className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1.5 block">Last Name</label>
                <input
                  type="text"
                  value={form.last_name}
                  onChange={(e) => setForm({ ...form, last_name: e.target.value })}
                  className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                />
                {fieldErrors.last_name && <p className="text-red-400 text-xs mt-1">{fieldErrors.last_name}</p>}
              </div>
            </div>

            {/* Read-only: email/phone_number are the OTP login identity and
                have no verification-on-change mechanism today -- shown for
                reference, disabled, matching CustomUserDetailsSerializer's
                own read_only_fields exactly. */}
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1.5 block">Email</label>
                <input
                  type="text"
                  value={profile?.email || ""}
                  disabled
                  className="w-full bg-black/20 border border-white/5 rounded-xl px-4 py-2.5 text-sm text-zinc-500 cursor-not-allowed"
                />
              </div>
              <div>
                <label className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1.5 block">Phone Number</label>
                <input
                  type="text"
                  value={profile?.phone_number || ""}
                  disabled
                  className="w-full bg-black/20 border border-white/5 rounded-xl px-4 py-2.5 text-sm text-zinc-500 cursor-not-allowed"
                />
              </div>
            </div>
            <p className="text-[11px] text-zinc-500 -mt-3">Email and phone number are tied to your sign-in and can't be changed here.</p>

            <div className="pt-2 border-t border-white/5">
              <p className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-3">Parent / Guardian Contact (optional)</p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1.5 block">Parent Name</label>
                  <input
                    type="text"
                    value={form.parent_name}
                    onChange={(e) => setForm({ ...form, parent_name: e.target.value })}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                  />
                  {fieldErrors.parent_name && <p className="text-red-400 text-xs mt-1">{fieldErrors.parent_name}</p>}
                </div>
                <div>
                  <label className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-1.5 block">Parent Phone</label>
                  <input
                    type="text"
                    value={form.parent_phone}
                    onChange={(e) => setForm({ ...form, parent_phone: e.target.value })}
                    className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-2.5 text-sm text-white focus:outline-none focus:border-[#facc15]"
                  />
                  {fieldErrors.parent_phone && <p className="text-red-400 text-xs mt-1">{fieldErrors.parent_phone}</p>}
                </div>
              </div>
            </div>

            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={handleReset}
                disabled={saving}
                className="flex-1 py-2.5 bg-white/5 hover:bg-white/10 text-zinc-300 font-semibold rounded-xl transition-all text-sm disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={saving}
                className="flex-1 py-2.5 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-all text-sm disabled:opacity-50"
              >
                {saving ? "Saving..." : "Save Changes"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
