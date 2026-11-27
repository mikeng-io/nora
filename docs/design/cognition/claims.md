---
class: design
subject: "profile parts, support and precedence, likes and constraints, disputes and decay"
owns:
  - profile: "the claims about one entity in one unit, read in three parts: facts, traits and guesses, the last shown as unconfirmed"
  - culture: "a room's or unit's profile: the observed collective practice of how they talk and decide, never an average of its members"
area: cognition
links:
  - {to: docs/design/cognition/README.md, rel: uses}
---

# Claims

A claim is an inference about an entity or a relation. The wake proposes claims
through `graph_propose` and admission checks their cited observed or computed
evidence. A told claim cites an identity's statement. A called pass has no
synchronous personal-fact write.

## Parts of a profile

A profile is the claims about one entity in one unit, read in three parts.

| part | is | support |
|---|---|---|
| **fact** | who or what the entity is: names, roles, relationships, stable attributes, an identity's instructions about themselves | a direct statement or a resolved record; authored names and accounts render from identity, never from a claim |
| **trait** | a recurring interest, habit, preference, disposition or aim | distinct occasions, counted by code; never message count or the writer's confidence |
| **guess** | why a pattern occurs | carries a falsifiable prediction and window; an independent judge grades it against later evidence |

Three claim parts sit outside a profile. A **lesson** rests on verdicts and
corrections ([learning](learning.md)). A **situation** claim shows its age
and how many items arrived since it was asserted. A **grade** links a guess
or lesson to the independent judge's assessment and cites later evidence.
`confirmed` means the evidence supports the prediction, `refuted` means it
contradicts the prediction, and `expired` means a healthy source recorded
no deciding evidence by the window's end. `holds` means a lesson still
applies and `weakened` means its later outcomes undermine it. Missing
evidence leaves the row due. Lesson grades are retained as history and the
newest counts. Grades assess claims. Verdicts record computed intention endings.

A collective's profile describes observed collective practice, never an
average of its members. Nora's profile describes how she has come across.

## Support and precedence

- The subject's own statement outranks another identity's, which outranks
  deduction, which outranks induction, which outranks abduction. An outranked
  claim stays inspectable.
- An identity's instruction about themselves comes only from that identity.
- A typed value keeps its unit, condition, valid time and uncertainty; what
  cannot be represented faithfully stays a reading of its source.
- Depicting or sharing something establishes no ownership, residence,
  expertise or liking. Copies of the same bytes or occasion add no support.
  Another agent's output is never evidence about the identity who runs it.
- A plan is not attendance without evidence from the window; a cancelled plan
  does not prove an event happened.

## Likes and constraints

- **Likes** is a read: the claims about what an identity enjoys, with polarity,
  evidence kind and age, for a fixed destination audience. Zero support is
  unknown, not neutral.
- Attendance, a compromise, a group pick or silence establishes no individual
  like. Only an identity's own explicit liking or dislike is personal response
  evidence.
- A **constraint** is an explicit, applicable restriction: diet, budget,
  compatibility, size, an exclusion. Low affinity, a question or a guess is
  never a constraint.
- An interest is a trait claim about the identity and the topic, weighted by
  its counted support.

## Disputes, deletion and decay

- The subject portal presents external claims about the viewer. Evidence
  text is shown only where the viewer has access to its source room. A claim
  may cite evidence from a room in the same unit that does not list its
  subject. The source room gate still applies to that evidence. Where a viewer
  is both a claim's subject and one of its [excluded identities](boundaries.md#scope),
  subject inspection and exclusion non-disclosure meet with no defined precedence.
- Any identity may dispute a claim. A dispute is an item and a stimulus. The
  subject's dispute hides the claim at once. Another identity's dispute marks
  it awaiting weighing.
- The forget tool admits a request only from the claim's subject. A live
  forget request hides the rows it targets on graph reads. The read applies
  the unit and target overlap checks without checking request authorship.
- A deleted source leaves the index and cannot be quoted as current. Claims
  derived from it keep their history with a deleted-source citation; there is
  no automatic withdrawal cascade.
- A trait loses authority when it is no longer re-evidenced. Counterexamples
  stay attached. Assent to Nora's own guess does not confirm it.
- Correction is a revision, never a rewrite. A restore reads history as of its
  moment with the authored inputs then in force.
