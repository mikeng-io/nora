"use client";

import { useEffect } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";

// Registers the service worker (public/sw.js) in production builds. A new
// version installs in the background and waits; the viewer is offered a
// reload rather than having the page swapped under them mid-read.
export function ServiceWorkerRegistration() {
  const t = useTranslations("app");

  useEffect(() => {
    if (!("serviceWorker" in navigator)) return;
    if (process.env.NODE_ENV !== "production" && process.env.NEXT_PUBLIC_SW_DEV !== "1") return;

    let reloading = false;
    const onController = () => {
      if (reloading) return;
      reloading = true;
      location.reload();
    };
    navigator.serviceWorker.addEventListener("controllerchange", onController);

    const offer = (worker: ServiceWorker) => {
      toast(t("updateReady"), {
        duration: Infinity,
        action: { label: t("updateReload"), onClick: () => worker.postMessage("skip-waiting") },
      });
    };

    navigator.serviceWorker
      .register("/sw.js", { scope: "/" })
      .then((reg) => {
        if (reg.waiting && navigator.serviceWorker.controller) offer(reg.waiting);
        reg.addEventListener("updatefound", () => {
          const incoming = reg.installing;
          incoming?.addEventListener("statechange", () => {
            if (incoming.state === "installed" && navigator.serviceWorker.controller) offer(incoming);
          });
        });
        // Check for a new version when the app comes back to the front.
        const check = () => document.visibilityState === "visible" && reg.update().catch(() => {});
        document.addEventListener("visibilitychange", check);
      })
      .catch(() => {});

    return () => navigator.serviceWorker.removeEventListener("controllerchange", onController);
  }, [t]);

  return null;
}
