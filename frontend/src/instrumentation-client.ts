import * as Sentry from "@sentry/nextjs";
import { scrubSentryEvent } from "./sentry-scrub";

// Production error tracking gap fix (browser runtime). Only initializes
// when a DSN is configured -- see src/instrumentation.ts's identical
// guard/reasoning for the server+edge side.
const dsn = process.env.NEXT_PUBLIC_SENTRY_DSN;
const environment =
  process.env.NEXT_PUBLIC_SENTRY_ENVIRONMENT ||
  (process.env.NODE_ENV === "production" ? "production" : "development");

if (dsn) {
  Sentry.init({
    dsn,
    environment,
    // Conservative, error-monitoring-only configuration: no performance
    // tracing, and no session-replay integration is added below, so
    // nothing but error capture (plus navigation breadcrumbs, via the
    // hook below) is active.
    tracesSampleRate: 0,
    sendDefaultPii: false,
    beforeSend: scrubSentryEvent,
  });
}

// Required export for App Router navigation breadcrumbs (adds "user
// navigated to /x" context to a later error report) -- unrelated to
// performance tracing, which stays off (tracesSampleRate: 0 above).
export const onRouterTransitionStart = Sentry.captureRouterTransitionStart;
