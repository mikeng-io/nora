"""Entry point: python -m nora"""

import asyncio
import logging
import sys

import aiohttp

from . import config, db, listener
from .handler import Handler
from .honcho import Honcho
from .llm import LLM
from .signal_send import SignalSender


def setup_logging(cfg) -> None:
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, cfg.logging["level"].upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=[logging.FileHandler(cfg.log_path), logging.StreamHandler(sys.stdout)],
    )


async def main() -> None:
    cfg = config.load()
    setup_logging(cfg)
    log = logging.getLogger("nora")

    await db.init(cfg.db_path)
    log.info("buffer ready at %s", cfg.db_path)

    async with aiohttp.ClientSession() as session:
        honcho = Honcho(session, cfg.honcho["base_url"], cfg.honcho["workspace_id"])
        await honcho.ensure_workspace()
        sender = SignalSender(session, cfg.signal_cli["base_url"])
        llm = LLM(
            cfg.llm["base_url"],
            cfg.llm_api_key,
            cfg.llm["model"],
            cfg.llm["max_tokens"],
            cfg.llm["temperature"],
        )
        handler = Handler(cfg, sender, honcho, llm)
        log.info("Nora listening as %s", cfg.signal_cli["account"])
        await listener.listen(session, cfg.signal_cli["base_url"], handler.on_event)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
