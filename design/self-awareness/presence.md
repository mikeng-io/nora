# Generated presence

## One situation builder

The presence reader resolves the arriving room through live policy, finds the
other rooms of its unit and reads the relevant adapters' health at turn start.
The same resolution feeds the declared presence block and the room directory,
so there is one answer to which rooms exist.

Presence renders through declared context assembly on engagement and fire
occasions that need the situation. It replaces any hand-concatenated network
line or occasion-specific home-room block. A minimal offline context can state
the platform alone without inventing the rest.

The block identifies the current room by label and network, the unit's other
rooms and their current transport states, and the fact that other groups
exist. It never emits room identifiers, another unit's label, another room's
member names or capability instructions. An unresolved room directory is not
the same as a unit with no other rooms.

## Transport and delivery

Adapter health distinguishes up, reconnecting, needs-a-person, not-running and
unknown. A degraded adapter needs a person even if a stale connection flag
says connected; a health exception is unknown, never up. Health is sampled for
each new read rather than cached into the prompt.

A relay reads the same live mapping after its policy gates and before fire.
Anything other than up returns unavailable and sends nothing. A reconnecting
note can say the transport may recover; a degraded or unknown note makes no
such promise. Tool selection is therefore a routing cost, not evidence that a
send occurred.

Presence does not volunteer itself as a topic. It exists so room and health
questions have grounded answers and a relay cannot be described as successful
against the visible situation.

## Handles

Her public handles come from live adapter identity where the integration can
supply them, with declared values only as fallback. A failed live read retains
a declared value; a network with neither is omitted. A rename therefore
changes the next generated presence rather than leaving a copied fact behind.

Handles are for people to reach Nora and answer questions about her accounts.
They are never parsed into identity identifiers, used to match a sender or
added to self aliases. Structural mention detection continues to use the
adapter's immutable account identifiers.

The handle line belongs inside presence rather than a global cached block.
Public display names are safe to speak, while private room and account IDs
remain outside the model surface.
