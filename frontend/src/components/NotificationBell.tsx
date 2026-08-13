"use client";

import { useState, useEffect, useRef } from "react";
import { Bell, Loader2, Check, ExternalLink } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

interface NotificationType {
  id: number;
  title: string;
  body: string;
  notification_type: string;
  is_read: boolean;
  created_at: string;
  read_at: string | null;
  action_url: string;
}

export default function NotificationBell() {
  const [isOpen, setIsOpen] = useState(false);
  const [notifications, setNotifications] = useState<NotificationType[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const isMountedRef = useRef(true);
  const abortControllerRef = useRef<AbortController | null>(null);
  const lastFetchedTimeRef = useRef<number>(0);

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
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/notifications/unread-count/`, {
        credentials: "include"
      });
      if (res.ok) {
        const data = await res.json();
        if (isMountedRef.current && data && typeof data.count === 'number' && !isNaN(data.count)) {
          setUnreadCount(data.count);
        }
      }
    } catch (err) {
      console.error("Failed to fetch unread count", err);
    }
  };

  const fetchNotifications = async () => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
    }
    const controller = new AbortController();
    abortControllerRef.current = controller;

    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/notifications/`, {
        credentials: "include",
        signal: controller.signal
      });
      if (res.ok) {
        const data = await res.json();
        let list: NotificationType[] = [];
        if (Array.isArray(data)) {
          list = data;
        } else if (data && Array.isArray(data.results)) {
          list = data.results;
        } else {
          throw new Error("Invalid notification response format");
        }

        if (isMountedRef.current) {
          setNotifications(list);
          lastFetchedTimeRef.current = Date.now();
        }
      } else {
        if (isMountedRef.current) {
          setError("Failed to load notifications.");
        }
      }
    } catch (err: any) {
      if (err.name === 'AbortError') {
        return;
      }
      console.error("Failed to fetch notifications list", err);
      if (isMountedRef.current) {
        setError("Failed to connect to the notifications service.");
      }
    } finally {
      if (isMountedRef.current && abortControllerRef.current === controller) {
        setLoading(false);
      }
    }
  };

  // Mount/Unmount hooks
  useEffect(() => {
    isMountedRef.current = true;
    fetchUnreadCount();

    return () => {
      isMountedRef.current = false;
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  // Open / Close hooks with cache implementation
  useEffect(() => {
    if (isOpen) {
      const now = Date.now();
      // Reuse existing data if fetched within the last 30 seconds
      if (notifications.length === 0 || now - lastFetchedTimeRef.current > 30000) {
        fetchNotifications();
      }
      fetchUnreadCount();
    }
  }, [isOpen]);

  // Click outside and keydown listener for Escape
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false);
      }
    };
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleKeyDown);
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleKeyDown);
    };
  }, [isOpen]);

  const markAsRead = async (id: number) => {
    try {
      const csrfToken = getCsrfToken();
      const headers: Record<string, string> = {
        "Content-Type": "application/json"
      };
      if (csrfToken) {
        headers["X-CSRFToken"] = csrfToken;
      }

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/notifications/${id}/read/`, {
        method: "POST",
        headers: headers,
        credentials: "include"
      });
      if (res.ok) {
        if (isMountedRef.current) {
          setNotifications(prev =>
            prev.map(n => n.id === id ? { ...n, is_read: true, read_at: new Date().toISOString() } : n)
          );
          setUnreadCount(prev => Math.max(0, prev - 1));
        }
      }
    } catch (err) {
      console.error("Error marking notification as read", err);
    }
  };

  const markAllAsRead = async () => {
    try {
      const csrfToken = getCsrfToken();
      const headers: Record<string, string> = {
        "Content-Type": "application/json"
      };
      if (csrfToken) {
        headers["X-CSRFToken"] = csrfToken;
      }

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/notifications/read-all/`, {
        method: "POST",
        headers: headers,
        credentials: "include"
      });
      if (res.ok) {
        if (isMountedRef.current) {
          setNotifications(prev =>
            prev.map(n => ({ ...n, is_read: true, read_at: new Date().toISOString() }))
          );
          setUnreadCount(0);
        }
      }
    } catch (err) {
      console.error("Error marking all notifications as read", err);
    }
  };

  const handleNotificationClick = async (notif: NotificationType) => {
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
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffSec = Math.floor(diffMs / 1000);
      const diffMin = Math.floor(diffSec / 60);
      const diffHr = Math.floor(diffMin / 60);
      const diffDays = Math.floor(diffHr / 24);

      if (diffSec < 60) return "Just now";
      if (diffMin < 60) return `${diffMin}m ago`;
      if (diffHr < 24) return `${diffHr}h ago`;
      if (diffDays === 1) return "Yesterday";
      if (diffDays < 7) return `${diffDays}d ago`;
      return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
    } catch (e) {
      return "";
    }
  };

  const getTypeBadgeStyle = (type: string) => {
    switch (type) {
      case "COURSE_UPDATE":
        return "border border-[#facc15]/20 bg-[#facc15]/10 text-[#facc15]";
      case "ENROLLMENT":
        return "border border-blue-400/20 bg-blue-400/10 text-blue-400";
      case "PAYMENT":
        return "border border-green-400/20 bg-green-400/10 text-green-400";
      case "COURSE_COMPLETION":
        return "border border-amber-400/20 bg-amber-400/10 text-amber-400";
      case "CERTIFICATE":
        return "border border-indigo-400/20 bg-indigo-400/10 text-indigo-400";
      case "ANNOUNCEMENT":
      default:
        return "border border-purple-400/20 bg-purple-400/10 text-purple-400";
    }
  };

  const getTypeLabel = (type: string) => {
    return type.replace(/_/g, ' ').toLowerCase();
  };

  const displayedNotifications = notifications.slice(0, 25);

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="relative p-2 rounded-full text-zinc-400 hover:text-white focus:outline-none focus:ring-2 focus:ring-[#facc15] focus:ring-offset-2 focus:ring-offset-black transition-all cursor-pointer"
        aria-haspopup="true"
        aria-expanded={isOpen}
        aria-label={`Notifications, ${unreadCount} unread`}
      >
        <Bell className="w-6 h-6" />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 bg-[#facc15] text-black text-[10px] font-bold h-5 w-5 rounded-full flex items-center justify-center border-2 border-black animate-pulse">
            {unreadCount > 99 ? "99+" : unreadCount}
          </span>
        )}
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: 15, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 15, scale: 0.95 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="absolute right-0 mt-3 w-80 sm:w-96 bg-zinc-950 border border-white/10 rounded-2xl shadow-2xl z-50 overflow-hidden flex flex-col max-h-[480px]"
          >
            <div className="p-4 border-b border-white/5 flex items-center justify-between bg-zinc-900/40">
              <span className="font-bold text-white text-base">
                Notifications {unreadCount > 0 && `(${unreadCount})`}
              </span>
              {unreadCount > 0 && (
                <button
                  onClick={markAllAsRead}
                  className="text-xs text-[#facc15] hover:text-yellow-400 font-semibold transition-colors flex items-center gap-1 cursor-pointer"
                >
                  <Check className="w-3.5 h-3.5" /> Mark all read
                </button>
              )}
            </div>

            <div className="overflow-y-auto flex-1 max-h-[360px] divide-y divide-white/5 scrollbar-thin scrollbar-thumb-zinc-800">
              {loading ? (
                <div className="p-4 space-y-4">
                  {[1, 2, 3].map(i => (
                    <div key={i} className="animate-pulse flex gap-3">
                      <div className="h-2 w-2 rounded-full bg-zinc-700 mt-2 shrink-0" />
                      <div className="flex-1 space-y-2">
                        <div className="h-4 bg-zinc-800 rounded w-2/3" />
                        <div className="h-3 bg-zinc-800 rounded w-full" />
                        <div className="h-3 bg-zinc-800 rounded w-1/2" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : error ? (
                <div className="p-6 text-center text-zinc-400 flex flex-col items-center gap-3">
                  <span className="text-sm">{error}</span>
                  <button
                    onClick={fetchNotifications}
                    className="px-4 py-1.5 bg-zinc-850 hover:bg-zinc-800 text-white text-xs font-semibold rounded-lg border border-white/10 transition-colors cursor-pointer"
                  >
                    Retry
                  </button>
                </div>
              ) : displayedNotifications.length === 0 ? (
                <div className="p-8 text-center text-zinc-500 text-sm font-medium">
                  No notifications yet
                </div>
              ) : (
                displayedNotifications.map(notif => (
                  <button
                    key={notif.id}
                    onClick={() => handleNotificationClick(notif)}
                    className={`w-full text-left p-4 hover:bg-white/5 transition-all flex gap-3 relative cursor-pointer ${
                      !notif.is_read ? 'bg-[#facc15]/5' : ''
                    }`}
                  >
                    {!notif.is_read && (
                      <span className="absolute left-3 top-5 w-2 h-2 bg-[#facc15] rounded-full shrink-0" />
                    )}

                    <div className={`flex-1 min-w-0 ${!notif.is_read ? 'pl-2' : ''}`}>
                      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                        <span className={`px-1.5 py-0.5 rounded text-[9px] font-bold uppercase tracking-wider ${getTypeBadgeStyle(notif.notification_type)}`}>
                          {getTypeLabel(notif.notification_type)}
                        </span>
                        <span className="text-[10px] text-zinc-500 font-medium">
                          {formatRelativeTime(notif.created_at)}
                        </span>
                      </div>
                      <h4 className={`text-sm text-white truncate ${!notif.is_read ? 'font-bold' : 'font-medium'}`}>
                        {notif.title}
                      </h4>
                      <p className="text-xs text-zinc-400 mt-1 line-clamp-2 leading-relaxed">
                        {notif.body}
                      </p>
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
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
