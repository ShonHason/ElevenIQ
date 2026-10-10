// The shared display cells of the player table, What stands out and compare (display round,
// user 2026-10-10): a metric name that opens its definition; a value with its unit and season
// total; a league percentile shown as its band (the number in the popover, with the
// all-leagues percentile where it exists). Every text is exported.
import type { GroupsGroupJson, PlayersKeyJson } from "../types/contract";
import { Bar } from "./MetricTable";
import { Details } from "./Details";

type Meta = GroupsGroupJson["metrics"][number];
type Row = PlayersKeyJson["metrics"][number];

export function MetricName({ meta }: { meta: Meta }) {
  return <Details trigger={meta.label} lines={[meta.definition]} className="text-ink" />;
}

export function ValueText({ row, meta, src }: { row: Row; meta: Meta; src: string }) {
  return (
    <span className="whitespace-nowrap">
      <span className="num" data-src={src}>{row.value_text}</span>
      {meta.unit && row.value !== null && <span className="ml-1 text-ink-3" data-unit>{meta.unit}</span>}
      {row.total_text && <div className="num text-[12px] leading-4 text-ink-3" data-total>{row.total_text}</div>}
    </span>
  );
}

export function BandCell({ row, src, hint }: { row: Row; src: string; hint: string }) {
  const s = row.local;
  if (s.band === null) return <span className="text-[12px] italic text-ink-2">{s.status_text}</span>;
  const g = row.global;
  return (
    <span className="whitespace-nowrap">
      <Details trigger={<><span className="font-medium" data-src={src}>{s.band}</span><span className="sr-only">, {hint}</span></>}
               lines={[s.note, g.note ?? g.status_text, s.population_text]} />
      <Bar scope={s} />
    </span>
  );
}
