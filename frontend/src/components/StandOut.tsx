// What stands out (W3 fix round): the 6 league percentiles furthest from 50, chosen and ranked
// by the exporter; hollow-bar metrics included with their neutral phrase.
import type { GroupsGroupJson, LabelsJson, PlayersKeyJson } from "../types/contract";
import { Bar } from "./MetricTable";
import { Details } from "./Details";
import { HollowLegend } from "./Cells";

export function StandOut({ doc, group, labels }: { doc: PlayersKeyJson; group: GroupsGroupJson; labels: LabelsJson }) {
  if (doc.stand_out.length === 0) return null;
  const w = labels.words;
  return (
    <section aria-labelledby="stand-out-title" className="rounded-lg border border-line bg-surface-1 px-4 py-3">
      <h2 id="stand-out-title" className="text-[17px] font-semibold leading-6 tracking-[-0.005em]">{w.stand_out_title}</h2>
      <p className="mt-0.5 text-[12px] leading-4 text-ink-3">{w.stand_out_note}</p>
      {doc.stand_out.some((h) => h.bin === "uncoloured") && <div className="mt-1.5"><HollowLegend text={w.hollow_legend} /></div>}
      <ol className="mt-3 grid gap-y-2">
        {doc.stand_out.map((h) => {
          // The family name only: the withheld explanation sits under the Profile shape (user).
          const family = labels.families[group.metrics[h.order].family];
          return (
            <li key={h.metric} className="border-b border-line pb-2 last:border-b-0">
              <div className="flex items-baseline justify-between gap-3">
                <span className="min-w-0 text-ink">{h.label}</span>
                <span className="whitespace-nowrap">
                  <Details trigger={<span className="text-[15px] font-semibold" data-src={`players/${doc.identity.key}.json#metrics.${h.order}.local.band`}>{doc.metrics[h.order].local.band}</span>}
                           lines={[doc.metrics[h.order].local.note]} />
                  <Bar scope={{ pct: h.pct, bin: h.bin }} />
                </span>
              </div>
<div className="flex flex-wrap justify-between gap-x-3 text-[12px] leading-4 text-ink-3">
                <span>
                  {h.direction === "lower_better" && <span className="mr-2">{w.lower_better}</span>}
                  <span>{family}</span>
                </span>

              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
