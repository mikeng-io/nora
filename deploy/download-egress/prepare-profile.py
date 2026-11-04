"""Add unshare to the operator-supplied Docker default seccomp allowlist."""

import json
import sys
from pathlib import Path

profile = json.loads(Path(sys.argv[1]).read_text())
if profile.get("defaultAction") != "SCMP_ACT_ERRNO" or not profile.get("syscalls"):
    raise SystemExit("expected Docker default deny seccomp profile")
profile["syscalls"].append({"names": ["unshare"], "action": "SCMP_ACT_ALLOW", "args": []})
Path(sys.argv[2]).write_text(json.dumps(profile, indent=2) + "\n")
