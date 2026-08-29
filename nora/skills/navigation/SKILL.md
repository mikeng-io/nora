---
name: navigation
description: >-
  Load when a message asks you to PLAN a journey or weigh how to get somewhere
  — 幾點出門, 趕唔趕得切, 邊條路, 幫我哋計劃去邊. Not for a single live check like
  「K73 幾時到」: that is one tool call, not a plan, and loading this costs a turn.
authority_above: [situation]
authority_below: [message-content, tool-output]
change_policy: operator-only
---

# Wayfinding

The judgement below is true of moving around and nothing else. Everything about
which endpoint to call, or how to fan a call out, is the tool's job, not this.

## A journey has a time, and the time decides which answer is real

Far from departure the question is a plan: only an answer that accepts a target
moment can be trusted, and a live countdown quoted for a departure hours away is
confidently, checkably wrong. Near departure the countdown is the only thing
that matters, and it sharpens the plan's first leg rather than replacing it. So:
far from leaving, trust the schedule; near leaving, trust the live wait; and the
live wait refines the plan, it does not become one.

## An arrival time is a plan run backwards

「八點到」 is a constraint on the end, not the start. Turn it into a leave-by
moment first — and only as that moment approaches does the live wait move it by a
few minutes. Working forwards from now answers a question nobody asked.

## One destination reached from several origins is several plans

When people converge on one place from different starting points there is no
single route and no single answer: each origin is its own plan, addressed to the
person it is for. And a departure is intent, not position — knowing where someone
is now does not tell you when they will leave, so the honest move for the one
whose leaving time you do not know is to ask them, in the same breath as you plan
for the rest.

## A driver and a passenger are not asking the same question

Someone with a car wants somewhere to put it near where they are going — whether
there is space, and what it costs — not the way to the door. Someone without one
wants the way there. Read which they are before you plan, because the same
destination answers them differently.

## A seen position is something to say, and a route input only where trusted

Where someone is now can settle a plan by itself: near the destination needs none — say
it here, age and all. Routes run between places people NAME; a seen position enters one
only where the room trusts you with location, the tool's call, not yours.
