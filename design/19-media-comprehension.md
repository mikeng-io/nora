# Media comprehension

Custody records which bytes Nora holds. Comprehension records a reading of
those bytes, keyed to the same media object. A stored handle can exist before
a reading is available.

## Stored reading

| Field | Holds |
|---|---|
| Title | What the media object is, in a few words |
| Description | What it shows |
| Content | Legible or audible content, including a voice note's transcript |

Custody takes place during ingest. The comprehension job can add a
reading afterward. Voice-note transcription is saved during custody and
renders as the speaker's words. A later title or description supplements it.

## Reading and rendering

Transcript readers join through the media handle and select the detail
appropriate to the surface. The join gives the engagement prefill and record
tools a common reading. Rendering follows the destination's disclosure and
content-check policy. A source handle can still render when its detail is
unavailable.

`media_fetch` serves a stored reading without reprocessing the bytes. A
specific question about the media asks for a fresh reading. Its answer is
specific to that question and does not replace the stored description.

## Coverage

Video comprehension reads bounded windows and keeps their source times.
A gap or a cap is part of the reading's coverage and must remain visible when
the descriptive text is clipped. Latency and outcome measurements distinguish
a completed read, an object with no readable content and an unavailable read.
