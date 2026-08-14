import { test as base, expect, type Page } from "@playwright/test";

/** The demo workspace created by `python -m tradeloom.cli seed --demo`. */
export const DEMO = {
  email: "demo@example.com",
  password: "DemoTrader!2024",
  workspace: "Reyes Trading",
} as const;

export const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000";

/**
 * Sign in through the real form.
 *
 * Deliberately not a seeded cookie: the login flow sets an HttpOnly session cookie *and* a
 * readable CSRF cookie, and every mutating request in the app depends on both being present. A
 * shortcut here would let a broken auth flow pass the rest of the suite.
 */
export async function signIn(
  page: Page,
  email: string = DEMO.email,
  password: string = DEMO.password,
) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

export const test = base.extend<{ authedPage: Page }>({
  authedPage: async ({ page }, use) => {
    await signIn(page);
    await page.waitForURL("**/dashboard");
    await use(page);
  },
});

export { expect };

/** The readable CSRF cookie the app copies into `X-CSRF-Token` on every unsafe request. */
export const CSRF_COOKIE = "tl_csrf";

/**
 * Send a mutating request the way the app does, from inside the page.
 *
 * Worth a helper rather than inlining: a request with a wrong or missing CSRF token is refused
 * with 403, so a test that only asserts "4xx" would pass whether or not the endpoint under test
 * behaves. Getting the token right is what makes those assertions mean anything.
 */
export async function apiSend(
  page: Page,
  path: string,
  { method = "POST", body }: { method?: string; body?: unknown } = {},
): Promise<{ status: number; json: unknown }> {
  return page.evaluate(
    async ([api, cookieName, target, verb, payload]) => {
      const pattern = new RegExp(`(?:^|;\\s*)${cookieName}=([^;]*)`);
      const token = pattern.exec(document.cookie)?.[1] ?? "";
      const response = await fetch(`${api}${target}`, {
        method: verb as string,
        credentials: "include",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": decodeURIComponent(token) },
        body: payload === undefined ? undefined : JSON.stringify(payload),
      });
      let json: unknown = null;
      try {
        json = await response.json();
      } catch {
        json = null;
      }
      return { status: response.status, json };
    },
    [API_URL, CSRF_COOKIE, path, method, body] as const,
  );
}

/**
 * Assert a string is a money figure the formatter produced, not a leaked float or a null
 * rendered as zero. Catches "NaN", "undefined", "$0.30000000000000004" and friends.
 */
export function expectMoneyLike(text: string | null) {
  expect(text, "expected a rendered value").not.toBeNull();
  const value = (text ?? "").trim();
  expect(value).not.toMatch(/NaN|undefined|null|Infinity/i);
  // Either the explicit "no value" dash, or sign + symbol + grouped digits + exactly 2 decimals.
  expect(value).toMatch(/^(—|[+-]?[^\d\s]{0,4}\s?[\d,]+\.\d{2}$)/);
}

/**
 * Assert no value reached the page unformatted.
 *
 * `formatPrice` caps a price at six decimals, so six is legitimate and seven is not: a longer
 * tail means a decimal string was interpolated straight into JSX, bypassing the formatters. This
 * check found exactly that on the journal and trade-detail pages when it was first written.
 */
export async function expectNoFloatArtefacts(page: Page) {
  const body = (await page.locator("body").innerText()) ?? "";
  expect(body, "NaN reached the page").not.toMatch(/NaN/);
  expect(body, "an object was rendered as a string").not.toMatch(/\[object Object\]/);

  const overlong = body.match(/\d\.\d{7,}/g);
  expect(overlong, `unformatted decimals rendered: ${overlong?.slice(0, 5).join(", ")}`).toBeNull();
}
