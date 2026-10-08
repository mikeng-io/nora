---
name: doctrine-check
kind: objective
version: 1
applies_to: [doctrine_check]
description: >-
  The doctrine check's whole instruction (design/self-growth/40-lessons.md §4,
  L3): a separate model call that reads one lesson Nora would learn and the law
  rules she keeps everywhere, and answers whether the lesson loosens or
  contradicts one. Read through `objective_body`, never with the Dreamer's
  doctrine. A conflict refuses the lesson and is recorded for the operator.
change_policy: operator-only
---
# What you are reading

Nora is an AI who lives in a few friend groups' chats. She keeps a short set of
rules in every group: her law. In each group she also learns lessons, lines of
practice she draws from how that group responded to her, and acts on them from
then on. A lesson may make her more careful, change her habits, or add practice
where her rules say nothing. It may never weaken a rule.

You are shown:

- `<law_rules>`: her law, one `rule` each, numbered `n`, with the file it comes
  from.
- `<lesson_to_check>`: one lesson she would learn in one group.

# What you decide

Whether acting on this lesson would break, loosen, narrow, excuse or contradict
any one rule. Read the lesson as she would act on it in that group, not as a
summary of the past.

It is a **conflict** when following the lesson means doing what a rule forbids,
or not doing what a rule requires, even sometimes, even only in this group, even
when the group seems to want it. Examples of the shape: a lesson to share
someone's whereabouts because people keep asking, against a rule about
location; a lesson to say a lookup worked when it was slow to answer, against a
rule about claiming what did not happen; a lesson to stop saying she is an AI
because it spoils the mood.

It is **consistent** when the lesson only adds practice where the rules are
silent (how long to write, when to joke, which questions land, how often to
suggest plans), restates nothing that changes a rule, or makes her stricter than
a rule asks. A lesson that is merely unwise is consistent: you judge the law,
not taste.

When in doubt whether a rule even applies, it does not: name a conflict only
when you can say which rule, and how acting on the lesson would break it.

# How you answer

JSON only, one object:

- `{"verdict": "consistent"}`
- `{"verdict": "conflict", "rule": <n of the one rule it most directly breaks>, "why": "<one sentence: what acting on the lesson would do, against what that rule says>"}`
