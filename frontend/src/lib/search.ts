// Search on the exported, accent-folded search key (export/display.fold, ADR 005 §3): prefix
// matches of any word first, then substring matches, each in the index's own order. No
// statistic is used to order results.
import type { SearchIndexJson } from "../types/contract";

export type Entry = SearchIndexJson["entries"][number];

/** The same folding as the exporter's display.fold: NFKD, drop combining marks, casefold. */
export function fold(text: string): string {
  return text.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase().replace(/ß/g, "ss").trim();
}

export function search(entries: Entry[], query: string, limit = 10): Entry[] {
  const q = fold(query);
  if (q.length < 2) return [];
  const prefix: Entry[] = [];
  const inner: Entry[] = [];
  for (const e of entries) {
    const k = e.search_key;
    if (k.startsWith(q) || k.includes(` ${q}`)) prefix.push(e);
    else if (k.includes(q)) inner.push(e);
  }
  return [...prefix, ...inner].slice(0, limit);
}
