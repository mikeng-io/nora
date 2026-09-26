"use client";

import { useCallback, useSyncExternalStore } from "react";

// The browser's install offer (Chromium's `beforeinstallprompt`) is caught
// once, early, and kept for the account menu's "Install" item. Safari has
// no such event; there the menu explains Share → Add to Home Screen.
interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

let deferred: InstallPromptEvent | null = null;
let installed = false;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

if (typeof window !== "undefined") {
  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();
    deferred = e as InstallPromptEvent;
    emit();
  });
  window.addEventListener("appinstalled", () => {
    deferred = null;
    installed = true;
    emit();
  });
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

function standalone(): boolean {
  return (
    matchMedia("(display-mode: standalone)").matches ||
    (navigator as Navigator & { standalone?: boolean }).standalone === true
  );
}

function isIos(): boolean {
  const ua = navigator.userAgent;
  return /iPad|iPhone|iPod/.test(ua) || (ua.includes("Mac") && navigator.maxTouchPoints > 1);
}

export type InstallState = "installed" | "prompt" | "ios" | "unsupported";

const snapshot = (): InstallState =>
  installed || standalone() ? "installed" : deferred ? "prompt" : isIos() ? "ios" : "unsupported";

export function useInstall() {
  const state = useSyncExternalStore(subscribe, snapshot, () => "unsupported" as InstallState);
  const install = useCallback(async () => {
    if (!deferred) return false;
    await deferred.prompt();
    const { outcome } = await deferred.userChoice;
    deferred = null;
    emit();
    return outcome === "accepted";
  }, []);
  return { state, install };
}
