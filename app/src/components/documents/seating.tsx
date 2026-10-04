"use client";

import { useId } from "react";
import { Check, CircleDashed, Clock, X, type LucideIcon } from "lucide-react";
import { useTranslations } from "next-intl";
import { Panel } from "@/components/ui/panel";
import { Meter, Stat } from "@/components/ui/stat";
import { cn } from "@/lib/cn";
import { countSeating, seatPositions, tableFill, type SeatStatus } from "./seating-count";
import type { Render } from "./types";

type Seat = Render<"Seating">["props"]["tables"][number]["seats"][number];
type Key = SeatStatus | "none";

// A reply is never colour alone (DESIGN.md §3): each has an icon and a spoken word.
const ICON: Record<Key, LucideIcon> = { confirmed: Check, pending: Clock, declined: X, none: CircleDashed };
const ICON_TONE: Record<Key, string> = {
  confirmed: "text-success",
  pending: "text-caution",
  declined: "text-danger",
  none: "text-ink-muted",
};
const WORD = { confirmed: "seatConfirmed", pending: "seatPending", declined: "seatDeclined", none: "seatNoReply" } as const;
// The round table's dots: filled for a reply that holds the seat, hollow for a decline,
// a ring for no reply, a faint ring for a free seat.
const DOT: Record<Key | "free", string> = {
  confirmed: "fill-success stroke-success",
  pending: "fill-caution stroke-caution",
  declined: "fill-surface stroke-danger",
  none: "fill-none stroke-ink-muted",
  free: "fill-none stroke-hairline",
};

const SIZE = 96;
const MID = SIZE / 2;
const RING = 36;

/** A round table seen from above, one dot per seat. Decoration: the list below says it all. */
function RoundTable({ seats, capacity }: { seats: readonly Seat[]; capacity: number }) {
  const n = Math.max(capacity, seats.length);
  // Dots shrink as the ring fills, so 20 seats never overlap.
  const r = Math.min(6, ((2 * Math.PI * RING) / n) * 0.4);
  return (
    <svg aria-hidden="true" focusable="false" viewBox={`0 0 ${SIZE} ${SIZE}`} className="size-24 shrink-0">
      <circle cx={MID} cy={MID} r={RING - r - 5} className="fill-hoodie stroke-hairline" strokeWidth={1} />
      {seatPositions(n, MID, MID, RING).map((p, i) => {
        const seat = seats[i];
        return <circle key={i} cx={p.x} cy={p.y} r={r} strokeWidth={1.5} className={DOT[seat ? (seat.status ?? "none") : "free"]} />;
      })}
    </svg>
  );
}

export function SeatingBlock({ props }: Render<"Seating">) {
  const t = useTranslations("documents.renderer");
  const uid = useId();
  const totals = countSeating(props.tables);
  const tile = (key: Key, count: number) => {
    const Icon = ICON[key];
    return (
      <Stat
        label={
          <span className="inline-flex items-center gap-1">
            <Icon aria-hidden className={cn("size-3.5 shrink-0", ICON_TONE[key])} />
            {t(WORD[key])}
          </span>
        }
        value={count}
      />
    );
  };

  return (
    <figure className="flex min-w-0 flex-col gap-3">
      <figcaption className="text-meta text-ink-muted">{props.caption}</figcaption>
      <Panel>
        <dl className="grid grid-cols-3 gap-x-4 gap-y-4 px-4 py-4 sm:grid-cols-6 sm:px-5">
          <Stat label={t("seatingTables")} value={totals.tables} />
          <Stat label={t("seatingGuests")} value={totals.guests} />
          {tile("confirmed", totals.confirmed)}
          {tile("pending", totals.pending)}
          {tile("declined", totals.declined)}
          {tile("none", totals.noReply)}
        </dl>
      </Panel>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {props.tables.map((table, ti) => {
          const fill = tableFill(table, props.capacity);
          const titleId = `${uid}-t${ti}`;
          return (
            <Panel key={ti} aria-labelledby={titleId} className="flex min-w-0 flex-col">
              <header className="flex items-start gap-3 px-4 pt-4 pb-2 sm:px-5">
                <div className="flex min-w-0 flex-1 flex-col items-start gap-1.5">
                  <h3 id={titleId} className="text-title font-semibold break-words text-ink">
                    {table.name}
                  </h3>
                  {table.label ? (
                    <span className="max-w-full rounded-full bg-hoodie px-2.5 py-0.5 text-label font-medium break-words text-ink-soft">
                      {table.label}
                    </span>
                  ) : null}
                </div>
                <div className="flex w-20 shrink-0 flex-col items-end gap-1.5">
                  <p className={cn("font-mono text-title font-medium tnum", fill.state === "over" ? "text-danger" : "text-ink")}>
                    <span aria-hidden="true">{`${fill.seated} / ${fill.capacity}`}</span>
                    <span className="sr-only">{t("seatingSeated", { seated: fill.seated, capacity: fill.capacity })}</span>
                  </p>
                  <div aria-hidden="true" className="w-full">
                    <Meter
                      value={fill.seated}
                      max={fill.capacity}
                      label={t("seatingMeter", { name: table.name })}
                      tone={fill.state === "over" ? "danger" : fill.state === "full" ? "success" : "denim"}
                    />
                  </div>
                  {fill.over > 0 ? <p className="text-label font-medium text-danger">{t("seatingOver", { count: fill.over })}</p> : null}
                </div>
              </header>
              <div className="flex justify-center py-2">
                <RoundTable seats={table.seats} capacity={props.capacity} />
              </div>
              <ul aria-label={t("seatingGuestsAt", { name: table.name })} className="divide-y divide-hairline border-t border-hairline">
                {table.seats.length === 0 ? (
                  <li className="px-4 py-3 text-meta text-ink-muted sm:px-5">{t("seatingEmptyTable")}</li>
                ) : (
                  table.seats.map((seat, si) => {
                    const key: Key = seat.status ?? "none";
                    const Icon = ICON[key];
                    return (
                      <li key={si} className="flex items-start gap-2.5 px-4 py-2.5 sm:px-5">
                        <Icon aria-hidden className={cn("mt-0.5 size-4 shrink-0", ICON_TONE[key])} />
                        <div className="min-w-0 flex-1">
                          <p className="text-body font-medium break-words text-ink">
                            <span className="sr-only">{`${t(WORD[key])}: `}</span>
                            {seat.name}
                          </p>
                          {seat.role ? <p className="text-meta break-words text-ink-muted">{seat.role}</p> : null}
                          {seat.note ? <p className="text-meta break-words text-ink-soft">{seat.note}</p> : null}
                        </div>
                      </li>
                    );
                  })
                )}
              </ul>
            </Panel>
          );
        })}
      </div>
    </figure>
  );
}
