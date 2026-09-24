"""Social reads behind the `x_read`, `x_search`, `linkedin_read` and
`linkedin_search` (DEC-201), `instagram_read` and `instagram_search`
(design/instagram-adoption/18) tools — X, LinkedIn and Instagram, READ-ONLY,
over two backends the model never learns the names of.

`SocialRouter` is what the composition root injects; it is itself a
`SocialProvider`, so `nora.agent.registry` is unchanged and unaware. Below it,
X and LinkedIn go to OpenCLI and Instagram to the instagram-bridge (`15`):
the bridge's rows are `instagram/story`, `instagram/profile`,
`instagram/search` (P11) and `instagram/post` (P12). OpenCLI serves X and
LinkedIn, and no Instagram read. See `router.py`.

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

* `instagram_client.py::InstagramSocialClient` — `GET /accounts/{igid}`,
  `GET /accounts/{igid}/story`, `GET /accounts?query=` and
  `GET /posts/{shortcode}` on the instagram-bridge container, and the
  username lookup `GET /accounts?username=` the router resolves a handle or
  a link with (P10), over the
  private `nora` network. The bodies are pinned by shared fixtures the Go
  handlers generate (`tests/fixtures/instagram_bridge/`).

* `subjects.py::PersonsSubjectIndex` — the production `SocialSubjectIndex`:
  `person_id -> igid` off `identity.toml` (kind-aware), Tier 0 off the rooms'
  `persons` lists, and the reverse `igid -> person_id` a link needs.

`SocialRouter` also owns subject RESOLUTION (`SUBJECT_INPUTS`, keyed on
platform, read and input kind) and the REACH check (`TIER_ZERO_READS`, keyed
on the read), which is the other thing every social read passes through and
the reason the router is more than a dispatch table: both used to sit in
`instagram_client.py`, where they could only govern that file's one row while
the OpenCLI rows ran with no subject authorization at all
(design/instagram-adoption/07 §tiers, /10 §"Who owns what", /18).

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
from .router import (
    EXCEPTION_ROUTES,
    SUBJECT_INPUTS,
    TARGET_HANDLE,
    TARGET_NAMED,
    TARGET_PERSON,
    TIER_ZERO_READS,
    SocialRouter,
    TargetKind,
)
from .subjects import PersonsSubjectIndex

__all__ = [
    "EXCEPTION_ROUTES",
    "SUBJECT_INPUTS",
    "TARGET_HANDLE",
    "TARGET_NAMED",
    "TARGET_PERSON",
    "TIER_ZERO_READS",
    "InstagramSocialClient",
    "OpenCliBridgeClient",
    "PersonsSubjectIndex",
    "SocialRouter",
    "TargetKind",
]
