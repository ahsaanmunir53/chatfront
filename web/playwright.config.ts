import { defineConfig, devices } from "@playwright/test";

// End-to-end tests run the real API (mock model) and the built web app on one port.
const port = 8010;
const chromiumPath = process.env.PLAYWRIGHT_CHROMIUM_PATH;

export default defineConfig({
  testDir: "e2e",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    locale: "en-US",
    trace: "retain-on-failure",
    launchOptions: chromiumPath ? { executablePath: chromiumPath } : {},
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: [
      "cd ../api",
      "uv run alembic upgrade head",
      "uv run python -m chatfront.seed",
      `uv run uvicorn chatfront.main:create_app --factory --host 127.0.0.1 --port ${port}`,
    ].join(" && "),
    url: `http://127.0.0.1:${port}/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: {
      DATABASE_URL:
        process.env.E2E_DATABASE_URL ??
        "postgresql+asyncpg://chatfront_owner:owner@127.0.0.1:5432/chatfront_e2e",
      AI_MODE: "mock",
      WORKER_ENABLED: "true",
      WORKER_POLL_SECONDS: "0.3",
      PUBLIC_RATE_LIMIT_PER_MINUTE: "1000",
    },
  },
});
