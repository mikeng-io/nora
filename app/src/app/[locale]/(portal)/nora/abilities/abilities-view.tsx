"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import {
  CalendarClock,
  ChevronDown,
  Database,
  Eye,
  Flag,
  Hash,
  PencilLine,
  Search,
  X,
} from "lucide-react";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { Panel } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { TabList, TabPanel, Tabs, useTabParam } from "@/components/ui/tabs";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { DoctrineBody } from "@/components/nora/doctrine-body";
import { useSkills, useTools } from "@/lib/queries";
import type { Skill, Tool } from "@/lib/api/nora";
import { TOOL_FILTERS, effectKey, matchesFilter, matchesQuery, tierKey, type ToolFilter } from "@/lib/tools";
import { cn } from "@/lib/cn";

// Everything she can call and every skill she can load, read live from the
// deployed registry (nora/web/tools.py, nora/web/skills.py): a tool added to
// her shows up here with no portal change. Search and the tab live in the
// URL, so the command palette can link straight to one tool.
const TABS = ["tools", "skills"] as const;
type Tab = (typeof TABS)[number];

/** The ?q= search, kept in the URL without a navigation per keystroke. */
function useQueryParam(): [string, (q: string) => void] {
  const params = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  useEffect(() => setQ(params.get("q") ?? ""), [params]);
  const set = (next: string) => {
    setQ(next);
    const url = new URL(window.location.href);
    if (next) url.searchParams.set("q", next);
    else url.searchParams.delete("q");
    window.history.replaceState(null, "", url);
  };
  return [q, set];
}

export function AbilitiesView() {
  const t = useTranslations("abilities");
  const tNav = useTranslations("nav");
  const tools = useTools();
  const skills = useSkills();
  const [tab, setTab] = useTabParam<Tab>(TABS, "tools");
  const [query, setQuery] = useQueryParam();
  const [filter, setFilter] = useState<ToolFilter>("all");

  const shownTools = useMemo(
    () =>
      [...(tools.data ?? [])]
        .filter((tool) => matchesFilter(tool, filter) && matchesQuery(`${tool.name} ${tool.description ?? ""}`, query))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [tools.data, filter, query],
  );
  const shownSkills = useMemo(
    () =>
      [...(skills.data ?? [])]
        .filter((s) => matchesQuery(`${s.name} ${s.description} ${s.instructions ?? ""}`, query))
        .sort((a, b) => a.name.localeCompare(b.name)),
    [skills.data, query],
  );
  const counts = useMemo(
    () => Object.fromEntries(TOOL_FILTERS.map((f) => [f, (tools.data ?? []).filter((x) => matchesFilter(x, f)).length])),
    [tools.data],
  ) as Record<ToolFilter, number>;

  return (
    <PageFrame>
      <PageHeader title={t("title")} description={t("lede")} back={{ href: "/nora", label: tNav("nora") }} />

      <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
        <div className="mb-5 grid gap-4">
          <TabList
            label={t("sections")}
            items={[
              { value: "tools", label: t("tabs.tools"), count: tools.data?.length },
              { value: "skills", label: t("tabs.skills"), count: skills.data?.length },
            ]}
          />
          <label className="flex h-11 items-center gap-2 rounded-control border border-hairline bg-surface px-3 focus-within:border-seam focus-within:ring-2 focus-within:ring-denim lg:h-10">
            <Search className="size-4 shrink-0 text-ink-muted" aria-hidden="true" />
            <span className="sr-only">{t("search")}</span>
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={tab === "tools" ? t("searchTools") : t("searchSkills")}
              className="min-w-0 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-ink-muted [&::-webkit-search-cancel-button]:hidden"
            />
            {query ? (
              <button
                type="button"
                onClick={() => setQuery("")}
                aria-label={t("clear")}
                className="grid size-7 place-items-center rounded-full text-ink-muted hover:bg-hoodie hover:text-ink"
              >
                <X className="size-4" aria-hidden="true" />
              </button>
            ) : null}
          </label>
        </div>

        <TabPanel value="tools">
          {tools.isPending ? (
            <ListSkeleton />
          ) : tools.isError && !tools.data ? (
            <UnavailableState onRetry={() => tools.refetch()} />
          ) : (tools.data ?? []).length === 0 ? (
            <EmptyState art="quiet">{t("noTools")}</EmptyState>
          ) : (
            <>
              <div role="group" aria-label={t("filterLabel")} className="scrollbar-none -mx-4 mb-4 flex gap-2 overflow-x-auto px-4 sm:mx-0 sm:flex-wrap sm:px-0">
                {TOOL_FILTERS.map((f) => (
                  <button
                    key={f}
                    type="button"
                    aria-pressed={filter === f}
                    onClick={() => setFilter(f)}
                    className={cn(
                      "flex h-9 shrink-0 items-center gap-1.5 rounded-full border px-3.5 text-meta font-medium transition-colors duration-100",
                      filter === f
                        ? "border-denim bg-surface text-denim"
                        : "border-hairline bg-surface text-ink-soft hover:border-seam hover:text-ink",
                    )}
                  >
                    {t(`filter.${f}`)}
                    <span className="font-mono text-[0.6875rem] text-ink-muted tnum">{counts[f]}</span>
                  </button>
                ))}
              </div>
              {shownTools.length === 0 ? (
                <EmptyState compact>{t("noMatch")}</EmptyState>
              ) : (
                <Panel>
                  <ul className="divide-y divide-hairline">
                    {shownTools.map((tool) => (
                      <ToolRow key={tool.name} tool={tool} open={shownTools.length === 1} />
                    ))}
                  </ul>
                </Panel>
              )}
              <p className="mt-4 px-1 text-label text-ink-muted">{t("toolsNote")}</p>
            </>
          )}
        </TabPanel>

        <TabPanel value="skills">
          {skills.isPending ? (
            <ListSkeleton />
          ) : skills.isError && !skills.data ? (
            <UnavailableState onRetry={() => skills.refetch()} />
          ) : (skills.data ?? []).length === 0 ? (
            <EmptyState art="quiet">{t("noSkills")}</EmptyState>
          ) : shownSkills.length === 0 ? (
            <EmptyState compact>{t("noMatch")}</EmptyState>
          ) : (
            <>
              <div className="grid gap-3">
                {shownSkills.map((skill) => (
                  <SkillCard key={skill.name} skill={skill} open={shownSkills.length === 1} />
                ))}
              </div>
              <p className="mt-4 px-1 text-label text-ink-muted">{t("skillsNote")}</p>
            </>
          )}
        </TabPanel>
      </Tabs>
    </PageFrame>
  );
}

function ListSkeleton() {
  return (
    <div className="grid gap-3">
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-24 rounded-panel" />
      ))}
    </div>
  );
}

// ----------------------------------------------------------------- tools

function ToolRow({ tool, open: startOpen }: { tool: Tool; open: boolean }) {
  const t = useTranslations("abilities");
  const [open, setOpen] = useState(startOpen);
  const description = tool.description?.trim() ?? "";
  const long = description.length > 220 || description.includes("\n");
  const tier = tierKey(tool.source_tier);
  const effect = effectKey(tool.side_effect_state);

  const facts: Array<{ icon: typeof Eye; text: ReactNode; tone?: "caution" }> = [
    tool.read_only
      ? { icon: Eye, text: t("fact.readOnly") }
      : {
          icon: PencilLine,
          text: effect ? t(`effect.${effect}`) : `${t("fact.writes")} · ${tool.side_effect_state}`,
          tone: effect === "unknown" ? "caution" : undefined,
        },
    { icon: Hash, text: t("fact.budget", { n: tool.budget }) },
  ];
  if (tool.time_shiftable === true) facts.push({ icon: CalendarClock, text: t("fact.anyTime") });
  if (tool.time_shiftable === false) facts.push({ icon: CalendarClock, text: t("fact.nowOnly") });
  if (tool.cacheable) facts.push({ icon: Database, text: t("fact.cacheable") });
  if (tool.terminal) facts.push({ icon: Flag, text: t("fact.terminal") });

  return (
    <li className="grid gap-2.5 px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-mono text-[0.875rem] font-semibold text-ink">{tool.name}</h3>
        <Badge tone={tier === "action" ? "outline" : "neutral"}>{t(`tier.${tier}`)}</Badge>
      </div>
      {description ? (
        <div className="grid gap-1.5">
          <p
            className={cn(
              "max-w-[72ch] text-body whitespace-pre-line text-ink-soft",
              long && !open && "line-clamp-3",
            )}
          >
            {description}
          </p>
          {long ? (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              aria-expanded={open}
              className="inline-flex w-fit items-center gap-1 rounded-control py-1 text-meta font-semibold text-denim"
            >
              {open ? t("less") : t("more")}
              <ChevronDown
                className={cn("size-4 transition-transform duration-160 ease-nora", open && "rotate-180")}
                aria-hidden="true"
              />
            </button>
          ) : null}
        </div>
      ) : (
        <p className="text-body text-ink-muted">{t("noDescription")}</p>
      )}
      <ul className="flex flex-wrap gap-x-4 gap-y-1.5">
        {facts.map((f, i) => (
          <li
            key={i}
            className={cn("flex items-center gap-1.5 text-label", f.tone === "caution" ? "text-caution" : "text-ink-muted")}
          >
            <f.icon className="size-3.5 shrink-0" aria-hidden="true" />
            {f.text}
          </li>
        ))}
      </ul>
    </li>
  );
}

// ---------------------------------------------------------------- skills

function SkillCard({ skill, open: startOpen }: { skill: Skill; open: boolean }) {
  const t = useTranslations("abilities");
  const [open, setOpen] = useState(startOpen);
  const bodyId = `skill-${skill.name}`;
  return (
    <Panel className="overflow-hidden">
      <div className="grid gap-2 px-4 pt-4 pb-3 sm:px-5">
        <h3 className="font-mono text-[0.875rem] font-semibold text-ink">{skill.name}</h3>
        <p className="max-w-[72ch] text-body text-ink-soft">{skill.description}</p>
      </div>
      {skill.instructions ? (
        <>
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-controls={bodyId}
            className="flex w-full items-center justify-between gap-2 border-t border-hairline px-4 py-3 text-left text-meta font-semibold text-denim transition-colors hover:bg-hoodie/60 sm:px-5"
          >
            {open ? t("hideInstructions") : t("showInstructions")}
            <ChevronDown
              className={cn("size-4 transition-transform duration-160 ease-nora", open && "rotate-180")}
              aria-hidden="true"
            />
          </button>
          {open ? (
            <div id={bodyId} className="animate-fade border-t border-hairline bg-studio/60 px-4 py-5 sm:px-5">
              <DoctrineBody body={skill.instructions} headingBase={4} />
            </div>
          ) : null}
        </>
      ) : (
        <p className="border-t border-hairline px-4 py-3 text-label text-ink-muted sm:px-5">{t("noInstructions")}</p>
      )}
    </Panel>
  );
}
