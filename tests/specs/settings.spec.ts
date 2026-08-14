import { API_URL, expect, signIn, test } from "../support/fixtures";

/**
 * Settings change real state, so these tests check the change survived rather than that a toast
 * appeared. The security section gets the most attention: a password form that reports success
 * without changing the password is worse than one that fails.
 */
test.describe("settings", () => {
  test("a profile change is persisted, not just reflected in the form", async ({
    authedPage: page,
  }) => {
    await page.goto("/settings");
    await expect(page.getByRole("heading", { name: "Settings", level: 1 })).toBeVisible();

    const name = `E2E Display ${Date.now()}`;
    await page.locator("#p-display").fill(name);

    const saved = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/users/me") && response.request().method() === "PATCH",
    );
    await page.getByRole("button", { name: /^Save/ }).first().click();
    expect((await saved).status()).toBe(200);

    // Read it back from the server, not from the input we just typed into.
    const stored = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/users/me`, { credentials: "include" });
      return (await response.json()).data;
    }, API_URL);
    expect(stored.display_name).toBe(name);
  });

  test("the email address is not editable from the profile form", async ({ authedPage: page }) => {
    await page.goto("/settings");

    // Changing an email is an identity change and needs verification; the form must not imply
    // otherwise by presenting it as an ordinary editable field.
    await expect(page.locator("#p-email")).toBeDisabled();
  });

  test("a workspace setting is persisted", async ({ authedPage: page }) => {
    await page.goto("/settings");
    await page.getByRole("tab", { name: "Workspace" }).click();
    await expect(page.locator("#w-name")).toBeVisible();

    const name = `Reyes Trading ${Date.now()}`;
    await page.locator("#w-name").fill(name);

    const saved = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/organizations") &&
        ["PATCH", "PUT"].includes(response.request().method()),
    );
    await page.getByRole("button", { name: /^Save/ }).first().click();
    expect((await saved).status()).toBe(200);

    const stored = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/organizations/current`, {
        credentials: "include",
      });
      return (await response.json()).data;
    }, API_URL);
    expect(stored.name).toBe(name);
  });

  test("changing a password requires the current one", async ({ authedPage: page }) => {
    await page.goto("/settings");
    await page.getByRole("tab", { name: "Security" }).click();
    await expect(page.locator("#s-current")).toBeVisible();

    await page.locator("#s-current").fill("NotTheRightPassword!1");
    await page.locator("#s-new").fill("BrandNewSecret!2026");
    await page.locator("#s-confirm").fill("BrandNewSecret!2026");

    const response = page.waitForResponse(
      (r) => r.url().includes("/api/v1/auth/") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: /Change password|Update password/i }).first().click();

    // Refused, and — the part that matters — the old password still works afterwards.
    expect((await response).status()).toBeGreaterThanOrEqual(400);

    const stillValid = await page.evaluate(async (api) => {
      const login = await fetch(`${api}/api/v1/auth/login`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "demo@example.com", password: "DemoTrader!2024" }),
      });
      return login.status;
    }, API_URL);
    expect(stillValid).toBe(200);
  });

  test("a weak new password is refused", async ({ authedPage: page }) => {
    await page.goto("/settings");
    await page.getByRole("tab", { name: "Security" }).click();
    await expect(page.locator("#s-current")).toBeVisible();

    await page.locator("#s-current").fill("DemoTrader!2024");
    await page.locator("#s-new").fill("password");
    await page.locator("#s-confirm").fill("password");

    const response = page.waitForResponse(
      (r) => r.url().includes("/api/v1/auth/") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: /Change password|Update password/i }).first().click();
    expect((await response).status()).toBeGreaterThanOrEqual(400);

    // The account is untouched: the real password still signs in.
    const stillValid = await page.evaluate(async (api) => {
      const login = await fetch(`${api}/api/v1/auth/login`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: "demo@example.com", password: "DemoTrader!2024" }),
      });
      return login.status;
    }, API_URL);
    expect(stillValid).toBe(200);
  });

  test("a member cannot read another workspace's settings", async ({ page }) => {
    await signIn(page, "admin@example.com", "AdminOps!2024");
    await page.waitForURL("**/dashboard");

    // `current` is resolved from the server-side session, so it must describe the admin's own
    // workspace — the browser has no say in which workspace it is reading.
    const organization = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/organizations/current`, {
        credentials: "include",
      });
      return (await response.json()).data;
    }, API_URL);

    expect(organization.name).not.toContain("Reyes Trading");
  });
});
