// Interface chrome only (navigation, controls, empty states). Every statement about data —
// statuses, reasons, withheld texts, phrases — comes from the export (labels.json and the
// files), never from here (CLAUDE.md, Design).
export const UI = {
  site: "ElevenIQ",
  nav: { similar: "Find similar players", gems: "Gem Finder", methodology: "Methodology" },
  search: {
    label: "Search players",
    placeholder: "Player name or nickname…",
    none: "No player found",
    hint: "Type at least 2 letters",
  },
  theme: { label: "Theme", system: "System", light: "Light", dark: "Dark" },
  home: {
    title: "Find similar players",
    lead: "Search a player from the 2015/16 season to see their profile and the players most like them in the same position.",
  },
  season: "Season 2015/16",
  registry: "Registry",
  attributionFallback: "Data: StatsBomb Open Data",
  loading: "Loading…",
  notFound: "Page not found",
  back: "Back to search",
  error: "The data could not be loaded.",
};
