// What a Checklist keeps on this device, as pure functions over an injected
// storage so a blocked or throwing one is testable. Nothing here is shared:
// a tick is the reader's own, in this browser.

type Store = Pick<Storage, "getItem" | "setItem" | "removeItem">;

/** djb2 over the text, as base36: short, stable across runs, not a secret. */
export function hashText(text: string): string {
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = ((h * 33) ^ text.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

/** One key per document and per list: a revision that changes the items starts clean, and
 * two lists in one document differ by their title even when an item reads the same. */
export function checklistKey(documentId: string | null, title: string | undefined, texts: string[]): string {
  return `nora:check:${documentId ?? ""}:${hashText(`${title ?? ""}\u0001${texts.join("\u0000")}`)}`;
}

/** The ticked indexes stored under `key`; empty on anything missing, malformed or out of range. */
export function readTicks(store: Store | null | undefined, key: string, count: number): number[] {
  try {
    const raw = store?.getItem(key);
    if (!raw) return [];
    const v: unknown = JSON.parse(raw);
    if (!Array.isArray(v)) return [];
    return [...new Set(v)].filter((n): n is number => Number.isInteger(n) && n >= 0 && n < count).sort((a, b) => a - b);
  } catch {
    return [];
  }
}

/** Stores the ticks (or forgets the key when none). False when the storage refused. */
export function writeTicks(store: Store | null | undefined, key: string, ticks: number[]): boolean {
  try {
    if (!store) return false;
    if (ticks.length === 0) store.removeItem(key);
    else store.setItem(key, JSON.stringify([...ticks].sort((a, b) => a - b)));
    return true;
  } catch {
    return false;
  }
}

/** `window.localStorage`, or null where even touching it throws (blocked site data). */
export function deviceStorage(): Store | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}
