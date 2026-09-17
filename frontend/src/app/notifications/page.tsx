"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, ExternalLink, Bell } from "lucide-react";

// Full web notifications page -- final release audit gap fix
// ("Notifications exist and NotificationBell exists, but there is no full
// web notifications page"). Reuses the exact same API/CSRF pattern
// NotificationBell.tsx already established (GET api/notifications/,
// GET api/notifications/unread-count/, POST api/notifications/<id>/read/,
// POST api/notifications/read-all/) -- no new backend endpoint, no new
// notification state. The list endpoint is intentionally unpaginated on
// the backend (a plain array, no page param support), so this page
// fetches once and renders the full list -- no "load more" is invented
// here since the backend has nothing to page through.

interface NotificationItem {
  id: number;
  title: string;
  body: string;
  notification_type: string;
  is_read: boolean;
  created_at: string;
  read_at: string | null;
  action_url: string;
}

type FilterValue = "" | "false" | "true";

export default function NotificationsPage() {
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<FilterValue>("");
  const [unreadCount, setUnreadCount] = useState(0);
  const [markingAllRead, setMarkingAllRead] = useState(false);

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

  const fetchUnreadCount = async () => {
    try {
      const res = await fetch(`${API}/api/notifications/unread-count/`, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        if (typeof data.count === 'number') setUnreadCount(data.count);
      }
    } catch (err) {
      console.error("Failed to fetch unread count", err);
    }
  };

  const fetchNotifications = async (activeFilter: FilterValue) => {
    setLoading(true);
    setError("");
    try {
      const query = activeFilter ? `?is_read=${activeFilter}` : "";
      const res = await fetch(`${API}/api/notifications/${query}`, { credentials: "include" });
      if (res.ok) {
        const data = await res.json();
        // The backend returns a plain array (unpaginated) -- defensively
        // handle a {results: [...]} shape too, same as NotificationBell.
        const list: NotificationItem[] = Array.isArray(data) ? data : (data?.results || []);
        setNotifications(list);
      } else {
        setError("Failed to load notifications.");
      }
    } catch (err) {
      setError("Network error loading notifications.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchNotifications(filter);
    fetchUnreadCount();
  }, [filter]);

  const markAsRead = async (id: number) => {
    try {
      const res = await fetch(`${API}/api/notifications/${id}/read/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
        credentials: "include",
      });
      if (res.ok) {
        setNotifications(prev => prev.map(n => n.id === id ? { ...n, is_read: true, read_at: new Date().toISOString() } : n));
        setUnreadCount(prev => Math.max(0, prev - 1));
      }
    } catch (err) {
      console.error("Error marking notification as read", err);
    }
  };

  const markAllAsRead = async () => {
    setMarkingAllRead(true);
    try {
      const res = await fetch(`${API}/api/notifications/read-all/`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
        credentials: "include",
      });
      if (res.ok) {
        setNotifications(prev => prev.map(n => ({ ...n, is_read: true, read_at: new Date().toISOString() })));
        setUnreadCount(0);
      }
    } catch (err) {
      console.error("Error marking all notifications as read", err);
    } finally {
      setMarkingAllRead(false);
    }
  };

  // Identical same-origin/relative-path safety check to
  // NotificationBell.tsx's handleNotificationClick -- action_url is a
  // plain string the backend never restricts, so this never trusts it
  // blindly before navigating.
  const handleRowClick = async (notif: NotificationItem) => {
    if (!notif.is_read) {
      await markAsRead(notif.id);
    }
    if (notif.action_url) {
      const url = notif.action_url.trim();
      const isRelative = url.startsWith('/') && !url.startsWith('//');
      let isSameOrigin = false;
      try {
        const parsedUrl = new URL(url);
        if (typeof window !== 'undefined' && parsedUrl.origin === window.location.origin) {
          isSameOrigin = true;
        }
      } catch (e) {}
      if (isRelative || isSameOrigin) {
        window.location.href = url;
      } else {
        console.warn("Blocked navigation to unsafe external destination:", url);
      }
    }
  };

  const formatRelativeTime = (dateStr: string) => {
    try {
      const date = new Date(dateStr);
      const diffMs = Date.now() - date.getTime();
      const diffMin = Math.floor(diffMs / 60000);
      const diffHr = Math.floor(diffMin / 60);
      const diffDays = Math.floor(diffHr / 24);
      if (diffMin < 1) return "Just now";
      if (diffMin < 60) return `${diffMin}m ago`;
      if (diffHr < 24) return `${diffHr}h ago`;
      if (diffDays === 1) return "Yesterday";
      if (diffDays < 7) return `${diffDays}d ago`;
      return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
    } catch (e) {
      return "";
    }
  };

  // Same type -> color mapping as NotificationBell.tsx, extended with
  // explicit LIVE_CLASS/ASSIGNMENT cases (both valid NotificationType
  // choices that fell through to the default case there) since this is
  // new code, not a redesign of the existing component.
  const getTypeBadgeStyle = (type: string) => {
    switch (type) {
      case "COURSE_UPDATE": return "border border-[#facc15]/20 bg-[#facc15]/10 text-[#facc15]";
      case "ENROLLMENT": return "border border-blue-400/20 bg-blue-400/10 text-blue-400";
      case "PAYMENT": return "border border-green-400/20 bg-green-400/10 text-green-400";
      case "COURSE_COMPLETION": return "border border-amber-400/20 bg-amber-400/10 text-amber-400";
      case "CERTIFICATE": return "border border-indigo-400/20 bg-indigo-400/10 text-indigo-400";
      case "LIVE_CLASS": return "border border-red-400/20 bg-red-400/10 text-red-400";
      case "ASSIGNMENT": return "border border-cyan-400/20 bg-cyan-400/10 text-cyan-400";
      case "ANNOUNCEMENT":
      default: return "border border-purple-400/20 bg-purple-400/10 text-purple-400";
    }
  };

  const getTypeLabel = (type: string) => type.replace(/_/g, ' ').toLowerCase();

  return (
    <div className="min-h-screen bg-black text-white font-sans selection:bg-[#facc15] selection:text-black pb-24">
      <nav className="border-b border-white/10 bg-black/50 backdrop-blur-md fixed top-0 w-full z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <Link href="/" className="flex items-center">
            <span className="font-bold text-lg tracking-tight">Natya</span>
          </Link>
          <div className="flex gap-4">
            <Link href="/invoices" className="text-sm font-medium hover:text-[#facc15] transition-colors">Invoices</Link>
            <Link href="/orders" className="text-sm font-medium hover:text-[#facc15] transition-colors">My Orders</Link>
            <Link href="/dashboard" className="text-sm font-medium hover:text-[#facc15] transition-colors">Dashboard</Link>
          </div>
        </div>
      </nav>

      <div className="max-w-3xl mx-auto px-6 pt-32">
        <div className="flex items-center justify-between mb-2">
          <h1 className="text-4xl font-bold flex items-center gap-3">
            <Bell className="w-8 h-8 text-[#facc15]" />
            Notifications
          </h1>
          {unreadCount > 0 && (
            <button
              onClick={markAllAsRead}
              disabled={markingAllRead}
              className="px-4 py-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-50 text-sm font-semibold rounded-xl transition-all flex items-center gap-2 text-[#facc15]"
            >
              <Check className="w-4 h-4" />
              {markingAllRead ? "Marking..." : `Mark all read (${unreadCount})`}
            </button>
          )}
        </div>
        <p className="text-zinc-400 text-sm mb-8">Everything the platform has notified you about.</p>

        {/* Reuses the existing ?is_read= query param the backend already
            supports -- no new filtering behavior invented. */}
        <div className="flex gap-2 mb-6">
          {([
            { value: "", label: "All" },
            { value: "false", label: "Unread" },
            { value: "true", label: "Read" },
          ] as { value: FilterValue; label: string }[]).map(opt => (
            <button
              key={opt.value}
              onClick={() => setFilter(opt.value)}
              className={`px-4 py-1.5 rounded-full text-xs font-semibold transition-all border ${
                filter === opt.value
                  ? "bg-[#facc15] text-black border-[#facc15]"
                  : "bg-zinc-900 text-zinc-400 border-white/10 hover:text-white hover:bg-zinc-800"
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        <div className="bg-zinc-900/50 border border-white/10 rounded-2xl overflow-hidden shadow-2xl divide-y divide-white/5">
          {loading ? (
            <div className="p-16 text-center text-zinc-500">
              <div className="flex flex-col items-center justify-center gap-3">
                <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                <span className="text-sm">Loading notifications...</span>
              </div>
            </div>
          ) : error ? (
            <div className="p-8 text-center text-zinc-400 flex flex-col items-center gap-3">
              <span className="text-sm">{error}</span>
              <button
                onClick={() => fetchNotifications(filter)}
                className="px-4 py-1.5 bg-zinc-800 hover:bg-zinc-700 text-white text-xs font-semibold rounded-lg border border-white/10 transition-colors"
              >
                Retry
              </button>
            </div>
          ) : notifications.length === 0 ? (
            <div className="p-16 text-center text-zinc-500 text-sm">
              {filter === "false" ? "No unread notifications." : filter === "true" ? "No read notifications." : "No notifications yet."}
            </div>
          ) : (
            notifications.map(notif => (
              <button
                key={notif.id}
                onClick={() => handleRowClick(notif)}
                className={`w-full text-left p-5 hover:bg-white/5 transition-all flex gap-3 relative ${!notif.is_read ? 'bg-[#facc15]/5' : ''}`}
              >
                {!notif.is_read && (
                  <span className="absolute left-3 top-6 w-2 h-2 bg-[#facc15] rounded-full shrink-0" />
                )}
                <div className={`flex-1 min-w-0 ${!notif.is_read ? 'pl-3' : ''}`}>
                  <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                    <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider ${getTypeBadgeStyle(notif.notification_type)}`}>
                      {getTypeLabel(notif.notification_type)}
                    </span>
                    <span className="text-[10px] text-zinc-500 font-medium">{formatRelativeTime(notif.created_at)}</span>
                  </div>
                  <h4 className={`text-sm text-white ${!notif.is_read ? 'font-bold' : 'font-medium'}`}>{notif.title}</h4>
                  <p className="text-xs text-zinc-400 mt-1 leading-relaxed">{notif.body}</p>
                  {notif.action_url && (
                    <div className="flex items-center gap-1 mt-2 text-[10px] font-semibold text-[#facc15]">
                      <span>Go to link</span>
                      <ExternalLink className="w-2.5 h-2.5" />
                    </div>
                  )}
                </div>
              </button>
            ))
          )}
        </div>
      </div>
    </div>
  );
}
