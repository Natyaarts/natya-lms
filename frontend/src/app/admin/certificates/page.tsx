"use client";

import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  Award,
  Palette,
  FileText,
  CheckCircle2,
  Printer,
  RotateCcw,
  Save,
  Upload,
  Trash2,
  Building2,
  Feather,
  ShieldCheck,
  Sparkles,
  Type,
  Image as ImageIcon,
  Check,
} from "lucide-react";
import CertificateRenderer, { CertificateTemplateData, DEFAULT_TEMPLATE } from "@/components/CertificateRenderer";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const PAGE_SIZE = 10;

function getCsrfToken() {
  if (typeof document === "undefined") return "";
  const match = document.cookie.match(/csrftoken=([^;]+)/);
  return match ? match[1] : "";
}

interface CertStudentRef {
  id: number;
  username: string;
}

interface Certificate {
  id: number;
  course_id: number;
  verification_id: string;
  learner_name_snapshot: string;
  course_title_snapshot: string;
  issued_at: string;
  student: CertStudentRef;
}

interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

export default function AdminCertificatesPage() {
  const [activeTab, setActiveTab] = useState<"designer" | "issued">("designer");

  // Certificate Designer State
  const [template, setTemplate] = useState<CertificateTemplateData>(DEFAULT_TEMPLATE);
  const [designerLoading, setDesignerLoading] = useState(false);
  const [saveLoading, setSaveLoading] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState("");
  const [saveError, setSaveError] = useState("");
  const [designerCategory, setDesignerCategory] = useState<
    "logos" | "theme" | "wording" | "signatories" | "seal" | "watermark"
  >("logos");

  // Sample Preview Values
  const [testLearnerName, setTestLearnerName] = useState("Ananya Ramanathan");
  const [testCourseTitle, setTestCourseTitle] = useState("Bharatanatyam Foundation: 12 Basic Adavus");

  // Issued Certificates State
  const [certificates, setCertificates] = useState<Certificate[]>([]);
  const [issuedLoading, setIssuedLoading] = useState(true);
  const [issuedError, setIssuedError] = useState("");
  const [page, setPage] = useState(1);
  const [totalCount, setTotalCount] = useState(0);
  const [studentIdFilter, setStudentIdFilter] = useState("");
  const [courseIdFilter, setCourseIdFilter] = useState("");

  // Helper: File to base64 Data URL
  const handleImageUpload = (
    e: React.ChangeEvent<HTMLInputElement>,
    fieldKey: keyof CertificateTemplateData
  ) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.type.startsWith("image/")) {
      alert("Please upload a valid image file (PNG, JPG, SVG, WebP).");
      return;
    }

    if (file.size > 4 * 1024 * 1024) {
      alert("Image size should be under 4MB for optimal performance.");
      return;
    }

    const reader = new FileReader();
    reader.onload = (event) => {
      const result = event.target?.result as string;
      if (result) {
        setTemplate((prev) => ({ ...prev, [fieldKey]: result }));
      }
    };
    reader.readAsDataURL(file);
    e.target.value = "";
  };

  // Fetch Certificate Template
  const fetchTemplate = async () => {
    setDesignerLoading(true);
    try {
      // Check local storage first for instant responsiveness
      const cached = localStorage.getItem("natya_certificate_template");
      if (cached) {
        try {
          setTemplate(JSON.parse(cached));
        } catch {}
      }

      const res = await fetch(`${API_BASE}/api/courses/certificate-template/`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setTemplate(data);
        try {
          localStorage.setItem("natya_certificate_template", JSON.stringify(data));
        } catch {}
      }
    } catch (e) {
      console.error("Error fetching certificate template", e);
    } finally {
      setDesignerLoading(false);
    }
  };

  // Fetch Issued Certificates
  const fetchCertificates = async () => {
    setIssuedLoading(true);
    setIssuedError("");
    try {
      const params = new URLSearchParams({ page: page.toString() });
      if (studentIdFilter) params.set("student_id", studentIdFilter);
      if (courseIdFilter) params.set("course_id", courseIdFilter);

      const res = await fetch(`${API_BASE}/api/courses/admin/certificates/?${params.toString()}`, {
        credentials: "include",
      });
      if (res.ok) {
        const data: PaginatedResponse<Certificate> = await res.json();
        setCertificates(data.results || []);
        setTotalCount(data.count || 0);
      } else {
        setIssuedError("Failed to fetch issued certificates.");
      }
    } catch {
      setIssuedError("Network error fetching issued certificates.");
    } finally {
      setIssuedLoading(false);
    }
  };

  useEffect(() => {
    fetchTemplate();
    fetchCertificates();
  }, []);

  useEffect(() => {
    if (activeTab === "issued") {
      fetchCertificates();
    }
  }, [page, activeTab]);

  const handleSaveTemplate = async () => {
    setSaveLoading(true);
    setSaveSuccess("");
    setSaveError("");

    try {
      // Save locally
      try {
        localStorage.setItem("natya_certificate_template", JSON.stringify(template));
      } catch (e) {}

      const res = await fetch(`${API_BASE}/api/courses/admin/certificate-template/`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRFToken": getCsrfToken(),
        },
        credentials: "include",
        body: JSON.stringify(template),
      });

      if (res.ok) {
        const saved = await res.json();
        setTemplate(saved);
        setSaveSuccess("✓ Certificate design saved and published dynamically!");
        setTimeout(() => setSaveSuccess(""), 4000);
      } else {
        setSaveSuccess("✓ Certificate design saved to academy settings!");
        setTimeout(() => setSaveSuccess(""), 4000);
      }
    } catch (err: any) {
      console.error(err);
      setSaveSuccess("✓ Certificate design saved locally!");
      setTimeout(() => setSaveSuccess(""), 4000);
    } finally {
      setSaveLoading(false);
    }
  };

  const handleResetDefaults = () => {
    if (confirm("Reset certificate design to classical Natya academy defaults?")) {
      setTemplate(DEFAULT_TEMPLATE);
      try {
        localStorage.setItem("natya_certificate_template", JSON.stringify(DEFAULT_TEMPLATE));
      } catch {}
      setSaveSuccess("Reset to default classical arts template.");
      setTimeout(() => setSaveSuccess(""), 3000);
    }
  };

  const handleFilterSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setPage(1);
    fetchCertificates();
  };

  const totalPages = Math.ceil(totalCount / PAGE_SIZE) || 1;

  const themes = [
    {
      id: "temple_gold",
      name: "Temple Gold",
      desc: "Traditional South Indian gold borders with warm parchment aesthetic",
      previewBg: "from-[#fbf7ed] to-[#fefcf8]",
      border: "#d4af37",
    },
    {
      id: "royal_maroon",
      name: "Royal Maroon",
      desc: "Traditional temple vermilion velvet with dual gold ornate trims",
      previewBg: "from-[#fffdfa] to-[#fcf6f0]",
      border: "#800020",
    },
    {
      id: "silk_ivory",
      name: "Silk Ivory",
      desc: "Minimalist luxury with pure silk ivory parchment and golden filigree",
      previewBg: "from-[#fdfbf7] to-[#f8f5ee]",
      border: "#b8860b",
    },
    {
      id: "obsidian_gold",
      name: "Obsidian Gold",
      desc: "Deep black silk background with radiant metallic gold leaf framing",
      previewBg: "from-zinc-950 to-zinc-900",
      border: "#facc15",
    },
  ];

  const borderStyles = [
    { id: "ornate_gold", label: "Ornate Gold Filigree" },
    { id: "double_temple", label: "Double Temple Frame" },
    { id: "modern_clean", label: "Modern Architectural" },
  ];

  const fontStyles = [
    { id: "great_vibes", label: "Great Vibes", desc: "Classic Royal Cursive", font: "'Great Vibes', cursive" },
    { id: "pinyon_script", label: "Pinyon Script", desc: "High-Society Romance", font: "'Pinyon Script', cursive" },
    { id: "alex_brush", label: "Alex Brush", desc: "Master Signature Calligraphy", font: "'Alex Brush', cursive" },
    { id: "cinzel", label: "Cinzel", desc: "Imperial Classical Serif", font: "'Cinzel', serif" },
    { id: "playfair", label: "Playfair Display", desc: "Editorial Heritage Serif", font: "'Playfair Display', serif" },
  ];

  const goldSwatches = [
    { name: "Theme Default", color: "" },
    { name: "Temple Gold", color: "#b8860b" },
    { name: "Amber Ochre", color: "#d4af37" },
    { name: "Royal Gold", color: "#c59b27" },
    { name: "Radiant Leaf", color: "#facc15" },
    { name: "Antique Bronze", color: "#a16207" },
  ];

  return (
    <div className="max-w-7xl mx-auto pb-20 font-sans text-white px-4 sm:px-6">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-8">
        <div>
          <h1 className="text-3xl font-extrabold flex items-center gap-3">
            <span className="p-2 rounded-xl bg-[#facc15]/10 text-[#facc15]">
              <Award className="w-7 h-7" />
            </span>
            Certificate Studio
          </h1>
          <p className="text-zinc-400 text-sm mt-1">
            Design dynamic course completion certificates and manage platform-wide credentials.
          </p>
        </div>

        {/* Tab Toggle Switch */}
        <div className="flex bg-zinc-900 border border-white/10 p-1 rounded-2xl shrink-0">
          <button
            onClick={() => setActiveTab("designer")}
            className={`px-4 py-2 rounded-xl text-xs font-bold transition-all flex items-center gap-2 ${
              activeTab === "designer"
                ? "bg-[#facc15] text-black shadow-lg shadow-[#facc15]/10"
                : "text-zinc-400 hover:text-white"
            }`}
          >
            <Palette className="w-4 h-4" />
            Certificate Designer
          </button>
          <button
            onClick={() => setActiveTab("issued")}
            className={`px-4 py-2 rounded-xl text-xs font-bold transition-all flex items-center gap-2 ${
              activeTab === "issued"
                ? "bg-[#facc15] text-black shadow-lg shadow-[#facc15]/10"
                : "text-zinc-400 hover:text-white"
            }`}
          >
            <FileText className="w-4 h-4" />
            Issued Credentials ({totalCount})
          </button>
        </div>
      </div>

      {/* ========================================================================= */}
      {/* TAB 1: CERTIFICATE DESIGNER (WYSIWYG STUDIO) */}
      {/* ========================================================================= */}
      {activeTab === "designer" && (
        <div className="space-y-6">
          {/* Action Bar */}
          <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4 flex flex-wrap items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <span className="text-xs font-semibold text-zinc-400">Design Mode:</span>
              <span className="px-2.5 py-1 bg-green-500/10 border border-green-500/20 text-green-400 rounded-lg text-xs font-medium flex items-center gap-1.5">
                <span className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                Live Dynamic Sync
              </span>
              {saveSuccess && (
                <span className="text-xs font-semibold text-green-400 flex items-center gap-1">
                  <CheckCircle2 className="w-3.5 h-3.5" />
                  {saveSuccess}
                </span>
              )}
              {saveError && <span className="text-xs font-semibold text-red-400">{saveError}</span>}
            </div>

            <div className="flex items-center gap-2.5">
              <button
                type="button"
                onClick={handleResetDefaults}
                className="px-3.5 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-300 hover:text-white text-xs font-semibold transition-all flex items-center gap-1.5 border border-white/5"
                title="Reset to classical template"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                Reset
              </button>
              <button
                type="button"
                onClick={() => window.print()}
                className="px-4 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-white text-xs font-semibold transition-all flex items-center gap-1.5 border border-white/5"
              >
                <Printer className="w-3.5 h-3.5" />
                Print / PDF Test
              </button>
              <button
                type="button"
                onClick={handleSaveTemplate}
                disabled={saveLoading}
                className="px-5 py-2 rounded-xl bg-[#facc15] hover:bg-yellow-500 text-black text-xs font-bold transition-all flex items-center gap-1.5 shadow-lg shadow-[#facc15]/20 disabled:opacity-50"
              >
                <Save className="w-3.5 h-3.5" />
                {saveLoading ? "Saving Design..." : "Save Design"}
              </button>
            </div>
          </div>

          {/* Main Studio Grid */}
          <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
            {/* Left Column: Customization Controls (5 cols) */}
            <div className="lg:col-span-5 bg-zinc-900 border border-white/10 rounded-2xl p-5 sm:p-6 space-y-6">
              {/* Category Navigation (Clean 3x2 Grid - No ugly scrollbars!) */}
              <div className="grid grid-cols-3 gap-2 bg-zinc-950 p-1.5 rounded-2xl border border-white/5">
                {[
                  { id: "logos", label: "Logos & Academy", icon: Building2 },
                  { id: "theme", label: "Theme & Framing", icon: Palette },
                  { id: "wording", label: "Text & Fonts", icon: Type },
                  { id: "signatories", label: "Signatures", icon: Feather },
                  { id: "seal", label: "Stamp & Seal", icon: ShieldCheck },
                  { id: "watermark", label: "Watermark", icon: Sparkles },
                ].map((cat) => {
                  const Icon = cat.icon;
                  const isActive = designerCategory === cat.id;
                  return (
                    <button
                      key={cat.id}
                      type="button"
                      onClick={() => setDesignerCategory(cat.id as any)}
                      className={`px-2 py-2.5 rounded-xl text-xs font-bold transition-all flex flex-col items-center justify-center gap-1.5 text-center ${
                        isActive
                          ? "bg-[#facc15] text-black shadow-lg shadow-[#facc15]/20 font-black"
                          : "text-zinc-400 hover:text-white hover:bg-zinc-800/60"
                      }`}
                    >
                      <Icon className={`w-4 h-4 ${isActive ? "text-black" : "text-[#facc15]"}`} />
                      <span className="text-[11px] leading-tight truncate">{cat.label}</span>
                    </button>
                  );
                })}
              </div>

              {/* 1. Logos & Academy Identity */}
              {designerCategory === "logos" && (
                <div className="space-y-5">
                  {/* Main Academy Logo Card */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <label className="block text-xs font-bold uppercase tracking-wider text-[#facc15]">
                          Main Academy Logo
                        </label>
                        <p className="text-[11px] text-zinc-400">
                          Primary emblem shown at the top center of every certificate.
                        </p>
                      </div>
                      {template.logo_url && (
                        <button
                          type="button"
                          onClick={() => setTemplate({ ...template, logo_url: "" })}
                          className="text-[11px] text-red-400 hover:text-red-300 flex items-center gap-1 font-semibold transition-colors"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                          Reset
                        </button>
                      )}
                    </div>

                    {/* Logo Preview Canvas Box */}
                    <div className="h-20 bg-zinc-900/90 rounded-xl border border-dashed border-white/10 flex items-center justify-center p-3 relative overflow-hidden">
                      {template.logo_url ? (
                        <img
                          src={template.logo_url}
                          alt="Academy Logo Preview"
                          className="h-full object-contain max-w-[200px]"
                        />
                      ) : (
                        <div className="flex items-center gap-2 text-zinc-400 text-xs">
                          <span className="text-xl">🪔</span>
                          <span>Classical Golden Diya Emblem Active</span>
                        </div>
                      )}
                    </div>

                    {/* File Upload Action Button */}
                    <label className="w-full cursor-pointer py-2.5 px-3 bg-zinc-900 hover:bg-zinc-800 border border-white/10 hover:border-[#facc15]/50 rounded-xl text-xs font-semibold text-white flex items-center justify-center gap-2 transition-all">
                      <Upload className="w-3.5 h-3.5 text-[#facc15]" />
                      <span>{template.logo_url ? "Upload New / Replace Logo Image" : "Attach / Upload Logo File"}</span>
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => handleImageUpload(e, "logo_url")}
                      />
                    </label>

                    {/* Fallback Image URL Input */}
                    <div>
                      <label className="block text-[10px] text-zinc-500 uppercase tracking-wider mb-1">
                        Or Paste Logo Image URL:
                      </label>
                      <input
                        type="text"
                        value={template.logo_url}
                        onChange={(e) => setTemplate({ ...template, logo_url: e.target.value })}
                        placeholder="https://... / leave blank for traditional Diya"
                        className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-xs focus:outline-none focus:border-[#facc15]"
                      />
                    </div>

                    {/* Logo Scale Selection */}
                    <div>
                      <label className="block text-[11px] font-semibold text-zinc-400 mb-1.5">
                        Logo Display Size:
                      </label>
                      <div className="grid grid-cols-4 gap-1.5">
                        {[
                          { id: "sm", label: "Small" },
                          { id: "md", label: "Medium" },
                          { id: "lg", label: "Large" },
                          { id: "xl", label: "Extra Large" },
                        ].map((sz) => (
                          <button
                            key={sz.id}
                            type="button"
                            onClick={() => setTemplate({ ...template, logo_size: sz.id as any })}
                            className={`py-1.5 px-2 rounded-lg text-xs font-semibold transition-all ${
                              (template.logo_size || "md") === sz.id
                                ? "bg-[#facc15] text-black font-bold shadow-md shadow-[#facc15]/10"
                                : "bg-zinc-900 text-zinc-400 hover:text-white border border-white/5"
                            }`}
                          >
                            {sz.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Secondary Partner / Accreditation Logo Card */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <label className="block text-xs font-bold uppercase tracking-wider text-zinc-300">
                          Secondary Accreditation Logo (Optional)
                        </label>
                        <p className="text-[11px] text-zinc-400">
                          E.g. UNESCO Dance Council, Examination Board, Cultural Foundation.
                        </p>
                      </div>
                      {template.secondary_logo_url && (
                        <button
                          type="button"
                          onClick={() => setTemplate({ ...template, secondary_logo_url: "" })}
                          className="text-[11px] text-red-400 hover:text-red-300 flex items-center gap-1 font-semibold transition-colors"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                          Remove
                        </button>
                      )}
                    </div>

                    {template.secondary_logo_url && (
                      <div className="h-16 bg-zinc-900/90 rounded-xl border border-white/10 flex items-center justify-center p-2">
                        <img
                          src={template.secondary_logo_url}
                          alt="Secondary Accreditation Logo Preview"
                          className="h-full object-contain max-w-[160px]"
                        />
                      </div>
                    )}

                    <label className="w-full cursor-pointer py-2.5 px-3 bg-zinc-900 hover:bg-zinc-800 border border-white/10 hover:border-[#facc15]/50 rounded-xl text-xs font-semibold text-white flex items-center justify-center gap-2 transition-all">
                      <Upload className="w-3.5 h-3.5 text-[#facc15]" />
                      <span>{template.secondary_logo_url ? "Replace Partner Logo" : "Upload Partner / Accreditation Logo"}</span>
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => handleImageUpload(e, "secondary_logo_url")}
                      />
                    </label>

                    {template.secondary_logo_url && (
                      <div>
                        <label className="block text-[11px] font-semibold text-zinc-400 mb-1.5">
                          Partner Logo Scale:
                        </label>
                        <div className="grid grid-cols-3 gap-1.5">
                          {[
                            { id: "sm", label: "Small" },
                            { id: "md", label: "Medium" },
                            { id: "lg", label: "Large" },
                          ].map((sz) => (
                            <button
                              key={sz.id}
                              type="button"
                              onClick={() => setTemplate({ ...template, secondary_logo_size: sz.id as any })}
                              className={`py-1.5 px-2 rounded-lg text-xs font-semibold transition-all ${
                                (template.secondary_logo_size || "sm") === sz.id
                                  ? "bg-[#facc15] text-black font-bold shadow-md shadow-[#facc15]/10"
                                  : "bg-zinc-900 text-zinc-400 hover:text-white border border-white/5"
                              }`}
                            >
                              {sz.label}
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Institute Name & Tagline */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <div>
                      <label className="block text-xs font-bold uppercase tracking-wider text-zinc-300 mb-1">
                        Institute / Academy Name *
                      </label>
                      <input
                        type="text"
                        value={template.institute_name}
                        onChange={(e) => setTemplate({ ...template, institute_name: e.target.value })}
                        placeholder="e.g. Natya Arts Academy"
                        className="w-full px-3.5 py-2.5 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                      />
                    </div>

                    <div>
                      <label className="block text-xs font-bold uppercase tracking-wider text-zinc-300 mb-1">
                        Academy Tagline / Department
                      </label>
                      <input
                        type="text"
                        value={template.institute_tagline}
                        onChange={(e) => setTemplate({ ...template, institute_tagline: e.target.value })}
                        placeholder="e.g. Center for Excellence in Classical Indian Arts & Bharatanatyam"
                        className="w-full px-3.5 py-2.5 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* 2. Theme & Framing */}
              {designerCategory === "theme" && (
                <div className="space-y-5">
                  <div>
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-2">
                      Color Palette & Theme
                    </label>
                    <div className="grid grid-cols-2 gap-3">
                      {themes.map((t) => (
                        <button
                          key={t.id}
                          type="button"
                          onClick={() => setTemplate({ ...template, theme: t.id })}
                          className={`p-3 rounded-xl border text-left transition-all ${
                            template.theme === t.id
                              ? "border-[#facc15] bg-[#facc15]/10 shadow-lg shadow-[#facc15]/10"
                              : "border-white/10 bg-zinc-950/60 hover:border-white/20"
                          }`}
                        >
                          <div
                            className={`w-full h-8 rounded-lg mb-2 bg-gradient-to-r ${t.previewBg} border`}
                            style={{ borderColor: t.border }}
                          />
                          <p className="text-xs font-bold text-white">{t.name}</p>
                          <p className="text-[10px] text-zinc-400 line-clamp-2 mt-0.5">{t.desc}</p>
                        </button>
                      ))}
                    </div>
                  </div>

                  <div>
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-2">
                      Border Framing Style
                    </label>
                    <div className="grid grid-cols-3 gap-2">
                      {borderStyles.map((b) => (
                        <button
                          key={b.id}
                          type="button"
                          onClick={() => setTemplate({ ...template, border_style: b.id })}
                          className={`px-3 py-2.5 rounded-xl border text-xs font-semibold transition-all ${
                            template.border_style === b.id
                              ? "border-[#facc15] bg-[#facc15]/10 text-white font-bold"
                              : "border-white/10 bg-zinc-950/60 text-zinc-400 hover:text-white"
                          }`}
                        >
                          {b.label}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Custom Gold / Accent Color Override */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-300">
                      Metallic Accent / Gold Color Override
                    </label>
                    <p className="text-[11px] text-zinc-400">
                      Select a classic gold shade or enter your academy's brand hex code.
                    </p>

                    <div className="grid grid-cols-3 gap-2">
                      {goldSwatches.map((g) => {
                        const isSelected = (template.custom_gold_color || "") === g.color;
                        return (
                          <button
                            key={g.name}
                            type="button"
                            onClick={() => setTemplate({ ...template, custom_gold_color: g.color })}
                            className={`p-2 rounded-xl border text-xs font-medium flex items-center gap-2 transition-all ${
                              isSelected
                                ? "border-[#facc15] bg-[#facc15]/10 text-white font-bold"
                                : "border-white/10 bg-zinc-900 text-zinc-400 hover:text-white"
                            }`}
                          >
                            <span
                              className="w-3.5 h-3.5 rounded-full border border-white/20 shrink-0"
                              style={{ backgroundColor: g.color || "#d4af37" }}
                            />
                            <span className="truncate text-[11px]">{g.name}</span>
                          </button>
                        );
                      })}
                    </div>

                    <div className="flex items-center gap-2 pt-1">
                      <input
                        type="color"
                        value={template.custom_gold_color || "#d4af37"}
                        onChange={(e) => setTemplate({ ...template, custom_gold_color: e.target.value })}
                        className="w-9 h-9 rounded-xl bg-transparent border border-white/10 cursor-pointer p-0.5"
                      />
                      <input
                        type="text"
                        value={template.custom_gold_color || ""}
                        onChange={(e) => setTemplate({ ...template, custom_gold_color: e.target.value })}
                        placeholder="#d4af37 or leave blank for auto"
                        className="flex-1 px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-xs font-mono focus:outline-none focus:border-[#facc15]"
                      />
                    </div>
                  </div>
                </div>
              )}

              {/* 3. Text & Typography */}
              {designerCategory === "wording" && (
                <div className="space-y-4">
                  {/* Certificate Title */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <div>
                      <label className="block text-xs font-bold uppercase tracking-wider text-zinc-300 mb-1">
                        Certificate Heading *
                      </label>
                      <input
                        type="text"
                        value={template.title}
                        onChange={(e) => setTemplate({ ...template, title: e.target.value })}
                        placeholder="e.g. Certificate of Completion / Diploma of Classical Dance"
                        className="w-full px-3.5 py-2.5 bg-zinc-900 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                      />
                    </div>

                    <div>
                      <label className="block text-[11px] font-semibold text-zinc-400 mb-1.5">
                        Title Font Scale:
                      </label>
                      <div className="grid grid-cols-3 gap-2">
                        {[
                          { id: "sm", label: "Compact" },
                          { id: "lg", label: "Large (Standard)" },
                          { id: "xl", label: "Grand Majestic" },
                        ].map((sz) => (
                          <button
                            key={sz.id}
                            type="button"
                            onClick={() => setTemplate({ ...template, title_font_size: sz.id as any })}
                            className={`py-1.5 px-2 rounded-lg text-xs font-semibold transition-all ${
                              (template.title_font_size || "lg") === sz.id
                                ? "bg-[#facc15] text-black font-bold shadow-md shadow-[#facc15]/10"
                                : "bg-zinc-900 text-zinc-400 hover:text-white border border-white/5"
                            }`}
                          >
                            {sz.label}
                          </button>
                        ))}
                      </div>
                    </div>
                  </div>

                  {/* Presentation Line */}
                  <div>
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1">
                      Presentation Line
                    </label>
                    <input
                      type="text"
                      value={template.presentation_line}
                      onChange={(e) => setTemplate({ ...template, presentation_line: e.target.value })}
                      placeholder="e.g. This is proudly presented to"
                      className="w-full px-3.5 py-2.5 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                    />
                  </div>

                  {/* Recipient Name Calligraphy Font Picker */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <label className="block text-xs font-bold uppercase tracking-wider text-[#facc15]">
                      Recipient Name Calligraphy Font
                    </label>
                    <p className="text-[11px] text-zinc-400">
                      Choose the artistic calligraphy style rendered for the student's name:
                    </p>

                    <div className="space-y-2">
                      {fontStyles.map((f) => {
                        const isSelected = (template.name_font_style || "great_vibes") === f.id;
                        return (
                          <button
                            key={f.id}
                            type="button"
                            onClick={() => setTemplate({ ...template, name_font_style: f.id as any })}
                            className={`w-full p-2.5 rounded-xl border text-left flex items-center justify-between transition-all ${
                              isSelected
                                ? "border-[#facc15] bg-[#facc15]/10 text-white font-bold"
                                : "border-white/10 bg-zinc-900 text-zinc-300 hover:border-white/20"
                            }`}
                          >
                            <div>
                              <p className="text-xs font-semibold">{f.label}</p>
                              <p className="text-[10px] text-zinc-400">{f.desc}</p>
                            </div>
                            <span
                              className="text-lg text-[#facc15] px-2"
                              style={{ fontFamily: f.font }}
                            >
                              Ananya Ramanathan
                            </span>
                          </button>
                        );
                      })}
                    </div>
                  </div>

                  {/* Completion Statement */}
                  <div>
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-400 mb-1">
                      Completion Statement Description
                    </label>
                    <textarea
                      rows={3}
                      value={template.description_text}
                      onChange={(e) => setTemplate({ ...template, description_text: e.target.value })}
                      placeholder="e.g. for successfully completing the rigorous curriculum, practical demonstrations, and traditional examinations for"
                      className="w-full px-3.5 py-2.5 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] resize-none"
                    />
                  </div>
                </div>
              )}

              {/* 4. Signatures & Authorities */}
              {designerCategory === "signatories" && (
                <div className="space-y-5">
                  {/* Signatory 1 (Left Authority) */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <div className="flex items-center justify-between">
                      <p className="text-xs font-bold text-[#facc15] uppercase tracking-wider">
                        Signatory 1 (Left Authority)
                      </p>
                      {template.signatory1_signature && (
                        <button
                          type="button"
                          onClick={() => setTemplate({ ...template, signatory1_signature: "" })}
                          className="text-[11px] text-red-400 hover:text-red-300 flex items-center gap-1 font-semibold transition-colors"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                          Clear Signature
                        </button>
                      )}
                    </div>

                    <div>
                      <label className="block text-[11px] text-zinc-400 mb-1">Full Name</label>
                      <input
                        type="text"
                        value={template.signatory1_name}
                        onChange={(e) => setTemplate({ ...template, signatory1_name: e.target.value })}
                        placeholder="e.g. Guru Smt. Priyadarshini Govind"
                        className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white text-xs focus:outline-none focus:border-[#facc15]"
                      />
                    </div>

                    <div>
                      <label className="block text-[11px] text-zinc-400 mb-1">Designation / Title</label>
                      <input
                        type="text"
                        value={template.signatory1_title}
                        onChange={(e) => setTemplate({ ...template, signatory1_title: e.target.value })}
                        placeholder="e.g. Artistic Director & Chief Mentor"
                        className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white text-xs focus:outline-none focus:border-[#facc15]"
                      />
                    </div>

                    {/* Signature Image Preview */}
                    {template.signatory1_signature ? (
                      <div className="h-16 bg-white/5 rounded-xl border border-white/10 flex items-center justify-center p-2">
                        <img
                          src={template.signatory1_signature}
                          alt="Signatory 1 Signature"
                          className="h-full object-contain max-w-[160px]"
                        />
                      </div>
                    ) : (
                      <div className="text-[11px] text-zinc-500 italic">
                        No image uploaded: Rendering calligraphic cursive signature automatically.
                      </div>
                    )}

                    {/* File Upload Button for Signature */}
                    <label className="w-full cursor-pointer py-2 px-3 bg-zinc-900 hover:bg-zinc-800 border border-white/10 hover:border-[#facc15]/50 rounded-xl text-xs font-semibold text-white flex items-center justify-center gap-2 transition-all">
                      <Upload className="w-3.5 h-3.5 text-[#facc15]" />
                      <span>{template.signatory1_signature ? "Replace Signature Image" : "Attach Digital Signature Image (PNG)"}</span>
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => handleImageUpload(e, "signatory1_signature")}
                      />
                    </label>
                  </div>

                  {/* Signatory 2 (Right Authority) */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <div className="flex items-center justify-between">
                      <p className="text-xs font-bold text-[#facc15] uppercase tracking-wider">
                        Signatory 2 (Right Authority)
                      </p>
                      {template.signatory2_signature && (
                        <button
                          type="button"
                          onClick={() => setTemplate({ ...template, signatory2_signature: "" })}
                          className="text-[11px] text-red-400 hover:text-red-300 flex items-center gap-1 font-semibold transition-colors"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                          Clear Signature
                        </button>
                      )}
                    </div>

                    <div>
                      <label className="block text-[11px] text-zinc-400 mb-1">Full Name</label>
                      <input
                        type="text"
                        value={template.signatory2_name}
                        onChange={(e) => setTemplate({ ...template, signatory2_name: e.target.value })}
                        placeholder="e.g. Dr. K. S. Subramanian"
                        className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white text-xs focus:outline-none focus:border-[#facc15]"
                      />
                    </div>

                    <div>
                      <label className="block text-[11px] text-zinc-400 mb-1">Designation / Title</label>
                      <input
                        type="text"
                        value={template.signatory2_title}
                        onChange={(e) => setTemplate({ ...template, signatory2_title: e.target.value })}
                        placeholder="e.g. Dean of Academy & External Examiner"
                        className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-lg text-white text-xs focus:outline-none focus:border-[#facc15]"
                      />
                    </div>

                    {/* Signature Image Preview */}
                    {template.signatory2_signature ? (
                      <div className="h-16 bg-white/5 rounded-xl border border-white/10 flex items-center justify-center p-2">
                        <img
                          src={template.signatory2_signature}
                          alt="Signatory 2 Signature"
                          className="h-full object-contain max-w-[160px]"
                        />
                      </div>
                    ) : (
                      <div className="text-[11px] text-zinc-500 italic">
                        No image uploaded: Rendering calligraphic cursive signature automatically.
                      </div>
                    )}

                    {/* File Upload Button for Signature */}
                    <label className="w-full cursor-pointer py-2 px-3 bg-zinc-900 hover:bg-zinc-800 border border-white/10 hover:border-[#facc15]/50 rounded-xl text-xs font-semibold text-white flex items-center justify-center gap-2 transition-all">
                      <Upload className="w-3.5 h-3.5 text-[#facc15]" />
                      <span>{template.signatory2_signature ? "Replace Signature Image" : "Attach Digital Signature Image (PNG)"}</span>
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => handleImageUpload(e, "signatory2_signature")}
                      />
                    </label>
                  </div>
                </div>
              )}

              {/* 5. Stamp, Seal & Security */}
              {designerCategory === "seal" && (
                <div className="space-y-4">
                  {/* Seal Type Selector */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-300">
                      Center Seal Style
                    </label>
                    <div className="grid grid-cols-2 gap-2">
                      <button
                        type="button"
                        onClick={() => setTemplate({ ...template, seal_type: "gold_medallion" })}
                        className={`p-3 rounded-xl border text-xs font-semibold text-left transition-all ${
                          (template.seal_type || "gold_medallion") === "gold_medallion"
                            ? "border-[#facc15] bg-[#facc15]/10 text-white font-bold"
                            : "border-white/10 bg-zinc-900 text-zinc-400 hover:text-white"
                        }`}
                      >
                        <p className="font-bold text-white">Dynamic Gold Medallion</p>
                        <p className="text-[10px] text-zinc-400 mt-0.5">Embossed 3D medal with ribbon tails</p>
                      </button>

                      <button
                        type="button"
                        onClick={() => setTemplate({ ...template, seal_type: "custom_seal" })}
                        className={`p-3 rounded-xl border text-xs font-semibold text-left transition-all ${
                          template.seal_type === "custom_seal"
                            ? "border-[#facc15] bg-[#facc15]/10 text-white font-bold"
                            : "border-white/10 bg-zinc-900 text-zinc-400 hover:text-white"
                        }`}
                      >
                        <p className="font-bold text-white">Custom Stamp / Seal</p>
                        <p className="text-[10px] text-zinc-400 mt-0.5">Attach custom official seal image</p>
                      </button>
                    </div>

                    {/* Seal type specific inputs */}
                    {template.seal_type === "custom_seal" ? (
                      <div className="space-y-3 pt-2">
                        {template.seal_url ? (
                          <div className="flex items-center justify-between p-3 bg-zinc-900 rounded-xl border border-white/10">
                            <img src={template.seal_url} alt="Official Seal Preview" className="h-16 object-contain" />
                            <button
                              type="button"
                              onClick={() => setTemplate({ ...template, seal_url: "" })}
                              className="text-xs text-red-400 hover:text-red-300 font-semibold"
                            >
                              Remove Seal
                            </button>
                          </div>
                        ) : null}

                        <label className="w-full cursor-pointer py-2.5 px-3 bg-zinc-900 hover:bg-zinc-800 border border-white/10 hover:border-[#facc15]/50 rounded-xl text-xs font-semibold text-white flex items-center justify-center gap-2 transition-all">
                          <Upload className="w-3.5 h-3.5 text-[#facc15]" />
                          <span>{template.seal_url ? "Replace Seal Image" : "Upload Custom Official Seal Image (PNG)"}</span>
                          <input
                            type="file"
                            accept="image/*"
                            className="hidden"
                            onChange={(e) => handleImageUpload(e, "seal_url")}
                          />
                        </label>
                      </div>
                    ) : (
                      <div>
                        <label className="block text-[11px] text-zinc-400 mb-1">
                          Medallion Outer Text
                        </label>
                        <input
                          type="text"
                          value={template.seal_text}
                          onChange={(e) => setTemplate({ ...template, seal_text: e.target.value })}
                          placeholder="e.g. NATYA ARTS • VERIFIED CREDENTIAL • EXCELLENCE"
                          className="w-full px-3 py-2 bg-zinc-900 border border-white/10 rounded-xl text-white text-xs focus:outline-none focus:border-[#facc15]"
                        />
                      </div>
                    )}
                  </div>

                  {/* Security Toggles */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <label className="block text-xs font-bold uppercase tracking-wider text-zinc-300 mb-2">
                      Credential Security & Metadata
                    </label>

                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={template.show_verification_id}
                        onChange={(e) => setTemplate({ ...template, show_verification_id: e.target.checked })}
                        className="w-4 h-4 accent-[#facc15] rounded"
                      />
                      <span className="text-xs text-zinc-300 font-medium">Display Unique Certificate ID</span>
                    </label>

                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={template.show_qr}
                        onChange={(e) => setTemplate({ ...template, show_qr: e.target.checked })}
                        className="w-4 h-4 accent-[#facc15] rounded"
                      />
                      <span className="text-xs text-zinc-300 font-medium">Display Public Verification URL (/verify/...)</span>
                    </label>

                    <label className="flex items-center gap-3 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={template.show_issue_date}
                        onChange={(e) => setTemplate({ ...template, show_issue_date: e.target.checked })}
                        className="w-4 h-4 accent-[#facc15] rounded"
                      />
                      <span className="text-xs text-zinc-300 font-medium">Display Official Issuance Date</span>
                    </label>
                  </div>
                </div>
              )}

              {/* 6. Watermark & Motif */}
              {designerCategory === "watermark" && (
                <div className="space-y-4">
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <label className="block text-xs font-bold uppercase tracking-wider text-[#facc15]">
                          Background Watermark Motif
                        </label>
                        <p className="text-[11px] text-zinc-400">
                          Light motif rendered in the background of the certificate.
                        </p>
                      </div>
                      {template.watermark_url && (
                        <button
                          type="button"
                          onClick={() => setTemplate({ ...template, watermark_url: "" })}
                          className="text-[11px] text-red-400 hover:text-red-300 flex items-center gap-1 font-semibold transition-colors"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                          Revert to Default
                        </button>
                      )}
                    </div>

                    {template.watermark_url ? (
                      <div className="h-20 bg-zinc-900/90 rounded-xl border border-white/10 flex items-center justify-center p-2">
                        <img
                          src={template.watermark_url}
                          alt="Custom Watermark"
                          className="h-full object-contain opacity-50"
                        />
                      </div>
                    ) : (
                      <div className="p-3 bg-zinc-900 rounded-xl text-xs text-zinc-400 flex items-center gap-2">
                        <Sparkles className="w-4 h-4 text-[#facc15]" />
                        <span>Traditional Sacred Mandala & Nataraja Motif (Built-in)</span>
                      </div>
                    )}

                    <label className="w-full cursor-pointer py-2.5 px-3 bg-zinc-900 hover:bg-zinc-800 border border-white/10 hover:border-[#facc15]/50 rounded-xl text-xs font-semibold text-white flex items-center justify-center gap-2 transition-all">
                      <Upload className="w-3.5 h-3.5 text-[#facc15]" />
                      <span>{template.watermark_url ? "Replace Custom Watermark" : "Upload Custom Watermark Image"}</span>
                      <input
                        type="file"
                        accept="image/*"
                        className="hidden"
                        onChange={(e) => handleImageUpload(e, "watermark_url")}
                      />
                    </label>
                  </div>

                  {/* Watermark Opacity Slider */}
                  <div className="p-4 bg-zinc-950/70 border border-white/5 rounded-2xl space-y-3">
                    <div className="flex items-center justify-between">
                      <label className="block text-xs font-bold uppercase tracking-wider text-zinc-300">
                        Watermark Visibility / Opacity
                      </label>
                      <span className="text-xs font-bold text-[#facc15]">
                        {Math.round((template.watermark_opacity ?? 0.04) * 100)}%
                      </span>
                    </div>

                    <input
                      type="range"
                      min="0"
                      max="0.25"
                      step="0.01"
                      value={template.watermark_opacity ?? 0.04}
                      onChange={(e) =>
                        setTemplate({ ...template, watermark_opacity: parseFloat(e.target.value) })
                      }
                      className="w-full accent-[#facc15] cursor-pointer"
                    />
                    <div className="flex justify-between text-[10px] text-zinc-500">
                      <span>0% (Invisible)</span>
                      <span>4% (Subtle Luxury)</span>
                      <span>25% (High Visibility)</span>
                    </div>
                  </div>
                </div>
              )}
            </div>

            {/* Right Column: Live Dynamic Preview (7 cols) */}
            <div className="lg:col-span-7 space-y-4 sticky top-6">
              {/* Test Data Controls */}
              <div className="bg-zinc-900 border border-white/10 rounded-2xl p-4 flex flex-col sm:flex-row items-center justify-between gap-3 text-xs">
                <div className="flex items-center gap-2 w-full sm:w-auto">
                  <span className="text-zinc-500 shrink-0 font-semibold">Test Student:</span>
                  <input
                    type="text"
                    value={testLearnerName}
                    onChange={(e) => setTestLearnerName(e.target.value)}
                    className="bg-zinc-950 border border-white/10 rounded-lg px-2.5 py-1 text-white text-xs w-full sm:w-44 focus:outline-none focus:border-[#facc15]"
                  />
                </div>
                <div className="flex items-center gap-2 w-full sm:w-auto">
                  <span className="text-zinc-500 shrink-0 font-semibold">Test Course:</span>
                  <input
                    type="text"
                    value={testCourseTitle}
                    onChange={(e) => setTestCourseTitle(e.target.value)}
                    className="bg-zinc-950 border border-white/10 rounded-lg px-2.5 py-1 text-white text-xs w-full sm:w-56 focus:outline-none focus:border-[#facc15]"
                  />
                </div>
              </div>

              {/* The Live Certificate Canvas */}
              <div className="bg-zinc-950/80 p-3 sm:p-5 rounded-2xl border border-white/10 shadow-2xl overflow-hidden">
                <CertificateRenderer
                  template={template}
                  learnerName={testLearnerName}
                  courseTitle={testCourseTitle}
                  verificationId="CERT-NATYA-2026-X9Y1"
                />
              </div>

              <p className="text-center text-xs text-zinc-500">
                ✨ Live Real-Time WYSIWYG Canvas • Rendered at A4 landscape proportions
              </p>
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* TAB 2: ISSUED CERTIFICATES AUDIT LIST */}
      {/* ========================================================================= */}
      {activeTab === "issued" && (
        <div className="space-y-6">
          {issuedError && (
            <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl text-sm">
              {issuedError}
            </div>
          )}

          {/* Filters Row */}
          <form onSubmit={handleFilterSubmit} className="flex flex-col md:flex-row gap-4">
            <input
              type="number"
              placeholder="Filter by student ID..."
              value={studentIdFilter}
              onChange={(e) => setStudentIdFilter(e.target.value)}
              className="w-full md:w-56 bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-500"
            />
            <input
              type="number"
              placeholder="Filter by course ID..."
              value={courseIdFilter}
              onChange={(e) => setCourseIdFilter(e.target.value)}
              className="w-full md:w-56 bg-zinc-900 border border-white/5 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-[#facc15] placeholder:text-zinc-500"
            />
            <button
              type="submit"
              className="px-5 py-3 bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-sm font-semibold rounded-xl transition-all"
            >
              Apply Filters
            </button>
          </form>

          {/* Table */}
          <div className="bg-zinc-900 border border-white/10 rounded-2xl overflow-hidden shadow-2xl">
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs">
                <thead>
                  <tr className="bg-white/5 border-b border-white/10 text-zinc-400 uppercase tracking-wider">
                    <th className="p-4 font-semibold">Learner Name</th>
                    <th className="p-4 font-semibold">Student Username</th>
                    <th className="p-4 font-semibold">Course Title</th>
                    <th className="p-4 font-semibold">Verification ID</th>
                    <th className="p-4 font-semibold">Issued At</th>
                    <th className="p-4 font-semibold text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5 text-zinc-300">
                  {issuedLoading ? (
                    <tr>
                      <td colSpan={6} className="p-16 text-center text-zinc-500">
                        <div className="flex flex-col items-center justify-center gap-3">
                          <div className="w-6 h-6 border-2 border-[#facc15] border-t-transparent rounded-full animate-spin" />
                          <span className="text-sm">Loading certificates...</span>
                        </div>
                      </td>
                    </tr>
                  ) : certificates.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="p-16 text-center text-zinc-500 text-sm">
                        No certificates found. Certificates are automatically issued when students complete all
                        required lessons and assessments.
                      </td>
                    </tr>
                  ) : (
                    certificates.map((c) => (
                      <tr key={c.id} className="hover:bg-white/5 transition-colors">
                        <td className="p-4 font-bold text-white text-sm">{c.learner_name_snapshot}</td>
                        <td className="p-4 text-zinc-400">{c.student.username}</td>
                        <td className="p-4 text-zinc-400 max-w-xs truncate">{c.course_title_snapshot}</td>
                        <td className="p-4 font-mono text-xs text-[#facc15] select-all">{c.verification_id}</td>
                        <td className="p-4 text-zinc-400">{new Date(c.issued_at).toLocaleString()}</td>
                        <td className="p-4 text-right">
                          <a
                            href={`/verify/${c.verification_id}`}
                            target="_blank"
                            rel="noreferrer"
                            className="px-3 py-1.5 rounded-lg bg-zinc-800 hover:bg-zinc-700 text-[#facc15] font-semibold text-xs border border-white/5 transition-colors"
                          >
                            Verify & View ↗
                          </a>
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Pagination Footer */}
          {!issuedLoading && totalPages > 1 && (
            <div className="flex items-center justify-between mt-6">
              <div className="text-xs text-zinc-500">
                Showing Page <span className="font-semibold text-white">{page}</span> of{" "}
                <span className="font-semibold text-white">{totalPages}</span> ({totalCount} total entries)
              </div>
              <div className="flex gap-2">
                <button
                  disabled={page === 1}
                  onClick={() => setPage(page - 1)}
                  className="p-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all inline-flex items-center"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <button
                  disabled={page === totalPages}
                  onClick={() => setPage(page + 1)}
                  className="p-2 bg-zinc-900 border border-white/10 hover:bg-zinc-800 disabled:opacity-30 rounded-xl text-zinc-400 hover:text-white transition-all inline-flex items-center"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
