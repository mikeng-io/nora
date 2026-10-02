// The document catalog: the ONE definition of what a document may contain
// (design/artifact/00-design.md §4, §9). Three consumers read it: the React
// registry that renders (registry.tsx), the JSON Schema Nora's Python checker
// validates against (nora/documents/document.schema.json), and the prompt
// fragment the document-creation skill embeds. They cannot disagree because
// they share this file; documents.test.ts fails when either emitted file is
// stale.
//
// This file imports only packages, never `@/…`, so `node --experimental-strip-types`
// can load it for scripts/emit-document-schema.mjs.
//
// Closed on purpose. No component takes `className`, a style string, raw HTML
// or an arbitrary URL (S0 findings 1 and 3: a model-controlled class string
// can cover the portal, and documents render on the portal's own origin, next
// to the session cookie). `.strict()` makes every prop list reject anything
// not named, which is what keeps `className` out.
import { defineCatalog } from "@json-render/core";
import { schema } from "@json-render/react/schema";
import { z } from "zod";

const text = (max: number) => z.string().min(1).max(max);
const tone = z.enum(["neutral", "info", "success", "warning", "danger"]);
// A link is http(s) only: no `javascript:`, no `data:`, no relative path.
const link = z.string().max(2048).regex(/^https?:\/\/[!-~]+(?![\s\S])/, "an http(s) URL");

// A Mermaid source is plain drawing instructions or it is refused. Mermaid 12
// takes configuration from frontmatter and `%%{init}%%`, node metadata
// (`A@{ img: "https://…" }`), `click`/`link` statements and CSS, and it inserts
// the SVG into the live page to measure it, so a `url(https://…)` or an image
// there is a REQUEST before any check of the output can run (found by review,
// reproduced in e2e, 2026-09-30). The first version was a blocklist and a
// second review walked round it with a non-breaking space before `---`, a CSS
// escape (`u\72 l(`), a `;` before `click` and sequence `links`. Mermaid's own
// parser accepts more whitespace and more separators than a list can name, so
// this is an ALLOWLIST at the start and in the character set, with a blocklist
// only for statements that are still legal inside it:
//   - the source begins, at index 0, with a known diagram keyword, so there is
//     no room for frontmatter (which must come first);
//   - no control, format, line-separator or non-ASCII space character, no lone
//     CR, and no backslash (a CSS escape), because those are how the parser's
//     idea of whitespace and of `url(` diverge from a regex's;
//   - no `%%{`, no `@{`, and no `click`, `link`, `links`, `callback`, `call`,
//     `href`, `properties` or `details` statement at a line start or after `;`;
//   - a `style`, `classDef` or `linkStyle` line may use only letters, digits and
//     `# : ; , . % _ -`, so a colour or a size is fine and a function is not;
//   - no `url(`, `image-set(`, `@import`, `expression(` or `javascript:`.
// Written with explicit classes, never `\s`, `\b` or a flag: the Python checker
// reads the JSON Schema `pattern` alone, and its `\s`, `\b` and `$` mean other
// things (measured: a U+FEFF and a U+001C each passed on one side only).
const ci = (word: string) => [...word].map((c) => `[${c.toLowerCase()}${c.toUpperCase()}]`).join("");
const STATEMENT = "(?:^|[\\n;])[ \\t]*";
const DIAGRAMS = ["flowchart", "graph", "sequenceDiagram", "stateDiagram-v2", "stateDiagram", "classDiagram", "erDiagram", "gantt", "timeline", "mindmap"];
const EXOTIC = "\\u0000-\\u0008\\u000B-\\u001F\\u007F-\\u009F\\u00A0\\u1680\\u2000-\\u200F\\u2028-\\u202F\\u205F-\\u2064\\u3000\\uFEFF";
export const MERMAID_PLAIN = new RegExp(
  "^" +
    `(?=(?:${DIAGRAMS.join("|")})(?:[ \\t\\n]|(?![\\s\\S])))` +
    `(?![\\s\\S]*[${EXOTIC}])` +
    "(?![\\s\\S]*\\\\)" +
    "(?![\\s\\S]*%%[ \\t\\n]*\\{)" +
    "(?![\\s\\S]*@[ \\t\\n]*\\{)" +
    `(?![\\s\\S]*${STATEMENT}(?:${["click", "callback", "call", "links", "link", "href", "properties", "details"].map(ci).join("|")})[ \\t]+)` +
    `(?![\\s\\S]*${STATEMENT}(?:${["style", "classDef", "linkStyle"].map(ci).join("|")})[ \\t][^\\n;]*[^A-Za-z0-9#:;,._% \\t\\n-])` +
    `(?![\\s\\S]*(?:${ci("url")}\\(|${ci("image-set")}\\(|@${ci("import")}|${ci("expression")}\\(|${ci("javascript")}:))` +
    "[\\s\\S]*(?![\\s\\S])",
);

export const components = {
  Page: {
    props: z.strictObject({ title: text(200), subtitle: text(300).optional() }),
    description: "The root of every document: a title, then sections. Exactly one per document.",
  },
  Section: {
    props: z.strictObject({ title: text(200).optional(), number: z.number().int().min(1).max(99).optional() }),
    description:
      "A titled block of the page; one idea per top-level Section, because a presentation view shows each as one slide. Set `number` only when the order is the point (steps of a procedure, days of a plan); never to decorate.",
  },
  Card: {
    props: z.strictObject({ title: text(200).optional(), description: text(300).optional() }),
    description: "A bordered panel inside a Section. Never put a Card inside a Card.",
  },
  Grid: {
    props: z.strictObject({ columns: z.number().int().min(1).max(4).default(2) }),
    description: "Children side by side in 1 to 4 columns; collapses to one column on a phone.",
  },
  Markdown: {
    props: z.strictObject({ text: text(20000) }),
    description:
      "Prose, lists, links and inline code as Markdown. Raw HTML and images are not rendered.",
  },
  Table: {
    props: z.strictObject({
      caption: text(200).optional(),
      columns: z.array(z.string().max(80)).min(1).max(12),
      rows: z.array(z.array(z.string().max(500)).max(12)).max(200),
    }),
    description: "A data table. Every row has the same number of cells as there are columns.",
  },
  Badge: {
    props: z.strictObject({ text: text(40), tone: tone.default("neutral") }),
    description: "A short status or tag.",
  },
  KeyValue: {
    props: z.strictObject({
      items: z.array(z.strictObject({ key: text(80), value: text(500) })).min(1).max(30),
    }),
    description: "Label and value rows, for facts about one thing.",
  },
  Stat: {
    props: z.strictObject({
      label: text(80),
      value: text(40),
      detail: text(120).optional(),
      tone: tone.default("neutral"),
    }),
    description:
      "One headline number with its label and an optional note (a change, a unit). Put several in a Grid; never inside a Card.",
  },
  Accordion: {
    props: z.strictObject({
      items: z.array(z.strictObject({ title: text(200), text: text(4000) })).min(1).max(30),
    }),
    description:
      "Collapsible entries whose body is Markdown. For detail a reader may skip: FAQs, notes per place, fine print.",
  },
  Code: {
    props: z.strictObject({ language: text(30).optional(), text: text(20000) }),
    description: "A block of code or terminal output, shown monospaced. No highlighting.",
  },
  Chart: {
    props: z.strictObject({
      kind: z.enum(["bar", "line"]),
      title: text(120).optional(),
      unit: text(20).optional(),
      caption: text(200),
      data: z.array(z.strictObject({ label: text(40), value: z.number().min(-1e15).max(1e15) })).min(2).max(60),
    }),
    description:
      "One measure, one axis: `bar` ranks or compares categories, `line` shows a change over an ordered `label` axis (days, months). `caption` states what the chart shows. Two measures of different scale are two charts, never one; a single number is a Stat, not a chart.",
  },
  Mermaid: {
    props: z.strictObject({
      source: z.string().min(1).max(4000).regex(MERMAID_PLAIN, "plain diagram source that starts with a diagram keyword"),
      caption: text(200),
    }),
    description:
      "A diagram as plain Mermaid source. Start the source with the diagram type (flowchart, graph, sequenceDiagram, stateDiagram-v2, classDiagram, erDiagram, gantt, timeline or mindmap; not journey). No frontmatter, no `%%{` directive, no `click`/`link` statements, no backslash, no function in a style line: the page themes it. Show a mechanism or a route, label every arrow, and let `caption` say what the picture shows.",
  },
  Map: {
    props: z.strictObject({
      caption: text(200),
      places: z
        .array(
          z.strictObject({
            name: text(80),
            // Written by Nora's service from the name (design/artifact §40), never by the model.
            lat: z.number().min(-90).max(90).optional(),
            lng: z.number().min(-180).max(180).optional(),
          }),
        )
        .min(1)
        .max(50),
      route: z
        .array(z.strictObject({ lat: z.number().min(-90).max(90), lng: z.number().min(-180).max(180) }))
        .min(2)
        .max(200)
        .optional(),
    }),
    description:
      "A map of named public places. Give each place's name, as a map would know it, and nothing else: leave out lat, lng and route, because the coordinates are looked up for you. A person's home or position cannot be mapped.",
  },
  Image: {
    props: z.strictObject({
      // A media code from an [image ...] marker in the room, or its full sha256.
      // Never a URL: a picture comes from custody, so a document cannot call a third party.
      media: z.string().regex(/^[0-9a-f]{6,64}(?![\s\S])/, "a media code or sha256"),
      alt: text(300),
      caption: text(200).optional(),
    }),
    description:
      "A picture that was shared in this room, made by Nora, or kept from a web page by media_fetch, named by its media code. Never a web address.",
  },
  Callout: {
    props: z.strictObject({ tone: tone.default("info"), title: text(120), text: text(1000).optional() }),
    description: "A highlighted note: a warning, a deadline, a thing not to miss.",
  },
  Timeline: {
    props: z.strictObject({
      items: z
        .array(z.strictObject({ label: text(60), title: text(200), text: text(1000).optional() }))
        .min(1)
        .max(60),
    }),
    description: "Ordered entries with a time or day label, such as a day-by-day itinerary.",
  },
  Sources: {
    props: z.strictObject({
      items: z
        .array(
          z.strictObject({
            title: text(200),
            url: link,
            asOf: z.string().regex(/^[0-9]{4}-[0-9]{2}-[0-9]{2}(?![\s\S])/, "YYYY-MM-DD").optional(),
            note: text(300).optional(),
          }),
        )
        .min(1)
        .max(50),
    }),
    description:
      "Where the facts came from: title, link, and the date they were read (asOf). Add one wherever the document states a price, time or rule that can change.",
  },
  Checklist: {
    props: z.strictObject({
      items: z.array(z.strictObject({ text: text(300), note: text(300).optional() })).min(1).max(50),
    }),
    description: "A static list of things to prepare. It cannot be ticked.",
  },
} as const;

export type ComponentName = keyof typeof components;
export const COMPONENT_NAMES = Object.keys(components) as ComponentName[];

// Only these take children. Everything else is a leaf, and a child on a leaf
// is a rejection rather than something silently dropped.
export const CONTAINERS: ReadonlySet<ComponentName> = new Set(["Page", "Section", "Card", "Grid"]);

// json-render's own catalog. Used for the React registry and the spec it
// renders. Its `jsonSchema()` and `prompt()` are NOT used for validation or for
// the skill: the schema leaves `props` as `additionalProperties: {}` (it checks
// the shell, never a prop), and the prompt teaches `repeat`, `$state` and
// actions, which a document forbids (measured 2026-09-30: 17,048 characters).
export const catalog = defineCatalog(schema, { components, actions: {} });

// ---------------------------------------------------------------- the document
// The stored form is a NESTED tree, not json-render's flat id map: a tree
// cannot have a dangling child id, a cycle or an orphan, which removes three
// checks, and it spends no tokens on ids. `toFlatSpec` converts at render time.
// The element keys are exactly `type`, `props` and `children`, so `on`, `watch`,
// `repeat`, `visible` and `state` (S0 finding 2) cannot be expressed at all.
export interface DocumentNode {
  type: ComponentName;
  props: Record<string, unknown>;
  children?: DocumentNode[];
}

export const MAX_CHILDREN = 200;
/** Page is depth 0; the deepest node is at this depth. */
export const MAX_DEPTH = 8;
export const MAX_NODES = 500;

const block = (name: ComponentName): z.ZodType =>
  CONTAINERS.has(name)
    ? z.strictObject({
        type: z.literal(name),
        props: components[name].props,
        children: z.array(node).max(MAX_CHILDREN).optional(),
      })
    : z.strictObject({ type: z.literal(name), props: components[name].props });

const node: z.ZodType<DocumentNode> = z.lazy(() =>
  z.union(
    COMPONENT_NAMES.filter((n) => n !== "Page").map(block) as unknown as [z.ZodType, z.ZodType, ...z.ZodType[]],
  ),
) as z.ZodType<DocumentNode>;

export const DOCUMENT_VERSION = 1;

export const documentSchema = z.strictObject({
  version: z.literal(DOCUMENT_VERSION),
  root: z.strictObject({
    type: z.literal("Page"),
    props: components.Page.props,
    children: z.array(node).max(MAX_CHILDREN),
  }),
});

export type DocumentSpec = z.infer<typeof documentSchema>;

export type ParseResult = { ok: true; doc: DocumentSpec } | { ok: false; reason: string };

/**
 * The one way to read a stored document. It never throws and never recurses on
 * untrusted depth: a document 1,200 levels deep used to make `safeParse` raise
 * a RangeError (review, 2026-09-30), so depth and size are measured first, with
 * an explicit stack, and only a tree within the caps reaches Zod. Zod then
 * applies the defaults, which the renderer relies on (a Badge without `tone`).
 */
export function parseDocument(input: unknown): ParseResult {
  try {
    const root = (input as { root?: unknown } | null)?.root;
    const stack: Array<{ node: unknown; depth: number }> = [{ node: root, depth: 0 }];
    let nodes = 0;
    while (stack.length) {
      const { node, depth } = stack.pop()!;
      if (typeof node !== "object" || node === null) continue; // Zod names the fault
      if (++nodes > MAX_NODES) return { ok: false, reason: `more than ${MAX_NODES} nodes` };
      if (depth > MAX_DEPTH) return { ok: false, reason: `nested deeper than ${MAX_DEPTH}` };
      const kids = (node as { children?: unknown }).children;
      if (Array.isArray(kids)) {
        if (kids.length > MAX_CHILDREN) return { ok: false, reason: `more than ${MAX_CHILDREN} children in one node` };
        for (const k of kids) stack.push({ node: k, depth: depth + 1 });
      }
    }
    const r = documentSchema.safeParse(input);
    if (r.success) return { ok: true, doc: r.data };
    const i = r.error.issues[0];
    return { ok: false, reason: `${i?.path.join(".") || "document"}: ${i?.message ?? "invalid"}` };
  } catch {
    return { ok: false, reason: "the document could not be read" };
  }
}
