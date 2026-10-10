// A combobox (WAI-ARIA 1.2 pattern): type, arrow keys, Enter opens the player (#/player/<key>).
import { Search } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { getSearchIndex } from "../api";
import { href } from "../lib/router";
import { search, type Entry } from "../lib/search";
import { UI } from "../lib/ui-text";

// `group` limits the results to one position group's profiled rows (the compare picker: same
// group only, user 2026-10-09); `onPick` replaces navigation to the player page.
export function SearchBox({ autoFocus = false, size = "md", group, exclude, onPick, label = UI.search.label }: {
  autoFocus?: boolean; size?: "md" | "lg"; group?: string; exclude?: string;
  onPick?: (e: Entry) => void; label?: string;
}) {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [failed, setFailed] = useState(false);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => { getSearchIndex().then((d) => setEntries(d.entries)).catch(() => setFailed(true)); }, []);
  const pool = useMemo(() => group ? entries.filter((e) => e.group === group && e.profile === "dna" && e.key !== exclude) : entries,
                       [entries, group, exclude]);
  const results = useMemo(() => search(pool, query), [pool, query]);
  useEffect(() => setActive(0), [query]);

  const go = (e: Entry) => {
    if (onPick) onPick(e); else window.location.hash = href.player(e.key);
    setOpen(false);
    setQuery("");
    input.current?.blur();
  };
  const onKey = (ev: React.KeyboardEvent) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); setOpen(true); setActive((a) => Math.max(0, Math.min(a + 1, results.length - 1))); }
    else if (ev.key === "ArrowUp") { ev.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
    else if (ev.key === "Enter" && results[active]) { ev.preventDefault(); go(results[active]); }
    else if (ev.key === "Escape") { setOpen(false); }
  };
  const big = size === "lg";
  const show = open && query.trim().length > 0;
  const listed = show && !failed && query.trim().length >= 2 && results.length > 0;
  const message = failed ? UI.error : query.trim().length < 2 ? UI.search.hint : UI.search.none;

  return (
    <div className="relative w-full">
      <label className="sr-only" htmlFor={`${listId}-input`}>{label}</label>
      <Search aria-hidden="true" size={big ? 18 : 15}
              className={`pointer-events-none absolute top-1/2 -translate-y-1/2 text-ink-3 ${big ? "left-3.5" : "left-2.5"}`} />
      <input id={`${listId}-input`} ref={input} type="search" role="combobox" autoComplete="off"
             spellCheck={false} autoFocus={autoFocus}
             aria-expanded={listed} aria-controls={listed ? listId : undefined} aria-autocomplete="list"
             aria-activedescendant={listed && results[active] ? `${listId}-${active}` : undefined}
             placeholder={UI.search.placeholder} value={query}
             onChange={(e) => { setQuery(e.target.value); setOpen(true); }}
             onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 120)}
             onKeyDown={onKey}
             className={`w-full rounded-md border border-line bg-surface-1 text-ink placeholder:text-ink-3 ${big ? "h-12 pl-10 pr-3 text-[15px]" : "h-8 pl-8 pr-2"}`} />
      {show && !listed && (
        <div role="status" className="absolute left-0 right-0 z-30 mt-1 rounded-lg border border-line bg-surface-1 px-3 py-2 text-ink-3 shadow-[var(--shadow)]">
          {message}
        </div>
      )}
      {listed && (
        <ul id={listId} role="listbox" aria-label={label}
            className="absolute left-0 right-0 z-30 mt-1 max-h-96 overflow-auto rounded-lg border border-line bg-surface-1 py-1 shadow-[var(--shadow)]">
          {results.map((e, i) => (
            <li key={e.key} id={`${listId}-${i}`} role="option" aria-selected={i === active}
                onMouseDown={(ev) => { ev.preventDefault(); go(e); }} onMouseEnter={() => setActive(i)}
                className={`cursor-pointer px-3 py-1.5 ${i === active ? "bg-surface-2" : ""}`}>
              <div className="font-medium text-ink">{e.display_name}</div>
              <div className="flex flex-wrap gap-x-3 text-[12px] leading-4 text-ink-3">
                <span>{e.team}</span><span>{e.league}</span><span>{e.group}</span>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
