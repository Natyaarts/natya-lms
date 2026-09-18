"use client";

import { useEffect } from "react";
import * as Sentry from "@sentry/nextjs";

// Production error tracking gap fix. Next.js App Router requirement:
// global-error.tsx replaces the ENTIRE root layout (including <html>/
// <body>) when an error escapes even the root layout itself -- it must
// render its own full document, unlike error.tsx above. This is the
// last-resort boundary; error.tsx (nested, reuses the normal layout)
// handles everything else.
export default function GlobalError({
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
    <html lang="en">
      <body style={{ background: "#000", color: "#fff", fontFamily: "sans-serif" }}>
        <div style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center", padding: "24px" }}>
          <div style={{ maxWidth: "420px", textAlign: "center" }}>
            <h1 style={{ fontSize: "1.75rem", fontWeight: 700, marginBottom: "0.75rem" }}>Something went wrong</h1>
            <p style={{ color: "#a1a1aa", fontSize: "0.875rem", marginBottom: "2rem" }}>
              An unexpected error occurred. It&apos;s been reported, and we&apos;re looking into it.
            </p>
            <button
              onClick={reset}
              style={{
                padding: "0.625rem 1.25rem",
                background: "#facc15",
                color: "#000",
                fontWeight: 700,
                borderRadius: "0.75rem",
                border: "none",
                fontSize: "0.875rem",
                cursor: "pointer",
              }}
            >
              Try Again
            </button>
          </div>
        </div>
      </body>
    </html>
  );
}
