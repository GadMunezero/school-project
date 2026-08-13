import { API_URL, expect, expectNoFloatArtefacts, test } from "../support/fixtures";

test.describe("dashboard and analytics", () => {
  test("headline metrics match the analytics endpoint exactly", async ({ authedPage: page }) => {
    await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();

    const result = await page.evaluate(async (apiUrl) => {
      const response = await fetch(`${apiUrl}/api/v1/analytics/dashboard?days=90`, {
        credentials: "include",
      });
      return response.json();
    }, API_URL);

    const metrics = result?.data?.metrics ?? {};
    test.skip(!metrics.total_trades || Number(metrics.total_trades) === 0, "no trades in window");

    // Read the tile itself rather than scanning the page, so this cannot pass on a coincidence.
    const tile = page.locator("div", { has: page.getByText("Win rate", { exact: true }) }).last();
    const shown = (await tile.innerText()).match(/([\d,]+\.\d+)%/)?.[1]?.replace(/,/g, "");

    if (metrics.win_rate === null) {
      expect(await tile.innerText()).toContain("—");
    } else {
      expect(shown, "the win rate tile should show a percentage").toBeTruthy();
      // The tile must display the API's figure, rounded for presentation and nothing more —
      // not a value recomputed in the browser from wins ÷ total.
      expect(Math.abs(Number(shown) - Number(metrics.win_rate))).toBeLessThanOrEqual(0.005);
    }

    await expectNoFloatArtefacts(page);
  });

  test("an undefined profit factor says so instead of showing zero", async ({
    authedPage: page,
  }) => {
    const result = await page.evaluate(async (apiUrl) => {
      const response = await fetch(`${apiUrl}/api/v1/analytics/dashboard?days=3650`, {
        credentials: "include",
      });
      return response.json();
    }, API_URL);

    const profitFactor = result?.data?.metrics?.profit_factor;
    test.skip(profitFactor !== null, "this workspace has losing trades, so the ratio is defined");

    await page.getByLabel("Time window").selectOption("3650");
    const body = await page.locator("body").innerText();
    expect(body).toContain("Undefined");
    expect(body).not.toContain("0.00");
  });

  test("changing the window refetches rather than filtering in the browser", async ({
    authedPage: page,
  }) => {
    const requests: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/analytics/dashboard")) requests.push(request.url());
    });

    await page.getByLabel("Time window").selectOption("30");
    await expect.poll(() => requests.some((url) => url.includes("days=30"))).toBe(true);
  });

  test("the analytics page renders its breakdowns", async ({ authedPage: page }) => {
    await page.goto("/analytics");
    await expect(page.getByRole("heading", { name: /analytics/i, level: 1 })).toBeVisible();
    await expectNoFloatArtefacts(page);
  });
});
