// The Profile shape (plan 006 W4 rules): one vertex per DNA family of the group on a 0–100 radial
// scale (the exported dimension score). A withheld family is an open, dashed marker on the rim
// labelled with the withheld chip, never drawn as 0, and the polygon closes over the scored
// vertices only. No overall number. The 50 ring is "around average". Up to two series (compare).
// Geometry is layout only: the scores themselves are the exported numbers.
import type { PlayersKeyJson } from "../types/contract";

type Dim = NonNullable<PlayersKeyJson["dna"]>["dimensions"][number];
export type Series = { key: string; name: string; dims: Dim[] };

// Room around the circle for the family labels (up to ~20 characters at the sides).
const SIZE = 480, C = SIZE / 2, R = 120;
const STROKES = ["var(--ink)", "var(--p0)"];

function point(i: number, n: number, value: number) {
  const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
  const r = (R * value) / 100;
  return [C + r * Math.cos(a), C + r * Math.sin(a)] as const;
}

export function ProfileShape({ series, labels }: {
  series: Series[];
  labels: { title: string; note: string; withheld: string; average: string };
}) {
  const families = series[0].dims;
  const n = families.length;
  return (
    <figure className="m-0">
      <figcaption>
        <h2 className="text-[17px] font-semibold leading-6 tracking-[-0.005em]">{labels.title}</h2>
        <p className="mt-0.5 text-[12px] leading-4 text-ink-3">{labels.note}</p>
      </figcaption>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" className="mx-auto mt-2 block w-full max-w-[440px]"
           aria-label={`${labels.title}: ${series.map((s) => s.name).join(", ")}`}>
        {[25, 50, 75, 100].map((ring) => (
          <polygon key={ring} fill="none" stroke="var(--line)" strokeWidth={ring === 50 ? 1.5 : 1}
                   strokeDasharray={ring === 50 ? undefined : "2 3"}
                   points={families.map((_, i) => point(i, n, ring).join(",")).join(" ")} />
        ))}
        <text x={C + 4} y={C - (R * 50) / 100 - 4} fontSize="10" fill="var(--ink-3)">{labels.average}</text>
        {families.map((d, i) => {
          const [x, y] = point(i, n, 100);
          return <line key={d.family} x1={C} y1={C} x2={x} y2={y} stroke="var(--line)" />;
        })}
        {series.map((s, si) => {
          const scored = s.dims.map((d, i) => ({ d, i })).filter(({ d }) => d.score !== null);
          return (
            <g key={s.key}>
              <polygon fill={si === 0 ? "color-mix(in srgb, var(--ink) 8%, transparent)" : "none"}
                       stroke={STROKES[si]} strokeWidth={2} strokeDasharray={si === 1 ? "6 4" : undefined}
                       points={scored.map(({ d, i }) => point(i, n, d.score!).join(",")).join(" ")} />
              {s.dims.map((d, i) => {
                if (d.score === null) {
                  const [x, y] = point(i, n, 100);
                  return si === 0 ? <circle key={d.family} cx={x} cy={y} r={5} fill="var(--surface-1)"
                                            stroke="var(--ink-3)" strokeDasharray="2 2" data-withheld={d.family} /> : null;
                }
                const [x, y] = point(i, n, d.score);
                return <circle key={d.family} cx={x} cy={y} r={4} fill={STROKES[si]}
                               data-src={`players/${s.key}.json#dna.dimensions.${d.family}.score`}
                               data-value={d.score} />;
              })}
            </g>
          );
        })}
        {families.map((d, i) => {
          const [x, y] = point(i, n, 128);
          const anchor = Math.abs(x - C) < 8 ? "middle" : x > C ? "start" : "end";
          return (
            <text key={d.family} x={x} y={y} textAnchor={anchor} dominantBaseline="middle" fontSize="11.5" fill="var(--ink-2)">
              <tspan x={x} dy="-0.4em" fontWeight={600} fill="var(--ink)">{d.label}</tspan>
              {d.score === null ? <tspan x={x} dy="1.25em">{labels.withheld}</tspan>
                : series.map((s, si) => (
                  <tspan key={s.key} x={si === 0 ? x : undefined} dy={si === 0 ? "1.25em" : undefined}
                         dx={si === 0 ? undefined : 6} fill={STROKES[si]} className="num">{s.dims[i].score_text}</tspan>
                ))}
            </text>
          );
        })}
      </svg>
      {/* The chart's content for screen readers: every family's score, or withheld. */}
      <ul className="sr-only">
        {families.map((d, i) => (
          <li key={d.family}>
            {d.label}: {d.score === null ? labels.withheld : series.map((s) => `${s.name} ${s.dims[i].score_text}`).join(", ")}
          </li>
        ))}
      </ul>
    </figure>
  );
}
