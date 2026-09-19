import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "path";

// Minimal Vitest setup -- this project had zero test infrastructure before
// the admin-dashboard `.toLocaleString()` crash regression test. Scoped to
// exactly what's needed to render a "use client" App Router page component
// in jsdom: the React plugin (JSX/TSX transform) and the `@/*` path alias
// already defined in tsconfig.json.
export default defineConfig({
  // `as any`: @vitejs/plugin-react and vitest/config resolve two separate
  // (structurally near-identical) copies of vite's own types from nested
  // node_modules, which TypeScript treats as nominally incompatible --
  // a well-known interop wrinkle in this ecosystem, not a real type error.
  // Purely a type-checking cast; does not change runtime behavior.
  plugins: [react()] as any,
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
