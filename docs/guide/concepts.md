# Concepts

The words this guide uses, in plain terms. Each has one meaning everywhere.
The code and the [design](../../design/cognition/README.md) use more precise
names for the same ideas; the table at the end maps one to the other.

## People and places

- **Person.** Someone Nora knows. One person can have accounts on several
  apps; Nora treats them as the same person only when the deployment says so,
  never by guessing.
- **Room.** A place where Nora talks: a group chat, a channel, a private
  conversation, a voice channel. Each room has its own rules for what she may
  hear, what she may say and whether she may speak unasked.
- **Circle.** A set of rooms whose people may share what Nora knows, such as
  family or work. A circle is a hard wall: what she learns in one circle is
  never read in another. Each Nora has a **home circle**, and your private
  conversations with her belong to it.

## What she knows

- **What happened.** Messages, reactions, links, shared locations, results of
  her own actions: the record of events. It is never rewritten, though old events can expire.
- **Patterns.** Counts and baselines worked out from what happened, such as how
  active a room usually is. Code computes them, so they can always be checked
  by computing them again.
- **Belief.** Something Nora thinks is true about a person, a room or a thing.
  A belief points at the evidence it rests on, says how it was reached (someone
  said it, or she worked it out) and how sure she is. Beliefs come in three
  kinds:
  - a **fact**: who or what something is, usually because someone said so;
  - a **trait**: a habit, interest or preference, seen on several separate
    occasions;
  - a **guess**: why something happens, with a prediction that could prove it
    wrong.
- **Profile.** Everything she believes about one person, room or circle, read
  together. A room's profile is its culture; her own profile is how she has
  come across.
- **Dispute.** A person can see what Nora believes about them and challenge
  it. A dispute from the person themselves hides the belief at once.

## What she does

- **Promise.** Something Nora has committed to, for herself or on someone's
  behalf. A promise names what would close it and what would make it no longer
  wanted. It is either **one-off** (book the table, send the reminder) or
  **ongoing** (keep an eye on a game's price while the room is into it).
- **Outcome.** How a one-off promise ended:
  - **achieved**: the thing that would close it happened;
  - **dropped**: it was no longer wanted;
  - **missed**: time ran out with neither, while everything that could have
    closed it was working;
  - **unknown**: time ran out while a source it depended on was down. An
    outage is never counted as her failure.
- **Lesson.** Something Nora has learned about how to act in a circle, such as
  "reminders land better after lunch here". A lesson comes from outcomes or
  from someone correcting her, and keeps a running score of how the decisions
  that relied on it turned out.
- **Act.** Anything she does that changes something outside her own memory:
  a message, a reaction, a document, a song played, a job started. An act
  counts only once its result is recorded.
- **Silence.** Deciding not to act. It is a normal outcome, recorded like any
  other.

## How she thinks

- **Cognitive Graph.** The one place that holds what happened, patterns,
  beliefs, promises, outcomes and lessons, with the links between them. It is
  Nora's whole understanding.
- **Engagement loop.** Nora in a room. It runs when someone calls her, when a
  conversation might want her, or when one of her promises comes due. It is
  the only part of Nora that speaks.
- **Reflection loop.** Nora between conversations. It works out what new
  events mean, revises beliefs and lessons, checks outcomes and schedules what
  to look at next. It never speaks.
- **Turn.** One run of a loop, from what started it to its result. Every turn
  starts fresh from the graph; there is no chat session carried over.
- **Debrief.** A short review at the end of each conversational turn that
  writes down what the turn left open, so the next turn can pick it up.
- **Personality.** Her voice and conduct, written by people as plain text. It
  outranks anything she learns, and she never rewrites it.
- **Policy.** The deployment's rules: who may talk to her, in which rooms,
  what each room may be told, and which circles exist.

## Public words and design words

| this guide | design and code |
|---|---|
| circle | unit |
| home circle | primary unit |
| what happened | item (observed) |
| pattern | aggregate (computed) |
| belief | claim (inferred) |
| fact, trait, guess | profile parts: fact, trait, guess |
| promise | intention (committed) |
| one-off, ongoing | achieve, maintain |
| outcome | verdict |
| missed | leaked |
| lesson | lesson |
| act | act |
| turn | pass |
| engagement loop, reflection loop | engagement loop, reflection loop |
| personality | doctrine |
| policy | policy |
