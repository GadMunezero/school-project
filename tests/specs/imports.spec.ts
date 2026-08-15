import path from "node:path";

import { API_URL, expect, test } from "../support/fixtures";

// Playwright transpiles specs to CJS, so `__dirname` is available and `import.meta` is not.
const SAMPLES = path.resolve(__dirname, "../../data/samples");

/**
 * The import wizard, end to end, on a real broker export.
 *
 * This is the flow where a mistake is most expensive: a mis-parsed file writes wrong trades into
 * the journal, and every downstream figure inherits the error. So the test commits the file and
 * then checks that the trades it produced actually exist — and that reverting removes them.
 */
test.describe("CSV import", () => {
  // Both tests commit into the same workspace and one asserts an exact trade count, so they
  // cannot overlap. Playwright isolates browser contexts, not the database behind them.
  test.describe.configure({ mode: "serial" });

  test("upload → map → validate → commit → revert round-trips cleanly", async ({
    authedPage: page,
  }) => {
    const account = await createAccount(page, "E2E round-trip");
    const tradesBefore = await countTrades(page);

    await page.goto("/imports");
    await page.getByRole("button", { name: "Upload CSV" }).click();

    await page.getByLabel("Import into").selectOption(account);
    await page
      .locator("#i-file")
      .setInputFiles(path.join(SAMPLES, "us-equities-desktop.csv"));
    await page.getByRole("button", { name: "Upload", exact: true }).click();

    // The wizard navigates to the new import, with the server's suggested mapping pre-filled.
    await page.waitForURL(/\/imports\/[0-9a-f-]{36}/);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("us-equities-desktop.csv");

    // The detector should have recognised this layout; every required field must be mapped.
    for (const field of ["timestamp", "symbol", "side", "quantity", "price"]) {
      await expect(page.locator(`#map-${field}`)).not.toHaveValue("");
    }

    await page.getByRole("button", { name: "Validate rows" }).click();

    // Step two: the tallies must add up to the file's row count.
    await expect(page.getByText("Rows in file")).toBeVisible();
    const importId = page.url().split("/").pop()!;
    const record = await fetchImport(page, importId);
    expect(record.valid_rows + record.invalid_rows + record.duplicate_rows).toBe(record.total_rows);
    expect(record.valid_rows).toBeGreaterThan(0);

    await page.getByRole("button", { name: /^Import \d+ rows$/ }).click();
    await expect(page.getByText("Import complete")).toBeVisible();

    // The committed rows must have become real trades, not just a success message.
    const committed = await fetchImport(page, importId);
    expect(committed.created_order_count).toBeGreaterThan(0);
    const tradesAfter = await countTrades(page);
    expect(tradesAfter).toBeGreaterThan(tradesBefore);

    // And reverting must put the journal back exactly where it started.
    await page.getByRole("button", { name: "Revert import" }).first().click();
    // Two buttons now carry this label — the header one and the dialog's confirm.
    await page.getByRole("button", { name: "Revert import" }).last().click();
    // Scoped to the heading: the success toast uses the same words.
    await expect(page.getByRole("heading", { name: "Import reverted" })).toBeVisible();

    expect(await countTrades(page)).toBe(tradesBefore);
  });

  test("re-importing the same file skips rows it has already seen", async ({
    authedPage: page,
  }) => {
    // Duplicate detection is scoped to the account — `orders.by_external_ids(account_id, …)` —
    // so both passes must target the same one. Picking by dropdown index left that to the
    // ordering of a shared account list; a dedicated account pins it down and keeps this test
    // independent of the seeded workspace and of the round-trip test above.
    const account = await createAccount(page, "E2E duplicates");

    const upload = async () => {
      await page.goto("/imports");
      await page.getByRole("button", { name: "Upload CSV" }).click();
      await page.getByLabel("Import into").selectOption(account);
      await page.locator("#i-file").setInputFiles(path.join(SAMPLES, "crypto-exchange.csv"));
      await page.getByRole("button", { name: "Upload", exact: true }).click();
      await page.waitForURL(/\/imports\/[0-9a-f-]{36}/);
      await page.getByRole("button", { name: "Validate rows" }).click();
      await expect(page.getByText("Rows in file")).toBeVisible();
      const id = page.url().split("/").pop()!;
      await page.getByRole("button", { name: /^Import \d+ rows$/ }).click();
      await expect(page.getByRole("heading", { name: "Import complete" })).toBeVisible();
      // The heading renders from the mutation response; wait until the server itself reports
      // the import committed before letting the next import depend on it.
      await expect
        .poll(async () => (await fetchImport(page, id)).status)
        .toBe("completed");
      return id;
    };

    await upload();

    // The second pass sees the same execution ids and must not double-count the fills.
    await page.goto("/imports");
    await page.getByRole("button", { name: "Upload CSV" }).click();
    await page.getByLabel("Import into").selectOption(account);
    await page.locator("#i-file").setInputFiles(path.join(SAMPLES, "crypto-exchange.csv"));
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    await page.waitForURL(/\/imports\/[0-9a-f-]{36}/);
    await page.getByRole("button", { name: "Validate rows" }).click();

    // "Already imported" is a tally heading that renders whatever the count is, so its presence
    // proves nothing. Wait for the server to finish validating, then read the counts it produced.
    const secondId = page.url().split("/").pop()!;
    await expect
      .poll(async () => (await fetchImport(page, secondId)).status)
      .toBe("preview");

    const second = await fetchImport(page, secondId);
    // Both passes must have landed on the same account, or "no duplicates" would be vacuous.
    expect(second.account_id).toBe(account);
    expect(second.total_rows).toBeGreaterThan(0);
    // Every row carries an execution id already in the database, so none may be importable.
    expect(second.duplicate_rows).toBe(second.total_rows);
    expect(second.valid_rows).toBe(0);
  });
});

/** Create an account through the API and return its id, so tests never share one. */
async function createAccount(
  page: import("@playwright/test").Page,
  name: string,
): Promise<string> {
  return page.evaluate(
    async ([apiUrl, accountName]) => {
      const csrf = document.cookie
        .split("; ")
        .find((entry) => entry.startsWith("tl_csrf="))
        ?.split("=")[1];
      const response = await fetch(`${apiUrl}/api/v1/accounts`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrf ? { "X-CSRF-Token": decodeURIComponent(csrf) } : {}),
        },
        // Unique per run, so a repeat run never collides with an earlier account.
        body: JSON.stringify({
          name: `${accountName} ${Date.now()}`,
          account_type: "paper",
          currency: "USD",
          initial_balance: "100000",
        }),
      });
      if (!response.ok) throw new Error(`could not create account: ${response.status}`);
      return (await response.json()).data.id as string;
    },
    [API_URL, name] as const,
  );
}

async function countTrades(page: import("@playwright/test").Page): Promise<number> {
  return page.evaluate(async (apiUrl) => {
    const response = await fetch(`${apiUrl}/api/v1/trades?page_size=1`, { credentials: "include" });
    const body = await response.json();
    return body?.meta?.total ?? 0;
  }, API_URL);
}

async function fetchImport(page: import("@playwright/test").Page, importId: string) {
  return page.evaluate(
    async ([apiUrl, id]) => {
      const response = await fetch(`${apiUrl}/api/v1/imports/${id}`, { credentials: "include" });
      const body = await response.json();
      return body.data;
    },
    [API_URL, importId] as const,
  );
}
