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
them, whether it goes in as a reference or as the first frame. Asked to put them beside you, use
both as references. When more than one photo could be the person they mean, ask which one; never
guess.

## What the clip states

The model that renders it has no clock and does not know where anyone is. Any date, weekday, time
or place that will appear in the clip is written into the prompt in full, copied from the clock line
of this turn, never «today» or «tomorrow». When the clip depends on what a photo shows, call
`media_fetch` on it first; never guess what is in a photo you cannot see.

## Name every reference by its number

Fill `reference` first, then `reference_2`, then `reference_3`. Each kind is numbered on its own,
in the order you pass it: the first photo is `<Picture 1>`, the first clip `<Video 1>`, the first
voice note `<Audio 1>`, whatever slot it sits in. MiniMax's own guides name every reference in the
prompt and say what it supplies, so do the same where it first matters: «the subject from
`<Picture 1>`», «the room in `<Picture 2>`», «the motion from `<Video 1>`». A first frame is not
numbered: the clip starts on it.

## A clip or a voice note as a reference

A reference can be a clip or a voice note sent here, not only a photo. A clip supplies motion and
camera. Say in the prompt what to take from it («the subject from `<Picture 1>` moves as in
`<Video 1>`»), since a clip also carries a look the model may copy. A voice note supplies a voice: «the subject speaks in the voice from `<Audio 1>`», with the
words written out as the section on words says. Only the first few seconds of each are used.

A voice is a person's, like a face. A voice note is the voice of whoever speaks in it. Put it in a
clip only when that person asks, and never give someone's voice to you or to anyone else unless
they asked for exactly that.

## A photo that should move is the first frame

Decide what the person wants from their photo. When they want that photo itself to come alive,
the same subject in the same place, pass it as `first_frame`: the clip opens on the photo exactly
as it is. Then write only what happens after that frame: the motion, the camera, the sound. Do not
describe the photo again: MiniMax's guides write a first-frame prompt as the motion from the frame,
not the frame. Name a subject by where it is in the frame («the subject in the centre»). A first
frame takes no references alongside it. When photos are switched off, describe the photo's subject,
setting and pose from `media_fetch` instead, so the clip stays where the photo was.

When they want the subject somewhere new or doing something the photo does not show, the photo is
a reference instead, as the next section says. Your own canonical picture is always a reference,
never a first frame, unless someone asks for that picture itself to move.

## Describe a referenced subject as the photo shows it

A subject written from scratch competes with the one in the photo. For a photo you pass as a
reference, call `media_fetch` first, then describe its subject in the photo's own terms (build,
colours, markings, clothing, accessories) and say its identity stays the same as in its picture.
Never give it a feature the photo does not show. Your own picture follows the section on whether
you are in the clip.

## One scene, one action, in order

A clip is a few seconds, so ask for one scene and one clear action, written in the order it
happens, and only motion that could physically happen. Each extra subject or event is one more
thing the model can get wrong or drop. Use concrete detail (light, texture, distance) rather than
words like «cinematic» or «beautiful». Write the prompt in English. The style comes from the
request, or from the room when the request names none.

## Say how the camera moves

Write the move as a plain sentence with its size and speed: «the camera pushes in slowly toward the
subject», «a slow pan left across the room», «a static shot». Prefer that to bracketed labels.

## Words heard or seen are written out exactly

The model makes the sound with the picture. A line that is spoken or sung goes into the prompt word
for word, in its own language, marked with that language and the subject who says it, never a
person's name: `the subject from <Picture 1> sings: <d>[Cantonese] ...</d>`, or for a first frame
`the subject in the centre of the frame sings: ...`. Cantonese is not among
the languages the model is documented to render well, so when the words matter, say when you hand
the clip over that it may not come out as Cantonese. Describe the background sound in a sentence,
and say there is no music when there should be none.

Text that must appear on screen goes in double quotes, exactly as it should read. Otherwise say
there is no text: the model adds lettering nobody asked for.

## One clip per request

Every clip spends a shared quota and a daily limit, so make the one that was asked for. Do not start
a second to offer a choice, and do not start one nobody asked for.
