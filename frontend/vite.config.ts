import { cpSync, createReadStream, existsSync, statSync } from "node:fs";
import { extname, join, normalize, resolve, sep } from "node:path";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The data the site reads (/data/…): the FULL export by default (export/v1, built by
// `npm run local`); ELEVENIQ_DATA points elsewhere (the Playwright tests use the fixture,
// tests/fixtures/web). Served directly, never copied into public/, so a test run can no longer
// replace the data the dev server shows. A build copies it into dist/data.
const DATA_DIR = resolve(import.meta.dirname, process.env.ELEVENIQ_DATA ?? "../export/v1");
const TYPES: Record<string, string> = { ".json": "application/json; charset=utf-8" };

function serveData(): Plugin {
  const serve = (req: { url?: string }, res: import("node:http").ServerResponse, next: () => void) => {
    const url = (req.url ?? "").split("?")[0];
    const m = /^\/data\/(.+)$/.exec(url);              // the root /data/ only (base "./" is the root)
    if (!m) return next();
    let rel: string;
    try { rel = decodeURIComponent(m[1]); } catch { res.statusCode = 400; res.end("bad path"); return; }
    const file = normalize(join(DATA_DIR, rel));
    if (!file.startsWith(DATA_DIR + sep) || !existsSync(file) || !statSync(file).isFile()) {
      res.statusCode = 404;
      res.end(existsSync(join(DATA_DIR, "manifest.json")) ? "not found"
        : `no export at ${DATA_DIR}: run \`npm run local\` (it builds the export first)`);
      return;
    }
    res.setHeader("Content-Type", TYPES[extname(file)] ?? "application/octet-stream");
    createReadStream(file).pipe(res);
  };
  return {
    name: "eleveniq-serve-data",
    configureServer(server) { server.middlewares.use(serve); },
    configurePreviewServer(server) { server.middlewares.use(serve); },
  };
}

// On `vite build` only: the export travels with the site in dist/data.
function bundleData(): Plugin {
  return {
    name: "eleveniq-bundle-data",
    apply: "build",
    writeBundle() {
      if (!existsSync(join(DATA_DIR, "manifest.json"))) {
        throw new Error(`no export at ${DATA_DIR}: build it first (npm run local, or build_export)`);
      }
      cpSync(DATA_DIR, resolve(import.meta.dirname, "dist", "data"), { recursive: true });
    },
  };
}

// base "./": the static build works under any sub-path (Cloudflare Pages).
export default defineConfig({
  base: "./",
  plugins: [react(), tailwindcss(), serveData(), bundleData()],
  test: { include: ["src/**/*.test.ts"] },
});
