# Why Nora

Nora is a personal AI assistant with social intelligence. She works with you
one-on-one and in the chats you share with other people, and she stays: for
months, across apps, with the same understanding of who everyone is and what
is still open.

This guide explains what Nora is for and how she works, at the level of ideas.
Exact behaviour lives in the code; the [design](../../design/cognition/README.md)
holds the architecture builders work to.

## The problem

Most AI assistants are built for one person in one thread. You ask, they
answer, and the work ends there. Real life does not look like that.

- **Most of what matters involves other people.** Plans, favours, decisions and
  running jokes happen in shared chats, between people who know each other.
  An assistant that only sees you misses most of the picture, and one dropped
  into a group without understanding it is noise.
- **Most of the work is in between.** The useful moment is often not when you
  ask: it is the booking nobody made, the reminder that should land when it
  will be acted on, the thing someone mentioned a week ago that suddenly
  matters.
- **Memory alone does not make an assistant better.** Remembering more is not
  the same as getting things right. An assistant improves only if it can tell
  whether what it did actually worked.

## What Nora is

Nora is one assistant, with one continuing identity, who lives where you
already talk.

- **She is personal.** You can talk to her privately, ask her for things and
  give her jobs, the way you would any assistant.
- **She is social.** She can be part of shared chats with family, a team, a
  club, or anyone else. She learns who is who, how each group works and what
  each person needs, and she keeps what she learns in one circle out of every
  other.
- **She takes initiative carefully.** In a shared chat she follows along when
  nobody asks, and speaks only when she has something worth saying. Staying
  quiet is a decision she makes, not a failure.
- **She follows through.** What she takes on stays open until something real
  closes it, and she checks how it turned out.
- **She learns from outcomes.** Lessons come from whether her actions worked
  and from people correcting her, not from how much attention she got.

We call the combination **social intelligence**: knowing when to speak, what
each person needs, what may be said in front of whom, and what is still
unfinished.

## What Nora is not

- **Not a chatbot that waits.** She answers when asked, and she also acts on
  her own follow-ups.
- **Not a surveillance tool.** Each room decides what she may hear and say.
  People can see what she believes about them and dispute it.
- **Not self-rewriting.** Her personality and conduct are written by people.
  She learns about people and about her own practice; she never rewrites who
  she is.
- **Not a product.** Nora is an open-source, self-hosted study of assistants
  that live with people over time. There is no hosted service or support
  promise.

## Who this is for

- **People curious about assistants that live with people**: how memory,
  initiative, privacy and learning fit together in one system.
- **Builders** who want ideas or code for long-lived, multi-person agents.
- **Self-hosters** who want their own Nora, on their own hardware, for their
  own people.

## The ideas Nora is built on

1. **One understanding, two loops.** Everything she knows, believes, has
   promised and has learned lives in one place. One loop is Nora in a
   conversation; the other is Nora thinking between conversations.
2. **Evidence before belief.** Every belief points at what it rests on, and
   says how sure she is.
3. **Promises that can be checked.** Every promise names what would close it,
   so "done" is something that happened, not something she claimed.
4. **Learning from consequences.** A lesson is trusted only as far as the
   decisions that relied on it turned out well.
5. **Boundaries by design.** Circles are hard walls, rooms set their own rules,
   and she refuses to act when she cannot be sure what is allowed.
6. **The model judges, code checks.** The AI model proposes what something
   means and what to do; code decides what is admitted, who may see it, and
   never guesses.

The [concepts](concepts.md) page explains the words used across this guide.
