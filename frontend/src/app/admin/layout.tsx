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
  const [user, setUser] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [dropdownOpen, setDropdownOpen] = useState(false);

  const handleLogout = async () => {
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/auth/logout/`, {
        method: 'POST',
        credentials: 'include'
      });
    } catch (err) {
      console.error(err);
    }
    window.location.href = '/admin/login';
  };

  useEffect(() => {
    // Skip auth check for login page
    if (pathname === '/admin/login') {
      setLoading(false);
      setIsAdmin(true);
      return;
    }

    // Check if user is superadmin
    fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/auth/user/`, { credentials: 'include' })
      .then(res => {
        if (res.ok) {
          return res.json();
        } else {
          throw new Error("Not authenticated");
        }
      })
      .then(data => {
        setUser(data);
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
    { 
      name: "Overview", 
      href: "/admin",
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/><rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/></svg>
      )
    },
    { 
      name: "Users", 
      href: "/admin/users",
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
      )
    },
    { 
      name: "Courses", 
      href: "/admin/courses",
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5v-15A2.5 2.5 0 0 1 6.5 2H20v20H6.5a2.5 2.5 0 0 1 0-5H20"/></svg>
      )
    },
    { 
      name: "Onboarding", 
      href: "/admin/onboarding-fields",
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/></svg>
      )
    },
    { 
      name: "Payments", 
      href: "/admin/payments",
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect width="20" height="14" x="2" y="5" rx="2"/><line x1="2" y1="10" x2="22" y2="10"/></svg>
      )
    },
    { 
      name: "Enrollments", 
      href: "/admin/enrollments",
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
      )
    },
    { 
      name: "CMS Editor", 
      href: "/admin/cms",
      icon: (
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/><line x1="3" y1="9" x2="21" y2="9"/><line x1="9" y1="21" x2="9" y2="9"/></svg>
      )
    },
  ];

  return (
    <div className="min-h-screen bg-[#09090b] text-zinc-100 flex font-sans selection:bg-[#facc15]/30">
      {/* Sidebar */}
      <div className="w-60 bg-[#09090b] border-r border-white/5 flex flex-col h-screen sticky top-0">
        <div className="h-14 px-6 border-b border-white/5 flex items-center justify-between shrink-0">
          <Link href="/admin" className="flex items-center gap-2 transition-opacity hover:opacity-80">
            <img src="/img/logo.png" alt="Natya Admin" className="h-6 w-auto" />
            <span className="text-[10px] font-bold tracking-widest text-zinc-500 uppercase mt-0.5">Admin</span>
          </Link>
        </div>
        
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          <div className="text-xs font-semibold text-zinc-500 uppercase tracking-wider mb-3 px-3">Main Menu</div>
          {links.map((link) => {
            const isActive = pathname === link.href;
            return (
              <Link 
                key={link.name} 
                href={link.href}
                className={`flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition-all ${
                  isActive 
                    ? "bg-white/10 text-white shadow-sm" 
                    : "text-zinc-400 hover:text-white hover:bg-white/5"
                }`}
              >
                <div className={`${isActive ? "text-white" : "text-zinc-500"}`}>
                  {link.icon}
                </div>
                {link.name}
              </Link>
            );
          })}
        </nav>
        
        <div className="p-4 border-t border-white/5 bg-[#09090b]">
          <Link href="/dashboard" className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium text-zinc-400 hover:text-white hover:bg-white/5 transition-all w-full">
            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-zinc-500"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" x2="9" y1="12" y2="12"/></svg>
            Exit to App
          </Link>
        </div>
      </div>

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col h-screen overflow-hidden bg-[#09090b]">
        {/* Top Header */}
        <header className="h-14 shrink-0 border-b border-white/5 flex items-center justify-between px-8 bg-[#09090b]/80 backdrop-blur-md sticky top-0 z-10">
          <div className="flex items-center text-sm font-medium text-zinc-400">
            <span className="hover:text-white cursor-pointer transition-colors">Admin</span>
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="mx-2 text-zinc-600"><path d="m9 18 6-6-6-6"/></svg>
            <span className="text-zinc-100 capitalize">
              {pathname === '/admin' ? 'Overview' : pathname.split('/').pop()?.replace(/-/g, ' ')}
            </span>
          </div>
          
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 pl-4 border-l border-white/5 relative">
              <div className="flex flex-col items-end">
                <span className="text-sm font-medium text-white leading-none">{user?.first_name || user?.username || 'Super Admin'}</span>
                <span className="text-[10px] text-zinc-500 mt-1 uppercase tracking-wider">Authenticated</span>
              </div>
              <button 
                onClick={() => setDropdownOpen(!dropdownOpen)}
                className="w-8 h-8 rounded-full bg-gradient-to-tr from-[#facc15] to-[#eab308] text-black flex items-center justify-center text-sm font-bold shadow-sm ring-2 ring-[#09090b] uppercase hover:scale-105 transition-transform"
              >
                {user ? (user.first_name?.[0] || user.username?.[0] || "A") : "A"}
              </button>

              {dropdownOpen && (
                <div className="absolute right-0 top-full mt-2 w-48 bg-[#1a1a1a] border border-white/10 rounded-xl shadow-xl py-2 z-50">
                  <div className="px-4 py-2 border-b border-white/10 mb-2">
                    <p className="text-sm font-semibold text-white truncate">{user?.email || user?.phone_number || ''}</p>
                  </div>
                  <Link href="/dashboard" className="block px-4 py-2 text-sm text-zinc-300 hover:bg-white/5 hover:text-white transition-colors">
                    Student Dashboard
                  </Link>
                  <button 
                    onClick={handleLogout}
                    className="w-full text-left px-4 py-2 text-sm text-red-400 hover:bg-red-400/10 transition-colors"
                  >
                    Log Out
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        {/* Scrollable Content */}
        <main className="flex-1 overflow-y-auto p-8">
          <div className="max-w-6xl mx-auto">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
}
