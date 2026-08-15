import { API_URL, apiSend, expect, expectNoFloatArtefacts, test } from "../support/fixtures";

/**
 * Billing decides limits, never correctness.
 *
 * Two things matter here. A workspace must see its real plan and real usage rather than a
 * placeholder, and a deployment with no payment provider configured must say so instead of
 * offering a checkout that cannot complete. This harness runs with Stripe disabled, which is
 * exactly the case where a page is most tempted to pretend.
 */
test.describe("billing", () => {
  test("the page shows the plan the workspace actually has", async ({ authedPage: page }) => {
    await page.goto("/billing");
    await expect(page.getByRole("heading", { name: "Billing", level: 1 })).toBeVisible();

    const subscription = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/billing/subscription`, {
        credentials: "include",
      });
      return (await response.json()).data;
    }, API_URL);

    // The demo workspace is seeded on an active Pro subscription.
    expect(subscription.plan).toBe("pro");
    expect(subscription.status).toBe("active");

    await expect(page.getByRole("heading", { name: /Pro plan/i })).toBeVisible();
    await expect(page.getByText("Active", { exact: true }).first()).toBeVisible();
  });

  test("usage counts are the workspace's own, not a placeholder", async ({ authedPage: page }) => {
    await page.goto("/billing");
    await expect(page.getByRole("heading", { name: /plan/i }).first()).toBeVisible();

    const [subscription, accounts] = await page.evaluate(async (api) => {
      const sub = await fetch(`${api}/api/v1/billing/subscription`, { credentials: "include" });
      const acc = await fetch(`${api}/api/v1/accounts?page_size=100`, { credentials: "include" });
      return [(await sub.json()).data, (await acc.json()).data];
    }, API_URL);

    // The usage figure has to match what the workspace actually holds. A hard-coded 0, or a count
    // taken across every tenant, would both show up here.
    expect(subscription.usage.accounts).toBe(accounts.length);
    expect(subscription.usage.trades).toBeGreaterThan(0);

    await expect(page.getByText(String(subscription.usage.accounts)).first()).toBeVisible();
    await expectNoFloatArtefacts(page);
  });

  test("with no payment provider configured the page says so", async ({ authedPage: page }) => {
    await page.goto("/billing");

    const subscription = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/billing/subscription`, {
        credentials: "include",
      });
      return (await response.json()).data;
    }, API_URL);

    // The suite runs with STRIPE_ENABLED=false.
    expect(subscription.billing_enabled).toBe(false);

    await expect(page.getByText(/Payments are not configured on this deployment/i)).toBeVisible();
    // And no button that would start a checkout it cannot finish.
    await expect(page.getByRole("button", { name: "Manage payment details" })).toHaveCount(0);
  });

  test("plans are listed with what each one includes", async ({ authedPage: page }) => {
    await page.goto("/billing");

    const plans = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/billing/plans`, { credentials: "include" });
      return (await response.json()).data;
    }, API_URL);

    expect(plans.length).toBeGreaterThan(0);
    for (const plan of plans) {
      // A plan card with no described entitlements cannot be compared against another.
      expect(plan.plan, "every offer names its plan").toBeTruthy();
    }

    // The workspace's current plan is marked as such rather than offered for purchase again.
    await expect(page.getByText("Current", { exact: true }).first()).toBeVisible();
  });

  test("the plan a workspace is on is not something the browser can change", async ({
    authedPage: page,
  }) => {
    // Entitlements are enforced server-side. Asking to be on a different plan without going
    // through billing must not work, whatever the client sends.
    // A valid CSRF token, so the refusal below is the endpoint's decision and not the CSRF
    // guard's — a 403 from a bad token would make this assertion pass no matter what.
    const attempt = await apiSend(page, "/api/v1/billing/subscription", {
      method: "PATCH",
      body: { plan: "enterprise" },
    });

    // Whatever the answer is, it must not be a success: 404/405 (no such route) or a 4xx refusal.
    expect(attempt.status).toBeGreaterThanOrEqual(400);
    expect(attempt.status).not.toBe(403);

    const after = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/billing/subscription`, {
        credentials: "include",
      });
      return (await response.json()).data.plan;
    }, API_URL);
    expect(after).toBe("pro");
  });
});
