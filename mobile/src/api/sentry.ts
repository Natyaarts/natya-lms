import * as Sentry from '@sentry/react-native';

// Production error tracking gap fix. Only initializes when a DSN is
// configured -- mirrors the backend's `if SENTRY_DSN:` and the web's
// `if (dsn)` guards exactly, so local development needs no Sentry setup
// at all. EXPO_PUBLIC_* is Expo's own established convention (SDK 49+,
// well within this app's SDK 56) for a value that needs to reach the JS
// bundle at build time -- the same role NEXT_PUBLIC_* plays on the web
// side; a DSN is designed to be public/embeddable, not a secret.
//
// Deliberately conservative and JS-level only for this phase: the
// `@sentry/react-native/expo` config plugin (which would add Sentry's
// Gradle build hooks for automatic Proguard-mapping/source-map upload)
// is NOT added to app.json's plugins array here -- that changes the
// native Android build process in a way that cannot be verified without
// a real EAS build, which this phase does not perform (no production
// EAS credentials available in this environment). This still gives real
// crash/error reporting (unhandled JS exceptions, promise rejections,
// React render errors via Sentry.wrap in App.tsx) via the package's
// standard autolinked native module -- the same autolinking mechanism
// every other native dependency already in this app (expo-notifications,
// expo-document-picker, etc.) already uses safely. See this phase's
// final report for the exact remaining limitation this leaves.
const dsn = process.env.EXPO_PUBLIC_SENTRY_DSN;

// Same denylist/recursive-scrub standard as the backend's EventScrubber
// and the web's src/sentry-scrub.ts -- sendDefaultPii: false already
// keeps cookies/IP/device identity out by default; this is the explicit
// extra layer for anything that does end up in request headers/body/
// extra context (e.g. a crashing screen's local state captured as
// breadcrumb/extra data).
const SENSITIVE_KEYS = new Set([
  'password', 'passwd', 'secret', 'api_key', 'apikey', 'auth', 'authorization',
  'credentials', 'token', 'access_token', 'refresh_token', 'id_token', 'jwt',
  'session', 'sessionid', 'cookie', 'csrftoken', 'csrf_token', 'x_csrftoken',
  'otp', 'otp_code',
  'razorpay_signature', 'razorpay_key_secret', 'razorpay_webhook_secret',
  'phone_number', 'parent_phone',
]);
const REDACTED = '[Filtered]';

function scrubValue(value: unknown, depth = 0): unknown {
  if (depth > 6 || value === null || typeof value !== 'object') return value;
  if (Array.isArray(value)) return value.map((v) => scrubValue(v, depth + 1));
  const result: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
    result[key] = SENSITIVE_KEYS.has(key.toLowerCase()) ? REDACTED : scrubValue(val, depth + 1);
  }
  return result;
}

function scrubEvent(event: Sentry.ErrorEvent): Sentry.ErrorEvent | null {
  const headers = event.request?.headers as Record<string, string> | undefined;
  if (headers) {
    for (const key of Object.keys(headers)) {
      if (['authorization', 'cookie', 'x-csrftoken'].includes(key.toLowerCase())) {
        headers[key] = REDACTED;
      }
    }
  }
  if (event.request?.data) {
    (event.request as any).data = scrubValue(event.request.data);
  }
  if ((event as any).extra) {
    (event as any).extra = scrubValue((event as any).extra);
  }
  return event;
}

export function initSentry(): void {
  if (!dsn) return;

  Sentry.init({
    dsn,
    environment: process.env.EXPO_PUBLIC_SENTRY_ENVIRONMENT || (__DEV__ ? 'development' : 'production'),
    // Conservative, error-monitoring-only configuration -- no
    // performance tracing enabled.
    tracesSampleRate: 0,
    sendDefaultPii: false,
    beforeSend: scrubEvent,
  });
}

export { Sentry };
