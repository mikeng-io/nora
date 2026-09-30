import type { DocumentSpec } from "../catalog";

// A technical page: code, options in tabs, detail folded away.
export const runbook: DocumentSpec = {
  version: 1,
  root: {
    type: "Page",
    props: { title: "Restarting the game server", subtitle: "When it stops answering" },
    children: [
      {
        type: "Section",
        props: { title: "First, check", number: 1 },
        children: [
          { type: "Markdown", props: { text: "Most stalls are the **world save**, not the process. Look before you restart." } },
          { type: "Code", props: { language: "shell", text: "docker ps --filter name=game\ndocker logs --tail 50 game" } },
          { type: "Callout", props: { tone: "warning", title: "Save first", text: "A restart during a save can lose the last few minutes of progress." } },
        ],
      },
      {
        type: "Section",
        props: { title: "Restart", number: 2 },
        children: [
          {
            type: "Tabs",
            props: {},
            children: [
              {
                type: "Tab",
                props: { label: "From the host" },
                children: [
                  { type: "Code", props: { language: "shell", text: "docker restart game" } },
                  { type: "Markdown", props: { text: "Wait about **40 seconds** for the world to load." } },
                ],
              },
              {
                type: "Tab",
                props: { label: "From the portal" },
                children: [
                  { type: "Markdown", props: { text: "Open the game's page and use the restart control. It saves first, then restarts." } },
                ],
              },
            ],
          },
          {
            type: "Accordion",
            props: {
              items: [
                { title: "It still will not come up", text: "Check free disk space, then the last lines of the log:\n\n```\ndf -h /data\n```\n\nA full disk is the usual cause." },
                { title: "Rolling back a save", text: "Backups are kept daily for a week. Restore the newest one from **before** the problem began." },
              ],
            },
          },
        ],
      },
    ],
  },
};
