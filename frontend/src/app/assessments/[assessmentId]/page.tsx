"use client";

/**
 * Phase 4.2 -- assessment "start" screen. Deliberately minimal: shows the
 * safe assessment metadata (GET /api/courses/assessments/<id>/ -- a
 * read-only, side-effect-free preview added specifically so this screen
 * can show instructions/passing criteria BEFORE the student commits to
 * starting, since starting begins the time-limit clock server-side) and
 * a single action button. No management dashboard, no editing.
 */

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface AssessmentPreview {
  id: number;
  title: string;
  description: string;
  instructions: string;
  passing_percentage: string;
  max_attempts: number;
  time_limit_minutes: number | null;
  question_count: number;
  attempts_used: number;
  attempts_remaining: number;
  active_attempt_id: number | null;
  can_start: boolean;
}

export default function AssessmentStartPage() {
  const { assessmentId } = useParams();
  const router = useRouter();

  const [preview, setPreview] = useState<AssessmentPreview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/courses/assessments/${assessmentId}/`, {
          credentials: "include",
        });
        if (res.status === 401 || res.status === 403) {
          setError("You do not have access to this assessment.");
        } else if (res.status === 404) {
          setError("This assessment could not be found.");
        } else if (res.ok) {
          setPreview(await res.json());
        } else {
          setError("Something went wrong loading this assessment.");
        }
      } catch {
        setError("Could not connect to the server. Please try again.");
      } finally {
        setLoading(false);
      }
    };
    load();
  }, [assessmentId]);

  const handleStart = async () => {
    if (preview?.active_attempt_id) {
      router.push(`/assessments/attempt/${preview.active_attempt_id}`);
      return;
    }
    setStarting(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/courses/assessments/${assessmentId}/start/`, {
        method: "POST",
        credentials: "include",
      });
      const data = await res.json();
      if (res.ok) {
        router.push(`/assessments/attempt/${data.attempt_id}`);
      } else {
        setError(data.error || "Could not start this assessment.");
        setStarting(false);
      }
    } catch {
      setError("Could not connect to the server. Please try again.");
      setStarting(false);
    }
  };

  return (
    <div className="min-h-screen bg-black text-white font-sans flex items-center justify-center px-6 py-16">
      <div className="w-full max-w-xl">
        {loading ? (
          <div className="h-64 bg-zinc-900 animate-pulse rounded-3xl" />
        ) : error && !preview ? (
          <div className="bg-zinc-900 border border-white/10 rounded-3xl p-8 text-center">
            <p className="text-red-400 mb-4">{error}</p>
            <Link href="/dashboard" className="text-[#facc15] hover:text-white transition-colors text-sm">
              Back to Dashboard
            </Link>
          </div>
        ) : preview ? (
          <div className="bg-zinc-900 border border-white/10 rounded-3xl p-8">
            <h1 className="text-3xl font-bold mb-2">{preview.title}</h1>
            {preview.description && <p className="text-zinc-400 mb-6">{preview.description}</p>}

            <div className="grid grid-cols-2 gap-4 mb-6 text-sm">
              <div className="bg-black/40 rounded-xl p-4">
                <p className="text-zinc-500">Questions</p>
                <p className="text-lg font-semibold">{preview.question_count}</p>
              </div>
              <div className="bg-black/40 rounded-xl p-4">
                <p className="text-zinc-500">Passing Score</p>
                <p className="text-lg font-semibold">{preview.passing_percentage}%</p>
              </div>
              <div className="bg-black/40 rounded-xl p-4">
                <p className="text-zinc-500">Attempts</p>
                <p className="text-lg font-semibold">{preview.attempts_used} / {preview.max_attempts}</p>
              </div>
              <div className="bg-black/40 rounded-xl p-4">
                <p className="text-zinc-500">Time Limit</p>
                <p className="text-lg font-semibold">
                  {preview.time_limit_minutes ? `${preview.time_limit_minutes} min` : "None"}
                </p>
              </div>
            </div>

            {preview.instructions && (
              <div className="mb-6">
                <p className="text-sm font-semibold text-zinc-300 mb-1">Instructions</p>
                <p className="text-sm text-zinc-400 whitespace-pre-line">{preview.instructions}</p>
              </div>
            )}

            {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

            {preview.active_attempt_id ? (
              <button
                onClick={handleStart}
                className="w-full py-3 rounded-full bg-[#facc15] text-black font-semibold hover:scale-[1.02] transition-transform"
              >
                Continue Attempt
              </button>
            ) : preview.can_start ? (
              <button
                onClick={handleStart}
                disabled={starting}
                className="w-full py-3 rounded-full bg-[#facc15] text-black font-semibold hover:scale-[1.02] transition-transform disabled:opacity-50"
              >
                {starting ? "Starting..." : "Start Assessment"}
              </button>
            ) : (
              <button disabled className="w-full py-3 rounded-full bg-zinc-800 text-zinc-500 font-semibold cursor-not-allowed">
                Maximum Attempts Reached
              </button>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
