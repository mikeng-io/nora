#!/usr/bin/env python3
"""Provision SigNoz alert channels and rules from deploy/signoz/alerts/rules.yaml.

The 19 Prometheus rules port 1:1 as declarative entries; this script is the
minimal applier, not a framework — read the YAML, upsert channels, upsert
rules, print what it changed. API access is a SigNoz API key named in the
environment (secret.toml-style: `SIGNOZ_API_KEY` env var or `NORA_SIGNOZ_API_KEY`).

Usage:
    SIGNOZ_API_KEY=... uv run python scripts/signoz_provision.py \\
        [--api http://localhost:8080] [--dry-run]

Never run against a real Discord webhook in tests; the channel endpoint is
whatever local HTTP receiver the operator configures.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

try:
    import yaml
except ImportError:
    yaml = None


def _load(path: str) -> dict:
    if yaml is not None:
        return yaml.safe_load(open(path))
    raise SystemExit("pyyaml required: uv run python scripts/signoz_provision.py")


def _req(api: str, key: str, method: str, path: str, body=None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{api}{path}", data=data, method=method,
        headers={"Content-Type": "application/json", "SIGNOZ-API-KEY": key})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _rule_payload(name: str, rule: dict, defaults: dict, channel_id: str) -> dict:
    severity = rule.get("severity", "warning")
    alert = {
        "alert": name,
        "alertType": "METRIC_BASED_ALERT",
        "ruleType": rule["kind"] == "absent" and "absent_rule" or "threshold_rule",
        "condition": {
            "compositeQuery": {
                "queryType": "prom",
                "panelType": "graph",
                "promQueries": [{"name": "A", "query": rule["query"], "disabled": False}],
            },
            "alertOnAbsent": rule["kind"] == "absent",
            "absentFor": rule.get("for_seconds", 0),
            "op": "4",  # above
            "matchType": "4",
            "target": 1.0 if rule["kind"] == "absent" else 0.0,
            "selectedQueryName": "A",
        },
        "severity": severity.upper(),
        "labels": {"alertname": name, "severity": severity},
        "annotations": {"summary": rule.get("summary", name)},
        "evalWindow": defaults.get("eval_window", "5m0s"),
        "broadcastToAll": False,
        "preferredChannels": [channel_id],
        "version": "v5",
    }
    return alert


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8080")
    ap.add_argument("--rules", default="deploy/signoz/alerts/rules.yaml")
    ap.add_argument("--channel-name", default="nora-alerts")
    ap.add_argument("--webhook-env", default="NORA_ALERT_WEBHOOK")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = os.environ.get("SIGNOZ_API_KEY") or os.environ.get("NORA_SIGNOZ_API_KEY", "")
    webhook = os.environ.get(args.webhook_env, "http://alert-sink:8001/webhook")
    doc = _load(args.rules)
    defaults = doc.get("defaults", {})
    rules = doc.get("rules", [])
    print(f"{len(rules)} rules in {args.rules}; api={args.api}; "
          f"channel={args.channel_name}; dry_run={args.dry_run}")
    if args.dry_run:
        for r in rules:
            print(f"  {r['name']:<32} {r['kind']:<9} {r['query'][:70]}")
        return 0
    if not key:
        print("SIGNOZ_API_KEY (or NORA_SIGNOZ_API_KEY) required", file=sys.stderr)
        return 2

    # Upsert the notification channel (v2 channel API verified against the
    # running v0.148.1 backend).
    code, channels = _req(args.api, key, "GET", "/api/v2/channels")
    if code != 200:
        print(f"list channels: HTTP {code}: {channels}", file=sys.stderr)
        return 2
    existing = {c.get("name"): c for c in channels.get("data", [])}
    channel_id = (existing.get(args.channel_name) or {}).get("id")
    if not channel_id:
        body = {
            "name": args.channel_name,
            "webhook_configs": [{"url": webhook, "send_resolved": True}],
        }
        code, created = _req(args.api, key, "POST", "/api/v2/channels", body)
        if code not in (200, 201):
            print(f"create channel: HTTP {code}: {created}", file=sys.stderr)
            return 2
        channel_id = created.get("data", {}).get("id") or created.get("id")
    print(f"channel {args.channel_name}: {channel_id}")

    code, rules_resp = _req(args.api, key, "GET", "/api/v1/rules")
    existing_rules = {}
    if code == 200:
        for r in rules_resp.get("data", rules_resp if isinstance(rules_resp, list) else []):
            existing_rules[r.get("alert") or r.get("name")] = r
    ok = 0
    for r in rules:
        payload = _rule_payload(r["name"], r, defaults, channel_id)
        if r["name"] in existing_rules:
            code, out = _req(args.api, key, "PUT",
                             f"/api/v1/rules/{existing_rules[r['name']].get('id')}", payload)
        else:
            code, out = _req(args.api, key, "POST", "/api/v1/rules", payload)
        if code in (200, 201):
            ok += 1
        else:
            print(f"  rule {r['name']}: HTTP {code}: {out}", file=sys.stderr)
    print(f"provisioned {ok}/{len(rules)} rules")
    return 0 if ok == len(rules) else 1


if __name__ == "__main__":
    raise SystemExit(main())
