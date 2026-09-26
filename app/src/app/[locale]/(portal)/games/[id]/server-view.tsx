"use client";

import { useTranslations } from "next-intl";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Skeleton } from "@/components/ui/skeleton";
import { TabList, TabPanel, Tabs, useTabParam } from "@/components/ui/tabs";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { Ago } from "@/components/ui/ago";
import { ServerMark, ServerStateBadge } from "@/components/games/game-bits";
import { isNotFound } from "@/lib/api/client";
import { useServerStatus, useServers } from "@/lib/queries";
import { serverState } from "@/lib/games";
import { ContainerOverview, LocalSaveOverview } from "./overview";
import { PlayersTab } from "./players-tab";
import { ActivityTab } from "./activity-tab";
import { InsightsTab } from "./insights-tab";
import { WorldTab } from "./world-tab";

const TABS = ["overview", "players", "activity", "insights", "world"] as const;
type Tab = (typeof TABS)[number];

// One server. A container game (Abiotic Factor, Palworld) has players,
// sessions and a leaderboard; a game played from a shared save (The Big
// Walk) has progress instead, and no players or sessions to list: Pulse
// answers those with an empty page for it, so the tabs are not offered.
export function ServerView({ id }: { id: string }) {
  const t = useTranslations("games");
  const status = useServerStatus(id);
  const servers = useServers();
  const [tab, setTab] = useTabParam<Tab>(TABS, "overview");
  const summary = servers.data?.find((s) => s.id === id);

  if (status.isPending) {
    return (
      <PageFrame width="wide">
        <div className="mb-8 flex items-center gap-4">
          <Skeleton className="size-14 rounded-control" />
          <div className="grid gap-2">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-5 w-24" />
          </div>
        </div>
        <Skeleton className="h-72 rounded-panel" />
      </PageFrame>
    );
  }
  if (status.isError && !status.data) {
    return (
      <PageFrame>
        {isNotFound(status.error) ? (
          <EmptyState
            art="lost"
            title={t("notFoundTitle")}
            action={
              <Link href="/games" className="text-meta font-semibold text-denim hover:underline">
                {t("back")}
              </Link>
            }
          >
            {t("notFound")}
          </EmptyState>
        ) : (
          <UnavailableState what={t("unavailable")} onRetry={() => status.refetch()} />
        )}
      </PageFrame>
    );
  }

  const data = status.data;
  const server = data.status.server;
  const local = data.kind === "local-save";
  const state = local ? "local" : serverState({ current: data.status.current });
  const playing = data.kind === "container" ? data.status.current.playerCount > 0 : false;
  const available: Tab[] = local ? ["overview", "activity", "world"] : ["overview", "players", "activity", "insights", "world"];
  const current = available.includes(tab) ? tab : "overview";

  return (
    <PageFrame width="wide">
      <PageHeader
        title={server.displayName}
        back={{ href: "/games", label: t("back") }}
        leading={<ServerMark color={server.color ?? summary?.color ?? "var(--seam)"} size={52} />}
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <ServerStateBadge state={state} playing={playing} />
            {status.dataUpdatedAt ? (
              <span className="text-meta text-ink-muted">
                {t("checked")} <Ago ts={status.dataUpdatedAt} />
              </span>
            ) : null}
          </span>
        }
      />
      <Tabs value={current} onValueChange={(v) => setTab(v as Tab)}>
        <TabList
          label={t("sections")}
          className="mb-5 lg:mb-6"
          items={available.map((v) => ({ value: v, label: t(`tabs.${v}`) }))}
        />
        <TabPanel value="overview">
          {data.kind === "container" ? (
            <ContainerOverview id={id} status={data.status} />
          ) : (
            <LocalSaveOverview id={id} status={data.status} />
          )}
        </TabPanel>
        {!local ? (
          <TabPanel value="players">
            <PlayersTab id={id} />
          </TabPanel>
        ) : null}
        <TabPanel value="activity">
          <ActivityTab id={id} local={local} />
        </TabPanel>
        {!local ? (
          <TabPanel value="insights">
            <InsightsTab id={id} />
          </TabPanel>
        ) : null}
        <TabPanel value="world">
          <WorldTab id={id} />
        </TabPanel>
      </Tabs>
    </PageFrame>
  );
}
