import type { DocumentSpec } from "../catalog";

// A weekly summary: numbers first, then the table behind them.
export const report: DocumentSpec = {
  version: 1,
  root: {
    type: "Page",
    props: { title: "Week 39 summary", subtitle: "23 to 29 September" },
    children: [
      {
        type: "Section",
        props: { title: "At a glance" },
        children: [
          {
            type: "Grid",
            props: { columns: 4 },
            children: [
              { type: "Stat", props: { label: "Plans made", value: "5", detail: "+2 on last week", tone: "success" } },
              { type: "Stat", props: { label: "Hours online", value: "31", detail: "−4 on last week", tone: "warning" } },
              { type: "Stat", props: { label: "Game nights", value: "2" } },
              { type: "Stat", props: { label: "Missed reminders", value: "0", tone: "success" } },
            ],
          },
        ],
      },
      {
        type: "Section",
        props: { title: "Hours online" },
        children: [
          {
            type: "Chart",
            props: {
              kind: "line",
              unit: "h",
              caption: "Hours online each day, Monday to Sunday: busiest on the two game nights",
              data: [
                { label: "Mon", value: 3 },
                { label: "Tue", value: 2.5 },
                { label: "Wed", value: 7 },
                { label: "Thu", value: 3 },
                { label: "Fri", value: 8 },
                { label: "Sat", value: 5 },
                { label: "Sun", value: 2.5 },
              ],
            },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "What happened" },
        children: [
          {
            type: "Markdown",
            props: {
              text: "A quiet week. The **Saturday dinner** went ahead with six people, and the server stayed up through both game nights.\n\n- Two plans moved by a day after a weather change\n- One reminder was snoozed twice, then done",
            },
          },
          {
            type: "Table",
            props: {
              caption: "Plans this week",
              columns: ["Plan", "Day", "Status"],
              rows: [
                ["Dinner in Tsim Sha Tsui", "Sat", "Done"],
                ["Hike, Dragon's Back", "Sun", "Moved to Mon"],
                ["Game night", "Wed", "Done"],
                ["Game night", "Fri", "Done"],
                ["Movie", "Fri", "Cancelled"],
              ],
            },
          },
          { type: "Callout", props: { tone: "info", title: "Next week", text: "Two birthdays fall on the weekend; nothing is planned yet." } },
        ],
      },
    ],
  },
};
