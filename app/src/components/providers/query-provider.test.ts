import type { ReactElement, ReactNode } from "react";
import type { QueryClient } from "@tanstack/react-query";
import type { Persister } from "@tanstack/react-query-persist-client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const hooks = vi.hoisted(() => ({ effects: [] as Array<() => void | (() => void)> }));
vi.mock("react", async (original) => ({
  ...await original<typeof import("react")>(),
  useState: (init: unknown) => [typeof init === "function" ? init() : init, vi.fn()],
  useEffect: (effect: () => void | (() => void)) => { hooks.effects.push(effect); },
}));
vi.mock("idb-keyval", () => ({ get: vi.fn(), set: vi.fn(), del: vi.fn(), clear: vi.fn() }));
vi.mock("@/server/actions", () => ({ signOutAction: vi.fn().mockResolvedValue(undefined) }));

import { clear, del, set } from "idb-keyval";
import { forgetDevice, resumeDeviceWrites } from "@/lib/device-data";
import { signOutAction } from "@/server/actions";
import { cacheKey, QueryProvider } from "./query-provider";

class Channel extends EventTarget {
  static opened: Channel[] = [];
  postMessage = vi.fn();
  close = vi.fn();
  constructor(public name: string) { super(); Channel.opened.push(this); }
}

// Drive effect setup/cleanup with real QueryClients and the real throttled
// persister. The app's Node harness has no DOM; only browser I/O is mocked.
function mount(personId: string) {
  const element = QueryProvider({ personId, children: null });
  const render = element.type as (props: { personId: string; children: ReactNode }) => ReactElement;
  const tree = render(element.props);
  const cleanups = hooks.effects.splice(0).map((setup) => setup());
  const { client, persistOptions } = tree.props as {
    client: QueryClient; persistOptions: { persister: Persister };
  };
  return { client, persister: persistOptions.persister, unmount: () => cleanups.forEach((f) => f?.()) };
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.clearAllMocks();
  vi.mocked(clear).mockResolvedValue();
  vi.mocked(del).mockResolvedValue();
  vi.mocked(set).mockResolvedValue();
  hooks.effects = [];
  Channel.opened = [];
  vi.stubGlobal("window", new EventTarget());
  vi.stubGlobal("BroadcastChannel", Channel);
  vi.stubGlobal("navigator", { onLine: true });
  vi.stubGlobal("location", { pathname: "/en/plan", search: "", assign: vi.fn() });
  resumeDeviceWrites();
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("the portal query cache is per person", () => {
  it("keys the provider by person", () => {
    expect(QueryProvider({ personId: "person-a", children: null }).key).toBe("person-a");
    expect(QueryProvider({ personId: "person-b", children: null }).key).toBe("person-b");
  });

  it("cancels reads, empties the old client and removes its persisted cache on identity change", async () => {
    const old = mount("person-a");
    old.client.setQueryData(["private-plan"], "old plan");
    let aborted = false;
    const read = old.client.fetchQuery({ queryKey: ["pending"], queryFn: ({ signal }) => {
      signal.addEventListener("abort", () => { aborted = true; });
      return new Promise(() => {});
    } }).catch(() => {});
    old.unmount();
    const next = mount("person-b");
    expect(aborted).toBe(true);
    expect(old.client.getQueryCache().getAll()).toEqual([]);
    expect(next.client).not.toBe(old.client);
    expect(next.client.getQueryData(["private-plan"])).toBeUndefined();
    expect(del).toHaveBeenCalledWith(cacheKey("person-a"));
    await read;
    next.unmount();
  });

  it("cannot persist an old client's throttled write after a new account resumes writes", async () => {
    const old = mount("person-a");
    const snapshot = { timestamp: 1, buster: "", clientState: { mutations: [], queries: [] } };
    await old.persister.persistClient(snapshot);
    const queued = old.persister.persistClient(snapshot);
    old.unmount();
    const next = mount("person-b");
    vi.mocked(set).mockClear();
    await vi.advanceTimersByTimeAsync(3000);
    await queued;
    expect(set).not.toHaveBeenCalled();
    next.unmount();
  });

  it("clears and redirects a mounted tab on another tab's sign-out", () => {
    const tab = mount("person-a");
    tab.client.setQueryData(["private-plan"], "old plan");
    expect(Channel.opened).toHaveLength(1);
    Channel.opened[0].dispatchEvent(new MessageEvent("message", { data: "signed-out" }));
    expect(tab.client.getQueryCache().getAll()).toEqual([]);
    expect(del).toHaveBeenCalledWith(cacheKey("person-a"));
    expect(signOutAction).toHaveBeenCalledWith("en");
    tab.unmount();
    expect(Channel.opened[0].close).toHaveBeenCalled();
  });

  it("forgetDevice tells local and remote caches to stop before clearing storage", async () => {
    const tab = mount("person-a");
    tab.client.setQueryData(["private-plan"], "old plan");
    await forgetDevice();
    expect(tab.client.getQueryCache().getAll()).toEqual([]);
    expect(Channel.opened[1].name).toBe(Channel.opened[0].name);
    expect(Channel.opened[1].postMessage).toHaveBeenCalledWith("signed-out");
    expect(Channel.opened[1].close).toHaveBeenCalled();
    expect(clear).toHaveBeenCalled();
    tab.unmount();
  });
});
