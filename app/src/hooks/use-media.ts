"use client";

import { useCallback, useSyncExternalStore } from "react";

export function useMedia(query: string, serverValue = false): boolean {
  const subscribe = useCallback(
    (cb: () => void) => {
      const m = matchMedia(query);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    [query],
  );
  return useSyncExternalStore(subscribe, () => matchMedia(query).matches, () => serverValue);
}

/** Desktop layout: the side rail and floating panels (64rem, Tailwind `lg`). */
export const useIsDesktop = () => useMedia("(min-width: 64rem)", false);
export const useReducedMotion = () => useMedia("(prefers-reduced-motion: reduce)", false);
