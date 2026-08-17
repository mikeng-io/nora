"use client";

import { useEffect, useState } from "react";
import * as ScrollArea from "@radix-ui/react-scroll-area";
import { fetchMemory, type MemoryEntry } from "@/lib/memory-schema";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Alert } from "@/components/ui/alert";

function initialsOf(name: string) {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

export default function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetchMemory()
      .then(setEntries)
      .catch(() => setError(true));
  }, []);

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        Nora &middot; memory showcase
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">What Nora remembers</h1>
      <p className="mb-8 max-w-[56ch] text-text-secondary">
        Her read on the group &mdash; shared across everyone on the list.
      </p>

      {error && <Alert variant="destructive">Couldn&apos;t reach Nora.</Alert>}
      {!error && !entries && <p className="text-text-muted">Loading…</p>}

      {entries && (
        <ScrollArea.Root className="max-h-[70vh] overflow-hidden">
          <ScrollArea.Viewport className="h-full w-full pr-4">
            <div className="space-y-3">
              {entries.map((entry) => (
                <Card key={entry.person_id}>
                  <CardHeader className="flex-row items-center gap-2.5 space-y-0 pb-2">
                    <Avatar className="h-7 w-7">
                      <AvatarFallback>{initialsOf(entry.display_name)}</AvatarFallback>
                    </Avatar>
                    <CardTitle>{entry.display_name}</CardTitle>
                  </CardHeader>
                  <CardContent className="pt-0">
                    {!entry.reachable && (
                      <p className="font-mono text-[12.5px] text-text-muted">unavailable right now</p>
                    )}
                    {entry.reachable && entry.conclusions.length === 0 && (
                      <p className="font-mono text-[12.5px] text-text-muted">nothing yet</p>
                    )}
                    {entry.reachable && entry.conclusions.length > 0 && (
                      <ul className="list-disc space-y-1 pl-4 text-[13.5px] text-text-secondary">
                        {entry.conclusions.map((c, i) => (
                          <li key={i}>{c}</li>
                        ))}
                      </ul>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          </ScrollArea.Viewport>
          <ScrollArea.Scrollbar orientation="vertical" className="w-1.5">
            <ScrollArea.Thumb className="rounded-full bg-border-strong" />
          </ScrollArea.Scrollbar>
        </ScrollArea.Root>
      )}
    </div>
  );
}
