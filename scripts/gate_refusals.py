"""Reconstruct gate refusal times from Prometheus, for `nora measure participation`.

**A separate script, not a flag on the command, because this is the weak
instrument.** Everything else those measures read is a durable Postgres row.
These are positive deltas on a counter, and the counter has three defects that
no summation repairs:

1. **Scrape resolution.** A delta at *t* means at least one refusal in the
   interval ending at *t*, never exactly one and never exactly then.
2. **Lazy labels plus restarts.** Prometheus creates a labelled series on its
   first observation, so every restart drops a series that has not fired yet
   (a `go` series can be absent for days).
3. **Retention.** ~7 days, after which the refusals simply are not there.

So the output carries `covers_from`, and the CLI REFUSES a file that does not
span the window asked for: a short refusal list makes both refusal-side
measures look clean, which is exactly what they would say if nothing had been
refused. The two must never be confusable.

What actually fixes this is a durable per-verdict row, which DEC-179 excluded
from `decision_log` on purpose. This script is what that decision costs. If
such a row is added, this is deleted and the CLI's `--refusals` reads it.

Run it where Prometheus is reachable (inside the deploy host):

    python3 scripts/gate_refusals.py --range 7d > /tmp/refusals.json
    nora measure participation --days 7 --refusals /tmp/refusals.json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request

DEFAULT_PROMETHEUS = "http://127.0.0.1:9190"
SERIES = ("nora_relevance_gate_total", "nora_relevance_gate_errors_total")


def _query(base: str, expr: str, timeout: float) -> list[dict]:
    url = f"{base.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(url, timeout=timeout) as fh:  # noqa: S310 - operator-supplied base
        payload = json.load(fh)
    if payload.get("status") != "success":
        raise SystemExit(f"prometheus refused {expr!r}: {payload.get('error')}")
    return payload["data"]["result"]


def refusals(base: str, window: str, *, timeout: float) -> tuple[list[dict], int]:
    """Every refusal the counters still remember, as `(rows, covers_from)`.

    RAW SAMPLES, not `query_range`: a range query at any step can step over a
    sample shorter than the step and misreport the counter. Reset-aware,
    treating a drop as all-new.

    A `go` is not a refusal and is skipped. Every kind on the errors series IS
    one: a fail-closed no_go refuses the room exactly as a chosen one does.
    """
    out: list[dict] = []
    earliest: float | None = None
    for name in SERIES:
        for s in _query(base, f"{name}[{window}]", timeout):
            labels = s["metric"]
            if labels.get("verdict") == "go":
                continue
            room = labels.get("conversation_id", "")
            if not room:
                # Samples with no room label cannot be joined to
                # anything. Dropped rather than bucketed under "", which would
                # put unattributable refusals in a denominator as though they
                # had been checked.
                continue
            prev: float | None = None
            for ts, raw in s["values"]:
                # Prometheus speaks float SECONDS; everything downstream is
                # epoch MILLISECONDS. Converted here, once, at the only
                # boundary where the two meet.
                ts, v = float(ts) * 1000.0, float(raw)
                earliest = ts if earliest is None else min(earliest, ts)
                new = v if (prev is None or v < prev) else v - prev
                prev = v
                for _ in range(int(new)):
                    out.append({
                        "conversation_id": room, "at": int(ts), "provenance": "counter",
                    })
    out.sort(key=lambda r: (r["at"], r["conversation_id"]))
    return out, int(earliest or 0)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prometheus", default=DEFAULT_PROMETHEUS)
    ap.add_argument("--range", default="7d", help="Prometheus range selector (default 7d)")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args(argv)

    rows, covers_from = refusals(args.prometheus, args.range, timeout=args.timeout)
    if not rows:
        # Loud on stderr, because an empty list is indistinguishable from «the
        # gate refused nothing» downstream and that is the one reading these
        # measures exist to prevent.
        print(
            "no refusals found — either the gate has refused nothing, or the "
            "conversation_id label has not been deployed yet (it landed "
            "2026-09-16). Check `nora_relevance_gate_total` carries it.",
            file=sys.stderr,
        )
    json.dump({"covers_from": covers_from, "refusals": rows}, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
