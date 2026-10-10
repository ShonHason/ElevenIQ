// Theme: follows the system; the toggle stores light / dark (DESIGN.md §3.1). Storage can be
// unavailable (private windows): every access is guarded and the page works without it.
export type Theme = "system" | "light" | "dark";
const KEY = "eleveniq-theme";

export function readTheme(): Theme {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch { return "system"; }
}

export function applyTheme(t: Theme): void {
  const root = document.documentElement;
  if (t === "system") root.removeAttribute("data-theme");
  else root.setAttribute("data-theme", t);
  try {
    if (t === "system") window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, t);
  } catch { /* storage unavailable: the choice lasts for this page only */ }
}
