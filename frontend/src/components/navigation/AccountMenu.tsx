"use client";

import { useState, useRef, useEffect } from "react";
import Link from "next/link";
import { User, ShoppingBag, CreditCard, FileText, Shield, LogOut } from "lucide-react";
import { useAuth } from "@/context/AuthContext";

export default function AccountMenu() {
  const { user, isAdmin, isTeacher, logout } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setIsOpen(false);
      }
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setIsOpen(false);
      }
    }
    if (isOpen) {
      document.addEventListener("mousedown", handleClickOutside);
      document.addEventListener("keydown", handleKeyDown);
    }
    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [isOpen]);

  if (!user) return null;

  const initial = (user.first_name?.[0] || user.username?.[0] || "U").toUpperCase();
  const displayName = [user.first_name, user.last_name].filter(Boolean).join(" ") || user.username || "Account";
  const portalLabel = isAdmin ? "Admin Dashboard" : isTeacher ? "Instructor Portal" : null;

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-10 h-10 rounded-full bg-zinc-800 border-2 border-[#facc15] flex items-center justify-center text-sm font-bold uppercase text-[#facc15] hover:scale-105 transition-transform focus:outline-none focus:ring-2 focus:ring-[#facc15]/50"
        aria-expanded={isOpen}
        aria-haspopup="true"
        aria-label="Account menu"
      >
        {initial}
      </button>

      {isOpen && (
        <div className="absolute right-0 mt-2 w-60 bg-[#141414] border border-white/10 rounded-2xl shadow-2xl py-2 z-50 animate-in fade-in slide-in-from-top-2 duration-150">
          <div className="px-4 py-3 border-b border-white/10">
            <p className="text-sm font-bold text-white truncate">{displayName}</p>
            <p className="text-xs text-zinc-400 truncate">{user.email || user.phone_number || `@${user.username}`}</p>
          </div>

          <div className="py-1">
            <Link
              href="/profile"
              onClick={() => setIsOpen(false)}
              className="flex items-center gap-3 px-4 py-2.5 text-sm text-zinc-300 hover:bg-white/5 hover:text-white transition-colors"
            >
              <User className="w-4 h-4 text-zinc-400" />
              <span>My Profile</span>
            </Link>

            <Link
              href="/orders"
              onClick={() => setIsOpen(false)}
              className="flex items-center gap-3 px-4 py-2.5 text-sm text-zinc-300 hover:bg-white/5 hover:text-white transition-colors"
            >
              <ShoppingBag className="w-4 h-4 text-zinc-400" />
              <span>My Orders</span>
            </Link>

            <Link
              href="/subscriptions"
              onClick={() => setIsOpen(false)}
              className="flex items-center gap-3 px-4 py-2.5 text-sm text-zinc-300 hover:bg-white/5 hover:text-white transition-colors"
            >
              <CreditCard className="w-4 h-4 text-zinc-400" />
              <span>Subscriptions</span>
            </Link>

            <Link
              href="/invoices"
              onClick={() => setIsOpen(false)}
              className="flex items-center gap-3 px-4 py-2.5 text-sm text-zinc-300 hover:bg-white/5 hover:text-white transition-colors"
            >
              <FileText className="w-4 h-4 text-zinc-400" />
              <span>Invoices</span>
            </Link>

            {portalLabel && (
              <Link
                href="/admin"
                onClick={() => setIsOpen(false)}
                className="flex items-center gap-3 px-4 py-2.5 text-sm text-[#facc15] hover:bg-[#facc15]/10 transition-colors border-t border-white/5 mt-1 pt-2.5"
              >
                <Shield className="w-4 h-4 text-[#facc15]" />
                <span className="font-medium">{portalLabel}</span>
              </Link>
            )}
          </div>

          <div className="border-t border-white/10 pt-1 mt-1">
            <button
              onClick={() => {
                setIsOpen(false);
                logout();
              }}
              className="w-full flex items-center gap-3 px-4 py-2.5 text-sm text-red-400 hover:bg-red-400/10 transition-colors text-left font-medium"
            >
              <LogOut className="w-4 h-4" />
              <span>Log Out</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
