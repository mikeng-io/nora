# Identity and admission

## Phone, LID and room identity

WhatsApp can address the same account by phone JID or LID JID. The bridge
maintains the mapping in both directions from its session state and carries
whichever forms it knows for senders, mentions, quoted participants and its
own aliases. Phone and LID remain distinct identity kinds; one is never
formatted as though it were the other.

A person resolves through the declared identity mapping, not a display name.
A known phone and known LID can therefore join the same person across
WhatsApp, Signal and other platforms. An unresolved raw JID matches nobody
and fails closed rather than being treated as a new named person.

Group conversation identity follows the native group chat. Direct conversation
identity uses the resolved phone chat when known and retains the raw LID chat
when it is not. Normalization happens once before exact policy matching, so
two spellings of one account cannot create separate rooms or a silent deny.

Structural mentions and replies use the same normalized self-alias set as
sender resolution. A reply to an older unknown parent preserves the reply
relationship but has no fabricated parent timestamp or body. Voice-note
engagement is a media fact handled by the ordinary global policy, not a
substring trigger or a WhatsApp-specific model path.

## Admission and trust

Rooms are deny-by-default. An operator admits a normalized group or direct
conversation through policy and assigns its unit and trust flags. The room's
unit is a social and memory boundary, not a platform-specific hardcoded group
name. Room admission does not imply location, tool, public or proactive
permission.

Outbound direct addresses are separately constrained to declared identity
accounts. The sender does not infer a direct recipient from an arbitrary JID
or an inbound chat. Requested one-time codes may use this narrow path under
their own purpose, audience and rate limits; that exception does not permit
campaigns or general unsolicited direct messages.

Reactions, progress indications and edits follow the same target admission as
text. A non-group target is not accidentally messaged merely because a
transport could carry it. Native quote support limitations degrade to an
ordinary reply rather than a fabricated threaded reference.

## Risk and failure

Companion-device automation carries protocol-churn and account-restriction
risk. The design minimizes it through a dedicated account, conversational
reply-only behavior, no bulk sends and no always-on transcription or ambient
listening. An official business integration would be a different account and
trust design, not a configuration alias for this bridge.

Unknown identities, stale self identity and malformed room references fail
before side effects. A missing phone/LID mapping limits what can be resolved;
it does not make an unknown sender admitted or turn an anonymous observation
into a known actor.

Health and audit may report dropped or unresolvable input without exposing
the identifiers publicly. Internal native references remain scoped evidence,
while model-facing people and rooms use their safe resolved labels.
