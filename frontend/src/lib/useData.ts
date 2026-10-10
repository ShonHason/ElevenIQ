// Load-once hooks over the data module, with a cancel guard (a late answer is ignored).
import { useEffect, useState } from "react";

export function useData<T>(load: () => Promise<T>, deps: unknown[]): { data: T | null; failed: boolean } {
  const [state, setState] = useState<{ data: T | null; failed: boolean }>({ data: null, failed: false });
  useEffect(() => {
    let current = true;
    setState({ data: null, failed: false });
    load().then((d) => { if (current) setState({ data: d, failed: false }); })
      .catch((e) => { console.error(e); if (current) setState({ data: null, failed: true }); });
    return () => { current = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}
