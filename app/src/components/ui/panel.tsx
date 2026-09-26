import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/cn";

// A panel is surface on studio with one hairline (DESIGN.md §5, the
// Hairline-First Rule). Inside it: rows and dividers, never another card.
export function Panel({ className, ...props }: HTMLAttributes<HTMLElement>) {
  return (
    <section
      className={cn("rounded-panel border border-hairline bg-surface", className)}
      {...props}
    />
  );
}

export function PanelHeader({
  title,
  description,
  action,
  className,
  id,
}: {
  title: ReactNode;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
  id?: string;
}) {
  return (
    <header className={cn("flex items-start gap-3 px-4 pt-4 pb-3 sm:px-5", className)}>
      <div className="min-w-0 flex-1">
        <h2 id={id} className="text-title font-semibold text-ink">
          {title}
        </h2>
        {description ? <p className="mt-0.5 text-meta text-ink-muted">{description}</p> : null}
      </div>
      {action ? <div className="-my-1 shrink-0">{action}</div> : null}
    </header>
  );
}

export function PanelBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("px-4 pb-4 sm:px-5 sm:pb-5", className)} {...props} />;
}

/** Rows separated by hairlines, the way a panel holds a list. */
export function Rows({ className, ...props }: HTMLAttributes<HTMLUListElement>) {
  return <ul className={cn("divide-y divide-hairline", className)} {...props} />;
}

export function SectionTitle({
  children,
  action,
  id,
  className,
}: {
  children: ReactNode;
  action?: ReactNode;
  id?: string;
  className?: string;
}) {
  return (
    <div className={cn("mb-3 flex items-end justify-between gap-3", className)}>
      <h2 id={id} className="text-title font-semibold text-ink">
        {children}
      </h2>
      {action}
    </div>
  );
}
