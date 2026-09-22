"use client";

import { useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { Menu } from "lucide-react";
import { useAuth } from "@/context/AuthContext";
import NotificationBell from "@/components/NotificationBell";
import AccountMenu from "./AccountMenu";
import MobileNavDrawer from "./MobileNavDrawer";

export default function AppHeader() {
  const pathname = usePathname();
  const { isAuthenticated, loading } = useAuth();
  const [isMobileOpen, setIsMobileOpen] = useState(false);

  // Suppress AppHeader on routes that have specialized shells or full-screen modes
  if (!pathname) return null;
  if (pathname === "/") return null; // Landing page uses animated hero navbar
  if (pathname.startsWith("/admin")) return null; // Admin dashboard uses its own sidebar layout
  if (pathname === "/login" || pathname === "/register" || pathname === "/verify") return null; // Clean auth card screens
  if (pathname.endsWith("/learn")) return null; // Theater mode recorded-class video player

  // Specialized minimal header for Onboarding flow
  if (pathname === "/onboarding") {
    return (
      <header className="h-20 border-b border-white/10 bg-black/60 backdrop-blur-md fixed top-0 w-full z-50 flex items-center px-6 md:px-8">
        <Link href="/" className="flex items-center">
          <Image src="/img/logo.png" alt="Natya LMS Logo" width={130} height={38} className="object-contain" priority />
        </Link>
      </header>
    );
  }

  const navLinks = [
    { name: "Explore", href: "/courses" },
    { name: "Bundles", href: "/bundles" },
    { name: "My Learning", href: "/dashboard" },
    { name: "Live Classes", href: "/live-classes" },
  ];

  const isLinkActive = (href: string) => {
    if (href === "/dashboard") return pathname === "/dashboard";
    return pathname.startsWith(href);
  };

  return (
    <>
      <header className="border-b border-white/10 bg-black/60 backdrop-blur-md fixed top-0 w-full z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 h-20 flex items-center justify-between gap-4">
          {/* Brand Logo */}
          <Link href={isAuthenticated ? "/dashboard" : "/"} className="flex items-center shrink-0 hover:opacity-90 transition-opacity">
            <Image
              src="/img/logo.png"
              alt="Natya LMS Logo"
              width={135}
              height={38}
              className="object-contain"
              priority
            />
          </Link>

          {/* Desktop Navigation Links */}
          <nav className="hidden lg:flex items-center gap-1 xl:gap-2">
            {navLinks.map((link) => {
              const active = isLinkActive(link.href);
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`px-3.5 py-2 rounded-full text-sm font-medium transition-all ${
                    active
                      ? "text-[#facc15] bg-[#facc15]/10 font-semibold"
                      : "text-zinc-300 hover:text-white hover:bg-white/5"
                  }`}
                >
                  {link.name}
                </Link>
              );
            })}
          </nav>

          {/* Desktop & Mobile Actions */}
          <div className="flex items-center gap-2 sm:gap-3 shrink-0">
            {/* Notification Bell (always visible if authenticated, even on mobile) */}
            {isAuthenticated && (
              <div className="flex items-center">
                <NotificationBell />
              </div>
            )}

            {/* Desktop Account Menu / Auth CTAs */}
            <div className="hidden lg:flex items-center gap-3">
              {loading ? (
                <div className="w-10 h-10 rounded-full bg-zinc-800 animate-pulse border border-white/10" />
              ) : isAuthenticated ? (
                <AccountMenu />
              ) : (
                <div className="flex items-center gap-2">
                  <Link
                    href="/login"
                    className="px-4 py-2 text-sm font-semibold text-zinc-300 hover:text-white hover:bg-white/5 rounded-full transition-colors"
                  >
                    Sign In
                  </Link>
                  <Link
                    href="/register"
                    className="px-5 py-2 text-sm font-bold text-black bg-[#facc15] hover:bg-yellow-400 rounded-full transition-colors"
                  >
                    Sign Up
                  </Link>
                </div>
              )}
            </div>

            {/* Tablet/Mobile Hamburger Toggle */}
            <button
              onClick={() => setIsMobileOpen(true)}
              className="lg:hidden w-10 h-10 rounded-xl bg-white/5 border border-white/10 flex items-center justify-center text-zinc-300 hover:text-white hover:bg-white/10 transition-colors focus:outline-none"
              aria-label="Open mobile menu"
            >
              <Menu className="w-5 h-5" />
            </button>
          </div>
        </div>
      </header>

      {/* Responsive Slide-over Drawer for Tablet/Mobile */}
      <MobileNavDrawer isOpen={isMobileOpen} onClose={() => setIsMobileOpen(false)} />
    </>
  );
}
