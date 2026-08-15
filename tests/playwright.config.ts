import { existsSync, globSync } from "node:fs";

import { defineConfig, devices } from "@playwright/test";

/**
 * Resolve a Chromium to drive.
 *
 * CI images often ship a Chromium build that does not match the one this Playwright release
 * would download, and downloading another copy in a sandbox is both slow and often blocked. So:
 * honour an explicit override, otherwise use a pre-installed browser if one is present, otherwise
 * fall back to Playwright's own managed download.
 */
function resolveChromium(): string | undefined {
  const explicit = process.env.PLAYWRIGHT_CHROMIUM_PATH;
  if (explicit && existsSync(explicit)) return explicit;

  const root = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (!root || !existsSync(root)) return undefined;

  const candidates = globSync(`${root}/chromium-*/chrome-linux/chrome`).sort();
  return candidates.at(-1);
}

const CHROMIUM = resolveChromium();

/**
 * End-to-end configuration.
 *
 * These tests drive the real client against the real API against a real database. Nothing is
 * mocked: if the position builder changes how a partial exit is averaged, a number on the trade
 * detail page changes and a test here fails. That is the point — the unit suites prove each layer
 * in isolation, and this suite proves they are wired together.
 *
 * `scripts/e2e.sh` brings the stack up (seeded SQLite, uvicorn, `next start`) and runs this.
 */
/**
 * `localhost`, not `127.0.0.1`, and deliberately so. The API's CORS allow-list and the session
 * cookie's host are both matched by *string*, so mixing the two spellings makes the browser treat
 * them as different origins: the login preflight fails and the cookie is written to a host the
 * page is not on. Every part of the stack in `scripts/e2e.sh` uses the same spelling.
 */
const WEB_URL = process.env.E2E_WEB_URL ?? "http://localhost:3000";

export default defineConfig({
  testDir: "./specs",
  // Money assertions are exact; a flaky retry would hide a real regression rather than survive one.
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? [["github"], ["html", { open: "never" }]] : [["list"]],

  use: {
    baseURL: WEB_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
  },

  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        ...(CHROMIUM ? { launchOptions: { executablePath: CHROMIUM } } : {}),
      },
    },
  ],
});
