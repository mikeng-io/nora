---
class: design
subject: how Nora reads and searches X
owns:
  - x reader: the read-only reader that serves X posts, profiles and an account's recent posts
area: social
links:
  - {to: docs/design/social/README.md, rel: uses}
---

# X

## Provider and supported surfaces

One read-only backend serves a read and a free-text search. Only readable page
shapes count: a post, a profile and an account's recent posts. A login wall,
home timeline, search page, settings page or another network's link is not a
post.

## Link and subject resolution

The link decides the subject. A post link names a post; a profile link or an
explicit handle needs the intended kind of read, a profile or recent posts.
Tracking data is not part of the target, one leading handle marker is syntax and
not part of the account, and the backend receives a validated account or post
identity, never the pasted URL. The recogniser follows the shared
[link-owner rules](README.md#link-owners).

## Read and search

A read takes a link or handle; a search takes free text. Both keep the tool's
ordinary availability, budget and result notes.

## Account, audience and authority

A public read does not notify the account read, so it may serve any admitted
turn, including one no person asked in. The shared egress, untrusted-content
and no-write rules still apply.

## Results, media and custody

Results follow the [shared rules](README.md#rules-every-network-shares).
Media stays a machine input for the media reader under its ordinary custody.

## Public summons

X has no summons surface.

## Failure and coverage

An unreadable shape, a missing backend or an upstream error is unavailable with
a note, never a guessed answer.
