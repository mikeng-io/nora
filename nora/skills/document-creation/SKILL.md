---
name: document-creation
description: >-
  How to make a page, report, chart, map or table that people open as a file
  or a link, on your computer, and how to change it later under the same
  link. Load before making any such file. Not for an answer that fits in a
  chat message, or for a picture: generate_image makes pictures.
---

# Making a document people open

This builds on the computer-use skill: the same project folders, the same
loop of running, reading the error and fixing.

## Make a file only when a message will not do

A short answer, a few numbers or a list belongs in your reply. Make a
document when people will open it, keep it, or read it on a bigger screen: a
report with tables, a chart of a series, a diagram, a map of places, a
comparison too wide for chat.

## Work in the task's project folder

Open or reuse `projects/<slug>/`. Keep the source there (`report.md`,
`data.csv`, `chart.json`) and the rendered file in `out/`, so a later change
edits the source and renders again rather than starting over.

## Write compact source and let nora-render draw it

Your output is capped at about 12,000 tokens a turn, and a page of
hand-written HTML spends most of them on markup. Write markdown or data, then
render it:

- `nora-render report report.md out/report.html`: markdown with ```` ```chart ````
  and ```` ```mermaid ```` blocks and local pictures, all inside one page.
- `nora-render chart data.csv out/chart.svg --type line --title "…"`: the
  first column is the labels, one column per series. A JSON source is
  `{"type": "bar", "title": "…", "x": [...], "series": [{"name": "…", "values": [...]}]}`.
- `nora-render mermaid flow.mmd out/flow.svg`, and `nora-render markdown`.
- `nora-render map places.json out/map.png`, from `{"points": [{"address": "…"}]}`,
  needs a network your computer may not have. Exit 3 means it is not
  available: give the places in the report as text instead.

Exit 2 means the source is wrong, and its message says what to fix. Write the
title in the room's language.

## Check the file before you share it

A document that failed to render is not a document. Check the exit code was
0, then look: `computer_read` the start of a page, or `ls -l out/` for its
size (well under 25 MB). Read the figures back against the data. Fix and
render again before anything leaves the computer.

## Share it with computer_export

`computer_export(path="projects/<slug>/out/report.html", to="link", title=…)`
gives a link that opens it. Put the link in your reply in your own words,
with a line on what it shows. Use `to="chat"` when they want the file itself
here. A link you were not given by the tool does not exist; never write one.

## Change it under the same link

When they ask for a change, edit the source in the same project, render
again, check it, and export with the `artifact_id` of the link you made: the
`<computer>` block lists your links with theirs. The link stays the same and
now opens the new version. Export without `artifact_id` only when they want a
separate document.
