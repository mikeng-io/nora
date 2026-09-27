// The shape of site/src/assets/rig/rig.json (written by site/scripts/make_rig.py)
// and of one frame of her, as the renderer draws it.
//
// Positions are fractions of the pose's texture: x of its width, y of its
// height. Radii are fractions of its HEIGHT, so a circle stays a circle
// whatever the texture's aspect.

export type Rgb = [number, number, number];

export interface Ellipse {
  cx: number;
  cy: number;
  rx: number;
  ry: number;
}

export interface Eye extends Ellipse {
  rot: number;
  core: Rgb;
  rim: Rgb;
}

export interface Limb {
  id: string;
  pivot: [number, number];
  region: Ellipse;
}

export interface PoseRig {
  size: [number, number];
  /** Where this texture sits in the picture it was cut from (x, y, w, h as fractions). */
  crop: [number, number, number, number];
  /** What the render's own eyes do: the renderer's resting expression. */
  expression: "open" | "closed" | "lidded";
  /** Where the render's own eyes look, in gaze units. */
  gaze: [number, number];
  eyes: Eye[];
  mouth: { cx: number; cy: number; w: number; h: number; colour: Rgb };
  head: Ellipse;
  neck: [number, number];
  face: Ellipse;
  bun: { cx: number; cy: number; r: number };
  hips: [number, number];
  /** The line she rests on: her soles standing, her seat sitting. */
  ground: number;
  limbs: Limb[];
}

export type PoseId = "stand" | "stand-night" | "perch" | "ledge" | "quiet" | "wave" | "lost" | "hop";

/** One frame of her. Every value has a resting state of zero (or one, for scales). */
export interface Frame {
  /** Eye offset: x in units of an eye's width travel, y of its height travel; about -1..1. */
  gaze: [number, number];
  /** Head roll about the neck, radians; + tips the top of her head to the right. */
  roll: number;
  /** Her face features shifting with a turn of the head, texture-height units. */
  turn: [number, number];
  /** Her whole head moving (a nod, a droop), texture-height units. */
  nod: [number, number];
  /** Upper lid, 0 open to 1 closed (the crescent of her closed eyes). */
  lid: number;
  /** 0: the curved lid of a blink; 1: the flat lid of a sleepy or unimpressed look. */
  flat: number;
  /** Tilt of a flat lid, radians, mirrored per eye (+ lowers the outer corner). */
  tilt: number;
  /** The lash line drawn along a flat lid, 0..1. */
  lash: number;
  /** Lower lid rising, 0..1: the ^ ^ of her happy eyes. */
  low: number;
  /** Eye scale, x and y (eyes widen a little as they close). */
  eyeScale: [number, number];
  /** Mouth: 0 a line, 1 a small "o". */
  mouthO: number;
  /** Mouth bend, -1..1; + turns the corners up. */
  smile: number;
  /** Extra blush, 0..1. */
  blush: number;
  /** Breath: the torso's lift, texture-height units. */
  breath: number;
  /** A weight shift: lean about the ground point, radians. */
  sway: number;
  /** Hair and bun trailing a movement, texture-height units. */
  hairLag: [number, number];
  /** Amplitude of the hair's own drift, texture-height units. */
  hairDrift: number;
  /** Squash and stretch about the ground point. */
  squash: [number, number];
  /** Per limb (in the pose's order): rotation about its pivot, radians. */
  limbRot: number[];
  /** Per limb: an extra shift, texture-height units. */
  limbShift: [number, number][];
  /** Seconds, for the hair's drift. */
  time: number;
}

export function restFrame(): Frame {
  return {
    gaze: [0, 0],
    roll: 0,
    turn: [0, 0],
    nod: [0, 0],
    lid: 0,
    flat: 0,
    tilt: 0,
    lash: 0,
    low: 0,
    eyeScale: [1, 1],
    mouthO: 0,
    smile: 0,
    blush: 0,
    breath: 0,
    sway: 0,
    hairLag: [0, 0],
    hairDrift: 0,
    squash: [1, 1],
    limbRot: [0, 0, 0],
    limbShift: [
      [0, 0],
      [0, 0],
      [0, 0],
    ],
    time: 0,
  };
}
