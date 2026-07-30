"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import { useRouter } from "next/navigation";

export default function OnboardingPage() {
  const router = useRouter();
  const [fields, setFields] = useState<any[]>([]);
  const [formData, setFormData] = useState<any>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const fetchFields = async () => {
      try {
        const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/onboarding-fields/`);
        if (res.ok) {
          const data = await res.json();
          setFields(data);
          
          // If no fields configured in backend, just skip onboarding
          if (data.length === 0) {
            handleSkip();
          }
        }
      } catch (err) {
        console.error(err);
      } finally {
        setLoading(false);
      }
    };
    fetchFields();
  }, []);

  const handleSkip = async () => {
    try {
      await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/save-profile/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
        credentials: "include"
      });
    } catch (e) {}
    window.location.href = "/dashboard";
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      const res = await fetch(`${process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'}/api/users/save-profile/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(formData),
        credentials: "include"
      });
      if (res.ok) {
        window.location.href = "/dashboard";
      } else {
        alert("Failed to save profile. Please try again.");
      }
    } catch (err) {
      console.error(err);
      alert("Error saving profile.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleInputChange = (name: string, value: any) => {
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center text-white">
        <div className="w-8 h-8 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white font-sans flex flex-col">
      <nav className="h-20 border-b border-white/10 flex items-center px-8 shrink-0">
        <Image src="/img/logo.png" alt="Natya LMS Logo" width={120} height={40} className="object-contain" />
      </nav>

      <div className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-xl bg-[#0a0a0a] border border-white/10 p-8 rounded-3xl">
          <h1 className="text-3xl font-bold mb-2">Complete your profile</h1>
          <p className="text-zinc-400 mb-8">Please tell us a little bit about yourself before we begin.</p>

          <form onSubmit={handleSubmit} className="space-y-6">
            {fields.map(field => (
              <div key={field.name} className="flex flex-col">
                <label className="text-sm font-medium mb-2 text-zinc-300">
                  {field.label} {field.required && <span className="text-red-500">*</span>}
                </label>
                
                {field.type === 'text' && (
                  <input
                    type="text"
                    required={field.required}
                    value={formData[field.name] || ''}
                    onChange={e => handleInputChange(field.name, e.target.value)}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 focus:outline-none focus:border-[#facc15] transition-colors"
                  />
                )}
                
                {field.type === 'textarea' && (
                  <textarea
                    required={field.required}
                    value={formData[field.name] || ''}
                    onChange={e => handleInputChange(field.name, e.target.value)}
                    rows={3}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 focus:outline-none focus:border-[#facc15] transition-colors"
                  />
                )}

                {field.type === 'date' && (
                  <input
                    type="date"
                    required={field.required}
                    value={formData[field.name] || ''}
                    onChange={e => handleInputChange(field.name, e.target.value)}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 focus:outline-none focus:border-[#facc15] transition-colors [color-scheme:dark]"
                  />
                )}

                {field.type === 'dropdown' && (
                  <select
                    required={field.required}
                    value={formData[field.name] || ''}
                    onChange={e => handleInputChange(field.name, e.target.value)}
                    className="w-full bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 focus:outline-none focus:border-[#facc15] transition-colors"
                  >
                    <option value="" disabled>Select an option</option>
                    {field.options && field.options.map((opt: string) => (
                      <option key={opt} value={opt}>{opt}</option>
                    ))}
                  </select>
                )}

                {field.type === 'checkbox' && (
                  <div className="flex items-center gap-3">
                    <input
                      type="checkbox"
                      required={field.required}
                      checked={formData[field.name] || false}
                      onChange={e => handleInputChange(field.name, e.target.checked)}
                      className="w-5 h-5 rounded border-zinc-800 bg-zinc-900 text-[#facc15] focus:ring-[#facc15] focus:ring-offset-black"
                    />
                    <span className="text-sm text-zinc-400">Yes</span>
                  </div>
                )}
              </div>
            ))}

            <button
              type="submit"
              disabled={submitting}
              className="w-full bg-[#facc15] text-black font-semibold rounded-xl py-4 hover:bg-yellow-400 transition-colors mt-8 flex justify-center items-center"
            >
              {submitting ? (
                <div className="w-6 h-6 border-2 border-black border-t-transparent rounded-full animate-spin"></div>
              ) : (
                "Save Profile & Continue"
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
}
