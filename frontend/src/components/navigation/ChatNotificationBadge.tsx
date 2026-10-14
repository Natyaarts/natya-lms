"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { MessageSquare } from "lucide-react";
import { fetchUnreadCount } from "@/lib/chatApi";

export default function ChatNotificationBadge() {
  const [unreadCount, setUnreadCount] = useState(0);

  useEffect(() => {
    let isMounted = true;

    const checkUnread = async () => {
      const count = await fetchUnreadCount();
      if (isMounted) setUnreadCount(count);
    };

    checkUnread();
    const interval = setInterval(checkUnread, 15000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  return (
    <Link
      href="/chat"
      className="relative p-2 rounded-xl text-zinc-300 hover:text-white hover:bg-white/5 transition-colors flex items-center justify-center"
      aria-label={`Messages${unreadCount > 0 ? ` (${unreadCount} unread)` : ""}`}
      title="Messages"
    >
      <MessageSquare className="w-5 h-5" />
      {unreadCount > 0 && (
        <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] px-1 bg-[#facc15] text-black text-[10px] font-extrabold rounded-full flex items-center justify-center animate-pulse shadow-sm">
          {unreadCount > 99 ? "99+" : unreadCount}
        </span>
      )}
    </Link>
  );
}
