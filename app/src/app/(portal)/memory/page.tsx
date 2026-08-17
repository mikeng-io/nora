"use client";

import { useEffect, useState } from "react";
import * as ScrollArea from "@radix-ui/react-scroll-area";
import { fetchMemory, type MemoryEntry } from "@/lib/memory-schema";

export default function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchMemory()
      .then(setEntries)
      .catch(() => setError(true));
  }, []);

  if (error) return <p className="text-red-600">Couldn&apos;t reach Nora.</p>;
  if (!entries) return <p className="text-[var(--text-muted)]">Loading…</p>;

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        Nora &middot; memory showcase
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">What Nora remembers</h1>
      <p className="mb-8 max-w-[56ch] text-[var(--text-secondary)]">
        Her read on the group &mdash; shared across everyone on the list.
      </p>

      <ScrollArea.Root className="max-h-[70vh] overflow-hidden">
        <ScrollArea.Viewport className="h-full w-full pr-4">
          <div className="space-y-3.5">
            {entries.map((entry) => (
              <section
                key={entry.person_id}
                className="rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-5"
              >
                <h2 className="mb-2.5 flex items-center gap-2 text-[15px] font-semibold">
                  <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent)" }} />
                  {entry.display_name}
                </h2>
                {!entry.reachable && (
                  <p className="font-mono text-[12.5px] text-[var(--text-muted)]">unavailable right now</p>
                )}
                {entry.reachable && entry.conclusions.length === 0 && (
                  <p className="font-mono text-[12.5px] text-[var(--text-muted)]">nothing yet</p>
                )}
                {entry.reachable && entry.conclusions.length > 0 && (
                  <ul className="list-disc space-y-1 pl-4 text-[13.5px] text-[var(--text-secondary)]">
                    {entry.conclusions.map((c, i) => (
                      <li key={i}>{c}</li>
                    ))}
                  </ul>
                )}
              </section>
            ))}
          </div>
        </ScrollArea.Viewport>
        <ScrollArea.Scrollbar orientation="vertical" className="w-1.5">
          <ScrollArea.Thumb className="rounded-full bg-[var(--border-strong)]" />
        </ScrollArea.Scrollbar>
      </ScrollArea.Root>
    </div>
  );
}
