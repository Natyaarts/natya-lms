"use client";

import { useState } from "react";
import { motion } from "framer-motion";
import { Eye, EyeOff } from "lucide-react";

export default function AdminLogin() {
  const [formData, setFormData] = useState({
    username: "",
    password: ""
  });
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  const handleChange = (e: any) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSubmit = async (e: any) => {
    e.preventDefault();
    if (loading) return;

    setError("");
    setLoading(true);

    try {
      const payload = {
        username: formData.username.trim(),
        password: formData.password
      };

      // Extract CSRF token from cookies if it exists
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

      const headers: any = { "Content-Type": "application/json" };
      if (csrfToken) {
        headers["X-CSRFToken"] = csrfToken;
      }

      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/auth/login/`, {
        method: "POST",
        headers: headers,
        body: JSON.stringify(payload),
        credentials: "include" // Important for cookies
      });

      if (res.ok) {
        window.location.href = "/admin";
      } else {
        const text = await res.text();
        console.error("Login Error Status:", res.status);
        console.error("Login Error Text:", text);
        try {
          const data = JSON.parse(text);
          setError(data.non_field_errors?.[0] || data.username?.[0] || data.email?.[0] || "Invalid credentials");
        } catch (e) {
          setError(`Server error: ${res.status}`);
        }
        setLoading(false);
      }
    } catch (err) {
      setError("Failed to connect to server");
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-black font-sans text-white p-6 relative overflow-hidden">
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-red-500/10 rounded-full blur-[150px] pointer-events-none" />
      
      <div className="absolute top-8 left-8 z-50 flex items-center gap-2">
        <img src="/img/logo.png" alt="Natya Admin Logo" className="h-10 w-auto" />
        <span className="text-sm font-bold tracking-widest text-[#facc15] uppercase bg-[#facc15]/10 px-2 py-1 rounded">Admin</span>
      </div>

      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-md bg-[#0a0a0a] border border-white/10 rounded-[2rem] p-8 relative z-10 shadow-2xl"
      >
        <div className="text-center mb-8">
          <h2 className="text-3xl font-semibold mb-2">Admin Portal</h2>
          <p className="text-zinc-400">Sign in with your staff credentials.</p>
        </div>

        {error && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-xl mb-6 text-sm text-center">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <input 
              type="text" 
              name="username"
              placeholder="Username or Email" 
              required
              value={formData.username}
              onChange={handleChange}
              disabled={loading}
              className="w-full px-4 py-3 bg-zinc-900 border border-zinc-800 rounded-xl text-white focus:outline-none focus:border-zinc-500 transition-colors disabled:opacity-60"
            />
          </div>
          <div className="relative">
            <input 
              type={showPassword ? "text" : "password"} 
              name="password"
              placeholder="Password" 
              required
              value={formData.password}
              onChange={handleChange}
              disabled={loading}
              className="w-full pl-4 pr-12 py-3 bg-zinc-900 border border-zinc-800 rounded-xl text-white focus:outline-none focus:border-zinc-500 transition-colors disabled:opacity-60"
            />
            <button
              type="button"
              onClick={() => setShowPassword(!showPassword)}
              className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-white p-1 rounded-lg transition-colors focus:outline-none"
              title={showPassword ? "Hide password" : "Show password"}
              aria-label={showPassword ? "Hide password" : "Show password"}
            >
              {showPassword ? (
                <EyeOff className="w-5 h-5" />
              ) : (
                <Eye className="w-5 h-5" />
              )}
            </button>
          </div>
          
          <button 
            type="submit"
            disabled={loading}
            className="w-full py-3 bg-white text-black font-semibold rounded-xl hover:bg-zinc-200 transition-colors mt-4 disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2"
          >
            {loading ? (
              <>
                <span className="h-4 w-4 border-2 border-black/30 border-t-black rounded-full animate-spin" />
                <span>Signing In...</span>
              </>
            ) : (
              "Sign In"
            )}
          </button>
        </form>
      </motion.div>
    </div>
  );
}
