import { defineConfig, devices } from "@playwright/test";

function portFromEnvironment(name: string, fallback: number): number {
  const value = process.env[name];
  if (!value) return fallback;
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1024 || port > 65535) {
    throw new Error(`${name} must be an integer port between 1024 and 65535.`);
  }
  return port;
}

const runId = (process.env.STARUN_E2E_RUN_ID ?? `pid-${process.pid}`).replace(
  /[^a-zA-Z0-9_-]/g,
  "_",
);
const portOffset = process.pid % 10_000;
const webPort = portFromEnvironment("STARUN_E2E_WEB_PORT", 20_000 + portOffset);
const apiPort = portFromEnvironment("STARUN_E2E_API_PORT", 40_000 + portOffset);
const webOrigin = `http://127.0.0.1:${webPort}`;
const apiOrigin = `http://127.0.0.1:${apiPort}`;
const runtimeDir = `../web/test-results/runtime/${runId}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [
    ["list"],
    ["html", { open: "never", outputFolder: `playwright-report/${runId}` }],
  ],
  outputDir: `test-results/artifacts/${runId}`,
  timeout: 45_000,
  expect: {
    timeout: 12_000,
  },
  use: {
    baseURL: webOrigin,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      testIgnore: /mobile\.spec\.ts/,
    },
    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"] },
      testIgnore: /mobile\.spec\.ts/,
    },
    {
      name: "webkit",
      use: { ...devices["Desktop Safari"] },
      testIgnore: /mobile\.spec\.ts/,
    },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"] },
      testMatch: /mobile\.spec\.ts/,
    },
    {
      name: "mobile-webkit",
      use: { ...devices["iPhone 15"] },
      testMatch: /mobile\.spec\.ts/,
    },
  ],
  webServer: [
    {
      command:
        `node -e "const fs=require('fs');fs.rmSync('${runtimeDir}',{recursive:true,force:true});fs.mkdirSync('${runtimeDir}/data',{recursive:true})" && ` +
        "uv run alembic upgrade head && " +
        `uv run uvicorn app.main:app --host 127.0.0.1 --port ${apiPort} --workers 1`,
      cwd: "../api",
      env: {
        STARUN_DATABASE_URL: `sqlite:///${runtimeDir}/starun-e2e.db`,
        STARUN_DATA_ROOT: `${runtimeDir}/data`,
        STARUN_MIN_FREE_DISK_BYTES: "0",
        STARUN_DAILY_TASK_LIMIT: "100",
        STARUN_MOCK_AGENT_STEP_DELAY_SECONDS: "0.5",
      },
      url: `${apiOrigin}/api/health`,
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `npm run dev -- --hostname 127.0.0.1 --port ${webPort}`,
      cwd: ".",
      env: {
        NEXT_PUBLIC_API_BASE_URL: webOrigin,
        STARUN_API_PROXY_TARGET: apiOrigin,
      },
      url: webOrigin,
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
