import { API_URL, expect, test } from "../support/fixtures";

/**
 * The calendar's one job is that the number on a cell and the trades behind it are the same set.
 *
 * The drill-down is fetched from the server by trading day rather than filtered in the browser by
 * calendar date, because a futures session opens at 18:00 the previous evening. A client-side
 * filter would list a different set of trades than the cell was built from, and the two views
 * would contradict each other with no error anywhere.
 */
test.describe("daily P&L calendar", () => {
  test("renders a month grid and opens on a month that has trades", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();

    // Weekday headers are what makes it a calendar rather than a strip of squares.
    for (const label of ["Mon", "Wed", "Sun"]) {
      await expect(page.getByText(label, { exact: true }).first()).toBeVisible();
    }
    await expect(page.getByRole("button", { name: "Previous month" })).toBeVisible();
  });

  test("a day cell opens the trades it was built from, and they sum to it", async ({
    authedPage: page,
  }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();

    // Ask the API what the cells should be, using the browser's own session.
    const calendar = await page.evaluate(async (apiUrl) => {
      const response = await fetch(`${apiUrl}/api/v1/analytics/dashboard?days=3650`, {
        credentials: "include",
      });
      const body = await response.json();
      return body?.data?.breakdowns?.calendar ?? [];
    }, API_URL);

    test.skip(calendar.length === 0, "the demo seed produced no trading days");

    // The component opens on the month of the most recent day with trades, so this cell is on
    // screen without navigating.
    const cell = calendar[calendar.length - 1];

    const detail = await page.evaluate(
      async ({ apiUrl, date }) => {
        const response = await fetch(
          `${apiUrl}/api/v1/analytics/calendar/${date}?days=3650`,
          { credentials: "include" },
        );
        return response.json();
      },
      { apiUrl: API_URL, date: cell.date },
    );

    // The server's own two views of the same day have to agree before the UI is even involved.
    expect(detail.data.summary.net_pnl).toBe(cell.net_pnl);
    expect(detail.data.summary.trades).toBe(cell.trades);

    const summed = detail.data.trades.reduce(
      (total: number, trade: { net_pnl: string }) => total + Number(trade.net_pnl),
      0,
    );
    expect(Math.abs(summed - Number(cell.net_pnl))).toBeLessThan(0.01);

    // Now the UI: the cell for that date opens a drawer listing those trades.
    await page.getByRole("button", { name: new RegExp(`^${cell.date}:`) }).click();

    const drawer = page.getByRole("dialog");
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText(cell.date);

    // Every symbol the API attributed to the day is listed.
    const symbols: string[] = [
      ...new Set(detail.data.trades.map((t: { symbol: string }) => t.symbol)),
    ];
    for (const symbol of symbols) {
      await expect(drawer.getByText(symbol, { exact: true }).first()).toBeVisible();
    }
  });

  test("a day with no trades cannot be opened", async ({ authedPage: page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();

    // Cells with no trading are disabled rather than opening an empty drawer.
    const blank = page.getByRole("button", { name: /: no trades$/ }).first();
    const count = await page.getByRole("button", { name: /: no trades$/ }).count();
    test.skip(count === 0, "every day in this month has trades");

    await expect(blank).toBeDisabled();
  });
});
