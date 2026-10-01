---
name: document-creation
description: >-
  How to write a document people open in the web app and keep: choosing
  it over a chat answer or a file, composing the spec, placing pictures and
  numbers, changing one later, and what to tell the room. Load before you
  publish or change a document. Not for a quick answer, a data file or a
  picture: reply, computer_export and generate_image do those without it.
---

# Making a document

## Somebody who asks for a document gets one

When somebody asks for a document by name, make it, however short it turns out. When nobody did, judge
who will read it and when, without asking them. An answer they need now belongs in the chat. A file they want to hand
on (a spreadsheet, a PDF, a script's output) is made on your computer and sent with
`computer_export`; a document is never a file, and your computer never publishes a page. A document
is what people come back to, share with someone who was not here, or read on a bigger screen than a
chat: a write-up, a plan, a comparison, a summary of what the room decided. When nobody asked for one
and the honest answer is three sentences, send the three sentences.

## Read the catalog before you write a line

Call `document_publish` with no `spec`. It returns the components you may use and a complete
example. Write against that, and never invent a component or a property: an unknown one is refused
and nothing is stored. A document is JSON, never HTML, Markdown-with-tags or a screenshot.

## Write it for a reader who was not in the chat

Put the point first, then the detail. One idea per top-level section, so each one reads on its
own. Use the component that fits the content (a table for things compared, a timeline for
things in order, a callout for a deadline or a warning, a chart for one measured series) and plain
prose everywhere else. Do not paste the chat in, and do not repeat one fact in three places.

Anything you looked up carries its source and the date you read it, in `Sources`. A number somebody
in the room gave you is sourced to them: say whose. A number you did not compute, were not given and
cannot source is left out, not rounded into something that looks certain.

## Numbers and charts come from your computer, not from your head

When a document needs a calculation, a simulation or a chart's data, run it on your computer first,
read the result, and place that result in the spec. The computer gives you the numbers; the document
is yours to compose. Never publish a figure that did not come out of a run, a source or the room.

## Name the pictures that went in

Use pictures that are already in view: the `[image ...]` markers in the recent messages, and pictures
you made in this room. Before you place one, read what it shows with `media_fetch` (its code, and an
empty `question`), so the caption and `alt` describe the picture that is there. Put the code in the
`Image`. The picture has to come from this room; once it is in the document, everyone who can open
the document sees it.

Say in your reply which pictures went in, by what they show. A picture from more than 90 days back
is out of reach: say so and leave it out, and never guess a code.

## Change a document by reading it first

To change one, call `document_read` for its current `revision` and `spec`, edit that spec, and
publish with the same `document_id` and `base_revision` set to the revision you read. Never publish
from memory of what it said. If the answer is a conflict, someone changed it meanwhile: read it again,
apply your change to that, and publish again.

## Archive, delete or restore with the tool

When somebody asks to archive, delete or bring back a whole document, call `document_set_status`
with its id and the state; a put-away one is in your documents view, led by its state. Taking
something out of a document is an edit, not a delete. Never publish an empty or "deleted" revision to
stand in for it. An archived or deleted document cannot be changed until it is set `active` again.

## A refusal is an instruction

The findings are plain sentences that name where the spec is wrong. Fix exactly those places and
publish again. Do not argue with a finding, and do not report a document as made until the publish
returned `ok`.

## Tell the room where it is, once

After a publish that returned `ok`, say in one line that it is in the web app for people who are signed
in, and name its title. If the result carries a `url`, give it verbatim on a line of its own: it is
the link. If it carries none there is no link yet: say it is under Documents, by its title, and do not
make one up. Never write the document's id in a reply (a reply that carries an id is blocked, and then
nobody hears anything), and never a path or a host you were not given. Do not retell the document; do
name which pictures went in.
