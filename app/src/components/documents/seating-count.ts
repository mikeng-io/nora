import type { Props } from "./types";

type Table = Props<"Seating">["tables"][number];
export type SeatStatus = NonNullable<Table["seats"][number]["status"]>;

export type SeatingTotals = {
  tables: number;
  guests: number;
  confirmed: number;
  pending: number;
  declined: number;
  /** Seats with no `status`. Counted for the reader, never authored. */
  noReply: number;
};

/** What the summary strip shows, counted from the seats themselves. A guest with no
 * `status` is "no reply", so the four buckets always add up to `guests`. */
export function countSeating(tables: readonly Table[]): SeatingTotals {
  const totals: SeatingTotals = { tables: tables.length, guests: 0, confirmed: 0, pending: 0, declined: 0, noReply: 0 };
  for (const table of tables) {
    for (const seat of table.seats) {
      totals.guests += 1;
      if (seat.status === "confirmed") totals.confirmed += 1;
      else if (seat.status === "pending") totals.pending += 1;
      else if (seat.status === "declined") totals.declined += 1;
      else totals.noReply += 1;
    }
  }
  return totals;
}

export type TableFill = {
  seated: number;
  capacity: number;
  /** Free seats; never negative. */
  free: number;
  /** Guests beyond `capacity`; 0 when it fits. */
  over: number;
  state: "empty" | "partial" | "full" | "over";
};

/** One table against the plan's seats-per-table. Every listed guest holds a seat,
 * whatever they replied: a declined guest is still on the plan until it is edited. */
export function tableFill(table: Pick<Table, "seats">, capacity: number): TableFill {
  const seated = table.seats.length;
  const over = Math.max(0, seated - capacity);
  const state = seated === 0 ? "empty" : over > 0 ? "over" : seated === capacity ? "full" : "partial";
  return { seated, capacity, free: Math.max(0, capacity - seated), over, state };
}

/** `n` points evenly round a circle, the first at twelve o'clock, going clockwise. */
export function seatPositions(n: number, cx: number, cy: number, r: number): Array<{ x: number; y: number }> {
  return Array.from({ length: n }, (_, i) => {
    const a = -Math.PI / 2 + (2 * Math.PI * i) / n;
    return { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
  });
}
