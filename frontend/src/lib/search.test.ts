import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { fold, search, type Entry } from "./search";

const fixture = JSON.parse(readFileSync(new URL("../../../tests/fixtures/web/search-index.json", import.meta.url), "utf8"));
const entries: Entry[] = fixture.entries;

describe("fold (mirrors export/display.fold)", () => {
  it("drops accents and case", () => {
    expect(fold("  N'Golo Kanté ")).toBe("n'golo kante");
    expect(fold("Alarcón")).toBe("alarcon");
    expect(fold("Özil")).toBe("ozil");
  });
  it("matches Python's display.fold on letters NFKD does not split (values from Python)", () => {
    const py: Record<string, string> = {
      "Ødegaard": "ødegaard", "Łukasz": "łukasz", "Đoković": "đokovic", "Müller": "muller",
      "Straße": "strasse", "Çağlar Söyüncü": "caglar soyuncu", "  Sergio Busquets i Burgos ": "sergio busquets i burgos",
    };
    for (const [raw, want] of Object.entries(py)) expect(fold(raw)).toBe(want);
  });
  it("matches the exporter's search keys for every fixture entry", () => {
    for (const e of entries) {
      const want = [...new Set([fold(e.display_name), fold(e.name)])].join(" ");
      expect(e.search_key).toBe(want);
    }
  });
});

describe("search", () => {
  it("finds Kanté without the accent and Isco by nickname", () => {
    expect(search(entries, "kante").map((e) => e.key)).toContain("3961-22-2-27");
    expect(search(entries, "Isco")[0].key).toBe("4926-220-11-27");
    expect(search(entries, "alarcon").map((e) => e.key)).toContain("4926-220-11-27");
  });
  it("needs two letters and caps the list", () => {
    expect(search(entries, "k")).toEqual([]);
    expect(search(entries, "a", 3).length).toBe(0);
    expect(search(entries, "an", 3).length).toBeLessThanOrEqual(3);
  });
  it("puts word-prefix matches before inner matches, otherwise index order", () => {
    const fake = (key: string, k: string) => ({ ...entries[0], key, search_key: k });
    const list = [fake("1-1-1-27", "amorim"), fake("2-1-1-27", "wes morgan"), fake("3-1-1-27", "moreno x")];
    expect(search(list, "mor").map((e) => e.key)).toEqual(["2-1-1-27", "3-1-1-27", "1-1-1-27"]);
  });
});
