import type { DocumentSpec } from "../catalog";

// The shape of the real documents found in S1: a short plan, mostly prose.
export const evening: DocumentSpec = {
  version: 1,
  root: {
    type: "Page",
    props: { title: "Saturday evening", subtitle: "Dinner, then drinks nearby" },
    children: [
      {
        type: "Section",
        props: { title: "Plan" },
        children: [
          {
            type: "Card",
            props: { title: "19:00 Dinner", description: "a table for six is held until 19:15" },
            children: [
              { type: "Markdown", props: { text: "Hot pot place near the station.\n\n- **Order first:** the broth and two platters to share\n- **Skip:** the set menu, it is smaller than it looks\n- Booking is under *Ken*" } },
            ],
          },
          {
            type: "Card",
            props: { title: "21:30 Drinks", description: "ten minutes' walk" },
            children: [
              { type: "Markdown", props: { text: "A bar with a quiet back room; it takes no bookings, so arrive before 22:00." } },
            ],
          },
          { type: "Callout", props: { tone: "info", title: "Last train", text: "00:42 from the station; leave the bar by 00:15." } },
        ],
      },
    ],
  },
};
