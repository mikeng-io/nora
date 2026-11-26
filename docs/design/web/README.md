---
class: design
subject: the portal, the signed-in place where people see what Nora holds about them
owns:
  - portal: "the signed-in web place where a roster member reads presentations of what Nora holds for them, and acts only for themselves"
  - the portal asks for no authority of its own: "every read is made on behalf of the signed-in person, whose identity the portal alone establishes"
area: web
links:
  - {to: docs/design/cognition/graph.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
  - {to: docs/design/appearance/README.md, rel: uses}
---

# Portal

The portal is the circle's side of Nora: where everyone is, what is planned, and
Nora herself: whether she is listening, what she holds about you, how she
behaves and what she can do. It is an installable web app for phones and
desktops, behind a sign-in. It adds no memory and no loop: it is a reader of the
[graph's presentations](../cognition/graph.md), assembled for one person at a
time. Its look is [appearance](../appearance/README.md), desk register.

## Parts

| part | responsibility |
|---|---|
| **the portal app** | pages, sign-in, the session, and the one place a request's identity is checked |
| **the app API** | Nora's side: the read routes the portal may call, on the private network |
| **the world** | a shared 3D place drawn from the same presentations ([world](world.md)) |
| **the venue** | a building in the world for singing together ([venue](venue.md)) |

## Sign-in and identity

Only people on the roster get in. Two ways in lead to the same identity: a
Discord account, or a one-time code Nora sends to the person's own chat account.
An account that is not on the roster is refused; accounts resolve through
[identity](../people/identity.md).

The session is checked against the roster on every request, not once at sign-in:
removing a person from the roster ends their access at once. One-time codes are
bound to their purpose, and a request for one answers the same whether or not the
account exists, so the roster cannot be probed.

## How a read reaches Nora

The browser only ever calls the portal. The portal checks the session, then
forwards to Nora's app API only what an explicit allowlist names: reads, with
named parameters and shaped identifiers. The person a read is for comes from the
session; any identity the browser supplies is ignored. The app API trusts the
portal for who is asking, so it is reachable only from the portal's private
network, and a container that can reach it is an attacker the
[threat model](../security/README.md) names (A5).

A few pages are the portal's own and never reach Nora, such as a shelf for
media the circle downloads together. They hold no cognition state.

## What it shows

| page | presentation |
|---|---|
| **Today** | Nora's state, who is around, what is coming, a line about you from your profile, the circle |
| **Map and people** | live presence and a person's last day, places and usual week: the [location](../location/README.md) presentations the viewer may see |
| **Plans** | the agenda by week and month: a plan's venue, who is coming ([agenda](../agenda/README.md)) |
| **Documents** | what Nora has written for the rooms you are in, drawn from its stored spec ([documents](../documents/README.md)) |
| **Nora** | her status, her profile as seen by you, her doctrine as a reader, her tools and skills |

## Invariants

- The unit is the read boundary: a page shows a person only what a pass in
  their unit could read, and a person in two units sees two independent
  states ([boundaries](../cognition/boundaries.md)).
- A person's profile is read by that person only; what Nora has learned about
  you is never another member's page.
- The portal writes nothing of its own judgement. What a person changes through
  it is that person's own act, recorded as theirs.
- A read that cannot be made is unavailable on the page, never an empty page that
  looks like nothing is held.
