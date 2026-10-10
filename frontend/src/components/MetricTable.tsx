// The metric table (DESIGN.md §5): per family, each metric's value and its league and
// all-leagues percentiles with the colour bin, n (population sentence on hover), status text,
// notes and withheld tags. Every display string is exported; nothing is computed here.
import type { GroupsGroupJson, LabelsJson, PlayersKeyJson } from "../types/contract";
import { Chip } from "./Chip";
import { NotePopover } from "./NotePopover";
import { UI } from "../lib/ui-text";

type Row = PlayersKeyJson["metrics"][number];
type Meta = GroupsGroupJson["metrics"][number];
type Scope = Row["local"];

const BIN_COLOUR = ["var(--p0)", "var(--p1)", "var(--p2-bar)", "var(--p3)", "var(--p4)"];

function Bar({ scope }: { scope: Scope }) {
  if (scope.pct === null || scope.bin === null) return null;
  const hollow = scope.bin === "uncoloured";
  return (
    <span className="relative ml-2 inline-block h-1.5 w-12 overflow-hidden rounded-[3px] align-middle shadow-[inset_0_0_0_1px_var(--line)]" aria-hidden="true">
      <span className="absolute inset-y-0 left-0 rounded-[3px]"
            style={{ width: `${scope.pct}%`, background: hollow ? "transparent" : BIN_COLOUR[scope.bin as number],
                     boxShadow: hollow ? "inset 0 0 0 1.5px var(--ink-3)" : undefined }} />
    </span>
  );
}

function ScopeCell({ scope, src, basis, basisLabel }: { scope: Scope; src: string; basis: boolean; basisLabel: string }) {
  if (scope.pct_text === null) {
    return <td className="px-2.5 py-1.5 text-[12px] italic text-ink-2">{scope.status_text}</td>;
  }
  return (
    <td className="whitespace-nowrap px-2.5 py-1.5 text-right" title={scope.population_text ?? undefined}>
      <span className="num font-medium" data-src={src}>{scope.pct_text}</span>
      <Bar scope={scope} />
      {basis && <span className="ml-1.5"><Chip>{basisLabel}</Chip></span>}
      <div className="text-[12px] leading-4 text-ink-3">{scope.phrase}</div>
      {scope.population_text && <span className="sr-only">{scope.population_text}</span>}
    </td>
  );
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
    <div className="overflow-x-auto rounded-lg border border-line bg-surface-1">
      <table className="w-full border-collapse text-[13px] leading-[18px]">
        <thead className="text-[12px] text-ink-2">
          <tr className="border-b border-line">
            <th className="sticky left-0 bg-surface-1 px-2.5 py-2 text-left font-semibold"><span className="sr-only">{UI.table.metric}</span></th>
            <th className="px-2.5 py-2 text-right font-semibold">{w.column_value}</th>
            <th className="px-2.5 py-2 text-right font-semibold">{w.column_local}</th>
            <th className="px-2.5 py-2 text-right font-semibold">{w.column_global}</th>
            <th className="px-2.5 py-2 text-left font-semibold"><span className="sr-only">{UI.table.notes}</span></th>
          </tr>
        </thead>
        {families.map((fam) => (
          <tbody key={fam}>
            <tr><th colSpan={5} className="bg-surface-2 px-2.5 py-1.5 text-left text-[12px] font-semibold text-ink-2">{labels.families[fam]}</th></tr>
            {group.metrics.map((meta: Meta, i: number) => meta.family !== fam ? null : (
              <tr key={meta.metric} className="border-b border-line last:border-b-0" data-metric={meta.metric}>
                <th scope="row" className="sticky left-0 bg-surface-1 px-2.5 py-1.5 text-left font-normal">
                  <div className="text-ink">{meta.label}</div>
                  <div className="text-[12px] leading-4 text-ink-3">{meta.direction === "style" ? w.chip_style : meta.direction_text}</div>
                </th>
                <td className="num whitespace-nowrap px-2.5 py-1.5 text-right" data-src={`players/${key}.json#metrics.${i}.value_text`}>
                  {doc.metrics[i].value_text}
                </td>
                <ScopeCell scope={doc.metrics[i].local} src={`players/${key}.json#metrics.${i}.local.pct_text`}
                           basis={doc.metrics[i].basis === "local"} basisLabel={w.chip_basis} />
                <ScopeCell scope={doc.metrics[i].global} src={`players/${key}.json#metrics.${i}.global.pct_text`}
                           basis={doc.metrics[i].basis === "global"} basisLabel={w.chip_basis} />
                <td className="min-w-[180px] px-2.5 py-1.5 text-[12px] leading-4">
                  <div className="flex flex-col gap-1">
                    {meta.withheld_tag && <span className="text-ink-3">{meta.withheld_tag}</span>}
                    {meta.notes.map((n) => <NotePopover key={n.full} short={n.short} full={n.full} />)}
                    {meta.reasons && <span className="text-ink-3">{meta.reasons}</span>}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        ))}
      </table>
    </div>
  );
}
