import type { LabelsJson, PlayersKeyJson } from "../types/contract";
import { href } from "../lib/router";
import { Chip } from "./Chip";
import { UI } from "../lib/ui-text";

type Nb = NonNullable<PlayersKeyJson["neighbours"]>;

export function Neighbours({ nb, from, labels }: { nb: Nb; from: string; labels: LabelsJson }) {
  const w = labels.words;
  return (
    <section>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-[17px] font-semibold leading-6 tracking-[-0.005em]">{w.neighbours_title}</h2>
        <Chip kind="provisional">{w.chip_provisional}</Chip>
      </div>
      <p className="mt-0.5 text-[12px] leading-4 text-ink-3">{nb.label}</p>
      <ol className="mt-3 divide-y divide-line rounded-lg border border-line bg-surface-1">
        {nb.items.map((n, i) => (
          <li key={n.key} className="grid grid-cols-[2.5rem_1fr] gap-x-3 px-3 py-2.5 md:grid-cols-[2.5rem_1fr_auto]">
            <span className="num pt-0.5 text-ink-3" data-src={`players/${from}.json#neighbours.items.${i}.rank`}>{n.rank}</span>
            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <a href={href.player(n.key)} className="font-medium">{n.display_name}</a>
                {n.same_team && <Chip kind="same_team">{w.chip_same_team}</Chip>}
              </div>
              <div className="flex flex-wrap gap-x-3 text-[12px] leading-4 text-ink-3">
                <span>{n.team}</span><span>{n.league}</span>
              </div>
              <div className="mt-1 text-[12px] leading-4 text-ink-2">
                <span className="text-ink-3">{w.most_alike}:</span> {n.most_alike.map((p) => p.label).join(", ")}
              </div>
              <div className="text-[12px] leading-4 text-ink-2">
                <span className="text-ink-3">{w.most_different}:</span> {n.most_different.map((p) => p.label).join(", ")}
              </div>
            </div>
            <div className="col-start-2 mt-1 flex items-center gap-3 md:col-start-auto md:mt-0 md:flex-col md:items-end">
              <span className="num text-ink" data-src={`players/${from}.json#neighbours.items.${i}.distance_text`}>{n.distance_text}</span>
              <a href={href.compare(from, n.key)} className="text-[12px]">{UI.compare}</a>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
