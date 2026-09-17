import type { NextConfig } from "next";
import { withSentryConfig } from "@sentry/nextjs/config";

const nextConfig: NextConfig = {
  images: {
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'images.unsplash.com',
      },
      {
        protocol: 'http',
        hostname: 'localhost',
        port: '8000',
      },
    ],
  },
};

// Production error tracking gap fix. withSentryConfig wraps the build to
// (a) auto-inject the DSN/environment tunnel config the instrumentation
// files above already read from env vars, and (b) upload source maps to
// Sentry -- but ONLY when SENTRY_AUTH_TOKEN/SENTRY_ORG/SENTRY_PROJECT are
// present in the build environment; without them the plugin skips the
// upload step and prints a notice, it does not fail the build (verified
// locally via `npm run build` with none of those set). `silent: true`
// keeps that notice out of normal build output; disabling it isn't
// needed for correctness. No source maps or credentials are committed to
// the repo either way -- upload happens at build time only, to Sentry's
// servers, never to this codebase.
export default withSentryConfig(nextConfig, {
  silent: true,
  org: process.env.SENTRY_ORG,
  project: process.env.SENTRY_PROJECT,
});
