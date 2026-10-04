"use client";

import { Phone } from "lucide-react";
import { useTranslations } from "next-intl";
import { Panel } from "@/components/ui/panel";
import { firstGrapheme, telHref } from "./contact-phone";
import type { Render } from "./types";

// People to reach on the day: one Panel, divided rows. The avatar is a neutral disc, not
// a PersonAvatar: a document's contact has no person id, and a hue would claim one
// (DESIGN.md, the Person-Hue Rule). The number is the tap target, 44px on a phone.
export function ContactBlock({ props }: Render<"Contact">) {
  const t = useTranslations("documents.renderer");
  return (
    <Panel>
      <ul className="divide-y divide-hairline">
        {props.items.map((it, i) => {
          const href = it.phone ? telHref(it.phone) : null;
          return (
            <li key={i} className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3 sm:px-5">
              <span
                aria-hidden="true"
                className="grid size-10 shrink-0 select-none place-items-center rounded-full bg-hoodie text-body font-semibold text-ink-soft"
              >
                {firstGrapheme(it.name)}
              </span>
              <div className="min-w-0 flex-1 basis-40">
                <p className="text-body font-semibold break-words text-ink">{it.name}</p>
                {it.role ? <p className="text-meta break-words text-ink-muted">{it.role}</p> : null}
                {it.note ? <p className="text-meta break-words text-ink-soft">{it.note}</p> : null}
              </div>
              {it.phone && href ? (
                <a
                  href={href}
                  aria-label={t("contactCall", { name: it.name, phone: it.phone })}
                  className="inline-flex min-h-11 min-w-11 shrink-0 items-center gap-2 rounded-control border border-hairline bg-surface px-3 font-mono text-meta tnum text-denim transition-colors duration-[160ms] ease-nora hover:bg-hoodie motion-reduce:transition-none"
                >
                  <Phone aria-hidden className="size-4 shrink-0" />
                  <span>{it.phone}</span>
                </a>
              ) : it.phone ? (
                <span className="font-mono text-meta tnum text-ink-soft">{it.phone}</span>
              ) : null}
            </li>
          );
        })}
      </ul>
    </Panel>
  );
}
