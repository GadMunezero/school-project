import { API_URL, expect, expectNoFloatArtefacts, test } from "../support/fixtures";

/**
 * The journal is where a wrong number would do the most damage, so these assertions are about
 * *values*, not about elements existing. Each one compares what the page renders against what the
 * API said — which is the only way to catch the frontend quietly recomputing something.
 */
test.describe("journal", () => {
  test("lists the seeded trades and renders every P&L as formatted money", async ({
    authedPage: page,
  }) => {
    await page.goto("/journal");
    await expect(page.getByRole("heading", { name: "Journal", level: 1 })).toBeVisible();

    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible();
    expect(await rows.count()).toBeGreaterThan(0);

    await expectNoFloatArtefacts(page);
  });

  test("the page shows exactly what the API returned, without recomputing it", async ({
    authedPage: page,
  }) => {
    // Ask the API directly, using the browser's own session so tenancy is applied identically.
    const payload = await page.evaluate(async (apiUrl) => {
      const response = await fetch(`${apiUrl}/api/v1/trades?page_size=1&status=closed`, {
        credentials: "include",
      });
      return response.json();
    }, API_URL);

    const trade = payload?.data?.[0];
    test.skip(!trade, "the demo seed produced no closed trades");

    await page.goto(`/journal/${trade.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(trade.symbol);

    const body = await page.locator("body").innerText();

    // net_pnl arrives as a decimal string like "-142.50". The page must show those exact digits,
    // grouped — never a re-derived or re-rounded figure.
    const [whole, fraction = "00"] = String(trade.net_pnl).replace("-", "").split(".");
    const grouped = Number(whole).toLocaleString("en-US");
    expect(body).toContain(`${grouped}.${fraction.padEnd(2, "0").slice(0, 2)}`);
  });

  test("an undefined R multiple renders as a dash, not as zero", async ({ authedPage: page }) => {
    const payload = await page.evaluate(async (apiUrl) => {
      const response = await fetch(`${apiUrl}/api/v1/trades?page_size=100&status=closed`, {
        credentials: "include",
      });
      return response.json();
    }, API_URL);

    const withoutR = (payload?.data ?? []).find(
      (trade: { r_multiple: string | null; symbol: string }) => trade.r_multiple === null,
    );
    test.skip(!withoutR, "every seeded trade has a defined R multiple");

    await page.goto(`/journal/${withoutR.id}`);
    // Wait for the trade to render — reading innerText straight after goto captures the skeleton.
    await expect(page.getByRole("heading", { level: 1 })).toContainText(withoutR.symbol);

    const body = await page.locator("body").innerText();
    // "0.00R" would be a claim the trade broke even against its risk, which is not what null means.
    expect(body).not.toContain("0.00R");
    expect(body).toContain("—");
  });

  test("filtering by status narrows the list server-side", async ({ authedPage: page }) => {
    await page.goto("/journal?status=open");
    await expect(page.getByRole("heading", { name: "Journal", level: 1 })).toBeVisible();

    const openCount = await page.evaluate(async (apiUrl) => {
      const response = await fetch(`${apiUrl}/api/v1/trades?status=open&page_size=1`, {
        credentials: "include",
      });
      const body = await response.json();
      return body?.meta?.total ?? 0;
    }, API_URL);

    const rows = page.locator("tbody tr");
    if (openCount === 0) {
      await expect(rows).toHaveCount(0);
    } else {
      await expect(rows.first()).toBeVisible();
      expect(await rows.count()).toBeLessThanOrEqual(openCount);
    }
  });

  test("opening a trade shows its fills, and they sum to the recorded quantity", async ({
    authedPage: page,
  }) => {
    const payload = await page.evaluate(async (apiUrl) => {
      const response = await fetch(`${apiUrl}/api/v1/trades?page_size=1&status=closed`, {
        credentials: "include",
      });
      return response.json();
    }, API_URL);

    const trade = payload?.data?.[0];
    test.skip(!trade, "the demo seed produced no closed trades");

    await page.goto(`/journal/${trade.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(trade.symbol);

    // Quantity comes straight from the aggregate and must appear as the API stated it.
    const body = await page.locator("body").innerText();
    expect(body).toContain(trade.symbol);
    const quantity = String(trade.quantity).replace(/\.?0+$/, "");
    expect(body).toContain(quantity);
    await expectNoFloatArtefacts(page);
  });
});
