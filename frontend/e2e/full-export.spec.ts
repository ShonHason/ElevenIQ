// Smoke test on the FULL export (export/v1), run only when it exists (playwright.config.ts).
import { expect, test } from "@playwright/test";

test("full export: search Emre Can and open his page", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("combobox").fill("Emre Can");
  await expect(page.getByRole("option").filter({ hasText: "Emre Can" })).toBeVisible();
  await page.getByRole("combobox").press("Enter");
  await expect(page).toHaveURL(/#\/player\/3493-24-2-27$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Emre Can");
  await expect(page.getByText("Liverpool").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Profile shape" })).toBeVisible();
});

test("full export: the direct URL works", async ({ page }) => {
  await page.goto("/#/player/3493-24-2-27");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Emre Can");
});

test("full export: Gem Finder pages 50 rows at a time, in the exporter's order", async ({ page }) => {
  await page.goto("/#/gems/ST");
  const gems = await (await page.request.get("/data/gems/ST.json")).json();
  const rows = page.locator('table[data-table="gems"] tbody tr[data-key]');
  await expect(rows).toHaveCount(50);
  await page.locator("[data-more]").click();
  await expect(rows).toHaveCount(100);
  const first = (gems.families as { family: string; definition: string | null }[]).find((f) => f.definition)!.family;
  const want = (gems.order[first] as number[]).slice(0, 100).map((i) => gems.rows[i].key);
  expect(await rows.evaluateAll((els) => els.map((e) => e.getAttribute("data-key")))).toEqual(want);
});
