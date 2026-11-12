// IndieHR (P1-T1d): browser specs on two hospital addresses (tests-ihr/), separate from
// upstream's playwright.config.ts (its CARE-JWT globalSetup and username/password login are
// gone since P1-T4b). Runs against the dev stack: scripts/care-dev.sh up, seed, keycloak up,
// keycloak seed, frontend. CI: .github/workflows/apps.yml, job "Browser isolation (Playwright)".
//
// Public repository, public artifacts: no trace, video or HTML report. A failed test leaves
// only a screenshot taken with no password or code field on screen and a small scrubbed JSON
// (tests-ihr/lib/evidence.ts). One worker, no retries: a one-time code is never typed twice.
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests-ihr",
  testMatch: "**/*.spec.ts", // lib/*.test.ts are plain node:test unit tests
  outputDir: "./test-results-ihr",
  // Clears an earlier run's "Keycloak refused a code" flag (lib/totp.ts), keeping used steps
  globalSetup: "./tests-ihr/lib/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  forbidOnly: !!process.env.CI,
  timeout: 180_000,
  expect: { timeout: 15_000 },
  reporter: [["list"]],
  use: {
    locale: "en",
    timezoneId: "Asia/Kolkata",
    trace: "off",
    video: "off",
    screenshot: "off",
    actionTimeout: 20_000,
    navigationTimeout: 30_000,
    launchOptions: {
      // Docker publishes on 127.0.0.1 only; never try ::1 first
      args: ["--host-resolver-rules=MAP *.localhost 127.0.0.1"],
    },
  },
  projects: [
    // One real sign-in first: if Keycloak refuses our code, nothing else types one.
    { name: "preflight", testMatch: /preflight\.spec\.ts$/ },
    {
      name: "browser",
      testIgnore: /preflight\.spec\.ts$/,
      dependencies: ["preflight"],
    },
  ],
});
