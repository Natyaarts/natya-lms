// Production error tracking gap fix -- shared scrubbing helper used by
// both instrumentation.ts (server+edge) and instrumentation-client.ts
// (browser). sendDefaultPii: false (set in both callers) already keeps
// Sentry from attaching cookies/IP/user identity by default -- this is
// an additional, explicit layer: a denylist-based recursive scrub of
// anything that DOES end up in an event's request headers/body/extra
// context, mirroring the backend's own EventScrubber(denylist=...,
// recursive=True) so both sides apply the same standard, not just
// "whatever each SDK happens to do by default".
const SENSITIVE_KEYS = new Set([
  "password", "passwd", "secret", "api_key", "apikey", "auth", "authorization",
  "credentials", "token", "access_token", "refresh_token", "id_token", "jwt",
  "session", "sessionid", "cookie", "csrftoken", "csrf_token", "x_csrftoken",
  "otp", "otp_code",
  "razorpay_signature", "razorpay_key_secret", "razorpay_webhook_secret",
  "phone_number", "parent_phone",
]);

const REDACTED = "[Filtered]";

function scrubValue(value: unknown, depth = 0): unknown {
  if (depth > 6 || value === null || typeof value !== "object") return value;
  if (Array.isArray(value)) return value.map((v) => scrubValue(v, depth + 1));

  const result: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
    result[key] = SENSITIVE_KEYS.has(key.toLowerCase()) ? REDACTED : scrubValue(val, depth + 1);
  }
  return result;
}

/**
 * Scrubs an outgoing Sentry event in place (headers by exact match,
 * request body / extra context recursively by the same denylist above)
 * before it's sent.
 */
export function scrubSentryEvent<T extends { request?: any; extra?: any }>(event: T): T {
  const headers = event.request?.headers as Record<string, string> | undefined;
  if (headers) {
    for (const key of Object.keys(headers)) {
      if (["authorization", "cookie", "x-csrftoken"].includes(key.toLowerCase())) {
        headers[key] = REDACTED;
      }
    }
  }

  if (event.request?.data) {
    event.request.data = scrubValue(event.request.data);
  }
  if (event.request?.cookies) {
    event.request.cookies = REDACTED;
  }
  if (event.extra) {
    event.extra = scrubValue(event.extra);
  }

  return event;
}
