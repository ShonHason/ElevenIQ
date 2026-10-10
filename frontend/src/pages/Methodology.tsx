// The Methodology page (plan 006 simple mode, W5): plain sections from the export's words table
// (labels.json methodology); nothing is written here.
import { useEffect } from "react";
import { getLabels } from "../api";
import { UI } from "../lib/ui-text";
import { useData } from "../lib/useData";

export function Methodology() {
  const { data, failed } = useData(() => getLabels(), []);
  useEffect(() => {
    if (data) document.title = `${data.methodology.title} — ${UI.site}`;
  }, [data]);
  if (failed) return <p className="pt-10 text-ink-2">{UI.error}</p>;
  if (!data) return <p className="pt-10 text-ink-3">{UI.loading}</p>;
  const m = data.methodology;
  return (
    <article className="mx-auto max-w-[680px] pt-8">
      <h1 tabIndex={-1} className="text-[24px] font-semibold leading-[30px] tracking-[-0.01em] outline-none">{m.title}</h1>
      {m.sections.map((s) => (
        <section key={s.title} className="mt-6" data-section={s.title}>
          <h2 className="text-[17px] font-semibold leading-6 tracking-[-0.005em]">{s.title}</h2>
          {s.paragraphs.map((p) => <p key={p} className="mt-2 text-ink-2">{p}</p>)}
        </section>
      ))}
      <p className="mt-8 text-[12px] leading-4 text-ink-3">{data.words.attribution}</p>
    </article>
  );
}
