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
