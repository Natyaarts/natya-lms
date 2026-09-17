"use client";

/**
 * Phase 4.6 -- the learner's own certificate view/print page. Fetches
 * (and lazily generates, server-side, if this is the first visit)
 * GET /api/courses/certificates/course/<courseId>/ -- eligibility is
 * NEVER computed here; a 404 simply means "not eligible yet" per the
 * backend's own authoritative Phase 4.5 completion check.
 *
 * "Download" is the browser's own Print -> Save as PDF (see the
 * @media print rules below) -- no server-side PDF file exists, by
 * deliberate design (see the Phase 4.6 report).
 */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Certificate {
  id: number;
  course_id: number;
  verification_id: string;
  learner_name_snapshot: string;
  course_title_snapshot: string;
  issued_at: string;
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" });
}

export default function CertificatePage() {
  const { courseId } = useParams();
  const [certificate, setCertificate] = useState<Certificate | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/courses/certificates/course/${courseId}/`, {
          credentials: "include",
        });
        if (res.ok) {
          setCertificate(await res.json());
        } else if (res.status === 404) {
          const body = await res.json().catch(() => ({}));
          setError(body.error || "This course is not yet complete -- finish every lesson and pass every required assessment to earn your certificate.");
        } else if (res.status === 401 || res.status === 403) {
          setError("Please log in to view your certificate.");
        } else {
          setError("Something went wrong loading your certificate.");
        }
      } catch {
        setError("Could not connect to the server. Please try again.");
      } finally {
        setLoading(false);
      }
    };
    if (courseId) load();
  }, [courseId]);

  if (loading) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="h-64 w-full max-w-2xl bg-zinc-900 animate-pulse rounded-3xl mx-6" />
      </div>
    );
  }

  if (error || !certificate) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center px-6">
        <div className="bg-zinc-900 border border-white/10 rounded-3xl p-8 text-center max-w-md">
          <p className="text-zinc-300 mb-4">{error}</p>
          <Link href="/dashboard" className="text-[#facc15] hover:text-white transition-colors text-sm">
            Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white font-sans px-6 py-12 print:bg-white print:p-0">
      <style>{`
        @media print {
          body { background: white !important; }
          .no-print { display: none !important; }
          .certificate-card { box-shadow: none !important; border: 2px solid #d4af37 !important; }
        }
      `}</style>

      <div className="max-w-3xl mx-auto no-print flex items-center justify-between mb-6">
        <Link href="/dashboard" className="text-sm text-zinc-400 hover:text-white transition-colors">
          &larr; Back to Dashboard
        </Link>
        <button
          onClick={() => window.print()}
          className="px-5 py-2 rounded-full bg-[#facc15] text-black text-sm font-semibold hover:scale-[1.02] transition-transform"
        >
          Print / Save as PDF
        </button>
      </div>

      <div className="certificate-card max-w-3xl mx-auto bg-white text-black rounded-2xl border-4 border-[#d4af37] p-12 text-center relative overflow-hidden">
        <div className="absolute inset-0 border-[10px] border-double border-[#d4af37]/40 m-3 pointer-events-none rounded-xl" />
        <p className="text-xs uppercase tracking-[0.3em] text-zinc-500 mb-2">Natya LMS</p>
        <h1 className="text-3xl font-serif font-bold mb-8">Certificate of Completion</h1>

        <p className="text-sm text-zinc-500 mb-2">This certifies that</p>
        <p className="text-4xl font-serif font-bold mb-8 text-[#8a6d1a]">{certificate.learner_name_snapshot}</p>

        <p className="text-sm text-zinc-500 mb-2">has successfully completed the course</p>
        <p className="text-2xl font-semibold mb-8">{certificate.course_title_snapshot}</p>

        <p className="text-sm text-zinc-500 mb-10">Issued on {formatDate(certificate.issued_at)}</p>

        <div className="flex items-center justify-between text-xs text-zinc-500 border-t border-zinc-200 pt-4 mt-4">
          <span>Certificate ID: {certificate.verification_id}</span>
          <span>Verify at: /verify/{certificate.verification_id}</span>
        </div>
      </div>

      <div className="max-w-3xl mx-auto no-print mt-6 text-center">
        <Link
          href={`/verify/${certificate.verification_id}`}
          className="text-sm text-zinc-500 hover:text-[#facc15] transition-colors"
        >
          View public verification page
        </Link>
      </div>
    </div>
  );
}
