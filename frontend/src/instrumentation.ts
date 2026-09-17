import * as Sentry from "@sentry/nextjs";
import { scrubSentryEvent } from "./sentry-scrub";

// Production error tracking gap fix (server + edge runtimes). Only ever
// initializes when a DSN is actually configured -- local development
// needs no Sentry setup at all, matching the backend's own
// `if SENTRY_DSN:` pattern exactly. NEXT_PUBLIC_SENTRY_DSN is used (not a
// server-only var) so the exact same value also reaches the client build
// via instrumentation-client.ts -- a DSN is designed to be public/
// embeddable, not a secret in the same sense as an API key.
const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
const environment =
  process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ||
  (process.env.NODE_ENV === "production" ? "production" : "development");

export async function register() {
  if (!dsn) return;

  if (process.env.NEXT_RUNTIME === "nodejs") {
    Sentry.init({
      dsn,
      environment,
      // Conservative, error-monitoring-only configuration -- no
      // performance tracing by default.
      tracesSampleRate: 0,
      sendDefaultPii: false,
      beforeSend: scrubSentryEvent,
    });
  }

  if (process.env.NEXT_RUNTIME === "edge") {
    Sentry.init({
      dsn,
      environment,
      tracesSampleRate: 0,
      sendDefaultPii: false,
      beforeSend: scrubSentryEvent,
    });
  }
}

export const onRequestError = Sentry.captureRequestError;
