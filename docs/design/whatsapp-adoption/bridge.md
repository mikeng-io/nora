# Bridge and recovery

## Account and service boundary

The bridge is a small Node service using WhatsApp's companion-device protocol.
It runs in Nora's deployment project, publishes no public listener and uses the
same service authentication as the other bridges. Only its own session state
and explicitly intended capture/action stores are available to it; Nora's
database and model configuration are not.

Pairing links the dedicated account and persists its credentials in the
transport-owned session directory. Losing or invalidating that directory
requires re-pairing but does not destroy Nora's conversation record. Logging
out stops reconnect churn and leaves health able to report an unpaired state.
Credential contents never enter logs, traces, fixtures or model input.

The service exposes self identity, health, roster reads, media custody and
reserved actions. Enabling an integration requires a usable identity answer;
starting deaf with a half-paired account is not a healthy deployment.

## Durable capture

Admission is first: a denied room produces no journal, media bytes or queued
work. Admitted native input is normalized once into the shared message and
observation envelopes, including source scope, object and actor references,
source time, native ordering and coverage.

Admitted media is downloaded into the custody spool before the envelope is
committed, so the journal already names the bytes or their explicit failure.
The capture then appends and syncs the journal. That append is the capture's
commit point and does not wait for Redis, a model or a delivery receipt.

A single-flight drain publishes journal records in order and marks their
queue confirmations. Restart replays unconfirmed records without overtaking
an older head or repeating a confirmed one. Journal failure stops upstream
capture rather than dropping the oldest input and continuing as though the
audit were complete.

Clockless or reused native transition keys use the journaled committed-state
ledger. A genuine repeated state and a stale replay cannot always be
distinguished; ambiguous input is retained for diagnosis without being queued
or advancing the folded state. No sequence number is manufactured to make the
ambiguity disappear.

## Compatibility and health

The legacy HTTP/SSE adapter remains a compatibility surface. Its bounded
offline buffer reports overflow explicitly and cannot establish complete
historical capture. Heartbeats and dropped-event notices are listener-level
health signals, not user messages.

Process health says the HTTP service answers. Pairing says credentials exist.
Socket connectivity says the upstream connection is open. Event freshness says
whether the listener still sees activity in a quiet room. Queue depth,
journal failure, dropped legacy events and action outcomes answer different
questions and are never collapsed into one green status.

## Outbound effects

Every effect is reserved under a stable action identity before native work.
Replay returns the recorded outcome instead of re-sending. A confirmed action
preserves every native post handle in order for later edit, reaction and audit
resolution; an unconfirmed action supplies none.

Retry classification distinguishes a definite refusal from transport
uncertainty. Only a failure that cannot have performed the effect is safely
retryable. Attachment and text parts of one logical reply share its action
semantics without being collapsed into one native post.

Queued receipts, when used, happen only after the corresponding input is
durable. Their own action identity and result are durable too, so restart does
not react twice or confuse a queued receipt with a completed agent answer.
