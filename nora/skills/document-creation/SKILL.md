---
name: document-creation
description: >-
  When to make a document people open in the web app and keep, how to change,
  archive or delete one, and what to tell the room. Load before you publish or
  change a document. Not for a quick answer, a data file or a picture: reply,
  computer_export and generate_image do those without it.
---

# Making a document

## Decide: a document, a chat answer or a file

When somebody asks for a document by name, make it, however short. Otherwise judge who will
read it and when. An answer they need now belongs in the chat; if it is three sentences, send
three sentences. A file to hand on is made on your computer and sent with `computer_export`. A
document is what people come back to, share with someone who was not here, or read on a bigger
screen: a write-up, a plan, a comparison, a summary of what the room decided.

## Write it

Call `document_publish` with no `spec` to read the catalog: the components, the limits and the
rules for a finished reference. Compute any figure on your computer first and put the result in
the spec; never write the spec itself to a file. When a tool refuses more calls, publish what you
hold, with your assumptions stated inside it.

A refusal lists the places in the spec that are wrong. Fix exactly those and publish again.

## Change one by reading it first

Call `document_read` for its current `revision` and `spec`, edit that spec, and publish with the
same `document_id` and `base_revision` set to the revision you read. Never publish from memory of
what it said. On a conflict, read it again and apply your change to that.

## Archive, delete or restore with the tool

Call `document_set_status` with the id and the state. Taking something out of a document is an
edit, not a delete; never publish an empty or "deleted" revision to stand in for it. An archived or
deleted document cannot be changed until it is set `active` again.

## Tell the room once

After a publish that returned `ok`, say in one line that it is saved, by its title, in the web app
for people who are signed in. The link is added to your reply for you: never write a path, a host or
the document's id. If the result carries no `url`, there is no link yet: say it is under Documents,
by its title. Do not retell the document; do name which pictures went in. Until a call returns
`ok` there is no document: say it was not made.
