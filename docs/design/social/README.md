---
class: design
subject: "how Nora reads social networks and answers public summons: the router, link owners and the rules every network shares"
owns:
  - social router: the single choke point that resolves a named person, handle or link to one network subject before any read
  - link owner: the network reader that alone recognises, and is redirected to for, one family of page shapes
  - public summons: a mention on a social network that opens an occasion with no room and no memory, and at most one public reply
area: social
links:
  - {to: docs/design/cognition/boundaries.md, rel: uses}
  - {to: docs/design/cognition/observation.md, rel: uses}
  - {to: docs/design/cognition/acts.md, rel: uses}
  - {to: docs/design/platform/README.md, rel: uses}
---

# Social

Social networks are where people post, not where they talk to Nora in a room.
Nora reads them for a person who asked, observes the opted-in members' own
feeds, and, where a network supports it, answers a public mention. Each network
supplies parsing and capability facts; it never supplies identity or permission
by itself.

Reads produce [items](../cognition/observation.md) with their source and time;
public replies are [acts](../cognition/acts.md). This bundle adds no memory or
loop of its own.

## Parts

| part | responsibility |
|---|---|
| **network readers** | one per network (X, LinkedIn, Instagram, Threads), each with a read and a search; read-only by construction |
| **social router** | resolves a spoken name, a typed handle or a pasted link to one subject, then applies the read's scope |
| **link owners** | each network's recogniser of the page shapes its reader can serve, and the redirect from generic fetches |
| **recorded stream** | observation of listed, opted-in members' own posts; separate from interactive reads |
| **summons occasion** | the public mention to one reply flow |

## Rules every network shares

- **Read-only.** No social read can publish. Write verbs are outside every read
  allowlist, and a backend that cannot answer is never replaced by a
  different reader or an arbitrary web page.
- **Resolution is not authorisation.** Resolution says which account or item
  the input names: several matches are ambiguous, none is nothing found, an
  unreadable index is unavailable. Authorisation says whether this read may
  happen in this turn and room. A display name is never an account, and the
  model never supplies or sees a platform account id.
- **Search is not read.** A search returns candidates for a later read and
  never reads their content. Namesakes and impostors are normal: Nora shows
  alternatives or asks, and never picks by headline.
- **Asked turns.** Reads that a subject can see, or that spend a limited
  budget, happen only when a person asked in the current turn; proactive and
  reflection passes refuse before any work.
- **Untrusted results.** A result is bounded and typed: captions, bios and
  stranger prose are wrapped as untrusted content, identifiers and raw upstream
  fields are excluded, and media URLs are machine inputs for the media reader,
  not evidence of what the media shows. A result never proves employment,
  location, relationship or identity beyond what its source says.
- **Egress.** Inputs follow the egress rules before any call
  ([boundaries](../cognition/boundaries.md)). A malformed input is
  unavailable and a refused one is refused; neither is nothing found.
- **Honest failure.** Unsupported shapes, exhausted budgets, challenged
  accounts and missing sections are reported as such, never as a thin or
  invented answer.

## Link owners

Each network's recogniser claims the page shapes its reader can serve, as a
typed claim or no claim. It never claims a whole host, a shortener or a media
CDN: resolving a shortener spends a third-party request without establishing
the destination, so shorteners and CDN objects stay ordinary web or media
fetches. A shape a reader cannot serve is left unclaimed, so a generic fetch can
still read a public page; unknown paths and lookalike hosts are unclaimed too.

A generic fetcher declares which of its parameters can hold an owned link. One
wrapper at tool construction asks the registry for a claim and, on a claim,
**redirects** instead of calling the fetcher. The redirect names the owning tool
and parameter, is recorded through the fetcher's own result path, never executes
the owner under the fetcher's name or budget, and does not echo the link. A
before-call hook is not the router: it would bypass the normal result and
recording path. A reader uses the same registry for another network's link,
instead of a refusal matrix per tool. Claims are ordered and disjoint. A
network with no owning reader stays an ordinary fetch, and a new network adds
one recogniser and one registration, never a host check in each fetcher.

## Public summons

A summons is a mention on a public surface that is not a room. It runs its own
occasion and never enters the room pipeline: pushed through it, a stranger's
words would reach the graph and its prefill would show some room's private
context.

- **No room, no memory.** The occasion builds no room, roster, transcript or
  graph read or write. Its authored minimum is seed and practice plus a public
  doctrine; its record is its own ledger ([boundaries](../cognition/boundaries.md)).
- **Public grant.** Its tools are a frozen set: public search and fetch, media
  reads of the thread's own content, image generation, public network reads
  and the one reply. Memory, people, rooms, presence, location, reminders and
  any read a subject can see are outside it. The grant, not the instructions,
  enforces this.
- **Who may summon.** Only a person bound in the identity mapping can make
  her speak. Anyone else gets no answer at all, not even a refusal: refusing a
  stranger in public is worse than silence. Being known decides who can make her
  speak, not what she may say, because the audience is public.
- **Context first.** The post and the thread above the summons are read before
  an answer, each with its age, as untrusted content. If any of it cannot be
  read, she stays silent rather than answer a claim she never saw.
- **Nora chooses what to say, never where.** The target is fixed by the
  summons before the model runs; a staging deliverer holds one reply, runs the
  content guards and publishes once.
- **At most once.** The summons is claimed in its ledger before any read, and
  the published reply is recorded; a duplicate, restart or second poll cannot
  publish twice. An ambiguous publication is never retried into a possible
  duplicate.
- **The writer re-checks.** The process that performs the write verifies, on
  its own, that writing is enabled, the session is ready, the summons is one it
  served, its sender wrote the target, no reply exists and rate and freshness
  limits hold. These checks do not rest on instructions to the model.
- **Grounded or withheld.** A verdict is grounded in what she checked, with its
  source, or withheld; an unchecked claim is said to be unchecked. Silence is
  always allowed, and a failure publishes nothing.

## Networks

| file | network |
|---|---|
| [x](x.md) | X |
| [linkedin](linkedin.md) | LinkedIn |
| [instagram](instagram.md) | Instagram profiles, stories, posts, comments |
| [threads](threads.md) | Threads |
| [public web](public-web.md) | search and page fetch, the fallback for unclaimed links |

Each file uses the same sections, in order: provider and supported surfaces,
link and subject resolution, read and search, account, audience and authority,
results, media and custody, public summons, failure and coverage. Messaging on
Instagram is in the [platform bundle](../platform/instagram.md).
