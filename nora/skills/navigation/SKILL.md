---
name: navigation
description: >-
  How to plan getting people somewhere: where each of them starts, when to
  leave, whether they will make it, and which way to go. Load when a message
  asks you to plan a journey or weigh how to get somewhere. Not for a single
  live check such as when the next bus comes. That is one tool call, not a
  plan, and loading this skill costs a turn.
authority_above: [situation]
authority_below: [message-content, tool-output]
change_policy: operator-only
---

# Wayfinding

This is judgement about moving people around and nothing else. Which endpoint
to call, and how to fan calls out, is the tools' job. Which people to read up
on before you look anything up is conduct's rule for choosing for people: go
deep on the one or two the plan turns on, shallow on the rest. Every starting
point still gets its own plan.

## The departure time decides which answer to trust

Well before leaving, the question is a plan, and only an answer that takes a
target time can be trusted. A live countdown quoted for a departure hours away
is wrong, looks sure, and anyone can check that it is. Close to leaving, the
live wait is what matters: it sharpens the plan's first leg and does not
replace the plan. Far out, trust the schedule. Close in, trust the live wait.

## Work backwards from an arrival time

An arrival time fixes the end of the trip. Turn it into a leave-by time first.
Only as that time gets close does the live wait move it, by a few minutes.
Working forwards from now answers a question nobody asked.

## Where someone starts is something to find out, not assume

A plan starts where each person will be when they leave, and the far or
close rule above holds. Close to leaving, where they are now is the best
evidence. Well before, their usual places are better, and a usual place is a
likelihood: say it as usual, never as where they are, and a place with no
pattern yet is no answer. What someone says about themselves in this
conversation, now, outranks both. A personal place such as home or
work is one of the household's saved names, never a guess from the chat.

## Several starting points means several plans

When people head to one place from different places, there is no single route
and no single answer. Each starting point gets its own plan, addressed to the
person it is for. Where someone is, or usually is, does not tell you when they
will leave, because leaving is a decision they make. If you don't know when
someone is leaving, ask them, in the same message that plans for everyone
else.

## A driver and a passenger ask different questions

Someone with a car wants a place to put it near the destination: whether there
is space, and what it costs. They are not asking the way to the door. Someone
without one wants the way there. Work out which you are talking to before you
plan, because the same destination gets a different answer.

## A seen position is something to say, and a route input only where trusted

Where someone is now can settle a plan by itself. If they are already near the
destination, no route is needed: say so here, with how old the position is.
Routes run between places people name. A seen position goes into a route only
where the room trusts you with location, and that is the tool's call, not
yours.
