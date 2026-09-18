"use client";

import { useEffect } from "react";
import Link from "next/link";
import * as Sentry from "@sentry/nextjs";

// Production error tracking gap fix. This file did not exist before --
// any client-render error anywhere under this route tree previously had
// no error boundary at all (Next.js's own default, unstyled crash
// screen). Reports to Sentry only if a DSN is configured (captureException
// itself is a safe no-op when no client is initialized); always shows a
// recoverable UI regardless of whether reporting succeeds.
export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    Sentry.captureException(error);
  }, [error]);

  return (
    <div className="min-h-screen bg-black text-white font-sans flex items-center justify-center px-6">
      <div className="max-w-md text-center">
        <h1 className="text-3xl font-bold mb-3">Something went wrong</h1>
        <p className="text-zinc-400 text-sm mb-8">
          An unexpected error occurred. It's been reported, and we're looking into it.
        </p>
        <div className="flex gap-3 justify-center">
          <button
            onClick={reset}
            className="px-5 py-2.5 bg-[#facc15] text-black font-bold rounded-xl hover:bg-yellow-500 transition-all text-sm"
          >
            Try Again
          </button>
          <Link
            href="/"
            className="px-5 py-2.5 bg-zinc-900 border border-white/10 hover:bg-zinc-800 text-white font-semibold rounded-xl transition-all text-sm"
          >
            Go Home
          </Link>
        </div>
      </div>
    </div>
  );
}
