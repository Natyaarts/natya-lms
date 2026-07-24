"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const pathname = usePathname();
  const [isAdmin, setIsAdmin] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Skip auth check for login page
    if (pathname === '/admin/login') {
      setLoading(false);
      setIsAdmin(true);
      return;
    }

    // Check if user is superadmin
    fetch("http://localhost:8000/api/auth/user/", { credentials: 'include' })
      .then(res => {
        if (res.ok) {
          return res.json();
        } else {
          throw new Error("Not authenticated");
        }
      })
      .then(data => {
        if (data.is_superuser || data.is_teacher) {
          setIsAdmin(true);
        } else {
          window.location.href = "/dashboard";
        }
      })
      .catch(err => {
        console.error(err);
        window.location.href = "/admin/login";
      })
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center text-white">
        <div className="w-8 h-8 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  if (!isAdmin) return null;

  if (pathname === '/admin/login') {
    return <>{children}</>;
  }

  const links = [
    { name: "Overview", href: "/admin" },
    { name: "Users", href: "/admin/users" },
    { name: "Courses", href: "/admin/courses" },
    { name: "Back to App", href: "/dashboard" },
  ];

  return (
    <div className="min-h-screen bg-black text-white flex">
      {/* Sidebar */}
      <div className="w-64 bg-[#0a0a0a] border-r border-white/10 flex flex-col h-screen sticky top-0">
        <div className="p-6 border-b border-white/10 flex items-center justify-between">
          <Link href="/admin" className="flex items-center gap-2">
            <img src="/img/logo.png" alt="Natya Admin Logo" className="h-8 w-auto" />
            <span className="text-xs font-bold tracking-widest text-[#facc15] uppercase bg-[#facc15]/10 px-2 py-1 rounded">Admin</span>
          </Link>
        </div>
        
        <nav className="flex-1 p-4 space-y-2">
          {links.map((link) => {
            const isActive = pathname === link.href;
            return (
              <Link 
                key={link.name} 
                href={link.href}
                className={`flex items-center px-4 py-3 rounded-xl transition-colors ${
                  isActive 
                    ? "bg-[#facc15] text-black font-semibold" 
                    : "text-zinc-400 hover:text-white hover:bg-white/5"
                }`}
              >
                {link.name}
              </Link>
            );
          })}
        </nav>
        
        <div className="p-4 border-t border-white/10">
          <div className="text-xs text-zinc-500 uppercase font-medium tracking-wider mb-2">Super Admin</div>
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-full bg-zinc-800 flex items-center justify-center text-sm font-medium">A</div>
            <div className="text-sm font-medium">Admin User</div>
          </div>
        </div>
      </div>

      {/* Main Content */}
      <div className="flex-1 overflow-auto p-8 relative">
        <div className="absolute top-0 right-0 w-[500px] h-[500px] bg-[#facc15]/5 rounded-full blur-[150px] pointer-events-none" />
        <div className="relative z-10">
          {children}
        </div>
      </div>
    </div>
  );
}
