"use client";

import { useEffect, useState } from "react";
import { fetchStatus, type Status } from "@/lib/status-schema";

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

  if (error) return <p className="text-red-600">Couldn&apos;t reach Nora.</p>;
  if (!status) return <p className="text-[var(--text-muted)]">Loading…</p>;

  const hours = Math.floor(status.uptime_seconds / 3600);
  const minutes = Math.floor((status.uptime_seconds % 3600) / 60);

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
        Nora &middot; public face
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">Status</h1>
      <p className="mb-8 max-w-[56ch] text-[var(--text-secondary)]">
        What&apos;s running, what&apos;s reachable, what she&apos;s still holding.
      </p>

      <div className="mb-7 grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-[var(--border)] bg-[var(--border)]">
        <div className="bg-[var(--bg-surface)] p-5">
          <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            Uptime
          </p>
          <p className="font-mono text-[22px] tabular-nums">
            {hours}h {minutes}m
          </p>
        </div>
        <div className="bg-[var(--bg-surface)] p-5">
          <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            Store
          </p>
          <Dot reachable={status.store.reachable} />
        </div>
        <div className="bg-[var(--bg-surface)] p-5">
          <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-[var(--text-muted)]">
            Memory
          </p>
          <Dot reachable={status.honcho.reachable} />
        </div>
      </div>

      <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-surface)] p-5">
        <p className="mb-1 text-[13.5px] font-semibold">Reminders pending</p>
        <p className="font-mono text-[12.5px] text-[var(--text-secondary)]">
          {status.reminders.count}
          {status.reminders.next_due_at && ` · next due ${status.reminders.next_due_at}`}
        </p>
      </div>
    </div>
  );
}

function Dot({ reachable }: { reachable: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-sm">
      <span
        className="h-1.5 w-1.5 rounded-full"
        style={{ background: reachable ? "var(--success)" : "var(--danger)" }}
      />
      {reachable ? "reachable" : "unreachable"}
    </span>
  );
}
