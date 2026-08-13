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
