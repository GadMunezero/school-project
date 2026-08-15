import { API_URL, apiSend, expect, signIn, test } from "../support/fixtures";

/**
 * The beta feedback loop, end to end: a user sends something from wherever they are, and it turns
 * up in the staff queue with enough context to act on.
 */

const ADMIN = { email: "admin@example.com", password: "AdminOps!2024" } as const;

test.describe("feedback", () => {
  test("a report sent from a page reaches staff with that page attached", async ({
    authedPage: page,
    browser,
  }) => {
    // Somewhere specific, so the recorded page is worth asserting on.
    await page.goto("/reports");
    await expect(page.getByRole("heading", { name: "Reports", level: 1 })).toBeVisible();

    const message = `E2E feedback ${Date.now()} — the equity curve stops a day short.`;
    await page.getByRole("button", { name: "Feedback" }).click();
    await page.locator("#fb-kind").selectOption("bug");
    await page.locator("#fb-message").fill(message);
    await page.getByRole("button", { name: "Send", exact: true }).click();

    // The dialog closes on success, which is the user-visible confirmation.
    await expect(page.locator("#fb-message")).toHaveCount(0);

    // Now read it as staff, from a separate session.
    const staffContext = await browser.newContext();
    const staffPage = await staffContext.newPage();
    await signIn(staffPage, ADMIN.email, ADMIN.password);
    await staffPage.waitForURL("**/dashboard");

    const reports = await staffPage.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/admin/feedback?page_size=50`, {
        credentials: "include",
      });
      return (await response.json()).data as {
        message: string;
        page: string | null;
        kind: string;
        status: string;
        reporter_email: string | null;
        context: Record<string, string>;
      }[];
    }, API_URL);

    const report = reports.find((row) => row.message === message);
    expect(report, "the report should be in the staff queue").toBeTruthy();
    expect(report!.kind).toBe("bug");
    expect(report!.status).toBe("new");
    // The page and reporter are what make a report actionable rather than a shrug.
    expect(report!.page).toBe("/reports");
    expect(report!.reporter_email).toBe("demo@example.com");
    expect(report!.context.viewport).toMatch(/^\d+x\d+$/);

    await staffContext.close();
  });

  test("the queue is not readable by the workspace that filed into it", async ({
    authedPage: page,
  }) => {
    const listed = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/admin/feedback`, { credentials: "include" });
      return response.status;
    }, API_URL);

    expect(listed).toBe(403);
  });

  test("an empty report cannot be sent", async ({ authedPage: page }) => {
    await page.goto("/dashboard");
    await page.getByRole("button", { name: "Feedback" }).click();

    // Nothing typed: the control says so by being unavailable rather than failing after a click.
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();

    await page.locator("#fb-message").fill("ok");
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeDisabled();

    await page.locator("#fb-message").fill("Long enough to be a report.");
    await expect(page.getByRole("button", { name: "Send", exact: true })).toBeEnabled();
  });

  test("staff can triage a report out of the queue", async ({ page }) => {
    await signIn(page, ADMIN.email, ADMIN.password);
    await page.waitForURL("**/dashboard");

    const created = await apiSend(page, "/api/v1/feedback", {
      body: { kind: "idea", message: `E2E triage ${Date.now()}`, page: "/dashboard" },
    });
    expect(created.status).toBe(201);
    const id = (created.json as { data: { id: string } }).data.id;

    await page.goto("/admin");
    await page.getByRole("tab", { name: "Feedback" }).click();

    const card = page.locator("div", { hasText: "E2E triage" }).last();
    await expect(card).toBeVisible();

    const triaged = await apiSend(page, `/api/v1/admin/feedback/${id}/status`, {
      body: { status: "reviewed" },
    });
    expect(triaged.status).toBe(200);

    // The default filter shows what still needs attention, so a reviewed report leaves it.
    await page.reload();
    await page.getByRole("tab", { name: "Feedback" }).click();
    await expect(page.getByText(`E2E triage`, { exact: false })).toHaveCount(0);
  });
});
