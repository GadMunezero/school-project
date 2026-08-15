import { API_URL, apiSend, expect, signIn, test } from "../support/fixtures";

/**
 * The gate that makes a beta a beta.
 *
 * This harness runs with signup open, which is the default, so these tests cover the two things
 * that are true in that mode: an administrator can mint and revoke codes, and the signup form does
 * not ask for one it does not need. Closed-mode refusals are asserted against the real HTTP
 * endpoints in `backend/tests/test_invites.py`, where the setting can actually be flipped.
 */

const ADMIN = { email: "admin@example.com", password: "AdminOps!2024" } as const;

interface Invite {
  id: string;
  code: string;
  note: string | null;
  state: string;
  uses_left: number;
}

async function asAdmin(page: import("@playwright/test").Page) {
  await signIn(page, ADMIN.email, ADMIN.password);
  await page.waitForURL("**/dashboard");
  await page.goto("/admin");
  await page.getByRole("tab", { name: "Invites" }).click();
}

test.describe("invites", () => {
  test("an administrator can issue a code and see it listed", async ({ page }) => {
    await asAdmin(page);

    const note = `E2E tester ${Date.now()}`;
    await page.locator("#i-note").fill(note);
    await page.getByRole("button", { name: "Issue invite" }).click();

    // The row appears with a code an administrator can actually read and send on.
    const row = page.locator("tr", { hasText: note });
    await expect(row).toBeVisible();
    await expect(row.getByText("Active")).toBeVisible();

    const invites = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/admin/invites`, { credentials: "include" });
      return (await response.json()).data as Invite[];
    }, API_URL);

    const created = invites.find((invite) => invite.note === note);
    expect(created, "the issued invite should come back from the API").toBeTruthy();
    expect(created!.code).toMatch(/^[A-Z2-9]{10}$/);
    expect(created!.state).toBe("active");
    expect(created!.uses_left).toBe(1);

    // Nothing confusable: no O/0, I/1, U/V, so a code survives being read off a screen.
    expect(created!.code).not.toMatch(/[OI01LUV]/);
  });

  test("revoking a code takes it out of circulation", async ({ page }) => {
    await asAdmin(page);

    const note = `E2E revoke ${Date.now()}`;
    await page.locator("#i-note").fill(note);
    await page.getByRole("button", { name: "Issue invite" }).click();

    const row = page.locator("tr", { hasText: note });
    await expect(row).toBeVisible();
    await row.getByRole("button", { name: "Revoke" }).click();

    await expect(row.getByText("Revoked")).toBeVisible();
    // And no revoke button remains, because there is nothing left to revoke.
    await expect(row.getByRole("button", { name: "Revoke" })).toHaveCount(0);
  });

  test("a workspace owner cannot issue themselves an invite", async ({ authedPage: page }) => {
    // The demo user owns their workspace but holds no platform role. The nav hiding the admin
    // console is a courtesy; this is the control.
    const attempt = await apiSend(page, "/api/v1/admin/invites", {
      body: { note: "self-service", max_uses: 99 },
    });
    expect(attempt.status).toBe(403);
  });

  test("the signup form asks for a code only when the deployment needs one", async ({ page }) => {
    // Navigate first: `fetch` inside the page needs a page to run in.
    await page.goto("/signup");

    const policy = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/auth/signup-policy`);
      return (await response.json()).data;
    }, API_URL);

    // This harness runs open, so the field must not be there — asking everyone for a code they
    // were never given is its own kind of broken.
    expect(policy.invite_required).toBe(false);

    await expect(page.getByLabel("Full name")).toBeVisible();
    await expect(page.getByLabel("Invite code")).toHaveCount(0);
  });
});
