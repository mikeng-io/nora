#!/usr/bin/env python3
"""Composition root for the standalone bridge-projection publisher.

`nora.platform.bridge.projection` is deliberately pure — `nora.platform` may
not read `nora.config` or `nora.policy` (import-linter contracts). This script
is the deploy-side composition root (same shape as deploy/signal-bridge's
wrapper.py): it wires the REAL loaders — `config.load(require_secrets=False)`,
`ReloadFiles`, the app's `ACK` receipt constant — into the publisher and runs
its long-lived loop. No secrets are mounted: secret.toml is never opened.

Two process shapes, one image:

* `--platform <p>` — the original single-platform publisher. Requires
  `NORA_BRIDGE_ACCOUNT_KEY` (or `<P>_BRIDGE_ACCOUNT_KEY`) verbatim; without an
  explicit opaque leg it refuses rc=2, because a guessed leg is a guessed grant.
* `--all` — one process hosting every platform's publisher against the same
  config/policy/identity mounts (the deployment's shape: one
  `bridge-projection` service for all four bridges). Per-platform account legs
  come from `<PLATFORM>_BRIDGE_ACCOUNT_KEY`, falling back to the shared
  `NORA_BRIDGE_ACCOUNT_KEY`; a DISABLED platform needs no credential and still
  publishes its explicit `enabled:false` deny file, and one platform's missing
  leg publishes its own deny artifact + staleness instead of deadlocking the
  fleet. Per-platform endpoints come from `<PLATFORM>_BRIDGE_BASE_URL` /
  `<PLATFORM>_BRIDGE_SELF_URL` / `<PLATFORM>_BRIDGE_READS_URL`, then the shared
  `NORA_BRIDGE_*` envs, then the platform's configured `base_url` — the bridge
  address the app's own client uses.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import functools
import logging
import os
import sys
from pathlib import Path
from typing import Any

from nora import config
from nora.engagement.stages.indicate import ACK
from nora.obs.logging import setup_logging
from nora.obs.metrics import Metrics
from nora.platform.bridge.projection import (
    DEFAULT_PROJECTION_DIR,
    ENV_ACCOUNT_KEY,
    ENV_API_TOKEN,
    ENV_BASE_URL,
    ENV_BRIDGE_PORT,
    ENV_HEALTH_PORT,
    ENV_PLATFORM,
    ENV_PROJECTION_DIR,
    ENV_READS_URL,
    ENV_SELF_URL,
    ENV_TOKEN_FALLBACK,
    PLATFORMS,
    ProjectionPublisher,
    run_fleet,
)


def _platform_env(platform: str, suffix: str) -> str | None:
    """`<PLATFORM>_BRIDGE_<SUFFIX>` — the per-platform env family compose sets."""
    return os.environ.get(f"{platform.upper()}_BRIDGE_{suffix}") or None


def _bridge_base(platform: str) -> str | None:
    """One bridge base URL, when the deployment names it — the single knob
    `--self-url` and `--reads-url` both derive from."""
    return _platform_env(platform, "BASE_URL") or os.environ.get(ENV_BASE_URL) or None


def _self_url(platform: str) -> str | None:
    """The bridge's own /v1/self. Explicit env wins; the platform's base or a
    same-container bridge (`NORA_BRIDGE_PORT` set) derive it."""
    if url := _platform_env(platform, "SELF_URL") or os.environ.get(ENV_SELF_URL):
        return url
    if base := _bridge_base(platform):
        return f"{base}/v1/self"
    if port := os.environ.get(ENV_BRIDGE_PORT):
        return f"http://127.0.0.1:{port}/v1/self"
    return None


def _reads_url(platform: str) -> str | None:
    """The bridge's POST /v1/reads (guild channel directory). Explicit env,
    then the platform base — the publisher's last resort is the platform's
    configured `base_url` (bridge-mode deployments only)."""
    if url := _platform_env(platform, "READS_URL") or os.environ.get(ENV_READS_URL):
        return url
    if base := _bridge_base(platform):
        return f"{base}/v1/reads"
    return None


def _boot_config(path: Path) -> Any:
    """One best-effort boot load for `--all`: enumerates enabled platforms and
    their configured base_url/account_key. A refusal here changes nothing —
    every publisher retries the same load on its own tick and fails closed."""
    try:
        return config.load(path, require_secrets=False)
    except Exception:  # noqa: BLE001 — the publishers' own load reports honestly
        return None


def _fleet_publishers(
    cfg: Any, *, config_path: Path, out_dir: Path, metrics: Metrics,
) -> list[ProjectionPublisher]:
    """One publisher per platform; a platform's missing leg denies itself only."""
    token = os.environ.get(ENV_API_TOKEN) or os.environ.get(ENV_TOKEN_FALLBACK)
    publishers = []
    for platform in PLATFORMS:
        platform_cfg = getattr(cfg.platforms, platform, None) if cfg else None
        enabled = platform_cfg.enabled if platform_cfg is not None else True
        key = _platform_env(platform, "ACCOUNT_KEY") or os.environ.get(ENV_ACCOUNT_KEY)
        if not key:
            # Enabled with no env leg: a sentinel that mismatches every real
            # configured leg, so the tick publishes the deny artifact and
            # reports key_mismatch — fail closed, never a guessed grant. A
            # disabled platform needs no credential; its configured leg (or a
            # placeholder) rides the enabled:false artifact for provenance.
            key = ("unset" if enabled
                   else getattr(platform_cfg, "account_key", "") or "disabled")
        base = getattr(platform_cfg, "base_url", "") or ""
        build = functools.partial(
            ProjectionPublisher, platform=platform, config_path=config_path,
            out_dir=out_dir,
            config_loader=functools.partial(config.load, require_secrets=False),
            ack_emoji=ACK, metrics=metrics,
            self_url=_self_url(platform) or (f"{base}/v1/self" if base else None),
            self_token=token,
            reads_url=_reads_url(platform) or (f"{base}/v1/reads" if base else None),
        )
        try:
            publishers.append(build(account_key=key))
        except ValueError:
            # A malformed leg (env or config) must not crash the fleet: publish
            # the platform's deny artifact under a guaranteed-mismatch sentinel.
            publishers.append(build(account_key="unset"))
    return publishers


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bridge-projection")
    parser.add_argument(
        "--platform", choices=PLATFORMS,
        default=os.environ.get(ENV_PLATFORM) or None,
        help="bridge platform this projection serves (env: NORA_BRIDGE_PLATFORM)",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="host every platform's publisher in one process with one shared "
        "health surface (per-platform legs: <PLATFORM>_BRIDGE_ACCOUNT_KEY)",
    )
    parser.add_argument(
        "--config", default="config.toml",
        help="path to config.toml (policy/identity files resolve from it)",
    )
    parser.add_argument(
        "--projection-dir",
        default=os.environ.get(ENV_PROJECTION_DIR, DEFAULT_PROJECTION_DIR),
        help="directory holding {platform}.json (env: NORA_BRIDGE_PROJECTION_DIR)",
    )
    parser.add_argument(
        "--self-url", default=None,
        help="single mode only: bridge /v1/self URL (env: NORA_BRIDGE_SELF_URL, "
        "or NORA_BRIDGE_PORT loopback). Unset: config floor only.",
    )
    parser.add_argument(
        "--reads-url", default=None,
        help="single mode only: bridge POST /v1/reads endpoint for the guild "
        "channel directory (env: NORA_BRIDGE_READS_URL or NORA_BRIDGE_BASE_URL; "
        "fallback: the platform's configured base_url).",
    )
    parser.add_argument(
        "--health-port", type=int,
        default=int(os.environ.get(ENV_HEALTH_PORT, "0")),
        help="optional /health + /live + /metrics port (env: NORA_BRIDGE_PROJECTION_PORT)",
    )
    return parser


def _single(args: argparse.Namespace) -> int:
    """The original one-platform shape — explicit env leg or rc=2."""
    if args.platform is None:
        print("--platform (or NORA_BRIDGE_PLATFORM) is required", file=sys.stderr)
        return 2
    account_key = (
        _platform_env(args.platform, "ACCOUNT_KEY")
        or os.environ.get(ENV_ACCOUNT_KEY, "")
    )
    if not account_key.strip():
        # The account leg is authoritative and opaque — required by the stream
        # name and the app's reader; there is nothing honest to publish
        # without it, so refuse rather than write a guessed leg.
        print(f"{ENV_ACCOUNT_KEY} is required and must be non-empty", file=sys.stderr)
        return 2
    token = os.environ.get(ENV_API_TOKEN) or os.environ.get(ENV_TOKEN_FALLBACK)
    setup_logging(
        level="INFO", format="json",
        secrets=[v for v in (token, account_key) if v],
    )
    try:
        publisher = ProjectionPublisher(
            platform=args.platform, config_path=Path(args.config),
            out_dir=Path(args.projection_dir), account_key=account_key,
            config_loader=functools.partial(config.load, require_secrets=False),
            ack_emoji=ACK,
            self_url=args.self_url or _self_url(args.platform),
            self_token=token,
            reads_url=args.reads_url or _reads_url(args.platform),
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 2
    logging.getLogger("nora.bridge.projection").info(
        "bridge projection publisher starting: platform=%s dir=%s",
        args.platform, args.projection_dir,
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(publisher.run(health_port=args.health_port))
    return 0


def _all(args: argparse.Namespace) -> int:
    """One process, every platform: each leg's failure denies only itself."""
    token = os.environ.get(ENV_API_TOKEN) or os.environ.get(ENV_TOKEN_FALLBACK)
    setup_logging(level="INFO", format="json", secrets=[token] if token else [])
    metrics = Metrics()
    cfg = _boot_config(Path(args.config))
    publishers = _fleet_publishers(
        cfg, config_path=Path(args.config),
        out_dir=Path(args.projection_dir), metrics=metrics,
    )
    logging.getLogger("nora.bridge.projection").info(
        "bridge projection fleet starting: platforms=%s dir=%s",
        [p.platform for p in publishers], args.projection_dir,
    )
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(run_fleet(
            publishers, health_port=args.health_port, metrics=metrics))
    return 0


def main() -> int:
    args = build_parser().parse_args()
    if args.all:
        return _all(args)
    return _single(args)


if __name__ == "__main__":
    raise SystemExit(main())
