"""A stubbed host for `deploy/computer/nora-computer` (not a test file).

The script is the only thing that runs `sbx`, and it also shells out to `docker
compose exec db psql`, `flock`, `timeout` and `sha256sum`. Each is a stub on a
private PATH, behaving like the real one on the properties the script reads, and
logging every call to `$STUB_DIR/calls.log`, so a call the script should not
make is visible and an unexpected one exits 1 rather than succeeding silently.

What a stub cannot show is the real binary: that `sbx` takes these arguments and
answers in these words is the README's read-back on the deploy host. Where that
read-back has been made, the stub reproduces it, exit status included: `sbx
policy check network` exits 1 for a denied host, so a stub that exited 0 there
would keep the suite green on a script that read every denial as unknown.
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
denied_store="$STUB_DIR/denied"
touch "$allowed" "$denied_store"
case "$1 $2 $3" in
  "policy check network")
      shift 3
      target=""; for a in "$@"; do target="$a"; done
      # The real `sbx policy check` (measured on the deploy host, 2026-10-01):
      # exit 0 and `Allowed:` for an allowed host, exit 1 and `Denied:` for a
      # denied one, the verdict first on stdout and the same Governance/Context
      # lines after it. The default below is that; the STUB_CHECK_* knobs are
      # the shapes the real tool must NOT be read as a verdict from.
      verdict() {  # $1 Allowed|Denied, $2 target, $3 reason or ""
          echo "$1: $2"; echo "Governance: Local policy only"
          echo "Context: sandbox:nora-computer"
          [ -z "$3" ] || echo "Reason: $3"
      }
      denied() { verdict Denied "$target" "no matching allow rule (default deny)"; }
      [ "${STUB_CHECK_BROKEN:-}" = 1 ] && { echo "policy engine unavailable"; exit 0; }
      [ "${STUB_CHECK_DENIES_ALL:-}" = 1 ] && { denied; exit 1; }
      case "${STUB_CHECK_SHAPE:-}" in
        allowed-exit-1)    verdict Allowed "$target" ""; exit 1 ;;
        denied-exit-0)     denied; exit 0 ;;
        denied-exit-2)     denied; exit 2 ;;
        empty)             exit 0 ;;
        empty-exit-1)      exit 1 ;;
        crash)             echo "panic: policy daemon went away" >&2; exit 137 ;;
      esac
      # Decided like sbx 0.46.0 (measured on norty 2026-10-07): a deny rule
      # whose CIDR holds the target's host wins, but ONLY when that host is an IP
      # literal (a NAME is never CIDR-checked: the accepted limit, design
      # browser-runtime S1 step 4); otherwise `**` or the exact target allowed
      # is Allowed, and anything else is the default deny.
      decision=$(python3 - "$target" "$allowed" "$denied_store" <<'PY'
import ipaddress, sys
target, allowed, denied = sys.argv[1:4]
host = target.rsplit(":", 1)[0].strip("[]")
allows = open(allowed).read().split()
try:
    ip = ipaddress.ip_address(host)
except ValueError:
    ip = None
if ip is not None:
    for cidr in open(denied).read().split():
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                print("deny-rule"); sys.exit(0)
        except ValueError:
            pass
print("allow" if "**" in allows or target in allows else "default-deny")
PY
      )
      case "$decision" in
        deny-rule)
            rid="local:3f6c1d0e-7a52-4b8e-9c14-2d5a8e0b6f71"
            verdict Denied "$target" "denied by local rule rule \"$rid\""
            exit 1 ;;
        allow) verdict Allowed "$target" ""; exit 0 ;;
        *) denied; exit 1 ;;
      esac ;;
  "policy ls --json")
      [ "${STUB_LS_BROKEN:-}" = 1 ] && { echo "not json"; exit 0; }
      touch "$STUB_DIR/foreign_denied"
      python3 - "$allowed" "$denied_store" "$STUB_DIR/foreign_denied" <<'PY'
import json, sys
# The real shape (sbx 0.46.0): scope and applies_to are both "sandbox:<name>".
def rule(decision, r, name="nora-computer"):
    return {"resource_type": "network", "decision": decision, "resources": [r],
            "scope": "sandbox:" + name, "applies_to": "sandbox:" + name}
rules = [rule("allow", r) for r in open(sys.argv[1]).read().split()]
rules += [rule("deny", r) for r in open(sys.argv[2]).read().split()]
# Rules of a sandbox whose name merely starts with ours: never ours.
rules += [rule("deny", r, "nora-computer-eval") for r in open(sys.argv[3]).read().split()]
rules.append({"resource_type": "network", "decision": "deny", "resources": ["**"],
              "scope": "global", "applies_to": "all"})
print(json.dumps({"rules": rules}))
PY
      exit 0 ;;
  "policy allow network")
      shift 3
      [ "${STUB_ALLOW_FAILS:-}" = 1 ] && { echo "policy store is read-only" >&2; exit 1; }
      res=""; for a in "$@"; do res="$a"; done
      echo "$res" >> "$allowed"
      exit 0 ;;
  "policy deny network")
      shift 3
      [ "${STUB_DENY_FAILS:-}" = 1 ] && { echo "policy store is read-only" >&2; exit 1; }
      # An add that reports success and records nothing: the read-back's case.
      [ "${STUB_DENY_SILENT:-}" = 1 ] && exit 0
      res=""; for a in "$@"; do res="$a"; done
      # Like sbx: a comma list is one rule per CIDR.
      echo "$res" | tr ',' '\n' >> "$denied_store"
      exit 0 ;;
  "policy log "*)
      echo "policy-log-output"; exit 0 ;;
esac
case "$1" in
  ls)
      if [ -e "$STUB_DIR/sandbox" ]; then
          st=$(cat "$STUB_DIR/sandbox"); [ -n "$st" ] || st=running
          echo "{\"sandboxes\": [{\"name\": \"nora-computer\", \"status\": \"$st\"}]}"
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

# The VM, as a directory: `/workspace` and `/tmp/nora-*` inside it are
# `$STUB_DIR/vm/workspace` and `$STUB_DIR/vm/tmp/nora-*` on the host. A command
# the script sends is run here with those two paths translated, which is enough
# for the tar, sha256sum, ls, grep and rm the script uses. The things only a real
# VM has (users, ownership) are the stubs below, and they log what was asked.
_VM_RUN = r"""#!/usr/bin/env python3
import os, subprocess, sys
vm = os.path.join(os.environ["STUB_DIR"], "vm")
def tr(a):
    return a.replace("/workspace", vm + "/workspace").replace("/tmp/nora-", vm + "/tmp/nora-")
args = [tr(a) for a in sys.argv[1:]]
if os.environ.get("STUB_VM_TAR_FAILS") == "1" and "tar $ex" in " ".join(args):
    sys.exit(2)
env = dict(os.environ)
if os.environ.get("STUB_VM_TAR_GARBAGE") == "1":
    # A `tar` that "succeeds" and writes something that is not a tarball: the
    # digest taken after it still matches the copy, so only reading the archive
    # back can tell.
    shim = os.path.join(os.environ["STUB_DIR"], "garbage-bin")
    os.makedirs(shim, exist_ok=True)
    with open(os.path.join(shim, "tar"), "w") as fh:
        fh.write(
            "#!/bin/bash\n"
            'for ((i=1;i<=$#;i++)); do\n'
            '  if [ "${!i}" = "-czf" ]; then\n'
            '    j=$((i+1)); echo "not a tarball" > "${!j}"; exit 0\n'
            "  fi\n"
            "done\nexit 0\n"
        )
    os.chmod(os.path.join(shim, "tar"), 0o755)
    env["PATH"] = shim + ":" + env["PATH"]
if os.environ.get("STUB_VM_RM_FAILS") == "1" and args and args[0] == "rm":
    sys.exit(1)
if os.environ.get("STUB_VM_RM_LIES") == "1" and args and args[0] == "rm":
    sys.exit(0)   # says it removed it, and did not
if os.environ.get("STUB_VM_TAR_RECORD") == "1":
    # Records what the real tar was handed: the umask it ran under and the mode
    # of the directory it writes into, then of the archive it wrote.
    shim = os.path.join(os.environ["STUB_DIR"], "record-bin")
    os.makedirs(shim, exist_ok=True)
    with open(os.path.join(shim, "tar"), "w") as fh:
        fh.write(
            "#!/bin/bash\n"
            'log="$STUB_DIR/vm-tar.log"\n'
            'echo "umask $(umask)" >> "$log"\n'
            'for ((i=1;i<=$#;i++)); do\n'
            '  if [ "${!i}" = "-czf" ]; then j=$((i+1)); out="${!j}"; fi\n'
            "done\n"
            'd=$(dirname "$out")\n'
            'echo "dir $(ls -ld "$d" | cut -c1-10) $(echo "$d" | sed "s|.*/vm||")" >> "$log"\n'
            '/usr/bin/tar "$@"; rc=$?\n'
            'echo "archive $(ls -l "$out" | cut -c1-10)" >> "$log"\n'
            'echo "tmp-top $(ls /tmp 2>/dev/null | grep -c nora-backup-)" >> "$log"\n'
            "exit $rc\n"
        )
    os.chmod(os.path.join(shim, "tar"), 0o755)
    env["PATH"] = shim + ":" + env["PATH"]
done = subprocess.run(args, env=env, stdout=subprocess.PIPE)
sys.stdout.write(done.stdout.decode().replace(vm, ""))
sys.exit(done.returncode)
"""

_VM_CP_RUN = r"""#!/usr/bin/env python3
import os, shutil, sys
vm = os.path.join(os.environ["STUB_DIR"], "vm")
src, dst = sys.argv[2], sys.argv[3]
def tr(p):
    if p.startswith("nora-computer:"):
        return p.split(":", 1)[1].replace("/tmp/nora-", vm + "/tmp/nora-")
    return p
src_p, dst_p = tr(src), tr(dst)
if src.startswith("nora-computer:") is False and dst.startswith("nora-computer:") is False:
    sys.exit("neither side is the sandbox")
if os.environ.get("STUB_CP_IN_CORRUPTS") == "1" and dst.startswith("nora-computer:"):
    data = open(src_p, "rb").read()
    open(dst_p, "wb").write(data[:-7] + b"garbage")
    sys.exit(0)
if os.environ.get("STUB_CP_CORRUPTS") == "1" and src.startswith("nora-computer:"):
    data = open(src_p, "rb").read()
    open(dst_p, "wb").write(data[:-7] + b"garbage")
    sys.exit(0)
shutil.copyfile(src_p, dst_p)
"""

# What only a real VM can say about users, kept as stubs on the PATH.
_ID = r"""#!/bin/bash
if [ "$1" = "-u" ] && grep -qxF "$2" "$STUB_DIR/vm-users" 2>/dev/null; then echo 1001; exit 0; fi
exit 1
"""
_USERADD = r"""#!/bin/bash
echo "useradd $*" >> "$STUB_DIR/vm-users.log"
for a in "$@"; do u="$a"; done
echo "$u" >> "$STUB_DIR/vm-users"
exit 0
"""
_CHOWN = r"""#!/bin/bash
echo "chown $*" >> "$STUB_DIR/vm-users.log"
exit 0
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
        for name, body in (("vm-run", _VM_RUN), ("vm-cp", _VM_CP_RUN)):
            (self.dir / name).write_text(body)
            (self.dir / name).chmod(0o755)
        for name, body in (("id", _ID), ("useradd", _USERADD), ("chown", _CHOWN)):
            self._install(name, body)
        self.vm = self.dir / "vm"
        (self.vm / "workspace").mkdir(parents=True)
        (self.vm / "tmp").mkdir()
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
        self, *args: str, stdin: str | None = None, cwd: str | None = None, **env: str,
    ) -> subprocess.CompletedProcess[str]:
        full = {
            "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.home), "STUB_DIR": str(self.dir),
            "NORA_ENV_FILE": str(self.env_file), "XDG_RUNTIME_DIR": str(self.dir),
            # macOS bsdtar adds `._*` AppleDouble members; GNU tar on the host never does.
            "COPYFILE_DISABLE": "1",
            **env,
        }
        return subprocess.run(  # noqa: S603 - a fixed local script, stubbed PATH
            ["/bin/bash", str(SCRIPT), *args], env=full, capture_output=True,
            text=True, check=False, timeout=120, input=stdin if stdin is not None else "",
            cwd=cwd,
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

    def denied(self) -> set[str]:
        path = self.dir / "denied"
        return set(path.read_text().split()) if path.exists() else set()

    def allow_calls(self) -> list[str]:
        return self.sbx_calls("policy allow network")

    def deny_calls(self) -> list[str]:
        return self.sbx_calls("policy deny network")

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

    # -- the simulated VM ----------------------------------------------------

    def make_room(self, scope: str, files: dict[str, str]) -> None:
        """A room's directory in the VM's /workspace, with files by relative path."""
        for rel, text in files.items():
            path = self.vm / "workspace" / scope / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def vm_tmp_files(self) -> list[str]:
        return sorted(p.name for p in (self.vm / "tmp").iterdir())

    def vm_user_calls(self) -> list[str]:
        path = self.dir / "vm-users.log"
        return path.read_text().splitlines() if path.exists() else []

    @property
    def backup_dir(self) -> Path:
        return self.home / "nora-backups" / "computer-workspace"

    def archives(self) -> list[Path]:
        """The nightly rotation: `workspace-*.tar.gz` directly in the backup dir."""
        if not self.backup_dir.exists():
            return []
        return sorted(self.backup_dir.glob("workspace-*.tar.gz"))

    def destroy_archives(self) -> list[Path]:
        """What `destroy` took: outside the rotation, never pruned."""
        folder = self.backup_dir / "destroy"
        return sorted(folder.glob("workspace-*.tar.gz")) if folder.exists() else []

    def vm_tar_log(self) -> list[str]:
        path = self.dir / "vm-tar.log"
        return path.read_text().splitlines() if path.exists() else []


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
