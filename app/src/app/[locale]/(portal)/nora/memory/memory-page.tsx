"use client";

import { useTranslations } from "next-intl";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { NoraAvatar } from "@/components/ui/avatar";
import { MemoryView } from "@/components/nora/memory-view";

// Her memory of the viewer, on its own page: the same view the viewer's own
// profile carries under its Memory tab (src/components/nora/memory-view.tsx).
export function MemoryPage() {
  const t = useTranslations("memory");
  const tNav = useTranslations("nav");
  return (
    <PageFrame width="wide">
      <PageHeader
        title={t("title")}
        description={t("lede")}
        back={{ href: "/nora", label: tNav("nora") }}
        leading={<NoraAvatar size={52} />}
      />
      <MemoryView />
    </PageFrame>
  );
}
