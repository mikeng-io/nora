"use client";

// Renders a document (src/lib/documents/catalog.ts) on the portal's own
// primitives. Not the shadcn package's components: DESIGN.md's Hairline-First
// rule (a panel is one hairline, never a card inside a card) is not what
// shadcn's Card is, and S0 found its layout defaults and Alert colours broken
// on this theme (design/artifact/00-design.md §13). json-render supplies the
// catalog, the registry and the renderer; the look is the portal's.
import { createContext, useContext, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { defineRegistry, JSONUIProvider, Renderer } from "@json-render/react";
import { nestedToFlat } from "@json-render/core";
import { ChevronDown } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { ChecklistBlock } from "./checklist";
import { Md } from "./markdown";
import { ContactBlock } from "./contact";
import { CountdownBlock } from "./countdown";
import { ProgressBlock } from "./progress";
import { ScheduleBlock } from "./schedule";
import { SeatingBlock } from "./seating";
import { TimelineBlock } from "./timeline";
import { ChartBlock, DocumentIdContext, ImageBlock, MapBlock, MermaidBlock } from "./blocks";
import { cn } from "@/lib/cn";
import { catalog, parseDocument, type DocumentSpec, type components } from "@/lib/documents/catalog";
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

/** What the page that shows a document puts right under its title (who made it, the
 * archive and delete controls): the title is the first thing on the page, so these
 * cannot sit above it. Empty for a fixture. */
const DocumentChromeContext = createContext<ReactNode>(null);

function PageBlock({ props, children }: Render<"Page">) {
  const chrome = useContext(DocumentChromeContext);
  return (
    <article className="flex min-w-0 flex-col gap-8">
      <div className="flex flex-col gap-4">
        {/* `data-document-cover`: the Page's own title block, which the present view shows
            on the cover slide only (design/artifact §34). */}
        <header className="flex flex-col gap-2" data-document-cover>
          <h1 className="text-display font-semibold text-ink">{props.title}</h1>
          {props.subtitle ? <p className="text-lead text-ink-soft">{props.subtitle}</p> : null}
        </header>
        {chrome}
      </div>
      {children}
    </article>
  );
}

const ALIGN = { left: "text-left", right: "text-right tabular-nums", center: "text-center" } as const;

function TableBlock({ props }: Render<"Table">) {
  const t = useTranslations("documents.renderer");
  const align = (j: number) => ALIGN[props.align?.[j] ?? "left"];
  // The first column stays put while a wide table scrolls sideways, and the header row
  // while a long one scrolls down. Borders sit on the cells (border-separate), because a
  // collapsed border does not travel with a sticky cell.
  const first = "sticky left-0 z-10 bg-surface";
  return (
    // A wide table scrolls on a phone, so its scroll region takes focus (axe:
    // scrollable-region-focusable) and has a name.
    <div
      role="region"
      aria-label={props.caption ?? t("tableFallback")}
      tabIndex={0}
      className="max-h-[70vh] overflow-auto rounded-panel border border-hairline bg-surface focus-visible:outline-2 focus-visible:outline-denim"
    >
      <table className="w-full border-separate border-spacing-0 text-left text-body">
        {props.caption ? <caption className="px-4 py-2 text-left text-meta text-ink-muted">{props.caption}</caption> : null}
        <thead>
          <tr>
            {props.columns.map((c, i) => (
              <th
                key={i}
                scope="col"
                className={cn(
                  "sticky top-0 border-b border-hairline bg-surface px-4 py-2 text-label font-semibold text-ink-soft",
                  align(i),
                  i === 0 ? "left-0 z-30" : "z-20",
                )}
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="[&_tr:last-child_td]:border-b-0">
          {props.rows.map((r, i) => (
            <tr key={i}>
              {props.columns.map((_, j) => (
                <td key={j} className={cn("border-b border-hairline px-4 py-2 align-top text-ink", align(j), j === 0 && first, props.align?.[j] === "right" && "whitespace-nowrap")}>
                  {r[j] ?? ""}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {props.footer ? (
          <tfoot>
            <tr>
              {props.columns.map((_, j) => (
                <td key={j} className={cn("border-t border-hairline px-4 py-2 align-top font-semibold text-ink", align(j), j === 0 && first, props.align?.[j] === "right" && "whitespace-nowrap")}>
                  {props.footer?.[j] ?? ""}
                </td>
              ))}
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}

function CodeBlock({ props }: Render<"Code">) {
  const t = useTranslations("documents.renderer");
  return (
    <figure className="overflow-hidden rounded-panel border border-hairline bg-surface">
      {props.language ? (
        <figcaption className="border-b border-hairline px-4 py-1.5 font-mono text-label text-ink-muted">{props.language}</figcaption>
      ) : null}
      <pre
        role="region"
        aria-label={props.language ? t("codeLabel", { language: props.language }) : t("codeFallback")}
        tabIndex={0}
        className="overflow-x-auto px-4 py-3 font-mono text-meta text-ink focus-visible:outline-2 focus-visible:outline-denim"
      >
        <code>{props.text}</code>
      </pre>
    </figure>
  );
}

function SourcesBlock({ props }: Render<"Sources">) {
  const t = useTranslations("documents.renderer");
  return (
    <ol className="flex list-decimal flex-col gap-2 pl-5 text-body text-ink marker:text-ink-muted">
      {props.items.map((it, i) => (
        <li key={i}>
          <a href={it.url} target="_blank" rel="noopener noreferrer nofollow" className="text-denim underline underline-offset-2">
            {it.title}
          </a>
          {it.asOf ? <span className="text-meta text-ink-muted">{` · ${t("readOn", { date: it.asOf })}`}</span> : null}
          {it.note ? <span className="text-meta text-ink-soft">{` · ${it.note}`}</span> : null}
        </li>
      ))}
    </ol>
  );
}

const { registry } = defineRegistry(catalog, {
  components: {
    Page: PageBlock,
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
    Table: TableBlock,
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
    Code: CodeBlock,
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
    Timeline: TimelineBlock,
    Sources: SourcesBlock,
    Checklist: ChecklistBlock,
    Progress: ProgressBlock,
    Countdown: CountdownBlock,
    Contact: ContactBlock,
    Seating: SeatingBlock,
    Schedule: ScheduleBlock,
  } as never,
});

/** The stored nested tree, as json-render's flat spec. Already checked. */
export function toFlatSpec(doc: DocumentSpec) {
  return nestedToFlat(doc.root as unknown as Record<string, unknown>);
}

/** Renders a stored document. It validates first, every time: the renderer is
 * the last line of defence, so it never draws what the schema would refuse, and
 * it is here that the defaults (a Badge's tone) are applied.
 *
 * `chrome` is drawn right under the document's title (the page's meta and controls), and
 * after the refusal when the stored spec cannot be shown, so they stay reachable then. */
export function DocumentView({
  doc,
  documentId = null,
  chrome = null,
}: {
  doc: unknown;
  documentId?: string | null;
  chrome?: ReactNode;
}) {
  const t = useTranslations("documents.renderer");
  const parsed = parseDocument(doc);
  if (!parsed.ok) {
    return (
      <div className="flex flex-col gap-4">
        <div role="alert" className="rounded-panel border border-hairline border-l-4 border-l-danger bg-surface px-4 py-3">
          <p className="text-title font-semibold text-ink">{t("cannotShow")}</p>
        </div>
        {chrome}
      </div>
    );
  }
  return (
    <DocumentIdContext.Provider value={documentId}>
      <DocumentChromeContext.Provider value={chrome}>
        <JSONUIProvider registry={registry}>
          <Renderer spec={toFlatSpec(parsed.doc)} registry={registry} />
        </JSONUIProvider>
      </DocumentChromeContext.Provider>
    </DocumentIdContext.Provider>
  );
}
