import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const fx = async (page: Page, path: string) => (await page.request.get(`/data/${path}`)).json();

test("Profile shape: vertices trace to the fixture; withheld families are dashed and not at 0", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const doc = await fx(page, "players/3961-22-2-27.json");
  const dims = doc.dna.dimensions as { family: string; score: number | null; status: string }[];
  for (const d of dims.filter((x) => x.score !== null)) {
    const v = page.locator(`[data-src="players/3961-22-2-27.json#dna.dimensions.${d.family}.score"]`);
    await expect(v).toHaveAttribute("data-value", String(d.score));
  }
  const withheld = dims.filter((x) => x.score === null).map((x) => x.family).sort();
  expect(withheld).toEqual(["Defending", "Passing"]);
  for (const f of withheld) {
    const m = page.locator(`[data-withheld="${f}"]`);
    await expect(m).toHaveAttribute("stroke-dasharray", "2 2");
    // on the rim (radius 120 of the 0–100 scale), never at the centre (0)
    const cx = Number(await m.getAttribute("cx")), cy = Number(await m.getAttribute("cy"));
    expect(Math.hypot(cx - 390, cy - 390)).toBeCloseTo(180, 0);   // centre and rim of the 780 canvas
  }
  await expect(page.getByText("Profile shape", { exact: true })).toBeVisible();
});

test("no overall rating number on the page", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const text = (await page.locator("main").innerText()).toLowerCase();
  for (const word of ["overall", "rating", "total score"]) expect(text).not.toContain(word);
});

test("metric cells trace to the fixture", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const doc = await fx(page, "players/3961-22-2-27.json");
  const cells = page.locator('[data-src^="players/3961-22-2-27.json#metrics."]');
  await expect(cells.first()).toBeVisible();
  const n = await cells.count();
  expect(n).toBeGreaterThan(40);
  for (let i = 0; i < n; i++) {
    const el = cells.nth(i);
    const path = (await el.getAttribute("data-src"))!.split("#")[1].split(".");
    let v: unknown = doc;
    for (const p of path) v = (v as Record<string, unknown>)[p];
    await expect(el).toHaveText(String(v));
  }
});

test("near-gate chip only on the near-gate row", async ({ page }) => {
  await page.goto("/#/player/3052-36-2-27");
  await expect(page.getByText("Fewer minutes: less reliable")).toBeVisible();
  await page.goto("/#/player/3961-22-2-27");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("N'Golo Kanté");
  await expect(page.getByText("Fewer minutes: less reliable")).toHaveCount(0);
});

test("neighbours: ten in-group players with links and ranks", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const doc = await fx(page, "players/3961-22-2-27.json");
  const items = page.locator("section", { has: page.getByRole("heading", { name: "Similar players" }) }).locator("ol > li");
  await expect(items).toHaveCount(doc.neighbours.items.length);
  await items.first().getByRole("link").first().click();
  await expect(page).toHaveURL(new RegExp(`#/player/${doc.neighbours.items[0].key}$`));
});

test("a below-gate row and a goalkeeper show their status, no shape", async ({ page }) => {
  await page.goto("/#/player/2955-143-7-27");
  await expect(page.getByText("below the minutes gate").first()).toBeVisible();
  await expect(page.getByText("Profile shape", { exact: true })).toHaveCount(0);
  await page.goto("/#/player/2947-137-7-27");
  await expect(page.getByText("goalkeeper: no outfield percentiles").first()).toBeVisible();
});

test("a note's short text opens its full text", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const trigger = page.getByRole("button", { name: "no cross-league rank in any group" }).first();
  await trigger.click();
  await expect(page.getByText("league averages differ too much in every position group")).toBeVisible();
});

for (const scheme of ["light", "dark"] as const) {
  for (const key of ["3961-22-2-27", "2955-143-7-27"]) {
    test(`axe player ${key} (${scheme})`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await page.goto(`/#/player/${key}`);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      const r = await new AxeBuilder({ page }).analyze();
      expect(r.violations.filter((v) => v.impact === "serious" || v.impact === "critical")
        .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
    });
  }
}


test("What stands out: the exporter's six as bands, traced; Kanté's defensive volumes visible", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const doc = await fx(page, "players/3961-22-2-27.json");
  expect(doc.stand_out.length).toBe(6);
  const section = page.locator("section", { has: page.getByRole("heading", { name: "What stands out" }) });
  for (const h of doc.stand_out) {
    await expect(section.locator(`[data-src="players/3961-22-2-27.json#metrics.${h.order}.local.band"]`))
      .toHaveText(doc.metrics[h.order].local.band);
  }
  for (const name of ["Tackles attempted", "Interceptions", "Blocks", "Ball recoveries"]) {
    await expect(section.getByText(name, { exact: true })).toBeVisible();
  }
});

test("lower-better rows: band, bar and the popover's number read the shown percentile", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const doc = await fx(page, "players/3961-22-2-27.json");
  const group = await fx(page, "groups/DM-CM.json");
  for (const [i, meta] of (group.metrics as { metric: string; direction: string }[]).entries()) {
    if (meta.direction !== "lower_better") continue;
    const local = doc.metrics[i].local;
    const row = page.locator(`tr[data-metric="${meta.metric}"]`);
    const band = row.locator(`[data-src="players/3961-22-2-27.json#metrics.${i}.local.band"]`);
    await expect(band).toHaveText(local.band);
    await expect(row.locator("[data-bar]").first()).toHaveAttribute("data-bar", String(local.pct));
    const width = await row.locator("[data-bar] > span").first().evaluate((e) => (e as HTMLElement).style.width);
    expect(parseFloat(width)).toBeCloseTo(local.pct, 3);
    await band.click();
    await expect(page.getByText(local.note)).toBeVisible();
    await page.keyboard.press("Escape");
  }
});

test("withheld families: one line each, the full text in a popover", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const fig = page.locator("figure").locator("..");
  await expect(fig.getByText("Defending scores are withheld")).toHaveCount(0);
  const doc = await fx(page, "players/3961-22-2-27.json");
  const def = doc.dna.dimensions.find((d: { family: string }) => d.family === "Defending");
  await fig.getByRole("button", { name: def.withheld_short }).click();
  await expect(page.getByText(/Defending scores are withheld/)).toBeVisible();
});

test("each note once in a metric row", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const row = page.locator('tr[data-metric="pass_completion_pct"]');
  await expect(row.getByRole("button", { name: /associated with team possession/ })).toHaveCount(1);
});

test("no internal jargon on the player page", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const text = await page.locator("main").innerText();
  for (const j of ["M5", "similarity engine", "DNA distance", "not used: league means differ", "basis"]) {
    expect(text).not.toContain(j);
  }
});


test("What stands out shows the family name, not the withheld tag", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const section = page.locator("section", { has: page.getByRole("heading", { name: "What stands out" }) });
  await expect(section.getByText(/score withheld/)).toHaveCount(0);
  await expect(section.getByText("Defending", { exact: true }).first()).toBeVisible();
});


test("display round: every value has its unit, totals trace, one league column", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const doc = await fx(page, "players/3961-22-2-27.json");
  const group = await fx(page, "groups/DM-CM.json");
  await expect(page.getByRole("columnheader", { name: "All-leagues percentile" })).toHaveCount(0);
  for (const [i, meta] of (group.metrics as { metric: string; unit: string }[]).entries()) {
    const row = page.locator(`tr[data-metric="${meta.metric}"]`);
    const v = doc.metrics[i];
    if (meta.unit) await expect(row.locator("[data-unit]")).toHaveText(meta.unit);
    else expect(v.value_text.endsWith("%")).toBe(true);
    await expect(row.locator("[data-total]")).toHaveText(v.total_text);
  }
});

test("a metric name opens its definition", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const group = await fx(page, "groups/DM-CM.json");
  const meta = group.metrics.find((m: { metric: string }) => m.metric === "progressive_passes_p90");
  await page.locator('tr[data-metric="progressive_passes_p90"]').getByRole("button", { name: meta.label }).click();
  await expect(page.getByText(meta.definition)).toBeVisible();
});

test("the season summary traces to the fixture", async ({ page }) => {
  await page.goto("/#/player/3961-22-2-27");
  const doc = await fx(page, "players/3961-22-2-27.json");
  for (const s of doc.identity.summary) {
    await expect(page.locator(`[data-summary="${s.key}"]`)).toHaveText(`${s.text} ${s.label}`);
  }
});
