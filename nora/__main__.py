"""Entry point: python -m nora

Ordered, fatal startup validation (`build_app`), an `App` class holding the wired
components, and a `main()` entry point (spec C.1, D.1).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from . import config
from .agent import factory
from .config import ConfigError
from .handler import Handler
from .obs.metrics import METRICS
from .persona.loader import PersonaError, PersonaStore
from .persons import PersonsError, load_persons
from .platform.signal.account import AccountError, derive_account
from .platform.signal.adapter import SignalAdapter
from .store import db
from .store.honcho import HonchoClient


class StartupError(Exception):
    """A fatal startup validation failure — refuse to start, with a readable message."""


async def build_app(config_path) -> "App":
    try:
        cfg = config.load(config_path)
    except ConfigError as exc:
        raise StartupError(str(exc)) from exc

    try:
        persons = load_persons(cfg.persons_file)
    except PersonsError as exc:
        raise StartupError(f"person mapping error: {exc}") from exc

    await db.init(cfg.buffer.db_path)

    try:
        account = await derive_account(cfg.signal_cli.base_url, override=cfg.signal_cli.account)
    except AccountError as exc:
        raise StartupError(f"account error: {exc}") from exc

    persona_store = PersonaStore(
        cfg.persona.dir, strict=cfg.persona.strict, reload=cfg.persona.reload,
        canary=cfg.persona.canary, display_name=cfg.persona.display_name,
        trigger=cfg.trigger.text,
    )
    try:
        persona = persona_store.get(applies_to="tag")
    except PersonaError as exc:
        raise StartupError(f"persona error: {exc}") from exc

    honcho = HonchoClient(cfg.honcho.base_url, cfg.honcho.workspace_id)
    await honcho.ensure_workspace()          # best-effort, never fatal — no try/except

    adapter = SignalAdapter(cfg.signal_cli.base_url, account)
    handler = Handler(cfg=cfg, adapter=adapter, persons=persons, honcho=honcho,
                      persona_store=persona_store, metrics=METRICS)

    return App(cfg=cfg, handler=handler, adapter=adapter, account=account,
               persona=persona, honcho=honcho)


class App:
    def __init__(self, *, cfg, handler, adapter, account, persona, honcho) -> None:
        self.cfg = cfg
        self.handler = handler
        self.adapter = adapter
        self.account = account
        self.persona = persona
        self.honcho = honcho

    async def run(self) -> None:
        """M6 stub: the metrics/health server lands in Task 38 — no-op here."""
        await self.adapter.listen(self.handler.on_event)

    async def shutdown(self) -> None:
        await factory.close_client()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.yaml")
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
