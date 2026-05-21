import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

// Vitest config for component tests (M5-4 onwards).
//
// We intentionally use jsdom (not happy-dom) because jest-dom matchers
// have first-class jsdom support. The `@` alias mirrors tsconfig.json
// so imports like `@/components/...` work in tests without a separate
// alias config.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    // The Next.js .next build dir + node_modules are excluded by
    // default; nothing extra to add today.
  },
});
