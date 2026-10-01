"use client";

import { useCallback, useEffect, useRef, useState, type TouchEvent } from "react";
import { useTranslations } from "next-intl";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { useRouter } from "@/i18n/routing";
import { Button } from "@/components/ui/button";
import { PageFrame } from "@/components/shell/app-shell";
import { useRegisterTitle } from "@/components/shell/page-title";
import { DocumentView } from "@/components/documents/registry";
import { parseDocument, type DocumentSpec } from "@/lib/documents/catalog";
import { planSlides, slideFromQuery, slideKey, slideSpec, swipeStep, type Slide } from "@/lib/documents/present";
import { useDocument } from "@/lib/queries";
import { cn } from "@/lib/cn";
import { DocumentPageView } from "./document-view";

// `/documents/<id>?view=present` (design/artifact §15, §32): the stored document, one
// top-level Section per slide, over the whole window. The same spec and the same renderer
// as the page; only the split is new (src/lib/documents/present.ts). Arrows, space, Page
// Up/Down, Home and End move, Escape leaves, a swipe moves on a phone, and the counter and
// the exit control are always on screen. A document that cannot be split (no top-level
// Section, or a spec this build refuses) is shown as the ordinary page, with a note.
export function PresentView({ id, slide }: { id: string; slide?: string }) {
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
      id={id}
      title={doc.data.title}
      doc={parsed.doc}
      slides={plan.slides}
      initial={slideFromQuery(slide, plan.slides.length)}
    />
  );
}

function Deck({
  id,
  title,
  doc,
  slides,
  initial,
}: {
  id: string;
  title: string;
  doc: DocumentSpec;
  slides: Slide[];
  initial: number;
}) {
  const t = useTranslations("documentViews");
  const router = useRouter();
  const [index, setIndex] = useState(initial);
  const frame = useRef<HTMLDivElement>(null);
  const touch = useRef<{ x: number; y: number } | null>(null);
  useRegisterTitle(title);
  const count = slides.length;
  // A refetch can bring fewer slides than the one on screen: read the index clamped.
  const at = Math.min(index, count - 1);
  const current = slides[at];
  const leave = useCallback(() => router.replace(`/documents/${id}`), [router, id]);

  useEffect(() => {
    frame.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // A key typed into a control (a table's scroll region is fine; a tab list is not)
      // stays with that control.
      const target = e.target as HTMLElement | null;
      if (target?.closest("input, textarea, select, [role='tablist']")) return;
      // Space and Enter on a focused button or link press it (Leave, Previous, Next).
      if ((e.key === " " || e.key === "Enter") && target?.closest("button, a")) return;
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

  return (
    <div
      ref={frame}
      role="dialog"
      aria-modal="true"
      aria-label={t("presentLabel", { title })}
      tabIndex={-1}
      data-testid="present-view"
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      className="fixed inset-0 z-[60] flex flex-col bg-studio outline-none"
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
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div
          key={current.index}
          className={cn(
            "page-enter mx-auto flex min-h-full w-full max-w-[1080px] flex-col justify-center px-6 py-8 sm:px-10 lg:px-16",
            // The Page's title is the cover; a Section slide shows the Section alone.
            current.kind === "section" && "[&_article>header]:hidden",
          )}
          data-testid="slide"
          data-slide-kind={current.kind}
        >
          <DocumentView doc={slideSpec(doc, current)} documentId={id} />
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
    </div>
  );
}
