---
class: design
subject: the threat model, who attacks Nora, what they want and which risks the operator accepts
owns:
  - threat model: "the attackers, the critical assets and the accepted risks, as the operator set them"
  - attacker profiles: "A1 an unauthenticated internet user, A2 a non-roster person in a room or DM, A3 a signed-in roster member, A4 malicious web or tool content, A5 a compromised co-networked container"
  - accepted risk: "a risk the operator has ruled out of scope, so a finding that only restates it is not a defect"
area: security
links:
  - {to: docs/design/cognition/boundaries.md, rel: uses}
  - {to: docs/operations/exposure.md, rel: uses}
  - {to: docs/engineering/security-triage.md, rel: uses}
---

# Security model

Nora is a self-hosted, single-operator social agent. People reach her through
chat platforms and a portal; she reads untrusted text and web content, calls
tools with side effects, and holds what people have said and where they have
been. This file is the threat model: who the attackers are, what they want, and
what the operator has chosen not to defend. The operator sets attackers,
critical assets and accepted risk; the controls belong to the bundles that own
the parts they protect. Scans and finding triage start from here, and
vulnerability reporting is in the repository's security policy.

## Trust boundaries

| part | boundary |
|---|---|
| **portal** | the internet reaches it, authenticated against the roster; it then speaks to an internal API that trusts it to say who is asking ([web](../web/README.md)) |
| **messaging ingress** | any member of a room Nora is in, and any DM sender, reaches her: roster or not. Admission decides whether a turn runs, and recording is wider than answering. Bridges prove themselves to her with a shared secret ([platform](../platform/README.md), [people](../people/README.md)) |
| **agent runtime** | untrusted message and web text reaches a model that makes tool calls with side effects; what may leave is bounded by [boundaries](../cognition/boundaries.md) |
| **computer sandbox** | Nora reaches a sandbox host with a token; the sandbox's own egress to private ranges is denied and credentials reach it only through a fill broker ([computer](../computer/README.md)) |
| **data stores** | the record, the graph, held media and the embedder are reachable only from the host and the private network |
| **presence** | a live position source feeds location history ([location](../location/README.md)) |
| **deployment** | the host, its networks, the release pipeline and the secrets they hold |

Which ports and networks make each boundary real is in
[exposure](../../operations/exposure.md).

## Assets

The operator names three as critical:

1. the roster, which decides who is who, and portal access;
2. chat records and memory, the graph and the media it points at;
3. platform sessions and keys, which let an attacker read and send as Nora.

Also protected, and not named critical: live location and its history; the
secrets (model, bridge, computer, location and search credentials, and what the
sandbox's vault holds); and the host and sandbox themselves.

## Attackers

The operator rules all five in scope, none out.

- **A1, an unauthenticated internet user** against the portal: sign-in bypass,
  code guessing and enumeration, request forgery or path traversal through the
  portal's proxy, and trust in forwarded host headers.
- **A2, a non-roster person in a room or DM:** prompt injection to make Nora
  disclose another person's data or location, run tools, or get past admission
  and grants.
- **A3, a signed-in roster member:** reading another person's, room's or unit's
  data through the portal's person handling, or through Nora across units.
- **A4, malicious web or tool content:** indirect prompt injection from search
  results, fetched or browsed pages, files and tool servers; reaching private
  ranges from the sandbox or a server-side fetch.
- **A5, a compromised co-networked container or bridge:** reaching the internal
  API, which has no caller authentication, the chat transport's unauthenticated
  control interface, and anything else that trusts the private network.

## Controls, by owner

| control | owner |
|---|---|
| unit as the read boundary, origin scope, exposure, location consent, credential and doctrine leak protection that always runs | [boundaries](../cognition/boundaries.md) |
| the three-state tool result | [agent](../agent/tools.md) |
| deny-by-default policy, which refuses to start when it allows nothing: who may talk to her, and where | [people](../people/README.md) |
| sign-in, the roster re-check on every request, one-time codes | [web](../web/README.md) |
| sandbox egress rules and credentials by broker only | [computer](../computer/README.md) |

## Accepted risk

- **Disabled content checks.** A finding that only reports a content check that is
  off by default ([boundaries](../cognition/boundaries.md)) is accepted. The
  protections that always run are not accepted away.
- **Host, operator and container-daemon compromise**, and the operator's own
  configuration files.
- **Development-only paths**, unless they are reachable in the production image.
- **The model provider misbehaving**: a provider returning malicious or wrong
  output. Prompt injection from room members or web content stays in scope
  (A2, A4).
- **The project is experimental:** fixes are best-effort on the default branch.
