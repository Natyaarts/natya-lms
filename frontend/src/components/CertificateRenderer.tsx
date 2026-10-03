"use client";

import React from "react";

export interface CertificateTemplateData {
  title?: string;
  institute_name?: string;
  institute_tagline?: string;
  presentation_line?: string;
  description_text?: string;
  theme?: "temple_gold" | "royal_maroon" | "silk_ivory" | "obsidian_gold" | string;
  border_style?: "ornate_gold" | "double_temple" | "royal_filigree" | "modern_clean" | string;
  // Logos
  logo_url?: string;
  logo_size?: "sm" | "md" | "lg" | "xl";
  secondary_logo_url?: string;
  secondary_logo_size?: "sm" | "md" | "lg";
  // Watermark
  watermark_url?: string;
  watermark_opacity?: number;
  // Signatures
  signatory1_name?: string;
  signatory1_title?: string;
  signatory1_signature?: string;
  signatory1_font?: string;
  signatory2_name?: string;
  signatory2_title?: string;
  signatory2_signature?: string;
  signatory2_font?: string;
  // Seal
  seal_type?: "gold_medallion" | "custom_seal";
  seal_url?: string;
  seal_text?: string;
  // Typography & Styling
  name_font_style?: "great_vibes" | "pinyon_script" | "alex_brush" | "cinzel" | "playfair";
  title_font_size?: "sm" | "md" | "lg" | "xl";
  custom_gold_color?: string;
  // Toggles
  show_qr?: boolean;
  show_verification_id?: boolean;
  show_issue_date?: boolean;
}

interface CertificateRendererProps {
  template: CertificateTemplateData;
  learnerName: string;
  courseTitle: string;
  issuedAt?: string;
  verificationId?: string;
  isPrintMode?: boolean;
}

export const DEFAULT_TEMPLATE: CertificateTemplateData = {
  title: "Certificate of Completion",
  institute_name: "Natya Arts Academy",
  institute_tagline: "Center for Excellence in Classical Indian Arts & Bharatanatyam",
  presentation_line: "This is proudly presented to",
  description_text: "for successfully completing the rigorous curriculum, practical demonstrations, and traditional examinations for",
  theme: "temple_gold",
  border_style: "ornate_gold",
  logo_url: "",
  logo_size: "md",
  secondary_logo_url: "",
  secondary_logo_size: "sm",
  watermark_url: "",
  watermark_opacity: 0.04,
  signatory1_name: "Guru Smt. Priyadarshini Govind",
  signatory1_title: "Artistic Director & Chief Mentor",
  signatory1_signature: "",
  signatory1_font: "Great Vibes",
  signatory2_name: "Dr. K. S. Subramanian",
  signatory2_title: "Dean of Academy & External Examiner",
  signatory2_signature: "",
  signatory2_font: "Alex Brush",
  seal_type: "gold_medallion",
  seal_url: "",
  seal_text: "NATYA ARTS • VERIFIED CREDENTIAL • EXCELLENCE",
  name_font_style: "great_vibes",
  title_font_size: "lg",
  show_qr: true,
  show_verification_id: true,
  show_issue_date: true,
};

export default function CertificateRenderer({
  template,
  learnerName,
  courseTitle,
  issuedAt = new Date().toISOString(),
  verificationId = "CERT-NATYA-2026-X9Y1",
  isPrintMode = false,
}: CertificateRendererProps) {
  const merged = { ...DEFAULT_TEMPLATE, ...template };

  const formatDate = (isoString: string) => {
    try {
      return new Date(isoString).toLocaleDateString("en-US", {
        year: "numeric",
        month: "long",
        day: "numeric",
      });
    } catch {
      return isoString;
    }
  };

  // Theme Colors
  const isDark = merged.theme === "obsidian_gold";
  const isMaroon = merged.theme === "royal_maroon";
  const isIvory = merged.theme === "silk_ivory";

  const goldPrimary = merged.custom_gold_color || (isDark ? "#facc15" : isMaroon ? "#c59b27" : "#b8860b");
  const goldAccent = isDark ? "#fef08a" : "#d4af37";

  const bgGradient = isDark
    ? "bg-gradient-to-br from-zinc-950 via-zinc-900 to-black text-white"
    : isMaroon
    ? "bg-gradient-to-br from-[#fffdfa] via-[#fbf5ed] to-[#fffdfa] text-zinc-900"
    : isIvory
    ? "bg-gradient-to-br from-[#fefefc] via-[#f9f6ef] to-[#fcf9f2] text-zinc-900"
    : "bg-gradient-to-br from-[#fffdf9] via-[#fbf6ea] to-[#fffdfa] text-zinc-900"; // temple_gold

  // Logo Heights
  const logoHeights: Record<string, string> = {
    sm: "h-8 sm:h-10",
    md: "h-11 sm:h-14",
    lg: "h-14 sm:h-18",
    xl: "h-16 sm:h-22",
  };
  const mainLogoHeight = logoHeights[merged.logo_size || "md"] || logoHeights.md;
  const secLogoHeight = logoHeights[merged.secondary_logo_size || "sm"] || logoHeights.sm;

  // Title Size
  const titleSizeClass =
    merged.title_font_size === "xl"
      ? "text-2xl sm:text-4xl lg:text-5xl"
      : merged.title_font_size === "sm"
      ? "text-lg sm:text-2xl lg:text-3xl"
      : "text-xl sm:text-3xl lg:text-4xl";

  // Name font style
  const getNameFontFamily = () => {
    switch (merged.name_font_style) {
      case "great_vibes":
        return "'Great Vibes', cursive";
      case "pinyon_script":
        return "'Pinyon Script', cursive";
      case "alex_brush":
        return "'Alex Brush', cursive";
      case "cinzel":
        return "'Cinzel', serif";
      case "playfair":
        return "'Playfair Display', serif";
      default:
        return "'Great Vibes', cursive";
    }
  };

  return (
    <div
      className={`certificate-container relative w-full aspect-[1.414/1] overflow-hidden rounded-2xl select-none ${bgGradient} ${
        isPrintMode ? "shadow-none border-none m-0" : "shadow-2xl border border-white/10"
      }`}
      style={{
        boxShadow: isDark
          ? "0 25px 60px -15px rgba(0,0,0,0.9), 0 0 40px rgba(250,204,21,0.08)"
          : "0 25px 60px -15px rgba(184,134,11,0.22), 0 0 35px rgba(212,175,55,0.12)",
      }}
    >
      {/* Import Classical Fonts for Web & Print */}
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Alex+Brush&family=Cinzel:wght@600;700;800;900&family=Great+Vibes&family=Pinyon+Script&family=Playfair+Display:ital,wght@0,600;0,700;0,900;1,600&display=swap');
      `}</style>

      {/* Background Watermark */}
      <div
        className="absolute inset-0 flex items-center justify-center pointer-events-none"
        style={{ opacity: merged.watermark_opacity ?? 0.04 }}
      >
        {merged.watermark_url ? (
          <img src={merged.watermark_url} alt="Watermark" className="w-1/2 h-1/2 object-contain" />
        ) : (
          <svg width="460" height="460" viewBox="0 0 200 200" fill="currentColor">
            <circle cx="100" cy="100" r="90" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="4 4" />
            <circle cx="100" cy="100" r="75" fill="none" stroke="currentColor" strokeWidth="1" />
            <path d="M100 15 C105 50 140 85 175 90 C140 95 105 130 100 165 C95 130 60 95 25 90 C60 85 95 50 100 15 Z" />
            <circle cx="100" cy="100" r="28" />
          </svg>
        )}
      </div>

      {/* Decorative Outer Border */}
      <div
        className="absolute inset-3 sm:inset-5 rounded-xl border-2 pointer-events-none"
        style={{
          borderColor: isMaroon ? "#800020" : isDark ? "#ca8a04" : "#d4af37",
        }}
      />

      {/* Decorative Inner Inset Border */}
      <div
        className="absolute inset-5 sm:inset-7 rounded-lg border pointer-events-none"
        style={{
          borderColor: isMaroon ? "#d4af37" : isDark ? "#eab308" : "#b8860b",
          borderStyle: merged.border_style === "double_temple" ? "double" : "solid",
          borderWidth: merged.border_style === "double_temple" ? "3px" : "1px",
        }}
      />

      {/* Ornate Corner Filigree Ornaments */}
      {merged.border_style !== "modern_clean" && (
        <>
          {/* Top-Left */}
          <div className="absolute top-4 sm:top-6 left-4 sm:left-6 w-9 h-9 sm:w-14 sm:h-14 pointer-events-none">
            <svg viewBox="0 0 100 100" className="w-full h-full" style={{ fill: goldPrimary }}>
              <path d="M0,0 L45,0 C32,12 22,22 22,45 L22,100 L0,100 Z" opacity="0.85" />
              <circle cx="12" cy="12" r="5" />
              <path d="M26,26 Q48,26 48,48 Q48,70 26,70 Z" fill="none" stroke={goldPrimary} strokeWidth="2.5" />
            </svg>
          </div>
          {/* Top-Right */}
          <div className="absolute top-4 sm:top-6 right-4 sm:right-6 w-9 h-9 sm:w-14 sm:h-14 pointer-events-none rotate-90">
            <svg viewBox="0 0 100 100" className="w-full h-full" style={{ fill: goldPrimary }}>
              <path d="M0,0 L45,0 C32,12 22,22 22,45 L22,100 L0,100 Z" opacity="0.85" />
              <circle cx="12" cy="12" r="5" />
              <path d="M26,26 Q48,26 48,48 Q48,70 26,70 Z" fill="none" stroke={goldPrimary} strokeWidth="2.5" />
            </svg>
          </div>
          {/* Bottom-Left */}
          <div className="absolute bottom-4 sm:bottom-6 left-4 sm:left-6 w-9 h-9 sm:w-14 sm:h-14 pointer-events-none -rotate-90">
            <svg viewBox="0 0 100 100" className="w-full h-full" style={{ fill: goldPrimary }}>
              <path d="M0,0 L45,0 C32,12 22,22 22,45 L22,100 L0,100 Z" opacity="0.85" />
              <circle cx="12" cy="12" r="5" />
              <path d="M26,26 Q48,26 48,48 Q48,70 26,70 Z" fill="none" stroke={goldPrimary} strokeWidth="2.5" />
            </svg>
          </div>
          {/* Bottom-Right */}
          <div className="absolute bottom-4 sm:bottom-6 right-4 sm:right-6 w-9 h-9 sm:w-14 sm:h-14 pointer-events-none rotate-180">
            <svg viewBox="0 0 100 100" className="w-full h-full" style={{ fill: goldPrimary }}>
              <path d="M0,0 L45,0 C32,12 22,22 22,45 L22,100 L0,100 Z" opacity="0.85" />
              <circle cx="12" cy="12" r="5" />
              <path d="M26,26 Q48,26 48,48 Q48,70 26,70 Z" fill="none" stroke={goldPrimary} strokeWidth="2.5" />
            </svg>
          </div>
        </>
      )}

      {/* Main Certificate Content */}
      <div className="relative h-full flex flex-col justify-between px-8 sm:px-14 py-7 sm:py-9 text-center z-10">
        {/* Header Block: Logo & Academy Title */}
        <div className="space-y-1">
          {/* Logos Row (Main Logo + Optional Secondary Logo) */}
          <div className="flex items-center justify-center gap-4 mb-1">
            {merged.logo_url ? (
              <img
                src={merged.logo_url}
                alt="Academy Logo"
                className={`${mainLogoHeight} object-contain max-w-[180px] drop-shadow-sm`}
              />
            ) : (
              <div
                className="w-10 h-10 sm:w-12 sm:h-12 rounded-full flex items-center justify-center border shadow-inner"
                style={{
                  borderColor: goldPrimary,
                  background: isDark ? "rgba(250,204,21,0.12)" : "rgba(212,175,55,0.15)",
                }}
              >
                <span className="text-base sm:text-xl" role="img" aria-label="emblem">
                  🪔
                </span>
              </div>
            )}

            {merged.secondary_logo_url && (
              <img
                src={merged.secondary_logo_url}
                alt="Partner / Accreditation Logo"
                className={`${secLogoHeight} object-contain max-w-[140px] opacity-90`}
              />
            )}
          </div>

          {/* Institute / Academy Name */}
          <h2
            className="text-base sm:text-2xl font-serif font-extrabold tracking-[0.22em] uppercase"
            style={{
              fontFamily: "'Cinzel', serif",
              color: isMaroon ? "#800020" : isDark ? "#fef08a" : "#78350f",
            }}
          >
            {merged.institute_name}
          </h2>

          {/* Tagline / Subtitle */}
          {merged.institute_tagline && (
            <p className="text-[9px] sm:text-xs tracking-[0.2em] uppercase text-zinc-500 font-medium">
              {merged.institute_tagline}
            </p>
          )}

          {/* Gold Decorative Divider with Center Star */}
          <div className="flex items-center justify-center gap-2 pt-1 max-w-xs mx-auto">
            <div className="h-[1px] flex-1 bg-gradient-to-r from-transparent via-[#d4af37] to-transparent opacity-70" />
            <span className="text-[10px] text-[#d4af37]">✦ ✦ ✦</span>
            <div className="h-[1px] flex-1 bg-gradient-to-r from-transparent via-[#d4af37] to-transparent opacity-70" />
          </div>
        </div>

        {/* Certificate Title */}
        <div className="my-1">
          <h1
            className={`${titleSizeClass} font-serif font-black tracking-widest uppercase drop-shadow-sm`}
            style={{
              fontFamily: "'Cinzel', serif",
              color: isDark ? "#facc15" : isMaroon ? "#800020" : "#854d0e",
            }}
          >
            {merged.title}
          </h1>
          <p className="text-[10px] sm:text-xs italic tracking-wider text-zinc-500 mt-0.5 font-serif">
            {merged.presentation_line}
          </p>
        </div>

        {/* Recipient / Learner Name */}
        <div className="my-1">
          <div
            className="text-3xl sm:text-5xl lg:text-6xl py-1 px-8 border-b-2 inline-block min-w-[260px] sm:min-w-[420px]"
            style={{
              fontFamily: getNameFontFamily(),
              borderColor: `${goldPrimary}66`,
              color: isDark ? "#ffffff" : isMaroon ? "#650015" : "#1a1a1a",
              textShadow: isDark
                ? "0 3px 12px rgba(250,204,21,0.25)"
                : "0 1px 2px rgba(0,0,0,0.05)",
            }}
          >
            {learnerName || "Learner Name"}
          </div>
        </div>

        {/* Completion Statement & Course Title */}
        <div className="max-w-2xl mx-auto px-4 my-1">
          <p className="text-[10px] sm:text-xs leading-relaxed text-zinc-600 dark:text-zinc-400 font-serif">
            {merged.description_text}
          </p>
          <p
            className="text-sm sm:text-xl font-bold font-serif tracking-wide mt-1"
            style={{
              fontFamily: "'Playfair Display', serif",
              color: isDark ? "#facc15" : isMaroon ? "#800020" : "#713f12",
            }}
          >
            {courseTitle || "Classical Arts Mastery"}
          </p>
        </div>

        {/* Bottom Section: Dual Signatories & Official Seal */}
        <div className="grid grid-cols-3 items-end pt-3 sm:pt-4 border-t border-zinc-200/50 dark:border-white/10 mt-1">
          {/* Signatory 1 (Left Authority) */}
          <div className="text-left space-y-0.5">
            <div className="h-8 sm:h-12 flex items-end">
              {merged.signatory1_signature ? (
                <img
                  src={merged.signatory1_signature}
                  alt={merged.signatory1_name}
                  className="h-full object-contain max-w-[140px]"
                />
              ) : (
                <span
                  className="text-xl sm:text-3xl font-normal tracking-wide"
                  style={{
                    fontFamily: "'Great Vibes', cursive",
                    color: isDark ? "#facc15" : "#78350f",
                  }}
                >
                  {merged.signatory1_name?.split(" ")[0] || "Priyadarshini"}
                </span>
              )}
            </div>
            <div className="w-28 sm:w-44 h-[1px] bg-zinc-400/60 dark:bg-zinc-600" />
            <p className="text-[10px] sm:text-xs font-bold text-zinc-800 dark:text-zinc-200 truncate">
              {merged.signatory1_name}
            </p>
            <p className="text-[8px] sm:text-[10px] text-zinc-500 uppercase tracking-wider truncate">
              {merged.signatory1_title}
            </p>
          </div>

          {/* Center: Official Seal & Date */}
          <div className="flex flex-col items-center justify-center">
            {merged.seal_type === "custom_seal" && merged.seal_url ? (
              <img src={merged.seal_url} alt="Official Seal" className="w-14 h-14 sm:w-20 sm:h-20 object-contain drop-shadow" />
            ) : (
              <div className="relative flex flex-col items-center">
                {/* Ribbon Tails */}
                <div className="absolute -bottom-2 flex gap-1 z-0">
                  <div
                    className="w-2.5 sm:w-3.5 h-6 sm:h-9 -rotate-12 rounded-b shadow"
                    style={{ background: isMaroon ? "#800020" : "#b8860b" }}
                  />
                  <div
                    className="w-2.5 sm:w-3.5 h-6 sm:h-9 rotate-12 rounded-b shadow"
                    style={{ background: isMaroon ? "#800020" : "#d4af37" }}
                  />
                </div>

                {/* Sunburst Medallion */}
                <div
                  className="relative z-10 w-14 h-14 sm:w-20 sm:h-20 rounded-full flex flex-col items-center justify-center shadow-xl border-2"
                  style={{
                    background: isDark
                      ? "radial-gradient(circle, #fde047 0%, #ca8a04 75%, #854d0e 100%)"
                      : "radial-gradient(circle, #fffbeb 0%, #fef08a 25%, #eab308 70%, #92400e 100%)",
                    borderColor: isDark ? "#fef08a" : "#ca8a04",
                  }}
                >
                  <div className="w-11 h-11 sm:w-16 sm:h-16 rounded-full border border-dashed border-amber-950/40 flex items-center justify-center text-center p-1">
                    <div className="text-[7px] sm:text-[9px] font-black text-amber-950 uppercase tracking-tighter leading-[1]">
                      OFFICIAL
                      <br />
                      ★ ★ ★
                      <br />
                      SEAL
                    </div>
                  </div>
                </div>
              </div>
            )}

            {merged.show_issue_date && (
              <p className="text-[8px] sm:text-[10px] text-zinc-500 font-serif mt-3">
                Issued: {formatDate(issuedAt)}
              </p>
            )}
          </div>

          {/* Signatory 2 (Right Authority) */}
          <div className="text-right space-y-0.5 flex flex-col items-end">
            <div className="h-8 sm:h-12 flex items-end">
              {merged.signatory2_signature ? (
                <img
                  src={merged.signatory2_signature}
                  alt={merged.signatory2_name}
                  className="h-full object-contain max-w-[140px]"
                />
              ) : (
                <span
                  className="text-xl sm:text-3xl font-normal tracking-wide"
                  style={{
                    fontFamily: "'Alex Brush', cursive",
                    color: isDark ? "#facc15" : "#78350f",
                  }}
                >
                  {merged.signatory2_name?.split(" ")[0] || "Subramanian"}
                </span>
              )}
            </div>
            <div className="w-28 sm:w-44 h-[1px] bg-zinc-400/60 dark:bg-zinc-600" />
            <p className="text-[10px] sm:text-xs font-bold text-zinc-800 dark:text-zinc-200 truncate">
              {merged.signatory2_name}
            </p>
            <p className="text-[8px] sm:text-[10px] text-zinc-500 uppercase tracking-wider truncate">
              {merged.signatory2_title}
            </p>
          </div>
        </div>

        {/* Footer: Security & Verification */}
        <div className="flex items-center justify-between text-[8px] sm:text-[10px] text-zinc-500 pt-2 border-t border-zinc-200/40 dark:border-white/5">
          {merged.show_verification_id ? (
            <span className="font-mono">
              Certificate ID: <strong className="text-zinc-700 dark:text-zinc-300 font-bold">{verificationId}</strong>
            </span>
          ) : (
            <span />
          )}

          {merged.show_qr && (
            <span className="tracking-wider">
              Verify Authenticity at:{" "}
              <span className="font-semibold" style={{ color: goldPrimary }}>
                academy.natyaarts.com/verify/{verificationId}
              </span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
