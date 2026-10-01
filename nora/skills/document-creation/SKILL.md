---
name: document-creation
description: >-
  How to write a document people open in the web app and keep: choosing
  it over a chat answer or a file, composing the spec, choosing components,
  placing pictures and numbers, changing one later, what to tell the room. Load before you
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

## Write a finished reference, not notes

A document is read later, kept, and opened by people who were not in the chat. Open with a short
summary of what it covers and what to do with it. Then give every section its full substance: the facts,
the reasoning behind a recommendation, the alternatives you set aside, and what could go wrong or change.
Write complete sentences and full names, in the room's language but in the register of a written report,
not the register of chat. A list of fragments is a draft, and a draft is not published. One idea per
top-level section, so each one reads on its own. Do not paste the chat in, and do not repeat one fact in
three places.

Anything you looked up carries its source and the date you read it, in `Sources`. A number somebody
in the room gave you is sourced to them: say whose. A number you did not compute, were not given and
cannot source is left out, not rounded into something that looks certain.

## Match each piece of content to the component built for it

The catalog is the whole set, and it is there to be used. Things compared are a Table or Tabs. Things
in order are a Timeline. One measured series is a Chart. A mechanism, or a route between steps, is a
Mermaid diagram. Facts about one thing are KeyValue, a headline figure is a Stat, and a status or tag is a Badge. A
deadline or a warning is a Callout. Code or terminal output is Code. Things to prepare are a Checklist. Detail a reader may skip is an Accordion.
Pictures are Image, and where things are is a Map. Markdown is for prose only: a document made of
Markdown blocks where a component fits is a note, not a document.

## Show where things are with what you can ground

A Map takes coordinates, and none of your tools returns any, so never write them from memory. When position
matters and you hold no coordinates, name each place in Markdown text with its map link from `place_info`.

## Numbers and charts come from your computer, not from your head

When a document needs a calculation, a simulation or a chart's data, run it on your computer first,
read the result, and place that result in the spec. The computer gives you the numbers; the document
is yours to compose. Never publish a figure that did not come out of a run, a source or the room.

## Name the pictures that went in

A picture comes from the `[image ...]` markers in the recent messages, from one you made in this room, or
from a page you opened. `web_fetch` lists a page's picture links under `images`; look at one with
`media_fetch(url=...)`, which keeps it and returns a `code`. Do not wait to be asked: when the subject is
something people look at, look for pictures of it. Choose ones that show what the section is about, and
skip logos, icons, avatars and adverts.

Before you place one, read what it shows with `media_fetch` (its code, and an empty `question`), so the
caption and `alt` describe the picture that is there. Put the code in the `Image`. Once it is in the
document, everyone who can open the document sees it. A web picture is somebody else's: name the page it
came from in `Sources` and in the caption. If `media_fetch` returns no `code`, it was not kept: leave it
out, and never guess a code.

Say in your reply which pictures went in, by what they show. A picture from more than 90 days back in the
room is out of reach: say so and leave it out.

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
