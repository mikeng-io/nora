import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { persistQueryClientRestore, type PersistedClient } from "@tanstack/react-query-persist-client";
import { describe, expect, it, vi } from "vitest";
import messages from "../../../messages/en.json";

// next-intl's client entry cannot be resolved by Node ESM here, so the two hooks
// the view uses read the real English messages directly.
vi.mock("next-intl", () => ({
  useLocale: () => "en",
  useTranslations: (ns: string) => (key: string) =>
    (messages as unknown as Record<string, Record<string, string>>)[ns]?.[key] ?? `${ns}.${key}`,
}));
vi.mock("@/i18n/routing", () => ({ Link: "a" }));
vi.mock("idb-keyval", () => ({ get: vi.fn(), set: vi.fn(), del: vi.fn(), clear: vi.fn() }));
vi.mock("@/server/actions", () => ({ signOutAction: vi.fn() }));

import { CACHE_VERSION } from "@/components/providers/query-provider";
import { MemoryView } from "./memory-view";

// The /memory response as the build before the Honcho read left sent it, and
// as a returning viewer's device still holds it: one wrapper per workspace,
// with `profile` nested inside.
const OLD_BUILD = "v2.0";
const OLD_SHAPE = [
  { workspace: "67", person_id: "miles", display_name: "Miles", reachable: true,
    identity: ["Name: Miles"], episodes: ["2026-09-20 asked about hotpot"] },
];
const NEW_SHAPE = [
  { unit: "friends", disputes: [],
    lines: [{ part: "facts", kind: "attribute", text: "Name: Miles", mode: "told",
      state: "current", told_by: "Miles", written_at: 1_790_000_000_000, evidence: [] }] },
];

const snapshot = (buster: string, data: unknown): PersistedClient => ({
  timestamp: Date.now(), buster,
  clientState: { mutations: [], queries: [{ queryKey: ["memory"], queryHash: '["memory"]', dehydratedAt: Date.now(),
    state: { data, dataUpdateCount: 1, dataUpdatedAt: Date.now(), error: null, errorUpdateCount: 0,
      errorUpdatedAt: 0, fetchFailureCount: 0, fetchFailureReason: null, fetchMeta: null,
      isInvalidated: false, status: "success", fetchStatus: "idle" } }] },
});

/** What the portal does on load: restore the saved snapshot under this build's buster. */
async function restored(saved: PersistedClient) {
  const client = new QueryClient();
  await persistQueryClientRestore({
    queryClient: client, buster: CACHE_VERSION, maxAge: 7 * 24 * 60 * 60 * 1000,
    persister: { persistClient: async () => {}, restoreClient: async () => saved, removeClient: async () => {} },
  });
  return client;
}

const render = (client: QueryClient) => renderToString(
  createElement(QueryClientProvider, { client }, createElement(MemoryView)));

describe("a returning viewer's saved /memory response", () => {
  it("is not restored from the build that sent the old shape, and the page renders", async () => {
    const client = await restored(snapshot(OLD_BUILD, OLD_SHAPE));
    expect(client.getQueryData(["memory"])).toBeUndefined();
    expect(() => render(client)).not.toThrow();
  });

  it("is a real hazard: the old shape in the cache crashes the page", () => {
    const client = new QueryClient();
    client.setQueryData(["memory"], OLD_SHAPE);
    expect(() => render(client)).toThrow();
  });

  it("restores and renders a saved response of the current shape", async () => {
    const client = await restored(snapshot(CACHE_VERSION, NEW_SHAPE));
    expect(client.getQueryData(["memory"])).toEqual(NEW_SHAPE);
    expect(render(client)).toContain("Name: Miles");
  });
});
