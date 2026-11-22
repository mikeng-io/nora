# Trust and privacy

An assistant that lives in shared chats hears a great deal. Nora is built so
that what she hears is bounded by design, visible to the people it concerns,
and never decided by the AI model alone.

## You run her

- **Self-hosted.** Nora and every store she uses run on hardware you control.
  The AI model and some connected services, such as search or maps, can be
  outside services you choose.
- **One operator.** Each deployment has one operator who writes her
  personality, sets the rules and decides who she knows. There is no central
  service and no shared account.
- **Outside queries stay minimal.** When she searches the web or asks a map
  service for a route, the request is meant to carry only the public question,
  never a private person's name, account, address or location. Saved places
  never leave in a query. The extra filters that screen names and identifiers
  are off by default, and the operator can turn them on.

## Rooms and circles

- **Nothing is allowed until the operator allows it.** Policy names who may
  talk to her and in which rooms. With no room allowed, she refuses to start.
- **Each room has its own rules**: what she may hear, what may be said there,
  and whether she may speak unasked. Being allowed to read a room does not
  grant the right to speak in it.
- **Circles are hard walls.** Rooms whose people must not share what Nora
  knows go in different circles, and nothing she learns in one is read in
  another. A person in two circles is known separately in each.
- **People can be excluded.** A belief can name people it must never be
  revealed to or through, in any reply, reminder or context.

## Private conversations

Your private conversations with Nora belong to her home circle.

- The messages themselves are never shown outside your private conversation.
- A belief she forms from them, marked as coming from a private conversation,
  can inform her in the home circle's other rooms. Her personality rules
  decide whether anything drawn from it is said there. This is a rule she
  follows, not something code can enforce on a paraphrase, so it is stated
  plainly here.
- Other circles never see any of it.

## What she believes about you

- **Everything she believes about you is visible to you** on the portal.
- **You can dispute it.** A dispute from the person a belief is about hides it
  at once. A dispute from someone else marks it for weighing.
- **Beliefs are held to evidence.** She separates what someone said from what
  she worked out, and a guess stays a question when she says it. Being in a
  photo, at a gathering or in a group decision is never taken as evidence of
  what you personally like.
- **Corrections leave a trail.** A correction adds a revision; it does not
  erase the history that made the earlier belief inspectable.

## What she can and cannot change

| | who changes it |
|---|---|
| her values, personality and default conduct | people, in plain text the operator edits |
| who people are, and the rules for each room | the operator |
| her beliefs about people and rooms | evidence, through the reflection loop |
| her lessons about how to act | outcomes and corrections, through the reflection loop |

What she learns sits below what people wrote. It may refine her default
conduct for a circle, but never loosens her values or rules. A lesson
that would contradict her written rules is refused by an independent check.

## When she is unsure, she holds back

- A lookup that failed is "unknown", never "nothing", and she does not act on
  it as if it were nothing.
- If a safety check cannot complete, nothing is sent.
- An action that may or may not have happened is checked before it is ever
  retried, so she does not send the same thing twice.
- Credential and personality leak protection always runs.

## Where to read more

The rules above are the [boundaries](../../design/cognition/boundaries.md) of
the design. Security reporting is in [Security](../../SECURITY.md), and the
threat model in [the security model](../../SECURITY_MODEL.md).
