"use client";

import { useEffect } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import {
  X,
  BookOpen,
  Package,
  Compass,
  Video,
  Bell,
  User as UserIcon,
  ShoppingBag,
  CreditCard,
  FileText,
  Shield,
  LogOut,
  LogIn,
  UserPlus
} from "lucide-react";
import { useAuth } from "@/context/AuthContext";

interface MobileNavDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

export default function MobileNavDrawer({ isOpen, onClose }: MobileNavDrawerProps) {
  const pathname = usePathname();
  const { user, isAuthenticated, isAdmin, isTeacher, logout } = useAuth();

  // Prevent background scrolling when drawer is open
  useEffect(() => {
    if (isOpen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [isOpen]);

  useEffect(() => {
    function handleKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
    }
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const initial = (user?.first_name?.[0] || user?.username?.[0] || "U").toUpperCase();
  const displayName = [user?.first_name, user?.last_name].filter(Boolean).join(" ") || user?.username || "Account";
  const portalLabel = isAdmin ? "Admin Dashboard" : isTeacher ? "Instructor Portal" : null;

  const isActive = (href: string) => {
    if (href === "/dashboard") return pathname === "/dashboard";
    return pathname.startsWith(href);
  };

  return (
    <div className="fixed inset-0 z-50 lg:hidden flex">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/80 backdrop-blur-sm transition-opacity"
        onClick={onClose}
        aria-hidden="true"
      />

      {/* Drawer content */}
      <div className="relative ml-auto w-full max-w-xs sm:max-w-sm h-full bg-[#0d0d0d] border-l border-white/10 flex flex-col z-10 shadow-2xl overflow-y-auto">
        {/* Header */}
        <div className="h-20 px-6 border-b border-white/10 flex items-center justify-between shrink-0">
          <Link href="/" onClick={onClose} className="flex items-center">
            <Image src="/img/logo.png" alt="Natya LMS Logo" width={120} height={34} className="object-contain" />
          </Link>
          <button
            onClick={onClose}
            className="w-10 h-10 rounded-full bg-white/5 border border-white/10 flex items-center justify-center text-zinc-400 hover:text-white hover:bg-white/10 transition-colors"
            aria-label="Close menu"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* User Card if Authenticated */}
        {isAuthenticated && user && (
          <div className="p-5 border-b border-white/10 bg-white/[0.02]">
            <div className="flex items-center gap-3">
              <div className="w-11 h-11 rounded-full bg-zinc-800 border-2 border-[#facc15] flex items-center justify-center text-base font-bold text-[#facc15]">
                {initial}
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-bold text-white truncate">{displayName}</p>
                <p className="text-xs text-zinc-400 truncate">{user.email || user.phone_number || `@${user.username}`}</p>
              </div>
            </div>
          </div>
        )}

        {/* Navigation Sections */}
        <div className="flex-1 py-4 px-4 space-y-6">
          {/* Main Learning Links */}
          <div>
            <p className="px-3 text-[10px] font-bold uppercase tracking-wider text-zinc-500 mb-2">Learning</p>
            <div className="space-y-1">
              <Link
                href="/dashboard"
                onClick={onClose}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                  isActive("/dashboard")
                    ? "bg-[#facc15]/15 text-[#facc15] font-semibold"
                    : "text-zinc-300 hover:bg-white/5 hover:text-white"
                }`}
              >
                <BookOpen className="w-4 h-4" />
                <span>My Learning</span>
              </Link>

              <Link
                href="/courses"
                onClick={onClose}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                  isActive("/courses")
                    ? "bg-[#facc15]/15 text-[#facc15] font-semibold"
                    : "text-zinc-300 hover:bg-white/5 hover:text-white"
                }`}
              >
                <Compass className="w-4 h-4" />
                <span>Explore Courses</span>
              </Link>

              <Link
                href="/bundles"
                onClick={onClose}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                  isActive("/bundles")
                    ? "bg-[#facc15]/15 text-[#facc15] font-semibold"
                    : "text-zinc-300 hover:bg-white/5 hover:text-white"
                }`}
              >
                <Package className="w-4 h-4" />
                <span>Course Bundles</span>
              </Link>

              <Link
                href="/live-classes"
                onClick={onClose}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                  isActive("/live-classes")
                    ? "bg-[#facc15]/15 text-[#facc15] font-semibold"
                    : "text-zinc-300 hover:bg-white/5 hover:text-white"
                }`}
              >
                <Video className="w-4 h-4" />
                <span>Live Classes</span>
              </Link>

              <Link
                href="/notifications"
                onClick={onClose}
                className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                  isActive("/notifications")
                    ? "bg-[#facc15]/15 text-[#facc15] font-semibold"
                    : "text-zinc-300 hover:bg-white/5 hover:text-white"
                }`}
              >
                <Bell className="w-4 h-4" />
                <span>Notifications</span>
              </Link>
            </div>
          </div>

          {/* Account & Billing (if Authenticated) */}
          {isAuthenticated && (
            <div>
              <p className="px-3 text-[10px] font-bold uppercase tracking-wider text-zinc-500 mb-2">Account & Billing</p>
              <div className="space-y-1">
                <Link
                  href="/profile"
                  onClick={onClose}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                    isActive("/profile")
                      ? "bg-[#facc15]/15 text-[#facc15] font-semibold"
                      : "text-zinc-300 hover:bg-white/5 hover:text-white"
                  }`}
                >
                  <UserIcon className="w-4 h-4" />
                  <span>My Profile</span>
                </Link>

                <Link
                  href="/orders"
                  onClick={onClose}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                    isActive("/orders")
                      ? "bg-[#facc15]/15 text-[#facc15] font-semibold"
                      : "text-zinc-300 hover:bg-white/5 hover:text-white"
                  }`}
                >
                  <ShoppingBag className="w-4 h-4" />
                  <span>My Orders</span>
                </Link>

                <Link
                  href="/subscriptions"
                  onClick={onClose}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                    isActive("/subscriptions")
                      ? "bg-[#facc15]/15 text-[#facc15] font-semibold"
                      : "text-zinc-300 hover:bg-white/5 hover:text-white"
                  }`}
                >
                  <CreditCard className="w-4 h-4" />
                  <span>Subscriptions</span>
                </Link>

                <Link
                  href="/invoices"
                  onClick={onClose}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                    isActive("/invoices")
                      ? "bg-[#facc15]/15 text-[#facc15] font-semibold"
                      : "text-zinc-300 hover:bg-white/5 hover:text-white"
                  }`}
                >
                  <FileText className="w-4 h-4" />
                  <span>Invoices</span>
                </Link>

                {portalLabel && (
                  <Link
                    href="/admin"
                    onClick={onClose}
                    className="flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-semibold text-[#facc15] bg-[#facc15]/10 hover:bg-[#facc15]/20 transition-colors mt-2"
                  >
                    <Shield className="w-4 h-4 text-[#facc15]" />
                    <span>{portalLabel}</span>
                  </Link>
                )}
              </div>
            </div>
          )}
        </div>

        {/* Bottom Auth Section */}
        <div className="p-4 border-t border-white/10 mt-auto bg-black/40">
          {isAuthenticated ? (
            <button
              onClick={() => {
                onClose();
                logout();
              }}
              className="w-full flex items-center justify-center gap-2 py-3 px-4 rounded-xl text-sm font-bold text-red-400 bg-red-500/10 hover:bg-red-500/20 transition-colors"
            >
              <LogOut className="w-4 h-4" />
              <span>Log Out</span>
            </button>
          ) : (
            <div className="space-y-2">
              <Link
                href="/login"
                onClick={onClose}
                className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl text-sm font-bold text-white bg-white/10 hover:bg-white/15 transition-colors"
              >
                <LogIn className="w-4 h-4" />
                <span>Sign In</span>
              </Link>
              <Link
                href="/register"
                onClick={onClose}
                className="w-full flex items-center justify-center gap-2 py-2.5 px-4 rounded-xl text-sm font-bold text-black bg-[#facc15] hover:bg-yellow-400 transition-colors"
              >
                <UserPlus className="w-4 h-4" />
                <span>Sign Up</span>
              </Link>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
