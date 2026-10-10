// Small popovers (Base UI): a trigger that opens plain lines of exported text. Used for a
// metric's definition (tap on phones) and a band's exact percentile.
import { Popover } from "@base-ui/react/popover";
import type { ReactNode } from "react";

export function Details({ trigger, lines, className = "" }: { trigger: ReactNode; lines: (string | null | undefined)[]; className?: string }) {
  const body = lines.filter((l): l is string => Boolean(l));
  if (body.length === 0) return <span className={className}>{trigger}</span>;
  return (
    <Popover.Root>
      <Popover.Trigger className={`text-left underline decoration-dotted decoration-[var(--line)] underline-offset-[3px] hover:decoration-[var(--ink-3)] ${className}`}>
        {trigger}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={6}>
          <Popover.Popup className="max-w-xs space-y-1 rounded-lg border border-line bg-surface-1 px-3 py-2 text-[13px] leading-[18px] text-ink shadow-[var(--shadow)] transition-[opacity,transform] duration-[120ms] ease-out data-[ending-style]:opacity-0 data-[starting-style]:opacity-0">
            {body.map((l) => <p key={l} className="m-0">{l}</p>)}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
