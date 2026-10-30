"""`python -m nora.infra.social.session_probe` — one probe run, then exit.

Exit codes: 0 PASS, 1 FAIL (an alert or a sync happened; the unit treats this
as success), 2 ERROR (something could not be measured; the unit fails).
Env only, no `nora.config` (this is not a composition root).
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from .alerts import FanOutSink, LogSink
from .cookies import read_cookies
from .decide import NETWORKS
from .probe import Probe, ProbeConfig
from .runner import subprocess_runner

EXIT = {"PASS": 0, "FAIL": 1, "ERROR": 2}


def _profile(raw: str) -> str:
    """Nora's Browser Bridge profile; blank refuses, never OpenCLI's default."""
    if not raw.strip():
        raise ValueError("OPENCLI_PROFILE must name Nora's Browser Bridge profile")
    return raw.strip()


def config_from_env(env: dict[str, str] | None = None) -> ProbeConfig:
    e = os.environ if env is None else env
    base = ProbeConfig()
    return ProbeConfig(
        chrome_db=Path(e.get("NORA_PROBE_CHROME_DB", str(base.chrome_db))),
        bridge_dir=Path(e.get("NORA_PROBE_BRIDGE_DIR", str(base.bridge_dir))),
        container=e.get("NORA_PROBE_BRIDGE_CONTAINER", base.container),
        state_file=Path(e.get("NORA_PROBE_STATE_FILE", str(base.state_file))),
        opencli_bin=e.get("OPENCLI_BIN", base.opencli_bin),
        opencli_profile=_profile(e.get("OPENCLI_PROFILE", base.opencli_profile)),
        live_interval=timedelta(seconds=float(e.get("NORA_PROBE_LIVE_INTERVAL_S", "3600"))),
    )


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    cfg = config_from_env()
    probe = Probe(
        cfg,
        read_chrome=lambda: read_cookies(cfg.chrome_db, NETWORKS),
        run=subprocess_runner,
        sink=FanOutSink([LogSink()]),
    )
    verdict, record = probe.run_once()
    logging.getLogger("nora.session_probe").info(
        "verdict=%s decisions=%s", verdict, record["decisions"]
    )
    return EXIT[verdict]


if __name__ == "__main__":
    sys.exit(main())
