"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Award, BookOpen, Check, CheckCircle2, ChevronRight, ArrowLeft } from "lucide-react";

type Step = "TRACK" | "DETAILS";

interface OnboardingField {
  name: string;
  label: string;
  type: "text" | "textarea" | "date" | "dropdown" | "checkbox";
  required: boolean;
  options?: string[];
  order?: number;
}

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState<Step>("TRACK");
  const [selectedTrack, setSelectedTrack] = useState<"CAREER_ACADEMY" | "REGULAR" | null>(null);

  const [fields, setFields] = useState<OnboardingField[]>([]);
  const [formData, setFormData] = useState<Record<string, any>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  const getCsrfToken = () => {
    let csrfToken = "";
    if (typeof document !== "undefined" && document.cookie) {
      const cookies = document.cookie.split(";");
      for (let i = 0; i < cookies.length; i++) {
        const cookie = cookies[i].trim();
        if (cookie.startsWith("csrftoken=")) {
          csrfToken = decodeURIComponent(cookie.substring("csrftoken=".length));
          break;
        }
      }
    }
    return csrfToken;
  };

  useEffect(() => {
    const initData = async () => {
      try {
        const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

        // Fetch onboarding fields and current user
        const [fieldsRes, userRes] = await Promise.allSettled([
          fetch(`${apiBase}/api/users/onboarding-fields/`, { credentials: "include" }),
          fetch(`${apiBase}/api/users/me/`, { credentials: "include" }),
        ]);

        let initialData: Record<string, any> = {};

        if (userRes.status === "fulfilled" && userRes.value.ok) {
          const user = await userRes.value.json();
          if (user.student_type === "CAREER_ACADEMY" || user.student_type === "REGULAR") {
            setSelectedTrack(user.student_type);
          }
          initialData.full_name = [user.first_name, user.last_name].filter(Boolean).join(" ");
          initialData.email = user.email || "";
          initialData.phone_number = user.phone_number || "";
          initialData.state = user.state || "";
          initialData.district = user.district || "";
          initialData.pincode = user.pincode || "";

          if (user.onboarding_data && typeof user.onboarding_data === "object") {
            initialData = { ...initialData, ...user.onboarding_data };
          }
        }

        if (fieldsRes.status === "fulfilled" && fieldsRes.value.ok) {
          const loadedFields: OnboardingField[] = await fieldsRes.value.json();
          setFields(loadedFields);

          loadedFields.forEach((f) => {
            if (f.name === "student_type") return;
            if (initialData[f.name] === undefined) {
              initialData[f.name] = f.type === "checkbox" ? false : "";
            }
          });
        }

        setFormData(initialData);
      } catch (err) {
        console.error("Failed to load onboarding info", err);
      } finally {
        setLoading(false);
      }
    };

    initData();
  }, []);

  const handleTrackSelect = (track: "CAREER_ACADEMY" | "REGULAR") => {
    setSelectedTrack(track);
    setErrorMsg("");
  };

  const handleProceedToDetails = () => {
    if (!selectedTrack) {
      setErrorMsg("Please select either Career Academy or Regular track to proceed.");
      return;
    }
    setStep("DETAILS");
  };

  const handleInputChange = (name: string, value: any) => {
    setFormData((prev) => ({ ...prev, [name]: value }));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg("");

    if (!selectedTrack) {
      setErrorMsg("Please select a learning track.");
      setStep("TRACK");
      return;
    }

    const fullName = (formData.full_name || formData.name || "").trim();
    if (!fullName) {
      setErrorMsg("Full Name is required.");
      return;
    }

    const state = (formData.state || "").trim();
    if (!state) {
      setErrorMsg("State is required.");
      return;
    }

    const district = (formData.district || "").trim();
    if (!district) {
      setErrorMsg("District is required.");
      return;
    }

    const pincode = (formData.pincode || "").trim();
    if (!pincode) {
      setErrorMsg("Pincode is required.");
      return;
    }

    setSubmitting(true);
    try {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const payload = {
        ...formData,
        student_type: selectedTrack,
      };

      const res = await fetch(`${apiBase}/api/users/save-profile/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        body: JSON.stringify(payload),
        credentials: "include",
      });

      if (res.ok) {
        window.location.href = "/dashboard";
      } else {
        const errData = await res.json().catch(() => ({}));
        setErrorMsg(errData.error || errData.detail || "Failed to save profile. Please check all fields.");
      }
    } catch (err) {
      console.error(err);
      setErrorMsg("Network error saving profile. Please check your connection.");
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center text-white">
        <div className="w-9 h-9 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin"></div>
      </div>
    );
  }

  // Filter out student_type from Step 2 form since Step 1 chooses it
  const visibleFields = fields.filter((f) => f.name !== "student_type");

  return (
    <div className="min-h-screen bg-[#050505] text-white font-sans flex flex-col pt-12 pb-24 px-4 sm:px-6">
      <div className="max-w-3xl mx-auto w-full">
        {/* Step Indicator */}
        <div className="flex items-center justify-center mb-8">
          <div className="flex items-center gap-3">
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${
                step === "TRACK"
                  ? "bg-[#facc15] text-black"
                  : "bg-zinc-800 text-zinc-300 border border-zinc-700"
              }`}
            >
              1
            </div>
            <span
              className={`text-sm font-semibold ${
                step === "TRACK" ? "text-white" : "text-zinc-400"
              }`}
            >
              Choose Track
            </span>
            <div className="w-12 h-[2px] bg-zinc-800"></div>
            <div
              className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-bold ${
                step === "DETAILS"
                  ? "bg-[#facc15] text-black"
                  : "bg-zinc-900 text-zinc-500 border border-zinc-800"
              }`}
            >
              2
            </div>
            <span
              className={`text-sm font-semibold ${
                step === "DETAILS" ? "text-white" : "text-zinc-500"
              }`}
            >
              Student Details
            </span>
          </div>
        </div>

        {errorMsg && (
          <div className="mb-6 p-4 rounded-xl bg-red-950/40 border border-red-800/60 text-red-300 text-sm flex items-center gap-3">
            <span className="w-2 h-2 rounded-full bg-red-500"></span>
            {errorMsg}
          </div>
        )}

        {step === "TRACK" ? (
          /* STEP 1: CAREER ACADEMY VS REGULAR */
          <div className="bg-[#0e0e10] border border-white/10 rounded-3xl p-6 sm:p-10 shadow-2xl">
            <div className="text-center max-w-xl mx-auto mb-10">
              <span className="text-xs font-bold uppercase tracking-wider text-[#facc15] bg-[#facc15]/10 px-3 py-1 rounded-full border border-[#facc15]/20">
                Enrollment Step 1
              </span>
              <h1 className="text-3xl sm:text-4xl font-bold mt-4 mb-3 tracking-tight">
                Select Your Learning Path
              </h1>
              <p className="text-zinc-400 text-sm sm:text-base leading-relaxed">
                Choose the program track that best fits your goals in classical dance, vocals, or
                instrumental arts.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
              {/* Card 1: Career Academy */}
              <div
                onClick={() => handleTrackSelect("CAREER_ACADEMY")}
                className={`relative rounded-2xl p-6 cursor-pointer transition-all duration-200 border-2 flex flex-col justify-between ${
                  selectedTrack === "CAREER_ACADEMY"
                    ? "bg-[#181508] border-[#facc15] shadow-lg shadow-[#facc15]/10"
                    : "bg-zinc-900/60 border-zinc-800 hover:border-zinc-700 hover:bg-zinc-900"
                }`}
              >
                <div>
                  <div className="flex items-center justify-between mb-4">
                    <div className="w-12 h-12 rounded-xl bg-[#facc15]/10 flex items-center justify-center text-[#facc15]">
                      <Award className="w-6 h-6" />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-extrabold uppercase px-2 py-0.5 rounded bg-[#facc15]/15 text-[#facc15] border border-[#facc15]/30">
                        Diploma & Certified
                      </span>
                      <div
                        className={`w-5 h-5 rounded-full border flex items-center justify-center ${
                          selectedTrack === "CAREER_ACADEMY"
                            ? "border-[#facc15] bg-[#facc15]"
                            : "border-zinc-700"
                        }`}
                      >
                        {selectedTrack === "CAREER_ACADEMY" && (
                          <div className="w-2 h-2 rounded-full bg-black"></div>
                        )}
                      </div>
                    </div>
                  </div>

                  <h3 className="text-xl font-bold text-white mb-2">Career Academy</h3>
                  <p className="text-xs font-semibold text-[#facc15] mb-3">
                    Professional Training & Career Pathway
                  </p>
                  <p className="text-zinc-400 text-xs sm:text-sm leading-relaxed mb-6">
                    A rigorous, multi-year curriculum designed for dedicated dancers and musicians
                    aiming for certification, graduation, professional performance, and teaching.
                  </p>
                </div>

                <div className="space-y-2.5 pt-4 border-t border-zinc-800/80 bg-black/20 -mx-6 -mb-6 p-5 rounded-b-2xl">
                  <div className="flex items-start gap-2.5 text-xs text-zinc-300">
                    <CheckCircle2 className="w-4 h-4 text-[#facc15] shrink-0 mt-0.5" />
                    <span>Recognized accreditation & stage certifications</span>
                  </div>
                  <div className="flex items-start gap-2.5 text-xs text-zinc-300">
                    <CheckCircle2 className="w-4 h-4 text-[#facc15] shrink-0 mt-0.5" />
                    <span>One-on-one guru reviews & assignment grading</span>
                  </div>
                  <div className="flex items-start gap-2.5 text-xs text-zinc-300">
                    <CheckCircle2 className="w-4 h-4 text-[#facc15] shrink-0 mt-0.5" />
                    <span>Arangetram, productions & stage opportunities</span>
                  </div>
                </div>
              </div>

              {/* Card 2: Regular Track */}
              <div
                onClick={() => handleTrackSelect("REGULAR")}
                className={`relative rounded-2xl p-6 cursor-pointer transition-all duration-200 border-2 flex flex-col justify-between ${
                  selectedTrack === "REGULAR"
                    ? "bg-[#13101c] border-[#A78BFA] shadow-lg shadow-[#A78BFA]/10"
                    : "bg-zinc-900/60 border-zinc-800 hover:border-zinc-700 hover:bg-zinc-900"
                }`}
              >
                <div>
                  <div className="flex items-center justify-between mb-4">
                    <div className="w-12 h-12 rounded-xl bg-purple-500/10 flex items-center justify-center text-[#A78BFA]">
                      <BookOpen className="w-6 h-6" />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-[11px] font-extrabold uppercase px-2 py-0.5 rounded bg-purple-500/15 text-[#A78BFA] border border-purple-500/30">
                        Flexible & Hobby
                      </span>
                      <div
                        className={`w-5 h-5 rounded-full border flex items-center justify-center ${
                          selectedTrack === "REGULAR"
                            ? "border-[#A78BFA] bg-[#A78BFA]"
                            : "border-zinc-700"
                        }`}
                      >
                        {selectedTrack === "REGULAR" && (
                          <div className="w-2 h-2 rounded-full bg-black"></div>
                        )}
                      </div>
                    </div>
                  </div>

                  <h3 className="text-xl font-bold text-white mb-2">Regular Learning</h3>
                  <p className="text-xs font-semibold text-[#A78BFA] mb-3">
                    Recreational & Self-Paced Track
                  </p>
                  <p className="text-zinc-400 text-xs sm:text-sm leading-relaxed mb-6">
                    Learn traditional Indian classical arts at your own pace for personal enjoyment,
                    fitness, cultural appreciation, and creative fulfillment.
                  </p>
                </div>

                <div className="space-y-2.5 pt-4 border-t border-zinc-800/80 bg-black/20 -mx-6 -mb-6 p-5 rounded-b-2xl">
                  <div className="flex items-start gap-2.5 text-xs text-zinc-300">
                    <CheckCircle2 className="w-4 h-4 text-[#A78BFA] shrink-0 mt-0.5" />
                    <span>100% Flexible schedule — learn anytime, anywhere</span>
                  </div>
                  <div className="flex items-start gap-2.5 text-xs text-zinc-300">
                    <CheckCircle2 className="w-4 h-4 text-[#A78BFA] shrink-0 mt-0.5" />
                    <span>Complete access to tutorials, mudras & recitals</span>
                  </div>
                  <div className="flex items-start gap-2.5 text-xs text-zinc-300">
                    <CheckCircle2 className="w-4 h-4 text-[#A78BFA] shrink-0 mt-0.5" />
                    <span>Open to all ages, beginners, and amateurs</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Step 1 Continue Button */}
            <div className="flex justify-end pt-4">
              <button
                type="button"
                onClick={handleProceedToDetails}
                disabled={!selectedTrack}
                className="w-full sm:w-auto px-8 py-3.5 bg-[#facc15] hover:bg-yellow-400 text-black font-bold rounded-xl transition-all flex items-center justify-center gap-2 disabled:opacity-40 disabled:cursor-not-allowed shadow-lg shadow-[#facc15]/10"
              >
                <span>Continue to Details</span>
                <ChevronRight className="w-5 h-5" />
              </button>
            </div>
          </div>
        ) : (
          /* STEP 2: PROFILE DETAILS FORM */
          <div className="bg-[#0e0e10] border border-white/10 rounded-3xl p-6 sm:p-10 shadow-2xl">
            {/* Top Selected Track Banner */}
            <div className="flex items-center justify-between p-4 rounded-2xl bg-zinc-900 border border-zinc-800 mb-8">
              <div className="flex items-center gap-3">
                <div
                  className={`w-9 h-9 rounded-lg flex items-center justify-center ${
                    selectedTrack === "CAREER_ACADEMY"
                      ? "bg-[#facc15]/15 text-[#facc15]"
                      : "bg-purple-500/15 text-[#A78BFA]"
                  }`}
                >
                  {selectedTrack === "CAREER_ACADEMY" ? (
                    <Award className="w-5 h-5" />
                  ) : (
                    <BookOpen className="w-5 h-5" />
                  )}
                </div>
                <div>
                  <span className="text-xs text-zinc-400">Selected Program Track:</span>
                  <div className="text-sm font-bold text-white">
                    {selectedTrack === "CAREER_ACADEMY" ? "Career Academy" : "Regular Learning"}
                  </div>
                </div>
              </div>
              <button
                type="button"
                onClick={() => setStep("TRACK")}
                className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-[#facc15] transition-colors"
              >
                Switch Track
              </button>
            </div>

            <div className="mb-8">
              <h2 className="text-2xl font-bold text-white mb-1">Student Information</h2>
              <p className="text-zinc-400 text-sm">
                Please provide your contact and residential details for batch coordination and
                certificate records.
              </p>
            </div>

            <form onSubmit={handleSubmit} className="space-y-5">
              {visibleFields.map((field) => (
                <div key={field.name} className="flex flex-col">
                  <label className="text-sm font-semibold mb-2 text-zinc-300">
                    {field.label} {field.required && <span className="text-red-500">*</span>}
                  </label>

                  {field.type === "text" && (
                    <input
                      type="text"
                      required={field.required}
                      placeholder={`Enter ${field.label.toLowerCase()}`}
                      value={formData[field.name] || ""}
                      onChange={(e) => handleInputChange(field.name, e.target.value)}
                      className="w-full bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-white placeholder-zinc-500 focus:outline-none focus:border-[#facc15] transition-colors text-sm"
                    />
                  )}

                  {field.type === "textarea" && (
                    <textarea
                      required={field.required}
                      placeholder={`Enter ${field.label.toLowerCase()}`}
                      value={formData[field.name] || ""}
                      onChange={(e) => handleInputChange(field.name, e.target.value)}
                      rows={3}
                      className="w-full bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-white placeholder-zinc-500 focus:outline-none focus:border-[#facc15] transition-colors text-sm"
                    />
                  )}

                  {field.type === "date" && (
                    <input
                      type="date"
                      required={field.required}
                      value={formData[field.name] || ""}
                      onChange={(e) => handleInputChange(field.name, e.target.value)}
                      className="w-full bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-white focus:outline-none focus:border-[#facc15] transition-colors text-sm [color-scheme:dark]"
                    />
                  )}

                  {field.type === "dropdown" && (
                    <select
                      required={field.required}
                      value={formData[field.name] || ""}
                      onChange={(e) => handleInputChange(field.name, e.target.value)}
                      className="w-full bg-zinc-900 border border-zinc-800 rounded-xl px-4 py-3 text-white focus:outline-none focus:border-[#facc15] transition-colors text-sm"
                    >
                      <option value="" disabled>
                        Select {field.label}
                      </option>
                      {field.options &&
                        field.options.map((opt: string) => (
                          <option key={opt} value={opt} className="bg-zinc-900 text-white">
                            {opt}
                          </option>
                        ))}
                    </select>
                  )}

                  {field.type === "checkbox" && (
                    <div className="flex items-center gap-3 mt-1">
                      <input
                        type="checkbox"
                        required={field.required}
                        checked={formData[field.name] || false}
                        onChange={(e) => handleInputChange(field.name, e.target.checked)}
                        className="w-5 h-5 rounded border-zinc-700 bg-zinc-900 text-[#facc15] focus:ring-[#facc15] focus:ring-offset-black"
                      />
                      <span className="text-sm text-zinc-300">I confirm this detail</span>
                    </div>
                  )}
                </div>
              ))}

              <div className="pt-4 flex flex-col sm:flex-row items-center gap-3">
                <button
                  type="button"
                  onClick={() => setStep("TRACK")}
                  className="w-full sm:w-auto px-6 py-3.5 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 font-semibold rounded-xl transition-colors flex items-center justify-center gap-2 text-sm order-2 sm:order-1"
                >
                  <ArrowLeft className="w-4 h-4" />
                  <span>Back to Track Selection</span>
                </button>

                <button
                  type="submit"
                  disabled={submitting}
                  className="w-full sm:flex-1 py-3.5 bg-[#facc15] hover:bg-yellow-400 text-black font-bold rounded-xl transition-all flex items-center justify-center gap-2 text-sm shadow-lg shadow-[#facc15]/10 order-1 sm:order-2"
                >
                  {submitting ? (
                    <div className="w-5 h-5 border-2 border-black border-t-transparent rounded-full animate-spin"></div>
                  ) : (
                    <>
                      <span>Complete Profile & Start Learning</span>
                      <Check className="w-4 h-4" />
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        )}
      </div>
    </div>
  );
}
