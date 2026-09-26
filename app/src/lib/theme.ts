"use client";

import { useCallback, useSyncExternalStore } from "react";

// Two lightings, the landing page's: Studio (light) and Late night (dark).
// The choice is stored under the SAME key the site uses (site/src/scripts/
// prefs.ts), on the same origin, so picking one in either place carries to
// the other. No choice means following the system.
export const THEME_KEY = "nora-theme";
export type ThemeChoice = "light" | "dark" | "system";

export const THEME_COLOR = { light: "#f4f2f2", dark: "#141112" } as const;

/** Runs in <head> before first paint; kept dependency-free and tiny. */
export const THEME_BOOT_SCRIPT = `(function(){try{var c=null;try{c=localStorage.getItem("${THEME_KEY}")}catch(e){}var d=c==="dark"||(c!=="light"&&matchMedia("(prefers-color-scheme: dark)").matches);var t=d?"dark":"light";document.documentElement.dataset.theme=t;var m=document.querySelectorAll('meta[name="theme-color"]');for(var i=0;i<m.length;i++){m[i].setAttribute("content",d?"${THEME_COLOR.dark}":"${THEME_COLOR.light}");m[i].removeAttribute("media")}}catch(e){}})();`;

function readChoice(): ThemeChoice {
  try {
    const c = localStorage.getItem(THEME_KEY);
    return c === "light" || c === "dark" ? c : "system";
  } catch {
    return "system";
  }
}

function systemDark(): boolean {
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(choice: ThemeChoice): void {
  const dark = choice === "dark" || (choice === "system" && systemDark());
  const root = document.documentElement;
  root.dataset.theme = dark ? "dark" : "light";
  for (const m of document.querySelectorAll('meta[name="theme-color"]')) {
    m.setAttribute("content", dark ? THEME_COLOR.dark : THEME_COLOR.light);
    m.removeAttribute("media");
  }
}

const listeners = new Set<() => void>();
let bound = false;

function subscribe(cb: () => void) {
  listeners.add(cb);
  if (!bound && typeof window !== "undefined") {
    bound = true;
    matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
      if (readChoice() === "system") applyTheme("system");
      listeners.forEach((l) => l());
    });
    window.addEventListener("storage", (e) => {
      if (e.key === THEME_KEY) {
        applyTheme(readChoice());
        listeners.forEach((l) => l());
      }
    });
  }
  return () => listeners.delete(cb);
}

const snapshot = () =>
  `${readChoice()}|${typeof document !== "undefined" ? document.documentElement.dataset.theme : "light"}`;

export function useTheme() {
  const snap = useSyncExternalStore(subscribe, snapshot, () => "system|light");
  const [choice, resolved] = snap.split("|") as [ThemeChoice, "light" | "dark"];
  const setChoice = useCallback((next: ThemeChoice) => {
    try {
      if (next === "system") localStorage.removeItem(THEME_KEY);
      else localStorage.setItem(THEME_KEY, next);
    } catch {
      // Storage can be missing (a private window); the choice still applies
      // for this page.
    }
    // A 240ms cross-fade when the lighting changes, as on the site. Readers
    // are told only once the new lighting is on the page: the transition
    // applies it a frame later, and a reader told earlier keeps the old one.
    const apply = () => {
      applyTheme(next);
      listeners.forEach((l) => l());
    };
    const doc = document as Document & { startViewTransition?: (cb: () => void) => unknown };
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (doc.startViewTransition && !reduce) doc.startViewTransition(apply);
    else apply();
  }, []);
  return { choice, resolved, setChoice };
}
