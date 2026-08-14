import { API_URL, expect, test } from "../support/fixtures";

/**
 * The importer is the bridge between the product and reality.
 *
 * Everything downstream — reports, backtests, replay — is only as truthful as the candles
 * underneath it, so these tests are about refusal and honesty rather than the happy path: a
 * malformed row must be reported, not repaired, and a re-import must not claim to have stored
 * candles that were already there.
 */

/** Four clean hourly candles, plus one row whose high is below its low. */
const CSV = [
  "Date,Open,High,Low,Close,Volume",
  "2024-03-01 14:00:00,100.5,101.25,100.0,101.0,1500",
  "2024-03-01 15:00:00,101.0,102.0,100.75,101.5,1800",
  "2024-03-01 16:00:00,101.5,101.75,100.25,100.5,1200",
  "2024-03-01 17:00:00,100.5,103.0,100.5,102.75,2100",
  "2024-03-01 18:00:00,102.75,99.0,101.0,100.0,900",
].join("\n");

async function chooseFile(page: import("@playwright/test").Page, body: string) {
  await page.setInputFiles("#md-file", {
    name: "candles.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(body),
  });
}

test.describe("market data import", () => {
  test("coverage shows which series a workspace actually holds", async ({ authedPage: page }) => {
    await page.goto("/market-data");
    await expect(page.getByRole("heading", { name: "Market data", level: 1 })).toBeVisible();

    // Every instrument gets a card; the seeded ones have candles behind them.
    const instruments = await page.evaluate(async (api) => {
      const response = await fetch(`${api}/api/v1/instruments?page_size=200`, {
        credentials: "include",
      });
      return (await response.json()).data as { id: string; symbol: string }[];
    }, API_URL);

    expect(instruments.length).toBeGreaterThan(0);
    await expect(page.getByText(instruments[0].symbol, { exact: true }).first()).toBeVisible();
  });

  test("the suggested mapping comes from the file's own headers", async ({ authedPage: page }) => {
    await page.goto("/market-data");
    await page.getByRole("button", { name: "Import candles" }).click();
    await chooseFile(page, CSV);

    // "Date" maps to timestamp by synonym; the rest match their own names.
    await expect(page.locator("#md-map-timestamp")).toHaveValue("Date");
    await expect(page.locator("#md-map-open")).toHaveValue("Open");
    await expect(page.locator("#md-map-close")).toHaveValue("Close");
    await expect(page.locator("#md-map-volume")).toHaveValue("Volume");
  });

  test("a preview refuses the impossible candle instead of repairing it", async ({
    authedPage: page,
  }) => {
    await page.goto("/market-data");
    await page.getByRole("button", { name: "Import candles" }).click();
    await chooseFile(page, CSV);

    await page.locator("#md-instrument").selectOption({ index: 1 });
    await page.locator("#md-timeframe").selectOption("1h");
    await page.getByRole("button", { name: "Preview" }).click();

    // Five rows in, four candles out: the bar whose high sits below its low cannot exist.
    await expect(page.getByText("4 of 5 rows parsed")).toBeVisible();
    await expect(page.getByText("1 rejected")).toBeVisible();
    await expect(page.getByText(/Row 5:/)).toBeVisible();
  });

  test("nothing is written until the import is committed", async ({ authedPage: page }) => {
    await page.goto("/market-data");
    await page.getByRole("button", { name: "Import candles" }).click();
    await chooseFile(page, CSV);

    const instrumentId = await page.locator("#md-instrument option").nth(1).getAttribute("value");
    await page.locator("#md-instrument").selectOption({ index: 1 });
    // A timeframe the seed never generates, so the count below is this test's alone.
    await page.locator("#md-timeframe").selectOption("5m");

    const count = async () =>
      page.evaluate(
        async ([api, id]) => {
          const response = await fetch(`${api}/api/v1/market-data/coverage/${id}`, {
            credentials: "include",
          });
          const rows = (await response.json()).data as { timeframe: string; bar_count: number }[];
          return rows.filter((row) => row.timeframe === "5m").reduce((n, r) => n + r.bar_count, 0);
        },
        [API_URL, instrumentId] as const,
      );

    const before = await count();
    await page.getByRole("button", { name: "Preview" }).click();
    await expect(page.getByText("4 of 5 rows parsed")).toBeVisible();

    // The dry run parsed the whole file and stored none of it.
    expect(await count()).toBe(before);

    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByText(/Stored 4 candles/)).toBeVisible();
    expect(await count()).toBe(before + 4);
  });

  test("re-importing the same file says nothing was new", async ({ authedPage: page }) => {
    const importOnce = async () => {
      await page.goto("/market-data");
      await page.getByRole("button", { name: "Import candles" }).click();
      await chooseFile(page, CSV);
      await page.locator("#md-instrument").selectOption({ index: 1 });
      await page.locator("#md-timeframe").selectOption("30m");
      await page.getByRole("button", { name: "Preview" }).click();
      await expect(page.getByText("4 of 5 rows parsed")).toBeVisible();
      await page.getByRole("button", { name: "Import", exact: true }).click();
    };

    await importOnce();
    await expect(page.getByText(/Stored 4 candles/)).toBeVisible();

    await importOnce();
    // Claiming four more would tell the user their duplicate upload did something.
    await expect(page.getByText(/Nothing new to store/)).toBeVisible();
  });
});
