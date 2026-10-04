import type { DocumentSpec } from "../catalog";

// A day-of pack opened on a phone at the venue: a run of show, a rota, what to carry,
// where people sit, who to call, and the money. Invented names and numbers throughout;
// it is the fixture for the blocks a spreadsheet-shaped brief needs (Schedule, Seating,
// Contact, Countdown, Progress, a tickable Checklist, a donut, a Table with totals).
export const wedding: DocumentSpec = {
  version: 1,
  root: {
    type: "Page",
    props: { title: "Jordan & Sam, day-of pack", subtitle: "Saturday 12 June 2027, Harbour Hall, Kowloon Tong" },
    children: [
      {
        type: "Section",
        props: { title: "At a glance", number: 1 },
        children: [
          {
            type: "Grid",
            props: { columns: 3 },
            children: [
              { type: "Countdown", props: { label: "Until the ceremony", date: "2027-06-12", time: "11:15", detail: "Doors open 10:45" } },
              { type: "Progress", props: { label: "Replies in", value: 74, max: 90, detail: "16 still to answer", tone: "info" } },
              { type: "Stat", props: { label: "Budget", value: "HK$132,000", detail: "all through the treasurer", tone: "neutral" } },
            ],
          },
          { type: "Callout", props: { tone: "warning", title: "Rings and IDs go with the treasurer", text: "Hand both to the registrar at 11:00; the rings stay with the treasurer until the vows." } },
        ],
      },
      {
        type: "Section",
        props: { title: "Run of show", number: 2 },
        children: [
          {
            type: "Schedule",
            props: {
              caption: "Who is where, from the first bus to the last photo",
              start: "07:00",
              end: "14:00",
              lanes: [
                {
                  name: "Treasurer",
                  blocks: [
                    { from: "07:15", to: "08:45", title: "Bride's house", note: "Red packets and cheques in the grey bag", tone: "info" },
                    { from: "09:30", to: "10:45", title: "Bus to venue" },
                    { from: "11:00", to: "11:15", title: "Registrar", note: "Rings and IDs", tone: "warning" },
                    { from: "13:00", to: "14:00", title: "Close the till", tone: "success" },
                  ],
                },
                {
                  name: "Photographer",
                  blocks: [
                    { from: "08:45", to: "09:15", title: "Tea ceremony" },
                    { from: "10:45", to: "11:00", title: "Guests arrive" },
                    { from: "12:15", to: "12:45", title: "Family photos", tone: "info" },
                  ],
                },
                {
                  name: "Music",
                  blocks: [
                    { from: "10:00", to: "11:05", title: "Pre-wedding slideshow" },
                    { from: "11:13", to: "11:45", title: "Entrance and vows", note: "Track 3, backup USB in the clutch", tone: "warning" },
                  ],
                },
              ],
            },
          },
          {
            type: "Timeline",
            props: {
              items: [
                { label: "07:15", title: "Leave for the bride's house", text: "Anyone late is **the treasurer's problem**.", people: ["Treasurer", "Groomsmen"], tone: "info" },
                { label: "11:00", title: "Registrar", text: "- Both IDs\n- The two rings\n- Two witnesses", people: ["Treasurer"], tone: "warning" },
                { label: "11:15", title: "Ceremony", text: "About 30 minutes.", tone: "success" },
                { label: "13:45", title: "Guests leave", text: "Clear reception, count the red packets, take the leftover drinks." },
              ],
            },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "What to carry", number: 3 },
        children: [
          {
            type: "Checklist",
            props: {
              title: "Before leaving the house",
              items: [
                { text: "Red packets, cheques, cash balance", owner: "Treasurer", note: "Counted the night before" },
                { text: "Both IDs and the two rings", owner: "Treasurer" },
                { text: "Tea set, paper cups, dates and lotus seeds", owner: "Ray" },
                { text: "Kneeling cushions x2", owner: "Ray" },
                { text: "Guest book and two pens", owner: "Dana" },
              ],
            },
          },
          {
            type: "Checklist",
            props: {
              title: "Before leaving the venue",
              items: [
                { text: "Leftover red packets and cash", owner: "Treasurer" },
                { text: "Leftover drinks", owner: "Treasurer" },
                { text: "Wedding shoes and the big photo frame" },
              ],
            },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "Who sits where", number: 4 },
        children: [
          {
            type: "Seating",
            props: {
              caption: "Ten seats a table",
              capacity: 10,
              tables: [
                {
                  name: "Table 1",
                  label: "Bride's family",
                  seats: [
                    { name: "Mr Ko", role: "Father", status: "confirmed" },
                    { name: "Mrs Ko", role: "Mother", status: "confirmed" },
                    { name: "Dee Ko", role: "Brother", status: "confirmed" },
                    { name: "Aunt Mo", role: "Aunt", note: "Needs the aisle seat", status: "pending" },
                  ],
                },
                {
                  name: "Table 2",
                  label: "Groom's family",
                  seats: [
                    { name: "Mr Yim", role: "Father", status: "confirmed" },
                    { name: "Mrs Yim", role: "Mother", status: "confirmed" },
                    { name: "Bea Yim", role: "Sister", status: "declined" },
                  ],
                },
              ],
            },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "Who to call", number: 5 },
        children: [
          {
            type: "Contact",
            props: {
              items: [
                { name: "Nina Wu", role: "Venue manager", phone: "5550 0101" },
                { name: "Owen Tam", role: "Bus coordinator", phone: "+852 5550 0102", note: "28-seater, leaves 09:30" },
                { name: "Pat", role: "Photographer" },
              ],
            },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "The money", number: 6 },
        children: [
          {
            type: "Grid",
            props: { columns: 2 },
            children: [
              {
                type: "Table",
                props: {
                  caption: "Paid on the day",
                  columns: ["Item", "Due", "HK$"],
                  align: ["left", "left", "right"],
                  rows: [
                    ["Venue balance", "On arrival", "60,000"],
                    ["Photographer", "After photos", "28,000"],
                    ["Bus", "On return", "8,200"],
                    ["Tips and red packets", "Treasurer's call", "36,000"],
                  ],
                  footer: ["Total", "", "132,200"],
                },
              },
              {
                type: "Chart",
                props: {
                  kind: "donut",
                  title: "Where it goes",
                  unit: "HK$",
                  caption: "Share of the day-of budget by item",
                  data: [
                    { label: "Venue", value: 60000 },
                    { label: "Photographer", value: 28000 },
                    { label: "Bus", value: 8200 },
                    { label: "Tips", value: 36000 },
                  ],
                },
              },
            ],
          },
          {
            type: "Accordion",
            props: { items: [{ title: "If the bus is late", text: "Call the coordinator first, then the venue. The tea ceremony can slip **15 minutes** without moving the registrar." }] },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "Sources", number: 7 },
        children: [
          {
            type: "Sources",
            props: { items: [{ title: "Venue booking confirmation", url: "https://example.com/booking", asOf: "2027-05-20", note: "Balance due on arrival" }] },
          },
        ],
      },
    ],
  },
};
