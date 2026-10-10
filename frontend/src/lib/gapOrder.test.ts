import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { gapOrder } from "./gapOrder";

const root = new URL("../../../tests/fixtures/web/", import.meta.url);
const read = (p: string) => JSON.parse(readFileSync(new URL(p, root), "utf8"));
const expected: Record<string, string[]> = JSON.parse(readFileSync(new URL("./gap-order.expected.json", import.meta.url), "utf8"));

describe("compare top-6 gap order (ADR 005 §1 exception)", () => {
  it("equals Python's display.gap_order on every same-group fixture pair", () => {
    const pairs = Object.entries(expected);
    expect(pairs.length).toBeGreaterThan(100);
    for (const [pair, want] of pairs) {
      const [ka, kb] = pair.split("|");
      const a = read(`players/${ka}.json`), b = read(`players/${kb}.json`);
      const metas = read(`groups/${a.identity.group}.json`).metrics;
      expect(gapOrder(metas, a.metrics, b.metrics), pair).toEqual(want);
    }
  });
});
