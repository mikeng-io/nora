"use client";

import { useMemo } from "react";
import { usePresence } from "@/lib/queries";
import { agePresence } from "@/lib/presence";
import { useNow } from "./use-now";

/** Everyone's latest fix, aged by the time since it was read (lib/presence
 * agePresence says why). Every page that shows where people are reads it
 * through this, so none of them can call an old position "now". */
export function usePresenceNow() {
  const query = usePresence();
  const now = useNow();
  const data = useMemo(
    () => (query.data && now ? agePresence(query.data, query.dataUpdatedAt, now) : query.data),
    [query.data, query.dataUpdatedAt, now],
  );
  return { ...query, data };
}
