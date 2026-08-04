"""Entry point: python -m nora

Ordered, fatal startup validation (`build_app`), an `App` class holding the wired
components, and a `main()` entry point (spec C.1, D.1).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

from . import config
from .agent import factory
from .config import ConfigError
from .handler import Handler
from .infra.honcho import HonchoClient
from .infra.sql import SqlAuditStore, create_sql_engine, run_migrations
from .obs.health import LLMState, build_health_app, serve
from .obs.logging import heartbeat, setup_logging
from .obs.metrics import METRICS
from .obs.tracing import setup_tracing
from .persona.loader import PersonaError, PersonaStore
from .persons import PersonsError, load_persons
from .platform.signal.account import AccountError, derive_account
from .platform.signal.adapter import SignalAdapter

PRUNE_INTERVAL_SECONDS = 300.0

log = logging.getLogger("nora.__main__")


class StartupError(Exception):
    """A fatal startup validation failure — refuse to start, with a readable message."""


async def _prune_loop(store, metrics) -> None:
    """The retention sweep `append` no longer does inline. Owned here until
    `app/runtime.py` exists (pipelines phase)."""
    while True:
        await asyncio.sleep(PRUNE_INTERVAL_SECONDS)
        try:
            deleted = await store.prune_all()
            if deleted:
                log.info("pruned %d rows past the retention window", deleted)
        except Exception:
            log.exception("prune sweep failed")
            metrics.inc("nora_errors_total", stage="prune")


async def build_app(config_path) -> App:
    try:
        cfg = config.load(config_path)
    except ConfigError as exc:
        raise StartupError(str(exc)) from exc

    key = os.environ.get(cfg.llm.api_key_env, "")
    setup_logging(cfg.obs.logging, secrets=[key] if key else [])
    setup_tracing(cfg.obs.tracing)

    try:
        persons = load_persons(cfg.identity_file)
    except PersonsError as exc:
        raise StartupError(f"person mapping error: {exc}") from exc

    try:
        run_migrations(cfg.store.db_path)
    except Exception as exc:
        raise StartupError(f"migration error: {exc}") from exc
    store = SqlAuditStore(
        create_sql_engine(cfg.store.db_path),
        max_per_conversation=cfg.store.max_per_conversation,
        metrics=METRICS,
    )

    try:
        account = await derive_account(
            cfg.platforms.signal.base_url, override=cfg.platforms.signal.account
        )
    except AccountError as exc:
        raise StartupError(f"account error: {exc}") from exc

    persona_store = PersonaStore(
        cfg.persona.dir, strict=cfg.persona.strict, reload=cfg.persona.reload,
        canary=cfg.persona.canary, display_name=cfg.persona.display_name,
        trigger=cfg.engagement.trigger.text,
    )
    try:
        persona = persona_store.get(applies_to="tag")
    except PersonaError as exc:
        raise StartupError(f"persona error: {exc}") from exc

    honcho = HonchoClient(cfg.memory.base_url, cfg.memory.workspace_id)
    await honcho.ensure_workspace()          # best-effort, never fatal — no try/except

    adapter = SignalAdapter(cfg.platforms.signal.base_url, account)
    llm_state = LLMState()
    handler = Handler(cfg=cfg, adapter=adapter, persons=persons, honcho=honcho,
                      persona_store=persona_store, store=store, metrics=METRICS,
                      llm_state=llm_state)

    return App(cfg=cfg, handler=handler, adapter=adapter, account=account,
               persona=persona, honcho=honcho, store=store, llm_state=llm_state)


class App:
    def __init__(self, *, cfg, handler, adapter, account, persona, honcho, store,
                 llm_state=None) -> None:
        self.cfg = cfg
        self.handler = handler
        self.adapter = adapter
        self.account = account
        self.persona = persona
        self.honcho = honcho
        self.store = store
        self.llm_state = llm_state or LLMState()
        self._health_runner = None
        self._bg_tasks: list[asyncio.Task] = []

    async def run(self) -> None:
        health_app = build_health_app(
            adapter=self.adapter, store=self.store, honcho=self.honcho,
            persona_store=self.handler.persona_store, metrics=self.handler.metrics,
            started_at=time.time(), llm_state=self.llm_state,
        )
        self._health_runner = await serve(health_app, self.cfg.obs.metrics.port)
        self._bg_tasks = [
            asyncio.create_task(heartbeat(self.handler.metrics)),
            asyncio.create_task(self._update_last_event_age()),
            asyncio.create_task(_prune_loop(self.store, self.handler.metrics)),
        ]
        try:
            await self.adapter.listen(self.handler.on_event)
        finally:
            for task in self._bg_tasks:
                task.cancel()

    async def _update_last_event_age(self) -> None:
        while True:
            await asyncio.sleep(10.0)
            health = await self.adapter.health()
            age = health.get("last_event_age_seconds")
            if age is not None:
                self.handler.metrics.gauge("nora_last_event_age_seconds", age)

    async def shutdown(self) -> None:
        if self._health_runner is not None:
            await self._health_runner.cleanup()
        await factory.close_client()
        await self.store.aclose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.toml")
    args = parser.parse_args()

    async def _run() -> None:
        app = await build_app(Path(args.config))
        try:
            await app.run()
        finally:
            await app.shutdown()

    try:
        asyncio.run(_run())
    except StartupError as exc:
        print(f"startup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
