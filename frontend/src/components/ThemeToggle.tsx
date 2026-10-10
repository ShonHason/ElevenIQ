import { Monitor, Moon, Sun } from "lucide-react";
import { useState } from "react";
import { applyTheme, readTheme, type Theme } from "../lib/theme";
import { UI } from "../lib/ui-text";

const OPTIONS: { value: Theme; label: string; Icon: typeof Sun }[] = [
  { value: "system", label: UI.theme.system, Icon: Monitor },
  { value: "light", label: UI.theme.light, Icon: Sun },
  { value: "dark", label: UI.theme.dark, Icon: Moon },
];

// Three pressed-state buttons (a simple toggle group; no roving focus needed).
export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  return (
    <div role="group" aria-label={UI.theme.label}
         className="flex rounded-md border border-line bg-surface-1 p-0.5">
      {OPTIONS.map(({ value, label, Icon }) => (
        <button key={value} type="button" aria-pressed={theme === value}
                aria-label={label} title={label}
                onClick={() => { setTheme(value); applyTheme(value); }}
                className={`grid h-7 w-7 place-items-center rounded-[5px] text-ink-2 transition-colors duration-100 hover:text-ink ${theme === value ? "bg-surface-2 text-ink" : ""}`}>
          <Icon size={15} aria-hidden="true" />
        </button>
      ))}
    </div>
  );
}
