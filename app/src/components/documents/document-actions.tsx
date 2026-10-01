"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { History, Presentation, Printer } from "lucide-react";
import { Link } from "@/i18n/routing";
import { Button, buttonClass } from "@/components/ui/button";
import { parseDocument } from "@/lib/documents/catalog";
import { PRESENT_FROM_PAGE, planSlides } from "@/lib/documents/present";
import { markLinksForPrint, printInLight } from "@/lib/documents/print";
import "./print.css";

// The three ways to take a document somewhere else (design/artifact §34): present it one
// Section per slide, print it (the browser's print is the PDF path), and read its history.
// Drawn in the page's chrome, marked `data-print-hide`, so none of it is ever on paper.

/** Where focus goes back to when a presentation ends: the Present button of the document
 * it was opened from. Per tab, best effort: storage that throws only loses the focus. */
const RETURN_KEY = "nora.present.return";

export function rememberPresentReturn(id: string) {
  try {
    sessionStorage.setItem(RETURN_KEY, id);
  } catch {
    /* no storage: focus simply starts at the top */
  }
}

function takePresentReturn(id: string): boolean {
  try {
    if (sessionStorage.getItem(RETURN_KEY) !== id) return false;
    sessionStorage.removeItem(RETURN_KEY);
    return true;
  } catch {
    return false;
  }
}

/** Present, Print / save as PDF, and History for one stored document (`id` is its UUID).
 * Present is offered only for a document that splits into slides (a top-level Section); a
 * link to `?view=present` for any other still opens, and says why it shows the page. */
export function DocumentActions({ id, spec }: { id: string; spec: unknown }) {
  const t = useTranslations("documentViews");
  const parsed = parseDocument(spec);
  const presentable = parsed.ok && planSlides(parsed.doc).ok;
  const present = useRef<HTMLAnchorElement>(null);
  useEffect(() => {
    if (presentable && takePresentReturn(id)) present.current?.focus();
  }, [id, presentable]);
  return (
    <div className="flex flex-wrap gap-2" data-testid="document-actions">
      {presentable ? (
        <Link
          ref={present}
          href={`/documents/${id}?view=present&${PRESENT_FROM_PAGE}=1`}
          className={buttonClass("outline", "sm")}
          data-testid="present-button"
        >
          <Presentation aria-hidden="true" />
          {t("present")}
        </Link>
      ) : null}
      <PrintButton />
      <Link href={`/documents/${id}/history`} className={buttonClass("quiet", "sm")}>
        <History aria-hidden="true" />
        {t("history")}
      </Link>
    </div>
  );
}

/** Opens the browser's print dialog, where "Save as PDF" is one of the printers, on light
 * paper whatever the theme (`printInLight`). */
export function PrintButton() {
  const t = useTranslations("documentViews");
  const onPrint = () =>
    void printInLight({
      html: document.documentElement,
      drawnThemes: () =>
        [...document.querySelectorAll("[data-print-document] [data-mermaid-theme]")].map(
          (el) => el.getAttribute("data-mermaid-theme") ?? "",
        ),
      print: () => window.print(),
    });
  return (
    <Button variant="quiet" size="sm" onClick={onPrint}>
      <Printer aria-hidden="true" />
      {t("print")}
    </Button>
  );
}

/** The document as it prints: the one subtree print.css keeps. Links are marked with the
 * address to print after them just before the dialog opens, whoever opened it (the
 * button, or the browser's own menu). */
export function PrintableDocument({ children }: { children: ReactNode }) {
  useEffect(() => {
    const mark = () => {
      const root = document.querySelector("[data-print-document]");
      if (root) markLinksForPrint(root);
    };
    window.addEventListener("beforeprint", mark);
    return () => window.removeEventListener("beforeprint", mark);
  }, []);
  return <div data-print-document>{children}</div>;
}
