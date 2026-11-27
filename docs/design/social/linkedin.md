---
class: design
subject: how Nora reads and searches LinkedIn through a dedicated account
owns:
  - linkedin reader: the read-only reader for a person's profile, experience and recent posts and for a company page, plus a people search
area: social
links:
  - {to: docs/design/social/README.md, rel: uses}
---

# LinkedIn

## Provider and supported surfaces

A read-only backend behind the dedicated account serves person profiles,
experience, recent posts, company pages and a **people** search; it is not a
jobs search. Each page kind is a separate backend read, never one expensive
browse hoping every section exists.

## Link and subject resolution

A person's public handle selects among profile, experience and recent posts. A
company is identified by its link, because the two handle shapes cannot be told
apart by guesswork. A direct post link is not claimed: the reader cannot serve
it, and reading the author's activity and hoping the post appears would not
ground it. A public post page stays available to a generic fetch under the
[link-owner rules](README.md#link-owners).

## Read and search

Search spends a provider-limited budget and returns candidates with profile
links. Namesakes share names and headlines, so Nora does not choose an identity
from the first plausible row. A profile link stays useful when search budget is
exhausted.

## Account, audience and authority

Profile viewing on the dedicated account is private, which limits what the
viewed person learns but does not make an unsolicited visit welcome. Every read
and search therefore needs a person to have asked in the current turn, and
proactive and reflection passes refuse before parsing or any backend work.
Configuration can leave the readers registered but inert.

## Results, media and custody

Results follow the [shared rules](README.md#rules-every-network-shares).
Missing sections and media are never replaced with invented detail, and
projected provider fields replace raw dumps.

## Public summons

LinkedIn has no summons surface.

## Failure and coverage

A missing backend is not a reason to route through an arbitrary page reader, and
Nora never executes a write on the network. Provider limits, account challenges
and credential expiry are observed limits and are reported, not counters to
invent.
