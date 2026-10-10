// The Profile shape (plan 006 W4 rules): one vertex per DNA family of the group on a 0–100 radial
// scale (the exported dimension score). A withheld family is an open, dashed marker on the rim
// labelled with the withheld chip, never drawn as 0, and the polygon closes over the scored
// vertices only. No overall number. The 50 ring is "around average". Up to two series (compare).
// Geometry is layout only: the scores themselves are the exported numbers.
import { Popover } from "@base-ui/react/popover";
import type { PlayersKeyJson } from "../types/contract";

type Dim = NonNullable<PlayersKeyJson["dna"]>["dimensions"][number];
export type Series = { key: string; name: string; dims: Dim[] };

// Room around the circle for the family labels (up to ~20 characters at the sides).
const SIZE = 780, C = SIZE / 2, R = 180;
// The two series' styles, shared with the compare page (solid ink, dashed orange).
export const STROKES = ["var(--ink)", "var(--p0)"];
export const DASHES = [undefined, "6 4"];

export function Swatch({ k, width = 18 }: { k: number; width?: number }) {
  return (
    <svg width={width} height="8" aria-hidden="true" className="mr-1.5 inline-block align-middle">
      <line x1="0" y1="4" x2={width} y2="4" stroke={STROKES[k]} strokeWidth="2.5" strokeDasharray={DASHES[k]} />
    </svg>
  );
}

function point(i: number, n: number, value: number) {
  const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
  const r = (R * value) / 100;
  return [C + r * Math.cos(a), C + r * Math.sin(a)] as const;
}

export function ProfileShape({ series, labels, definitions = {} }: {
  series: Series[];
  labels: { title: string; note: string; withheld: string; average: string };
  // Family -> what its score measures (groups/<g>.json family_definitions); hover or tap a label.
  definitions?: Record<string, string>;
}) {
  if (series.length < 1 || series.length > STROKES.length) throw new Error("one or two series");
  const families = series[0].dims;
  const n = families.length;
  return (
    <figure className="m-0">
      <figcaption>
        <h2 className="text-[17px] font-semibold leading-6 tracking-[-0.005em]">{labels.title}</h2>
        <p className="mt-0.5 text-[12px] leading-4 text-ink-3">{labels.note}</p>
        {series.length > 1 && (
          <ul className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[13px]">
            {series.map((s, si) => (
              <li key={s.key} className="flex items-center gap-2">
                <Swatch k={si} width={28} />
                {s.name}
              </li>
            ))}
          </ul>
        )}
      </figcaption>
      <svg viewBox={`0 0 ${SIZE} ${SIZE}`} role="group" className="mx-auto mt-2 block w-full max-w-[780px]"
           aria-label={`${labels.title}: ${series.map((s) => s.name).join(", ")}`}>
        {[25, 50, 75, 100].map((ring) => (
          <polygon key={ring} fill="none" stroke="var(--line)" strokeWidth={ring === 50 ? 1.5 : 1}
                   strokeDasharray={ring === 50 ? undefined : "2 3"}
                   points={families.map((_, i) => point(i, n, ring).join(",")).join(" ")} />
        ))}
        <text x={C + 5} y={C - (R * 50) / 100 - 5} className="shape-ring" fill="var(--ink-2)">{labels.average}</text>
        {families.map((d, i) => {
          const [x, y] = point(i, n, 100);
          return <line key={d.family} x1={C} y1={C} x2={x} y2={y} stroke="var(--line)" />;
        })}
        {series.map((s, si) => {
          const scored = s.dims.map((d, i) => ({ d, i })).filter(({ d }) => d.score !== null);
          return (
            <g key={s.key}>
              <polygon fill={si === 0 ? "color-mix(in srgb, var(--ink) 8%, transparent)" : "none"}
                       stroke={STROKES[si]} strokeWidth={2} strokeDasharray={DASHES[si]}
                       points={scored.map(({ d, i }) => point(i, n, d.score!).join(",")).join(" ")} />
              {s.dims.map((d, i) => {
                if (d.score === null) {
                  const [x, y] = point(i, n, 100);
                  return si === 0 ? <circle key={d.family} cx={x} cy={y} r={7} fill="var(--surface-1)"
                                            stroke="var(--ink-3)" strokeDasharray="2 2" data-withheld={d.family} /> : null;
                }
                const [x, y] = point(i, n, d.score);
                return <circle key={d.family} cx={x} cy={y} r={5.5} fill={STROKES[si]}
                               data-src={`players/${s.key}.json#dna.dimensions.${d.family}.score`}
                               data-value={d.score} />;
              })}
            </g>
          );
        })}
        {families.map((d, i) => {
          const [x, y] = point(i, n, 122);
          const anchor = Math.abs(x - C) < 8 ? "middle" : x > C ? "start" : "end";
          const def = definitions[d.family];
          const name = <tspan x={x} dy="-0.4em" fontWeight={600} fill="var(--ink)"
                              textDecoration={def ? "underline dotted" : undefined}>{d.label}</tspan>;
          const text = (
            <text x={x} y={y} textAnchor={anchor} dominantBaseline="middle" className="shape-label" fill="var(--ink-2)">
              {name}
              {d.score === null ? <tspan x={x} dy="1.25em">{labels.withheld}</tspan>
                : series.map((s, si) => (
                  <tspan key={s.key} x={si === 0 ? x : undefined} dy={si === 0 ? "1.25em" : undefined}
                         dx={si === 0 ? undefined : 6} fill={STROKES[si]} className="num">{s.dims[i].score_text}</tspan>
                ))}
            </text>
          );
          if (!def) return <g key={d.family}>{text}</g>;
          // A transparent hit area over the label's two lines (a tap between glyphs still opens
          // the definition; a larger target on phones). Sized in em of the label font.
          const shift = anchor === "end" ? "-100%" : anchor === "middle" ? "-50%" : "0";
          const label = (
            <g className="shape-label cursor-pointer">
              <rect x={x} y={y} width={`${Math.max(d.label.length, 4) * 0.6}em`} height="2.4em" fill="transparent"
                    style={{ transformBox: "fill-box", transform: `translate(${shift}, -40%)` }} />
              {text}
            </g>
          );
          return (
            <Popover.Root key={d.family}>
              <Popover.Trigger nativeButton={false} openOnHover delay={150} render={label}
                               aria-label={`${d.label}: ${def}`} data-family-def={d.family} />
              <Popover.Portal>
                <Popover.Positioner sideOffset={6}>
                  <Popover.Popup className="max-w-xs rounded-lg border border-line bg-surface-1 px-3 py-2 text-[13px] leading-[18px] text-ink shadow-[var(--shadow)] transition-[opacity,transform] duration-[120ms] ease-out data-[ending-style]:opacity-0 data-[starting-style]:opacity-0">
                    <p className="m-0"><span className="font-semibold">{d.label}:</span> {def}</p>
                  </Popover.Popup>
                </Popover.Positioner>
              </Popover.Portal>
            </Popover.Root>
          );
        })}
      </svg>
      {/* The chart's content for screen readers: every family's score, or withheld, and its
          definition. The svg is a group, not an img: its family labels are buttons. */}
      <ul className="sr-only">
        {families.map((d, i) => (
          <li key={d.family}>
            {d.label}: {d.score === null ? labels.withheld : series.map((s) => `${s.name} ${s.dims[i].score_text}`).join(", ")}
            {definitions[d.family] && <>. {definitions[d.family]}</>}
          </li>
        ))}
      </ul>
    </figure>
  );
}
