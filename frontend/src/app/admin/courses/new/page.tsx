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

      const res = await fetch("http://localhost:8000/api/courses/", {
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
    <div className="max-w-3xl mx-auto">
      <div className="flex items-center gap-4 mb-8">
        <Link href="/admin/courses" className="w-10 h-10 bg-zinc-900 rounded-full flex items-center justify-center hover:bg-zinc-800 transition-colors">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="19" y1="12" x2="5" y2="12"></line>
            <polyline points="12 19 5 12 12 5"></polyline>
          </svg>
        </Link>
        <h1 className="text-3xl font-bold">Create New Course</h1>
      </div>

      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="bg-zinc-900 border border-white/10 rounded-2xl p-8"
      >
        {error && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl mb-6 text-sm">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-6">
          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Course Title</label>
            <input 
              type="text" 
              name="title"
              required
              value={formData.title}
              onChange={handleChange}
              placeholder="e.g. Complete Web Development Bootcamp"
              className="w-full px-4 py-3 bg-black border border-white/10 rounded-xl text-white focus:outline-none focus:border-[#facc15] transition-colors"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Description</label>
            <textarea 
              name="description"
              required
              rows={4}
              value={formData.description}
              onChange={handleChange}
              placeholder="What will students learn in this course?"
              className="w-full px-4 py-3 bg-black border border-white/10 rounded-xl text-white focus:outline-none focus:border-[#facc15] transition-colors resize-none"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Price (₹)</label>
            <div className="relative">
              <span className="absolute left-4 top-1/2 -translate-y-1/2 text-zinc-500 font-medium">₹</span>
              <input 
                type="number" 
                name="price"
                step="0.01"
                min="0"
                required
                value={formData.price}
                onChange={handleChange}
                className="w-full pl-8 pr-4 py-3 bg-black border border-white/10 rounded-xl text-white focus:outline-none focus:border-[#facc15] transition-colors"
              />
            </div>
          </div>

          <div>
            <label className="block text-sm font-medium text-zinc-400 mb-2">Course Thumbnail</label>
            <input 
              type="file" 
              accept="image/*"
              onChange={handleFileChange}
              className="w-full px-4 py-3 bg-black border border-white/10 rounded-xl text-white focus:outline-none focus:border-[#facc15] transition-colors file:mr-4 file:py-2 file:px-4 file:rounded-full file:border-0 file:text-sm file:font-semibold file:bg-[#facc15] file:text-black hover:file:bg-[#eab308]"
            />
          </div>

          <div className="pt-6 border-t border-white/10">
            <button 
              type="submit"
              disabled={loading}
              className="w-full py-4 bg-[#facc15] text-black font-semibold rounded-xl hover:bg-[#eab308] transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <div className="w-5 h-5 border-2 border-black/20 border-t-black rounded-full animate-spin" />
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
