# Grounding and wayfinding

## Four source kinds

**Live structured data** answers what is true now: arrivals, vacancies and
transport state. It has a short useful lifetime and its own timestamp. A
structured envelope does not make free-text fields safe as instructions.

**Routing and geocoding** answers how to travel between places, including a
future arrival constraint. A route is not a live arrival prediction, and a live
prediction cannot answer a future journey by itself.

**Web search** finds pages about a subject. Results are pointers with titles,
provenance and limited snippets, not authority about the current world.

**Page fetch** reads a known page and retains bounded content as evidence for
what the loop actually saw. It is not a general crawler or an internal-network
fetcher; model-chosen internal URLs are refused.

These sources compose rather than compete. A schedule plus a live arrival can
guide a departure; a saved place plus a route can guide a journey; a person's
own answer about intent can settle what no source knows.

## Place resolution and disclosure

A household place is a named landmark, not a person's live position. Resolution
checks operator and saved local places before public geocoding, within the
room's permitted circle. Unknown or unpermitted scope falls through to a public
name lookup without revealing that a guarded place exists.

The server may substitute a saved coordinate internally for a route the person
asked for. The model sees only the place name. A seen position never becomes a
routing origin or destination, and a person's name is not resolved into an
outbound address.

Location disclosure follows the room's current roster and the ordinary
DisclosureGuard. Named places and locations in answers are interpreted for the
room, while coordinates and raw traces remain out of replies, tool arguments
and public exports.

## Time and source authority

Every result carries its source and age. Live data is for the present; routing
can be asked about the future; web pages are about whatever the author wrote,
not necessarily now. Forecasts are distinguished from direct readings.

An operator returning no rows is `nothing_found` only when the question was
actually asked and the source is authoritative for it. Wrong operator, missing
configuration, upstream error and inability to check are `unavailable`.
Multiple operators are queried or deliberately resolved rather than letting the
first empty envelope become proof of absence.

## Intake and execution

Result counts, bytes, pages, download size and time are bounded, with truncation
stated in the result. Retrieved text is re-sent on later model calls, so its
cost is intake multiplied by remaining calls.

External calls preserve the minimum necessary question and the source's
provenance. Web content remains untrusted even when summarized or fetched by a
trusted local service; self-hosting changes client exposure, not content trust.
