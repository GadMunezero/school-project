import { API_URL, apiSend, expect, signIn, test } from "../support/fixtures";

/**
 * Platform administration, from both sides of the boundary.
 *
 * The rule these tests exist for is that the frontend never decides authorization. Hiding a nav
 * link is a courtesy to the user, not a control: what matters is that the API refuses a request
 * from an account without the role, whatever the browser believes about itself.
 */

const ADMIN = { email: "admin@example.com", password: "AdminOps!2024" } as const;

/** Every admin route, hit directly. */
const ADMIN_ROUTES = [
  "/api/v1/admin/overview",
  "/api/v1/admin/users",
  "/api/v1/admin/organizations",
];

async function statusesFor(page: import("@playwright/test").Page, routes: string[]) {
  return page.evaluate(
    async ([api, paths]) => {
      const results: Record<string, number> = {};
      for (const path of paths as string[]) {
        const response = await fetch(`${api}${path}`, { credentials: "include" });
        results[path as string] = response.status;
      }
      return results;
    },
    [API_URL, routes] as const,
  );
}

test.describe("administration", () => {
  test("the console is refused to an account without the role", async ({ authedPage: page }) => {
    // The demo user owns their workspace but holds no platform role.
    await page.goto("/admin");
    await expect(page.getByText("Not available to your account")).toBeVisible();

    // The explanation on screen is a courtesy. This is the control:
    const statuses = await statusesFor(page, ADMIN_ROUTES);
    for (const [route, status] of Object.entries(statuses)) {
      expect(status, `${route} should be refused`).toBe(403);
    }
  });

  test("the nav does not offer administration to a non-admin", async ({ authedPage: page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("navigation").getByRole("link", { name: "Admin" })).toHaveCount(0);
  });

  test("an administrator sees platform-wide totals", async ({ page }) => {
    await signIn(page, ADMIN.email, ADMIN.password);
    await page.waitForURL("**/dashboard");

    await page.goto("/admin");
    await expect(page.getByRole("heading", { name: "Administration", level: 1 })).toBeVisible();

    const statuses = await statusesFor(page, ADMIN_ROUTES);
    for (const [route, status] of Object.entries(statuses)) {
      expect(status, `${route} should be allowed`).toBe(200);
    }

    // The overview counts every workspace, not just the admin's own — that is what makes it a
    // platform view rather than another tenant dashboard.
    const overview = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/admin/overview`, { credentials: "include" });
      return (await response.json()).data;
    }, API_URL);

    // The demo workspace plus the admin's own personal one, and both accounts.
    expect(overview.organizations).toBeGreaterThanOrEqual(2);
    expect(overview.users.total).toBeGreaterThanOrEqual(2);

    // Trades are counted across every workspace. The admin's own has none, so a platform total
    // that matched their session's scope would be zero.
    expect(overview.trades).toBeGreaterThan(0);
  });

  test("platform staff is not workspace access", async ({ page }) => {
    await signIn(page, ADMIN.email, ADMIN.password);
    await page.waitForURL("**/dashboard");

    // The admin can administer the platform, but their session's workspace is their own. Listing
    // trades must return their workspace's trades — an empty list — and never the demo user's.
    const trades = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/trades?page_size=5`, { credentials: "include" });
      return (await response.json()).data as unknown[];
    }, API_URL);

    expect(trades).toEqual([]);
  });

  test("an administrative action is recorded in the audit log", async ({ page }) => {
    await signIn(page, ADMIN.email, ADMIN.password);
    await page.waitForURL("**/dashboard");
    await page.goto("/admin");

    // Deliberately the admin's *own* workspace: overriding the demo workspace's plan would change
    // what the billing spec reads, and tests that quietly reshape each other's fixtures are how a
    // suite starts failing for reasons nobody can reproduce.
    const read = async (path: string) =>
      page.evaluate(
        async ([api, target]) => {
          const response = await fetch(`${api}${target}`, { credentials: "include" });
          return response.json();
        },
        [API_URL, path] as const,
      );

    const organizationId = (await read("/api/v1/organizations/current")).data.id;
    const before = (await read("/api/v1/admin/audit-logs?page_size=1")).meta?.total ?? 0;

    const applied = await apiSend(page, `/api/v1/admin/organizations/${organizationId}/plan`, {
      body: { plan: "pro", reason: "E2E audit trail check" },
    });
    expect(applied.status).toBe(200);

    const after = await read("/api/v1/admin/audit-logs?page_size=10");

    // An audit log that does not grow when an administrator changes a workspace's plan is
    // decoration, not a record.
    expect(after.meta?.total ?? 0).toBeGreaterThan(before);

    // And the entry has to carry the reason the administrator gave for it.
    expect(JSON.stringify(after.data ?? [])).toContain("E2E audit trail check");
  });
});
