"use client";

import * as RadixTabs from "@radix-ui/react-tabs";
import { useCallback, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { cn } from "@/lib/cn";

/** A page's tab kept in the URL (?tab=), so a tab can be linked to, and
 * Back returns to the one you were on. Unknown values fall back. */
export function useTabParam<T extends string>(allowed: readonly T[], fallback: T, key = "tab") {
  const params = useSearchParams();
  const raw = params.get(key);
  const value = (allowed as readonly string[]).includes(raw ?? "") ? (raw as T) : fallback;
  const setValue = useCallback(
    (next: string) => {
      const url = new URL(window.location.href);
      if (next === fallback) url.searchParams.delete(key);
      else url.searchParams.set(key, next);
      window.history.replaceState(null, "", url);
    },
    [fallback, key],
  );
  return [value, setValue] as const;
}

export const Tabs = RadixTabs.Root;
export const TabPanel = ({ className, ...props }: RadixTabs.TabsContentProps) => (
  <RadixTabs.Content
    className={cn("page-enter focus-visible:outline-none", className)}
    {...props}
  />
);

/** Underlined tabs on a hairline, the page's own sections. Scrolls sideways
 * on a narrow screen instead of wrapping. */
export function TabList({
  items,
  className,
  label,
}: {
  items: Array<{ value: string; label: ReactNode; count?: number }>;
  className?: string;
  label: string;
}) {
  return (
    <RadixTabs.List
      aria-label={label}
      className={cn(
        "scrollbar-none -mx-4 flex gap-1 overflow-x-auto border-b border-hairline px-4 sm:mx-0 sm:px-0",
        className,
      )}
    >
      {items.map((item) => (
        <RadixTabs.Trigger
          key={item.value}
          value={item.value}
          className={cn(
            "relative -mb-px flex h-11 shrink-0 items-center gap-1.5 px-3 text-[0.9375rem] font-medium text-ink-muted",
            "transition-colors duration-100 ease-nora hover:text-ink",
            "after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:rounded-full after:bg-denim after:opacity-0 after:transition-opacity after:duration-160",
            "data-[state=active]:text-ink data-[state=active]:after:opacity-100",
            "focus-visible:outline-offset-[-2px]",
          )}
        >
          {item.label}
          {item.count !== undefined ? (
            <span className="rounded-full bg-hoodie px-1.5 font-mono text-[0.6875rem] text-ink-muted tnum">
              {item.count}
            </span>
          ) : null}
        </RadixTabs.Trigger>
      ))}
    </RadixTabs.List>
  );
}

/** A small pill group for a choice of range or mode (the site's language
 * and platform switches). A radio group to assistive tech. */
export function Segmented<T extends string | number>({
  value,
  onChange,
  options,
  label,
  className,
  size = "md",
}: {
  value: T;
  onChange: (value: T) => void;
  options: Array<{ value: T; label: ReactNode }>;
  label: string;
  className?: string;
  size?: "sm" | "md";
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn("inline-flex max-w-full shrink-0 rounded-full bg-hoodie p-[3px]", className)}
      onKeyDown={(e) => {
        const i = options.findIndex((o) => o.value === value);
        if (e.key === "ArrowRight" || e.key === "ArrowDown") {
          e.preventDefault();
          onChange(options[(i + 1) % options.length].value);
        } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
          e.preventDefault();
          onChange(options[(i - 1 + options.length) % options.length].value);
        }
      }}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={String(o.value)}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            onClick={() => onChange(o.value)}
            className={cn(
              "grid shrink-0 place-items-center rounded-full font-semibold whitespace-nowrap transition-[background-color,color,box-shadow] duration-100 ease-nora",
              size === "sm" ? "h-7 min-w-9 px-2.5 text-label" : "h-8 min-w-10 px-3 text-meta",
              active
                ? "bg-surface text-ink shadow-[0_0_0_1px_var(--hairline)]"
                : "text-ink-muted hover:text-ink",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
