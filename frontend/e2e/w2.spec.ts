import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

test("search Kante (no accent) opens Kanté's page by id", async ({ page }) => {
  await page.goto("/");
  const box = page.getByRole("combobox");
  await box.fill("Kante");
  const option = page.getByRole("option").filter({ hasText: "N'Golo Kanté" });
  await expect(option).toBeVisible();
  await box.press("ArrowDown");
  await option.click();
  await expect(page).toHaveURL(/#\/player\/3961-22-2-27$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("N'Golo Kanté");
});

test("the nickname finds Isco and the URL carries ids only", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("combobox").fill("isco");
  await page.getByRole("combobox").press("Enter");
  await expect(page).toHaveURL(/#\/player\/4926-220-11-27$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Isco");
});

test("keyboard only: type, arrow, Enter", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.type("busq");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/#\/player\/5203-217-11-27$/);
});

test("the minutes on the page trace to the contract", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const el = page.locator('[data-src="players/3961-22-2-27.json#identity.minutes_text"]');
  const doc = await (await page.request.get("/data/players/3961-22-2-27.json")).json();
  await expect(el).toHaveText(doc.identity.minutes_text);
});

test("theme toggle: dark wins over a light system, and back", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto("/");
  await page.getByRole("button", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  expect(bg).toBe("rgb(20, 20, 19)");
  await page.getByRole("button", { name: "System" }).click();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);
});

test("names never key a URL", async ({ page }) => {
  await page.goto("/#/player/kante");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Page not found");
});

for (const path of ["/", "/#/player/3961-22-2-27"]) {
  for (const scheme of ["light", "dark"] as const) {
    test(`axe: no serious or critical violations on ${path} (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto(path);
      await page.waitForLoadState("networkidle");
      const r = await new AxeBuilder({ page }).analyze();
      const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(bad.map((v) => `${v.id}: ${v.nodes.length}`)).toEqual([]);
    });
  }
}


test("axe with the search list open, and the empty states as status text", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("combobox").fill("kan");
  await expect(page.getByRole("listbox")).toBeVisible();
  let r = await new AxeBuilder({ page }).analyze();
  expect(r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
  await page.getByRole("combobox").fill("zzzz");
  await expect(page.getByRole("status")).toHaveText("No player found");
  r = await new AxeBuilder({ page }).analyze();
  expect(r.violations.filter((v) => v.impact === "serious" || v.impact === "critical").map((v) => v.id)).toEqual([]);
});

test("route change focuses the page heading", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("combobox").fill("busq");
  await page.getByRole("combobox").press("Enter");
  await expect(page.getByRole("heading", { level: 1 })).toBeFocused();
});
