import type { DocumentSpec } from "../catalog";

// The December-trip scenario (design/artifact/00-design.md §15): research,
// then a document to prepare from and to present. Invented facts.
export const trip: DocumentSpec = {
  version: 1,
  root: {
    type: "Page",
    props: { title: "Lisbon in December", subtitle: "Five days, 14 to 19 December. Planning notes for the group." },
    children: [
      {
        type: "Section",
        props: { title: "The short version" },
        children: [
          {
            type: "Markdown",
            props: {
              text: "Mild and wet: **12 to 16 °C**, rain on about half the days. Trams and the old town are better in the rain than they sound; the **Christmas lights** are on from early December.\n\nFlights are the cost that moves, so book those first.",
            },
          },
          {
            type: "Grid",
            props: { columns: 3 },
            children: [
              { type: "Stat", props: { label: "Flights, per person", value: "HK$ 6,400", detail: "return, direct not available", tone: "warning" } },
              { type: "Stat", props: { label: "Stay, per night", value: "€ 95", detail: "shared two-bed apartment" } },
              { type: "Stat", props: { label: "Budget per day", value: "€ 60", detail: "food, transport, tickets", tone: "success" } },
            ],
          },
          {
            type: "KeyValue",
            props: {
              items: [
                { key: "Dates", value: "Sat 14 to Thu 19 December" },
                { key: "Group", value: "4 people" },
                { key: "Visa", value: "Not needed for a Hong Kong passport (90 days)" },
                { key: "Currency", value: "Euro; cards accepted almost everywhere" },
              ],
            },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "Day by day" },
        children: [
          {
            type: "Timeline",
            props: {
              items: [
                { label: "Sat 14", title: "Arrive, Alfama walk", text: "Land mid-morning, metro to the apartment, then a slow walk through the old town at dusk." },
                { label: "Sun 15", title: "Belém", text: "Tram 15 or train from Cais do Sodré; the pastry queue is shortest before 10:00." },
                { label: "Mon 16", title: "Day trip to Sintra", text: "Go early; buy the palace tickets online, the queues are long." },
                { label: "Tue 17", title: "Lisbon at leisure", text: "Markets, the tiles museum, a fado evening (book ahead)." },
                { label: "Wed 18", title: "Cascais and the coast", text: "Train from Cais do Sodré, 40 minutes each way." },
                { label: "Thu 19", title: "Fly home" },
              ],
            },
          },
          {
            type: "Map",
            props: {
              caption: "The four places in the plan, from the old town west along the river to Belém",
              places: [
                { name: "Alfama", lat: 38.7139, lng: -9.1294 },
                { name: "Baixa", lat: 38.7109, lng: -9.1393 },
                { name: "Belém", lat: 38.6979, lng: -9.2063 },
                { name: "Cais do Sodré", lat: 38.7065, lng: -9.1447 },
              ],
              route: [
                { lat: 38.7139, lng: -9.1294 },
                { lat: 38.7109, lng: -9.1393 },
                { lat: 38.7065, lng: -9.1447 },
                { lat: 38.6979, lng: -9.2063 },
              ],
            },
          },
          { type: "Callout", props: { tone: "warning", title: "Check the strike calendar", text: "Transport strikes are announced a few days ahead and can close the metro." } },
        ],
      },
      {
        type: "Section",
        props: { title: "What it costs" },
        children: [
          {
            type: "Chart",
            props: {
              kind: "bar",
              title: "Where the money goes, per person",
              unit: "€",
              caption: "Flights are more than half of the per-person cost; everything else together is under € 500",
              data: [
                { label: "Flights", value: 740 },
                { label: "Food and transport", value: 300 },
                { label: "Stay", value: 119 },
                { label: "Tickets and extras", value: 80 },
              ],
            },
          },
          {
            type: "Table",
            props: {
              caption: "Estimate for four people, in euro",
              columns: ["Item", "Per person", "Group"],
              rows: [
                ["Flights", "≈ 740", "≈ 2,960"],
                ["Stay, 5 nights", "119", "475"],
                ["Food and transport", "300", "1,200"],
                ["Tickets and extras", "80", "320"],
                ["Total", "≈ 1,239", "≈ 4,955"],
              ],
            },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "Good to know" },
        children: [
          {
            type: "Mermaid",
            props: {
              source: "flowchart LR\n  A[Airport] -->|red line 20 min| B[Alameda]\n  B -->|green line 10 min| C[Baixa]\n  C -->|walk 10 min| D[Apartment]",
              caption: "From the airport to the apartment takes about 40 minutes by metro",
            },
          },
          {
            type: "Image",
            props: { media: "a1b2c3d4e5f6", alt: "The building entrance, with a green door between two shops", caption: "What the entrance looks like" },
          },
          {
            type: "Accordion",
            props: {
              items: [
                { title: "Getting around", text: "Buy a **Viva Viagem** card at any metro station and load it with zapping credit; it works on metro, trams and buses.\n\nThe hills are steep: comfortable shoes matter more than a map." },
                { title: "Eating", text: "Lunch menus (*prato do dia*) are the cheapest good meal, usually €10 to €13 with a drink.\n\n- Book dinner on Friday and Saturday\n- Many kitchens close between 15:00 and 19:00" },
                { title: "If it rains all day", text: "The tiles museum, the aquarium, and the covered **Time Out Market** all work." },
              ],
            },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "Before we go" },
        children: [
          {
            type: "Checklist",
            props: {
              items: [
                { text: "Book flights", note: "this week" },
                { text: "Book the apartment" },
                { text: "Sintra palace tickets", note: "online, pick the morning slot" },
                { text: "Fado evening reservation" },
                { text: "Travel insurance" },
                { text: "Type F plug adapter" },
              ],
            },
          },
        ],
      },
      {
        type: "Section",
        props: { title: "Sources" },
        children: [
          {
            type: "Sources",
            props: {
              items: [
                { title: "Portuguese weather service, monthly climate", url: "https://www.ipma.pt/", asOf: "2026-09-30", note: "temperature and rainfall ranges" },
                { title: "Lisbon public transport", url: "https://www.metrolisboa.pt/", asOf: "2026-09-30", note: "Viva Viagem fares" },
                { title: "Sintra palaces, tickets", url: "https://www.parquesdesintra.pt/", asOf: "2026-09-30" },
              ],
            },
          },
        ],
      },
    ],
  },
};
