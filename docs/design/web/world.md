---
class: design
subject: the world, a shared 3D place that is a presentation of what Nora holds
owns:
  - world: "a shared 3D place visited in the portal whose fixed base is built by hand and whose contents are a presentation of the unit's graph"
  - world placement: "Nora's act of making a claim or intention real as an object in her unit's world, with a short spec of how it looks"
  - avatar reading: "the items Nora attaches to a person's avatar from what she holds about them in this unit, which that person may hide"
area: web
links:
  - {to: docs/design/web/README.md, rel: uses}
  - {to: docs/design/cognition/graph.md, rel: uses}
  - {to: docs/design/cognition/storage.md, rel: uses}
  - {to: docs/design/cognition/acts.md, rel: uses}
  - {to: docs/design/cognition/boundaries.md, rel: uses}
---

# The world

The world is a shared 3D place people visit in a browser, signed in through the
[portal](README.md). It has a fixed base built by hand. What fills the base, and
how it changes, comes from Nora's cognition: what she holds open, what she knows
about each circle, what is planned, where people go, what she makes of each
person. It is her understanding made visible.

## The world is a presentation

A presentation is a read over rows, assembled for an occasion, and nothing
writes it ([graph](../cognition/graph.md)). The world is one more portal
presentation: assembled for a reader at read time through one path, under
withdrawal, and never stored as data.

- **One world per unit.** The unit is the read boundary, and a boundary is
  drawn by scope, never by a filter inside it. Each unit's rooms are districts
  of its world, and every member sees all of it, so two people in one place can
  point at the same object. A person in two units visits two worlds, and nothing
  of one appears in the other, avatars included. Rooms whose talk must not meet
  already belong to different units, so the world needs no privacy model of its
  own.
- **It draws from claims, intentions and aggregates, never from items.** A
  message, a photo or a location fix of a person is never an object in the
  world, and neither is a reading of one.
- **Withdrawal applies.** A forgotten row is hidden at once from rendering
  ([storage](../cognition/storage.md)), and a hidden or unsupported row takes its
  object with it at the next assembly, with no world-specific code.
- **It can be assembled as of a past time**, because rows are append-only and
  the projection is a pure function of them.

| source | in the world |
|---|---|
| a room of the unit | a district, with its own building and ground |
| the room's Standing | the district's mood: light, weather, how busy it is |
| the room's culture | landmarks only that group would recognise |
| an open matter | an object Nora placed: a framed picture, a ticket stub, a souvenir |
| a settled matter | a pinned card on the district's board |
| a planned gathering | something that appears before the date and leaves after it: a banner, a table set for dinner |
| a person's profile | Nora's reading of that person, worn by their avatar |
| Nora's own profile | her home: what is in it, how it changes |
| places and routines | a map of the real places the unit goes, with where people usually are at this hour |
| relationships | who stands near whom when avatars idle |
| music played in the unit's rooms | the venue's setlist and posters |

## Who decides what

| part | decided by |
|---|---|
| the base: terrain, Nora's home, the districts, the venue | the operator, by hand |
| what each district holds | the projection |
| what Nora chooses to make real, and how it looks | Nora, through a world placement |
| turning a spec into a 3D item | the asset pipeline: code and models, no agent |
| a person's body and base look | that person |
| Nora's reading of a person, worn on their avatar | Nora; the person may hide it |

**The projection** is a pure function of the unit's supported rows: a row's kind
picks a stock object and its identity picks a free cell in its district by a
fixed rule. It stores nothing, so an object can shift when a neighbour arrives
or leaves. It mirrors the graph; it does not choose.

**A world placement** is Nora's act ([acts](../cognition/acts.md)): place,
change or retire an object for a row, with a short spec. Code validates every
call: the row exists, is supported and belongs to this unit, the district is one
of the unit's rooms, and the spec is within length. Doctrine says when to use it.
Her act is an item like any other, so she sees her own earlier placements and
may change or retire them, and a row may cite them.

**Rows and precedence.** A placement and the asset made for it are her acts. A
person's avatar choice and each item they hide are that person's own acts,
recorded as theirs. The scene a viewer receives is the presentation and is never
stored. For one row, Nora's latest placement wins over the projection, and a
person's hide wins over both on their own avatar. Deleting any of these rows is
a forget with its cascade, never a reset.

## The look

An isometric, toon-shaded diorama, rendered in the portal. It runs in a phone
browser and needs no first-person controls; stylised avatars avoid the uncanny
valley; objects sit on a grid, so one slightly off in scale or style still reads
as belonging. One fixed palette and one toon material cover everything: the
pipeline applies both, so library and generated objects match
([appearance](../appearance/README.md)).

Nora's home is at the centre, one district per room of the unit around it, and
the [venue](venue.md) is its own building. The world follows Hong Kong time: day
and night, and gatherings that appear before their date and leave after it, so
returning tomorrow shows something changed. People visit Nora's world and do
not get their own, which keeps her cognition the single source and gives one
place to look for what changed and why.

## Avatars

| part | what | who sets it |
|---|---|---|
| **the person's look** | body, face, hair, base outfit | the person, in an avatar editor or from a preset; Nora never touches it |
| **Nora's reading** | items attached to one point: something held, a hat, a badge, a title or the name she uses, a small memo bubble | Nora, from the person's profile in this unit |

Hiking boots in hand for someone she knows climbs every weekend; a lucky charm
before an exam she knows about, gone the day after. If Nora decided the whole
character, people would feel labelled; if she had no part, it would be any
avatar app. The split keeps the body theirs and the reading hers.

- **Veto.** A person can hide any item of Nora's reading, or all of it. A hide
  is read only to draw the scene; no cognition read returns it, so it never
  becomes a signal about the person.
- **Attached items only.** Clothing that deforms with the body needs rigging,
  and automatic generation does that badly.
- **Per world.** Nora's reading in each unit comes only from that unit's
  graph, so she sees you differently in each group and the worlds show it. The
  person's own look is the same in both.

## Making an object real

Nora decides; the pipeline makes. She never models anything: she writes a spec,
and everything after is code and models with no agent in the loop, run as a
deferred job through the job runner the way a video is
([generation](../media/generation.md)). The placement shows the closest library
object until the job finishes, then swaps.

Resolution runs cheapest first:

1. **Library.** Search the spec over the asset library: nearly free, and most
   specs once the library has grown.
2. **A picture on a stock object.** A frame, poster, ticket, sign, card or mug
   carries a generated image as a texture. Most memory objects are this, and a
   texture swap never produces a broken mesh.
3. **Generate.** Text to image to a textured 3D mesh, on a GPU job.

Clean-up is code: reduce to a face budget, apply the world's material and
palette, normalise scale to the grid, set the pivot and render a thumbnail.

**The check is fail-closed.** A vision model looks at the thumbnail with the
spec and answers pass or fail. A fail, an error from any stage or a timeout all
mean the object is not placed and the closest library object stands in. A bad
generation never reaches the world, and an error is never read as a pass. An
accepted object joins the library with its spec, so the next similar spec ends
at step one.

The pipeline's inputs are an image Nora generated for the spec, never an item of
a person: no chat photo, no face. A hard daily cap on generation jobs is read
from durable job state before every create.

## What the world never does

- Show an item of a person: a quote, a photo from chat, a perception.
- Filter per viewer inside a unit.
- Share a place across units: every place belongs to one unit's world, and one
  shared across units would need its own ruling on what it may show.
- Generate buildings or districts; structure is built by hand.
- Generate clothing that deforms with the body.
