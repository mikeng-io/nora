"""A stubbed host for `deploy/computer/nora-computer` (not a test file).

The script is the only thing that runs `sbx`, and it also shells out to `docker
compose exec db psql`, `flock`, `timeout` and `sha256sum`. Each is a stub on a
private PATH, behaving like the real one on the properties the script reads, and
logging every call to `$STUB_DIR/calls.log`, so a call the script should not
make is visible and an unexpected one exits 1 rather than succeeding silently.

What a stub cannot show is the real binary: that `sbx` takes these arguments and
answers in these words is the README's read-back on the deploy host.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy" / "computer" / "nora-computer"

NAME = "nora-computer"
HOST_IP = "172.17.0.1"
HOST_PORT = "18765"

_SBX = r"""#!/bin/bash
# A stub sbx. State lives in $STUB_DIR; every call is logged to calls.log.
echo "sbx $*" >> "$STUB_DIR/calls.log"
allowed="$STUB_DIR/allowed"
touch "$allowed"
case "$1 $2 $3" in
  "policy check network")
      shift 3
      [ "${STUB_CHECK_BROKEN:-}" = 1 ] && { echo "policy engine unavailable"; exit 0; }
      target=""; for a in "$@"; do target="$a"; done
      if grep -qxF "$target" "$allowed"; then
          echo "Allowed: $target"; echo "Governance: Local policy only"
      else
          echo "Denied: $target"; echo "Reason: no matching allow rule (default deny)"
      fi
      exit 0 ;;
  "policy allow network")
      shift 3
      [ "${STUB_ALLOW_FAILS:-}" = 1 ] && { echo "policy store is read-only" >&2; exit 1; }
      res=""; for a in "$@"; do res="$a"; done
      echo "$res" >> "$allowed"
      exit 0 ;;
  "policy log "*)
      echo "policy-log-output"; exit 0 ;;
esac
case "$1" in
  ls)
      if [ -e "$STUB_DIR/sandbox" ]; then
          echo '{"sandboxes": [{"name": "nora-computer", "status": "running"}]}'
      else
          echo '{"sandboxes": []}'
      fi
      exit 0 ;;
  run)
      [ "${STUB_RUN_FAILS:-}" = 1 ] && { echo "create failed" >&2; exit 1; }
      touch "$STUB_DIR/sandbox"; exit 0 ;;
  ports)
      if [ "$3" = "--json" ]; then echo '[]'; fi
      exit 0 ;;
  rm)
      [ "${STUB_RM_FAILS:-}" = 1 ] && { echo "rm failed" >&2; exit 1; }
      rm -f "$STUB_DIR/sandbox"; exit 0 ;;
  exec)
      exec "$STUB_DIR/bin-vm-exec" "$@" ;;
  cp)
      exec "$STUB_DIR/bin-vm-cp" "$@" ;;
esac
echo "stub sbx: unexpected call: $*" >&2
exit 1
"""

# `sbx exec`: the serve path's two calls are no-ops, and anything else is run as
# a VM command by the VM simulation (installed by the backup tests).
_VM_EXEC = r"""#!/bin/bash
args=("$@")
# drop: exec [-u root] [-e VAR] NAME ...
i=1
while [ "${args[$i]:-}" = "-u" ] || [ "${args[$i]:-}" = "-e" ]; do i=$((i + 2)); done
i=$((i + 1))
rest=("${args[@]:$i}")
if [ "${rest[0]:-}" = "pkill" ]; then exit 0; fi
if [ "${rest[0]:-}" = "/opt/computerd/venv/bin/python" ]; then exit 0; fi
if [ -x "$STUB_DIR/vm-run" ]; then exec "$STUB_DIR/vm-run" "${rest[@]}"; fi
echo "stub sbx exec: no VM simulation for: ${rest[*]}" >&2
exit 1
"""

_VM_CP = r"""#!/bin/bash
if [ -x "$STUB_DIR/vm-cp" ]; then exec "$STUB_DIR/vm-cp" "$@"; fi
echo "stub sbx cp: no VM simulation" >&2
exit 1
"""

# `docker compose exec -T db psql ...`: the SQL arrives on stdin, and is kept.
_DOCKER = r"""#!/bin/bash
echo "docker $*" >> "$STUB_DIR/calls.log"
if [ "$1 $2 $3 $4" = "compose exec -T db" ]; then
    cat >> "$STUB_DIR/psql.sql"
    [ "${STUB_DB_FAIL:-}" = 1 ] && exit 1
    exit 0
fi
echo "stub docker: unexpected call: $*" >&2
exit 1
"""

_TIMEOUT = r"""#!/bin/bash
shift
exec "$@"
"""

_FLOCK = r"""#!/bin/bash
exit 0
"""

_SHA256SUM = r"""#!/bin/bash
exec shasum -a 256 "$@"
"""


class Host:
    """One stubbed host. `sandbox=False` starts with no VM."""

    def __init__(self, root: Path, *, sandbox: bool = True) -> None:
        self.root = root
        self.dir = root / "state"
        self.bin = root / "bin"
        self.home = root / "home"
        for d in (self.dir, self.bin, self.home):
            d.mkdir()
        self._install("sbx", _SBX)
        self._install("docker", _DOCKER)
        self._install("timeout", _TIMEOUT)
        self._install("flock", _FLOCK)
        self._install("sha256sum", _SHA256SUM)
        # The host's python3 is whatever runs the tests: lifecycle_event.py is
        # stdlib and the script's inline parsers are portable.
        self._install("python3", f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')
        (self.dir / "bin-vm-exec").write_text(_VM_EXEC)
        (self.dir / "bin-vm-exec").chmod(0o755)
        (self.dir / "bin-vm-cp").write_text(_VM_CP)
        (self.dir / "bin-vm-cp").chmod(0o755)
        if sandbox:
            (self.dir / "sandbox").touch()
        self.env_file = root / "env"
        self.write_token("tok-test")

    def _install(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def write_token(self, token: str) -> None:
        self.env_file.write_text(f"NORA_COMPUTER_TOKEN={token}\n")

    def run(
        self, *args: str, stdin: str | None = None, **env: str,
    ) -> subprocess.CompletedProcess[str]:
        full = {
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.home), "STUB_DIR": str(self.dir),
            "NORA_ENV_FILE": str(self.env_file), "XDG_RUNTIME_DIR": str(self.dir),
            **env,
        }
        return subprocess.run(  # noqa: S603 - a fixed local script, stubbed PATH
            ["/bin/bash", str(SCRIPT), *args], env=full, capture_output=True,
            text=True, check=False, timeout=120, input=stdin if stdin is not None else "",
        )

    # -- what the script did ------------------------------------------------

    def calls(self) -> list[str]:
        path = self.dir / "calls.log"
        return path.read_text().splitlines() if path.exists() else []

    def sbx_calls(self, prefix: str = "") -> list[str]:
        return [c[4:] for c in self.calls() if c.startswith("sbx " + prefix)]

    def allowed(self) -> set[str]:
        path = self.dir / "allowed"
        return set(path.read_text().split()) if path.exists() else set()

    def allow_calls(self) -> list[str]:
        return self.sbx_calls("policy allow network")

    @property
    def spool_path(self) -> Path:
        return self.home / ".local" / "state" / "nora-computer" / "lifecycle-events.jsonl"

    def events(self) -> list[dict]:
        if not self.spool_path.exists():
            return []
        return [json.loads(line) for line in self.spool_path.read_text().splitlines() if line]

    def event_kinds(self) -> list[str]:
        return [e["event"] for e in self.events()]

    def psql_sql(self) -> str:
        path = self.dir / "psql.sql"
        return path.read_text() if path.exists() else ""

    def sandbox_exists(self) -> bool:
        return (self.dir / "sandbox").exists()

    @property
    def state_dir(self) -> Path:
        return self.home / ".local" / "state" / "nora-computer"


def load_lifecycle_event():
    """`deploy/computer/lifecycle_event.py` as a module (`deploy/` is not a
    package, and the script runs it by path)."""
    import importlib.util

    path = ROOT / "deploy" / "computer" / "lifecycle_event.py"
    spec = importlib.util.spec_from_file_location("lifecycle_event", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["lifecycle_event"] = module
    spec.loader.exec_module(module)
    return module
