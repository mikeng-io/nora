"use client";

import { useEffect, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { History, Presentation, Printer } from "lucide-react";
import { Link } from "@/i18n/routing";
import { Button, buttonClass } from "@/components/ui/button";
import { parseDocument } from "@/lib/documents/catalog";
import { planSlides } from "@/lib/documents/present";
import { markLinksForPrint } from "@/lib/documents/print";
import "./print.css";

// The three ways to take a document somewhere else (design/artifact §32): present it one
// Section per slide, print it (the browser's print is the PDF path), and read its history.
// Outside `PrintableDocument`, so none of it is ever on paper.

/** Present, Print / save as PDF, and History for one stored document. Present is offered
 * only for a document that splits into slides (a top-level Section); a link to
 * `?view=present` for any other still opens, and says why it shows the ordinary view. */
export function DocumentActions({ id, spec }: { id: string; spec: unknown }) {
  const t = useTranslations("documentViews");
  const parsed = parseDocument(spec);
  const presentable = parsed.ok && planSlides(parsed.doc).ok;
  return (
    <div className="mb-6 flex flex-wrap gap-2" data-testid="document-actions">
      {presentable ? (
        <Link href={`/documents/${id}?view=present`} className={buttonClass("outline", "sm")}>
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

/** Opens the browser's print dialog, where "Save as PDF" is one of the printers. */
export function PrintButton() {
  const t = useTranslations("documentViews");
  return (
    <Button variant="quiet" size="sm" onClick={() => window.print()}>
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
