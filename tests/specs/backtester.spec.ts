import { API_URL, apiSend, expect, expectNoFloatArtefacts, test } from "../support/fixtures";

/**
 * A backtest is a claim about what a strategy would have done.
 *
 * These tests care about whether the claim holds together: that the results page renders the run
 * the engine actually produced rather than recomputing anything in the browser, that submitting a
 * run does not block on the simulation, and that one workspace cannot read another's results.
 *
 * There is no Celery worker in this harness, which is deliberate: submitting here exercises the
 * real queueing path, exactly as it behaves when a worker is busy or a broker is down. The
 * completed run the results tests read is seeded by `BacktestService.execute` — the same call the
 * worker makes — so those numbers are the engine's own.
 */

interface Run {
  id: string;
  status: string;
  trade_count: number;
  metrics: Record<string, string | number | null>;
}

/**
 * The backtest that has a completed run behind it.
 *
 * Found by looking for the completed run rather than by taking the first row: other tests in this
 * file create backtests of their own, and an index would quietly start pointing at one of those.
 */
async function seededBacktest(page: import("@playwright/test").Page) {
  return page.evaluate(async (api) => {
    const list = await fetch(`${api}/api/v1/backtests?page_size=50`, { credentials: "include" });
    const rows = (await list.json()).data as { id: string; name: string }[];

    for (const row of rows) {
      const response = await fetch(`${api}/api/v1/backtests/${row.id}/runs`, {
        credentials: "include",
      });
      const runs = (await response.json()).data as { id: string; status: string }[];
      const completed = runs.find((run) => run.status === "completed");
      if (completed) return { ...row, runId: completed.id };
    }
    return null;
  }, API_URL);
}

/** The full result for a run: metrics, breakdowns, trades. */
async function resultFor(page: import("@playwright/test").Page, runId: string) {
  return page.evaluate(
    async ([api, id]) => {
      const response = await fetch(`${api}/api/v1/backtests/runs/${id}`, {
        credentials: "include",
      });
      return (await response.json()).data;
    },
    [API_URL, runId] as const,
  );
}

async function runsFor(page: import("@playwright/test").Page, backtestId: string): Promise<Run[]> {
  return page.evaluate(
    async ([api, id]) => {
      const response = await fetch(`${api}/api/v1/backtests/${id}/runs`, {
        credentials: "include",
      });
      return (await response.json()).data;
    },
    [API_URL, backtestId] as const,
  );
}

test.describe("backtester", () => {
  test("the list shows the workspace's backtests", async ({ authedPage: page }) => {
    await page.goto("/backtester");
    await expect(page.getByRole("heading", { name: "Backtester", level: 1 })).toBeVisible();

    const backtest = await seededBacktest(page);
    expect(backtest, "the demo workspace should have a seeded backtest").not.toBeNull();
    await expect(page.getByText(backtest!.name).first()).toBeVisible();
  });

  test("the results page shows the figures the engine returned", async ({ authedPage: page }) => {
    const backtest = await seededBacktest(page);
    test.skip(backtest === null, "no seeded backtest");

    const result = await resultFor(page, backtest!.runId);
    expect(Number(result.metrics.total_trades), "seeded run produced no trades").toBeGreaterThan(0);

    await page.goto(`/backtester/${backtest!.id}`);
    await expect(page.getByRole("heading", { name: backtest!.name, level: 1 })).toBeVisible({
      timeout: 15_000,
    });

    // Read the metric tiles back and compare them against the run the API returned. The point is
    // not that numbers rendered — it is that these numbers are the engine's, not something the
    // browser recomputed from a truncated trade list. The card renders its label and value as
    // adjacent paragraphs, so the value is the label's next sibling.
    const tile = (label: string) =>
      page.getByText(label, { exact: true }).locator("xpath=following-sibling::p[1]");

    await expect(tile("Trades")).toHaveText(String(result.metrics.total_trades));

    // The trade table is the evidence for that count, so the two must agree.
    expect(result.trades.length).toBe(Number(result.metrics.total_trades));

    // A raw decimal tail here would mean a value bypassed the formatters on its way to the DOM.
    await expectNoFloatArtefacts(page);
  });

  test("submitting a run returns without waiting for the simulation", async ({
    authedPage: page,
  }) => {
    const backtest = await seededBacktest(page);
    test.skip(backtest === null, "no seeded backtest");

    await page.goto(`/backtester/${backtest!.id}`);

    const submitted = page.waitForResponse(
      (response) =>
        response.url().includes(`/backtests/${backtest!.id}/run`) &&
        response.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Run backtest" }).first().click();
    const response = await submitted;

    // 202, not 200: the work is accepted, not done. This is the guarantee that a twenty-minute
    // simulation can never hold an HTTP request open.
    expect(response.status()).toBe(202);
    const body = (await response.json()).data;
    expect(body.queued).toBe(true);
    expect(body.run_id).toBeTruthy();

    // With no worker attached the run stays queued rather than quietly disappearing or being
    // recorded as a completed run that did nothing.
    await expect
      .poll(async () => (await runsFor(page, backtest!.id)).some((r) => r.id === body.run_id))
      .toBe(true);
    const queued = (await runsFor(page, backtest!.id)).find((r) => r.id === body.run_id);
    expect(queued!.status).toBe("queued");
  });

  test("a backtest keeps the settings it was configured with", async ({ authedPage: page }) => {
    await page.goto("/backtester");
    await page.getByRole("button", { name: "New backtest" }).click();
    await page.locator("#bt-name").fill("E2E configured run");
    await page.locator("#bt-strategy").selectOption({ index: 1 });
    await page.locator("#bt-instrument").selectOption({ index: 1 });
    await page.locator("#bt-timeframe").selectOption("1d");

    // Dates come from the form's own coverage hint, so the run lands on candles that exist — and
    // the hint has to be accurate for this to work at all.
    const hint = /Data (from|to) (\d{4}-\d{2}-\d{2})/;
    await expect(page.getByText(hint).first()).toBeVisible();
    const dates = (await page.getByText(hint).allTextContents())
      .map((text) => hint.exec(text)?.[2])
      .filter(Boolean) as string[];
    expect(dates.length, "the form did not report a candle range").toBeGreaterThanOrEqual(2);

    await page.locator("#bt-start").fill(dates[0]);
    await page.locator("#bt-end").fill(dates[1]);

    const created = page.waitForResponse(
      (response) =>
        response.url().endsWith("/api/v1/backtests") &&
        response.request().method() === "POST" &&
        response.status() === 201,
    );
    await page.getByRole("button", { name: "Create", exact: true }).click();
    const stored = (await (await created).json()).data;

    // Stored as chosen, not as the server preferred: a run whose settings drift is not reproducible.
    expect(stored.name).toBe("E2E configured run");
    expect(stored.timeframe).toBe("1d");
    expect(stored.start_date).toBe(dates[0]);
    expect(stored.end_date).toBe(dates[1]);
  });

  test("a range with no candles is refused rather than run over nothing", async ({
    authedPage: page,
  }) => {
    await page.goto("/backtester");
    await page.getByRole("button", { name: "New backtest" }).click();
    await page.locator("#bt-name").fill("E2E empty range");
    await page.locator("#bt-strategy").selectOption({ index: 1 });
    await page.locator("#bt-instrument").selectOption({ index: 1 });
    await page.locator("#bt-start").fill("1990-01-01");
    await page.locator("#bt-end").fill("1990-06-30");

    const response = page.waitForResponse(
      (r) => r.url().endsWith("/api/v1/backtests") && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Create", exact: true }).click();
    const created = await response;

    if (created.status() !== 201) {
      // Refused at creation, which is the earliest honest place to refuse.
      expect(created.status()).toBeGreaterThanOrEqual(400);
      return;
    }

    // Otherwise it must refuse at submission — a run over no candles must never reach "completed"
    // with a 0% return, which reads as "this strategy broke even" rather than "there was no data".
    const id = (await created.json()).data.id;
    // A valid CSRF token, so the refusal is the endpoint's own and not the CSRF guard's.
    const attempt = await apiSend(page, `/api/v1/backtests/${id}/run`);
    expect(attempt.status).toBeGreaterThanOrEqual(400);
    expect(attempt.status).not.toBe(403);
  });

  test("another workspace's backtest is a 404, not someone else's results", async ({
    authedPage: page,
  }) => {
    const backtest = await seededBacktest(page);
    test.skip(backtest === null, "no seeded backtest");

    // The admin account holds its own workspace and has no business seeing this run.
    const status = await page.evaluate(
      async ([api, id]) => {
        await fetch(`${api}/api/v1/auth/logout`, { method: "POST", credentials: "include" });
        const login = await fetch(`${api}/api/v1/auth/login`, {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email: "admin@example.com", password: "AdminOps!2024" }),
        });
        if (!login.ok) return `login failed: ${login.status}`;
        const response = await fetch(`${api}/api/v1/backtests/${id}`, { credentials: "include" });
        return response.status;
      },
      [API_URL, backtest!.id] as const,
    );

    // 404 rather than 403: a workspace must not learn that someone else's record exists. Platform
    // admin is not workspace access — holding the staff role must not open another tenant's data.
    expect(status).toBe(404);
  });
});
