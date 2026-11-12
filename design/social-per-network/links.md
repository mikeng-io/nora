# Link owners

## Recognition is a page claim

Each network parser recognises the page shapes its owning tool can serve:
Instagram post, reel, story and profile; X post and profile; LinkedIn person
and company. The recogniser returns a typed claim or no claim. It never claims
a whole host, a shortener or a media CDN.

Unreadable-but-related shapes have their own deliberate handling. A mobile
page that the owner requires in canonical form can explain that requirement.
A LinkedIn post is unclaimed because `linkedin_read` cannot serve it, leaving
`web_fetch` available for a public page. Unknown paths and lookalike hosts
are likewise unclaimed rather than redirected to a refusal.

Claims are ordered and expected to be disjoint. Adding a network means adding
one recogniser and one owner registration; it does not add host checks to
`web_fetch`, `media_fetch` or the other owners. Native parsing differences
stay in the network modules.

## One declared wrapper

`NativeTool.link_inputs` declares which parameters of a generic fetcher may
contain an owned URL. One post-pass in tool construction wraps those tools,
examines the named input and asks the registry for a claim. This is the same
kind of declaration-driven seam as output contracts, applied independently of
which agent receives the tool.

On a claim, the wrapper returns the redirect without calling the provider or
downloader. It records the outcome through the fetcher's own instrumentation
with the owner and claim kind, preserving the three-state result contract,
output checking, budget accounting and visibility to direct `spec.call` tests.

A Strands before-tool hook is not the router. It would bypass the tool's
normal result and recording path, depend on agent-specific hook wiring and
make budget ownership ambiguous. The declared wrapper is the only generic
interception point.

## Redirects, not rewrites

A redirect tells Nora which tool and parameter to use, composed from the
owner's claim and the fetcher's own result guidance. It never executes the
owner under the fetcher's name or budget and never bypasses the owner's
asked-turn or disclosure rules. The pasted link is not echoed in the note.

The owner tools also use the registry for another network's link, replacing
separate cross-network refusal matrices. An owner retains its own parser and
read behavior for its own links; all other refusal semantics stay with the
owner.

Read tools may accept `url` as an alias for `link` through one shared alias
resolver. Alias alone has that meaning, two spellings of the same link agree
under the network parser, and conflicting links are refused rather than
choosing one. The redirect note names the declared canonical parameter.

## Negative and internal boundaries

`t.co`, `lnkd.in`, media CDNs and provider-returned attachment URLs are never
claimed. Resolving a shortener would spend a third-party request without
establishing the destination network. Provider media continues through
`media_fetch` under its ordinary custody rules.

Direct internal calls for provider-returned media do not pass through the
generic link wrapper. Platform share rendering is also separate: it consumes
structured shares with its own author and label requirements, not an arbitrary
pasted URL. The host bridge independently validates what it receives because
it does not trust the caller.

YouTube keeps its own source allowlist because playing and reading about a
video are different tasks with no single owner. A network with no owning
reader remains an ordinary web or media fetch, not a guessed registration.
