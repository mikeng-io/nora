# Reading and coverage

## Windows and concurrency

Short media is read whole where the provider and result budget allow. Longer
media is cut into temporal windows with stable identity and source intervals.
Window reads may gather concurrently, but their descriptions are zipped back
to source order, never completion order.

Each window fails locally: unavailable, refused, empty or truncated output
becomes an unread interval while other windows continue. One exception cannot
discard valid sibling descriptions. Failed intervals remain visible in the
coverage result and never enter the described set.

Fan-out is owned at several levels: per-read window slots, ingest jobs, shared
tool executor slots and per-turn nested media reads. Aggregate provider demand
is sized from those owners and concurrent turns, not from one invocation's
semaphore. Queueing is part of the wall-clock budget, not free capacity.

## Audio and speech

Video preprocessing no longer discards audio by construction. Speech, lyrics
and meaningful sound are read alongside visual action and on-screen text.
Standalone audio uses the same modality boundary rather than pretending to be
voice-note STT.

Descriptions preserve the difference between verbatim evidence and summary.
Words that must be exact are attributed and timecoded; uncertain speaker
identity, language and transcription remain uncertain. Identifier and
coordinate protections apply to transcripts and generated descriptions just as
they do to visible text.

Previously stored silent readings are not rewritten merely because audio or a
provider changed. A new read is explicit about its provider, model and time so
old evidence is not misrepresented as new understanding.

## Timeout derivation

A model call has a deadline, and a tool invocation has a different, enclosing
budget. An invocation with multiple windows takes serial waves. Concurrent
invocations overlap rather than sum, while queued invocations and waves add
wall time.

The per-call timeout is derived from the invocation's serial wave count, not
from the number of media items or concurrent calls. The enclosing turn budget
includes those waves, nested reads, preprocessing and queueing. A low per-call
timeout cannot silently override the configured ceiling needed for the common
single-window case.

Truncation by token limit or time is unavailable. It is never rendered as a
short but complete reading. Deadline and size changes must preserve that
fail-closed distinction and leave the invocation's own outer lifecycle honest.

## Coverage and custody

Coverage is a set of intervals, not a duration total. A capped clip, an ffmpeg
refusal and a model refusal can combine; the report says exactly which source
intervals were read and which were not, including middle holes. A partial read
never claims to have watched only the first part unless that is literally true.

Media URLs and bytes remain under existing custody and download rules. A
description is not evidence that the source was seen when its read failed or
was withheld. Cache hits and fresh reads carry the same coverage and provenance
contracts.

Cancellation is awaited. It stops provider calls, downloads, ffmpeg and scratch
writes before the tool returns, and no cancelled read persists a description
as if it completed.
