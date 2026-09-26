"use client";

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import { ArrowRight, Minus, Skull } from "lucide-react";
import { Link } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { PersonAvatar } from "@/components/ui/avatar";
import { Badge, Dot } from "@/components/ui/badge";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Meter, Stat, StatGrid } from "@/components/ui/stat";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { BarList, Columns } from "@/components/charts/bars";
import { TimeChart } from "@/components/charts/time-chart";
import { isNotFound } from "@/lib/api/client";
import { fetchServerSessions, type AbioticFactorSave, type PlayerDetail, type VitalsHistoryPoint } from "@/lib/api/games";
import { qk, usePlayer, useSessions } from "@/lib/queries";
import {
  formatDate,
  formatDateTime,
  formatDuration,
  formatHours,
  formatNumber,
  formatRelative,
  formatTime,
  formatWeekday,
  formatWeekdayDate,
} from "@/lib/format";
import { localized, playtimeByDay, refName } from "@/lib/games";
import { personStyle } from "@/lib/person";
import { useNow } from "@/hooks/use-now";

// One player on one server: how much they play and when, and, for a game
// whose save Pulse can read, their character: job, traits, skills, gear,
// body and what has killed them. A player linked to someone in the circle
// shows who that is.
export function PlayerView({ id, playerKey }: { id: string; playerKey: string }) {
  const t = useTranslations("games");
  const player = usePlayer(id, playerKey);

  if (player.isPending) {
    return (
      <PageFrame width="wide">
        <Skeleton className="mb-3 h-8 w-56" />
        <Skeleton className="mb-8 h-5 w-40" />
        <Skeleton className="h-72 rounded-panel" />
      </PageFrame>
    );
  }
  if (player.isError && !player.data) {
    return (
      <PageFrame>
        {isNotFound(player.error) ? (
          <EmptyState
            art="lost"
            title={t("playerNotFoundTitle")}
            action={
              <Link href={`/games/${id}?tab=players`} className="text-meta font-semibold text-denim hover:underline">
                {t("allPlayersLink")}
              </Link>
            }
          >
            {t("playerNotFound")}
          </EmptyState>
        ) : (
          <UnavailableState onRetry={() => player.refetch()} />
        )}
      </PageFrame>
    );
  }
  return <PlayerDetailView id={id} p={player.data} />;
}

function PlayerDetailView({ id, p }: { id: string; p: PlayerDetail }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const now = useNow();
  const sessions = useSessions(id, 1, p.playerKey);
  const allSessions = useSessionsSample(id, p.playerKey);

  const days = useMemo(
    () => (allSessions ? playtimeByDay(allSessions, 14, now || Date.now()) : []),
    [allSessions, now],
  );

  return (
    <PageFrame width="wide">
      <PageHeader
        title={p.playerName}
        back={{ href: `/games/${id}?tab=players`, label: t("allPlayersLink") }}
        leading={
          p.linkedPerson ? (
            <PersonAvatar id={p.linkedPerson.personId} name={p.linkedPerson.displayName} size={56} />
          ) : (
            <span className="grid size-14 place-items-center rounded-full bg-hoodie text-[1.25rem] font-semibold text-ink-soft">
              {p.playerName.slice(0, 1).toUpperCase()}
            </span>
          )
        }
        description={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <Badge tone={p.online ? "success" : "neutral"} icon={<Dot tone={p.online ? "success" : "muted"} live={p.online} />}>
              {p.online ? t("onlineNow") : now ? t("seen", { when: formatRelative(p.lastSeenAt, locale, now) }) : t("offline")}
            </Badge>
            {p.linkedPerson ? (
              <Link
                href={`/people/${p.linkedPerson.personId}`}
                className="inline-flex items-center gap-1 text-meta font-semibold text-denim hover:underline"
                style={personStyle(p.linkedPerson.personId)}
              >
                {t("linkedPerson", { name: p.linkedPerson.displayName })}
                <ArrowRight className="size-3.5" aria-hidden="true" />
              </Link>
            ) : null}
          </span>
        }
      />

      <div className="stagger grid gap-4 lg:gap-6">
        <Panel>
          <PanelBody className="pt-5">
            <StatGrid className="sm:grid-cols-3 lg:grid-cols-6">
              <Stat label={t("playtime")} value={formatHours(p.totalPlaytimeMs, locale)} />
              <Stat label={t("sessions")} value={formatNumber(p.sessionCount, locale)} />
              <Stat label={t("longest")} value={formatDuration(p.longestSessionMs, locale)} />
              <Stat label={t("deaths")} value={formatNumber(p.deaths, locale)} />
              <Stat label={t("firstSeen")} value={formatDate(p.firstSeenAt, locale)} mono={false} />
              <Stat label={t("lastSeen")} value={formatDate(p.lastSeenAt, locale)} mono={false} />
            </StatGrid>
          </PanelBody>
        </Panel>

        <div className="grid items-start gap-4 lg:grid-cols-2 lg:gap-6">
          <Panel>
            <PanelHeader title={t("playtimeDays")} />
            <PanelBody>
              {allSessions === null ? (
                <Skeleton className="h-[140px]" />
              ) : days.some((d) => d.ms > 0) ? (
                <Columns
                  label={t("playtimeDays")}
                  data={days.map((d, i) => {
                    const ts = d.day * 86_400_000 + 12 * 3_600_000;
                    return {
                      key: String(d.day),
                      value: d.ms,
                      tick: i % 2 === 1 ? "" : formatWeekday(ts, locale, "short"),
                      title: `${formatWeekdayDate(ts, locale)}: ${formatHours(d.ms, locale)}`,
                    };
                  })}
                />
              ) : (
                <p className="text-body text-ink-soft">{t("noRecentPlay")}</p>
              )}
            </PanelBody>
          </Panel>

          <Panel>
            <PanelHeader title={t("recentSessions")} />
            {sessions.data?.data.length ? (
              <ul className="divide-y divide-hairline border-t border-hairline">
                {sessions.data.data.slice(0, 8).map((s) => (
                  <li key={s.id} className="flex items-center gap-3 px-4 py-2.5 sm:px-5">
                    <span className="min-w-0 flex-1 text-meta text-ink">{formatWeekdayDate(s.joinedAt, locale)}</span>
                    <span className="font-mono text-meta text-ink-soft tnum">
                      {formatTime(s.joinedAt, locale)}–{s.leftAt ? formatTime(s.leftAt, locale) : ""}
                    </span>
                    <span className="w-16 text-right font-mono text-meta text-ink tnum">
                      {s.leftAt === null ? t("now") : formatDuration(s.durationMs ?? 0, locale)}
                    </span>
                  </li>
                ))}
              </ul>
            ) : sessions.isPending ? (
              <PanelBody>
                <Skeleton className="h-32" />
              </PanelBody>
            ) : (
              <PanelBody>
                <p className="text-body text-ink-soft">{t("noSessions")}</p>
              </PanelBody>
            )}
          </Panel>
        </div>

        {p.save ? (
          <Character save={p.save} history={p.vitalsHistory ?? []} />
        ) : (
          <Panel>
            <EmptyState compact>{t("noSave")}</EmptyState>
          </Panel>
        )}

        <div className="grid items-start gap-4 lg:grid-cols-2 lg:gap-6">
          <Panel>
            <PanelHeader title={t("combat")} />
            <PanelBody>
              {p.save?.killCounts.length ? (
                <BarList
                  label={t("combat")}
                  data={[...p.save.killCounts]
                    .sort((a, b) => b.count - a.count)
                    .slice(0, 10)
                    .map((k) => ({
                      key: k.id,
                      label: <span className={k.known ? "" : "font-mono text-meta text-ink-muted"}>{refName(k, locale)}</span>,
                      value: k.count,
                      display: formatNumber(k.count, locale),
                    }))}
                />
              ) : (
                <p className="text-body text-ink-soft">{t("noKills")}</p>
              )}
            </PanelBody>
          </Panel>
          <Panel>
            <PanelHeader title={t("deathCauses")} />
            <PanelBody>
              {p.deathCauses.length ? (
                <BarList
                  label={t("deathCauses")}
                  data={p.deathCauses.map((c) => ({
                    key: c.cause,
                    label: (
                      <span className="flex items-center gap-2">
                        <Skull className="size-3.5 shrink-0 text-ink-muted" aria-hidden="true" />
                        {localized(c.cause, c.causeZhHant, locale)}
                      </span>
                    ),
                    value: c.count,
                    display: formatNumber(c.count, locale),
                  }))}
                />
              ) : (
                <p className="text-body text-ink-soft">{t("noDeaths")}</p>
              )}
            </PanelBody>
          </Panel>
        </div>

        {p.skillProgress && p.skillProgress.skills.length ? (
          <Panel>
            <PanelHeader
              title={t("learning")}
              description={t("learningLede", { days: p.skillProgress.days, xp: formatNumber(p.skillProgress.totalXp, locale) })}
            />
            <PanelBody>
              <BarList
                label={t("learning")}
                data={[...p.skillProgress.skills]
                  .sort((a, b) => b.xp - a.xp)
                  .slice(0, 10)
                  .map((s) => ({
                    key: String(s.index),
                    label: localized(s.en, s.zhHant, locale),
                    value: s.xp,
                    display: t("xp", { xp: formatNumber(s.xp, locale) }),
                  }))}
              />
            </PanelBody>
          </Panel>
        ) : null}
      </div>
    </PageFrame>
  );
}

/** Up to 200 of a player's sessions, for the per-day chart (the page's own
 * list reads the first page separately and stays small). */
function useSessionsSample(id: string, playerKey: string) {
  const q = useSessionsBig(id, playerKey);
  return q.data ? q.data.data : q.isError ? [] : null;
}

function useSessionsBig(id: string, playerKey: string) {
  return useQuery({
    queryKey: [...qk.sessions(id, 1, playerKey), "sample"],
    queryFn: ({ signal }) => fetchServerSessions(id, { page: 1, pageSize: 200, playerKey }, signal),
    staleTime: 60_000,
  });
}

// ------------------------------------------------------------ character

const LIMBS = ["head", "torso", "leftArm", "rightArm", "leftLeg", "rightLeg"] as const;
const VITALS = ["hunger", "thirst", "sanity", "fatigue", "continence", "radiation", "bodyTemperature"] as const;
type Vital = (typeof VITALS)[number];
/** Higher is worse for these; the meter says so in words. */
const INVERTED = new Set<Vital>(["fatigue", "continence", "radiation"]);
const VITAL_MAX: Partial<Record<Vital, number>> = { radiation: 10 };

function Character({ save, history }: { save: AbioticFactorSave; history: VitalsHistoryPoint[] }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const [vital, setVital] = useState<Vital | "money">("sanity");
  const skills = [...save.skills].sort((a, b) => b.level - a.level || b.xp - a.xp);

  const series = history
    .map((h) => ({
      t: h.savedAt,
      v: vital === "money" ? h.money : (h.survival?.[vital] as number | undefined),
    }))
    .filter((x): x is { t: number; v: number } => typeof x.v === "number");

  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-6">
      <div className="grid gap-4 lg:gap-6">
        <Panel>
          <PanelHeader
            title={t("character")}
            description={t("savedAt", { when: formatDateTime(save.savedAt, locale) })}
          />
          <PanelBody className="grid gap-4">
            <StatGrid className="sm:grid-cols-3">
              <Stat label={t("job")} value={localized(save.job.en, save.job.zhHant, locale)} mono={false} />
              <Stat label={t("money")} value={formatNumber(save.money, locale)} />
              <Stat
                label={t("location")}
                value={<span className={save.location.known ? "" : "font-mono text-meta text-ink-muted"}>{refName(save.location, locale)}</span>}
                mono={false}
              />
              <Stat label={t("crafted")} value={formatNumber(save.collections.crafted, locale)} />
              <Stat label={t("pickedUp")} value={formatNumber(save.collections.pickedUp, locale)} />
              <Stat label={t("fish")} value={formatNumber(save.collections.fish, locale)} />
            </StatGrid>
            {save.traits.length ? (
              <div className="grid gap-2">
                <h3 className="text-label font-medium text-ink-muted">{t("traits")}</h3>
                <ul className="flex flex-wrap gap-1.5">
                  {save.traits.map((tr) => (
                    <li key={tr.id}>
                      <Badge
                        tone={tr.negative ? "outline" : "neutral"}
                        icon={tr.negative ? <Minus aria-hidden="true" /> : undefined}
                        title={tr.negative ? t("negativeTrait") : undefined}
                      >
                        {localized(tr.en, tr.zhHant, locale)}
                      </Badge>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </PanelBody>
        </Panel>

        <Panel>
          <PanelHeader title={t("skills")} />
          <ul className="divide-y divide-hairline border-t border-hairline">
            {skills.map((s) => (
              <li key={s.index} className="grid gap-1.5 px-4 py-2.5 sm:px-5">
                <span className="flex items-baseline justify-between gap-3">
                  <span className="truncate text-body text-ink">{localized(s.en, s.zhHant, locale)}</span>
                  <span className="shrink-0 font-mono text-meta text-ink-soft tnum">
                    {t("level", { level: s.level })}
                  </span>
                </span>
                <Meter value={s.progress} label={t("levelProgress", { skill: localized(s.en, s.zhHant, locale) })} />
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="grid gap-4 lg:gap-6">
        <Panel>
          <PanelHeader title={t("body")} />
          <PanelBody className="grid gap-5">
            <div className="grid grid-cols-2 gap-x-5 gap-y-3">
              {LIMBS.map((l) => (
                <div key={l} className="grid gap-1.5">
                  <span className="flex justify-between text-meta">
                    <span className="text-ink-soft">{t(`limb.${l}`)}</span>
                    <span className="font-mono text-ink tnum">{Math.round(save.limbHealth[l])}%</span>
                  </span>
                  <Meter value={save.limbHealth[l]} max={100} label={t(`limb.${l}`)} tone={save.limbHealth[l] < 35 ? "caution" : "denim"} />
                </div>
              ))}
            </div>
            <div className="grid gap-3 border-t border-hairline pt-4">
              {VITALS.filter((v) => save.survival[v] !== undefined).map((v) => {
                const value = save.survival[v] as number;
                const max = VITAL_MAX[v] ?? 100;
                const bad = INVERTED.has(v) ? value / max > 0.75 : value / max < 0.25;
                return (
                  <div key={v} className="grid gap-1.5">
                    <span className="flex justify-between text-meta">
                      <span className="text-ink-soft">
                        {t(`vital.${v}`)}
                        {INVERTED.has(v) ? <span className="text-ink-muted"> · {t("lowerIsBetter")}</span> : null}
                      </span>
                      <span className="font-mono text-ink tnum">{formatNumber(value, locale, v === "bodyTemperature" ? 1 : 0)}</span>
                    </span>
                    <Meter value={Math.min(value, max)} max={max} label={t(`vital.${v}`)} tone={bad ? "caution" : "denim"} />
                  </div>
                );
              })}
            </div>
          </PanelBody>
        </Panel>

        {history.length > 1 ? (
          <Panel>
            <PanelHeader title={t("vitalsOverTime")} />
            <PanelBody className="grid gap-3">
              <div className="scrollbar-none -mx-1 overflow-x-auto px-1">
                <Segmented
                  size="sm"
                  label={t("measure")}
                  value={vital}
                  onChange={setVital}
                  options={[
                    ...(["sanity", "hunger", "thirst", "fatigue"] as const).map((v) => ({ value: v, label: t(`vital.${v}`) })),
                    { value: "money" as const, label: t("money") },
                  ]}
                />
              </div>
              {series.length > 1 ? (
                <TimeChart
                  points={series}
                  label={vital === "money" ? t("money") : t(`vital.${vital}`)}
                  formatValue={(v) => formatNumber(v, locale)}
                  formatTime={(ts) => formatDateTime(ts, locale)}
                  formatTick={(ts) => formatDate(ts, locale)}
                />
              ) : (
                <p className="text-body text-ink-soft">{t("noHistory")}</p>
              )}
            </PanelBody>
          </Panel>
        ) : null}

        {save.equipment.length ? (
          <Panel>
            <PanelHeader title={t("equipment")} />
            <ul className="divide-y divide-hairline border-t border-hairline">
              {save.equipment.map((e, i) => (
                <li key={`${e.id}-${i}`} className="grid gap-1.5 px-4 py-2.5 sm:px-5">
                  <span className="flex items-baseline justify-between gap-3">
                    <span className={e.known ? "truncate text-body text-ink" : "truncate font-mono text-meta text-ink-muted"}>
                      {refName(e, locale)}
                    </span>
                    <span className="shrink-0 font-mono text-meta text-ink-soft tnum">{Math.round(e.durability * 100)}%</span>
                  </span>
                  <Meter value={e.durability} label={t("durability")} tone={e.durability < 0.2 ? "caution" : "denim"} />
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}
      </div>
    </div>
  );
}
