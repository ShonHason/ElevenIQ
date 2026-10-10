// Compare mode (plan 006 W4; user decisions 2026-10-09): two players of the same position group,
// no distance or similarity score. Both Profile shapes overlaid; the top-6 gaps first (the ADR 005
// §1 exception, src/lib/gapOrder.ts), then every metric; the per-90 value and the league
// percentile side by side; defensive metrics also show the exported rate.
import { useEffect, useRef } from "react";
import { getGroup, getLabels, getPlayer } from "../api";
import { ProfileShape, STROKES, Swatch } from "../components/ProfileShape";
import { familyTag } from "../components/MetricTable";
import { BandCell, MetricName, ValueText } from "../components/Cells";
import { SearchBox } from "../components/SearchBox";
import { gapOrder } from "../lib/gapOrder";
import { href } from "../lib/router";
import { UI } from "../lib/ui-text";
import { useData } from "../lib/useData";
import type { GroupsGroupJson, LabelsJson, PlayersKeyJson } from "../types/contract";

type Doc = PlayersKeyJson;
function Cell({ doc, i, meta, hint }: { doc: Doc; i: number; meta: GroupsGroupJson["metrics"][number]; hint: string }) {
  const key = doc.identity.key;
  return (
    <>
      <td className="px-2.5 py-1.5 text-right">
        <ValueText row={doc.metrics[i]} meta={meta} src={`players/${key}.json#metrics.${i}.value_text`} />
      </td>
      <td className="px-2.5 py-1.5">
        <BandCell row={doc.metrics[i]} src={`players/${key}.json#metrics.${i}.local.band`} hint={hint} />
      </td>
    </>
  );
}

// The defensive rate for both players (user-approved label); a missing rate says so for that
// player only (missing data surfaces). Shown only for the metrics that carry a rate.
function Rate({ a, b, metric, labels }: { a: Doc; b: Doc; metric: string; labels: LabelsJson }) {
  const rates = [a, b].map((d) => d.defensive_rates.find((r) => r.metric === metric));
  if (rates.every((r) => r === undefined)) return null;
  return (
    <div className="mt-0.5 flex flex-wrap gap-x-2 text-[12px] leading-4 text-ink-3" data-rate={metric}>
      {[a, b].map((d, k) => (
        <span key={d.identity.key} className="num text-ink-2">
          <Swatch k={k} /><span className="sr-only">{d.identity.display_name}: </span>
          {rates[k]?.rate_text ?? labels.statuses.null_value}
        </span>
      ))}
      <span>{labels.words.rate_label}</span>
    </div>
  );
}

function Rows({ metrics, a, b, group, labels }: { metrics: string[]; a: Doc; b: Doc; group: GroupsGroupJson; labels: LabelsJson }) {
  const index = new Map(group.metrics.map((m, i) => [m.metric, i]));
  return (
    <>
      {metrics.map((m) => {
        const i = index.get(m)!;
        const meta = group.metrics[i];
        return (
          <tr key={m} className="border-b border-line last:border-b-0" data-metric={m}>
            <th scope="row" className="sticky left-0 bg-surface-1 px-2.5 py-1.5 text-left font-normal">
              <MetricName meta={meta} />
              <div className="text-[12px] leading-4 text-ink-3">{meta.direction === "style" ? labels.words.chip_style : meta.direction_text}</div>
              <Rate a={a} b={b} metric={m} labels={labels} />
            </th>
            <Cell doc={a} i={i} meta={meta} hint={labels.words.exact_hint} />
            <Cell doc={b} i={i} meta={meta} hint={labels.words.exact_hint} />
          </tr>
        );
      })}
    </>
  );
}

function Head({ a, b, labels }: { a: Doc; b: Doc; labels: LabelsJson }) {
  const w = labels.words;
  return (
    <thead className="text-[12px] text-ink-2">
      <tr className="border-b border-line">
        <th className="sticky left-0 bg-surface-1 px-2.5 py-2 text-left"><span className="sr-only">{UI.table.metric}</span></th>
        <th colSpan={2} scope="colgroup" className="px-2.5 py-2 text-right font-semibold text-ink"><Swatch k={0} />{a.identity.display_name}</th>
        <th colSpan={2} scope="colgroup" className="px-2.5 py-2 text-right font-semibold text-ink"><Swatch k={1} />{b.identity.display_name}</th>
      </tr>
      <tr className="border-b border-line">
        <th className="sticky left-0 bg-surface-1"><span className="sr-only">{UI.table.metric}</span></th>
        {[0, 1].map((k) => (
          [<th key={`v${k}`} className="px-2.5 py-1.5 text-right font-normal">{w.column_value}</th>,
           <th key={`l${k}`} className="px-2.5 py-1.5 text-left font-normal">{w.column_local}</th>]
        ))}
      </tr>
    </thead>
  );
}

export function Compare({ a: ka, b: kb }: { a: string; b: string }) {
  const { data, failed } = useData(async () => {
    const [a, b, labels] = await Promise.all([getPlayer(ka), getPlayer(kb), getLabels()]);
    const group = await getGroup(a.identity.group);
    return { a, b, labels, group };
  }, [ka, kb]);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (!data) return;
    document.title = `${data.a.identity.display_name} – ${data.b.identity.display_name} — ${UI.site}`;
    heading.current?.focus();
  }, [data]);
  if (failed) return <p className="pt-10 text-ink-2">{UI.error}</p>;
  if (!data) return <p className="pt-10 text-ink-3">{UI.loading}</p>;
  const { a, b, labels, group } = data;
  const w = labels.words;
  const ia = a.identity, ib = b.identity;
  // A refused pair says why: another position group, or a player without a profile (its status).
  const noProfile = [a, b].find((d) => !d.dna);
  const reason = ia.group !== ib.group ? w.compare_same_group
    : noProfile ? `${noProfile.identity.display_name}: ${noProfile.identity.group === "GK" ? labels.statuses.goalkeeper
        : !noProfile.identity.meets_gate ? labels.statuses.below_gate : labels.statuses.not_in_pool}` : null;
  if (reason || !a.dna || !b.dna) {
    return (
      <section className="pt-10">
        <h1 ref={heading} tabIndex={-1} className="text-[17px] font-semibold outline-none">{UI.compare}</h1>
        <p className="mt-2 text-ink-2">{reason}</p>
        <a href={href.player(ka)} className="mt-2 inline-block">{ia.display_name}</a>
      </section>
    );
  }
  const top = gapOrder(group.metrics, a.metrics, b.metrics);
  const families = [...new Set(group.metrics.map((m) => m.family))];
  return (
    <article className="pt-8">
      <h1 ref={heading} tabIndex={-1} className="sr-only outline-none">{`${UI.compare}: ${ia.display_name}, ${ib.display_name}`}</h1>
      <div className="grid gap-3 md:grid-cols-2">
        {[a, b].map((d, k) => (
          <header key={d.identity.key} className="rounded-lg border border-line bg-surface-1 px-4 py-3" style={{ borderTop: `3px ${k === 1 ? "dashed" : "solid"} ${STROKES[k]}` }}>
            <a href={href.player(d.identity.key)} className="text-[20px] font-semibold leading-7 text-ink no-underline">{d.identity.display_name}</a>
            <div className="mt-1 flex flex-wrap gap-x-4 text-ink-2">
              <span>{d.identity.team}</span><span>{d.identity.league}</span>
              <span><span className="num">{d.identity.minutes_text}</span> {w.column_minutes}</span>
            </div>
          </header>
        ))}
      </div>
      <div className="mt-3 max-w-md">
        <SearchBox group={ia.group} exclude={ia.key} label={UI.compareWith}
                   onPick={(e) => { window.location.hash = href.compare(ia.key, e.key); }} />
      </div>

      <div className="mt-8 grid items-start gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <ProfileShape series={[{ key: ia.key, name: ia.display_name, dims: a.dna.dimensions },
                               { key: ib.key, name: ib.display_name, dims: b.dna.dimensions }]}
                      labels={{ title: w.profile_shape_title, note: w.profile_shape_note,
                                withheld: w.chip_withheld, average: w.around_average }} />
        <section>
          <h2 className="text-[17px] font-semibold leading-6">{w.compare_top_title}</h2>
          <p className="mt-0.5 text-[12px] leading-4 text-ink-3">{w.compare_top_note}</p>
          <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface-1" tabIndex={0} role="region" aria-label={w.compare_top_title}>
            <table className="w-full border-collapse text-[13px] leading-[18px]" data-table="top">
              <Head a={a} b={b} labels={labels} />
              <tbody><Rows metrics={top} a={a} b={b} group={group} labels={labels} /></tbody>
            </table>
          </div>
        </section>
      </div>

      <section className="mt-10">
        <h2 className="text-[17px] font-semibold leading-6">{w.compare_all_title}</h2>
        <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface-1" tabIndex={0} role="region" aria-label={w.compare_all_title}>
          <table className="w-full border-collapse text-[13px] leading-[18px]" data-table="all">
            <Head a={a} b={b} labels={labels} />
            {families.map((fam) => (
              <tbody key={fam}>
                <tr><th colSpan={5} className="bg-surface-2 px-2.5 py-1.5 text-left text-[12px] font-semibold text-ink-2">
                  {labels.families[fam]}
                  {familyTag(group, fam) && <span className="ml-2 font-normal text-ink-3">{familyTag(group, fam)}</span>}
                </th></tr>
                <Rows metrics={group.metrics.filter((m) => m.family === fam).map((m) => m.metric)} a={a} b={b} group={group} labels={labels} />
              </tbody>
            ))}
          </table>
        </div>
      </section>
    </article>
  );
}
