"use client";

// Renders a document (src/lib/documents/catalog.ts) on the portal's own
// primitives. Not the shadcn package's components: DESIGN.md's Hairline-First
// rule (a panel is one hairline, never a card inside a card) is not what
// shadcn's Card is, and S0 found its layout defaults and Alert colours broken
// on this theme (design/artifact/00-design.md §13). json-render supplies the
// catalog, the registry and the renderer; the look is the portal's.
import { Children, useState, type ReactNode } from "react";
import { defineRegistry, JSONUIProvider, Renderer } from "@json-render/react";
import { nestedToFlat } from "@json-render/core";
import ReactMarkdown from "react-markdown";
import { ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { ChartBlock, DocumentIdContext, ImageBlock, MapBlock, MermaidBlock } from "./blocks";
import { cn } from "@/lib/cn";
import { catalog, type DocumentSpec, type components } from "@/lib/documents/catalog";
import type { z } from "zod";

type Props<N extends keyof typeof components> = z.infer<(typeof components)[N]["props"]>;
type Render<N extends keyof typeof components> = { props: Props<N>; children?: ReactNode };

const TONE = { neutral: "neutral", info: "denim", success: "success", warning: "caution", danger: "danger" } as const;
const STAT_TONE = { neutral: "text-ink-muted", info: "text-denim", success: "text-success", warning: "text-caution", danger: "text-danger" } as const;
const BAR = {
  neutral: "border-l-seam",
  info: "border-l-denim",
  success: "border-l-success",
  warning: "border-l-caution",
  danger: "border-l-danger",
} as const;
const COLS: Record<number, string> = {
  1: "grid-cols-1",
  2: "grid-cols-1 sm:grid-cols-2",
  3: "grid-cols-1 sm:grid-cols-3",
  4: "grid-cols-2 sm:grid-cols-4",
};

// Markdown for a document: raw HTML is never parsed (react-markdown's default),
// images are refused (a picture is an Image, through custody), links open
// outside and carry no referrer. Anything but http(s) and mailto is dropped.
const SAFE_LINK = /^(https?:|mailto:)/i;

function Md({ text }: { text: string }) {
  return (
    <div className="flex flex-col gap-3 text-body text-ink [&_code]:rounded-control [&_code]:bg-hoodie [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-meta [&_ol]:list-decimal [&_ol]:pl-5 [&_ul]:list-disc [&_ul]:pl-5 [&_li]:my-0.5 [&_h1]:text-heading [&_h2]:text-lead [&_h3]:text-title [&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-semibold [&_blockquote]:border-l-2 [&_blockquote]:border-hairline [&_blockquote]:pl-3 [&_blockquote]:text-ink-soft">
      <ReactMarkdown
        disallowedElements={["img", "iframe", "script", "style"]}
        unwrapDisallowed
        urlTransform={(url) => (SAFE_LINK.test(url) ? url : "")}
        components={{
          a: ({ href, children }) =>
            href ? (
              <a href={href} target="_blank" rel="noopener noreferrer nofollow" className="text-denim underline underline-offset-2">
                {children}
              </a>
            ) : (
              <span>{children}</span>
            ),
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}

const { registry } = defineRegistry(catalog, {
  components: {
    Page: ({ props, children }: Render<"Page">) => (
      <article className="flex min-w-0 flex-col gap-8">
        <header className="flex flex-col gap-2">
          <h1 className="text-display font-semibold text-ink">{props.title}</h1>
          {props.subtitle ? <p className="text-lead text-ink-soft">{props.subtitle}</p> : null}
        </header>
        {children}
      </article>
    ),
    Section: ({ props, children }: Render<"Section">) => (
      <section className="flex min-w-0 flex-col gap-4" data-slide>
        {props.title ? (
          <h2 className="flex items-baseline gap-3 text-heading font-semibold text-ink">
            {props.number ? <span className="font-mono text-meta text-ink-muted">{String(props.number).padStart(2, "0")}</span> : null}
            {props.title}
          </h2>
        ) : null}
        {children}
      </section>
    ),
    Card: ({ props, children }: Render<"Card">) => (
      <Panel>
        {props.title ? <PanelHeader title={props.title} description={props.description} /> : null}
        {children ? <PanelBody className={cn("flex flex-col gap-3", props.title ? undefined : "pt-4 sm:pt-5")}>{children}</PanelBody> : null}
      </Panel>
    ),
    Grid: ({ props, children }: Render<"Grid">) => (
      <div className={cn("grid gap-4", COLS[props.columns] ?? COLS[2])}>{children}</div>
    ),
    Markdown: ({ props }: Render<"Markdown">) => <Md text={props.text} />,
    Table: ({ props }: Render<"Table">) => (
      // A wide table scrolls sideways on a phone, so its scroll region takes focus (axe:
      // scrollable-region-focusable) and has a name.
      <div
        role="region"
        aria-label={props.caption ?? "Table"}
        tabIndex={0}
        className="overflow-x-auto rounded-panel border border-hairline bg-surface focus-visible:outline-2 focus-visible:outline-denim"
      >
        <table className="w-full min-w-max border-collapse text-left text-body">
          {props.caption ? <caption className="px-4 py-2 text-left text-meta text-ink-muted">{props.caption}</caption> : null}
          <thead>
            <tr className="border-b border-hairline">
              {props.columns.map((c, i) => (
                <th key={i} scope="col" className="px-4 py-2 text-label font-semibold text-ink-soft">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {props.rows.map((r, i) => (
              <tr key={i}>
                {props.columns.map((_, j) => (
                  <td key={j} className="px-4 py-2 align-top text-ink">
                    {r[j] ?? ""}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    ),
    // A flex row, so a Badge inside a column keeps its own width instead of stretching.
    Badge: ({ props }: Render<"Badge">) => (
      <div className="flex">
        <Badge tone={TONE[props.tone]}>{props.text}</Badge>
      </div>
    ),
    KeyValue: ({ props }: Render<"KeyValue">) => (
      <dl className="grid grid-cols-[minmax(0,10rem)_1fr] gap-x-4 gap-y-2 text-body">
        {props.items.map((it, i) => (
          <div key={i} className="contents">
            <dt className="text-ink-muted">{it.key}</dt>
            <dd className="text-ink">{it.value}</dd>
          </div>
        ))}
      </dl>
    ),
    Stat: ({ props }: Render<"Stat">) => (
      <Panel className="flex flex-col gap-1 px-4 py-3 sm:px-5 sm:py-4">
        <p className="text-label text-ink-muted">{props.label}</p>
        <p className="text-heading font-semibold text-ink">{props.value}</p>
        {props.detail ? <p className={cn("text-meta", STAT_TONE[props.tone])}>{props.detail}</p> : null}
      </Panel>
    ),
    Accordion: ({ props }: Render<"Accordion">) => (
      <div className="flex flex-col divide-y divide-hairline rounded-panel border border-hairline bg-surface">
        {props.items.map((it, i) => (
          <details key={i} className="group px-4 py-3">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 text-title font-semibold text-ink [&::-webkit-details-marker]:hidden">
              {it.title}
              <ChevronDown aria-hidden className="size-4 shrink-0 text-ink-muted transition-transform duration-[160ms] ease-nora group-open:rotate-180 motion-reduce:transition-none" />
            </summary>
            <div className="pt-3">
              <Md text={it.text} />
            </div>
          </details>
        ))}
      </div>
    ),
    Code: ({ props }: Render<"Code">) => (
      <figure className="overflow-hidden rounded-panel border border-hairline bg-surface">
        {props.language ? (
          <figcaption className="border-b border-hairline px-4 py-1.5 font-mono text-label text-ink-muted">{props.language}</figcaption>
        ) : null}
        <pre
          role="region"
          aria-label={props.language ? `${props.language} code` : "Code"}
          tabIndex={0}
          className="overflow-x-auto px-4 py-3 font-mono text-meta text-ink focus-visible:outline-2 focus-visible:outline-denim"
        >
          <code>{props.text}</code>
        </pre>
      </figure>
    ),
    // The labels are derived from the Tab children in toFlatSpec, so the stored
    // form has one source for them (the Tab), never a second list to keep equal.
    Tabs: function Tabs({ props, children }: { props: { labels?: string[] }; children?: ReactNode }) {
      const [active, setActive] = useState(0);
      const panels = Children.toArray(children);
      const labels = props.labels ?? [];
      return (
        <div className="flex flex-col gap-4">
          <div role="tablist" className="inline-flex max-w-full self-start overflow-x-auto rounded-control bg-hoodie p-1">
            {labels.map((label, i) => (
              <button
                key={i}
                role="tab"
                type="button"
                aria-selected={i === active}
                onClick={() => setActive(i)}
                className={cn(
                  "shrink-0 rounded-control px-3 py-1 text-meta font-medium transition-colors duration-100",
                  i === active ? "bg-surface text-ink" : "text-ink-muted",
                )}
              >
                {label}
              </button>
            ))}
          </div>
          {panels.map((panel, i) => (
            <div key={i} role="tabpanel" hidden={i !== active}>
              {panel}
            </div>
          ))}
        </div>
      );
    },
    Tab: ({ children }: { children?: ReactNode }) => <div className="flex flex-col gap-4">{children}</div>,
    Chart: ({ props }: Render<"Chart">) => <ChartBlock {...props} />,
    Mermaid: ({ props }: Render<"Mermaid">) => <MermaidBlock {...props} />,
    Map: ({ props }: Render<"Map">) => <MapBlock {...props} />,
    Image: ({ props }: Render<"Image">) => <ImageBlock {...props} />,
    Callout: ({ props }: Render<"Callout">) => (
      <div role="note" className={cn("rounded-panel border border-l-4 border-hairline bg-surface px-4 py-3", BAR[props.tone])}>
        <p className="text-title font-semibold text-ink">{props.title}</p>
        {props.text ? <p className="mt-0.5 text-body text-ink-soft">{props.text}</p> : null}
      </div>
    ),
    Timeline: ({ props }: Render<"Timeline">) => (
      <ol className="flex flex-col divide-y divide-hairline rounded-panel border border-hairline bg-surface">
        {props.items.map((it, i) => (
          <li key={i} className="grid grid-cols-1 gap-1 px-4 py-3 sm:grid-cols-[8rem_1fr] sm:gap-4">
            <span className="font-mono text-meta text-ink-muted">{it.label}</span>
            <div>
              <p className="text-title font-semibold text-ink">{it.title}</p>
              {it.text ? <p className="mt-0.5 text-body text-ink-soft">{it.text}</p> : null}
            </div>
          </li>
        ))}
      </ol>
    ),
    Sources: ({ props }: Render<"Sources">) => (
      <ol className="flex list-decimal flex-col gap-2 pl-5 text-body text-ink marker:text-ink-muted">
        {props.items.map((it, i) => (
          <li key={i}>
            <a href={it.url} target="_blank" rel="noopener noreferrer nofollow" className="text-denim underline underline-offset-2">
              {it.title}
            </a>
            {it.asOf ? <span className="text-meta text-ink-muted">{` · read ${it.asOf}`}</span> : null}
            {it.note ? <span className="text-meta text-ink-soft">{` · ${it.note}`}</span> : null}
          </li>
        ))}
      </ol>
    ),
    Checklist: ({ props }: Render<"Checklist">) => (
      <ul className="flex flex-col gap-2 text-body text-ink">
        {props.items.map((it, i) => (
          <li key={i} className="flex gap-3">
            <span aria-hidden className="mt-1 size-4 shrink-0 rounded-control border border-seam" />
            <span>
              {it.text}
              {it.note ? <span className="block text-meta text-ink-muted">{it.note}</span> : null}
            </span>
          </li>
        ))}
      </ul>
    ),
  } as never,
});

/** The stored nested tree, as json-render's flat spec. Already checked. */
export function toFlatSpec(doc: DocumentSpec) {
  const spec = nestedToFlat(doc.root as unknown as Record<string, unknown>);
  for (const el of Object.values(spec.elements)) {
    if (el.type !== "Tabs") continue;
    const labels = (el.children ?? []).map((k) => String((spec.elements[k]?.props as { label?: string })?.label ?? ""));
    el.props = { ...el.props, labels };
  }
  return spec;
}

export function DocumentView({ doc, documentId = null }: { doc: DocumentSpec; documentId?: string | null }) {
  return (
    <DocumentIdContext.Provider value={documentId}>
      <JSONUIProvider registry={registry}>
        <Renderer spec={toFlatSpec(doc)} registry={registry} />
      </JSONUIProvider>
    </DocumentIdContext.Provider>
  );
}
