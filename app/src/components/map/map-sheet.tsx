"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";
import { useReducedMotion } from "@/hooks/use-media";

// The phone's sheet over the map: three resting heights (a peek, half, and
// nearly all of it), dragged by its handle or tapped to step up. It sits
// inside the map's own box, so it never covers the bottom bar, and it tells
// the map how much of it is covered so the map can keep people in view.
export type Snap = "peek" | "half" | "full";

const PEEK_PX = 156;
const HEAD_PX = 60;

export function MapSheet({
  snap,
  onSnap,
  header,
  children,
  label,
  expandLabel,
  collapseLabel,
  onCovered,
}: {
  snap: Snap;
  onSnap: (s: Snap) => void;
  header: ReactNode;
  children: ReactNode;
  label: string;
  expandLabel: string;
  collapseLabel: string;
  /** Pixels of the map the sheet covers at rest. */
  onCovered?: (px: number) => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const [h, setH] = useState(0);
  const [drag, setDrag] = useState<number | null>(null);
  const start = useRef<{ y: number; base: number; t: number; moved: boolean } | null>(null);
  const reduce = useReducedMotion();

  useLayoutEffect(() => {
    const el = root.current?.parentElement;
    if (!el) return;
    const ro = new ResizeObserver(() => setH(el.clientHeight));
    ro.observe(el);
    setH(el.clientHeight);
    return () => ro.disconnect();
  }, []);

  const heights: Record<Snap, number> = {
    peek: Math.min(PEEK_PX, h),
    half: Math.round(h * 0.52),
    full: Math.max(0, h - 12),
  };
  const visible = drag ?? heights[snap];

  useEffect(() => {
    if (h > 0) onCovered?.(snap === "full" ? heights.half : heights[snap]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [snap, h]);

  const nearest = useCallback(
    (px: number, velocity: number): Snap => {
      // A flick decides by direction; a slow drag by the closest height.
      const order: Snap[] = ["peek", "half", "full"];
      if (Math.abs(velocity) > 0.5) {
        const i = order.findIndex((s) => heights[s] >= px - 1);
        const up = velocity < 0;
        const idx = up ? Math.min(order.length - 1, Math.max(0, i)) : Math.max(0, (i === -1 ? order.length : i) - 1);
        return order[idx];
      }
      return order.reduce((a, b) => (Math.abs(heights[b] - px) < Math.abs(heights[a] - px) ? b : a));
    },
    [heights.peek, heights.half, heights.full], // eslint-disable-line react-hooks/exhaustive-deps
  );

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    start.current = { y: e.clientY, base: heights[snap], t: performance.now(), moved: false };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const s = start.current;
    if (!s) return;
    const dy = e.clientY - s.y;
    if (!s.moved && Math.abs(dy) < 6) return;
    s.moved = true;
    setDrag(Math.max(heights.peek - 24, Math.min(heights.full, s.base - dy)));
  };
  const onPointerUp = (e: React.PointerEvent) => {
    const s = start.current;
    start.current = null;
    if (!s) return;
    if (!s.moved) {
      setDrag(null);
      onSnap(snap === "peek" ? "half" : snap === "half" ? "full" : "peek");
      return;
    }
    const dy = e.clientY - s.y;
    const velocity = dy / Math.max(1, performance.now() - s.t);
    const next = nearest(s.base - dy, velocity);
    setDrag(null);
    onSnap(next);
  };

  return (
    <div
      ref={root}
      role="region"
      aria-label={label}
      className={cn(
        "absolute inset-x-0 bottom-0 z-20 flex flex-col rounded-t-sheet border-t border-hairline bg-surface shadow-[0_-8px_24px_rgb(20_17_18/0.08)] lg:hidden",
        drag === null && !reduce && "transition-transform duration-240 ease-nora",
      )}
      style={{ height: heights.full || "70%", transform: `translateY(${(heights.full || 0) - visible}px)` }}
    >
      <div
        className="shrink-0 touch-none select-none"
        style={{ height: HEAD_PX }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => {
          start.current = null;
          setDrag(null);
        }}
      >
        <button
          type="button"
          aria-expanded={snap !== "peek"}
          aria-label={snap === "full" ? collapseLabel : expandLabel}
          onClick={(e) => {
            // Pointer taps are handled above; this is the keyboard's way in.
            if (e.detail === 0) onSnap(snap === "full" ? "peek" : snap === "peek" ? "half" : "full");
          }}
          className="mx-auto mt-2 mb-1 block h-5 w-16 rounded-full focus-visible:outline-offset-0"
        >
          <span className="mx-auto block h-1 w-10 rounded-full bg-seam" aria-hidden="true" />
        </button>
        <div className="px-4">{header}</div>
      </div>
      <div
        className="scroll-thin min-h-0 flex-1 overflow-y-auto overscroll-contain pb-3"
        style={{ maxHeight: Math.max(0, heights[snap] - HEAD_PX) }}
      >
        {children}
      </div>
    </div>
  );
}
