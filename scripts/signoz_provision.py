#!/usr/bin/env python3
"""Provision SigNoz alert channels and rules from deploy/signoz/alerts/rules.yaml.

The 19 Prometheus rules port 1:1 as declarative entries; this script is the
minimal applier, not a framework — read the YAML, upsert the notification
channel, upsert every rule, print what it changed. The API contract is the
one the pinned backend actually serves (verified against signoz:v0.148.1):

* channels: `GET/POST/PUT /api/v1/channels`, Alertmanager receiver shape
  (`webhook_configs` — a URL, send_resolved). Rules reference channels by
  their DISPLAY NAME in each threshold's `channels` list.
* rules: `GET/POST /api/v2/rules`, `PUT /api/v2/rules/{id}`,
  `schemaVersion: v2alpha1` PostableRule — promql query in
  `condition.compositeQuery.queries`, the comparison as a `basic` threshold
  (op + target + matchType), and the hold window as a `rolling` evaluation.
* `for: Nm` ⇒ matchType `all_the_times` + evalWindow Nm + `min_points`
  (condition.requireMinPoints/requiredNumPoints — a series that has not
  existed for the whole window is skipped, so the hold cannot fire early);
  `for: 0m` ⇒ matchType `last` + evalWindow 1m, so recovery resolves on the
  current point instead of a stale one inside the window. Queries that carry
  their own comparison emit an UNFILTERED 0/1 series (`bool`, `or vector(0)`,
  `a - b or a`) — a filtered series would fire on one bad point and linger
  after recovery. There is no seconds-granularity `absentFor` — the API
  field exists but the backend multiplies it by MINUTES, so this script
  refuses to write it; absence is `absent()` in the query itself.

Access is a SigNoz API key from the environment or the runtime secret store
(`SIGNOZ_API_KEY` / `NORA_SIGNOZ_API_KEY`; secret.toml-style, a name never a
value). API errors are reported by status code only — a response body can
echo request material, so it is never printed.

Usage:
    uv run python scripts/signoz_provision.py \
        [--api http://localhost:8080] [--dry-run]

The channel URL comes from `NORA_ALERT_WEBHOOK` (or the runtime secret
store): the existing alert-bridge's webhook URL during transition, a Discord
webhook for native `discord_configs`, or a local sink for tests. Never run
against a real Discord endpoint in tests.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    from nora.secret import resolve as _resolve_secrets
except Exception:  # noqa: BLE001 — resolve() absent or the store locked, either way env-only
    _resolve_secrets = None

#: Ops/matchTypes the backend accepts; the YAML vocabulary is validated to
#: this closed set so a typo fails here, not as a silent default downstream.
_OPS = {"above", "below", "equal", "not_equal", "above_or_equal",
        "below_or_equal", "outside_bounds"}
#: The five matchTypes the pinned backend's threshold evaluator implements
#: (pkg/types/ruletypes MatchType) — `min`/`max` are not among them.
_MATCHES = {"at_least_once", "all_the_times", "on_average", "in_total",
            "last"}


def _secret(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name]
    if _resolve_secrets is not None:
        try:
            return _resolve_secrets(Path("secret.toml")).get(name, "")
        except Exception:  # noqa: BLE001 — a locked/unreadable store means "no secret", not a crash
            return ""
    return ""


def _load(path: str) -> dict:
    if yaml is None:
        raise SystemExit("pyyaml required: uv run python scripts/signoz_provision.py")
    return yaml.safe_load(open(path))


def _req(api: str, key: str, method: str, path: str,
         body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(  # noqa: S310 — --api is the operator's own SigNoz base URL
        f"{api}{path}", data=data, method=method,
        headers={"Content-Type": "application/json", "SIGNOZ-API-KEY": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 — the API base is the operator's own SigNoz URL
            payload = r.read() or b"{}"
    except urllib.error.HTTPError as e:
        # NEVER echo the body: an API error can quote request material back
        # (the webhook URL, channel names), so the status code is the report.
        return e.code, {}
    except OSError as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        # A non-JSON 200 is the SPA fallback page, not an API answer — treat
        # it as a miss rather than trusting the status code.
        return 0, {"error": "non-JSON response (wrong endpoint?)"}
    return 200, parsed if isinstance(parsed, dict) else {"data": parsed}


def _rule_payload(name: str, rule: dict, defaults: dict, channel: str) -> dict:
    if rule.get("signal") == "logs":
        for key in ("query", "op", "target", "match", "window", "severity"):
            if key not in rule:
                raise SystemExit(f"rule {name}: missing required key {key!r}")
        if rule["op"] not in _OPS:
            raise SystemExit(f"rule {name}: op {rule['op']!r} not in {sorted(_OPS)}")
        if rule["match"] not in _MATCHES:
            raise SystemExit(f"rule {name}: match {rule['match']!r} not in {sorted(_MATCHES)}")
        annotations: dict[str, str] = {"summary": rule.get("summary", name)}
        if rule.get("description"):
            annotations["description"] = rule["description"]
        return {
            "alert": name,
            "alertType": "LOGS_BASED_ALERT",
            "ruleType": "threshold_rule",
            "version": "v5",
            "schemaVersion": "v2alpha1",
            "condition": {
                "compositeQuery": {
                    "queryType": "clickhouse_sql",
                    "panelType": "graph",
                    "queries": [{
                        "type": "clickhouse_sql",
                        "spec": {"name": "A", "query": rule["query"],
                                 "disabled": False},
                    }],
                },
                "selectedQueryName": "A",
                "thresholds": {
                    "kind": "basic",
                    "spec": [{
                        "name": rule["severity"],
                        "op": rule["op"],
                        "matchType": rule["match"],
                        "target": rule["target"],
                        "channels": [channel],
                    }],
                },
            },
            "evaluation": {
                "kind": "rolling",
                "spec": {
                    "evalWindow": rule["window"],
                    "frequency": defaults.get("frequency", "1m"),
                },
            },
            "notificationSettings": {"usePolicy": False},
            "labels": {"alertname": name, "severity": rule["severity"]},
            "annotations": annotations,
        }
    for key in ("query", "op", "target", "match", "window", "severity"):
        if key not in rule:
            raise SystemExit(f"rule {name}: missing required key {key!r}")
    if rule["op"] not in _OPS:
        raise SystemExit(f"rule {name}: op {rule['op']!r} not in {sorted(_OPS)}")
    if rule["match"] not in _MATCHES:
        raise SystemExit(f"rule {name}: match {rule['match']!r} not in {sorted(_MATCHES)}")
    annotations: dict[str, str] = {"summary": rule.get("summary", name)}
    if rule.get("description"):
        annotations["description"] = rule["description"]
    return {
        "alert": name,
        "alertType": "METRIC_BASED_ALERT",
        "ruleType": "promql_rule",
        "version": "v5",
        "schemaVersion": "v2alpha1",
        "condition": {
            "compositeQuery": {
                "queryType": "promql",
                "panelType": "graph",
                "queries": [{
                    "type": "promql",
                    "spec": {"name": "A", "query": rule["query"]},
                }],
            },
            "selectedQueryName": "A",
            "thresholds": {
                "kind": "basic",
                "spec": [{
                    "name": rule["severity"],
                    "op": rule["op"],
                    "matchType": rule["match"],
                    "target": rule["target"],
                    "channels": [channel],
                }],
            },
            **_min_points(rule),
        },
        "evaluation": {
            "kind": "rolling",
            "spec": {
                "evalWindow": rule["window"],
                "frequency": defaults.get("frequency", "1m"),
            },
        },
        "notificationSettings": {"usePolicy": False},
        "labels": {"alertname": name, "severity": rule["severity"]},
        "annotations": annotations,
    }


def _min_points(rule: dict) -> dict:
    """`min_points` -> condition.requireMinPoints/requiredNumPoints.

    The hold half of `for: Nm`: all_the_times alone fires as soon as every
    EXISTING point satisfies the threshold, so a series that has only
    existed for part of the window would fire early. Requiring the full
    window's point count (60s step) is what Prometheus's `for` enforces by
    time. Only metric rules carry it — the log rule's at_least_once page
    does not want it.
    """
    points = rule.get("min_points")
    if not points:
        return {}
    return {"requireMinPoints": True, "requiredNumPoints": int(points)}


def _channel_payload(name: str, webhook: str, kind: str) -> dict:
    """The channel body. `discord` means a real Discord webhook through the
    backend's NATIVE slack receiver: `discord_configs` does not exist in the
    pinned community API (v0.148.1 channel kinds: slack, webhook, pagerduty,
    opsgenie, msteams, googlechat, jira, jsmops, incidentio, telegram, email —
    verified live, `channel_unsupported_kind` on discord_configs), and Discord
    itself consumes the Slack message format at a webhook URL suffixed
    `/slack`. So `slack_configs` + the operator's
    `https://discord.com/api/webhooks/.../slack` URL IS the native path —
    no bridge, no intermediate service. `webhook` is the generic
    Alertmanager JSON sink, kept as the explicit TEST channel for a local
    recording endpoint — it is not a Discord integration and is never
    documented as one."""
    if kind == "discord":
        return {"name": name,
                "slack_configs": [{"api_url": webhook,
                                   "channel": "#nora-alerts",
                                   "send_resolved": True}]}
    return {"name": name,
            "webhook_configs": [{"url": webhook, "send_resolved": True}]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--rules", default="deploy/signoz/alerts/rules.yaml")
    ap.add_argument("--channel-name", default=None,
                    help="receiver display name (default: defaults.channel)")
    ap.add_argument("--channel-kind", choices=("discord", "webhook"),
                    default="discord",
                    help="'webhook' is the explicit local-test sink, not Discord")
    ap.add_argument("--webhook-env", default="NORA_ALERT_WEBHOOK")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    doc = _load(args.rules)
    defaults = doc.get("defaults", {})
    rules = doc.get("rules", [])
    channel = args.channel_name or defaults.get("channel", "nora-alerts")
    webhook = _secret(args.webhook_env)
    print(f"{len(rules)} rules in {args.rules}; api={args.api}; "
          f"channel={channel}; dry_run={args.dry_run}")
    if args.dry_run:
        for r in rules:
            print(f"  {r['name']:<30} {r['port'][:80]}")
        return 0

    key = _secret("SIGNOZ_API_KEY") or _secret("NORA_SIGNOZ_API_KEY")
    if not key:
        print("SIGNOZ_API_KEY (or NORA_SIGNOZ_API_KEY) required", file=sys.stderr)
        return 2

    # --- channel upsert (idempotent: create once, then keep it current) ---
    code, channels = _req(args.api, key, "GET", "/api/v1/channels")
    if code != 200 or not isinstance(channels.get("data"), list):
        print(f"list channels: HTTP {code}", file=sys.stderr)
        return 2
    existing = {c.get("name"): c for c in channels["data"]}
    if not webhook and channel in existing:
        channel_id = existing[channel]["id"]
        print(f"channel {channel}: {channel_id} (kept; no webhook to apply)")
    else:
        if not webhook:
            print(f"{args.webhook_env} required to create channel {channel}",
                  file=sys.stderr)
            return 2
        body = _channel_payload(channel, webhook, args.channel_kind)
        if channel in existing:
            channel_id = existing[channel]["id"]
            code, _ = _req(args.api, key, "PUT",
                           f"/api/v1/channels/{channel_id}", body)
        else:
            code, created = _req(args.api, key, "POST", "/api/v1/channels", body)
            channel_id = (created.get("data") or {}).get("id")
        if code not in (200, 201):
            print(f"upsert channel {channel}: HTTP {code}", file=sys.stderr)
            return 2
        print(f"channel {channel}: {channel_id}")

    # --- rule upserts ---
    # FAIL CLOSED: if the existing rules cannot be listed, POSTing blind would
    # create a second copy of every rule under a new id. A failed list is a
    # stop, never a "no rules exist" assumption (the R-27 shape).
    code, rules_resp = _req(args.api, key, "GET", "/api/v2/rules")
    if code != 200:
        print(f"list rules: HTTP {code}", file=sys.stderr)
        return 2
    existing_rules: dict[str, str] = {}
    rows = rules_resp.get("data") or {}
    for r in (rows.get("rules") if isinstance(rows, dict) else rows) or []:
        if isinstance(r, dict) and r.get("alert") and r.get("id"):
            existing_rules[r["alert"]] = r["id"]
    ok, failed = 0, []
    for r in rules:
        payload = _rule_payload(r["name"], r, defaults, channel)
        if r["name"] in existing_rules:
            code, _ = _req(args.api, key, "PUT",
                           f"/api/v2/rules/{existing_rules[r['name']]}", payload)
        else:
            code, _ = _req(args.api, key, "POST", "/api/v2/rules", payload)
        if code in (200, 201):
            ok += 1
        else:
            failed.append(r["name"])
            print(f"  rule {r['name']}: HTTP {code}", file=sys.stderr)
    print(f"provisioned {ok}/{len(rules)} rules")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
