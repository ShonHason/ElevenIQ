// Gem Finder (plan 006 simple mode, W5): one position group's profiled players with their family
// scores. Orders come from the exporter (ADR 005 §1: the page never sorts by a statistic); the
// page only filters by exported keys (league, team, minutes tier). Withheld families are listed
// with their reason and are never an order. "Players like X" lists X's exported neighbours from
// other teams, in the exporter's rank order.
import { useEffect, useMemo, useRef, useState } from "react";
import { getGems, getLabels, getPlayer } from "../api";
import { Chip } from "../components/Chip";
import { NotePopover } from "../components/NotePopover";
import { SearchBox } from "../components/SearchBox";
import { href } from "../lib/router";
import { UI } from "../lib/ui-text";
import { useData } from "../lib/useData";
import type { GemsGroupJson, LabelsJson, PlayersKeyJson } from "../types/contract";

const GROUPS = ["CB", "FB-WB", "DM-CM", "AM-Winger", "ST"];
type Row = GemsGroupJson["rows"][number];

const PAGE = 50;
const select = "h-8 rounded-md border border-line bg-surface-1 px-2 text-ink";

function ScoreCells({ row, scored }: { row: Row; scored: GemsGroupJson["families"] }) {
  return (
    <>
      {scored.map((f) => (
        <td key={f.family} className="num px-2.5 py-1.5 text-right" data-src={`gems#${row.key}.scores.${f.family}`}>
          {row.scores[f.family]?.score_text ?? "—"}
        </td>
      ))}
    </>
  );
}

function PlayerCell({ row, labels }: { row: Row; labels: LabelsJson }) {
  return (
    <th scope="row" className="sticky left-0 bg-surface-1 px-2.5 py-1.5 text-left font-normal">
      <a href={href.player(row.key)} className="font-medium text-ink">{row.display_name}</a>
      {row.near_gate && <span className="ml-2"><Chip kind="caution">{labels.words.near_gate_chip}</Chip></span>}
      <div className="flex flex-wrap gap-x-3 text-[12px] leading-4 text-ink-3"><span>{row.team}</span><span>{row.league}</span></div>
    </th>
  );
}

function Like({ gems, likeKey, labels, scored, onClear }: {
  gems: GemsGroupJson; likeKey: string; labels: LabelsJson; scored: GemsGroupJson["families"]; onClear: () => void;
}) {
  const { data } = useData(() => getPlayer(likeKey), [likeKey]);
  const byKey = useMemo(() => new Map(gems.rows.map((r) => [r.key, r])), [gems]);
  if (!data) return <p className="text-ink-3">{UI.loading}</p>;
  const doc: PlayersKeyJson = data;
  const items = (doc.neighbours?.items ?? []).filter((n) => !n.same_team);
  return (
    <section aria-labelledby="like-title" className="mt-6">
      <div className="flex flex-wrap items-baseline gap-3">
        <h2 id="like-title" className="text-[17px] font-semibold leading-6">
          {labels.words.gems_like_title} <a href={href.player(likeKey)}>{doc.identity.display_name}</a>
        </h2>
        <button type="button" onClick={onClear} className="text-ink-2 underline underline-offset-2 hover:text-ink">{UI.gems.clearLike}</button>
      </div>
      <p className="mt-0.5 text-[12px] leading-4 text-ink-3">{labels.words.gems_like_note}</p>
      <div className="mt-3 overflow-x-auto rounded-lg border border-line bg-surface-1" tabIndex={0} role="region" aria-label={labels.words.gems_like_title}>
        <table className="w-full border-collapse text-[13px] leading-[18px]" data-table="like">
          <thead className="text-[12px] text-ink-2">
            <tr className="border-b border-line">
              <th className="sticky left-0 bg-surface-1 px-2.5 py-2 text-left font-semibold">{UI.gems.player}</th>
              <th className="px-2.5 py-2 text-right font-semibold">{labels.words.column_minutes}</th>
              {scored.map((f) => <th key={f.family} className="px-2.5 py-2 text-right font-semibold">{f.label}</th>)}
            </tr>
          </thead>
          <tbody>
            {items.map((n) => {
              const row = byKey.get(n.key);
              return (
                <tr key={n.key} className="border-b border-line last:border-b-0" data-key={n.key}>
                  {row ? <PlayerCell row={row} labels={labels} />
                    : <th scope="row" className="px-2.5 py-1.5 text-left font-normal"><a href={href.player(n.key)}>{n.display_name}</a>
                        <div className="flex flex-wrap gap-x-3 text-[12px] leading-4 text-ink-3"><span>{n.team}</span><span>{n.league}</span></div></th>}
                  <td className="num px-2.5 py-1.5 text-right">{row?.minutes_text ?? "—"}</td>
                  {row ? <ScoreCells row={row} scored={scored} /> : scored.map((f) => <td key={f.family} className="px-2.5 py-1.5 text-right">—</td>)}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export function Gems({ group: routeGroup }: { group: string | null }) {
  const group = routeGroup && GROUPS.includes(routeGroup) ? routeGroup : GROUPS[0];
  const { data, failed } = useData(async () => {
    const [gems, labels] = await Promise.all([getGems(group), getLabels()]);
    return { gems, labels };
  }, [group]);
  const [league, setLeague] = useState("");
  const [team, setTeam] = useState("");
  const [minutes, setMinutes] = useState("");
  const [family, setFamily] = useState<string | null>(null);
  const [likeKey, setLikeKey] = useState<string | null>(null);
  const [limit, setLimit] = useState(PAGE);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { setLeague(""); setTeam(""); setMinutes(""); setFamily(null); setLikeKey(null); }, [group]);
  useEffect(() => { setLimit(PAGE); }, [group, league, team, minutes, family]);
  useEffect(() => {
    if (data) document.title = `${data.labels.words.gems_title} — ${UI.site}`;
  }, [data]);
  if (failed) return <p className="pt-10 text-ink-2">{UI.error}</p>;
  if (!data) return <p className="pt-10 text-ink-3">{UI.loading}</p>;
  const { gems, labels } = data;
  const w = labels.words;
  const scored = gems.families.filter((f) => f.definition !== null);
  const withheld = gems.families.filter((f) => f.definition === null);
  const by = family && gems.order[family] ? family : scored[0].family;
  // Filters by exported keys only; the order is the exporter's.
  const shown = gems.order[by].map((i) => gems.rows[i]).filter((r) =>
    (!league || String(r.competition_id) === league) && (!team || String(r.team_id) === team)
    && (!minutes || r.minutes_tiers.includes(minutes)));
  const teams = gems.teams.filter((t) => !league || String(t.competition_id) === league);
  return (
    <article className="pt-8">
      <h1 ref={heading} tabIndex={-1} className="text-[24px] font-semibold leading-[30px] tracking-[-0.01em] outline-none">{w.gems_title}</h1>
      <p className="mt-1 max-w-[720px] text-ink-2">{w.gems_lead}</p>

      <nav aria-label={UI.gems.position} className="mt-4 flex flex-wrap gap-2">
        {GROUPS.map((g) => (
          <a key={g} href={href.gems(g)} aria-current={g === group ? "page" : undefined}
             className={`rounded-full border px-3 py-1 text-[13px] no-underline ${g === group ? "border-ink bg-ink text-surface-0" : "border-line text-ink-2 hover:text-ink"}`}>
            {labels.groups[g].plural}
          </a>
        ))}
      </nav>

      <div className="mt-4 max-w-md">
        <div className="mb-1 text-[13px] text-ink-2" aria-hidden="true">{UI.gems.likeLabel}</div>
        <SearchBox key={group} group={group} label={UI.gems.likeLabel} onPick={(e) => setLikeKey(e.key)} />
      </div>
      {likeKey && <Like gems={gems} likeKey={likeKey} labels={labels} scored={scored} onClear={() => setLikeKey(null)} />}

      <div className="mt-6 flex flex-wrap items-end gap-3 text-[13px]">
        <label className="flex flex-col gap-1 text-ink-2">{UI.gems.orderBy}
          <select className={select} value={by} onChange={(e) => setFamily(e.target.value)} data-filter="order">
            {scored.map((f) => <option key={f.family} value={f.family}>{f.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-ink-2">{UI.gems.league}
          <select className={select} value={league} onChange={(e) => { setLeague(e.target.value); setTeam(""); }} data-filter="league">
            <option value="">{UI.gems.allLeagues}</option>
            {gems.leagues.map((l) => <option key={l.competition_id} value={String(l.competition_id)}>{l.league}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-ink-2">{UI.gems.team}
          <select className={select} value={team} onChange={(e) => setTeam(e.target.value)} data-filter="team">
            <option value="">{UI.gems.allTeams}</option>
            {teams.map((t) => <option key={t.team_id} value={String(t.team_id)}>{t.team}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1 text-ink-2">{UI.gems.minutes}
          <select className={select} value={minutes} onChange={(e) => setMinutes(e.target.value)} data-filter="minutes">
            <option value="">{w.gems_minutes_any}</option>
            {gems.minutes_tiers.map((m) => <option key={m.value} value={m.value}>{m.text}</option>)}
          </select>
        </label>
      </div>

      {withheld.length > 0 && (
        <ul className="mt-3 space-y-1.5 text-[12px] leading-4">
          {withheld.map((f) => (
            <li key={f.family} className="flex flex-wrap items-center gap-2" data-withheld-family={f.family}>
              <Chip kind="withheld">{w.chip_withheld}</Chip>
              <span className="font-medium">{f.label}</span>
              <NotePopover short={f.withheld_short!} full={f.withheld_full!} />
            </li>
          ))}
        </ul>
      )}

      <p className="mt-4 text-[12px] leading-4 text-ink-3" aria-live="polite">
        {UI.gems.count(shown.length, gems.rows.length)}. {w.gems_sort_note}
      </p>
      <div className="mt-2 overflow-x-auto rounded-lg border border-line bg-surface-1" tabIndex={0} role="region" aria-label={w.gems_title}>
        <table className="w-full border-collapse text-[13px] leading-[18px]" data-table="gems">
          <thead className="text-[12px] text-ink-2">
            <tr className="border-b border-line">
              <th className="sticky left-0 bg-surface-1 px-2.5 py-2 text-left font-semibold">{UI.gems.player}</th>
              <th className="px-2.5 py-2 text-right font-semibold">{w.column_minutes}</th>
              {scored.map((f) => (
                <th key={f.family} className="px-2.5 py-2 text-right font-semibold"
                    aria-sort={f.family === by ? "descending" : "none"}>
                  <button type="button" onClick={() => setFamily(f.family)} data-order={f.family}
                          className={`whitespace-nowrap ${f.family === by ? "text-ink underline underline-offset-2" : "text-ink-2 hover:text-ink"}`}>
                    {f.label}{f.family === by && <span aria-hidden="true"> ↓</span>}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.length === 0 ? (
              <tr><td colSpan={scored.length + 2} className="px-2.5 py-3 text-ink-2">{UI.gems.none}</td></tr>
            ) : shown.slice(0, limit).map((r) => (
              <tr key={r.key} className="border-b border-line last:border-b-0" data-key={r.key}>
                <PlayerCell row={r} labels={labels} />
                <td className="num px-2.5 py-1.5 text-right">{r.minutes_text}</td>
                <ScoreCells row={r} scored={scored} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {shown.length > limit && (
        <button type="button" onClick={() => setLimit((n) => n + PAGE)} data-more
                className="mt-3 rounded-md border border-line bg-surface-1 px-3 py-1.5 text-[13px] text-ink hover:bg-surface-2">
          {UI.gems.more(Math.min(PAGE, shown.length - limit))}
        </button>
      )}
      <ul className="mt-3 space-y-1 text-[12px] leading-4 text-ink-3">
        {scored.map((f) => <li key={f.family}><span className="font-medium text-ink-2">{f.label}:</span> {f.definition}</li>)}
      </ul>
    </article>
  );
}
