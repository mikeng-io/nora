"use client";

import { useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { Check, Circle } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PersonAvatar } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Skeleton } from "@/components/ui/skeleton";
import { Stat, StatGrid } from "@/components/ui/stat";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { BarList } from "@/components/charts/bars";
import type { BigWalkWorld, WorldState } from "@/lib/api/games";
import { useWorld } from "@/lib/queries";
import { formatDateTime, formatHours, formatNumber } from "@/lib/format";
import { refName } from "@/lib/games";
import { cn } from "@/lib/cn";

// The shared world, in each game's own terms: Abiotic Factor's stockpile
// and the sectors the crew has reached; The Big Walk's checklists, crew
// and notes.
export function WorldTab({ id }: { id: string }) {
  const t = useTranslations("games");
  const world = useWorld(id);
  if (world.isPending) return <Skeleton className="h-96 rounded-panel" />;
  if (world.isError && !world.data) return <UnavailableState onRetry={() => world.refetch()} />;
  if (world.data.kind === "abiotic-factor") return <AbioticWorld world={world.data.world} />;
  if (world.data.kind === "big-walk") return <BigWalkWorldView world={world.data.world} />;
  return <EmptyState compact>{t("noWorld")}</EmptyState>;
}

function Unknown({ children, known }: { children: string; known: boolean }) {
  return <span className={known ? "" : "font-mono text-meta text-ink-muted"}>{children}</span>;
}

function AbioticWorld({ world }: { world: WorldState }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const [all, setAll] = useState(false);
  const items = [...world.stockpile.items].sort((a, b) => b.count - a.count);
  const timeline = [...world.timeline].sort((a, b) => a.day - b.day);

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] lg:gap-6">
      <div className="grid gap-4 lg:gap-6">
        <Panel>
          <PanelHeader title={t("worldTitle")} description={t("savedAt", { when: formatDateTime(world.savedAt, locale) })} />
          <PanelBody>
            <StatGrid>
              <Stat label={t("day")} value={formatNumber(world.metadata.day, locale)} />
              <Stat label={t("itemsStored")} value={formatNumber(world.stockpile.totalItems, locale)} />
              <Stat label={t("kindsOfItem")} value={formatNumber(world.stockpile.distinctItems, locale)} />
              <Stat label={t("structures")} value={formatNumber(world.stockpile.totalStructures, locale)} />
            </StatGrid>
          </PanelBody>
        </Panel>
        <Panel>
          <PanelHeader title={t("stockpile")} />
          <PanelBody className="grid gap-4">
            {items.length ? (
              <>
                <BarList
                  label={t("stockpile")}
                  data={(all ? items : items.slice(0, 10)).map((i) => ({
                    key: i.id,
                    label: <Unknown known={i.known}>{refName(i, locale)}</Unknown>,
                    value: i.count,
                    display: formatNumber(i.count, locale),
                  }))}
                />
                {items.length > 10 ? (
                  <Button variant="quiet" size="sm" className="w-fit" onClick={() => setAll((a) => !a)}>
                    {all ? t("showLess") : t("showMore", { count: items.length - 10 })}
                  </Button>
                ) : null}
              </>
            ) : (
              <EmptyState compact>{t("noStockpile")}</EmptyState>
            )}
          </PanelBody>
        </Panel>
      </div>
      <div className="grid gap-4 lg:gap-6">
        {timeline.length ? (
          <Panel>
            <PanelHeader title={t("explored")} />
            <ol className="relative px-4 pb-4 sm:px-5">
              <span className="absolute top-2 bottom-6 left-[21px] w-px bg-hairline sm:left-[25px]" aria-hidden="true" />
              {timeline.map((s) => (
                <li key={s.id} className="relative flex items-center gap-3 py-2">
                  <span className="relative z-10 size-[11px] shrink-0 rounded-full border-2 border-surface bg-denim" aria-hidden="true" />
                  <span className="min-w-0 flex-1 text-body text-ink">
                    <Unknown known={s.known}>{refName(s, locale)}</Unknown>
                  </span>
                  <span className="shrink-0 font-mono text-label text-ink-muted tnum">{t("dayN", { day: s.day })}</span>
                </li>
              ))}
            </ol>
          </Panel>
        ) : null}
        {world.stockpile.sectors.length ? (
          <Panel>
            <PanelHeader title={t("sectors")} />
            <ul className="divide-y divide-hairline border-t border-hairline">
              {world.stockpile.sectors.map((s) => (
                <li key={s.sectorId} className="flex items-center justify-between gap-3 px-4 py-2.5 sm:px-5">
                  <span className="min-w-0 truncate text-body text-ink">
                    <Unknown known={s.known}>{refName(s, locale)}</Unknown>
                  </span>
                  <span className="shrink-0 font-mono text-label text-ink-muted tnum">
                    {t("sectorMeta", { structures: s.structures, items: s.storedItems })}
                  </span>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}
      </div>
    </div>
  );
}

const CHECKLISTS = ["gourds", "bigKeys", "fmStations", "lookoutLights", "namedDoors"] as const;

function BigWalkWorldView({ world }: { world: BigWalkWorld }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const notes = Object.entries(world.notes);

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)] lg:gap-6">
      <div className="grid gap-4 lg:gap-6">
        <Panel>
          <PanelHeader
            title={t("slot", { name: world.slotName })}
            description={t("savedAt", { when: formatDateTime(world.savedAt, locale) })}
            action={<Badge>{world.skipAidsActive ? t("skipAidsOn") : t("skipAidsOff")}</Badge>}
          />
          <PanelBody>
            <StatGrid>
              <Stat label={t("playtime")} value={formatHours(world.totalPlaytimeSeconds * 1000, locale)} />
              <Stat label={t("crew")} value={formatNumber(world.players.length, locale)} />
              <Stat label={t("inventory")} value={formatNumber(world.inventory.length, locale)} />
              <Stat label={t("seed")} value={String(world.worldSeed)} />
            </StatGrid>
          </PanelBody>
        </Panel>
        <Panel>
          <PanelHeader title={t("checklists")} description={t("checklistsLede")} />
          <div className="grid gap-5 px-4 pb-5 sm:px-5">
            {CHECKLISTS.map((k) => {
              const entries = Object.entries(world.completions[k]);
              if (!entries.length) return null;
              return (
                <section key={k} aria-label={t(`progress.${k}`)} className="grid gap-2">
                  <h3 className="flex items-baseline justify-between text-meta font-semibold text-ink">
                    {t(`progress.${k}`)}
                    <span className="font-mono text-label font-normal text-ink-muted tnum">
                      {formatNumber(entries.reduce((s, [, v]) => s + v, 0), locale)}
                    </span>
                  </h3>
                  <ul className="flex flex-wrap gap-1.5">
                    {entries.map(([area, n]) => (
                      <li
                        key={area}
                        className={cn(
                          "inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-label font-medium",
                          n > 0 ? "border-hairline bg-studio text-ink" : "border-dashed border-seam text-ink-muted",
                        )}
                      >
                        {n > 0 ? <Check className="size-3 text-success" aria-hidden="true" /> : <Circle className="size-3" aria-hidden="true" />}
                        <span className="capitalize">{area}</span>
                        <span className="font-mono tnum">{n}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })}
          </div>
        </Panel>
      </div>
      <div className="grid gap-4 lg:gap-6">
        <Panel>
          <PanelHeader title={t("crew")} />
          <ul className="divide-y divide-hairline border-t border-hairline">
            {world.players.map((p) => (
              <li key={p.steamId} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                {p.linkedPerson ? (
                  <Link href={`/people/${p.linkedPerson.personId}`} className="flex min-w-0 flex-1 items-center gap-3">
                    <PersonAvatar id={p.linkedPerson.personId} name={p.linkedPerson.displayName} size={32} />
                    <span className="truncate text-body font-semibold text-ink">{p.linkedPerson.displayName}</span>
                  </Link>
                ) : (
                  <span className="min-w-0 flex-1 truncate font-mono text-meta text-ink-muted">{p.steamId}</span>
                )}
                {p.walkingProven ? <Badge tone="success" icon={<Check aria-hidden="true" />}>{t("walkingProven")}</Badge> : null}
              </li>
            ))}
          </ul>
        </Panel>
        {notes.length ? (
          <Panel>
            <PanelHeader title={t("notes")} />
            <ul className="divide-y divide-hairline border-t border-hairline">
              {notes.map(([k, v]) => (
                <li key={k} className="grid gap-0.5 px-4 py-3 sm:px-5">
                  <span className="text-label text-ink-muted">{k}</span>
                  <span className="text-body text-ink">{v}</span>
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}
        {world.inventory.length ? (
          <Panel>
            <PanelHeader title={t("inventory")} />
            <PanelBody>
              <ul className="flex flex-wrap gap-1.5">
                {world.inventory.map((item, i) => (
                  <li key={`${item}-${i}`} className="rounded-full bg-hoodie px-2.5 py-1 font-mono text-label text-ink-soft">
                    {item}
                  </li>
                ))}
              </ul>
            </PanelBody>
          </Panel>
        ) : null}
      </div>
    </div>
  );
}
