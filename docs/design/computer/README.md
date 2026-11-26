---
class: design
subject: the computer, Nora's untrusted place for commands, files and a browser, owned by a scope
owns:
  - "computer: the microVM where model-influenced commands, files and browser actions run, outside Nora's trust"
  - "computer scope: the unit or private-DM binding that owns one computer's workspace, sessions and browser"
area: computer
links:
  - {to: docs/design/cognition/boundaries.md, rel: uses}
  - {to: docs/design/cognition/acts.md, rel: uses}
  - {to: docs/design/agent/tools.md, rel: uses}
---

# Computer

Nora's control plane stays outside the computer. What a model's commands, a link, an attachment, a
web page or a generated script does runs inside a microVM that holds no Nora credential and has no
route to her graph, transport bridges, gateway or control sockets. Nora calls the computer; the
computer calls nothing of hers. The party that is untrusted is the content, never the person asking.

## Parts

| part | is | defined in |
|---|---|---|
| **runtime** | commands, files, sessions, audit and custody, through one port | [sandbox](sandbox.md) |
| **browser** | an anonymous headed browser in the scope's computer | [browser](browser.md) |
| **linked files** | a shared provider file opened by handle, read-only | [external files](external-files.md) |

Documents are [documents](../documents/README.md)'s, and a long multi-step job is
[subagents](../subagents/README.md)'s; neither becomes a method of the shell runtime.

## Scope

- **A group's computer belongs to its unit.** The unit is the read boundary
  ([boundaries](../cognition/boundaries.md)), so work started in one platform's room resumes from
  another room of the same unit, and two units never share a workspace even when their members
  overlap. A room that needs privacy from its unit belongs in its own unit; no per-room file filter
  exists.
- **A private DM has its own scope,** bound by [people](../people/README.md), and is never reachable
  from a group's computer.
- **Scope is resolved once, by code, from the admitted turn or job,** and carried through every
  call, handle, file path, tab and process. The model never names a scope, an owner or an endpoint.
  An unknown, conflicting or revoked scope is unavailable, with no shared fallback.
- **Origin is provenance, never a grant.** Audit and sessions keep the room, actor and tag that
  started work; reading a unit's sessions or files from another room of the unit depends on the
  unit, not on the room that began it.
- **Reassigning a room to another unit moves no workspace.** That is an explicit operator data move.

## Invariants

- The computer is the security boundary against Nora's side; a directory is not. A kernel or root
  escape is outside the claim, which is why the VM, not the path check, is the boundary.
- A computer call is an effect: it passes the [act envelope](../cognition/acts.md), and an uncertain
  outcome is read back by identity, never replayed.
- Default egress is denied except an admitted public path through one shared HTTP gateway that checks
  the destination when it connects; private ranges and Nora's services are refused, and a hostname
  that resolves to a private address does not get through.
- No credential for a third party sits in the computer; the host substitutes one for an intended
  destination only.
- Exports are checked on Nora's side by size, content type and digest, never by the computer's
  claim. A missing file is unavailable, never an empty artifact.
- A command that exits non-zero is a completed result with its exit code; a timeout, an unreachable
  daemon or a refused operation is unavailable.
- Every effect is audited by Nora, because the computer is the less-trusted writer.

## Why

- **Unit-owned work** because a job must continue across a unit's transports, and the unit is
  already where Nora's understanding is shared.
- **A VM boundary, not a path check,** because content from the web or a member is hostile by
  default and Nora's stores must be out of its reach.
- **A host-controlled lifecycle** so no model path can create, destroy or reshape a computer.
