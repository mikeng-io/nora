"use client";

import { startTransition, useEffect, useState, type ReactNode } from "react";
import { MutationCache, QueryCache, QueryClient, onlineManager } from "@tanstack/react-query";
import { PersistQueryClientProvider } from "@tanstack/react-query-persist-client";
import { createAsyncStoragePersister } from "@tanstack/query-async-storage-persister";
import { del, get, set } from "idb-keyval";
import { ApiError, isNotFound, isSignedOut } from "@/lib/api/client";
import { DEVICE_SIGN_OUT, deviceWritesStopped, resumeDeviceWrites } from "@/lib/device-data";
import { signOutAction } from "@/server/actions";

// Bumped when a schema changes shape, so a device never restores a cache
// this build cannot read.
export const CACHE_VERSION = "v2.0";
const DAY = 24 * 60 * 60 * 1000;

export const cacheKey = (personId: string) => `nora-cache:${personId}`;

/** The session ended: send the viewer to sign in, keeping where they were. */
function toSignIn() {
  const locale = location.pathname.split("/")[1] || "zh-Hant";
  const back = encodeURIComponent(location.pathname + location.search);
  location.assign(`/${locale}/login?next=${back}`);
}

export function QueryProvider({ personId, children }: { personId: string; children: ReactNode }) {
  // Keyed by the person: a changed `personId` mounts a new cache and persister.
  // A changed prop never reaches `useState`, so without the key a mounted portal
  // would keep the last person's queries in memory after an account change.
  return (
    <PortalCache key={personId} personId={personId}>
      {children}
    </PortalCache>
  );
}

function PortalCache({ personId, children }: { personId: string; children: ReactNode }) {
  const [lifetime] = useState(() => ({ active: true }));
  // Start from what the device says, so an app opened offline shows what it
  // kept instead of firing reads that can only fail.
  useState(() => {
    if (typeof navigator !== "undefined") {
      onlineManager.setOnline(navigator.onLine);
      resumeDeviceWrites();
    }
    return null;
  });
  const [client] = useState(
    () =>
      new QueryClient({
        queryCache: new QueryCache({ onError: (e) => isSignedOut(e) && toSignIn() }),
        mutationCache: new MutationCache({ onError: (e) => isSignedOut(e) && toSignIn() }),
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            // Kept a week so the persisted cache can answer offline.
            gcTime: 7 * DAY,
            refetchOnWindowFocus: true,
            retry: (count, err) =>
              !(isNotFound(err) || isSignedOut(err)) &&
              !(err instanceof ApiError && err.kind === "bad-payload") &&
              count < 2,
            retryDelay: (n) => Math.min(1000 * 2 ** n, 8000),
          },
        },
      }),
  );

  // One cache per person on the device: signing in as somebody else never
  // shows them the last person's memory. Sign-out deletes it outright.
  const [persister] = useState(() =>
    createAsyncStoragePersister({
      key: cacheKey(personId),
      storage: {
        getItem: (k) => get<string>(k).then((v) => lifetime.active ? v ?? null : null),
        // Nothing is kept once the device is being forgotten (sign-out).
        setItem: (k, v) => (!lifetime.active || deviceWritesStopped() ? Promise.resolve() : set(k, v)),
        removeItem: (k) => del(k),
      },
      throttleTime: 2000,
    }),
  );

  useEffect(() => {
    lifetime.active = true;
    const dispose = () => {
      lifetime.active = false;
      void client.cancelQueries();
      client.clear();
      void persister.removeClient();
    };
    const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel(DEVICE_SIGN_OUT);
    const signedOut = (event: MessageEvent) => {
      if (event.data !== "signed-out") return;
      dispose();
      // The sender may not have cleared our shared cookie yet. Going straight
      // to login could send us back into the portal with the old session.
      startTransition(async () => {
        await signOutAction(location.pathname.split("/")[1] || "zh-Hant");
      });
    };
    channel?.addEventListener("message", signedOut);
    window.addEventListener(DEVICE_SIGN_OUT, dispose);
    return () => {
      window.removeEventListener(DEVICE_SIGN_OUT, dispose);
      channel?.close();
      dispose();
    };
  }, [client, persister, lifetime]);

  return (
    <PersistQueryClientProvider
      client={client}
      persistOptions={{
        persister,
        maxAge: 7 * DAY,
        buster: CACHE_VERSION,
        dehydrateOptions: {
          // Only answers are worth keeping; errors and in-flight reads are not.
          shouldDehydrateQuery: (q) => q.state.status === "success",
        },
      }}
    >
      {children}
    </PersistQueryClientProvider>
  );
}
