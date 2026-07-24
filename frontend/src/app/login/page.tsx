"use client";

import Link from "next/link";
import { useState, useEffect } from "react";
import { motion } from "framer-motion";
import CountrySelect from "@/components/CountrySelect";

export default function Login() {
  const [formData, setFormData] = useState({
    identifier: "",
    otp: ""
  });
  
  const [error, setError] = useState("");
  const [step, setStep] = useState(1); // 1 = identifier, 2 = otp
  const [authMethod, setAuthMethod] = useState<'mobile' | 'email'>('mobile');
  const [countryCode, setCountryCode] = useState('+91');

  useEffect(() => {
    fetch("http://localhost:8000/api/auth/user/", { credentials: 'include' })
      .then(res => {
        if (res.ok) {
          res.json().then(data => {
            if (data.is_superuser || data.is_teacher) {
              window.location.href = "/admin";
            } else {
              window.location.href = "/dashboard";
            }
          });
        }
      })
      .catch(err => console.error("Auth check failed", err));
  }, []);

  const handleChange = (e: any) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handleSendOTP = async (e: any) => {
    e.preventDefault();
    setError("");
    
    try {
      const finalIdentifier = authMethod === 'mobile' 
        ? `${countryCode}${formData.identifier}`
        : formData.identifier;

      const res = await fetch("http://localhost:8000/api/users/send-otp/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: finalIdentifier })
      });
      
      const data = await res.json();
      if (res.ok) {
        setStep(2);
      } else {
        setError(data.error || "Failed to send OTP");
      }
    } catch (err) {
      setError("Failed to connect to the server.");
    }
  };

  const handleVerifyOTP = async (e: any) => {
    e.preventDefault();
    setError("");
    
    try {
      const finalIdentifier = authMethod === 'mobile' 
        ? `${countryCode}${formData.identifier}`
        : formData.identifier;

      const res = await fetch("http://localhost:8000/api/users/verify-otp/", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ identifier: finalIdentifier, otp: formData.otp })
      });
      
      const data = await res.json();
      if (res.ok) {
        // Fetch user details to determine redirect
        const userRes = await fetch("http://localhost:8000/api/auth/user/", { credentials: 'include' });
        if (userRes.ok) {
          const userData = await userRes.json();
          if (userData.is_superuser || userData.is_teacher) {
            window.location.href = "/admin";
            return;
          }
        }
        window.location.href = "/dashboard";
      } else {
        setError(data.error || "Invalid OTP");
      }
    } catch (err) {
      setError("Failed to connect to the server.");
    }
  };

  const handleSocialLogin = (provider: string) => {
    window.location.href = `http://localhost:8000/accounts/${provider}/login/`;
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-black font-sans text-white p-6 relative overflow-hidden">
      
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] bg-[#facc15]/10 rounded-full blur-[150px] pointer-events-none" />
      
      <Link href="/" className="absolute top-8 left-8 z-50">
        <img src="/img/logo.png" alt="Natya LMS Logo" className="h-10 w-auto" />
      </Link>

      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full max-w-md bg-[#0a0a0a] border border-white/10 rounded-[2rem] p-8 relative z-10 shadow-2xl"
      >
        <div className="text-center mb-8">
          <h2 className="text-3xl font-semibold mb-2">Welcome back</h2>
          <p className="text-zinc-400">Sign in to your Natya LMS account.</p>
        </div>

        {error && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-3 rounded-xl mb-6 text-sm text-center">
            {error}
          </div>
        )}

        <div className="space-y-3 mb-8">
          <button 
            onClick={() => handleSocialLogin('google')}
            className="w-full flex items-center justify-center gap-3 px-4 py-3 bg-white text-black font-medium rounded-full hover:bg-zinc-200 transition-colors"
          >
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 48 48">
              <path fill="#FFC107" d="M43.611 20.083H42V20H24v8h11.303c-1.649 4.657-6.08 8-11.303 8c-6.627 0-12-5.373-12-12s5.373-12 12-12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4C12.955 4 4 12.955 4 24s8.955 20 20 20s20-8.955 20-20c0-1.341-.138-2.65-.389-3.917z"/><path fill="#FF3D00" d="m6.306 14.691l6.571 4.819C14.655 15.108 18.961 12 24 12c3.059 0 5.842 1.154 7.961 3.039l5.657-5.657C34.046 6.053 29.268 4 24 4C16.318 4 9.656 8.337 6.306 14.691z"/><path fill="#4CAF50" d="M24 44c5.166 0 9.86-1.977 13.409-5.192l-6.19-5.238A11.91 11.91 0 0 1 24 36c-5.222 0-9.654-3.343-11.124-8.089l-6.571 4.819C9.656 39.663 16.318 44 24 44z"/><path fill="#1976D2" d="M43.611 20.083H42V20H24v8h11.303a12.04 12.04 0 0 1-4.087 5.571l.003-.002l6.19 5.238C36.971 39.205 44 34 44 24c0-1.341-.138-2.65-.389-3.917z"/>
            </svg>
            Continue with Google
          </button>
        </div>

        <div className="flex items-center gap-4 mb-8">
          <div className="h-px bg-zinc-800 flex-1" />
          <span className="text-zinc-500 text-sm font-medium">OR OTP</span>
          <div className="h-px bg-zinc-800 flex-1" />
        </div>

        {step === 1 ? (
          <form onSubmit={handleSendOTP} className="space-y-4">
            
            <div className="flex gap-2 h-[50px] mb-4">
              <CountrySelect 
                value={countryCode} 
                onChange={setCountryCode} 
              />
              <input 
                type="tel" 
                name="identifier"
                placeholder="Mobile number" 
                required
                value={formData.identifier}
                onChange={handleChange}
                className="flex-1 px-4 py-3 bg-zinc-900 border border-zinc-800 rounded-xl text-white focus:outline-none focus:border-zinc-500 transition-colors h-full"
              />
            </div>
            
            <button 
              type="submit"
              className="w-full py-3 bg-gradient-to-r from-[#facc15] to-[#a16207] text-black font-semibold rounded-xl hover:opacity-90 transition-opacity mt-2"
            >
              Send OTP
            </button>
          </form>
        ) : (
          <form onSubmit={handleVerifyOTP} className="space-y-4">
            <div>
              <div className="text-sm text-zinc-400 mb-2">We sent a 6-digit code to {authMethod === 'mobile' ? `${countryCode}${formData.identifier}` : formData.identifier}</div>
              <input 
                type="text" 
                name="otp"
                placeholder="Enter 6-digit OTP" 
                required
                maxLength={6}
                value={formData.otp}
                onChange={handleChange}
                className="w-full px-4 py-3 text-center tracking-[0.5em] text-2xl bg-zinc-900 border border-zinc-800 rounded-xl text-white focus:outline-none focus:border-zinc-500 transition-colors"
              />
            </div>
            
            <button 
              type="submit"
              className="w-full py-3 bg-gradient-to-r from-[#facc15] to-[#a16207] text-black font-semibold rounded-xl hover:opacity-90 transition-opacity mt-2"
            >
              Verify and Log In
            </button>
            <button 
              type="button"
              onClick={() => setStep(1)}
              className="w-full py-2 text-zinc-400 hover:text-white text-sm transition-colors mt-2"
            >
              Change number/email
            </button>
          </form>
        )}

        <div className="mt-8 text-center text-zinc-500 text-sm">
          New here?{' '}
          <Link href="/register" className="text-white hover:underline font-medium">
            Sign Up
          </Link>
        </div>
      </motion.div>
    </div>
  );
}
