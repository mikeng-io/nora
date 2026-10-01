"use client";

import { useCallback, useEffect, useRef, useState, type TouchEvent } from "react";
import { createPortal } from "react-dom";
import { useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useRouter } from "@/i18n/routing";
import { Button } from "@/components/ui/button";
import { PageFrame } from "@/components/shell/app-shell";
import { useRegisterTitle } from "@/components/shell/page-title";
import { DocumentView } from "@/components/documents/registry";
import { rememberPresentReturn } from "@/components/documents/document-actions";
import { parseDocument, type DocumentSpec } from "@/lib/documents/catalog";
import {
  planSlides,
  slideFromQuery,
  slideKey,
  slideSpec,
  swipeStep,
  targetOwnsKey,
  type Slide,
} from "@/lib/documents/present";
import { useDocument } from "@/lib/queries";
import { cn } from "@/lib/cn";
import { DocumentPageView } from "./document-view";

// `/documents/<id>?view=present` (design/artifact §15, §34): the stored document, one
// top-level Section per slide, over the whole window. The same spec and the same renderer
// as the page; only the split is new (src/lib/documents/present.ts).
//
// - A modal in effect, not only in name: the deck is portalled to <body>, everything else
//   is `inert` while it is open, Tab cycles inside it, and focus returns to the Present
//   button when it closes.
// - Arrows, space, Page Up/Down, Home and End move, unless the focused element owns them
//   (a table's scroll region, a code block, a diagram, a map, anything that scrolls).
//   Escape always leaves. A swipe moves on a phone.
// - `?slide=` follows the slide (replaced, never pushed), so a reload keeps it. Leaving
//   goes BACK to the page the deck was opened from, or replaces this entry with the page.
// - `<id>` may be a link's 12-hex handle: everything handed down uses the document's UUID.
export function PresentView({ id, slide, fromPage }: { id: string; slide?: string; fromPage: boolean }) {
  const t = useTranslations("documentViews");
  const doc = useDocument(id);
  if (!doc.data) return <DocumentPageView id={id} />;
  const parsed = parseDocument(doc.data.document);
  const plan = parsed.ok ? planSlides(parsed.doc) : null;
  if (!parsed.ok || !plan?.ok) {
    return (
      <>
        <PageFrame width="narrow" className="pb-0 lg:pb-0">
          <p
            role="status"
            data-testid="present-fallback"
            className="rounded-panel border border-hairline border-l-4 border-l-denim bg-surface px-4 py-3 text-meta text-ink-soft"
          >
            {t("presentFallback")}
          </p>
        </PageFrame>
        <DocumentPageView id={id} />
      </>
    );
  }
  return (
    <Deck
      routeId={id}
      documentId={doc.data.id}
      title={doc.data.title}
      doc={parsed.doc}
      slides={plan.slides}
      initial={slideFromQuery(slide, plan.slides.length)}
      fromPage={fromPage}
    />
  );
}

const FOCUSABLE =
  "a[href], button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex='-1'])";

const scrolls = (el: unknown): boolean => {
  const e = el as HTMLElement;
  if (!(e instanceof HTMLElement)) return false;
  const style = getComputedStyle(e);
  const y = /(auto|scroll)/.test(style.overflowY) && e.scrollHeight > e.clientHeight;
  const x = /(auto|scroll)/.test(style.overflowX) && e.scrollWidth > e.clientWidth;
  return x || y;
};

function Deck({
  routeId,
  documentId,
  title,
  doc,
  slides,
  initial,
  fromPage,
}: {
  routeId: string;
  documentId: string;
  title: string;
  doc: DocumentSpec;
  slides: Slide[];
  initial: number;
  fromPage: boolean;
}) {
  const t = useTranslations("documentViews");
  const router = useRouter();
  const [index, setIndex] = useState(initial);
  const [host, setHost] = useState<HTMLElement | null>(null);
  const frame = useRef<HTMLDivElement>(null);
  const touch = useRef<{ x: number; y: number } | null>(null);
  useRegisterTitle(title);
  const count = slides.length;
  // A refetch can bring fewer slides than the one on screen: read the index clamped.
  const at = Math.min(index, count - 1);
  const current = slides[at];

  const leave = useCallback(() => {
    rememberPresentReturn(documentId);
    if (fromPage && window.history.length > 1) router.back();
    else router.replace(`/documents/${routeId}`);
  }, [router, routeId, documentId, fromPage]);

  // The deck's own node under <body>, and everything else inert while it is open.
  useEffect(() => {
    const node = document.createElement("div");
    node.setAttribute("data-present-host", "");
    document.body.appendChild(node);
    const quieted: HTMLElement[] = [];
    for (const child of Array.from(document.body.children)) {
      if (child === node || !(child instanceof HTMLElement) || child.inert) continue;
      child.inert = true;
      quieted.push(child);
    }
    setHost(node);
    return () => {
      for (const child of quieted) child.inert = false;
      node.remove();
    };
  }, []);

  useEffect(() => {
    if (host) frame.current?.focus();
  }, [host]);

  // `?slide=` follows the slide, replacing this entry so Back is never a slide step.
  useEffect(() => {
    const url = new URL(window.location.href);
    if (url.searchParams.get("slide") === String(at + 1)) return;
    url.searchParams.set("slide", String(at + 1));
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
  }, [at]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const root = frame.current;
      if (!root) return;
      if (e.key === "Tab") {
        // The focus trap: Tab and Shift+Tab cycle inside the deck.
        const items = Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE));
        if (!items.length) return;
        const first = items[0];
        const last = items[items.length - 1];
        const active = document.activeElement;
        if (e.shiftKey && (active === first || !root.contains(active))) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && (active === last || !root.contains(active))) {
          e.preventDefault();
          first.focus();
        }
        return;
      }
      const target = e.target instanceof Element ? e.target : null;
      if (targetOwnsKey(e.key, target as never, scrolls)) return;
      const action = slideKey(e, at, count);
      if (!action) return;
      e.preventDefault();
      if (action.type === "exit") leave();
      else setIndex(action.index);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [at, count, leave]);

  const onTouchStart = (e: TouchEvent) => {
    const p = e.touches[0];
    touch.current = p ? { x: p.clientX, y: p.clientY } : null;
  };
  const onTouchEnd = (e: TouchEvent) => {
    const start = touch.current;
    const p = e.changedTouches[0];
    touch.current = null;
    if (!start || !p) return;
    const step = swipeStep(p.clientX - start.x, p.clientY - start.y);
    if (step) setIndex(Math.min(Math.max(at + step, 0), count - 1));
  };

  if (!host) return null;
  return createPortal(
    <div
      ref={frame}
      role="dialog"
      aria-modal="true"
      aria-label={t("presentLabel", { title })}
      tabIndex={-1}
      data-testid="present-view"
      data-present-deck
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      className="fixed inset-0 z-[60] flex flex-col bg-studio text-ink outline-none"
    >
      <div className="safe-top flex items-center gap-3 border-b border-hairline bg-surface px-4 py-2">
        <p className="min-w-0 flex-1 truncate text-meta font-semibold text-ink">{title}</p>
        <p className="font-mono text-meta text-ink-muted" aria-live="polite" data-testid="slide-counter">
          {t("slideCounter", { n: at + 1, total: count })}
        </p>
        <Button variant="outline" size="sm" onClick={leave}>
          <X aria-hidden="true" />
          {t("exitPresent")}
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto" data-present-scroller>
        <div
          key={current.index}
          className={cn(
            "page-enter mx-auto flex min-h-full w-full max-w-[1080px] flex-col justify-center px-6 py-8 sm:px-10 lg:px-16",
            // The Page's title block is the cover; a Section slide shows the Section alone.
            current.kind === "section" && "[&_[data-document-cover]]:hidden",
          )}
          data-testid="slide"
          data-slide-kind={current.kind}
        >
          <DocumentView doc={slideSpec(doc, current)} documentId={documentId} />
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 border-t border-hairline bg-surface px-4 py-2 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        <Button variant="quiet" size="sm" disabled={at === 0} onClick={() => setIndex(at - 1)}>
          <ChevronLeft aria-hidden="true" />
          {t("previousSlide")}
        </Button>
        <p className="hidden text-label text-ink-muted sm:block">{t("presentHint")}</p>
        <Button variant="quiet" size="sm" disabled={at >= count - 1} onClick={() => setIndex(at + 1)}>
          {t("nextSlide")}
          <ChevronRight aria-hidden="true" />
        </Button>
      </div>
    </div>,
    host,
  );
}
