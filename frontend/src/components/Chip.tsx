import { CircleSlash, FlaskConical, TriangleAlert, Users } from "lucide-react";
import type { ReactNode } from "react";

const ICONS = { withheld: CircleSlash, caution: TriangleAlert, same_team: Users, provisional: FlaskConical };

// DESIGN.md §6: icon + short text; colour never alone.
export function Chip({ kind, children, title }: { kind?: keyof typeof ICONS; children: ReactNode; title?: string }) {
  const Icon = kind ? ICONS[kind] : null;
  return (
    <span title={title}
          className={`inline-flex h-[22px] items-center gap-1 whitespace-nowrap rounded-full border border-line bg-surface-2 px-2 text-[12px] leading-4 ${kind === "caution" ? "text-caution" : "text-ink-2"}`}>
      {Icon && <Icon size={13} aria-hidden="true" />}
      {children}
    </span>
  );
}
