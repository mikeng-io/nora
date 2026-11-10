# Result reporting

## Text at the decision point

A description names the tool's outcomes that the model must distinguish before
calling. The returned result explains the outcome that actually occurred:
status-specific meaning, relevant field semantics, safe next steps and what the
reply may disclose. Result interpretation is paid only for the tool that fired.

The result's `note` or reason is authoritative for that outcome. The
`output_contract` seam checks required result shape and preserves additional
data; it does not invent a note or convert an error into a successful lookup.
A status vocabulary and its notes must cover delegated backend outcomes as
well as local returns.

Pre-call writing guidance is different. Voice, argument constraints,
identifier bans, idempotency and behavior-changing MUSTs stay in their
appropriate doctrine or schema because they govern the call itself. Allowed
values belong in typed arguments rather than prose inventories.

## Three-state honesty

`ok`, `nothing_found` and `unavailable` remain distinct. A refused or partial
call reports exactly which parts succeeded, and an unavailable dependency is
never described as an empty answer. A result note guides honest reporting but
does not prove the user-visible reply obeyed it.

Changes that move interpretation from descriptions into results require
reporting measurements as well as routing measurements. The relevant cases
return actual failures, refusals and empty answers through the real tool path,
then judge what Nora says. Longer responses and longer descriptions are both
costs to measure rather than assuming one is always better.

## Scope and disclosure

A result cannot widen the authority under which the tool was admitted. Notes
may explain safe disclosure or a follow-up read, but they do not grant another
room, coordinate, identifier or action. They travel with the returned payload
and are not resident on unrelated turns.

Missing, malformed or unavailable results fail closed toward an honest unable-
to-check answer. A result that claims no outcome is not evidence that the
operation succeeded, and a successful load or write is never inferred from a
human-readable note alone.
