import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const api = process.env.CHATFRONT_API ?? "http://localhost:8000";
const from = (path: string) => fileURLToPath(new URL(path, import.meta.url));

export default defineConfig({
  // "/" when the API serves the app; "/chatfront/" on GitHub Pages.
  base: process.env.VITE_BASE ?? "/",
  plugins: [react()],
  resolve: {
    alias: {
      "@i18n": from("../i18n"),
      "@templates": from("../api/src/chatfront/templates"),
    },
  },
  server: {
    port: 5173,
    fs: { allow: [".."] },
    proxy: { "/api": api, "/media": api, "/health": api },
  },
  build: { outDir: "dist", sourcemap: false },
  test: { include: ["src/**/*.test.ts"] },
});
