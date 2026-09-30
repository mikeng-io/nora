"use client";

// The four blocks that load something heavy or fetch something: Chart, Mermaid,
// Map, Image. Kept out of registry.tsx so each can be read on its own. Each is a
// <figure> with a caption that states one claim (artifact-diagramming), and each
// fails visibly rather than drawing nothing.
import dynamic from "next/dynamic";
import { createContext, useContext, useEffect, useId, useRef, useState } from "react";
import { BarList } from "@/components/charts/bars";
import { TimeChart } from "@/components/charts/time-chart";
import { cn } from "@/lib/cn";
import { MERMAID_PLAIN } from "@/lib/documents/catalog";

/** The stored document's id, so an Image can name its bytes. Absent for fixtures. */
export const DocumentIdContext = createContext<string | null>(null);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const FIG = "flex flex-col gap-2 rounded-panel border border-hairline bg-surface p-4";

// ------------------------------------------------------------------ Chart
export function ChartBlock(props: {
  kind: "bar" | "line";
  title?: string;
  unit?: string;
  caption: string;
  data: Array<{ label: string; value: number }>;
}) {
  const unit = props.unit ? ` ${props.unit}` : "";
  const fmt = (v: number) => `${Number.isInteger(v) ? v : v.toFixed(1)}${unit}`;
  return (
    <figure className={FIG}>
      {props.title ? <p className="text-title font-semibold text-ink">{props.title}</p> : null}
      {props.kind === "bar" ? (
        <BarList
          label={props.caption}
          data={props.data.map((d, i) => ({ key: `${i}`, label: d.label, value: d.value, display: fmt(d.value) }))}
        />
      ) : (
        <TimeChart
          label={props.caption}
          points={props.data.map((d, i) => ({ t: i, v: d.value }))}
          formatValue={fmt}
          formatTime={(t) => props.data[Math.round(t)]?.label ?? ""}
          formatTick={(t) => props.data[Math.round(t)]?.label ?? ""}
        />
      )}
      <figcaption className="text-meta text-ink-muted">{props.caption}</figcaption>
      <details className="text-meta text-ink-soft">
        <summary className="cursor-pointer text-ink-muted">Show the data</summary>
        <table className="mt-2 w-full border-collapse text-left">
          <thead>
            <tr className="border-b border-hairline">
              <th scope="col" className="py-1 pr-4 font-semibold">Label</th>
              <th scope="col" className="py-1 font-semibold">Value</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-hairline">
            {props.data.map((d, i) => (
              <tr key={i}>
                <td className="py-1 pr-4">{d.label}</td>
                <td className="py-1 tabular-nums">{fmt(d.value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

// ---------------------------------------------------------------- Mermaid
const cssVar = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function useThemeName() {
  const [name, setName] = useState("light");
  useEffect(() => {
    const read = () => setName(document.documentElement.dataset.theme ?? "light");
    read();
    const mo = new MutationObserver(read);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => mo.disconnect();
  }, []);
  return name;
}

// What the renderer refuses to put on the page, whatever the source did to
// mermaid's configuration. Mermaid 12 reads config from frontmatter and `click`
// statements even at its strict level: a source can turn HTML labels back on,
// put `url(https://…)` into the SVG's own <style>, or add an <a href> to any
// site (found by review, reproduced in e2e). The schema refuses those sources,
// and this is the second wall: nothing that can load, link or run may survive.
const FORBIDDEN_ELEMENTS = "script, foreignObject, image, a, iframe, object, embed, video, audio, link, meta";

/** The SVG if it is passive and self-contained, else null. */
export function passiveSvg(svg: string): string | null {
  // `url(#id)` is how a marker or gradient refers to itself; any other url(),
  // and any @import, is a request.
  if (/url\(\s*(?!["']?#)/i.test(svg) || /@import/i.test(svg) || /javascript:/i.test(svg)) return null;
  const doc = new DOMParser().parseFromString(svg, "image/svg+xml");
  if (doc.querySelector("parsererror")) return null;
  if (doc.querySelector(FORBIDDEN_ELEMENTS)) return null;
  for (const el of Array.from(doc.querySelectorAll("*"))) {
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      if (name.startsWith("on")) return null;
      if ((name === "href" || name === "xlink:href") && !attr.value.startsWith("#")) return null;
    }
  }
  return svg;
}

export function MermaidBlock({ source, caption }: { source: string; caption: string }) {
  const id = "mmd" + useId().replace(/[^a-zA-Z0-9]/g, "");
  const theme = useThemeName();
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    setFailed(false);
    (async () => {
      // Before mermaid sees it: it draws into the live page, so a request made
      // while measuring happens before any check of its output.
      if (!MERMAID_PLAIN.test(source)) throw new Error("the source is not plain");
      const mermaid = (await import("mermaid")).default;
      // Strict: no click handlers, no HTML in labels, and an init directive in
      // the source cannot lift it (it is also refused by the schema).
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        // v12 moved this to the top level; the flowchart-level key no longer applies.
        htmlLabels: false,
        flowchart: { htmlLabels: false },
        fontFamily: "Inter, 'Noto Sans HK', system-ui, sans-serif",
        themeVariables: {
          background: cssVar("--surface"),
          primaryColor: cssVar("--hoodie"),
          primaryTextColor: cssVar("--ink"),
          primaryBorderColor: cssVar("--seam"),
          lineColor: cssVar("--ink-soft"),
          edgeLabelBackground: cssVar("--surface"),
          secondaryColor: cssVar("--surface"),
          tertiaryColor: cssVar("--surface"),
          noteBkgColor: cssVar("--hoodie"),
          noteTextColor: cssVar("--ink"),
        },
      });
      const out = await mermaid.render(id, source);
      const safe = passiveSvg(out.svg);
      if (!safe) throw new Error("the diagram is not passive");
      if (live) setSvg(safe);
    })().catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [id, source, theme]);

  return (
    <figure className={FIG}>
      {failed ? (
        <div role="note" className="flex flex-col gap-2">
          <p className="text-meta text-danger">This diagram could not be drawn. Its source:</p>
          <pre className="overflow-x-auto font-mono text-meta text-ink" role="region" aria-label="Diagram source" tabIndex={0}>
            {source}
          </pre>
        </div>
      ) : svg ? (
        <div
          role="img"
          aria-label={caption}
          tabIndex={0}
          className="overflow-x-auto focus-visible:outline-2 focus-visible:outline-denim [&_svg]:mx-auto [&_svg]:h-auto [&_svg]:max-w-full"
          // Mermaid's own output at its strict level, with HTML labels off.
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      ) : (
        <div className="h-32 animate-fade rounded-control bg-hoodie" aria-hidden />
      )}
      <figcaption className="text-meta text-ink-muted">{caption}</figcaption>
    </figure>
  );
}

// -------------------------------------------------------------------- Map
const NoraMap = dynamic(() => import("@/components/map/nora-map").then((m) => m.NoraMap), {
  ssr: false,
  loading: () => <div className="h-full animate-fade bg-hoodie" aria-hidden />,
});

export function MapBlock(props: {
  caption: string;
  places: Array<{ name: string; lat: number; lng: number }>;
  route?: Array<{ lat: number; lng: number }>;
}) {
  const coords: Array<[number, number]> = [
    ...props.places.map((p) => [p.lng, p.lat] as [number, number]),
    ...(props.route ?? []).map((p) => [p.lng, p.lat] as [number, number]),
  ];
  return (
    <figure className={FIG}>
      <div className="h-80 overflow-hidden rounded-control border border-hairline">
        <NoraMap
          className="h-full w-full"
          ariaLabel={props.caption}
          places={props.places}
          lines={props.route ? [{ id: "route", personId: "document", coords: props.route.map((p) => [p.lng, p.lat]) }] : []}
          fitTo={coords}
          frameKey={JSON.stringify(coords)}
          padding={{ top: 32, bottom: 32, left: 32, right: 32 }}
        />
      </div>
      <figcaption className="text-meta text-ink-muted">{props.caption}</figcaption>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-meta text-ink-soft">
        {props.places.map((p, i) => (
          <li key={i}>{p.name}</li>
        ))}
      </ul>
    </figure>
  );
}

// ------------------------------------------------------------------ Image
export function ImageBlock({ media, alt, caption }: { media: string; alt: string; caption?: string }) {
  const documentId = useContext(DocumentIdContext);
  const [broken, setBroken] = useState(false);
  // The id goes into a path, so only a UUID is used (a `../` or a `?` in it
  // would point the request elsewhere on this origin), and it is encoded anyway.
  const usable = documentId !== null && UUID.test(documentId);
  const showPlaceholder = !usable || broken;
  // A picture that failed before React attached `onError` (server-rendered, then
  // hydrated) never reports it, and stays a broken-image icon. Found by e2e:
  // the 404 came back and nothing changed. So look once after mount as well.
  const img = useRef<HTMLImageElement>(null);
  useEffect(() => {
    const el = img.current;
    if (el && el.complete && el.naturalWidth === 0) setBroken(true);
  }, [usable]);
  return (
    <figure className="flex flex-col gap-2">
      {showPlaceholder ? (
        <div
          role="img"
          aria-label={alt}
          className={cn("flex min-h-32 items-center justify-center rounded-panel border border-hairline bg-hoodie px-4 text-center text-meta text-ink-muted")}
        >
          {broken ? "This picture is no longer available." : alt}
        </div>
      ) : (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          ref={img}
          src={`/api/v1/documents/${encodeURIComponent(documentId as string)}/media/${encodeURIComponent(media)}`}
          alt={alt}
          loading="lazy"
          decoding="async"
          onError={() => setBroken(true)}
          className="max-w-full rounded-panel border border-hairline"
        />
      )}
      {caption ? <figcaption className="text-meta text-ink-muted">{caption}</figcaption> : null}
    </figure>
  );
}
