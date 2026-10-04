"use client";

// Time left to an event, counted on the reader's device in Hong Kong time. The server and
// the first client render show only the target as text (the clock is not known there, and
// a figure computed at build or request time would disagree with the browser's); the live
// figure appears after mount and refreshes every 30 s and when the tab comes back. It is
// never aria-live: a polite region would read out every tick.
import { useSyncExternalStore } from "react";
import { useTranslations } from "next-intl";
import { Panel } from "@/components/ui/panel";
import { remaining } from "./countdown-time";
import type { Render } from "./types";

const MINUTE = 60_000;

// The clock, to the minute: the snapshot is stable inside a minute, so React re-renders
// only when the figure can change. null on the server (and while hydrating).
function subscribe(onChange: () => void) {
  const id = window.setInterval(onChange, MINUTE / 2);
  const onVisible = () => {
    if (document.visibilityState === "visible") onChange();
  };
  document.addEventListener("visibilitychange", onVisible);
  return () => {
    window.clearInterval(id);
    document.removeEventListener("visibilitychange", onVisible);
  };
}
const minuteNow = () => Math.floor(Date.now() / MINUTE) * MINUTE;
const serverNow = () => null;

export function CountdownBlock({ props }: Render<"Countdown">) {
  const t = useTranslations("documents.renderer");
  const now = useSyncExternalStore(subscribe, minuteNow, serverNow);
  const left = now === null ? null : remaining(props.date, props.time, now);
  const figure =
    left === null
      ? null
      : left.kind === "days"
        ? t("countdownDays", { count: left.days })
        : left.kind === "hours"
          ? left.hours > 0
            ? t("countdownHoursMinutes", { hours: left.hours, minutes: left.minutes })
            : t("countdownMinutes", { minutes: left.minutes })
          : left.kind === "today"
            ? t("countdownToday")
            : t("countdownPassed");
  const when = props.time ? `${props.date} ${props.time}` : props.date;

  return (
    <Panel className="flex flex-col gap-1 px-4 py-3 sm:px-5 sm:py-4">
      <p className="text-label text-ink-muted">{props.label}</p>
      {/* Its height is reserved before the figure arrives, so nothing moves. */}
      <p className="min-h-[1.8rem] text-heading font-semibold tnum text-ink" data-countdown={left?.kind ?? "pending"}>
        {figure}
      </p>
      <p className="text-meta text-ink-soft">
        <time dateTime={props.time ? `${props.date}T${props.time}+08:00` : props.date} className="font-mono tnum">
          {when}
        </time>
        {` · ${t("countdownZone")}`}
      </p>
      {props.detail ? <p className="text-meta text-ink-muted">{props.detail}</p> : null}
    </Panel>
  );
}
