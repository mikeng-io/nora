"use client";

import { useEffect, useState } from "react";
import { fetchStatus, type Status } from "@/lib/status-schema";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";

const POLL_INTERVAL_MS = 20_000;

export default function StatusPage() {
  const [status, setStatus] = useState<Status | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      fetchStatus()
        .then((s) => {
          if (!cancelled) {
            setStatus(s);
            setError(false);
          }
        })
        .catch(() => {
          if (!cancelled) setError(true);
        });
    };
    tick();
    const id = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        Nora &middot; public face
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">Status</h1>
      <p className="mb-8 max-w-[56ch] text-text-secondary">
        What&apos;s running, what&apos;s reachable, what she&apos;s still holding.
      </p>

      {error && <Alert variant="destructive" className="mb-6">Couldn&apos;t reach Nora.</Alert>}

      {!error && !status && <p className="text-text-muted">Loading…</p>}

      {status && (
        <>
          <div className="mb-4 grid grid-cols-3 gap-3">
            <Card>
              <CardHeader className="p-4 pb-1">
                <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                  Uptime
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-1">
                <p className="font-mono text-[22px] tabular-nums">
                  {Math.floor(status.uptime_seconds / 3600)}h{" "}
                  {Math.floor((status.uptime_seconds % 3600) / 60)}m
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="p-4 pb-1">
                <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                  Store
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-1">
                <Dot reachable={status.store.reachable} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="p-4 pb-1">
                <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                  Memory
                </CardTitle>
              </CardHeader>
              <CardContent className="p-4 pt-1">
                <Dot reachable={status.honcho.reachable} />
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Reminders pending</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="font-mono text-[12.5px] text-text-secondary">
                {status.reminders.count}
                {status.reminders.next_due_at && ` · next due ${status.reminders.next_due_at}`}
              </p>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function Dot({ reachable }: { reachable: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <span className={`h-1.5 w-1.5 rounded-full ${reachable ? "bg-success" : "bg-danger"}`} />
      {reachable ? "reachable" : "unreachable"}
    </span>
  );
}
