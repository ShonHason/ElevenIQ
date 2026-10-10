import { useEffect, useState } from "react";
import { Footer } from "./components/Footer";
import { Header } from "./components/Header";
import { ContractError, getManifest } from "./api";
import { useRoute } from "./lib/router";
import { UI } from "./lib/ui-text";
import { Home } from "./pages/Home";
import { Compare } from "./pages/Compare";
import { Gems } from "./pages/Gems";
import { Methodology } from "./pages/Methodology";
import { Player } from "./pages/Player";

export function App() {
  const route = useRoute();
  const [contractError, setContractError] = useState<string | null>(null);
  useEffect(() => {
    document.title = route.name === "home" ? `${UI.site} — ${UI.home.title}` : UI.site;
  }, [route]);
  useEffect(() => {
    getManifest().catch((e) => setContractError(e instanceof ContractError ? e.message : UI.error));
  }, []);
  return (
    <div className="flex min-h-screen flex-col">
      <Header route={route} />
      <main className="mx-auto w-full max-w-[1200px] flex-1 px-4 md:px-6">
        {contractError ? <p role="alert" className="pt-10 text-ink-2">{contractError}</p>
          : route.name === "home" ? <Home />
          : route.name === "player" ? <Player playerKey={route.key} />
          : route.name === "compare" ? <Compare a={route.a} b={route.b} />
          : route.name === "gems" ? <Gems group={route.group} />
          : route.name === "methodology" ? <Methodology />
          : <section className="pt-10"><h1 className="text-[17px] font-semibold">{UI.notFound}</h1>
              <a href="#/" className="mt-2 inline-block">{UI.back}</a></section>}
      </main>
      <Footer />
    </div>
  );
}
