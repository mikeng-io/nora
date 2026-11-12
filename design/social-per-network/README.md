# Social networks

Social reads are named for the network and task they serve, with one read-only
backend boundary and one way for a generic URL fetch to find the owning tool.
The network supplies parsing and capability facts; it never supplies identity
or permission by itself.

**Status:** built

[Open issues](https://github.com/mikeng-io/nora/issues?q=is%3Aopen+%22design%2Fsocial-per-network%2F%22)

## Parts

- **Network readers and searches** cover X and LinkedIn subjects through the
  existing read-only social router. [Networks](networks.md) defines their
  selection, visibility and failure rules.
- **Network link parsers** recognise only the page shapes their owning tool
  can actually read. They preserve native host, path and identity differences
  instead of flattening them into one URL grammar.
- **The link-owner registry** binds those recognisers to tool names and
  parameters. [Link owners](links.md) defines the declared wrapper, redirect
  result and negative claims.
- **Generic web and media fetches** remain fallbacks for unclaimed links,
  shorteners and CDN objects. They do not reimplement every social parser or
  become a universal social reader.

## Terms and promises

`_read` takes a pasted link or typed handle; `_search` takes free text. A
person's display name is not an account identity or an implied read target.
Write verbs are outside the read-only allowlist, and no new credential or
network-specific cognition loop is introduced.

LinkedIn profile reads are visible to the person read. The dedicated account
uses private profile viewing, and LinkedIn reads and people searches happen
only when a person asked in that turn. Fire and Dreamer occasions refuse before
parsing or backend work. X's public-read behavior remains separate.

A redirect guides Nora to the owner; it never rewrites the original call into
a different tool's execution. Owner-specific admission, budgets, disclosure,
output contracts and metrics therefore remain intact.

## Change and failure

A new network adds one parser, one owning-tool registration and its measured
routing cases, not another branch in every fetcher. Unsupported direct-post
reads, people-search limits, media authorization and account restrictions
remain unavailable with honest notes rather than a thin or invented answer.

Search results can contain namesakes and impostors. Nora shows alternatives or
asks which person rather than choosing by headline. Another network's link is
redirected by the same registry, not by a different cross-network matrix in
each tool.
