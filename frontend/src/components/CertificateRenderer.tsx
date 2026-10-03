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
  signatory1_name?: string;
  signatory1_title?: string;
  signatory1_signature?: string;
  signatory2_name?: string;
  signatory2_title?: string;
  signatory2_signature?: string;
  logo_url?: string;
  seal_text?: string;
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
  signatory1_name: "Guru Smt. Priyadarshini Govind",
  signatory1_title: "Artistic Director & Chief Mentor",
  signatory1_signature: "",
  signatory2_name: "Dr. K. S. Subramanian",
  signatory2_title: "Dean of Academy & External Examiner",
  signatory2_signature: "",
  logo_url: "",
  seal_text: "NATYA ARTS • VERIFIED CREDENTIAL • EXCELLENCE",
  show_qr: true,
  show_verification_id: true,
  show_issue_date: true,
};

export default function CertificateRenderer({
  template,
  learnerName,
  courseTitle,
  issuedAt = new Date().toISOString(),
  verificationId = "CERT-SAMPLE-2026-X1",
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

  // Theme-specific styles
  const isDark = merged.theme === "obsidian_gold";
  const isMaroon = merged.theme === "royal_maroon";
  const isIvory = merged.theme === "silk_ivory";

  const bgClass = isDark
    ? "bg-gradient-to-br from-zinc-950 via-zinc-900 to-black text-white"
    : isMaroon
    ? "bg-gradient-to-br from-[#fffdfa] via-[#fcf6f0] to-[#fffdfa] text-zinc-900"
    : isIvory
    ? "bg-gradient-to-br from-[#fdfbf7] via-[#f8f5ee] to-[#fcf9f2] text-zinc-900"
    : "bg-gradient-to-br from-[#fefefd] via-[#fbf7ed] to-[#fefcf8] text-zinc-900"; // temple_gold

  const goldPrimary = isDark ? "#facc15" : "#b8860b";
  const goldAccent = "#d4af37";

  return (
    <div
      className={`relative w-full aspect-[1.414/1] overflow-hidden rounded-2xl select-none ${bgClass} ${
        isPrintMode ? "shadow-none border-none" : "shadow-2xl border border-white/10"
      }`}
      style={{
        boxShadow: isDark
          ? "0 25px 60px -15px rgba(0,0,0,0.9), 0 0 40px rgba(250,204,21,0.08)"
          : "0 25px 60px -15px rgba(184,134,11,0.25), 0 0 30px rgba(212,175,55,0.15)",
      }}
    >
      {/* Background Watermark: Nataraja / Lotus Motif */}
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none opacity-[0.035]">
        <svg width="480" height="480" viewBox="0 0 200 200" fill="currentColor">
          <circle cx="100" cy="100" r="90" fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray="4 4" />
          <circle cx="100" cy="100" r="75" fill="none" stroke="currentColor" strokeWidth="1" />
          <path d="M100 15 C105 50 140 85 175 90 C140 95 105 130 100 165 C95 130 60 95 25 90 C60 85 95 50 100 15 Z" />
          <circle cx="100" cy="100" r="25" />
        </svg>
      </div>

      {/* Decorative Outer Border */}
      <div
        className="absolute inset-4 sm:inset-6 rounded-xl border-2 pointer-events-none"
        style={{
          borderColor: isMaroon ? "#800020" : isDark ? "#ca8a04" : "#d4af37",
        }}
      />

      {/* Decorative Inner Inset Double Border */}
      <div
        className="absolute inset-6 sm:inset-8 rounded-lg border pointer-events-none"
        style={{
          borderColor: isMaroon ? "#d4af37" : isDark ? "#eab308" : "#b8860b",
          borderStyle: merged.border_style === "double_temple" ? "double" : "solid",
          borderWidth: merged.border_style === "double_temple" ? "3px" : "1px",
        }}
      />

      {/* Corner Filigree Ornaments */}
      {merged.border_style !== "modern_clean" && (
        <>
          {/* Top-Left */}
          <div className="absolute top-5 sm:top-7 left-5 sm:left-7 w-8 h-8 sm:w-12 sm:h-12 pointer-events-none">
            <svg viewBox="0 0 100 100" className="w-full h-full" style={{ fill: goldPrimary }}>
              <path d="M0,0 L40,0 C30,10 20,20 20,40 L20,100 L0,100 Z" opacity="0.8" />
              <circle cx="12" cy="12" r="6" />
              <path d="M25,25 Q45,25 45,45 Q45,65 25,65 Z" fill="none" stroke={goldPrimary} strokeWidth="3" />
            </svg>
          </div>
          {/* Top-Right */}
          <div className="absolute top-5 sm:top-7 right-5 sm:right-7 w-8 h-8 sm:w-12 sm:h-12 pointer-events-none rotate-90">
            <svg viewBox="0 0 100 100" className="w-full h-full" style={{ fill: goldPrimary }}>
              <path d="M0,0 L40,0 C30,10 20,20 20,40 L20,100 L0,100 Z" opacity="0.8" />
              <circle cx="12" cy="12" r="6" />
              <path d="M25,25 Q45,25 45,45 Q45,65 25,65 Z" fill="none" stroke={goldPrimary} strokeWidth="3" />
            </svg>
          </div>
          {/* Bottom-Left */}
          <div className="absolute bottom-5 sm:bottom-7 left-5 sm:left-7 w-8 h-8 sm:w-12 sm:h-12 pointer-events-none -rotate-90">
            <svg viewBox="0 0 100 100" className="w-full h-full" style={{ fill: goldPrimary }}>
              <path d="M0,0 L40,0 C30,10 20,20 20,40 L20,100 L0,100 Z" opacity="0.8" />
              <circle cx="12" cy="12" r="6" />
              <path d="M25,25 Q45,25 45,45 Q45,65 25,65 Z" fill="none" stroke={goldPrimary} strokeWidth="3" />
            </svg>
          </div>
          {/* Bottom-Right */}
          <div className="absolute bottom-5 sm:bottom-7 right-5 sm:right-7 w-8 h-8 sm:w-12 sm:h-12 pointer-events-none rotate-180">
            <svg viewBox="0 0 100 100" className="w-full h-full" style={{ fill: goldPrimary }}>
              <path d="M0,0 L40,0 C30,10 20,20 20,40 L20,100 L0,100 Z" opacity="0.8" />
              <circle cx="12" cy="12" r="6" />
              <path d="M25,25 Q45,25 45,45 Q45,65 25,65 Z" fill="none" stroke={goldPrimary} strokeWidth="3" />
            </svg>
          </div>
        </>
      )}

      {/* Main Content Area */}
      <div className="relative h-full flex flex-col justify-between px-8 sm:px-14 py-8 sm:py-10 text-center">
        {/* Header Block: Institute Branding */}
        <div className="space-y-1">
          {/* Logo or Emblem Icon */}
          <div className="flex items-center justify-center gap-2 mb-1">
            {merged.logo_url ? (
              <img src={merged.logo_url} alt="Academy Logo" className="h-9 sm:h-12 object-contain" />
            ) : (
              <div
                className="w-8 h-8 sm:w-10 sm:h-10 rounded-full flex items-center justify-center border"
                style={{
                  borderColor: goldPrimary,
                  background: isDark ? "rgba(250,204,21,0.1)" : "rgba(212,175,55,0.12)",
                }}
              >
                <span className="text-sm sm:text-base" role="img" aria-label="emblem">
                  🪔
                </span>
              </div>
            )}
          </div>

          <h2
            className="text-base sm:text-2xl font-serif font-extrabold tracking-[0.2em] uppercase"
            style={{
              color: isMaroon ? "#800020" : isDark ? "#fef08a" : "#78350f",
            }}
          >
            {merged.institute_name}
          </h2>

          {merged.institute_tagline && (
            <p className="text-[9px] sm:text-xs tracking-[0.18em] uppercase text-zinc-500 font-medium">
              {merged.institute_tagline}
            </p>
          )}

          {/* Golden Horizontal Divider with Center Diamond */}
          <div className="flex items-center justify-center gap-2 pt-2 max-w-xs mx-auto">
            <div className="h-[1px] flex-1 bg-gradient-to-r from-transparent via-[#d4af37] to-transparent opacity-60" />
            <span className="text-[10px] text-[#d4af37]">✦</span>
            <div className="h-[1px] flex-1 bg-gradient-to-r from-transparent via-[#d4af37] to-transparent opacity-60" />
          </div>
        </div>

        {/* Certificate Title */}
        <div className="my-1 sm:my-2">
          <h1
            className="text-xl sm:text-3xl lg:text-4xl font-serif font-black tracking-widest uppercase drop-shadow-sm"
            style={{
              color: isDark ? "#facc15" : isMaroon ? "#800020" : "#854d0e",
            }}
          >
            {merged.title}
          </h1>
          <p className="text-[10px] sm:text-xs italic tracking-wider text-zinc-500 mt-1 font-serif">
            {merged.presentation_line}
          </p>
        </div>

        {/* Recipient / Learner Name */}
        <div className="my-1 sm:my-2">
          <div
            className="text-2xl sm:text-4xl lg:text-5xl font-serif font-extrabold tracking-wide py-1 border-b inline-block min-w-[240px] sm:min-w-[360px] px-6"
            style={{
              borderColor: `${goldPrimary}55`,
              color: isDark ? "#ffffff" : isMaroon ? "#650015" : "#1a1a1a",
              textShadow: isDark ? "0 2px 10px rgba(250,204,21,0.2)" : "none",
            }}
          >
            {learnerName || "Learner Name"}
          </div>
        </div>

        {/* Achievement / Description Text */}
        <div className="max-w-xl mx-auto px-4 my-1">
          <p className="text-[10px] sm:text-xs leading-relaxed text-zinc-600 dark:text-zinc-400 font-serif">
            {merged.description_text}
          </p>
          <p
            className="text-sm sm:text-lg font-bold font-serif tracking-wide mt-1"
            style={{
              color: isDark ? "#facc15" : isMaroon ? "#800020" : "#713f12",
            }}
          >
            {courseTitle || "Classical Arts Mastery"}
          </p>
        </div>

        {/* Bottom Section: Signatories & Gold Seal */}
        <div className="grid grid-cols-3 items-end pt-3 sm:pt-4 border-t border-zinc-200/50 dark:border-white/10 mt-2">
          {/* Signatory 1 */}
          <div className="text-left space-y-0.5">
            <div className="h-7 sm:h-9 flex items-end">
              {merged.signatory1_signature ? (
                <img src={merged.signatory1_signature} alt="Signatory 1" className="h-full object-contain" />
              ) : (
                <span
                  className="font-serif italic text-sm sm:text-base font-bold tracking-wider"
                  style={{ color: isDark ? "#facc15" : "#854d0e" }}
                >
                  {merged.signatory1_name?.split(" ")[0] || "Priyadarshini"}
                </span>
              )}
            </div>
            <div className="w-24 sm:w-36 h-[1px] bg-zinc-400/60 dark:bg-zinc-600" />
            <p className="text-[10px] sm:text-xs font-bold text-zinc-800 dark:text-zinc-200 truncate">
              {merged.signatory1_name}
            </p>
            <p className="text-[8px] sm:text-[10px] text-zinc-500 uppercase tracking-wider truncate">
              {merged.signatory1_title}
            </p>
          </div>

          {/* Center: Official Gold Foil Seal */}
          <div className="flex flex-col items-center justify-center">
            <div className="relative flex flex-col items-center">
              {/* Ribbon Tails */}
              <div className="absolute -bottom-2 flex gap-1 z-0">
                <div
                  className="w-2 sm:w-2.5 h-5 sm:h-7 -rotate-12 rounded-b"
                  style={{ background: isMaroon ? "#800020" : "#b8860b" }}
                />
                <div
                  className="w-2 sm:w-2.5 h-5 sm:h-7 rotate-12 rounded-b"
                  style={{ background: isMaroon ? "#800020" : "#d4af37" }}
                />
              </div>

              {/* Medallion Disc */}
              <div
                className="relative z-10 w-12 h-12 sm:w-16 sm:h-16 rounded-full flex flex-col items-center justify-center shadow-lg border-2"
                style={{
                  background: isDark
                    ? "radial-gradient(circle, #fde047 0%, #ca8a04 80%, #854d0e 100%)"
                    : "radial-gradient(circle, #fffbeb 0%, #fef08a 30%, #eab308 70%, #a16207 100%)",
                  borderColor: isDark ? "#fef08a" : "#ca8a04",
                }}
              >
                <div className="w-9 h-9 sm:w-12 sm:h-12 rounded-full border border-dashed border-amber-900/40 flex items-center justify-center text-center p-0.5">
                  <div className="text-[7px] sm:text-[8px] font-bold text-amber-950 uppercase tracking-tighter leading-[1]">
                    VERIFIED
                    <br />
                    ★ ★ ★
                  </div>
                </div>
              </div>
            </div>

            {merged.show_issue_date && (
              <p className="text-[8px] sm:text-[10px] text-zinc-500 font-serif mt-3">
                Issued: {formatDate(issuedAt)}
              </p>
            )}
          </div>

          {/* Signatory 2 */}
          <div className="text-right space-y-0.5 flex flex-col items-end">
            <div className="h-7 sm:h-9 flex items-end">
              {merged.signatory2_signature ? (
                <img src={merged.signatory2_signature} alt="Signatory 2" className="h-full object-contain" />
              ) : (
                <span
                  className="font-serif italic text-sm sm:text-base font-bold tracking-wider"
                  style={{ color: isDark ? "#facc15" : "#854d0e" }}
                >
                  {merged.signatory2_name?.split(" ")[0] || "Subramanian"}
                </span>
              )}
            </div>
            <div className="w-24 sm:w-36 h-[1px] bg-zinc-400/60 dark:bg-zinc-600" />
            <p className="text-[10px] sm:text-xs font-bold text-zinc-800 dark:text-zinc-200 truncate">
              {merged.signatory2_name}
            </p>
            <p className="text-[8px] sm:text-[10px] text-zinc-500 uppercase tracking-wider truncate">
              {merged.signatory2_title}
            </p>
          </div>
        </div>

        {/* Footer: Verification ID & QR / Link */}
        <div className="flex items-center justify-between text-[8px] sm:text-[10px] text-zinc-500 pt-2 border-t border-zinc-200/40 dark:border-white/5">
          {merged.show_verification_id ? (
            <span className="font-mono">
              Certificate ID: <strong className="text-zinc-700 dark:text-zinc-300">{verificationId}</strong>
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
