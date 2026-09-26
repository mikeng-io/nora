"use client";

import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

// The phone's top bar shows the page's title once its large heading has
// scrolled away, the way a native large-title bar does. The page heading
// registers itself here; the bar reads it.
interface TitleState {
  title: string | null;
  visible: boolean;
}

const Ctx = createContext<{
  state: TitleState;
  set: (s: TitleState) => void;
} | null>(null);

export function PageTitleProvider({ children }: { children: ReactNode }) {
  const [state, set] = useState<TitleState>({ title: null, visible: false });
  return <Ctx.Provider value={{ state, set }}>{children}</Ctx.Provider>;
}

export function useCompactTitle(): TitleState {
  return useContext(Ctx)?.state ?? { title: null, visible: false };
}

/** Attach to a page's h1: the bar shows `title` once it scrolls under it. */
export function useRegisterTitle(title: string) {
  const ctx = useContext(Ctx);
  const ref = useRef<HTMLHeadingElement>(null);
  const set = ctx?.set;
  useEffect(() => {
    if (!set) return;
    const el = ref.current;
    set({ title, visible: false });
    if (!el) return;
    const io = new IntersectionObserver(
      ([entry]) => set({ title, visible: !entry.isIntersecting && entry.boundingClientRect.top < 80 }),
      { rootMargin: "-64px 0px 0px 0px", threshold: 0 },
    );
    io.observe(el);
    return () => {
      io.disconnect();
      set({ title: null, visible: false });
    };
  }, [title, set]);
  return ref;
}
