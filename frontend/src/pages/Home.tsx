import { SearchBox } from "../components/SearchBox";
import { UI } from "../lib/ui-text";

export function Home() {
  return (
    <section className="mx-auto max-w-[640px] pt-16 md:pt-24">
      <h1 tabIndex={-1} className="text-[24px] font-semibold leading-[30px] tracking-[-0.01em] outline-none">{UI.home.title}</h1>
      <p className="mt-2 text-ink-2">{UI.home.lead}</p>
      <div className="mt-6"><SearchBox size="lg" autoFocus /></div>
    </section>
  );
}
