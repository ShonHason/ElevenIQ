// The player page (plan 006 W3): identity strip, Profile shape, metric table, signature traits,
// neighbours. Every figure and statement comes from the export.
import { useEffect, useRef } from "react";
import { getGroup, getLabels, getPlayer } from "../api";
import { Chip } from "../components/Chip";
import { Details } from "../components/Details";
import { StandOut } from "../components/StandOut";
import { NotePopover } from "../components/NotePopover";
import { MetricTable } from "../components/MetricTable";
import { Neighbours } from "../components/Neighbours";
import { ProfileShape } from "../components/ProfileShape";
import { UI } from "../lib/ui-text";
import { SearchBox } from "../components/SearchBox";
import { href } from "../lib/router";
import { useData } from "../lib/useData";

export function Player({ playerKey }: { playerKey: string }) {
  const { data, failed } = useData(async () => {
    const doc = await getPlayer(playerKey);
    const [group, labels] = await Promise.all([getGroup(doc.identity.group), getLabels()]);
    return { doc, group, labels };
  }, [playerKey]);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (!data) return;
    document.title = `${data.doc.identity.display_name} — ${UI.site}`;
    heading.current?.focus();
  }, [data]);

  if (failed) return <p className="pt-10 text-ink-2">{UI.error}</p>;
  if (!data) return <p className="pt-10 text-ink-3">{UI.loading}</p>;
  const { doc, group, labels } = data;
  const i = doc.identity;
  const w = labels.words;
  const noProfile = !doc.dna ? (i.group === "GK" ? labels.statuses.goalkeeper
    : !i.meets_gate ? labels.statuses.below_gate : labels.statuses.not_in_pool) : null;

  return (
    <article className="pt-8">
      <header className="rounded-lg border border-line bg-surface-1 px-4 py-3">
        <h1 ref={heading} tabIndex={-1} className="text-[24px] font-semibold leading-[30px] tracking-[-0.01em] outline-none">{i.display_name}</h1>
        <div className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-ink-2">
          <span>{i.team}</span><span>{i.league}</span><span>{i.group_text}</span>
          <span><span className="num" data-src={`players/${i.key}.json#identity.minutes_text`}>{i.minutes_text}</span> {w.column_minutes}</span>
          {i.role_text && <span>{i.role_text}</span>}
          {i.near_gate && <Chip kind="caution">{w.near_gate_chip}</Chip>}
        </div>
        {doc.style_tags.length > 0 && (
          <ul className="mt-2 flex flex-wrap items-center gap-2" aria-label={w.style_tags_title}>
            {doc.style_tags.map((t) => (
              <li key={t.metric} data-tag={t.metric}>
                <Details trigger={<span className="inline-flex h-[22px] items-center rounded-full border border-line bg-surface-2 px-2 text-[12px] leading-4 text-ink-2">{t.tag}</span>}
                         lines={[t.note]} className="no-underline" />
              </li>
            ))}
          </ul>
        )}
        <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1 border-t border-line pt-2 text-[13px]" aria-label={UI.season}>
          {i.summary.map((s) => (
            <li key={s.key} data-summary={s.key}><span className="num font-semibold text-ink">{s.text}</span> <span className="text-ink-3">{s.label}</span></li>
          ))}
        </ul>
      </header>

      {noProfile ? (
        <p className="mt-6 text-ink-2">{noProfile}</p>
      ) : (
        <>
          <div className="mt-6 grid grid-cols-[minmax(0,1fr)] items-start gap-8 lg:grid-cols-[minmax(0,1fr)_340px]">
            <div>
              <ProfileShape series={[{ key: i.key, name: i.display_name, dims: doc.dna!.dimensions }]}
                            labels={{ title: w.profile_shape_title, note: w.profile_shape_note,
                                      withheld: w.chip_withheld, average: w.around_average }}
                            definitions={group.family_definitions} />
              <ul className="mt-3 space-y-1.5 text-[12px] leading-4">
                {doc.dna!.dimensions.filter((d) => d.withheld_short).map((d) => (
                  <li key={d.family} className="flex flex-wrap items-center gap-2">
                    <Chip kind="withheld">{w.chip_withheld}</Chip>
                    <span className="font-medium">{d.label}</span>
                    <NotePopover short={d.withheld_short!}
                                 full={[d.withheld_full, d.defending_note].filter(Boolean).join(" ")} />
                  </li>
                ))}
              </ul>
            </div>
            <StandOut doc={doc} group={group} labels={labels} />
          </div>
          <div className="mt-8 max-w-md">
            <SearchBox group={i.group} exclude={i.key} label={UI.compareWith}
                       onPick={(e) => { window.location.hash = href.compare(i.key, e.key); }} />
          </div>
          {doc.neighbours && <div className="mt-10"><Neighbours nb={doc.neighbours} from={i.key} labels={labels} /></div>}
        </>
      )}

      <section className="mt-10">
        <MetricTable doc={doc} group={group} labels={labels} />
      </section>

      {doc.signature_traits.length > 0 && (
        <section className="mt-10">
          <h2 className="text-[17px] font-semibold leading-6">{w.signature_title}</h2>
          <p className="mt-0.5 text-[12px] leading-4 text-ink-3">{w.signature_note}</p>
          <ul className="mt-3 divide-y divide-line rounded-lg border border-line bg-surface-1">
            {doc.signature_traits.map((t) => (
              <li key={t.metric} className="flex flex-wrap items-baseline justify-between gap-2 px-3 py-2">
                <span>{t.label}</span>
                <span className={t.holder ? "font-medium text-ink" : "text-ink-2"}>{t.holder_text}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}
