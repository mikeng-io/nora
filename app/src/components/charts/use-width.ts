"use client";

import { useLayoutEffect, useRef, useState } from "react";

/** The rendered width of a box, for charts drawn to their container. */
export function useWidth<T extends HTMLElement>(fallback = 320) {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setWidth(Math.max(0, Math.round(entry.contentRect.width))));
    ro.observe(el);
    setWidth(el.clientWidth || fallback);
    return () => ro.disconnect();
  }, [fallback]);
  return [ref, width] as const;
}

/** Round, readable axis ticks: 0, 5, 10 rather than 0, 4.3, 8.6. */
export function niceTicks(max: number, count = 4): number[] {
  if (!(max > 0)) return [0];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const top = Math.ceil(max / step) * step;
  const out: number[] = [];
  for (let v = 0; v <= top + step / 2; v += step) out.push(Number(v.toFixed(6)));
  return out;
}
