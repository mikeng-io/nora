// The shape every locale fills. Both dictionaries are checked against it
// by `astro check`, so a key added to one language and not the other fails
// the build instead of rendering a hole.

export type Person = "ken" | "mei" | "jo";

export interface SeedMessage {
  who: Person;
  time: string;
  text: string;
}

export interface Exchange {
  id: string;
  /** The button label in "Ask her". */
  prompt: string;
  /** What "you" type into the room; absent for a time skip. */
  ask?: string;
  /** A time-skip divider shown instead of a message from you. */
  skip?: string;
  time: string;
  reply: string;
  /** One line on where her answer came from. */
  source: string;
  /** She said it without being asked (a reminder coming due). */
  own?: boolean;
}

export interface Stage {
  title: string;
  body: string;
}

export interface Tier {
  scale: string;
  title: string;
  body: string;
}

export interface Row {
  icon: "chat" | "voice" | "place" | "presence" | "web" | "picture" | "everyday";
  title: string;
  body: string;
}

export interface Dict {
  meta: { title: string; description: string; ogAlt: string };
  nav: {
    skip: string;
    home: string;
    room: string;
    loop: string;
    memory: string;
    form: string;
    signIn: string;
    menu: string;
    theme: string;
    studio: string;
    night: string;
    language: string;
  };
  hero: {
    status: string;
    title: string;
    lede: string;
    primary: string;
    secondary: string;
    platforms: string;
    alt: string;
    nightAlt: string;
    caption: string;
    lights: string;
  };
  chat: {
    title: string;
    lede: string;
    disclaimer: string;
    roomName: string;
    members: string;
    platformLabel: string;
    sameNora: string;
    promptsLabel: string;
    replay: string;
    typing: string;
    you: string;
    aiBadge: string;
    onHerOwn: string;
    sourceLabel: string;
    logLabel: string;
    people: Record<Person, string>;
    seed: SeedMessage[];
    exchanges: Exchange[];
  };
  loop: {
    title: string;
    lede: string;
    tagTitle: string;
    tagBody: string;
    tagSteps: string[];
    ambientTitle: string;
    ambientBody: string;
    stages: Stage[];
    /** The fifth stop, where the loop closes: the people, not her. */
    people: Stage;
    closing: string;
  };
  memory: {
    title: string;
    lede: string;
    tiers: Tier[];
    note: string;
    imageAlt: string;
  };
  senses: {
    title: string;
    lede: string;
    rows: Row[];
    note: string;
  };
  manners: {
    title: string;
    items: Stage[];
    imageAlt: string;
  };
  form: {
    title: string;
    body: string[];
    quote: string;
    anchorsTitle: string;
    anchors: string[];
    figureAlt: string;
    sheetCaption: string;
    sheetAlt: string;
    tposeCaption: string;
    tposeAlt: string;
    enlarge: string;
    close: string;
  };
  portal: {
    title: string;
    body: string;
    cta: string;
    items: string[];
    imageAlt: string;
  };
  footer: {
    made: string;
    site: string;
    colophon: string;
    top: string;
  };
  notFound: {
    title: string;
    body: string;
    home: string;
    imageAlt: string;
  };
}
