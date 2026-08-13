import { API_URL, expect, test } from "../support/fixtures";

/**
 * Replay's defining promise is that you cannot see the future.
 *
 * That promise is kept structurally — the server sends only the bars up to the cursor — so the
 * test asserts it the only way that means anything: by inspecting what the browser actually
 * received, not what it chose to draw.
 */
test.describe("market replay", () => {
  test("the browser is never sent a bar beyond the cursor", async ({ authedPage: page }) => {
    const session = await createSession(page);
    test.skip(!session, "no instrument with enough candles in the seeded data");

    await page.goto(`/replay/${session.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(session.name);

    const initial = await fetchState(page, session.id);
    expect(initial.visible_candles.length).toBe(initial.cursor_index + 1);

    // The last candle the client holds is the current bar — never one after it.
    const lastVisible = initial.visible_candles.at(-1);
    expect(lastVisible.time).toBe(initial.current_bar.time);

    await page.getByRole("button", { name: "Next bar" }).click();

    await expect
      .poll(async () => (await fetchState(page, session.id)).cursor_index)
      .toBe(initial.cursor_index + 1);

    const advanced = await fetchState(page, session.id);
    expect(advanced.visible_candles.length).toBe(advanced.cursor_index + 1);
    expect(advanced.visible_candles.at(-1).time).toBe(advanced.current_bar.time);
    // Exactly one new bar was revealed, not a block of them.
    expect(advanced.visible_candles.length).toBe(initial.visible_candles.length + 1);
  });

  test("the arrow key advances the session", async ({ authedPage: page }) => {
    const session = await createSession(page);
    test.skip(!session, "no instrument with enough candles in the seeded data");

    await page.goto(`/replay/${session.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(session.name);
    const before = (await fetchState(page, session.id)).cursor_index;

    await page.locator("body").press("ArrowRight");
    await expect.poll(async () => (await fetchState(page, session.id)).cursor_index).toBe(before + 1);
  });

  test("a submitted order does not fill on the bar that created it", async ({
    authedPage: page,
  }) => {
    const session = await createSession(page);
    test.skip(!session, "no instrument with enough candles in the seeded data");

    await page.goto(`/replay/${session.id}`);
    await expect(page.getByRole("heading", { level: 1 })).toContainText(session.name);

    await page.locator("#ro-qty").fill("1");
    await page.getByRole("button", { name: /^Submit buy order$/ }).click();

    // Same bar: the order is working, and no position exists yet. This is the engine's
    // next-bar execution rule, which is what stops a backtest buying at a price it already knew.
    await expect
      .poll(async () => (await fetchState(page, session.id)).working_orders.length)
      .toBeGreaterThan(0);
    const sameBar = await fetchState(page, session.id);
    expect(sameBar.position).toBeNull();

    // Advance one bar and it fills.
    await page.getByRole("button", { name: "Next bar" }).click();
    await expect.poll(async () => (await fetchState(page, session.id)).position).not.toBeNull();

    const filled = await fetchState(page, session.id);
    expect(filled.position.direction).toBe("long");
    expect(Number(filled.position.quantity)).toBe(1);
  });
});

async function fetchState(page: import("@playwright/test").Page, id: string) {
  return page.evaluate(
    async ([apiUrl, replayId]) => {
      const response = await fetch(`${apiUrl}/api/v1/replay/${replayId}`, {
        credentials: "include",
      });
      return (await response.json()).data;
    },
    [API_URL, id] as const,
  );
}

/** Create a replay session directly, so the UI tests start from a known cursor. */
async function createSession(page: import("@playwright/test").Page) {
  return page.evaluate(async (apiUrl) => {
    const csrf = document.cookie
      .split("; ")
      .find((entry) => entry.startsWith("tl_csrf="))
      ?.split("=")[1];
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (csrf) headers["X-CSRF-Token"] = decodeURIComponent(csrf);

    const instrumentsResponse = await fetch(`${apiUrl}/api/v1/instruments?page_size=50`, {
      credentials: "include",
    });
    const instruments = (await instrumentsResponse.json())?.data ?? [];

    for (const instrument of instruments) {
      const coverageResponse = await fetch(
        `${apiUrl}/api/v1/market-data/coverage/${instrument.id}`,
        { credentials: "include" },
      );
      const coverage = (await coverageResponse.json())?.data ?? [];
      const daily = coverage.find(
        (row: { timeframe: string; bar_count: number }) =>
          row.timeframe === "1d" && row.bar_count > 80,
      );
      if (!daily) continue;

      const response = await fetch(`${apiUrl}/api/v1/replay`, {
        method: "POST",
        credentials: "include",
        headers,
        body: JSON.stringify({
          name: `E2E ${instrument.symbol}`,
          instrument_id: instrument.id,
          timeframe: "1d",
          start_at: daily.first_bar_at,
          end_at: daily.last_bar_at,
          initial_capital: "100000",
          warmup_bars: 30,
        }),
      });
      if (!response.ok) continue;
      return (await response.json()).data;
    }
    return null;
  }, API_URL);
}
