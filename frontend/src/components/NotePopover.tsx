// A cell's short text with its full text in a popover (DESIGN decision 7; Base UI Popover).
import { Popover } from "@base-ui/react/popover";
import { Info } from "lucide-react";

export function NotePopover({ short, full }: { short: string; full: string }) {
  if (short === full) return <span className="text-ink-3">{short}</span>;
  return (
    <Popover.Root>
      <Popover.Trigger className="inline-flex items-center gap-1 text-left text-ink-3 underline decoration-dotted underline-offset-2 hover:text-ink-2">
        {short}<Info size={12} aria-hidden="true" />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={6}>
          <Popover.Popup className="max-w-xs rounded-lg border border-line bg-surface-1 px-3 py-2 text-[13px] leading-[18px] text-ink shadow-[var(--shadow)] transition-[opacity,transform] duration-[120ms] ease-out data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 data-[starting-style]:scale-[0.98]">
            {full}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
