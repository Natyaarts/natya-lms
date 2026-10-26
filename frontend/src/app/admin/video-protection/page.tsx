"use client";

import React, { useEffect, useState, useCallback } from "react";
import {
  Shield,
  ShieldCheck,
  ShieldAlert,
  Lock,
  Eye,
  Download,
  Smartphone,
  Clock,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Search,
  User,
  Mail,
  Phone,
  Calendar,
  Info,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function getCsrfToken() {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : "";
}

function getAuthHeaders(includeContentType = true): HeadersInit {
  const headers: Record<string, string> = {};
  if (includeContentType) {
    headers["Content-Type"] = "application/json";
  }
  const csrf = getCsrfToken();
  if (csrf) {
    headers["X-CSRFToken"] = csrf;
  }
  if (typeof window !== "undefined") {
    const token = localStorage.getItem("access_token") || localStorage.getItem("token");
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
  }
  return headers;
}

interface VideoProtectionSettingData {
  watermark_enabled: boolean;
  download_enabled: boolean;
  screen_capture_protection_enabled: boolean;
  moving_watermark_enabled: boolean;
  watermark_position_interval: number;
  updated_at?: string;
  updated_by?: number | null;
  updated_by_username?: string | null;
}

interface WatermarkLookupResult {
  student_id: string;
  user_id: number;
  username: string;
  first_name: string;
  last_name: string;
  email: string;
  phone_number?: string | null;
  parent_name?: string | null;
  parent_phone?: string | null;
  is_active: boolean;
  is_student: boolean;
  date_joined: string;
}

export default function VideoProtectionAdminPage() {
  // Settings Form State
  const [settings, setSettings] = useState<VideoProtectionSettingData>({
    watermark_enabled: true,
    download_enabled: false,
    screen_capture_protection_enabled: true,
    moving_watermark_enabled: true,
    watermark_position_interval: 15,
  });

  const [initialSettings, setInitialSettings] = useState<VideoProtectionSettingData | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [unauthorized, setUnauthorized] = useState(false);

  // Watermark Lookup State
  const [lookupQuery, setLookupQuery] = useState("");
  const [lookupLoading, setLookupLoading] = useState(false);
  const [lookupResult, setLookupResult] = useState<WatermarkLookupResult | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);

  // Fetch settings on mount
  const fetchSettings = useCallback(async () => {
    setLoading(true);
    setSaveError(null);
    try {
      const res = await fetch(`${API_BASE}/api/courses/video-protection-settings/`, {
        method: "GET",
        headers: getAuthHeaders(false),
        credentials: "include",
      });

      if (res.status === 401 || res.status === 403) {
        setUnauthorized(true);
        setSaveError("Access restricted: You do not have administrative privileges to manage video protection.");
        return;
      }

      if (res.ok) {
        const data = await res.json();
        setSettings(data);
        setInitialSettings(data);
        setUnauthorized(false);
      } else {
        const errData = await res.json().catch(() => ({}));
        setSaveError(errData.detail || "Failed to load video protection settings.");
      }
    } catch (err) {
      console.error("Failed to fetch video protection settings:", err);
      setSaveError("Network error: Unable to contact backend API. Please check your connection.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  // Validation
  const interval = Number(settings.watermark_position_interval);
  const intervalError =
    isNaN(interval) || interval < 5 || interval > 300
      ? "Position interval must be between 5 and 300 seconds."
      : null;

  const hasUnsavedChanges =
    initialSettings !== null &&
    (settings.watermark_enabled !== initialSettings.watermark_enabled ||
      settings.download_enabled !== initialSettings.download_enabled ||
      settings.screen_capture_protection_enabled !== initialSettings.screen_capture_protection_enabled ||
      settings.moving_watermark_enabled !== initialSettings.moving_watermark_enabled ||
      settings.watermark_position_interval !== initialSettings.watermark_position_interval);

  // Save Settings
  const handleSave = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (saving || intervalError) return;

    setSaving(true);
    setSaveSuccess(null);
    setSaveError(null);

    const payload = {
      watermark_enabled: Boolean(settings.watermark_enabled),
      download_enabled: Boolean(settings.download_enabled),
      screen_capture_protection_enabled: Boolean(settings.screen_capture_protection_enabled),
      moving_watermark_enabled: Boolean(settings.moving_watermark_enabled),
      watermark_position_interval: Math.round(Number(settings.watermark_position_interval)),
    };

    try {
      const res = await fetch(`${API_BASE}/api/courses/video-protection-settings/`, {
        method: "PATCH",
        headers: getAuthHeaders(true),
        credentials: "include",
        body: JSON.stringify(payload),
      });

      if (res.status === 403) {
        setUnauthorized(true);
        setSaveError("Permission denied: Only Super Admins and Staff Admins can update video protection settings.");
        return;
      }

      if (res.ok) {
        const updated = await res.json();
        setSettings(updated);
        setInitialSettings(updated);
        setSaveSuccess("Protection settings updated successfully! Changes are immediately active in web and mobile players.");
        setTimeout(() => setSaveSuccess(null), 5000);
      } else {
        const err = await res.json().catch(() => ({}));
        if (err.watermark_position_interval) {
          setSaveError(`Interval Error: ${err.watermark_position_interval.join(" ")}`);
        } else {
          setSaveError(err.detail || "Failed to update video protection settings.");
        }
      }
    } catch (err) {
      console.error("Save error:", err);
      setSaveError("Network error: Could not submit settings update. Please check connection.");
    } finally {
      setSaving(false);
    }
  };

  // Watermark Lookup
  const handleLookup = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    const query = lookupQuery.trim();
    if (!query) return;

    setLookupLoading(true);
    setLookupResult(null);
    setLookupError(null);

    try {
      const res = await fetch(
        `${API_BASE}/api/users/lookup-watermark/?id=${encodeURIComponent(query)}`,
        {
          method: "GET",
          headers: getAuthHeaders(false),
          credentials: "include",
        }
      );

      if (res.status === 403) {
        setLookupError("Permission denied: Only Super Admins and Staff Admins can perform forensic watermark lookups.");
      } else if (res.status === 404) {
        setLookupError(`No student found associated with watermark identifier "${query}".`);
      } else if (res.ok) {
        const data = await res.json();
        setLookupResult(data);
      } else {
        const err = await res.json().catch(() => ({}));
        setLookupError(err.error || err.detail || "Failed to perform lookup.");
      }
    } catch (err) {
      console.error("Lookup error:", err);
      setLookupError("Network error while searching for watermark ID.");
    } finally {
      setLookupLoading(false);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <div className="w-10 h-10 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
        <p className="text-zinc-400 text-sm">Loading video protection configuration...</p>
      </div>
    );
  }

  return (
    <div className="space-y-8 max-w-6xl mx-auto pb-16">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 border-b border-zinc-800 pb-6">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <Shield className="w-6 h-6 text-[#facc15]" />
            <h1 className="text-2xl font-bold tracking-tight text-white">
              Recorded Video Protection & DRM Deterrence
            </h1>
          </div>
          <p className="text-zinc-400 text-sm max-w-2xl">
            Configure dynamic student watermarking, media delivery security, and capture deterrence across web and mobile players.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={fetchSettings}
            disabled={saving}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-zinc-900 border border-zinc-800 text-zinc-300 hover:text-white hover:bg-zinc-800 text-xs font-medium transition-all"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Reload</span>
          </button>

          <button
            type="button"
            onClick={() => handleSave()}
            disabled={saving || !hasUnsavedChanges || !!intervalError || unauthorized}
            className="flex items-center gap-2 px-5 py-2 rounded-xl bg-[#facc15] text-black font-semibold text-xs tracking-wide hover:bg-yellow-400 active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed transition-all shadow-[0_0_20px_rgba(250,204,21,0.2)]"
          >
            {saving ? (
              <>
                <div className="w-3.5 h-3.5 border-2 border-black border-t-transparent rounded-full animate-spin" />
                <span>Saving...</span>
              </>
            ) : (
              <>
                <ShieldCheck className="w-4 h-4" />
                <span>Save Changes</span>
              </>
            )}
          </button>
        </div>
      </div>

      {/* Notifications */}
      {saveSuccess && (
        <div className="flex items-center gap-3 p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 text-sm">
          <CheckCircle2 className="w-5 h-5 shrink-0 text-emerald-400" />
          <span className="flex-1 font-medium">{saveSuccess}</span>
        </div>
      )}

      {saveError && (
        <div className="flex items-center gap-3 p-4 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 text-sm">
          <AlertCircle className="w-5 h-5 shrink-0 text-red-400" />
          <span className="flex-1 font-medium">{saveError}</span>
        </div>
      )}

      {unauthorized && (
        <div className="flex items-start gap-3 p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-amber-300 text-sm">
          <ShieldAlert className="w-5 h-5 shrink-0 text-amber-400 mt-0.5" />
          <div>
            <p className="font-semibold text-amber-200">Administrative Privileges Required</p>
            <p className="text-xs text-amber-300/80 mt-1">
              You are signed in, but modifying security settings or performing watermark lookups is restricted to Super Administrators and Staff Administrators.
            </p>
          </div>
        </div>
      )}

      {/* Main Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        {/* Settings Column (2 Cols) */}
        <div className="lg:col-span-2 space-y-6">
          <form onSubmit={handleSave} className="space-y-6">
            {/* Setting Card 1: Watermarking */}
            <div className="p-6 rounded-2xl bg-zinc-950 border border-zinc-800/80 space-y-6">
              <div className="flex items-center gap-3 border-b border-zinc-800 pb-4">
                <div className="p-2 rounded-xl bg-[#facc15]/10 text-[#facc15]">
                  <Eye className="w-5 h-5" />
                </div>
                <div>
                  <h2 className="text-base font-semibold text-white">Dynamic Student Watermark</h2>
                  <p className="text-xs text-zinc-400">
                    Overlays student identifier on playback to deter unauthorized distribution.
                  </p>
                </div>
              </div>

              {/* Watermark Enabled Switch */}
              <div className="flex items-center justify-between gap-4">
                <div className="space-y-0.5">
                  <label htmlFor="watermark_enabled" className="text-sm font-medium text-white cursor-pointer">
                    Enable Dynamic Watermark Overlay
                  </label>
                  <p className="text-xs text-zinc-400">
                    Displays each student's unique ID (<span className="font-mono text-zinc-300">NAT-XXXXXX</span>) over the video.
                  </p>
                </div>
                <button
                  type="button"
                  id="watermark_enabled"
                  role="switch"
                  aria-checked={settings.watermark_enabled}
                  disabled={unauthorized}
                  onClick={() => setSettings({ ...settings, watermark_enabled: !settings.watermark_enabled })}
                  className={`w-12 h-6 rounded-full transition-colors relative p-0.5 focus:outline-none focus:ring-2 focus:ring-[#facc15]/50 ${
                    settings.watermark_enabled ? "bg-[#facc15]" : "bg-zinc-800"
                  } ${unauthorized ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
                >
                  <div
                    className={`w-5 h-5 rounded-full bg-black transition-transform ${
                      settings.watermark_enabled ? "translate-x-6" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>

              {/* Moving Watermark Switch */}
              <div className="flex items-center justify-between gap-4 pt-4 border-t border-zinc-900">
                <div className="space-y-0.5">
                  <label
                    htmlFor="moving_watermark_enabled"
                    className={`text-sm font-medium cursor-pointer ${
                      settings.watermark_enabled ? "text-white" : "text-zinc-600"
                    }`}
                  >
                    Randomized Moving Position
                  </label>
                  <p className="text-xs text-zinc-400">
                    Periodically changes watermark coordinates across the video frame to prevent cropping.
                  </p>
                </div>
                <button
                  type="button"
                  id="moving_watermark_enabled"
                  role="switch"
                  aria-checked={settings.moving_watermark_enabled}
                  disabled={!settings.watermark_enabled || unauthorized}
                  onClick={() =>
                    setSettings({ ...settings, moving_watermark_enabled: !settings.moving_watermark_enabled })
                  }
                  className={`w-12 h-6 rounded-full transition-colors relative p-0.5 focus:outline-none focus:ring-2 focus:ring-[#facc15]/50 ${
                    settings.watermark_enabled && settings.moving_watermark_enabled ? "bg-[#facc15]" : "bg-zinc-800"
                  } ${!settings.watermark_enabled || unauthorized ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
                >
                  <div
                    className={`w-5 h-5 rounded-full bg-black transition-transform ${
                      settings.watermark_enabled && settings.moving_watermark_enabled ? "translate-x-6" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>

              {/* Position Interval Slider */}
              <div className="space-y-3 pt-4 border-t border-zinc-900">
                <div className="flex items-center justify-between">
                  <label
                    htmlFor="watermark_position_interval"
                    className={`text-sm font-medium ${settings.watermark_enabled ? "text-white" : "text-zinc-600"}`}
                  >
                    Position Change Interval
                  </label>
                  <div className="flex items-center gap-1.5 font-mono text-xs font-semibold text-[#facc15] bg-[#facc15]/10 px-2.5 py-1 rounded-md border border-[#facc15]/20">
                    <Clock className="w-3.5 h-3.5" />
                    <span>{settings.watermark_position_interval}s</span>
                  </div>
                </div>

                <div className="flex items-center gap-4">
                  <input
                    type="range"
                    id="watermark_position_interval"
                    min={5}
                    max={300}
                    step={1}
                    disabled={!settings.watermark_enabled || unauthorized}
                    value={settings.watermark_position_interval}
                    onChange={(e) =>
                      setSettings({ ...settings, watermark_position_interval: parseInt(e.target.value) || 5 })
                    }
                    className="w-full h-1.5 bg-zinc-800 rounded-lg appearance-none cursor-pointer accent-[#facc15] disabled:opacity-30 disabled:cursor-not-allowed"
                  />
                  <input
                    type="number"
                    min={5}
                    max={300}
                    disabled={!settings.watermark_enabled || unauthorized}
                    value={settings.watermark_position_interval}
                    onChange={(e) =>
                      setSettings({
                        ...settings,
                        watermark_position_interval: Math.max(0, parseInt(e.target.value) || 0),
                      })
                    }
                    className="w-20 px-2 py-1 bg-zinc-900 border border-zinc-700 rounded-lg text-sm text-center font-mono text-white focus:outline-none focus:border-[#facc15] disabled:opacity-30"
                  />
                </div>

                <div className="flex justify-between text-[11px] text-zinc-500">
                  <span>5 seconds (Fastest)</span>
                  <span>Recommended: 15s</span>
                  <span>300 seconds (5 min)</span>
                </div>

                {intervalError && <p className="text-xs text-red-400 font-medium">{intervalError}</p>}
              </div>
            </div>

            {/* Setting Card 2: Capture Protection & Media Controls */}
            <div className="p-6 rounded-2xl bg-zinc-950 border border-zinc-800/80 space-y-6">
              <div className="flex items-center gap-3 border-b border-zinc-800 pb-4">
                <div className="p-2 rounded-xl bg-blue-500/10 text-blue-400">
                  <Smartphone className="w-5 h-5" />
                </div>
                <div>
                  <h2 className="text-base font-semibold text-white">Capture Deterrence & Downloads</h2>
                  <p className="text-xs text-zinc-400">
                    Controls hardware window flags and browser media download options.
                  </p>
                </div>
              </div>

              {/* Screen Capture Deterrence */}
              <div className="flex items-center justify-between gap-4">
                <div className="space-y-0.5">
                  <label htmlFor="screen_capture_protection_enabled" className="text-sm font-medium text-white cursor-pointer">
                    Mobile Screen Capture Protection
                  </label>
                  <p className="text-xs text-zinc-400">
                    Enforces <span className="font-mono text-zinc-300">FLAG_SECURE</span> on Android to block screenshots and black out recordings.
                  </p>
                </div>
                <button
                  type="button"
                  id="screen_capture_protection_enabled"
                  role="switch"
                  aria-checked={settings.screen_capture_protection_enabled}
                  disabled={unauthorized}
                  onClick={() =>
                    setSettings({
                      ...settings,
                      screen_capture_protection_enabled: !settings.screen_capture_protection_enabled,
                    })
                  }
                  className={`w-12 h-6 rounded-full transition-colors relative p-0.5 focus:outline-none focus:ring-2 focus:ring-[#facc15]/50 ${
                    settings.screen_capture_protection_enabled ? "bg-[#facc15]" : "bg-zinc-800"
                  } ${unauthorized ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
                >
                  <div
                    className={`w-5 h-5 rounded-full bg-black transition-transform ${
                      settings.screen_capture_protection_enabled ? "translate-x-6" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>

              {/* Download Controls */}
              <div className="flex items-center justify-between gap-4 pt-4 border-t border-zinc-900">
                <div className="space-y-0.5">
                  <div className="flex items-center gap-2">
                    <label htmlFor="download_enabled" className="text-sm font-medium text-white cursor-pointer">
                      Direct Video Download Buttons
                    </label>
                    <span className="text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                      Default: Disabled
                    </span>
                  </div>
                  <p className="text-xs text-zinc-400">
                    Controls in-player download-control deterrence (restricting browser download buttons, context menus, and PiP detachment). Note: This acts as client-side deterrence and does not guarantee that media cannot be downloaded by someone who extracts an authorized media URL.
                  </p>
                </div>
                <button
                  type="button"
                  id="download_enabled"
                  role="switch"
                  aria-checked={settings.download_enabled}
                  disabled={unauthorized}
                  onClick={() => setSettings({ ...settings, download_enabled: !settings.download_enabled })}
                  className={`w-12 h-6 rounded-full transition-colors relative p-0.5 focus:outline-none focus:ring-2 focus:ring-[#facc15]/50 ${
                    settings.download_enabled ? "bg-[#facc15]" : "bg-zinc-800"
                  } ${unauthorized ? "opacity-50 cursor-not-allowed" : "cursor-pointer"}`}
                >
                  <div
                    className={`w-5 h-5 rounded-full bg-black transition-transform ${
                      settings.download_enabled ? "translate-x-6" : "translate-x-0"
                    }`}
                  />
                </button>
              </div>
            </div>

            {/* Audit Metadata */}
            {settings.updated_at && (
              <div className="text-xs text-zinc-500 flex items-center justify-between px-2">
                <span>
                  Last modified: {new Date(settings.updated_at).toLocaleString()}
                </span>
                {settings.updated_by_username && (
                  <span>Author: {settings.updated_by_username}</span>
                )}
              </div>
            )}
          </form>
        </div>

        {/* Sidebar Column (1 Col): Watermark Reverse Lookup & Security Notes */}
        <div className="space-y-6">
          {/* Reverse Lookup Card */}
          <div className="p-6 rounded-2xl bg-zinc-950 border border-zinc-800/80 space-y-5">
            <div className="flex items-center gap-2 text-white font-semibold">
              <Search className="w-4 h-4 text-[#facc15]" />
              <h3>Forensic Watermark Lookup</h3>
            </div>

            <p className="text-xs text-zinc-400 leading-relaxed">
              If an unauthorized recording or leaked clip is discovered, look up the watermark ID below to identify the student account.
            </p>

            <form onSubmit={handleLookup} className="space-y-3">
              <div className="relative">
                <input
                  type="text"
                  placeholder="e.g. NAT-4A9B2F"
                  value={lookupQuery}
                  onChange={(e) => setLookupQuery(e.target.value.toUpperCase())}
                  className="w-full px-3.5 py-2.5 bg-zinc-900 border border-zinc-700 rounded-xl text-sm font-mono text-white placeholder-zinc-500 focus:outline-none focus:border-[#facc15]"
                />
              </div>

              <button
                type="submit"
                disabled={lookupLoading || !lookupQuery.trim()}
                className="w-full flex items-center justify-center gap-2 py-2.5 rounded-xl bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-white text-xs font-semibold disabled:opacity-40 transition-all"
              >
                {lookupLoading ? (
                  <>
                    <div className="w-3.5 h-3.5 border-2 border-white border-t-transparent rounded-full animate-spin" />
                    <span>Searching...</span>
                  </>
                ) : (
                  <>
                    <Search className="w-3.5 h-3.5" />
                    <span>Identify Student</span>
                  </>
                )}
              </button>
            </form>

            {lookupError && (
              <div className="p-3 rounded-xl bg-red-500/10 border border-red-500/20 text-red-300 text-xs">
                {lookupError}
              </div>
            )}

            {lookupResult && (
              <div className="p-4 rounded-xl bg-zinc-900 border border-zinc-800 space-y-3 text-xs">
                <div className="flex items-center justify-between pb-2 border-b border-zinc-800">
                  <span className="font-mono font-bold text-[#facc15]">{lookupResult.student_id}</span>
                  <span
                    className={`px-2 py-0.5 rounded-full text-[10px] font-semibold ${
                      lookupResult.is_active
                        ? "bg-emerald-500/10 text-emerald-400"
                        : "bg-red-500/10 text-red-400"
                    }`}
                  >
                    {lookupResult.is_active ? "Active Account" : "Suspended"}
                  </span>
                </div>

                <div className="space-y-2 text-zinc-300">
                  <div className="flex items-center gap-2">
                    <User className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
                    <span className="font-medium text-white">
                      {lookupResult.first_name} {lookupResult.last_name}
                    </span>
                    <span className="text-zinc-500">(@{lookupResult.username})</span>
                  </div>

                  <div className="flex items-center gap-2">
                    <Mail className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
                    <span className="truncate">{lookupResult.email}</span>
                  </div>

                  {lookupResult.phone_number && (
                    <div className="flex items-center gap-2">
                      <Phone className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
                      <span>{lookupResult.phone_number}</span>
                    </div>
                  )}

                  {lookupResult.parent_name && (
                    <div className="pt-2 border-t border-zinc-800 text-[11px] text-zinc-400">
                      <span>Parent: {lookupResult.parent_name}</span>
                      {lookupResult.parent_phone && <span> ({lookupResult.parent_phone})</span>}
                    </div>
                  )}

                  <div className="flex items-center gap-2 text-[11px] text-zinc-500 pt-1">
                    <Calendar className="w-3.5 h-3.5" />
                    <span>Joined: {new Date(lookupResult.date_joined).toLocaleDateString()}</span>
                  </div>
                </div>
              </div>
            )}
          </div>

          {/* Security Information Card */}
          <div className="p-5 rounded-2xl bg-zinc-950 border border-zinc-800 text-xs text-zinc-400 space-y-3">
            <div className="flex items-center gap-2 text-zinc-200 font-semibold">
              <Info className="w-4 h-4 text-[#facc15]" />
              <h4>Security Architecture</h4>
            </div>
            <ul className="space-y-2 list-disc list-inside text-zinc-400 leading-relaxed">
              <li>
                <strong className="text-zinc-300">Time-limited delivery:</strong> Playback URLs expire in 30 minutes with inline disposition.
              </li>
              <li>
                <strong className="text-zinc-300">Android FLAG_SECURE:</strong> Blocks OS screenshots and screen recorders completely.
              </li>
              <li>
                <strong className="text-zinc-300">Desktop & Browser Reality:</strong> Web browsers cannot intercept external OS recorders (OBS, Game Bar), making dynamic student watermarks the primary forensic defense.
              </li>
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
}
