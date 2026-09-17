"use client";

/**
 * Phase 4.6 -- public, unauthenticated certificate verification page.
 * Fetches GET /api/courses/certificates/verify/<verificationId>/, which
 * itself is public/throttled and returns only the minimal safe fields
 * (see PublicCertificateVerificationSerializer) -- no login required,
 * no internal ids, no way to reach the certificate owner's account.
 */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface VerificationResult {
  valid: boolean;
  verification_id?: string;
  learner_name_snapshot?: string;
  course_title_snapshot?: string;
  issued_at?: string;
  error?: string;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}

export default function VerifyCertificatePage() {
  const { verificationId } = useParams();
  const [result, setResult] = useState<VerificationResult | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/courses/certificates/verify/${verificationId}/`);
        const data = await res.json();
        setResult(data);
      } catch {
        setResult({ valid: false, error: "Could not connect to the server. Please try again." });
      } finally {
        setLoading(false);
      }
    };
    if (verificationId) load();
  }, [verificationId]);

  return (
    <div className="min-h-screen bg-black text-white font-sans flex items-center justify-center px-6 py-16">
      <div className="w-full max-w-md">
        <p className="text-center text-xs uppercase tracking-[0.3em] text-zinc-500 mb-6">Natya LMS Certificate Verification</p>

        {loading ? (
          <div className="h-56 bg-zinc-900 animate-pulse rounded-3xl" />
        ) : result?.valid ? (
          <div className="bg-zinc-900 border border-green-500/30 rounded-3xl p-8 text-center">
            <div className="w-14 h-14 mx-auto mb-4 rounded-full bg-green-500/15 flex items-center justify-center">
              <svg className="w-7 h-7 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" /></svg>
            </div>
            <p className="text-green-400 font-semibold mb-6">This is a valid certificate</p>
            <div className="text-left space-y-3 text-sm border-t border-white/10 pt-6">
              <div>
                <p className="text-zinc-500 text-xs uppercase tracking-wide">Awarded to</p>
                <p className="font-medium">{result.learner_name_snapshot}</p>
              </div>
              <div>
                <p className="text-zinc-500 text-xs uppercase tracking-wide">Course</p>
                <p className="font-medium">{result.course_title_snapshot}</p>
              </div>
              <div>
                <p className="text-zinc-500 text-xs uppercase tracking-wide">Issued</p>
                <p className="font-medium">{result.issued_at ? formatDate(result.issued_at) : "—"}</p>
              </div>
              <div>
                <p className="text-zinc-500 text-xs uppercase tracking-wide">Certificate ID</p>
                <p className="font-mono text-xs">{result.verification_id}</p>
              </div>
            </div>
          </div>
        ) : (
          <div className="bg-zinc-900 border border-red-500/30 rounded-3xl p-8 text-center">
            <div className="w-14 h-14 mx-auto mb-4 rounded-full bg-red-500/15 flex items-center justify-center">
              <svg className="w-7 h-7 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" /></svg>
            </div>
            <p className="text-red-400 font-semibold mb-2">Invalid certificate</p>
            <p className="text-zinc-500 text-sm">{result?.error || "No certificate was found for this verification ID."}</p>
          </div>
        )}

        <div className="text-center mt-6">
          <Link href="/" className="text-sm text-zinc-500 hover:text-[#facc15] transition-colors">
            Natya LMS
          </Link>
        </div>
      </div>
    </div>
  );
}
