"use client";

// W2 fixture page, not product: renders a demo document through DocumentView.
// `?demo=<name>` picks one; `sink` is a hostile-markdown fixture the e2e reads.
// Deleted with the route when W5 ships the real pages.
import Link from "next/link";
import { DocumentView } from "./registry";
import { DEMOS, DEMO_NAMES } from "@/lib/documents/demos";
import type { DocumentSpec } from "@/lib/documents/catalog";

const SINK: DocumentSpec = {
  version: 1,
  root: {
    type: "Page",
    props: { title: "Kitchen sink" },
    children: [
      {
        type: "Section",
        props: { title: "Hostile markdown" },
        children: [
          {
            type: "Markdown",
            props: {
              text: "A [link](https://example.com), `code`.\n\n![no images](https://example.com/x.png)\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))",
            },
          },
        ],
      },
    ],
  },
};

const MERMAID_SINK: DocumentSpec = {
  version: 1,
  root: {
    type: "Page",
    props: { title: "Mermaid sink" },
    children: [
      {
        type: "Mermaid",
        props: {
          source: "flowchart LR\n  A[<img src=x onerror=alert(1)>] --> B\n  click A href \"javascript:alert(1)\"\n  click B call alert(1)",
          caption: "hostile",
        },
      },
    ],
  },
};

export function DocumentsSpike({ demo }: { demo: string }) {
  const doc = demo === "sink" ? SINK : demo === "mermaid-sink" ? MERMAID_SINK : (DEMOS[demo] ?? DEMOS[DEMO_NAMES[0]]);
  return (
    <div className="mx-auto flex w-full max-w-3xl flex-col gap-8 px-4 py-6 sm:px-6" data-testid="spike">
      <nav aria-label="Demo documents" className="flex flex-wrap gap-3 text-meta text-ink-muted">
        {DEMO_NAMES.map((n) => (
          <Link key={n} href={`?demo=${n}`} className={n === demo ? "font-semibold text-ink" : "underline underline-offset-2"}>
            {n}
          </Link>
        ))}
      </nav>
      <DocumentView doc={doc} />
    </div>
  );
}
