"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import CertificateRenderer, { CertificateTemplateData, DEFAULT_TEMPLATE } from "@/components/CertificateRenderer";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface Certificate {
  id: number;
  course_id: number;
  verification_id: string;
  learner_name_snapshot: string;
  course_title_snapshot: string;
  issued_at: string;
}

export default function CertificatePage() {
  const { courseId } = useParams();
  const [certificate, setCertificate] = useState<Certificate | null>(null);
  const [template, setTemplate] = useState<CertificateTemplateData>(DEFAULT_TEMPLATE);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = async () => {
      try {
        // 1. Fetch certificate
        const res = await fetch(`${API_BASE}/api/courses/certificates/course/${courseId}/`, {
          credentials: "include",
        });

        if (res.ok) {
          const certData = await res.json();
          setCertificate(certData);

          // 2. Fetch dynamic template
          try {
            const cached = localStorage.getItem("natya_certificate_template");
            if (cached) {
              setTemplate(JSON.parse(cached));
            }
            const tRes = await fetch(`${API_BASE}/api/courses/certificate-template/?course_id=${courseId}`, {
              credentials: "include",
            });
            if (tRes.ok) {
              const tData = await tRes.json();
              setTemplate(tData);
            }
          } catch (e) {
            console.error("Using default certificate template", e);
          }
        } else if (res.status === 404) {
          const body = await res.json().catch(() => ({}));
          setError(
            body.error ||
              "This course is not yet complete -- finish every lesson and pass every required assessment to earn your certificate."
          );
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
          <Link href="/dashboard" className="text-[#facc15] hover:text-white transition-colors text-sm font-semibold">
            Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white font-sans px-4 sm:px-6 py-10 print:bg-white print:p-0">
      <style>{`
        @media print {
          @page { size: landscape A4; margin: 0; }
          body { background: white !important; margin: 0 !important; padding: 0 !important; }
          .no-print { display: none !important; }
        }
      `}</style>

      {/* Top Action Bar */}
      <div className="max-w-4xl mx-auto no-print flex items-center justify-between mb-6">
        <Link href="/dashboard" className="text-sm text-zinc-400 hover:text-white transition-colors font-medium">
          &larr; Back to Dashboard
        </Link>
        <button
          onClick={() => window.print()}
          className="px-6 py-2.5 rounded-full bg-[#facc15] text-black text-sm font-bold hover:scale-[1.02] transition-transform shadow-lg shadow-[#facc15]/20 flex items-center gap-2"
        >
          <span>🖨️</span>
          <span>Print / Save as PDF</span>
        </button>
      </div>

      {/* Dynamic Certificate Canvas */}
      <div className="max-w-4xl mx-auto">
        <CertificateRenderer
          template={template}
          learnerName={certificate.learner_name_snapshot}
          courseTitle={certificate.course_title_snapshot}
          issuedAt={certificate.issued_at}
          verificationId={certificate.verification_id}
          isPrintMode={false}
        />
      </div>

      {/* Verification Link */}
      <div className="max-w-4xl mx-auto no-print mt-6 text-center">
        <Link
          href={`/verify/${certificate.verification_id}`}
          className="text-xs text-zinc-500 hover:text-[#facc15] transition-colors"
        >
          Verify certificate authenticity at /verify/{certificate.verification_id} ↗
        </Link>
      </div>
    </div>
  );
}
