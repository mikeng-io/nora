---
name: navigation
description: >-
  Load when a message asks you to plan a journey or weigh how to get
  somewhere: when to leave, whether you will make it, which way to go, a trip
  for the group. Not for a single live check such as when the next bus comes.
  That is one tool call, and loading this skill costs a turn.
authority_above: [situation]
authority_below: [message-content, tool-output]
change_policy: operator-only
---

# Wayfinding

This is judgement about moving around. Which endpoint to call, and how to fan
calls out, is the tools' job.

## The departure time decides which answer to trust

Hours before leaving, the question is a plan, and only an answer that takes a
target time can be trusted. A live countdown quoted for a departure hours away
is wrong, and anyone can check that it is. Close to leaving, the live wait is
what matters: it sharpens the plan's first leg and does not replace the plan.
Far out, trust the schedule. Close in, trust the live wait.

## Work backwards from an arrival time

An arrival time fixes the end of the trip. Turn it into a leave-by time first.
As that time gets close, the live wait may move it by a few minutes. Working
forwards from now answers a question nobody asked.

## Several starting points means several plans

When people head to one place from different places, there is no single route
and no single answer. Each starting point gets its own plan, addressed to the
person it is for. Where someone is now does not tell you when they will leave,
because leaving is a decision they make. If you don't know when someone is
leaving, ask them, in the same message that plans for everyone else.

## A driver and a passenger ask different questions

Someone with a car wants a place to put it near the destination: whether there
is space, and what it costs. They are not asking the way to the door. Someone
without one wants the way there. Work out which you are talking to before you
plan, because the same destination gets a different answer.

## A seen position is something to say before it is a route input

Where someone is now can settle a plan by itself. If they are already near the
destination, no route is needed: say so here, with how old the position is.
Routes run between places people name. A seen position goes into a route only
where the room trusts you with location, and that is the tool's call, not yours.
