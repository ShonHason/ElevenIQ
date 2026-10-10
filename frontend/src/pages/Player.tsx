// W2 stub: the identity strip only; the profile shape, tables and neighbours are W3/W4.
import { useEffect, useRef, useState } from "react";
import { getPlayer } from "../api";
import type { PlayersKeyJson } from "../types/contract";
import { UI } from "../lib/ui-text";

export function Player({ playerKey }: { playerKey: string }) {
  const [doc, setDoc] = useState<PlayersKeyJson | null>(null);
  const [failed, setFailed] = useState(false);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    let current = true;                       // a late answer for another player is ignored
    setDoc(null); setFailed(false);
    getPlayer(playerKey).then((d) => { if (current) setDoc(d); })
      .catch(() => { if (current) setFailed(true); });
    return () => { current = false; };
  }, [playerKey]);
  // Once the page has its heading: title and focus (the route change fires before the data).
  useEffect(() => {
    if (!doc) return;
    document.title = `${doc.identity.display_name} — ${UI.site}`;
    heading.current?.focus();
  }, [doc]);
  if (failed) return <p className="pt-10 text-ink-2">{UI.error}</p>;
  if (!doc) return <p className="pt-10 text-ink-3">{UI.loading}</p>;
  const i = doc.identity;
  return (
    <section className="pt-8">
      <h1 ref={heading} tabIndex={-1} className="text-[24px] font-semibold leading-[30px] tracking-[-0.01em] outline-none">{i.display_name}</h1>
      <div className="mt-1 flex flex-wrap gap-x-4 text-ink-2">
        <span>{i.team}</span><span>{i.league}</span><span>{i.group_text}</span>
        <span><span className="num" data-src={`players/${i.key}.json#identity.minutes_text`}>{i.minutes_text}</span> min</span>
      </div>
    </section>
  );
}
