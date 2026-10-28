# 01 — Three layers, one source each

Realizes `00-overview.md`. Each layer is placed by one test: **where does the
fact change, and what reads that change first?** A fact belongs wherever the
thing that changes it lives, rendered from there. Written down anywhere else it
is a copy, and copies go stale in silence.

## Who: doctrine, unchanged

`nora/doctrine/core/identity.md` already carries the shape: an agent, not a person;
built by Miles, answering to the group; *"You are in several [groups], on more
than one platform, and the same you is in all of them."* That sentence is
true for as long as she exists. It is the right thing to put in a cached
prefix, and nothing in this bundle edits it.

`self_card` was not this layer either. It was episodic memory, Honcho's
conclusions about what she had said and done, *"not a description of who she
is"*. It left the tag and fire prompts when the `<self>` block (her Character ·
Nora account, `design/self-growth/20-self-block.md`) landed.

## Where: generated from the room table and adapter health

Two sources, both already in the process:

- **The room table**, read through the live `policy.toml` holder: network, label,
  unit, members, flags. Valid edits apply within three seconds; invalid edits
  keep the last good version. Workspace bindings require recreation.
- **Adapter health**, `AdapterHealth` in `nora/domain/ports.py`: every adapter
  implements `health()`, and the type already distinguishes three states
  (connected, disconnected and expected to heal, degraded and waiting for a
  human). It changes at any moment.

Neither belongs in doctrine, and the second cannot even belong in the cached
prefix: it varies between turns. `_where_line`'s docstring already works out
the placement for the network sentence, and every argument transfers:

1. **Not a tool**: relevant to every turn, so a tool means a round trip every
   time or a model deciding it does not need one.
2. **Not in the cached prefix**: it varies, so it would end the prefix's hit
   rate for everything after it.
3. **No identifiers**: R-18 keeps room ids off the wire. She is told names,
   never ids.

So `<presence>` is `_where_line` grown from one fact to the few she needs on
every turn. The detail she needs only sometimes goes to tools (`03`).

## What: the tool catalogue

The tool catalogue (`design/tool-catalogue/00-overview.md`) generates one line
per tool from the registry and keeps it resident, 991 tokens for all 54 tools.
That is a capability list that cannot go stale, because it is rebuilt from the
thing it describes. It is this layer. Adding a second capability surface here
would reopen DEC-150, which removed the last one because the schemas already
carried it.

## Framing: situation, not ability

One measurement governs how `<presence>` is worded. On the Relevance Gate,
telling the model what it was *reading* cut errors, and telling it what it
*could do* made it measurably worse (both measured; see the operator's CMS
notes, "Context framing beats capability listing"). So `<presence>` states a
situation: *where you are and what is around you.* It never lists abilities.
Abilities are the catalogue's job, and the catalogue words them as "when to
reach for this", which is selection, not a boast.
