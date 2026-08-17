"use client";

import { useEffect } from "react";

export function RegisterServiceWorker() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js").catch(() => {
        // Best-effort: a failed registration degrades to "not installable,"
        // never to a broken app.
      });
    }
  }, []);
  return null;
}
