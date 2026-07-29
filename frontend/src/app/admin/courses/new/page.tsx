"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { motion } from "framer-motion";

export default function CreateCourse() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [formData, setFormData] = useState({
    title: "",
    description: "",
    price: "0.00"
  });

  const [thumbnail, setThumbnail] = useState<File | null>(null);

  const handleChange = (e: any) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleFileChange = (e: any) => {
    if (e.target.files && e.target.files[0]) {
      setThumbnail(e.target.files[0]);
    }
  };

  const handleSubmit = async (e: any) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
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

      const headers: any = {};
      if (csrfToken) {
        headers["X-CSRFToken"] = csrfToken;
      }

      // Use FormData for file uploads
      const data = new FormData();
      data.append('title', formData.title);
      data.append('description', formData.description);
      data.append('price', formData.price);
      if (thumbnail) {
        data.append('thumbnail', thumbnail);
      }

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/courses/`, {
        method: "POST",
        headers: headers,
        body: data,
        credentials: "include"
      });

      if (res.ok) {
        const data = await res.json();
        router.push(`/admin/courses/${data.id}`);
      } else {
        const errData = await res.json();
        setError(errData.detail || "Failed to create course. Ensure you have permissions.");
      }
    } catch (err) {
      setError("Network error. Could not reach the server.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex items-center gap-3 mb-6">
        <Link href="/admin/courses" className="w-8 h-8 bg-white/5 border border-white/5 rounded-full flex items-center justify-center hover:bg-white/10 transition-colors text-zinc-400 hover:text-white">
          <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="19" y1="12" x2="5" y2="12"></line>
            <polyline points="12 19 5 12 12 5"></polyline>
          </svg>
        </Link>
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">Create New Course</h1>
        </div>
      </div>

      <motion.div 
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        className="bg-[#18181b] border border-white/5 rounded-xl p-6 shadow-sm"
      >
        {error && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-lg mb-5 text-sm flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Course Title</label>
            <input 
              type="text" 
              name="title"
              required
              value={formData.title}
              onChange={handleChange}
              placeholder="e.g. Complete Web Development Bootcamp"
              className="w-full px-3 py-2 bg-[#09090b] border border-white/10 rounded-lg text-sm text-white focus:outline-none focus:border-white/30 transition-colors placeholder:text-zinc-600"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Description</label>
            <textarea 
              name="description"
              required
              rows={3}
              value={formData.description}
              onChange={handleChange}
              placeholder="What will students learn in this course?"
              className="w-full px-3 py-2 bg-[#09090b] border border-white/10 rounded-lg text-sm text-white focus:outline-none focus:border-white/30 transition-colors resize-none placeholder:text-zinc-600"
            />
          </div>

          <div>
            <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Price (₹)</label>
            <div className="relative">
              <span className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500 text-sm font-medium">₹</span>
              <input 
                type="number" 
                name="price"
                step="0.01"
                min="0"
                required
                value={formData.price}
                onChange={handleChange}
                className="w-full pl-7 pr-3 py-2 bg-[#09090b] border border-white/10 rounded-lg text-sm text-white focus:outline-none focus:border-white/30 transition-colors"
              />
            </div>
          </div>

          <div>
            <label className="block text-xs font-semibold text-zinc-400 mb-1.5 uppercase tracking-wide">Course Thumbnail</label>
            <input 
              type="file" 
              accept="image/*"
              onChange={handleFileChange}
              className="w-full px-3 py-2 bg-[#09090b] border border-white/10 rounded-lg text-sm text-zinc-400 focus:outline-none focus:border-white/30 transition-colors file:mr-3 file:py-1 file:px-3 file:rounded file:border-0 file:text-xs file:font-medium file:bg-white/10 file:text-white hover:file:bg-white/20 cursor-pointer"
            />
          </div>

          <div className="pt-4 mt-2">
            <button 
              type="submit"
              disabled={loading}
              className="w-full py-2.5 bg-white text-black text-sm font-medium rounded-lg hover:bg-zinc-200 transition-colors shadow-sm disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <svg className="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle><path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path></svg>
                  Creating...
                </>
              ) : (
                "Create Course & Continue to Modules"
              )}
            </button>
          </div>
        </form>
      </motion.div>
    </div>
  );
}
