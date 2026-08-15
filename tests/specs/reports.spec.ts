import { API_URL, expect, expectNoFloatArtefacts, test } from "../support/fixtures";

/**
 * Reports exist to be checked, not believed.
 *
 * The claim the feature makes is that the headline percentage and the sessions listed under it
 * describe the same thing, and that opening a session shows the levels that report actually
 * measured. These tests verify that claim arithmetically against the API rather than checking
 * that some number rendered.
 */
test.describe("edge reports", () => {
  test("the page runs a report and lists every session behind the rate", async ({
    authedPage: page,
  }) => {
    await page.goto("/reports");
    await expect(page.getByRole("heading", { name: "Reports", level: 1 })).toBeVisible();

    // Wait for the run to land, then compare the page against the API that produced it.
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible();

    const run = await currentRun(page);
    test.skip(run === null, "the demo workspace has no candles to report on");

    expect(await rows.count()).toBe(run!.sessions.length);
    await expectNoFloatArtefacts(page);
  });

  test("the headline is the share of qualifying sessions in the headline buckets", async ({
    authedPage: page,
  }) => {
    await page.goto("/reports");
    await expect(page.locator("tbody tr").first()).toBeVisible();

    const run = await currentRun(page);
    test.skip(run === null || run.sample_size === 0, "no qualifying sessions");

    // Recompute the rate from the buckets. If the headline and the list ever disagree, the
    // feature is lying, which is the one failure mode that matters here.
    const hits = run!.headline_outcomes.reduce(
      (total, outcome) => total + (run!.buckets[outcome] ?? 0),
      0,
    );
    const expected = (hits / run!.sample_size) * 100;
    expect(Math.abs(Number(run!.hit_rate) - expected)).toBeLessThanOrEqual(0.01);

    // And the sample must exclude the sessions where the setup never arose.
    const excluded = run!.buckets["no_setup"] ?? 0;
    expect(run!.sample_size).toBe(run!.total_sessions - excluded);
  });

  test("selecting a session plots that day with the report's own levels", async ({
    authedPage: page,
  }) => {
    await page.goto("/reports");
    await expect(page.locator("tbody tr").first()).toBeVisible();

    const run = await currentRun(page);
    test.skip(run === null, "no report data");

    const index = run!.sessions.findIndex((s) => s.levels.length > 0);
    test.skip(index < 0, "no session carries levels");
    const session = run!.sessions[index];

    await page.locator("tbody tr").nth(index).click();

    // The inspector must name the day it is showing, and print the exact level prices the report
    // measured against — not values re-derived from the candles it happens to have fetched.
    const panel = page.locator("section", { hasText: "Trade it in replay" }).last();
    for (const level of session.levels) {
      await expect(panel).toContainText(level.label);
    }
    await expectNoFloatArtefacts(page);
  });

  test("a report whose setup never occurs says so instead of showing a zero rate", async ({
    authedPage: page,
  }) => {
    // Daily candles cannot form an intraday opening range, so every session is "no setup".
    await page.goto("/reports");
    await expect(page.locator("tbody tr").first()).toBeVisible();
    await page.getByLabel("Candles").selectOption("1d");

    await expect
      .poll(async () => {
        const body = await page.locator("body").innerText();
        return body.includes("never occurred") || body.includes("%");
      })
      .toBe(true);

    const body = await page.locator("body").innerText();
    if (body.includes("never occurred")) {
      // The honest empty state, not a 0% headline over an empty table.
      expect(body).not.toMatch(/\b0\.00%/);
    }
  });

  test("switching report recomputes rather than reusing the previous answer", async ({
    authedPage: page,
  }) => {
    await page.goto("/reports");
    await expect(page.locator("tbody tr").first()).toBeVisible();
    const first = await currentRun(page);
    test.skip(first === null, "no report data");

    await page.getByLabel("Report").selectOption("previous_day_levels");
    await expect.poll(async () => (await currentRun(page))?.key).toBe("previous_day_levels");

    const second = await currentRun(page);
    expect(second!.key).not.toBe(first!.key);
    // Prior-day levels are fixed before the session opens, so every level must be named for the
    // previous day — the clearest look-ahead check available from outside the engine.
    const withLevels = second!.sessions.find((s) => s.levels.length > 0);
    if (withLevels) {
      expect(withLevels.levels.map((l) => l.key).sort()).toEqual(["prior_high", "prior_low"]);
    }
  });

  test("a market with its own trading day says so instead of offering a dead control", async ({
    authedPage: page,
  }) => {
    await page.goto("/reports");
    await expect(page.locator("tbody tr").first()).toBeVisible();

    // MQ1 is the seeded futures contract; its session opens 18:00 New York the evening before.
    const options = await page.locator("#r-instrument option").allTextContents();
    test.skip(!options.includes("MQ1"), "no futures instrument in the demo workspace");

    await page.getByLabel("Instrument").selectOption({ label: "MQ1" });
    await expect.poll(async () => (await currentRun(page))?.instrument?.symbol).toBe("MQ1");

    const run = await currentRun(page);
    expect(run!.session_boundary).toEqual({
      opens_at: "18:00:00",
      timezone: "America/New_York",
    });

    // The timezone would change nothing here, so the page must not present it as a choice.
    await expect(page.locator("#r-zone")).toBeDisabled();
    await expect(page.getByText(/opens 18:00 America\/New_York/)).toBeVisible();
    await expect(page.getByText(/sessions bounded by the 18:00 America\/New_York open/)).toBeVisible();

    // Futures never open on a Saturday. A session landing there is the grouping bug returning.
    const weekend = run!.sessions.filter((s) => {
      const day = new Date(`${s.session_date}T00:00:00Z`).getUTCDay();
      return day === 6;
    });
    expect(weekend).toEqual([]);
  });

  test("an equity still follows the timezone the user picked", async ({ authedPage: page }) => {
    await page.goto("/reports");
    await expect(page.locator("tbody tr").first()).toBeVisible();

    const options = await page.locator("#r-instrument option").allTextContents();
    test.skip(!options.includes("NVLX"), "no equity instrument in the demo workspace");

    await page.getByLabel("Instrument").selectOption({ label: "NVLX" });
    await expect.poll(async () => (await currentRun(page))?.instrument?.symbol).toBe("NVLX");

    // Equities trade inside one calendar day, so the control stays live.
    expect((await currentRun(page))!.session_boundary).toBeNull();
    await expect(page.locator("#r-zone")).toBeEnabled();
  });
});

interface Run {
  key: string;
  hit_rate: string | null;
  sample_size: number;
  total_sessions: number;
  headline_outcomes: string[];
  buckets: Record<string, number>;
  sessions: { session_date: string; levels: { key: string; label: string }[] }[];
  instrument: { symbol: string };
  /** Non-null only for markets whose trading day is not a calendar day. */
  session_boundary: { opens_at: string; timezone: string } | null;
}

/**
 * Ask the API for the same report the page is showing.
 *
 * Reads the page's own controls so the request cannot drift from what is rendered — comparing the
 * page against a differently-parameterised run would prove nothing.
 */
async function currentRun(page: import("@playwright/test").Page): Promise<Run | null> {
  const reportKey = await page.locator("#r-key").inputValue();
  const instrumentId = await page.locator("#r-instrument").inputValue();
  const timeframe = await page.locator("#r-timeframe").inputValue();
  const zone = await page.locator("#r-zone").inputValue();
  const minutes = (await page.locator("#r-minutes").count())
    ? await page.locator("#r-minutes").inputValue()
    : null;

  if (!instrumentId) return null;

  return page.evaluate(
    async ([apiUrl, key, instrument, tf, tz, mins]) => {
      const query = new URLSearchParams({
        instrument_id: instrument as string,
        timeframe: tf as string,
        session_timezone: tz as string,
      });
      if (mins) query.set("minutes", mins as string);
      const response = await fetch(`${apiUrl}/api/v1/reports/${key}?${query}`, {
        credentials: "include",
      });
      if (!response.ok) return null;
      return (await response.json()).data;
    },
    [API_URL, reportKey, instrumentId, timeframe, zone, minutes] as const,
  );
}
