"use client";

/**
 * Phase 4.7 -- teacher/admin grading queue for one assignment. Reachable
 * from the "View Submissions" link added to each published assignment in
 * admin/courses/[id]/page.tsx. Authoring (create/edit/publish) an
 * Assignment stays a Django-admin job, same as Assessment/Question --
 * this page only grades submissions that already exist.
 *
 * Authorization is enforced entirely server-side (IsSuperAdminOrAssignment-
 * CourseInstructor / AssignmentSubmissionViewSet.by_assignment's own
 * CourseInstructor check) -- a teacher not assigned to this course gets a
 * 403 here, which this page just surfaces as an error message.
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
}

interface Submission {
  id: number;
  student: number;
  student_name: string;
  student_email: string;
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
      return { label: "Awaiting Grading", badgeClass: "bg-blue-500/15 text-blue-400" };
    case "GRADED":
      return { label: "Graded", badgeClass: "bg-green-500/15 text-green-400" };
    case "RETURNED_FOR_REVISION":
      return { label: "Returned for Revision", badgeClass: "bg-orange-500/15 text-orange-400" };
    default:
      return { label: status, badgeClass: "bg-zinc-700/40 text-zinc-400" };
  }
}

export default function AssignmentGradingPage() {
  const { assignmentId } = useParams();

  const [assignment, setAssignment] = useState<AssignmentDetail | null>(null);
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Per-submission grading form state, keyed by submission id.
  const [marksInput, setMarksInput] = useState<Record<number, string>>({});
  const [feedbackInput, setFeedbackInput] = useState<Record<number, string>>({});
  const [actionError, setActionError] = useState<Record<number, string>>({});
  const [actionLoading, setActionLoading] = useState<Record<number, boolean>>({});

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
      `${API_BASE}/api/courses/assignment-submissions/assignment/${assignmentId}/`,
      { credentials: "include" }
    );
    if (res.ok) {
      const data = await res.json();
      setSubmissions(data.results || []);
    } else if (res.status === 403) {
      setError("You do not have permission to view submissions for this assignment.");
    } else if (res.status === 404) {
      setError("This assignment could not be found.");
    }
  };

  useEffect(() => {
    const load = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/courses/assignments/${assignmentId}/`, {
          credentials: "include",
        });
        if (res.ok) {
          setAssignment(await res.json());
        }
        await loadSubmissions();
      } catch {
        setError("Could not connect to the server. Please try again.");
      } finally {
        setLoading(false);
      }
    };
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assignmentId]);

  const handleGrade = async (submission: Submission) => {
    const marks = marksInput[submission.id];
    if (marks === undefined || marks.trim() === "") {
      setActionError((prev) => ({ ...prev, [submission.id]: "Enter marks before grading." }));
      return;
    }
    setActionLoading((prev) => ({ ...prev, [submission.id]: true }));
    setActionError((prev) => ({ ...prev, [submission.id]: "" }));
    try {
      const res = await fetch(
        `${API_BASE}/api/courses/assignment-submissions/${submission.id}/grade/`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
          body: JSON.stringify({ marks_awarded: marks, feedback: feedbackInput[submission.id] || "" }),
          credentials: "include",
        }
      );
      const data = await res.json();
      if (res.ok) {
        await loadSubmissions();
      } else {
        setActionError((prev) => ({ ...prev, [submission.id]: data.error || "Could not grade this submission." }));
      }
    } catch {
      setActionError((prev) => ({ ...prev, [submission.id]: "Could not connect to the server. Please try again." }));
    } finally {
      setActionLoading((prev) => ({ ...prev, [submission.id]: false }));
    }
  };

  const handleReturnForRevision = async (submission: Submission) => {
    setActionLoading((prev) => ({ ...prev, [submission.id]: true }));
    setActionError((prev) => ({ ...prev, [submission.id]: "" }));
    try {
      const res = await fetch(
        `${API_BASE}/api/courses/assignment-submissions/${submission.id}/return-for-revision/`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfToken() },
          body: JSON.stringify({ feedback: feedbackInput[submission.id] || "" }),
          credentials: "include",
        }
      );
      const data = await res.json();
      if (res.ok) {
        await loadSubmissions();
      } else {
        setActionError((prev) => ({ ...prev, [submission.id]: data.error || "Could not return this submission for revision." }));
      }
    } catch {
      setActionError((prev) => ({ ...prev, [submission.id]: "Could not connect to the server. Please try again." }));
    } finally {
      setActionLoading((prev) => ({ ...prev, [submission.id]: false }));
    }
  };

  if (loading) return <div className="text-zinc-500 p-8">Loading submissions...</div>;

  return (
    <div className="max-w-4xl mx-auto pb-20">
      <div className="flex items-center gap-4 mb-8">
        <Link href="/admin/courses" className="w-10 h-10 bg-zinc-900 rounded-full flex items-center justify-center hover:bg-zinc-800 transition-colors">
          <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="19" y1="12" x2="5" y2="12"></line>
            <polyline points="12 19 5 12 12 5"></polyline>
          </svg>
        </Link>
        <div>
          <h1 className="text-2xl font-bold">{assignment?.title || "Assignment"}</h1>
          {assignment && <p className="text-sm text-zinc-500">Max marks: {assignment.max_marks}</p>}
        </div>
      </div>

      {error && (
        <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6 text-red-400 text-sm">{error}</div>
      )}

      {!error && submissions.length === 0 && (
        <div className="text-center py-12 text-zinc-500 border border-dashed border-white/10 rounded-xl">
          No submissions yet for this assignment.
        </div>
      )}

      <div className="space-y-4">
        {submissions.map((s) => (
          <div key={s.id} className="bg-zinc-900 border border-white/10 rounded-2xl p-6">
            <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
              <div>
                <p className="font-semibold text-white">{s.student_name}</p>
                <p className="text-xs text-zinc-500">{s.student_email} &middot; Attempt {s.attempt_number}</p>
              </div>
              <span className={`text-[11px] font-semibold px-2 py-1 rounded-full ${statusMeta(s.status).badgeClass}`}>
                {statusMeta(s.status).label}
              </span>
            </div>

            {s.content && (
              <p className="text-sm text-zinc-300 whitespace-pre-line bg-black/40 rounded-xl p-3 mb-3">{s.content}</p>
            )}
            {s.submitted_file && (
              <a
                href={s.submitted_file}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-block text-xs text-[#facc15] hover:text-white transition-colors mb-3"
              >
                View submitted file
              </a>
            )}

            {s.status === "SUBMITTED" ? (
              <div className="space-y-3 pt-3 border-t border-white/5">
                <div className="flex gap-3 flex-wrap">
                  <input
                    type="number"
                    min={0}
                    max={assignment ? Number(assignment.max_marks) : undefined}
                    step="0.01"
                    placeholder={`Marks (0 - ${assignment?.max_marks ?? ""})`}
                    value={marksInput[s.id] ?? ""}
                    onChange={(e) => setMarksInput((prev) => ({ ...prev, [s.id]: e.target.value }))}
                    className="w-40 px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15]"
                  />
                </div>
                <textarea
                  placeholder="Feedback (optional)"
                  rows={3}
                  value={feedbackInput[s.id] ?? ""}
                  onChange={(e) => setFeedbackInput((prev) => ({ ...prev, [s.id]: e.target.value }))}
                  className="w-full px-3 py-2 bg-zinc-950 border border-white/10 rounded-xl text-white text-sm focus:outline-none focus:border-[#facc15] resize-vertical"
                />
                {actionError[s.id] && <p className="text-red-400 text-sm">{actionError[s.id]}</p>}
                <div className="flex gap-3">
                  <button
                    onClick={() => handleGrade(s)}
                    disabled={actionLoading[s.id]}
                    className="px-5 py-2 text-sm bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-colors disabled:opacity-50"
                  >
                    {actionLoading[s.id] ? "Saving..." : "Grade"}
                  </button>
                  <button
                    onClick={() => handleReturnForRevision(s)}
                    disabled={actionLoading[s.id]}
                    className="px-5 py-2 text-sm bg-zinc-800 hover:bg-zinc-700 text-white rounded-xl transition-colors disabled:opacity-50"
                  >
                    Return for Revision
                  </button>
                </div>
              </div>
            ) : s.status === "GRADED" ? (
              <div className="bg-green-500/10 border border-green-500/20 rounded-xl p-4">
                <p className="text-sm font-semibold text-green-400 mb-1">
                  Grade: {s.marks_awarded} / {assignment?.max_marks}
                </p>
                {s.feedback && <p className="text-sm text-zinc-300 whitespace-pre-line">{s.feedback}</p>}
              </div>
            ) : (
              <div className="bg-orange-500/10 border border-orange-500/20 rounded-xl p-4">
                <p className="text-sm font-semibold text-orange-400 mb-1">Returned for Revision</p>
                {s.feedback && <p className="text-sm text-zinc-300 whitespace-pre-line">{s.feedback}</p>}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
