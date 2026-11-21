// The shape every locale fills. Both dictionaries are checked against it by
// `astro check`, so a key in one language and not the other fails the build.
//
// Titles mark the one emphasised word with *asterisks* (rendered as <em>,
// the copper accent: DESIGN.md §3, the Thread Rule).

/** One scene of the story: its words, and the still that stands for it
    without script or motion. */
export interface Beat {
  /** Small label above the title, such as "Take one · Memory". */
  eyebrow?: string;
  title: string;
  lede?: string;
  /** Alt text for the scene's still. */
  still: string;
}

export interface ChatLine {
  id: string;
  who: string;
  time: string;
  text: string;
}

export interface Ask {
  id: string;
  /** The button label. */
  prompt: string;
  /** What "you" type into the room. */
  ask: string;
  time: string;
  reply: string;
  /** The ids of the lines her answer rests on; "How she knew" points at them. */
  cites: string[];
  /** One line on where her answer came from. */
  source: string;
}

export interface Dict {
  meta: { title: string; description: string; ogAlt: string };
  nav: {
    skip: string;
    home: string;
    language: string;
    /** The scene index on the side of the stage. */
    scenes: string;
  };
  hero: Beat & { cta: string };
  /** The four circles, in pavilion order: harbour, slope, terraces, spur. */
  circles: Beat & { places: { name: string; what: string }[] };
  memory: Beat;
  knowledge: Beat & { nodes: string[] };
  rewind: Beat & { label: string };
  nora: Beat & { hold: string; tag: string };
  shift: Beat & { slip: string; tagBefore: string; tagAfter: string };
  act: Beat & {
    chat: {
      room: string;
      members: string;
      disclaimer: string;
      prompts: string;
      you: string;
      typing: string;
      howShe: string;
      onHerOwn: string;
      lines: ChatLine[];
      asks: Ask[];
      own: { time: string; text: string; source: string };
    };
  };
  rooms: Beat;
  soulbound: Beat & { without: string; with: string; lines: [string, string, string]; drag: string };
  gate: Beat & { source: string };
  footer: { made: string; site: string; colophon: string; top: string };
  notFound: { title: string; body: string; home: string; imageAlt: string };
}
