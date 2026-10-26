# 23 — Named places: the boundary map, and routing to a place this household has a name for

> Content-policy update, 2026-10-25: phone/UUID/coordinate, place/person-name
> and location audience filters described here remain implemented and tested in
> enabled mode, but runtime defaults them off through `[content_checks]`.
> Credentials, doctrine, scope, consent and availability are independent.
> See the [current decision](../../docs/adr/0030-content-filters-are-disabled-by-default-and-reversible.md).


Date: 2026-08-07. Status: PROPOSED (operator directed the build 2026-08-07;
§1's boundary clarification was his explicit precondition). Probe evidence in
§3 is live, not assumed.

## 1. The boundary map — which navigation-shaped thing answers which question

Five things now touch "getting around", and each has exactly one job. The
boundaries are drawn by TWO axes: what QUESTION it answers, and in which
DIRECTION data crosses the host boundary.

| layer | answers | time axis | what leaves the host |
|---|---|---|---|
| `transit` (tool) | "when is the next bus on route N" | live-only | route number + optional stop name → rt.data.gov.hk |
| `route` (tool) | "how do I get from A to B / how long" | time-shiftable | two PLACE STRINGS the person named → Google (paid) |
| `whereis` (tool) | "where is somebody in this room now" | live-only | **nothing** — reads the local `presence_fixes` table only |
| navigation (skill) | judgement: which of the above a journey question needs, and when | — | nothing (it is prompt text) |
| places (this design) | "the position of a name this household uses" («Home») | static | **nothing at resolution**; §5 governs what may leave afterwards |

The rules that keep the boundaries honest, each already enforced somewhere:

- **Outbound arguments are the egress surface** (design/14 §2.2). `route`'s
  args are screened (R-28): identifiers, person names, raw coordinates all
  refuse. `transit`'s args are a route number and a stop.
- **A seen position never enters an outbound argument** — barrier
  (`_looks_like_coordinate`) + skill judgement ("a seen position … never enters
  one"). `whereis` output is for the ROOM, not for other tools.
- **Room-scoped disclosure is subset-of-circle** (R-21/R-31): `whereis`
  refuses into a room containing anyone outside the circle, or anyone
  unidentified.
- **The skill holds judgement only** — never endpoints, never mechanics.

What is missing, and what this design adds: nothing today can answer «點樣返
屋企» — "Home" is not a public place Google can look up, and it must not be.
It is a NAME this household attached to a position, and Life360 already holds
it (§3).

## 2. The question this answers, and the one it refuses

Answers: *"route me to/from a place this household has a name for"* — where
the person ASKED for that trip. The place's position leaves to Google only as
the direct execution of that request (§5).

Refuses, unchanged: *"route me to where <person> is"*. A person's live
position stays in the room (R-28 barrier + skill). A saved place is a STATIC
household landmark; a seen position is surveillance-shaped. The line between
them is the whole point of this document.

## 3. The source, probed live 2026-08-07

`GET /v3/circles/{circle_id}/places` on the working recipe (urllib transport,
cloudfront host, KOKO UA — the life360-working-recipe values) returned HTTP
200 with:

```
places: [ { id, ownerId, circleId, name: "Acme Tower", latitude, longitude,
            radius, type: null, typeLabel: null },
          { …, name: "Home", … } ]
```

Same auth, same client, same fragility budget as the member reads (DEC-55's
bounded 403 path). Places change rarely — on the collector's cadence they are
fetched at startup and then at most hourly, upserted into a `presence_places`
table (`circle_id, place_id, name, lat, lng, radius, updated_ms`; delete rows
gone from the wire). The COLLECTOR owns the fetch; Nora reads the table — the
same split as fixes, for the same reason (§4 of design/19: Nora never touches
Life360's fragility).

## 4. Resolution is hybrid, ordered, and local

When `route()` receives an origin/destination string (already past the R-28
screen), it resolves in this order, ON HOST:

1. **Life360 saved places** for the conversation's circle — casefold exact
   match on `name` from `presence_places`.
2. *(seam, not built now)* other operator/derived sources — a Honcho peer-card
   fact, an operator-authored alias list (e.g. 屋企 → Home). The resolver is
   an ordered list of sources so these are additive.
3. **Google Places text lookup** — today's behaviour, for public place names.

**The inverse direction, added 2026-09-17:** this ordering resolves a NAME to
a position, for routing. `design/27 §3.7` (DEC-190) applies the same ordering
the other way — a POSITION to a place, inside the analytics substrate — so a
derived cluster takes its identity and name from the tagged place it matches.
One ordering, both directions; neither document restates the other's half.

A hit at 1 (or 2) substitutes the stored coordinate into the Google Routes
call SERVER-SIDE; the model never sees or handles the coordinate, so the R-28
model-facing barrier is untouched — it still refuses any coordinate the MODEL
writes. A miss falls through to 3 exactly as today.

**Circle-id caution (operator's explicit instruction):** circle ids are
derived from identity.toml's `[[persons.<id>.life360]]` blocks and exist in
exactly three places — that file, the two presence tables, and metrics labels
(DEC-55's established practice). They never appear in config.toml (DEC-8), in
replies, in tool results, or in log MESSAGES. Resolution is keyed by the
circle of THIS conversation's room — with several circles, a name resolves
only within the circle whose roster the room is a subset of; never across.

## 4.1 Orchestration — who computes what, and what the model never touches

The operator asked for this laid out before any tool-side build (2026-08-07).
The rule that decides every row: **coordinates and geometry stay server-side;
the model handles names.**

| operation | where it runs | what the model sees |
|---|---|---|
| "is this fix inside a saved place's radius" | pure function at `whereis` answer assembly (haversine ≤ `radius_m`) | `"place": "Home"` in the result — never the geometry |
| "resolve «Home» to a position for routing" | server-side resolver inside the `route` tool, before Google | nothing — it passes the NAME through, exactly as typed |
| "route between two resolved endpoints" | `RouteClient` (Google), coordinate substituted internally | the same legs/duration answer as today |
| user-facing arithmetic (bills, splits) | `calculate`, unchanged | what it always saw |

Three consequences, each an explicit decision:

- **`calculate` is NOT extended and no geometry tool is added.** Containment
  is not a model operation: the model never holds two coordinates to compare,
  so there is nothing for it to calculate. A geometry-capable model tool would
  CREATE the coordinate-handling surface everything above is built to avoid.
- **`whereis` gains a `place` field, not a new tool.** A fix inside a saved
  place's geofence answers «喺屋企» — the household's own name for the spot,
  which is the answer a person actually wants. Address stays as the fallback;
  the radius check runs where the R-31 guard already ran.
- **A non-subset room does not REFUSE a saved-place route — it falls
  through.** If the room is not a verified subset of the circle, the resolver
  simply does not fire and the name goes to Google's public lookup as an
  ordinary string («Home» finds whatever the public map calls that). This is
  R-21's own shape («not being here is indistinguishable from not existing»):
  a hard refusal would CONFIRM to an outsider that this household has a place
  by that name; silent fall-through makes a guarded name indistinguishable
  from an unsaved one. Note the asymmetry with `whereis`, which refuses
  loudly — there the subject is a person's position and silence would be a
  lie; here the subject is a dictionary entry whose existence is itself the
  secret.

## 5. The disclosure decision this needs (DEC-60, to be taken at build)

Routing to a resolved place sends that place's POSITION to Google. That is a
new disclosure class, and it is sanctioned by exactly one thing: **the person
asked for that trip.** «點樣返屋企» names the destination; executing the
request requires telling the router where that is. This is different in kind
from `whereis`→`route` composition (refused, §2): there the position is a
person's, live, and nobody asked for it to travel.

Guards that come with it:

- **Subset-guarded resolution — FALL-THROUGH, not refusal** (superseded 2026-08-07,
  council finding: this bullet first said "same refusal `whereis` gives", which
  §4.1 and R-35 and the built code all contradict, and which would itself leak).
  A saved place NAME resolves only when the conversation is a verified subset of
  the place's circle — same `DisclosureGuard` — but a NON-subset room does **not
  refuse**: the resolver silently declines and the name goes to Google's public
  lookup as an ordinary string. A hard refusal here would CONFIRM to an outsider
  that the household has a place by that name; silent fall-through makes a
  guarded name indistinguishable from an unsaved one (R-21's «not being here ≡
  not existing»). This is the deliberate INVERSE of `whereis`, which refuses
  loudly — there the subject is a person's live position and silence would be a
  lie; here the dictionary entry's existence is itself the secret. See §4.1.
- **Names in replies, never ids or raw coordinates.** The reply says «由屋企
  出發…»; the coordinate stays in the tool internals.
- **The barrier stays.** A model-written coordinate still refuses (R-28); only
  the server-side resolver may place one in an outbound call, and only for a
  place the person named.

## 6. What must be true before it ships

1. `presence_places` joins the Alembic chain; parity gate covers it.
2. The collector's places fetch shares the client's bounded-auth path — a 403
   backs off with the same cadence multiplier, never its own loop.
3. Resolution is measured live: «點樣返屋企» (or the operator's phrasing)
   resolves Home for a subset room, refuses for a non-subset room, and an
   unsaved name still routes via Google Places as today.
4. A named test per rule: resolution subset-refusal (the R-31 family),
   barrier-untouched (R-28 family), and the collector's places upsert/delete.
5. DEC-60 recorded with this document as its argument.
