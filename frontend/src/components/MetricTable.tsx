// The metric table (DESIGN.md §5; display round, user 2026-10-10): per family, each metric's
// value with its unit and season total, and its league percentile shown as a band with the bar
// (the number, the all-leagues percentile and the population in the popover), then notes.
// Every display string is exported; nothing is computed here.
import type { GroupsGroupJson, LabelsJson, PlayersKeyJson } from "../types/contract";
import { BandCell, MetricName, ValueText } from "./Cells";
import { NotePopover } from "./NotePopover";
import { UI } from "../lib/ui-text";

type Scope = PlayersKeyJson["metrics"][number]["local"];

const BIN_COLOUR = ["var(--p0)", "var(--p1)", "var(--p2-bar)", "var(--p3)", "var(--p4)"];

export function Bar({ scope }: { scope: Pick<Scope, "pct" | "bin"> }) {
  if (scope.pct === null || scope.bin === null) return null;
  const hollow = scope.bin === "uncoloured";
  return (
    <span className="relative ml-2 inline-block h-1.5 w-12 overflow-hidden rounded-[3px] align-middle shadow-[inset_0_0_0_1px_var(--line)]" aria-hidden="true" data-bar={scope.pct}>
      <span className="absolute inset-y-0 left-0 rounded-[3px]"
            style={{ width: `${scope.pct}%`, background: hollow ? "transparent" : BIN_COLOUR[scope.bin as number],
                     boxShadow: hollow ? "inset 0 0 0 1.5px var(--ink-3)" : undefined }} />
    </span>
  );
}

/** A family's withheld tag (every metric of the family shares it), or undefined. */
export function familyTag(group: GroupsGroupJson, family: string): string | undefined {
  return group.metrics.find((m) => m.family === family && m.withheld_tag)?.withheld_tag ?? undefined;
}

export function MetricTable({ doc, group, labels }: { doc: PlayersKeyJson; group: GroupsGroupJson; labels: LabelsJson }) {
  const w = labels.words;
  // Rows pair with the group metadata by index: both follow labels.metric_order. Refuse a
  // mismatch rather than show a value under the wrong label.
  if (doc.metrics.length !== group.metrics.length
      || group.metrics.some((m, i) => m.metric !== labels.metric_order[i])) {
    throw new Error("metric order differs between the player file, the group file and labels");
  }
  const key = doc.identity.key;
  const families = [...new Set(group.metrics.map((m) => m.family))];
  return (
    <div className="overflow-x-auto rounded-lg border border-line bg-surface-1" tabIndex={0} role="region" aria-label={UI.table.metrics}>
      <table className="w-full border-collapse text-[13px] leading-[18px]">
        <thead className="text-[12px] text-ink-2">
          <tr className="border-b border-line">
            <th className="sticky left-0 bg-surface-1 px-2.5 py-2 text-left font-semibold"><span className="sr-only">{UI.table.metric}</span></th>
            <th className="px-2.5 py-2 text-right font-semibold">{w.column_value}</th>
            <th className="px-2.5 py-2 text-left font-semibold">{w.column_local}</th>
            <th className="px-2.5 py-2 text-left font-semibold"><span className="sr-only">{UI.table.notes}</span></th>
          </tr>
        </thead>
        {families.map((fam) => {
          const tag = familyTag(group, fam);
          return (
            <tbody key={fam}>
              <tr><th colSpan={4} className="bg-surface-2 px-2.5 py-1.5 text-left text-[12px] font-semibold text-ink-2">
                {labels.families[fam]}
                {tag && <span className="ml-2 font-normal text-ink-3">{tag}</span>}
              </th></tr>
              {group.metrics.map((meta, i) => meta.family !== fam ? null : (
                <tr key={meta.metric} className="border-b border-line last:border-b-0" data-metric={meta.metric}>
                  <th scope="row" className="sticky left-0 bg-surface-1 px-2.5 py-1.5 text-left font-normal">
                    <MetricName meta={meta} />
                    <div className="text-[12px] leading-4 text-ink-3">{meta.direction === "style" ? w.chip_style : meta.direction_text}</div>
                  </th>
                  <td className="px-2.5 py-1.5 text-right">
                    <ValueText row={doc.metrics[i]} meta={meta} src={`players/${key}.json#metrics.${i}.value_text`} />
                  </td>
                  <td className="px-2.5 py-1.5">
                    <BandCell row={doc.metrics[i]} src={`players/${key}.json#metrics.${i}.local.band`} hint={w.exact_hint} />
                  </td>
                  <td className="min-w-[180px] px-2.5 py-1.5 text-[12px] leading-4">
                    <div className="flex flex-col gap-1">
                      {meta.notes.map((n) => <NotePopover key={n.full} short={n.short} full={n.full} />)}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          );
        })}
      </table>
    </div>
  );
}
