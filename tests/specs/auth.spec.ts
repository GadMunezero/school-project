import { DEMO, expect, signIn, test } from "../support/fixtures";

test.describe("authentication", () => {
  test("an unauthenticated visitor cannot reach the app", async ({ page }) => {
    await page.goto("/dashboard");
    await page.waitForURL("**/login**");
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
  });

  test("a wrong password is rejected without revealing whether the account exists", async ({
    page,
  }) => {
    const errorText = async () => {
      const alert = page.locator("#password-error");
      await expect(alert).toBeVisible();
      return (await alert.textContent())?.trim() ?? "";
    };

    await signIn(page, DEMO.email, "definitely-not-the-password");
    const knownAccount = await errorText();

    await signIn(page, "nobody-with-this-address@example.com", "definitely-not-the-password");
    const unknownAccount = await errorText();

    // Identical wording for both, so the form cannot be used to enumerate accounts.
    expect(knownAccount).not.toBe("");
    expect(knownAccount).toBe(unknownAccount);
    // And it must not name which half was wrong.
    expect(knownAccount).not.toMatch(/no such (user|account)|not registered|unknown email/i);
    await expect(page).toHaveURL(/login/);
  });

  test("signing in lands on the dashboard with the workspace loaded", async ({ page }) => {
    await signIn(page);
    await page.waitForURL("**/dashboard");
    await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
    await expect(page.getByText(DEMO.workspace).first()).toBeVisible();
  });

  test("the session survives a reload", async ({ authedPage: page }) => {
    await page.reload();
    await expect(page).toHaveURL(/dashboard/);
    await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
  });

  test("signing out clears the session and blocks the app again", async ({ authedPage: page }) => {
    await page.getByRole("button", { name: /account|menu|dana/i }).first().click();
    await page.getByRole("button", { name: /sign out/i }).click();
    await page.waitForURL("**/login**");

    await page.goto("/journal");
    await page.waitForURL("**/login**");
  });
});
