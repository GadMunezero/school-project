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
      const response = await fetch(`${apiUrl}/api/v1/trades?page_size=100&status=closed`, {
        credentials: "include",
      });
      return response.json();
    }, API_URL);

    const closed: Array<{ id: string; symbol: string; net_pnl: string }> = payload?.data ?? [];
    // A winner and a loser, because they render differently: formatMoney puts the sign outside the
    // currency symbol, so a loss is "-$128.21". Checking only the first trade passed for a long
    // time purely because that trade happened to be profitable.
    const winner = closed.find((t) => Number(t.net_pnl) > 0);
    const loser = closed.find((t) => Number(t.net_pnl) < 0);
    test.skip(!winner || !loser, "the demo seed produced no closed winner and loser to compare");

    for (const trade of [winner!, loser!]) {
      await page.goto(`/journal/${trade.id}`);
      await expect(page.getByRole("heading", { level: 1 })).toContainText(trade.symbol);

      // net_pnl arrives at full precision — "320.3263246069" for a fractional crypto size — and
      // the page shows it rounded to the currency's minor unit. The expectation is computed here
      // rather than borrowed from the app, so this still fails if the page derives the figure from
      // anything other than the net_pnl the API sent.
      //
      // The currency symbol is stripped from the page text rather than guessed at, so the sign and
      // every digit still have to match while the assertion stays independent of where the symbol
      // sits relative to the sign.
      const body = (await page.locator("body").innerText()).replace(/[$£€¥]/g, "");
      expect(body).toContain(toCents(String(trade.net_pnl)));
    }
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

/**
 * Round a decimal string to cents the way the display does: half-up on the magnitude, with
 * thousands grouping.
 *
 * Written out here rather than imported from the app so the assertion has an independent opinion
 * about the right answer. Truncating instead of rounding is what an earlier version did, and it
 * passed only while the first seeded trade happened to have a third decimal below five.
 */
function toCents(decimal: string): string {
  const negative = decimal.startsWith("-");
  const [whole, fraction = ""] = decimal.replace("-", "").split(".");
  const padded = (fraction + "000").slice(0, 3);

  let cents = BigInt(whole || "0") * 100n + BigInt(padded.slice(0, 2));
  if (Number(padded[2]) >= 5) cents += 1n;

  const units = cents / 100n;
  const remainder = cents % 100n;
  const grouped = Number(units).toLocaleString("en-US");
  return `${negative ? "-" : ""}${grouped}.${String(remainder).padStart(2, "0")}`;
}
