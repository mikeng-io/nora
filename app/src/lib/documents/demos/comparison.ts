import type { DocumentSpec } from "../catalog";

// A decision: two options, the facts side by side, a recommendation.
export const comparison: DocumentSpec = {
  version: 1,
  root: {
    type: "Page",
    props: { title: "Which apartment?", subtitle: "Two options for the December trip" },
    children: [
      {
        type: "Section",
        props: { title: "Side by side" },
        children: [
          {
            type: "Table",
            props: {
              columns: ["", "Alfama loft", "Baixa flat"],
              rows: [
                ["Price per night", "€ 110", "€ 95"],
                ["Bedrooms", "2", "2"],
                ["Metro", "12 min walk", "2 min walk"],
                ["Lift", "No, 4 floors", "Yes"],
                ["Noise", "Quiet", "Street below"],
                ["Cancellation", "Free until 7 Dec", "Free until 30 Nov"],
              ],
            },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "The case for each" },
        children: [
          {
            type: "Grid",
            props: { columns: 2 },
            children: [
              {
                type: "Card",
                props: { title: "Alfama loft", description: "atmosphere" },
                children: [
                  { type: "Badge", props: { text: "more charming", tone: "info" } },
                  { type: "Markdown", props: { text: "Old-town views and a quiet street. The four flights of stairs are the catch, with luggage and in the rain." } },
                ],
              },
              {
                type: "Card",
                props: { title: "Baixa flat", description: "convenience" },
                children: [
                  { type: "Badge", props: { text: "cheaper", tone: "success" } },
                  { type: "Markdown", props: { text: "Next to the metro and a lift. Noisier at night, especially on weekends." } },
                ],
              },
            ],
          },
        ],
      },
      {
        type: "Section",
        props: { title: "Recommendation" },
        children: [
          { type: "Callout", props: { tone: "success", title: "Baixa flat", text: "It is cheaper, the airport link is easier, and a lift matters with four suitcases. The loft wins only if quiet is the priority." } },
        ],
      },
    ],
  },
};
