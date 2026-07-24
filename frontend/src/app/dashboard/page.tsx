"use client";

import Link from "next/link";
import Image from "next/image";
import { useEffect, useState } from "react";
import { motion } from "framer-motion";

export default function Dashboard() {
  const [loading, setLoading] = useState(true);
  const [courses, setCourses] = useState<any[]>([]);

  useEffect(() => {
    const fetchMyCourses = async () => {
      try {
        const res = await fetch("http://localhost:8000/api/courses/my_courses/", {
          credentials: "include" // Important for sessions!
        });
        if (res.ok) {
          const data = await res.json();
          setCourses(data);
        } else {
          console.error("Failed to fetch courses, status:", res.status);
        }
      } catch (err) {
        console.error("Error fetching courses:", err);
      } finally {
        setLoading(false);
      }
    };

    fetchMyCourses();
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
            <div className="w-10 h-10 rounded-full bg-zinc-800 border-2 border-zinc-700 flex items-center justify-center text-sm font-bold">
              U
            </div>
          </div>
        </div>
      </nav>

      {/* Main Content */}
      <div className="pt-20">
        {/* Banner Section */}
        <div className="relative h-64 md:h-80 w-full">
          <Image 
            src="/img/banner.jfif" 
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

        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {[1, 2, 3].map((i) => (
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
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {courses.map((course) => (
              <Link key={course.id} href={`/courses/${course.id}`}>
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
      </div>
    </div>
  );
}
