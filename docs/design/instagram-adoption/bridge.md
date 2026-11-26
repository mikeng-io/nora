# Bridge and messaging

## One application, two contexts

The bridge holds one authenticated Instagram session and its realtime
connection. Messaging concerns threads, messages, reactions and members;
social concerns accounts, stories, posts and comments. They share session
lifecycle and the upstream gateway, not use-case interfaces.

The application separates domain values and errors, use cases, the Instagram
gateway and HTTP transport. Wire responses stay inside the gateway. Typed
identifiers distinguish messaging people, social accounts, usernames and
thread references so a decimal string cannot silently cross contexts.

Every upstream call passes through the gateway's common transport and
application-error handling. A successful HTTP envelope carrying an upstream
error is still a failure. Raw API responses never become the bridge's public
contract.

## Messaging contract

Inbound realtime events become ordinary message, reaction, edit, unsend,
typing, heartbeat, loss and summons signals. The adapter consumes them through
the existing platform boundary and records only admitted rooms. Heartbeats and
explicit loss distinguish a quiet socket from a dead one and dropped history
from a complete transcript.

The bridge resolves thread identifiers and exposes one canonical room key to
Nora. The roster supplies the messaging identity needed for mentions and the
social identity needed by the person index. A missing or incomplete roster is
unavailable rather than silently smaller, because disclosure checks depend on
every account being known.

Text, image, video, animated media and audio normalize into the existing media
model. View-once content that supplies no retrievable bytes is recorded as an
announcement with its author and time, not opened or persisted. That preserves
the sender's explicit choice instead of turning a read into a state-changing
view.

Outbound text and each attachment are separate messages; this transport has no
caption operation to copy from another bridge. Replies, reactions, typing and
unsend use their own resource semantics. Delivery timestamps and identifiers
stay in the delivery record, not in model-facing text.

## Session and failure

Session state is durable enough for resume without replaying an unreliable
history API. Checkpointing and logout need human action; reconnection is
retryable; malformed input, unsupported capability and upstream rejection are
different outcomes. Unknown failure never earns a retry classification.

One degraded session affects messaging and social reads together, but does not
change their meanings. A failed profile is not an empty profile, a failed
story read is not no story, and an unreachable bridge is not evidence of
absence.

Bridge writes are admitted independently of the model. Social reads use only
read resources, while comment publication has its own guards. Network
isolation is deployment defense, not a substitute for those application
contracts.

## Observation

Trace context propagates from Nora's turn through the bridge to Instagram.
Server spans use route patterns and upstream spans carry transport and domain
failure categories, including errors inside a successful HTTP response.
Structured logs join those traces without carrying credentials, message text,
usernames or signed media URLs.

Health reports session and stream state, identity needed by the adapter, and
buffer loss. It does not turn a checkpoint into a healthy empty room or a
healthy empty social result.
