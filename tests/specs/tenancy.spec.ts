import { API_URL, expect, test } from "../support/fixtures";

/**
 * The authorization boundary, exercised from the browser.
 *
 * The unit suite proves `TenantRepository` filters by organization. These tests prove the
 * property that matters to a user: nothing the client does — hidden button or hand-made request —
 * reaches another workspace's data, and a resource that exists elsewhere is indistinguishable
 * from one that does not exist at all.
 */
test.describe("tenancy and authorization", () => {
  test("a trade id from another workspace is a 404, not a 403", async ({ authedPage: page }) => {
    // A well-formed UUID that this workspace does not own. A 403 would confirm it exists
    // somewhere, which is itself a disclosure; the API returns 404 for both cases.
    const status = await page.evaluate(async (apiUrl) => {
      const response = await fetch(`${apiUrl}/api/v1/trades/00000000-0000-4000-8000-000000000001`, {
        credentials: "include",
      });
      return response.status;
    }, API_URL);

    expect(status).toBe(404);
  });

  test("the browser cannot choose which workspace it reads", async ({ authedPage: page }) => {
    // The active workspace comes from the server-side session record. Passing an organization id
    // as a parameter must not change what comes back.
    const [normal, spoofed] = await page.evaluate(async (apiUrl) => {
      const get = async (query: string) => {
        const response = await fetch(`${apiUrl}/api/v1/trades?page_size=5${query}`, {
          credentials: "include",
        });
        const body = await response.json();
        return (body?.data ?? []).map((trade: { id: string }) => trade.id).join(",");
      };
      return [
        await get(""),
        await get("&organization_id=00000000-0000-4000-8000-000000000002"),
      ];
    }, API_URL);

    expect(spoofed).toBe(normal);
  });

  test("a mutating request without the CSRF header is refused", async ({ authedPage: page }) => {
    const status = await page.evaluate(async (apiUrl) => {
      // The session cookie rides along automatically; the CSRF token is deliberately omitted,
      // which is exactly the shape of a cross-site forgery.
      const response = await fetch(`${apiUrl}/api/v1/accounts`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: "forged", currency: "USD" }),
      });
      return response.status;
    }, API_URL);

    expect(status).toBeGreaterThanOrEqual(400);
    expect(status).toBeLessThan(500);
  });

  test("signing out server-side makes the cached page unusable", async ({ authedPage: page }) => {
    await page.goto("/journal");
    await expect(page.getByRole("heading", { name: "Journal", level: 1 })).toBeVisible();

    // Revoke the session out from under the open tab, the way an admin suspension would.
    await page.evaluate(async (apiUrl) => {
      const csrf = document.cookie
        .split("; ")
        .find((entry) => entry.startsWith("tl_csrf="))
        ?.split("=")[1];
      await fetch(`${apiUrl}/api/v1/auth/logout`, {
        method: "POST",
        credentials: "include",
        headers: csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {},
      });
    }, API_URL);

    await page.reload();
    await page.waitForURL("**/login**");
  });
});
