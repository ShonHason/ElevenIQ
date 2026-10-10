import { execFileSync } from "node:child_process";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import Ajv2020 from "ajv/dist/2020";
import { describe, expect, it } from "vitest";
import { checkMajor, ContractError } from "./index";

const root = new URL("../../../", import.meta.url).pathname;
const contract = join(root, "contract", "v1");
const fixture = join(root, "tests", "fixtures", "web");

describe("contract (ADR 005 §2)", () => {
  it("refuses another major version", () => {
    expect(() => checkMajor("1.0.0")).not.toThrow();
    expect(() => checkMajor("2.0.0")).toThrow(ContractError);
  });
  it("the fixture validates against the same schemas as the exporter (Ajv)", () => {
    const ajv = new Ajv2020({ allErrors: false, strict: false });
    for (const f of readdirSync(contract)) ajv.addSchema(JSON.parse(readFileSync(join(contract, f), "utf8")));
    const id = (n: string) => `https://eleveniq.local/contract/v1/${n}.schema.json`;
    const check = (schema: string, path: string) => {
      const ok = ajv.validate(id(schema), JSON.parse(readFileSync(join(fixture, path), "utf8")));
      expect(ok, `${path}: ${ajv.errorsText()}`).toBe(true);
    };
    check("manifest", "manifest.json");
    check("search-index", "search-index.json");
    check("labels", "labels.json");
    for (const f of readdirSync(join(fixture, "players"))) check("player", `players/${f}`);
    for (const f of readdirSync(join(fixture, "groups"))) check("group", `groups/${f}`);
    for (const f of readdirSync(join(fixture, "gems"))) check("gems", `gems/${f}`);
  });
  it("the generated TypeScript types are current", () => {
    expect(() => execFileSync("node", ["scripts/gen-types.mjs", "--check"], { cwd: join(root, "frontend") })).not.toThrow();
  });
});
