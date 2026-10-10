// `npm run local`: make sure the full web export exists and is current, then start the dev
// server on it (http://localhost:5173). The export is rebuilt when it is missing, when a
// parquet it was built from changed (sha256 in its manifest stamps), or when the exporter /
// contract code is newer than it.
import { createHash } from "node:crypto";
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join, resolve } from "node:path";
import { spawnSync, spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = resolve(fileURLToPath(import.meta.url), "..", "..", "..");
const out = join(root, "export", "v1");
const manifest = join(out, "manifest.json");
const python = join(root, ".venv", "bin", "python");
const parquets = {
  season_parquet_sha256: "data/processed/player_season_stats.parquet",
  percentiles_parquet_sha256: "data/processed/player_season_percentiles.parquet",
  dna_parquet_sha256: "data/processed/player_dna.parquet",
  positions_parquet_sha256: "data/processed/player_season_positions.parquet",
  match_parquet_sha256: "data/interim/player_match_stats.parquet",
};

const sha = (f) => createHash("sha256").update(readFileSync(f)).digest("hex");
// Source files only (.py, .json): caches (__pycache__) and editor files never mark it stale.
const newest = (dir) => readdirSync(dir, { recursive: true })
  .map((f) => join(dir, String(f)))
  .filter((f) => /\.(py|json)$/.test(f) && !f.includes("__pycache__") && statSync(f).isFile())
  .reduce((t, f) => Math.max(t, statSync(f).mtimeMs), 0);

function staleReason() {
  if (!existsSync(manifest)) return "no export yet";
  const stamps = JSON.parse(readFileSync(manifest, "utf8")).stamps;
  for (const [key, rel] of Object.entries(parquets)) {
    const f = join(root, rel);
    if (!existsSync(f)) {
      console.error(`missing ${rel}: run the pipeline first (see README, "Run locally")`);
      process.exit(1);
    }
    if (stamps[key] !== sha(f)) return `${rel} changed since the export`;
  }
  const code = Math.max(newest(join(root, "src", "eleveniq")), newest(join(root, "contract", "v1")));
  if (code > statSync(manifest).mtimeMs) return "exporter or contract code is newer than the export";
  return null;
}

const reason = staleReason();
if (reason) {
  console.log(`building the web export (${reason})…`);
  const r = spawnSync(python, ["-m", "eleveniq.export.build_export", "--out", out], { cwd: root, stdio: "inherit" });
  if (r.error) {
    console.error(`could not run ${python}: create the virtualenv first (README, "Run locally")`);
    process.exit(1);
  }
  if (r.status !== 0) process.exit(r.status ?? 1);
} else {
  console.log("web export is current");
}
const vite = spawn("npx", ["vite", "--port", "5173", ...process.argv.slice(2)], {
  cwd: resolve(root, "frontend"), stdio: "inherit", env: { ...process.env, ELEVENIQ_DATA: out },
});
vite.on("exit", (code) => process.exit(code ?? 0));
