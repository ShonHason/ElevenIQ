// A small hash router: URLs carry ids only (plan 006 decision 9), e.g. #/player/3961-22-2-27.
// Hash URLs work on any static host without rewrites.
import { useEffect, useState } from "react";

export type Route =
  | { name: "home" }
  | { name: "player"; key: string }
  | { name: "compare"; a: string; b: string }
  | { name: "gems"; group: string | null }
  | { name: "methodology" }
  | { name: "not-found" };

const KEY = /^[0-9]+-[0-9]+-[0-9]+-[0-9]+$/;

export function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts.length === 0) return { name: "home" };
  if (parts[0] === "player" && parts.length === 2 && KEY.test(parts[1])) return { name: "player", key: parts[1] };
  if (parts[0] === "compare" && parts.length === 3 && KEY.test(parts[1]) && KEY.test(parts[2]))
    return { name: "compare", a: parts[1], b: parts[2] };
  if (parts[0] === "gems" && parts.length <= 2) return { name: "gems", group: parts[1] ?? null };
  if (parts[0] === "methodology" && parts.length === 1) return { name: "methodology" };
  return { name: "not-found" };
}

export const href = {
  home: () => "#/",
  player: (key: string) => `#/player/${key}`,
  compare: (a: string, b: string) => `#/compare/${a}/${b}`,
  gems: (group?: string) => (group ? `#/gems/${group}` : "#/gems"),
  methodology: () => "#/methodology",
};

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(window.location.hash));
  useEffect(() => {
    const on = () => {
      setRoute(parse(window.location.hash));
      window.scrollTo(0, 0);
      // Move focus to the new page's heading so keyboard and screen-reader users hear it.
      requestAnimationFrame(() => document.querySelector<HTMLElement>("main h1")?.focus());
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}
