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
const link = z.string().max(2048).regex(/^https?:\/\/[^\s]+(?![\s\S])/, "an http(s) URL");

// A Mermaid source is plain drawing instructions or it is refused. Mermaid 12
// takes configuration from frontmatter, `%%{init}%%`, `click`/`link`/`callback`
// statements and CSS (`style`, `classDef`, `linkStyle`), and it inserts the SVG
// into the live page to measure it, so a `url(https://…)` or an <a href> there
// is a request or a link before any check of the output can run (found by
// review and reproduced in e2e on 2026-09-30). The renderer applies this same
// pattern before it calls mermaid. Case-folded by hand, not with an `i` flag,
// because a JSON Schema `pattern` cannot carry flags and the Python checker
// reads that schema alone.
const ci = (word: string) => [...word].map((c) => `[${c.toLowerCase()}${c.toUpperCase()}]`).join("");
const LINE = "(?:^|\\n)[ \\t\\uFEFF]*";
export const MERMAID_PLAIN = new RegExp(
  "^" +
    `(?![\\s\\S]*${LINE}---)` +
    "(?![\\s\\S]*%%\\{)" +
    `(?![\\s\\S]*${LINE}(?:${["click", "link", "callback", "call", "style", "classDef", "linkStyle"].map(ci).join("|")})\\b)` +
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
  Tabs: {
    props: z.strictObject({}),
    description:
      "Switchable panels. Its children are Tab nodes and nothing else; use it to compare options or split one topic by audience, not to hide the main content.",
  },
  Tab: {
    props: z.strictObject({ label: text(60) }),
    description: "One panel of a Tabs: a label and the blocks inside it. Only valid directly inside Tabs.",
  },
  Chart: {
    props: z.strictObject({
      kind: z.enum(["bar", "line"]),
      title: text(120).optional(),
      unit: text(20).optional(),
      caption: text(200),
      data: z.array(z.strictObject({ label: text(40), value: z.number() })).min(2).max(60),
    }),
    description:
      "One measure, one axis: `bar` ranks or compares categories, `line` shows a change over an ordered `label` axis (days, months). `caption` states what the chart shows. Two measures of different scale are two charts, never one; a single number is a Stat, not a chart.",
  },
  Mermaid: {
    props: z.strictObject({
      source: z.string().min(1).max(4000).regex(MERMAID_PLAIN, "plain diagram source: no frontmatter, directive, click, style or url"),
      caption: text(200),
    }),
    description:
      "A diagram written as plain Mermaid source (flowchart, sequence, state). No frontmatter, `%%{` directive, `click`, `link`, `style`, `classDef` or `url(`: the page styles it. Show a mechanism or a route, label every arrow, and let `caption` say what the picture shows.",
  },
  Map: {
    props: z.strictObject({
      caption: text(200),
      places: z
        .array(
          z.strictObject({
            name: text(80),
            lat: z.number().min(-90).max(90),
            lng: z.number().min(-180).max(180),
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
      "A map of named places, and optionally the path between them. Coordinates only: look an address up first, then write its lat and lng.",
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
      "A picture that was shared in this room or made by Nora, named by its media code. Never a web address.",
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
export const CONTAINERS: ReadonlySet<ComponentName> = new Set(["Page", "Section", "Card", "Grid", "Tabs", "Tab"]);

// Where a container's children may come from. `Tab` exists only inside `Tabs`, and
// `Tabs` holds nothing but `Tab`; everything else takes the general blocks.
export const CHILDREN: Record<string, string> = { Tabs: "Tab nodes only", Tab: "blocks; only valid inside Tabs" };

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
/** Page is depth 0; the deepest node is at this depth. A Tab counts as a level. */
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

// `Tab` is a block of its own kind, allowed only as a child of `Tabs`.
const tab = z.strictObject({
  type: z.literal("Tab"),
  props: components.Tab.props,
  children: z.array(z.lazy(() => node)).max(MAX_CHILDREN),
});

const tabs = z.strictObject({
  type: z.literal("Tabs"),
  props: components.Tabs.props,
  children: z.array(tab).min(1).max(12),
});

const node: z.ZodType<DocumentNode> = z.lazy(() =>
  z.union(
    [
      tabs,
      ...COMPONENT_NAMES.filter((n) => n !== "Page" && n !== "Tab" && n !== "Tabs").map(block),
    ] as unknown as [z.ZodType, z.ZodType, ...z.ZodType[]],
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
