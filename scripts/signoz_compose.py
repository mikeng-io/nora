"""Run the SigNoz backend Compose with Nora's existing private secret source."""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from scripts.secret_delivery import source


def main() -> None:
    root = Path.cwd()
    values = source(root)
    for name in ("SIGNOZ_CLICKHOUSE_PASSWORD", "SIGNOZ_POSTGRES_PASSWORD"):
        # Hex keeps the same value valid in ClickHouse and Postgres DSNs.
        if not re.fullmatch(r"[0-9a-fA-F]{32,}", values.get(name, "")):
            raise SystemExit(f"Set {name} to at least 32 random hex characters in secret.toml")
    os.execvpe(  # noqa: S606 -- replace helper and preserve Docker exit status
        "docker",  # noqa: S607 -- operator Docker CLI from PATH
        ["docker", "compose", "-f", str(root / "deploy/signoz/deployment/compose.yaml"),
         *sys.argv[1:]],
        values,
    )


if __name__ == "__main__":
    main()
