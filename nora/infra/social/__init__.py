"""Social reads behind the `social_fetch` tool (DEC-151) — X and Instagram,
READ-ONLY, over two backends the model never learns the names of.

`SocialRouter` is what the composition root injects; it is itself a
`SocialProvider`, so `nora.agent.registry` and the tool are unchanged and
unaware. Below it, the DEFAULT is OpenCLI and the exception list has exactly
one row (`instagram/story`) — see `router.py` for the rule that keeps it short
(design/instagram-adoption/08-social-adapter-architecture.md).

The OpenCLI half has a loopback HTTP seam between two halves of THIS package:

* `bridge_client.py::OpenCliBridgeClient` — the `SocialProvider` the container
  composes behind `[social] enabled`; POSTs `{platform, what, target, limit}`
  to the bridge and never raises.
* `bridge_server.py::SocialBridge` — the host-side half, run as
  `python -m nora.infra.social` (a systemd user service,
  `deploy/social-bridge/`). It owns the read-only `(site, command)` allowlist
  — the security boundary — and spawns `opencli … -f json` as an argv vector,
  never a shell.

The Instagram half has no host-side half here at all:

* `instagram_client.py::InstagramSocialClient` — POSTs to the instagram-bridge
  container's `/social/story` on the private `nora` network. Its allowlist
  lives in that Go process (`deploy/instagram-bridge/`), which is where the
  read is performed.

**The invariant is the same across both and is what survives a backend that
is not a host subprocess**: every social read is re-authorized against a
frozen allowlist IN THE PROCESS THAT PERFORMS IT, which is never the process
running the model. "Host-side" was how that was achieved when there was one
backend; the property that matters is «a process the model cannot inject», so
a write verb stays unreachable even if the model-facing tool is prompt-injected
by the content it reads.
"""

from .bridge_client import OpenCliBridgeClient
from .instagram_client import InstagramSocialClient
from .router import EXCEPTION_ROUTES, SocialRouter

__all__ = [
    "EXCEPTION_ROUTES",
    "InstagramSocialClient",
    "OpenCliBridgeClient",
    "SocialRouter",
]
