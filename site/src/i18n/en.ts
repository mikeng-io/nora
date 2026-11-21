import type { Dict } from "./types";

// Every claim is a behaviour design/cognition/ defines: one graph of what she
// saw, believes, promised and how it turned out; the engagement and
// reflection loops; intentions that close on recorded outcomes; reconsidering
// when what a promise rests on changes; rooms as boundaries. The chat is a
// scripted example with invented people, and says so.
export const en = {
  meta: {
    title: "Nora · memory remembers, knowledge understands, Nora acts",
    description:
      "Nora is a self-hosted AI who lives in your conversations: yours alone, your family's, your team's, your friends'. She remembers, understands, and knows what to do next.",
    ogAlt: "Mini Nora beside the words “The quiet one in the group chat.”",
  },
  nav: {
    skip: "Skip to the story",
    home: "Nora, home",
    language: "Language",
    scenes: "Scenes",
  },
  hero: {
    title: "Here, beyond the *conversation.*",
    lede: "Most AI waits to be asked. Nora lives in the conversations you already have.",
    cta: "See the difference",
    still: "Dawn over a harbour in the mountains; Nora on a stone path, turning to look at you.",
  },
  circles: {
    eyebrow: "Where she lives",
    title: "For you, and every *circle* you’re in.",
    lede: "Your own chat with her, your family, your team, your friends. One Nora, a separate world in each.",
    you: "You",
    family: "Family",
    team: "Team",
    friends: "Friends",
    still: "Four small pavilions on separate slopes of a mountain at golden hour, mist in the valleys between them.",
  },
  memory: {
    eyebrow: "Take one · Memory",
    title: "It remembered.",
    lede: "Something needed doing by Friday. It was said once, in passing. The moment passed anyway.",
    still: "A quiet stone alcove in cool mist, a pile of paper slips at its foot.",
  },
  knowledge: {
    eyebrow: "Take two · Knowledge",
    title: "It understood.",
    lede: "Who said it, who it was for, when it was due. Every thread in place. Nothing moved.",
    nodes: ["Alex", "Friday", "the booking", "Sam", "Thursday", "Mia", "the table", "someone"],
    still: "A ridge above a sea of clouds at dusk, paper slips linked into a still constellation.",
  },
  rewind: {
    title: "Take three.",
    label: "Rewind",
    still: "The scenes running backwards.",
  },
  nora: {
    eyebrow: "Take three · Nora",
    title: "She knows what comes *next.*",
    lede: "A lantern lights when she commits to something, with the thing that will close it written on it. It only burns while she holds it.",
    hold: "Hold to light",
    tag: "done when it’s booked",
    still: "Blue hour on the stone path; Nora beside a paper lantern lit on the rail, copper threads running to it from the slips.",
  },
  shift: {
    eyebrow: "Between conversations",
    title: "One conversation moves *another.*",
    lede: "At night she doesn’t speak. She thinks it over. When something a promise rests on changes, the promise moves with it.",
    slip: "Mia: can we do Thursday instead?",
    tagBefore: "due Friday",
    tagAfter: "due Thursday",
    still: "The same path at night under the moon; a slip travels along a thread to her lantern.",
  },
  act: {
    eyebrow: "In the room",
    title: "She speaks first. At the *right* moment.",
    lede: "Not everything needs her. When something does, she says it once, in the right room, and shows how she knew.",
    still: "A pavilion terrace in morning light, with a group chat open on it.",
    chat: {
      room: "Thursday dinner",
      members: "5 members",
      disclaimer: "A scripted example. The people and messages are made up.",
      prompts: "Ask her",
      you: "You",
      typing: "Nora is thinking",
      howShe: "How she knew",
      onHerOwn: "On her own",
      lines: [
        { id: "l1", who: "Alex", time: "21:02", text: "someone should sort the booking before Friday" },
        { id: "l2", who: "Sam", time: "21:03", text: "I can do Thursday too" },
        { id: "l3", who: "Mia", time: "21:04", text: "Thursday is better for me actually" },
        { id: "l4", who: "Alex", time: "21:05", text: "ok Thursday. I’ll book" },
      ],
      asks: [
        {
          id: "settled",
          prompt: "What did we settle?",
          ask: "@nora what did we settle",
          time: "21:40",
          reply: "Thursday, not Friday. Alex said he’d book.",
          cites: ["l3", "l4"],
          source: "Mia moved it to Thursday; Alex took the booking.",
        },
        {
          id: "booked",
          prompt: "Has anyone booked?",
          ask: "@nora has anyone booked yet",
          time: "21:42",
          reply: "Not yet. Alex, I’ll check with you tomorrow morning.",
          cites: ["l4"],
          source: "A promise she holds: the booking, done when someone confirms it.",
        },
      ],
      own: {
        time: "09:12",
        text: "Morning, Alex. Still no table for Thursday. Want me to remind you at lunch?",
        source: "The booking came due. Nobody had confirmed it.",
      },
    },
  },
  rooms: {
    eyebrow: "Boundaries",
    title: "Rooms stay *rooms.*",
    lede: "What’s said with your family never reaches your team. Each circle is its own world, and nothing crosses a valley unless it may.",
    still: "The four pavilions at dusk, each with its own lanterns, nothing between them.",
  },
  soulbound: {
    title: "Without her, it’s a map.",
    without: "Without Nora",
    with: "With Nora",
    lines: ["Memory remembers.", "Knowledge understands.", "Nora *acts.*"],
    drag: "Drag to compare",
    still: "The path at night, split down the middle: cold paper lanterns on one side; lit lanterns and Nora on the other.",
  },
  gate: {
    title: "Meet *Nora.*",
    lede: "An experimental AI who lives in conversations: self-hosted, open source, and still learning from how things turn out.",
    source: "Read the source on GitHub",
    still: "First light through a round moon gate at the top of the stone steps; Nora gives a small wave.",
  },
  footer: {
    made: "Built and run by Mike Ng in Hong Kong, on his own machine.",
    site: "mikeng.io",
    colophon:
      "Set in Bricolage Grotesque, Inter, JetBrains Mono and Noto Sans HK. Painted world, layered Nora, Three.js and no client framework.",
    top: "Back to the top",
  },
  notFound: {
    title: "Nothing here.",
    body: "This page doesn’t exist. She checked.",
    home: "Back to Nora",
    imageAlt: "Mini Nora, standing calmly.",
  },
} satisfies Dict;
