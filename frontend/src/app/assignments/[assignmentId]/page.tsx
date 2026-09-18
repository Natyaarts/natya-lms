"use client";

/**
 * Phase 4.7 -- student-facing assignment detail/submission screen. Mirrors
 * assessments/[assessmentId]/page.tsx's exact "minimal preview + one action"
 * shape, adapted for assignments: text/file submission instead of starting
 * a timed attempt, and a resubmission path gated entirely by server-computed
 * status (never guessed client-side).
 */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface AssignmentDetail {
  id: number;
  title: string;
  description: string;
  max_marks: string;
  order: number;
}

interface Submission {
  id: number;
  attempt_number: number;
  status: "SUBMITTED" | "GRADED" | "RETURNED_FOR_REVISION";
  content: string;
  submitted_file: string | null;
  marks_awarded: string | null;
  feedback: string;
  submitted_at: string;
  graded_at: string | null;
}

function statusMeta(status: string) {
  switch (status) {
    case "SUBMITTED":
      return { label: "Submitted -- Awaiting Grading", badgeClass: "bg-blue-500/15 text-blue-400" };
    case "GRADED":
      return { label: "Graded", badgeClass: "bg-green-500/15 text-green-400" };
    case "RETURNED_FOR_REVISION":
      return { label: "Revision Requested", badgeClass: "bg-orange-500/15 text-orange-400" };
    default:
      return { label: status, badgeClass: "bg-zinc-700/40 text-zinc-400" };
  }
}

export default function AssignmentDetailPage() {
  const { assignmentId } = useParams();

  const [assignment, setAssignment] = useState<AssignmentDetail | null>(null);
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [content, setContent] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

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

  const loadSubmissions = async () => {
    const res = await fetch(
      `${API_BASE}/api/courses/assignment-submissions/my/?assignment_id=${assignmentId}`,
      { credentials: "include" }
    );
    if (res.ok) {
      const data = await res.json();
      setSubmissions(data.results || []);
    }
  };

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/courses/assignments/${assignmentId}/`, {
          credentials: "include",
        });
        if (res.status === 401 || res.status === 403) {
          setError("You do not have access to this assignment.");
        } else if (res.status === 404) {
          setError("This assignment could not be found.");
        } else if (res.ok) {
          setAssignment(await res.json());
          await loadSubmissions();
        } else {
          setError("Something went wrong loading this assignment.");
        }
      } catch {
        setError("Could not connect to the server. Please try again.");
      } finally {
        setLoading(false);
      }
    };
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assignmentId]);

  // Latest submission is first -- the `my` endpoint orders -submitted_at.
  const latest = submissions[0] || null;
  // Resubmission (or a first submission) is only offered when the server's
  // own status says so -- never a client-side guess.
  const canSubmit = !latest || latest.status === "RETURNED_FOR_REVISION";

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!content.trim() && !file) {
      setSubmitError("Provide submission text or a file.");
      return;
    }
    setSubmitting(true);
    setSubmitError(null);
    try {
      const formData = new FormData();
      if (content.trim()) formData.append("content", content);
      if (file) formData.append("file", file);

      const res = await fetch(`${API_BASE}/api/courses/assignments/${assignmentId}/submit/`, {
        method: "POST",
        headers: { "X-CSRFToken": getCsrfToken() },
        body: formData,
        credentials: "include",
      });
      const data = await res.json();
      if (res.ok) {
        setContent("");
        setFile(null);
        await loadSubmissions();
      } else {
        setSubmitError(data.error || "Could not submit this assignment.");
      }
    } catch {
      setSubmitError("Could not connect to the server. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-black text-white font-sans flex items-center justify-center px-6 py-16">
      <div className="w-full max-w-xl">
        {loading ? (
          <div className="h-64 bg-zinc-900 animate-pulse rounded-3xl" />
        ) : error && !assignment ? (
          <div className="bg-zinc-900 border border-white/10 rounded-3xl p-8 text-center">
            <p className="text-red-400 mb-4">{error}</p>
            <Link href="/dashboard" className="text-[#facc15] hover:text-white transition-colors text-sm">
              Back to Dashboard
            </Link>
          </div>
        ) : assignment ? (
          <div className="bg-zinc-900 border border-white/10 rounded-3xl p-8">
            <h1 className="text-3xl font-bold mb-2">{assignment.title}</h1>
            {assignment.description && (
              <p className="text-zinc-400 mb-6 whitespace-pre-line">{assignment.description}</p>
            )}

            <div className="bg-black/40 rounded-xl p-4 mb-6 inline-block text-sm">
              <p className="text-zinc-500">Max Marks</p>
              <p className="text-lg font-semibold">{assignment.max_marks}</p>
            </div>

            {/* Latest submission status + grade/feedback */}
            {latest && (
              <div className="mb-6">
                <div className="flex items-center gap-2 mb-3">
                  <span className={`text-[11px] font-semibold px-2 py-1 rounded-full ${statusMeta(latest.status).badgeClass}`}>
                    {statusMeta(latest.status).label}
                  </span>
                  <span className="text-xs text-zinc-500">Attempt {latest.attempt_number}</span>
                </div>

                {latest.content && (
                  <div className="mb-3">
                    <p className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-1">Your Submission</p>
                    <p className="text-sm text-zinc-300 whitespace-pre-line bg-black/40 rounded-xl p-3">{latest.content}</p>
                  </div>
                )}
                {latest.submitted_file && (
                  <a
                    href={latest.submitted_file}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-block text-xs text-[#facc15] hover:text-white transition-colors mb-3"
                  >
                    View submitted file
                  </a>
                )}

                {latest.status === "GRADED" && (
                  <div className="bg-green-500/10 border border-green-500/20 rounded-xl p-4 mb-3">
                    <p className="text-sm font-semibold text-green-400 mb-1">
                      Grade: {latest.marks_awarded} / {assignment.max_marks}
                    </p>
                    {latest.feedback && <p className="text-sm text-zinc-300 whitespace-pre-line">{latest.feedback}</p>}
                  </div>
                )}

                {latest.status === "RETURNED_FOR_REVISION" && latest.feedback && (
                  <div className="bg-orange-500/10 border border-orange-500/20 rounded-xl p-4 mb-3">
                    <p className="text-sm font-semibold text-orange-400 mb-1">Feedback</p>
                    <p className="text-sm text-zinc-300 whitespace-pre-line">{latest.feedback}</p>
                  </div>
                )}
              </div>
            )}

            {/* Submit / resubmit form -- shown only when the server's own
                status permits it (no submission yet, or returned for
                revision). A SUBMITTED-awaiting-grading or GRADED submission
                shows no form at all. */}
            {canSubmit && (
              <form onSubmit={handleSubmit} className="space-y-4">
                <h2 className="text-sm font-semibold text-zinc-300">
                  {latest ? "Resubmit Assignment" : "Submit Assignment"}
                </h2>
                <textarea
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                  placeholder="Write your submission here (optional if attaching a file)..."
                  rows={6}
                  className="w-full px-3 py-2 bg-black/40 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] resize-vertical"
                />
                <div>
                  <input
                    type="file"
                    onChange={(e) => setFile(e.target.files?.[0] || null)}
                    className="text-sm text-zinc-400 file:mr-3 file:py-2 file:px-4 file:rounded-xl file:border-0 file:bg-zinc-800 file:text-white file:text-xs file:font-semibold hover:file:bg-zinc-700"
                  />
                </div>
                {submitError && <p className="text-red-400 text-sm">{submitError}</p>}
                <button
                  type="submit"
                  disabled={submitting}
                  className="w-full py-3 rounded-full bg-[#facc15] text-black font-semibold hover:scale-[1.02] transition-transform disabled:opacity-50"
                >
                  {submitting ? "Submitting..." : latest ? "Resubmit" : "Submit"}
                </button>
              </form>
            )}

            {/* Submission history */}
            {submissions.length > 1 && (
              <div className="mt-8 pt-6 border-t border-white/10">
                <p className="text-xs font-semibold text-zinc-500 uppercase tracking-wide mb-3">Submission History</p>
                <div className="space-y-2">
                  {submissions.map((s) => (
                    <div key={s.id} className="flex items-center justify-between bg-black/40 rounded-xl px-4 py-2 text-sm">
                      <span className="text-zinc-400">Attempt {s.attempt_number}</span>
                      <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${statusMeta(s.status).badgeClass}`}>
                        {statusMeta(s.status).label}
                      </span>
                      {s.status === "GRADED" && (
                        <span className="text-zinc-300">{s.marks_awarded} / {assignment.max_marks}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
