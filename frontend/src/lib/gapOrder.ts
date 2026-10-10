// Compare mode's top 6 (ADR 005 §1, the one exception to "no math in the browser"; user
// 2026-10-09): subtract the two players' shown (direction-adjusted) league percentiles and
// order by the absolute gap, for display order only. Eligible: the group metadata's
// gap_eligible (option c) and a shown percentile on both rows. Ties by registry metric order.
// Mirrors export/display.gap_order; tested against Python's output on the fixture.
import type { GroupsGroupJson, PlayersKeyJson } from "../types/contract";

export const TOP_N = 6;

export function gapOrder(metas: GroupsGroupJson["metrics"], a: PlayersKeyJson["metrics"],
                         b: PlayersKeyJson["metrics"], n = TOP_N): string[] {
  if (metas.length !== a.length || a.length !== b.length) throw new Error("metric lists differ");
  const rows: [number, number, string][] = [];
  metas.forEach((m, i) => {
    const pa = a[i].local.pct, pb = b[i].local.pct;
    if (!m.gap_eligible || pa === null || pb === null) return;
    rows.push([-Math.abs(pa - pb), m.order, m.metric]);
  });
  rows.sort((x, y) => x[0] - y[0] || x[1] - y[1]);
  return rows.slice(0, n).map((r) => r[2]);
}
