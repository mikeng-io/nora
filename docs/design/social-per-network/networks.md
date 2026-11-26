# Network readers

## X

`x_read` distinguishes a post link from a profile or an account's recent
posts. The link determines the subject; an explicit handle needs the intended
kind of read. `x_search` takes a free-text query. Both use the existing
read-only social backend and preserve the tool's ordinary availability,
budget and result notes.

Only readable page shapes are accepted. A login wall, home timeline, search
page, settings page or unrelated network URL is not treated as a post.
Trailing tracking data is not part of the target, and the backend receives a
validated account or post identity rather than a pasted URL.

Public X reads do not notify the account read, so they may be used in any
otherwise admitted turn. They still carry egress and coordinate screening,
untrusted-content bounds and the same no-write boundary as every social read.

## LinkedIn

`linkedin_read` distinguishes a person's profile, experience and recent
posts from a company page. A person's public handle selects among those
views; a company is identified by its link because the two handle shapes
cannot be distinguished safely by guesswork.

A direct LinkedIn post link is unavailable through this tool. Reading the
author's activity and hoping the post appears would not ground the requested
post. The generic web fetch may still read a public post page when it can,
because the link-owner registry does not claim a shape its owner cannot serve.

`linkedin_search` is a people search, not a jobs search. It spends a
provider-limited budget and returns candidates with profile links. Several
people can share a name and headline, so Nora must not select an identity
from the first plausible row.

Profile, experience and company pages are separate backend reads rather than
one expensive browse that hopes every section exists. Missing sections and
media are not replaced with invented details. A profile link remains useful
when search budget is exhausted.

## Asked-turn and account boundaries

LinkedIn profile viewing is set to private on the dedicated account. That
reduces disclosure of the viewer, but it does not make an unsolicited visit
welcome. Every LinkedIn read and search therefore requires a person to have
asked in the current turn and refuses fire or Dreamer occasions before input
parsing or backend work.

Configuration can leave the tools registered but inert. An unavailable
backend is not a reason to route through an arbitrary webpage reader or to
execute an OpenCLI write verb. Provider monthly limits, account challenges
and cookie requirements are operational limitations to observe, not counters
to invent.

## Input and result safety

Inputs are validated and screened for identifiers and coordinates before any
backend call. A malformed input is unavailable; a screened input is refused;
neither starts a read or is described as nothing found. One leading handle
marker is syntax, not part of the account identity.

Results are bounded and untrusted. Provider fields are projected rather than
dumping raw text, profile links remain actionable, and media URLs retain
their actual availability limits. Search and read results never become proof
of employment, location, relationship or identity beyond what the source
actually says.
