# 04 — Social tools: split by action, never by network

`social_fetch` today is one tool with `platform` × `what`
(`post`, `profile`, `posts`, `search`, `story`). Once the tool catalogue lands,
a narrow tool costs about 18 resident tokens instead of about 400, and the
reason to fold everything into one enum goes away. This file is how to split
it without losing what the one-tool design was protecting.

## What the one-tool design protects, and still must

`design/instagram-adoption/09-tool-surface-and-naming.md` kept one tool for
two reasons. Neither was token cost, and both survive the catalogue:

1. **The model should not choose the network.** The person index resolves a
   person to their accounts. A tool named `instagram_story` makes the model
   assert a network from a name, which is the guess the index exists to remove.
2. **One read-only allowlist.** R-86 pins read-only at three layers; the
   host-side allowlist is one table. N tools with N allowlists means nobody
   can answer "what may Nora read" from one place.

So: **split the names the model sees by ACTION; keep one router and one
allowlist underneath.**

## The split

| Tool | Target | Returns | Network |
|---|---|---|---|
| `social_profile(person)` | a person, or a handle with a network | identity, privacy, follow state both ways, counts | resolved |
| `social_story(person)` | a person | current story items, each with a media URL | Instagram only today |
| `social_posts(person, cursor?)` | a person | recent posts, paged | resolved |
| `social_post(link)` | a pasted URL | one post: caption, author, media URLs | from the URL |
| `social_search(query, network)` | a query | accounts (Instagram), posts (X) | named, since there is no person to resolve |

All five route through one `SocialRouter` and one allowlist. Threads, or any
later network, joins as **a backend row in the router**, not a new tool. Its
arrival changes no model-facing name.

Paging uses the cursor shape already decided in
`design/instagram-adoption/10-discovery-retrieval-and-paging.md`: opaque
`next_cursor`, `null` means the end, absence never means more. Stories do not
page.

## Media stays one tool

Reading what is *in* a post or story is `media_fetch`, which already exists.
`10-discovery-retrieval-and-paging.md` set this division: social tools find
*that* something exists and where; `media_fetch` finds out what it is.
Instagram's `FetchMedia` is a **backend call behind `social_post`**, not a
model-facing tool. An Instagram-specific media tool would be a second
`media_fetch`.

## What each verb can actually serve today

Stated as measured, not as intended:

| Verb | Instagram | X | Threads |
|---|---|---|---|
| `social_profile` | works (bridge `/accounts/{igid}`, PR #387) but `social_fetch` still routes it to OpenCLI | OpenCLI | no backend |
| `social_story` | works on the bridge, but the Python client POSTs `/social/story` and gets 404 (`nora/infra/social/instagram_client.py`) | none exists | no backend |
| `social_posts` | **no backend.** The Go library's full query registry has none; OpenCLI's `user` returns Instagram's HTML shell | OpenCLI | no backend |
| `social_post` | `FetchMedia` exists, **never tried on a real post** | OpenCLI | no backend |
| `social_search` | works | OpenCLI | no backend |

The two Instagram rows marked "works but" are wiring, not building, and can be
fixed today inside `social_fetch` without waiting for the split (`05`).
`social_posts` for Instagram ships as `unavailable`, honestly, until a backend
exists; the verb is not held back for one network.
