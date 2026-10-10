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
  table: { metric: "Metric", notes: "Notes", metrics: "Metrics" },
  compare: "Compare",
  compareWith: "Compare with a player in the same position",
  notFound: "Page not found",
  back: "Back to search",
  error: "The data could not be loaded.",
  gems: {
    position: "Position",
    league: "League", allLeagues: "All leagues",
    team: "Team", allTeams: "All teams",
    minutes: "Minutes played",
    orderBy: "Order by",
    player: "Player",
    count: (n: number, total: number) => `${n} of ${total} players`,
    likeLabel: "Players like…",
    clearLike: "Clear",
    none: "No player matches these filters.",
    more: (n: number) => `Show ${n} more`,
  },
};
