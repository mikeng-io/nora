"use client";

import { useSyncExternalStore } from "react";

// One shared clock for every "5 minutes ago" on the page, ticking every
// 15 seconds, so relative times move without each one owning a timer.
const TICK_MS = 15_000;
let now = Date.now();
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function subscribe(cb: () => void) {
  listeners.add(cb);
  if (!timer) {
    now = Date.now();
    timer = setInterval(() => {
      now = Date.now();
      listeners.forEach((l) => l());
    }, TICK_MS);
  }
  return () => {
    listeners.delete(cb);
    if (listeners.size === 0 && timer) {
      clearInterval(timer);
      timer = null;
    }
  };
}

/** `Date.now()` that re-renders its reader every 15s. The server snapshot is
 * a fixed 0 so nothing time-relative is baked into server HTML. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, () => now, () => 0);
}
