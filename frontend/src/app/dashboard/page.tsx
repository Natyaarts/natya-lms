"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import NotificationBell from "@/components/NotificationBell";

export default function Dashboard() {
  const [loading, setLoading] = useState(true);
  const [courses, setCourses] = useState<any[]>([]);
  const [announcements, setAnnouncements] = useState<any[]>([]);
  const [user, setUser] = useState<any>(null);
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
    window.location.href = '/login';
  };

  useEffect(() => {
    const fetchData = async () => {
      try {
        const userRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/me/`, {
          credentials: "include"
        });
        if (userRes.ok) {
          const userData = await userRes.json();
          setUser(userData);
          if (userData.is_onboarded === false && !userData.is_superuser && !userData.is_teacher) {
            window.location.href = '/onboarding';
            return;
          }
        } else {
          window.location.href = '/login';
          return;
        }

        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/my_courses/`, {
          credentials: "include"
        });
        if (res.ok) setCourses(await res.json());

        const annRes = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/announcements/`, {
          credentials: "include"
        });
        if (annRes.ok) setAnnouncements(await annRes.json());
      } catch (err) {
        console.error("Error fetching data:", err);
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, []);

  return (
    <div className="min-h-screen bg-black text-white font-sans pb-24">
      {/* Navigation Bar */}
      <nav className="border-b border-white/10 bg-black/50 backdrop-blur-md fixed top-0 w-full z-50">
        <div className="max-w-7xl mx-auto px-6 h-20 flex items-center justify-between">
          <Link href="/" className="flex items-center">
            <Image src="/img/logo.png" alt="Natya LMS Logo" width={140} height={40} className="object-contain" />
          </Link>
          <div className="flex gap-4 items-center">
            <Link href="/courses" className="text-sm font-medium text-[#facc15] hover:text-white transition-colors">
              Browse Courses
            </Link>
            <Link href="/bundles" className="text-sm font-medium text-[#facc15] hover:text-white transition-colors">
              Bundles
            </Link>
            <Link href="/live-classes" className="text-sm font-medium text-[#facc15] hover:text-white transition-colors">
              Live Classes
            </Link>
            <Link href="/orders" className="text-sm font-medium text-[#facc15] hover:text-white transition-colors">
              My Orders
            </Link>

            <NotificationBell />

            <div className="relative">
              <button 
                onClick={() => setDropdownOpen(!dropdownOpen)}
                className="w-10 h-10 rounded-full bg-zinc-800 border-2 border-[#facc15] flex items-center justify-center text-sm font-bold uppercase text-[#facc15] hover:scale-105 transition-transform"
              >
                {user ? (user.first_name?.[0] || user.username?.[0] || "U") : "U"}
              </button>

              {dropdownOpen && (
                <div className="absolute right-0 mt-2 w-48 bg-[#1a1a1a] border border-white/10 rounded-xl shadow-xl py-2 z-50">
                  <div className="px-4 py-2 border-b border-white/10 mb-2">
                    <p className="text-sm font-semibold text-white truncate">{user?.first_name || user?.username || 'User'}</p>
                    <p className="text-xs text-zinc-400 truncate">{user?.email || user?.phone_number || ''}</p>
                  </div>
                  {user?.is_superuser || user?.is_teacher ? (
                    <Link href="/admin" className="block px-4 py-2 text-sm text-zinc-300 hover:bg-white/5 hover:text-white transition-colors">
                      Admin Dashboard
                    </Link>
                  ) : null}
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
        </div>
      </nav>

      {/* Main Content */}
      <div className="pt-20">
        {/* Banner Section */}
        <div className="relative h-64 md:h-80 w-full">
          <Image 
            src="/img/banner.jpg" 
            alt="Dashboard Banner" 
            fill 
            className="object-cover"
          />
          <div className="absolute inset-0 bg-gradient-to-t from-black via-black/50 to-transparent" />
          
          <div className="absolute bottom-0 left-0 w-full p-6">
            <div className="max-w-7xl mx-auto">
              <h1 className="text-4xl md:text-5xl font-bold mb-2">My Learning</h1>
              <p className="text-zinc-300 text-lg">Pick up right where you left off.</p>
            </div>
          </div>
        </div>

        <div className="px-6 max-w-7xl mx-auto mt-12">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-12">
            {/* Left 2 Cols: My Courses */}
            <div className="lg:col-span-2">
              <h2 className="text-2xl font-bold mb-6 text-white flex items-center gap-2">
                My Courses
                {!loading && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-zinc-800 text-zinc-400 border border-white/5">{courses.length}</span>
                )}
              </h2>

              {loading ? (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {[1, 2].map((i) => (
                    <div key={i} className="h-[300px] bg-zinc-900 animate-pulse rounded-3xl"></div>
                  ))}
                </div>
              ) : courses.length === 0 ? (
                <div className="text-center py-20 bg-[#0a0a0a] border border-white/10 rounded-3xl">
                  <h3 className="text-2xl font-bold mb-4">No courses yet</h3>
                  <p className="text-zinc-400 mb-8 max-w-md mx-auto">You haven't enrolled in any masterclasses yet. Explore our catalog and begin your musical journey today.</p>
                  <Link href="/courses" className="inline-flex items-center justify-center px-8 py-3 bg-[#facc15] text-black font-semibold rounded-full hover:bg-yellow-400 transition-colors">
                    Browse Catalog
                  </Link>
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {courses.map((course) => (
                    <Link key={course.id} href={`/courses/${course.id}/learn`}>
                      <motion.div
                        initial={{ opacity: 0, y: 20 }}
                        animate={{ opacity: 1, y: 0 }}
                        className="bg-[#0a0a0a] border border-white/10 rounded-3xl overflow-hidden hover:border-[#facc15]/50 transition-colors group h-full flex flex-col"
                      >
                        <div className="h-48 bg-zinc-800 relative overflow-hidden shrink-0">
                          <img
                            src={course.thumbnail || "https://images.unsplash.com/photo-1514320291840-2e0a9bf2a9ae?q=80&w=1470&auto=format&fit=crop"}
                            alt={course.title}
                            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-500"
                          />
                          <div className="absolute inset-0 bg-black/40 group-hover:bg-transparent transition-colors" />
                          <div className="absolute bottom-4 left-4 bg-black/60 backdrop-blur px-3 py-1 rounded-full text-xs font-medium border border-white/10">
                            0% Completed
                          </div>
                        </div>
                        <div className="p-6 flex flex-col grow">
                          <h3 className="text-xl font-bold mb-2 group-hover:text-[#facc15] transition-colors">{course.title}</h3>
                          <p className="text-zinc-400 text-sm mb-6 line-clamp-2 grow">{course.description}</p>
                          <div className="w-full bg-zinc-900 rounded-full h-2 overflow-hidden mb-2">
                            <div className="bg-[#facc15] h-full w-[5%]" />
                          </div>
                          <div className="text-xs text-zinc-500 text-right">Last accessed: Just now</div>
                        </div>
                      </motion.div>
                    </Link>
                  ))}
                </div>
              )}
            </div>

            {/* Right 1 Col: Announcements */}
            <div className="lg:col-span-1">
              <h2 className="text-2xl font-bold mb-6 text-white flex items-center gap-2">
                Announcements
                {!loading && announcements.length > 0 && (
                  <span className="text-xs px-2 py-0.5 rounded-full bg-purple-950/40 text-purple-400 border border-purple-500/20">{announcements.length}</span>
                )}
              </h2>

              {loading ? (
                <div className="space-y-4">
                  {[1, 2].map((i) => (
                    <div key={i} className="h-40 bg-zinc-900 animate-pulse rounded-3xl"></div>
                  ))}
                </div>
              ) : announcements.length === 0 ? (
                <div className="p-8 text-center text-zinc-500 border border-dashed border-white/10 rounded-3xl">
                  No announcements yet
                </div>
              ) : (
                <div className="space-y-4">
                  {announcements.map((ann) => (
                    <motion.div
                      key={ann.id}
                      initial={{ opacity: 0, x: 20 }}
                      animate={{ opacity: 1, x: 0 }}
                      className="p-6 bg-[#0a0a0a] border border-white/10 rounded-3xl space-y-3 shadow-md"
                    >
                      <div className="flex items-center justify-between">
                        <span className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                          ann.course
                            ? 'border border-blue-500/20 bg-blue-500/10 text-blue-400'
                            : 'border border-purple-500/20 bg-purple-500/10 text-purple-400'
                        }`}>
                          {ann.course ? 'Course Update' : 'Global Update'}
                        </span>
                        <span className="text-xs text-zinc-500 font-medium">
                          {new Date(ann.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                        </span>
                      </div>
                      <h3 className="text-base font-bold text-white leading-snug">
                        {ann.title}
                      </h3>
                      <p className="text-xs text-zinc-400 leading-relaxed whitespace-pre-wrap">
                        {ann.content}
                      </p>
                      {ann.sender_name && (
                        <div className="text-[10px] text-zinc-500 font-medium pt-2 border-t border-white/5">
                          Posted by {ann.sender_name}
                        </div>
                      )}
                    </motion.div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
