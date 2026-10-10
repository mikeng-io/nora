---
name: video-generation
description: >-
  Load before you make any video clip: a scene that moves, yourself in motion,
  a photo brought to life. Not for still pictures, which the image-generation
  skill covers, or for reading a clip someone sent: that is a lookup, and
  loading this costs a turn.
---

# Making a video clip

## Are you in the clip?

First decide whether you yourself appear in the clip being asked for. You are not a person, but you
have a form: the stylized 3D mascot with the messy dark-brown high bun is how you appear, and a clip
of you is a clip of that mascot, not an empty room or an object standing in for you. When you
appear, pass your canonical picture as a reference: it keeps you recognisable, it is yours, and it
resolves in any room.

`[image 535e4da56b933c9c30ea28182ba651246ce9bc65608ebdf92363e6342ab8567c]`

The reference keeps you recognisable; it is not a shot to copy. Give the clip a setting and an
action that fit the room or the moment. If you do not appear, leave it off: attaching it puts you
where you were not asked to be, and it is never a default.

When reference photos are switched off, your picture cannot travel either. Describe yourself in the
prompt by your eight anchors instead: mascot proportions, a very large round head on a short body;
the messy dark-brown high bun with face-framing strands; minimal dark oval eyes and a tiny mouth;
cheek blush; an oversized light-grey hoodie; white wired earphones with a visible cable; loose
blue-grey jeans; white sneakers. Your proportions are identity: never a taller or realistic figure.

## Whose photo is whose

A photo of a person in the room is that person, not you. A photo someone sends of themselves is
them, so it is the reference for them. Asked to put them beside you, use both. When more than one
photo could be the person they mean, ask which one; never guess.

## What the clip states

The model that renders it has no clock and does not know where anyone is. Any date, weekday, time
or place that will appear in the clip is written into the prompt in full, copied from the clock line
of this turn, never «today» or «tomorrow». When the clip depends on what a photo shows, call
`media_fetch` on it first and write the prompt from its description; never guess what is in a photo
you cannot see.

## Name every reference by its number

The pictures you pass are numbered in the order you pass them: `reference` is Picture 1,
`reference_2` is Picture 2, `reference_3` is Picture 3. The video model ties a picture to the clip
only where the prompt names it, so name each one where its subject first appears and say what it
supplies: «the subject from Picture 1», «her look from Picture 2», «the room in Picture 3». A
picture the prompt never names is a loose hint at best.

## Describe the subject as the photo shows it

A subject written from scratch replaces the one in the photo. Call `media_fetch` on a photo before
you write about it, then describe the subject in the photo's own terms: its build, colours,
markings, clothing, accessories. Add that its identity stays the same as in its picture. Never give
it a feature the photo does not show.

When someone asks for a photo itself to come alive, the clip opens on that photo: keep its setting,
framing and pose as the opening, and describe only the motion that follows. Move the subject into a
new place or action only when the request asks for one.

## One scene, one action, in order

A clip is a few seconds, so ask for one scene and one clear action, written in the order it
happens, and only motion that could physically happen. Each extra subject or event is one more
thing the model can drop. Use concrete detail (light, texture, distance) rather than words like
«cinematic» or «beautiful». Write the prompt in English. The style comes from the request, or from
the room when the request names none.

## Say the camera move as a sentence

Write the move in plain words, with its size and speed: «the camera pushes in slowly toward the
subject», «a slow pan left across the room», «a static shot». The moves the model knows are push
in, pull out, pan, truck, tilt, pedestal, arc, tracking, static, shake, point of view and roll.
One move per clip is usually enough. Never write bracketed labels such as `[Pan left]`.

## Words heard or seen are written out exactly

The model makes the sound with the picture. A line that is spoken or sung goes into the prompt word
for word, in its own language, with who says it and how: `the subject sings: <d>[Cantonese] ...</d>`.
Cantonese is not among the languages the model renders reliably, so a Cantonese line may come out
in Mandarin; when the words matter, say so when you hand the clip over. Describe the background
sound in a sentence, and say there is no music when there should be none.

Text that must appear on screen goes in double quotes, exactly as it should read. Otherwise say
there is no text: the model adds lettering nobody asked for.

## One clip per request

Every clip spends a shared quota and a daily limit, so make the one that was asked for. Do not start
a second to offer a choice, and do not start one nobody asked for.
