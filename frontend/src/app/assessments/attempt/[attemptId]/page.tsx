"use client";

/**
 * Phase 4.2 -- take screen. Phase 4.4 -- result/review/history screen.
 * Answers are only sent at final submission (no autosave endpoint exists
 * -- see the Phase 4.2 report's "answer saving" section for why), so
 * this page holds selections in local state until Submit is pressed. The
 * countdown shown for a timed assessment is informational only: the
 * server is the actual security boundary (see GET/submit responses,
 * which lazily flip an overdue attempt to TIMED_OUT regardless of what
 * this timer displays).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface SafeOption {
  id: number;
  option_text: string;
  order: number;
}

interface SafeQuestion {
  id: number;
  question_text: string;
  question_type: "SINGLE_CHOICE" | "MULTIPLE_CHOICE";
  order: number;
  marks: string;
  is_required: boolean;
  options: SafeOption[];
}

interface InProgressAttempt {
  attempt_id: number;
  attempt_number: number;
  status: "IN_PROGRESS";
  started_at: string;
  deadline: string | null;
  assessment: { id: number; title: string; description: string; instructions: string; passing_percentage: string; max_attempts: number; time_limit_minutes: number | null };
  questions: SafeQuestion[];
}

interface ReviewOption {
  id: number;
  option_text: string;
  order: number;
  is_correct_answer: boolean;
}

interface ReviewQuestion {
  id: number;
  question_text: string;
  question_type: "SINGLE_CHOICE" | "MULTIPLE_CHOICE";
  order: number;
  marks: string;
  explanation: string;
  options: ReviewOption[];
  selected_option_ids: number[];
  answered_correctly: boolean;
  marks_awarded: string;
}

interface FinishedAttempt {
  attempt_id: number;
  attempt_number: number;
  status: "SUBMITTED" | "TIMED_OUT";
  started_at: string;
  submitted_at: string | null;
  assessment: { id: number; title: string; description: string; instructions: string; passing_percentage: string; max_attempts: number; time_limit_minutes: number | null };
  score: string | null;
  percentage: string | null;
  passed: boolean | null;
  total_marks: string | null;
  question_count: number;
  correct_count: number;
  questions: ReviewQuestion[];
}

interface HistoryEntry {
  id: number;
  attempt_number: number;
  status: string;
  score: string | null;
  percentage: string | null;
  passed: boolean | null;
  started_at: string;
  submitted_at: string | null;
  can_review: boolean;
}

type Attempt = InProgressAttempt | FinishedAttempt;

function formatRemaining(ms: number): string {
  if (ms <= 0) return "0:00";
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export default function AssessmentAttemptPage() {
  const { attemptId } = useParams();

  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [answers, setAnswers] = useState<Record<number, Set<number>>>({});
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [showReview, setShowReview] = useState(false);
  const [history, setHistory] = useState<HistoryEntry[] | null>(null);
  const [showHistory, setShowHistory] = useState(false);

  const fetchAttempt = useCallback(async () => {
    const res = await fetch(`${API_BASE}/api/courses/assessment-attempts/${attemptId}/`, {
      credentials: "include",
    });
    if (res.ok) {
      const data: Attempt = await res.json();
      setAttempt(data);
      return data;
    }
    if (res.status === 404) {
      setError("This attempt could not be found.");
    } else {
      setError("Something went wrong loading this attempt.");
    }
    return null;
  }, [attemptId]);

  useEffect(() => {
    fetchAttempt().finally(() => setLoading(false));
  }, [fetchAttempt]);

  // Informational countdown only -- see file docstring.
  useEffect(() => {
    if (!attempt || attempt.status !== "IN_PROGRESS" || !attempt.deadline) return;
    const interval = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [attempt]);

  const deadlineMs = useMemo(() => {
    if (!attempt || attempt.status !== "IN_PROGRESS" || !attempt.deadline) return null;
    return new Date(attempt.deadline).getTime();
  }, [attempt]);

  const toggleOption = (question: SafeQuestion, optionId: number) => {
    setAnswers((prev) => {
      const next = { ...prev };
      const current = new Set(next[question.id] || []);
      if (question.question_type === "SINGLE_CHOICE") {
        next[question.id] = current.has(optionId) ? new Set() : new Set([optionId]);
      } else {
        if (current.has(optionId)) current.delete(optionId);
        else current.add(optionId);
        next[question.id] = current;
      }
      return next;
    });
  };

  const handleSubmit = async () => {
    if (!attempt || attempt.status !== "IN_PROGRESS") return;
    setSubmitting(true);
    setError(null);
    try {
      const payload = {
        answers: attempt.questions.map((q) => ({
          question_id: q.id,
          option_ids: Array.from(answers[q.id] || []),
        })),
      };
      const res = await fetch(`${API_BASE}/api/courses/assessment-attempts/${attemptId}/submit/`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (res.ok) {
        setAttempt(data);
      } else if (res.status === 409) {
        // Already submitted elsewhere -- just refetch the real result.
        await fetchAttempt();
      } else {
        setError(data.error || "Could not submit this attempt.");
        if (data.status === "TIMED_OUT") await fetchAttempt();
      }
    } catch {
      setError("Could not connect to the server. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const loadHistory = async (assessmentId: number) => {
    setShowHistory(true);
    if (history) return;
    try {
      const res = await fetch(
        `${API_BASE}/api/courses/assessment-attempts/my/?assessment_id=${assessmentId}`,
        { credentials: "include" },
      );
      if (res.ok) {
        const data = await res.json();
        setHistory(data.results || []);
      }
    } catch {
      // History is a nice-to-have on this page -- fail silently rather
      // than blocking the already-loaded result.
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="h-64 w-full max-w-2xl bg-zinc-900 animate-pulse rounded-3xl mx-6" />
      </div>
    );
  }

  if (error && !attempt) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center px-6">
        <div className="bg-zinc-900 border border-white/10 rounded-3xl p-8 text-center max-w-md">
          <p className="text-red-400 mb-4">{error}</p>
          <Link href="/dashboard" className="text-[#facc15] hover:text-white transition-colors text-sm">
            Back to Dashboard
          </Link>
        </div>
      </div>
    );
  }

  if (!attempt) return null;

  if (attempt.status !== "IN_PROGRESS") {
    const finished = attempt as FinishedAttempt;
    const attemptsRemaining = Math.max(finished.assessment.max_attempts - finished.attempt_number, 0);
    const canRetry = finished.status !== "SUBMITTED" || finished.passed !== true;
    const showRetryButton = canRetry && attemptsRemaining > 0;

    return (
      <div className="min-h-screen bg-black text-white font-sans px-6 py-12">
        <div className="max-w-2xl mx-auto">
          <div className="bg-zinc-900 border border-white/10 rounded-3xl p-8 md:p-10 text-center mb-6">
            <p className="text-xs uppercase tracking-widest text-zinc-500 mb-1">Assessment Result</p>
            <h1 className="text-2xl font-bold mb-1">{finished.assessment.title}</h1>
            <p className="text-sm text-zinc-500 mb-6">Attempt #{finished.attempt_number}</p>

            {finished.status === "SUBMITTED" ? (
              <>
                <p className="text-sm text-zinc-500 mb-1">Score</p>
                <p className="text-4xl font-bold mb-1">{finished.score} / {finished.total_marks}</p>
                <p className={`text-2xl font-semibold mb-4 ${finished.passed ? "text-[#facc15]" : "text-red-400"}`}>
                  {finished.percentage}%
                </p>
                <p className={`inline-block px-4 py-1.5 rounded-full text-sm font-bold mb-4 ${
                  finished.passed ? "bg-green-500/15 text-green-400" : "bg-red-500/15 text-red-400"
                }`}>
                  {finished.passed ? "PASSED" : "FAILED"}
                </p>
                <p className="text-sm text-zinc-500 mb-1">Passing score: {finished.assessment.passing_percentage}%</p>
                <p className="text-sm text-zinc-400 mb-6">
                  Questions: {finished.correct_count} correct / {finished.question_count}
                </p>
              </>
            ) : (
              <p className="text-zinc-400 mb-8">This attempt ran out of time before it was submitted.</p>
            )}

            <p className="text-xs text-zinc-600 mb-6">
              {finished.status === "SUBMITTED" ? `Submitted ${formatDate(finished.submitted_at)}` : "Timed out"}
            </p>

            <div className="flex flex-col sm:flex-row gap-3 justify-center">
              {finished.status === "SUBMITTED" && (
                <button
                  onClick={() => setShowReview((v) => !v)}
                  className="px-6 py-2.5 rounded-full border border-white/15 text-sm font-semibold text-white hover:bg-white/5 transition-colors"
                >
                  {showReview ? "Hide Review" : "Review Answers"}
                </button>
              )}
              <button
                onClick={() => loadHistory(finished.assessment.id)}
                className="px-6 py-2.5 rounded-full border border-white/15 text-sm font-semibold text-white hover:bg-white/5 transition-colors"
              >
                {showHistory ? "Hide History" : "View Previous Attempts"}
              </button>
              {showRetryButton ? (
                <Link
                  href={`/assessments/${finished.assessment.id}`}
                  className="px-6 py-2.5 rounded-full bg-[#facc15] text-black text-sm font-semibold hover:scale-[1.02] transition-transform"
                >
                  Retry Assessment
                </Link>
              ) : !canRetry ? null : (
                <span className="px-6 py-2.5 rounded-full bg-zinc-800 text-zinc-500 text-sm font-semibold">
                  Maximum attempts reached
                </span>
              )}
              <Link
                href="/dashboard"
                className="px-6 py-2.5 rounded-full border border-white/15 text-sm font-semibold text-zinc-400 hover:text-white hover:bg-white/5 transition-colors"
              >
                Back to Course
              </Link>
            </div>
          </div>

          {showReview && finished.questions.length > 0 && (
            <div className="space-y-4 mb-6">
              {finished.questions.map((q, idx) => (
                <div key={q.id} className="bg-zinc-900 border border-white/10 rounded-2xl p-6">
                  <div className="flex items-start justify-between gap-4 mb-3">
                    <p className="text-sm text-zinc-500">Question {idx + 1}</p>
                    <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${
                      q.answered_correctly ? "bg-green-500/15 text-green-400" : "bg-red-500/15 text-red-400"
                    }`}>
                      {q.answered_correctly ? "Correct" : "Incorrect"}
                    </span>
                  </div>
                  <p className="text-base font-medium mb-4">{q.question_text}</p>
                  <div className="space-y-1.5 mb-4">
                    {q.options.map((opt) => {
                      const wasSelected = q.selected_option_ids.includes(opt.id);
                      return (
                        <div
                          key={opt.id}
                          className={`px-4 py-2.5 rounded-xl border text-sm flex items-center justify-between ${
                            opt.is_correct_answer
                              ? "border-green-500/40 bg-green-500/10 text-green-300"
                              : wasSelected
                                ? "border-red-500/40 bg-red-500/10 text-red-300"
                                : "border-white/10 bg-black/30 text-zinc-400"
                          }`}
                        >
                          <span>{opt.option_text}</span>
                          {wasSelected && <span className="text-[10px] uppercase tracking-wide text-zinc-500">Your answer</span>}
                        </div>
                      );
                    })}
                  </div>
                  <p className="text-sm text-zinc-500 mb-2">Marks: {q.marks_awarded} / {q.marks}</p>
                  {q.explanation && (
                    <div className="mt-3 pt-3 border-t border-white/10">
                      <p className="text-xs uppercase tracking-wide text-zinc-500 mb-1">Explanation</p>
                      <p className="text-sm text-zinc-400">{q.explanation}</p>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}

          {showHistory && (
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6 overflow-x-auto">
              <p className="text-xs uppercase tracking-widest text-zinc-500 mb-4">Attempt History</p>
              {history === null ? (
                <div className="h-20 bg-black/30 animate-pulse rounded-xl" />
              ) : (
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left text-zinc-500 text-xs uppercase tracking-wide">
                      <th className="pb-2 pr-4">Attempt</th>
                      <th className="pb-2 pr-4">Status</th>
                      <th className="pb-2 pr-4">Score</th>
                      <th className="pb-2 pr-4">Result</th>
                      <th className="pb-2">Date</th>
                    </tr>
                  </thead>
                  <tbody>
                    {history.map((h) => (
                      <tr key={h.id} className="border-t border-white/5">
                        <td className="py-2.5 pr-4">#{h.attempt_number}</td>
                        <td className="py-2.5 pr-4 text-zinc-400">{h.status}</td>
                        <td className="py-2.5 pr-4">{h.percentage !== null ? `${h.percentage}%` : "—"}</td>
                        <td className="py-2.5 pr-4">
                          {h.passed === null ? "—" : (
                            <span className={h.passed ? "text-green-400" : "text-red-400"}>
                              {h.passed ? "Passed" : "Failed"}
                            </span>
                          )}
                        </td>
                        <td className="py-2.5 text-zinc-500">{formatDate(h.submitted_at || h.started_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </div>
    );
  }

  const inProgress = attempt as InProgressAttempt;
  const remaining = deadlineMs !== null ? formatRemaining(deadlineMs - now) : null;

  return (
    <div className="min-h-screen bg-black text-white font-sans px-6 py-12">
      <div className="max-w-2xl mx-auto">
        <div className="flex items-center justify-between mb-8">
          <h1 className="text-2xl font-bold">{inProgress.assessment.title}</h1>
          {remaining && (
            <span className="text-sm font-mono px-3 py-1 rounded-full bg-zinc-900 border border-white/10 text-[#facc15]">
              {remaining}
            </span>
          )}
        </div>

        {error && <p className="text-red-400 text-sm mb-4">{error}</p>}

        <div className="space-y-6">
          {inProgress.questions.map((q, idx) => (
            <div key={q.id} className="bg-zinc-900 border border-white/10 rounded-2xl p-6">
              <p className="text-xs text-zinc-500 mb-1">
                Question {idx + 1} of {inProgress.questions.length} &middot; {q.marks} marks
                {q.is_required && <span className="text-[#facc15]"> &middot; Required</span>}
              </p>
              <p className="text-lg font-medium mb-4">{q.question_text}</p>
              <div className="space-y-2">
                {q.options.map((opt) => {
                  const selected = (answers[q.id] || new Set()).has(opt.id);
                  return (
                    <button
                      key={opt.id}
                      type="button"
                      onClick={() => toggleOption(q, opt.id)}
                      className={`w-full text-left px-4 py-3 rounded-xl border transition-colors ${
                        selected
                          ? "border-[#facc15] bg-[#facc15]/10 text-white"
                          : "border-white/10 bg-black/30 text-zinc-300 hover:border-white/30"
                      }`}
                    >
                      {opt.option_text}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        <button
          onClick={handleSubmit}
          disabled={submitting}
          className="w-full mt-8 py-3 rounded-full bg-[#facc15] text-black font-semibold hover:scale-[1.02] transition-transform disabled:opacity-50"
        >
          {submitting ? "Submitting..." : "Submit Attempt"}
        </button>
      </div>
    </div>
  );
}
