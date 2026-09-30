"use client";

// W2 fixture page, not product: renders a demo document through DocumentView.
// `?demo=<name>` picks one; `sink` is a hostile-markdown fixture the e2e reads.
// Deleted with the route when W5 ships the real pages.
import Link from "next/link";
import { MermaidBlock } from "./blocks";
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

// Sources that the SCHEMA refuses, rendered straight through MermaidBlock so the
// renderer's own defence is tested without the schema in front of it. Each one
// tries a request to evil.example (an image, a font url, a link) or a script.
export const HOSTILE_MERMAID: Record<string, string> = {
  "frontmatter html labels":
    '---\nconfig:\n  htmlLabels: true\n---\nflowchart LR\n  A["<img src=https://evil.example/i.png><a href=https://evil.example/a>x</a>"]-->B',
  "frontmatter font url":
    '---\nconfig:\n  fontFamily: "x;background:url(https://evil.example/ff)"\n---\nflowchart LR\n  A-->B',
  "click link": 'flowchart LR\n  A-->B\n  click B "//evil.example/p"',
  "style url": 'flowchart LR\n  A-->B\n  style A fill:url(https://evil.example/s)',
  "classdef url": 'flowchart LR\n  A-->B\n  classDef x background:url(https://evil.example/c)\n  class A x',
  "label html": 'flowchart LR\n  A["<img src=x onerror=alert(1)>"]-->B',
  "nbsp frontmatter":
    '\u00A0---\nconfig:\n  htmlLabels: true\n\u00A0---\nflowchart LR\n  A["<img src=https://evil.example/n.png>"]-->B',
  "css escape": "flowchart LR\n  A-->B\n  style A fill:u\\72 l(https://evil.example/esc)",
  "semicolon click": 'flowchart LR; A-->B; click B "https://evil.example/semi"',
  "node image": 'flowchart LR\n  A@{ img: "https://evil.example/node.png", shape: imageSquare }-->B',
  "sequence links": 'sequenceDiagram\n  participant A\n  links A: {"x": "https://evil.example/sq"}\n  A->>A: hi',
  "leading space": " flowchart LR\n  A-->B",
};

const IMAGE_DOC: DocumentSpec = {
  version: 1,
  root: {
    type: "Page",
    props: { title: "Image path" },
    children: [{ type: "Image", props: { media: "a1b2c3d4e5f6", alt: "a door" } }],
  },
};

// Not hostile, just bad: a source mermaid cannot parse (it used to leave an
// error graphic on the page), and one dense enough to freeze the page.
const DENSE = "flowchart LR\n" + Array.from({ length: 450 }, (_, k) => `n${k % 30}-->n${(k * 7 + 1) % 30}`).join("\n");
const UNPARSEABLE = 'flowchart TD\n  A["secret label"]-->\n  -->-->';

export function DocumentsSpike({ demo }: { demo: string }) {
  if (demo === "mermaid-fail") {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6" data-testid="spike">
        <div data-case="unparseable"><MermaidBlock source={UNPARSEABLE} caption="does not parse" /></div>
        <div data-case="dense"><MermaidBlock source={DENSE} caption="too dense" /></div>
      </div>
    );
  }
  if (demo === "invalid") {
    // A document the schema refuses: the viewer must say so, not draw it.
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-6" data-testid="spike">
        <DocumentView doc={{ version: 1, root: { type: "Page", props: { title: "T", className: "fixed inset-0" }, children: [] } }} />
      </div>
    );
  }
  if (demo === "image-hostile-id" || demo === "image-uuid") {
    const id = demo === "image-uuid" ? "0b9d9f6e-3c0a-4f57-9a43-0d6f3a1c2e11" : "../../x?y=1";
    return (
      <div className="mx-auto w-full max-w-3xl px-4 py-6" data-testid="spike">
        <DocumentView doc={IMAGE_DOC} documentId={id} />
      </div>
    );
  }
  if (demo === "mermaid-sink") {
    return (
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-4 px-4 py-6" data-testid="spike">
        {Object.entries(HOSTILE_MERMAID).map(([name, source]) => (
          <div key={name} data-case={name}>
            <MermaidBlock source={source} caption={`hostile: ${name}`} />
          </div>
        ))}
      </div>
    );
  }
  const doc = demo === "sink" ? SINK : (DEMOS[demo] ?? DEMOS[DEMO_NAMES[0]]);
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
