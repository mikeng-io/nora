"use client";

import { clear as clearIdb } from "idb-keyval";

// Everything the portal keeps on a device: the persisted reads (IndexedDB,
// one store per person) and the pages the service worker stored. Held only
// while somebody is signed in; forgotten on sign-out and whenever the login
// page shows, so a shared phone never hands one person's data to the next.

let writesStopped = false;
let currentSessionId: string | undefined;
export const DEVICE_SIGN_OUT = "nora:device-sign-out";

/** True once the device is being forgotten: the persister writes nothing
 * after this, so a read still in flight cannot put data back. */
export const deviceWritesStopped = () => writesStopped;

/** A new session began (the portal mounted for someone): keep again. A
 * client-side redirect after sign-in keeps this module alive, so the flag
 * would otherwise outlast the session that set it. */
export const resumeDeviceWrites = (sessionId?: string) => {
  writesStopped = false;
  currentSessionId = sessionId;
};

export async function forgetDevice(): Promise<void> {
  const sessionId = currentSessionId;
  currentSessionId = undefined;
  writesStopped = true;
  // Stop mounted caches in this tab and every other tab before forgetting I/O.
  window.dispatchEvent(new Event(DEVICE_SIGN_OUT));
  if (sessionId && typeof BroadcastChannel !== "undefined") {
    const channel = new BroadcastChannel(DEVICE_SIGN_OUT);
    channel.postMessage({ type: "signed-out", sessionId });
    channel.close();
  }
  await clearIdb().catch(() => {});
  if ("caches" in window) {
    const keys = await caches.keys().catch(() => [] as string[]);
    await Promise.all(keys.filter((k) => k.startsWith("nora-")).map((k) => caches.delete(k)));
  }
  navigator.serviceWorker?.controller?.postMessage("clear-caches");
}
