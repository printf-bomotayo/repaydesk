/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the app calls /api on its own origin and Vite forwards it to the API, as nginx
// does in the container: no cross-origin requests anywhere, so no CORS to configure.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": process.env.REPAYDESK_API_URL ?? "http://127.0.0.1:18000",
    },
  },
  // A cold run transforms every page first; the first test in each file pays for it.
  test: { environment: "jsdom", globals: true, setupFiles: ["src/test-setup.ts"], testTimeout: 15_000 },
});
