import { useEffect, useState } from "react";
import { getLabels, getManifest } from "../api";
import { UI } from "../lib/ui-text";

// ADR 005 §8: attribution on every page, also when the labels cannot be loaded. The StatsBomb
// logo is added at W6, after the licence gate (the agreement read directly).
export function Footer() {
  const [text, setText] = useState({ attribution: UI.attributionFallback, portfolio: "", registry: "" });
  useEffect(() => {
    Promise.all([getLabels(), getManifest()]).then(([l, m]) => setText({
      attribution: l.words.attribution, portfolio: l.words.portfolio,
      registry: m.stamps.registry_version.slice(0, 12),
    })).catch(() => { /* the fallback attribution stays */ });
  }, []);
  return (
    <footer className="mt-16 border-t border-line">
      <div className="mx-auto flex max-w-[1200px] flex-wrap gap-x-6 gap-y-1 px-4 py-6 text-[12px] leading-4 text-ink-3 md:px-6">
        <span>{text.attribution}</span>
        {text.portfolio && <span>{text.portfolio}</span>}
        <span>{UI.season}</span>
        {text.registry && <span>{UI.registry} <span className="num">{text.registry}</span></span>}
      </div>
    </footer>
  );
}
