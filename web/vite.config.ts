import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const api = process.env.CHATFRONT_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@i18n": fileURLToPath(new URL("../i18n", import.meta.url)) } },
  server: {
    port: 5173,
    fs: { allow: [".."] },
    proxy: { "/api": api, "/media": api, "/health": api },
  },
  build: { outDir: "dist", sourcemap: true },
  test: { include: ["src/**/*.test.ts"] },
});
