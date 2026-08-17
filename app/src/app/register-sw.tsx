"use client";

import { useEffect } from "react";

// Registration disabled for now — it was actively fighting local debugging
// (a cache-first fetch handler serving a stale /login across server
// restarts, indistinguishable from "the fix didn't work"). Actively
// unregisters and clears any previously-installed worker/cache instead, so
// a browser that already has one from testing self-heals on next visit.
// Re-enable registration once the rest of the flow is verified working.
export function RegisterServiceWorker() {
  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    navigator.serviceWorker.getRegistrations().then((regs) => {
      regs.forEach((r) => r.unregister());
    });
    if ("caches" in window) {
      caches.keys().then((keys) => keys.forEach((k) => caches.delete(k)));
    }
  }, []);
  return null;
}
