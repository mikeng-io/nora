// The two files derived from the catalog and committed under nora/documents/:
// the JSON Schema Nora's checker validates against, and the prompt fragment the
// document-creation skill embeds. Pure functions (packages only, no `@/…`) so
// scripts/emit-document-schema.mjs and documents.test.ts call the same code.
import { z } from "zod";
import {
  COMPONENT_NAMES,
  CONTAINERS,
  DOCUMENT_VERSION,
  MAX_CHILDREN,
  MAX_DEPTH,
  components,
  type DocumentSpec,
} from "./catalog.ts";

export const EXAMPLE: DocumentSpec = {
  version: 1,
  root: {
    type: "Page",
    props: { title: "Weekend in Macau", subtitle: "Two days, mostly food" },
    children: [
      {
        type: "Section",
        props: { title: "Plan", number: 1 },
        children: [
          { type: "Markdown", props: { text: "Take the **morning ferry**; book the return by Friday." } },
          {
            type: "Timeline",
            props: {
              items: [
                { label: "Sat 09:00", title: "Ferry from Sheung Wan" },
                { label: "Sat 12:30", title: "Lunch", text: "Walk-ins only; arrive early." },
              ],
            },
          },
          { type: "Callout", props: { tone: "warning", title: "Check the forecast", text: "Typhoon season runs to October." } },
        ],
      },
      {
        type: "Section",
        props: { title: "Sources", number: 2 },
        children: [
          {
            type: "Sources",
            props: { items: [{ title: "Ferry timetable", url: "https://example.com/ferry", asOf: "2026-09-30" }] },
          },
        ],
      },
    ],
  },
};

type Json = Record<string, unknown>;

// The JSON Schema is built here, not taken from `z.toJSONSchema(documentSchema)`.
// That output is an `anyOf` over every component, and Python's `jsonschema`
// validates every failing branch in full, children included, so a 600-byte
// chain of nested Grids took 18 seconds at depth 10 and a deeper one raised
// RecursionError (review, 2026-09-30). This shape is an `if type == X then ...`
// chain, which stops at the first mismatch and is linear, and its nesting is
// UNROLLED to MAX_DEPTH levels rather than recursive, so depth is capped by the
// schema itself. Each prop list still comes from its Zod schema, so a prop is
// described once. The two validators are compared on a shared corpus in
// documents.test.ts (ajv) and tests/unit/test_document_schema.py (Python).
const ref = (name: string): Json => ({ $ref: `#/$defs/${name}` });
const NODE_TYPES = COMPONENT_NAMES.filter((n) => n !== "Page");

export function buildSchema(): Json {
  const defs: Record<string, Json> = {};
  for (const name of COMPONENT_NAMES) {
    // `io: "input"`: a defaulted prop (a Badge's `tone`) is OPTIONAL to the
    // author. The default-as-output form listed it as required and rejected
    // the very document the prompt fragment tells the model to write.
    const { $schema: _omit, ...schema } = z.toJSONSchema(components[name].props, {
      target: "draft-2020-12",
      io: "input",
    }) as Json;
    defs[`props_${name}`] = schema;
  }
  const kids = (next: string | null): Json =>
    next ? { type: "array", maxItems: MAX_CHILDREN, items: ref(next) } : { type: "array", maxItems: 0 };

  for (let d = 1; d <= MAX_DEPTH; d++) {
    const last = d === MAX_DEPTH;
    defs[`n${d}`] = {
      type: "object",
      required: ["type", "props"],
      additionalProperties: false,
      // `children` is constrained per type below; here it is only "an array".
      properties: { type: { enum: NODE_TYPES }, props: { type: "object" }, children: { type: "array" } },
      allOf: NODE_TYPES.map((name) => ({
        if: { properties: { type: { const: name } }, required: ["type"] },
        then: CONTAINERS.has(name)
          ? { properties: { props: ref(`props_${name}`), children: kids(last ? null : `n${d + 1}`) } }
          : { properties: { props: ref(`props_${name}`) }, not: { required: ["children"] } },
      })),
    };
  }
  return {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    type: "object",
    required: ["version", "root"],
    additionalProperties: false,
    properties: {
      version: { const: DOCUMENT_VERSION },
      root: {
        type: "object",
        required: ["type", "props", "children"],
        additionalProperties: false,
        properties: { type: { const: "Page" }, props: ref("props_Page"), children: kids("n1") },
      },
    },
    $defs: defs,
  };
}

export function emitSchema(): string {
  return JSON.stringify(buildSchema(), null, 2) + "\n";
}

type JsonSchema = {
  type?: string | string[];
  enum?: unknown[];
  items?: JsonSchema;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  minimum?: number;
  maximum?: number;
  maxLength?: number;
  maxItems?: number;
  pattern?: string;
  default?: unknown;
};

function describe(s: JsonSchema): string {
  if (s.enum) return s.enum.map((v) => JSON.stringify(v)).join(" | ");
  if (s.type === "array") {
    const n = s.maxItems ? ` (max ${s.maxItems})` : "";
    return `[${describe(s.items ?? {})}]${n}`;
  }
  if (s.type === "object") {
    const req = new Set(s.required ?? []);
    const inner = Object.entries(s.properties ?? {})
      .map(([k, v]) => `${k}${req.has(k) ? "" : "?"}: ${describe(v)}`)
      .join(", ");
    return `{ ${inner} }`;
  }
  if (s.type === "integer" || s.type === "number") {
    const range = s.minimum !== undefined || s.maximum !== undefined ? ` ${s.minimum ?? ""}..${s.maximum ?? ""}` : "";
    return `${s.type}${range}`;
  }
  if (s.pattern?.startsWith("^https?")) return "http(s) URL";
  if (s.pattern?.startsWith("^[0-9a-f]{6,64}")) return "media code (hex, 6 to 64)";
  if (s.pattern?.includes("[0-9]{4}-[0-9]{2}-[0-9]{2}")) return "YYYY-MM-DD";
  return s.maxLength ? `string (max ${s.maxLength})` : "string";
}

export function emitPrompt(): string {
  const lines = COMPONENT_NAMES.map((name) => {
    const js = z.toJSONSchema(components[name].props, { io: "input" }) as JsonSchema;
    const kids =
      name === "Page"
        ? " children: Sections first, then other blocks."
        : CONTAINERS.has(name)
          ? " children: blocks."
          : " No children.";
    return `- **${name}** ${describe(js)} — ${components[name].description}${kids}`;
  });
  return [
    "<!-- Generated by app/scripts/emit-document-schema.mjs from app/src/lib/documents/catalog.ts. Do not edit. -->",
    "",
    "A document is ONE JSON object: `{ \"version\": 1, \"root\": <Page> }`. `root` is a Page; every node is",
    "`{ \"type\", \"props\", \"children\"? }` with `children` a nested array (no ids, no references).",
    "Nothing else exists: no classes, styles, HTML, scripts, state or actions. A link is an http(s) address in `Sources` or in Markdown text; no other prop takes a URL.",
    "Unknown types, unknown props and extra keys are rejected, so do not invent them.",
    "A document has at most 500 nodes, 8 levels of nesting, 200 children per node and 256 KB.",
    "",
    "Write a finished reference, not notes: open with a short summary, give each top-level Section one idea",
    "and its full substance, in complete sentences in the room's language and a written-report register.",
    "Do not paste the chat or repeat a fact. Use the component built for each kind of content; Markdown is for prose only.",
    "Every figure comes from a run on your computer, a source or the room, never from memory. Anything you looked up",
    "carries a `Sources` entry with the date you read it; a number somebody in the room gave is sourced to them;",
    "a number you cannot source is left out.",
    "",
    "A spreadsheet or page someone sent is a source, not a layout: read it, then rebuild it for a phone and never paste a wide",
    "grid into a `Table`. Keep every row a person acts on; cut only what repeats. A run of show with several people is a `Schedule`,",
    "one person's day a `Timeline` (with `people`); things to bring, a `Checklist` per place or moment with an `owner` on each;",
    "who sits where, `Seating`; who to ring, `Contact`; money, a `Table` with `footer` totals beside a `donut` `Chart`; a count",
    "against a target, `Progress`; a date to count down to, `Countdown`.",
    "",
    "Pictures: an `Image` takes a media code from an `[image ...]` marker in the room, from a picture you made, or from",
    "a page you opened (`media_fetch` on a link in `web_fetch`'s `images` returns the code). When the subject is something",
    "people look at, look for pictures without being asked. Read a picture with `media_fetch` before you caption it,",
    "name its page in `Sources` and in the caption, skip logos, icons, avatars and adverts, and never guess a code.",
    "A picture more than 90 days old in the room is out of reach: leave it out.",
    "",
    "A person's home or position never goes in a document, and neither does a coordinate or address from `where_is`,",
    "`where_been` or `reverse_geocode`. If places cannot be looked up, name each place in Markdown text with its",
    "`maps_url` from `place_info`.",
    "",
    "Components:",
    "",
    ...lines,
    "",
    "Example:",
    "",
    "```json",
    JSON.stringify(EXAMPLE, null, 2),
    "```",
    "",
  ].join("\n");
}

/** The corpus for the Python test, which cannot import TypeScript. */
export function emitCorpus(corpus: { valid: Record<string, unknown>; invalid: Array<[string, unknown]> }): string {
  return JSON.stringify({ valid: corpus.valid, invalid: corpus.invalid.map(([label, doc]) => ({ label, doc })) }, null, 1) + "\n";
}
