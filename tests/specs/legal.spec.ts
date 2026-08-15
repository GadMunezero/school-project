import { API_URL, expect, test } from "../support/fixtures";

/**
 * Consent has to be something the person did.
 *
 * The checkbox used to be hardcoded true in the form and defaulted true on the server, so nobody
 * had ever agreed to anything and no record existed either way. These tests are about the two
 * halves of fixing that: the box must be ticked, and the documents must be readable before it is.
 */
test.describe("legal and consent", () => {
  test("the documents are readable without an account", async ({ page }) => {
    await page.goto("/legal/terms");
    await expect(page.getByRole("heading", { name: "Terms of Service" }).first()).toBeVisible();

    await page.goto("/legal/privacy");
    await expect(page.getByRole("heading", { name: "Privacy Policy" }).first()).toBeVisible();
  });

  test("an unwritten document says so rather than passing as an agreement", async ({ page }) => {
    await page.goto("/legal/terms");

    // The repository ships placeholders. Presenting them as binding terms would be the worst
    // possible outcome of scaffolding this at all.
    await expect(page.getByText(/has not been written yet/i).first()).toBeVisible();
  });

  test("signup cannot proceed without agreeing", async ({ page }) => {
    await page.goto("/signup");

    await page.getByLabel("Full name").fill("Consent Tester");
    await page.getByLabel("Email").fill(`consent-${Date.now()}@example.com`);
    await page.getByLabel("Password").fill("CorrectHorse!7392");

    // Submit with the box untouched.
    await page.getByRole("button", { name: "Create account" }).click();

    // The checkbox is untouched, so the form says why rather than silently doing nothing.
    await expect(page.getByRole("checkbox")).not.toBeChecked();
    await expect(page.getByText(/accept the terms/i).first()).toBeVisible();
    // And no account was created: the form is still the form.
    await expect(page).toHaveURL(/\/signup/);
  });

  test("the terms link opens the document from the signup form", async ({ page }) => {
    await page.goto("/signup");

    const link = page.getByRole("link", { name: "Terms of Service" });
    await expect(link).toBeVisible();
    await expect(link).toHaveAttribute("href", "/legal/terms");
  });

  test("the API refuses a signup that does not agree, whatever the form does", async ({ page }) => {
    await page.goto("/signup");

    const status = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/auth/signup`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: `bypass-${Date.now()}@example.com`,
          password: "CorrectHorse!7392",
          full_name: "Bypass Attempt",
        }),
      });
      return response.status;
    }, API_URL);

    // The checkbox is a courtesy; this is the control.
    expect(status).toBe(422);
  });
});