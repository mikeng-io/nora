"use client";

import type { ReactNode } from "react";
import { ChevronLeft } from "lucide-react";
import { Link } from "@/i18n/routing";
import { cn } from "@/lib/cn";
import { useRegisterTitle } from "@/components/shell/page-title";

export function PageHeader({
  title,
  description,
  actions,
  back,
  eyebrow,
  leading,
  className,
  children,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  back?: { href: string; label: string };
  eyebrow?: ReactNode;
  /** A mark before the title block (a person's avatar). */
  leading?: ReactNode;
  className?: string;
  children?: ReactNode;
}) {
  const ref = useRegisterTitle(title);
  return (
    <header className={cn("mb-6 grid gap-3 lg:mb-8", className)}>
      {back ? (
        <Link
          href={back.href}
          className="-ml-1.5 inline-flex h-8 w-fit items-center gap-0.5 rounded-control pr-2 pl-0.5 text-meta font-medium text-ink-muted transition-colors duration-100 hover:bg-hoodie hover:text-ink"
        >
          <ChevronLeft className="size-4" aria-hidden="true" />
          {back.label}
        </Link>
      ) : null}
      <div className="flex flex-wrap items-end justify-between gap-x-4 gap-y-3">
        <div className="flex min-w-0 items-center gap-4">
        {leading}
        <div className="grid min-w-0 gap-1.5">
          {eyebrow ? <div className="text-meta text-ink-muted">{eyebrow}</div> : null}
          <h1
            ref={ref}
            className="text-heading font-[650] tracking-[-0.01em] text-ink lg:text-[1.75rem] lg:leading-[1.15]"
          >
            {title}
          </h1>
          {description ? (
            <div className="max-w-[62ch] text-body text-ink-soft">{description}</div>
          ) : null}
        </div>
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>
      {children}
    </header>
  );
}
