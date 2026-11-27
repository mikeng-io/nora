---
class: design
subject: motion, how the desk confirms and the stage lives
owns:
  - scroll is the timeline: "each stage scene is scrubbed by scroll position, plays backwards on scroll up, and never traps the wheel, the keyboard or find-in-page"
area: appearance
links:
  - {to: docs/design/appearance/README.md, rel: uses}
---

# Motion

## Desk: motion confirms

- Durations: 100ms under the pointer, 160ms by default, 240ms for popovers and
  sheets, 320ms for route changes.
- Easing `cubic-bezier(0.2, 0, 0, 1)` in and `cubic-bezier(0.4, 0, 1, 1)` out.
- `transform` and `opacity` only. Only live state loops: a presence pulse,
  Nora's speaking indicator, an open-ended spinner.
- No shimmer skeletons: a static fill sized to the content, cross-faded out.

## Stage: the world moves, Nora lives

- **Scroll is the timeline.** Each scene is scrubbed by scroll position and
  plays backwards when you scroll up. The camera travels through the world; fog,
  water and light move with it. Nothing traps the wheel, the keyboard or
  find-in-page, and every scene can be passed with an ordinary scroll.
- **The world may move on its own** while it is on screen: fog drift, water,
  lantern flame, wind in the trees. Off screen, it stops.
- **Nora is alive, not animated.** She breathes, blinks, follows the pointer or
  what the story wants her to look at, speaks her recorded lines with lip sync,
  and her hair and coat answer the same wind as the fog. Her timings are a
  body's: a breath about four seconds, a blink a quarter of a second.
- **She moves between scenes by hopping.** Each scene has its own pose; she
  crouches, leaves and lands in the next through the camera's move. Her
  performance per scene is fixed to scroll position, so it plays the same way
  every time.
- **Interaction teaches.** The one or two gestures a scene asks for (hold,
  drag, poke) act out what the scene says. None is required to read on.
- **Sound is asked for.** Nothing is heard until the visitor turns sound on;
  her voice plays only on request, and its words are always written beside it.

## Reduced motion and fallbacks

With reduced motion, no script, a data saver or no WebGL2, every scene shows
its key frame as a still with its full text, and scenes cross-fade. Live state
keeps going; everything else stops. The whole story reads without the world.

## Performance

The stage is measured, not assumed: the first scene interactive within 2.5 s on
a mid-range phone over 4G, later assets loaded as the visitor approaches them,
60 fps on desktop with no long frames on a mid-range phone, and the renderer
paused while hidden. A scene that misses its budget is cut down, not shipped
slow.
