import { href, type Route } from "../lib/router";
import { UI } from "../lib/ui-text";
import { SearchBox } from "./SearchBox";
import { ThemeToggle } from "./ThemeToggle";

const NAV: { label: string; to: string; match: Route["name"][] }[] = [
  { label: UI.nav.similar, to: href.home(), match: ["home", "player", "compare"] },
  { label: UI.nav.gems, to: href.gems(), match: ["gems"] },
  { label: UI.nav.methodology, to: href.methodology(), match: ["methodology"] },
];

export function Header({ route }: { route: Route }) {
  return (
    <header className="header-bar sticky top-0 z-20 border-b border-line">
      <div className="mx-auto flex max-w-[1200px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5 md:px-6">
        <a href={href.home()} className="text-[15px] font-semibold tracking-[-0.01em] text-ink no-underline">
          {UI.site}
        </a>
        <nav aria-label="Main" className="order-3 flex w-full gap-4 overflow-x-auto md:order-none md:w-auto">
          {NAV.map((n) => {
            const current = n.match.includes(route.name);
            return (
              <a key={n.label} href={n.to} aria-current={current ? "page" : undefined}
                 className={`whitespace-nowrap py-1 no-underline transition-colors duration-100 ${current ? "font-medium text-ink" : "text-ink-2 hover:text-ink"}`}>
                {n.label}
              </a>
            );
          })}
        </nav>
        <div className="ml-auto flex min-w-0 flex-1 items-center gap-3 md:max-w-xs">
          {route.name !== "home" && <SearchBox />}
          <ThemeToggle />
        </div>
      </div>
    </header>
  );
}
