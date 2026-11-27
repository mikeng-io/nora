---
class: engineering
subject: how the code is grouped by area and layer, and where each package lives
owns: []
links:
  - {to: docs/design/cognition/README.md, rel: uses}
---

# Code map

Code is grouped by **area**, one package per subject designed under `docs/design/`
([ADR 0037](../adr/0037-code-is-grouped-by-area-and-depends-inward.md)), in three layers
importing inward ([rules](../../nora/AGENTS.md), enforced in `pyproject.toml`). The design
bundles say what each area does and why; this file says where the code is.

| layer | holds | may import |
|---|---|---|
| `domain/` | models, rules, the ports the area needs | stdlib, `nora.domain` |
| `app/` | the area's use cases | its domain |
| `adapters/` | stores, clients, tools and context blocks | its app and domain, frameworks, `nora.infra` |

`wire.py` builds an area's adapters and the composition root assembles areas; an area reaches
another only through its root. New code goes to its area.

## Areas

| area | subject |
|---|---|
| `conversation/` | rooms, the record, messages, threads, edits, reactions, polls, replies |
| `memory/` | what Nora remembers and recalls, held as the Cognitive Graph |
| `people/` | identity, policy, units, who is online |
| `location/` | presence, places, movement, routes, transit, parking, weather |
| `media/` | images and video: custody, comprehension, generation |
| `social/` | web search and fetch, the social networks |
| `computer/` | the computer unit, its sessions, the browser, artifacts |
| `documents/` | structured documents the portal renders |
| `agenda/` | gatherings, reminders, wakes |
| `voice/` | voice channels, speech, music |
| `subagents/` | delegated agent work |

## Shared runtime

`platform/`, `ingest/`, `engagement/`, `agent/` (Strands, tool registry, context assembly),
`app/` (dispatch, lifecycle, composition root), `language/`, `doctrine/`, `instructions/`,
`skills/`, `dreamer/`, `subagent/`, `web/` (portal API), `obs/`, `infra/`, `presence/`,
`analytics/`, `subject/`, `testing/`, and `domain/`, the shared kernel. The top-level modules
are `config.py`, `config_identity.py`, `config_reload.py`, `secret.py`, `access.py` (the
identity and access facade), `memory_routing.py`, `tag.py`, `persons.py`, `persons_file.py`
and `policy.py`. `persons.py`, `persons_file.py` and `policy.py` (`people`) are area code still
to move.

Outside `nora/`: the portal in `app/`, and the services in `deploy/`.

`tests/test_architecture_doc.py` keeps this map connected to the tree: every package and
top-level module under `nora/` appears here.
