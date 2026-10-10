// The one data module (ADR 005 §1): typed fetches of the static contract files. v1 reads static
// JSON; M7 swaps these for the API with the same contract. Pages never fetch directly.
import type {
  GroupsGroupJson, LabelsJson, ManifestJson, PlayersKeyJson, SearchIndexJson,
} from "../types/contract";

export const CONTRACT_MAJOR = "1";
const BASE = `${import.meta.env.BASE_URL}data/`;

export class ContractError extends Error {}

const cache = new Map<string, Promise<unknown>>();

async function getJson<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    const p = fetch(BASE + path).then(async (r) => {
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return r.json();
    });
    p.catch(() => cache.delete(path));        // a failed fetch is retried next time
    cache.set(path, p);
  }
  return cache.get(path) as Promise<T>;
}

/** Refuses a contract with another major version (ADR 005 §2). */
export function checkMajor(version: string): void {
  if (version.split(".")[0] !== CONTRACT_MAJOR) {
    throw new ContractError(
      `This site reads data contract ${CONTRACT_MAJOR}.x; the data is ${version}.`);
  }
}

export async function getManifest(): Promise<ManifestJson> {
  const m = await getJson<ManifestJson>("manifest.json");
  checkMajor(m.stamps.contract_version);
  return m;
}

export const getSearchIndex = () => getJson<SearchIndexJson>("search-index.json");
export const getLabels = () => getJson<LabelsJson>("labels.json");
export const getPlayer = (key: string) => getJson<PlayersKeyJson>(`players/${key}.json`);
export const getGroup = (group: string) => getJson<GroupsGroupJson>(`groups/${group}.json`);
