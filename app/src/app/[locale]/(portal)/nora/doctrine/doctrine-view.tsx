"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { Skeleton, SkeletonText } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { DoctrineBody, Inline, langOf } from "@/components/nora/doctrine-body";
import { useDoctrine } from "@/lib/queries";
import type { DoctrineSection } from "@/lib/api/nora";
import { cn } from "@/lib/cn";

// The doctrine sections in the order she reads them (identity, voice,
// silence, conduct, capability, constraints: nora/web/doctrine.py). Each is
// an article with its own anchor, which the command palette links to. A
// table of contents follows the reader on a wide screen; on a phone the
// sections are a row of chips under the heading.
const KNOWN = ["identity", "voice", "silence", "conduct", "capability", "constraints"] as const;

function useSectionName() {
  const t = useTranslations("doctrine");
  return (name: string) =>
    (KNOWN as readonly string[]).includes(name)
      ? t(`names.${name as (typeof KNOWN)[number]}`)
      : name.charAt(0).toUpperCase() + name.slice(1);
}

/** The section most in view, for the table of contents. */
function useActiveSection(ids: string[]): string | null {
  const [active, setActive] = useState<string | null>(ids[0] ?? null);
  useEffect(() => {
    if (!ids.length) return;
    const seen = new Map<string, boolean>();
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) seen.set(e.target.id, e.isIntersecting);
        const first = ids.find((id) => seen.get(id));
        if (first) setActive(first);
      },
      // A section counts as current while its top third is in the upper
      // half of the screen.
      { rootMargin: "-80px 0px -55% 0px", threshold: 0 },
    );
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) io.observe(el);
    }
    return () => io.disconnect();
  }, [ids.join("|")]); // eslint-disable-line react-hooks/exhaustive-deps
  return active;
}

export function DoctrineView() {
  const t = useTranslations("doctrine");
  const tNav = useTranslations("nav");
  const doctrine = useDoctrine();
  const sections = doctrine.data ?? [];
  const nameOf = useSectionName();
  const active = useActiveSection(sections.map((s) => s.name));

  // Arriving from the palette at /nora/doctrine#conduct: the section is not
  // on the page until the read lands, so scroll to it once it is.
  useEffect(() => {
    if (!doctrine.data || !window.location.hash) return;
    const el = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
    el?.scrollIntoView({ block: "start" });
  }, [doctrine.data]);

  return (
    <PageFrame width="wide">
      <PageHeader
        title={t("title")}
        description={t("lede")}
        back={{ href: "/nora", label: tNav("nora") }}
      />

      {doctrine.isPending ? (
        <div className="grid max-w-[68ch] gap-8">
          {[0, 1].map((i) => (
            <div key={i} className="grid gap-3">
              <Skeleton className="h-6 w-48" />
              <SkeletonText lines={5} />
            </div>
          ))}
        </div>
      ) : doctrine.isError && !doctrine.data ? (
        <UnavailableState onRetry={() => doctrine.refetch()} />
      ) : sections.length === 0 ? (
        <EmptyState art="quiet">{t("empty")}</EmptyState>
      ) : (
        <>
          <nav
            aria-label={t("contents")}
            className="scrollbar-none sticky top-[calc(env(safe-area-inset-top)+56px)] z-20 -mx-4 mb-6 flex gap-2 overflow-x-auto border-b border-hairline bg-studio/92 px-4 py-2.5 backdrop-blur-xl lg:hidden"
          >
            {sections.map((s) => (
              <a
                key={s.name}
                href={`#${s.name}`}
                aria-current={active === s.name ? "location" : undefined}
                className={cn(
                  "flex h-9 shrink-0 items-center rounded-full border px-3.5 text-meta font-medium transition-colors duration-100",
                  active === s.name
                    ? "border-denim bg-surface text-denim"
                    : "border-hairline bg-surface text-ink-soft hover:text-ink",
                )}
              >
                {nameOf(s.name)}
              </a>
            ))}
          </nav>

          <div className="grid gap-10 lg:grid-cols-[minmax(0,68ch)_220px] lg:justify-between lg:gap-16">
            <div className="grid gap-12 lg:gap-16">
              {sections.map((s, i) => (
                <Section key={s.name} section={s} index={i} title={nameOf(s.name)} />
              ))}
              <p className="border-t border-hairline pt-5 text-meta text-ink-muted">{t("footnote")}</p>
            </div>

            <nav aria-label={t("contents")} className="hidden lg:block">
              <div className="sticky top-10 grid gap-1">
                <p className="mb-2 text-label font-medium text-ink-muted">{t("contents")}</p>
                <ol className="relative grid gap-0.5 border-l border-hairline">
                  {sections.map((s) => {
                    const on = active === s.name;
                    return (
                      <li key={s.name}>
                        <a
                          href={`#${s.name}`}
                          aria-current={on ? "location" : undefined}
                          className={cn(
                            "-ml-px flex items-center justify-between gap-2 border-l-2 py-1.5 pr-2 pl-3.5 text-meta transition-colors duration-100",
                            on
                              ? "border-denim font-semibold text-denim"
                              : "border-transparent text-ink-soft hover:text-ink",
                          )}
                        >
                          <span className="truncate">{nameOf(s.name)}</span>
                          <span className="font-mono text-[0.6875rem] text-ink-muted tnum">v{s.version}</span>
                        </a>
                      </li>
                    );
                  })}
                </ol>
              </div>
            </nav>
          </div>
        </>
      )}
    </PageFrame>
  );
}

function Section({ section: s, index, title }: { section: DoctrineSection; index: number; title: string }) {
  const t = useTranslations("doctrine");
  return (
    <article
      id={s.name}
      aria-labelledby={`${s.name}-title`}
      className="page-enter scroll-mt-32 lg:scroll-mt-10"
      style={{ animationDelay: `${Math.min(index, 4) * 30}ms` }}
    >
      <header className="mb-5 grid gap-2 border-b border-hairline pb-5">
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="font-mono text-label text-ink-muted tnum">{String(index + 1).padStart(2, "0")}</span>
          <h2 id={`${s.name}-title`} className="text-[1.375rem] leading-tight font-[650] tracking-[-0.01em] text-ink">
            {title}
          </h2>
          <Badge tone="outline" title={t("versionHelp")}>
            {t("version", { version: s.version })}
          </Badge>
        </div>
        {s.description ? (
          <p lang={langOf(s.description)} className="max-w-[62ch] text-lead text-ink-soft [&:lang(zh-Hant-HK)]:leading-[1.75]">
            <Inline text={s.description} />
          </p>
        ) : null}
      </header>
      <DoctrineBody body={s.body} />
    </article>
  );
}
