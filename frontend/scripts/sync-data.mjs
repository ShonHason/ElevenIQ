// Copy an export directory (tests/fixtures/web or export/v1) into public/data, the data the
// site fetches (git-ignored). The site never computes a statistic (ADR 005 §1).
import { cpSync, existsSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(process.argv[2] ?? join(here, "..", "..", "tests", "fixtures", "web"));
const dst = join(here, "..", "public", "data");
if (!existsSync(join(src, "manifest.json"))) {
  console.error(`no manifest.json in ${src}`);
  process.exit(1);
}
rmSync(dst, { recursive: true, force: true });
cpSync(src, dst, { recursive: true });
console.log(`data: ${src} → ${dst}`);
