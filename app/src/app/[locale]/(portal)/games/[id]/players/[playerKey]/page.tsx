"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { ArrowLeft, Clock, Skull, User } from "lucide-react";
import { Link } from "@/i18n/routing";
import {
  fetchServerPlayer,
  fetchServerSessions,
  type PlayerDetail,
  type Session,
} from "@/lib/games-schema";
import {
  formatDateTime,
  formatDurationMs,
  formatPercent,
  formatRelativeTime,
} from "@/lib/games-format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { StatTile } from "@/components/ui/stat-tile";
import { BarList, type BarListItem } from "@/components/ui/bar-list";
import { Meter } from "@/components/ui/meter";
import { BarSeries } from "@/components/ui/mini-chart";
import { PersonAvatar } from "@/components/ui/person-avatar";

const SESSIONS_SAMPLE = 200;
const RECENT_SESSIONS = 12;
const DAILY_PLAYTIME_DAYS = 14;

export default function PlayerProfilePage() {
  const params = useParams<{ id: string; playerKey: string }>();
  const { id } = params;
  // `useParams()`'s decoding of a `:`-bearing dynamic segment isn't
  // reliable across Next.js's client-router paths — decode defensively
  // (a no-op if it's already decoded) so this never double-encodes on the
  // way back out through fetchServerPlayer/fetchServerSessions.
  const playerKey = decodeURIComponent(params.playerKey);
  const t = useTranslations("games");
  const locale = useLocale();
  const [player, setPlayer] = useState<PlayerDetail | null>(null);
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      fetchServerPlayer(id, playerKey),
      fetchServerSessions(id, { playerKey, pageSize: SESSIONS_SAMPLE }).catch(() => null),
    ])
      .then(([p, s]) => {
        if (cancelled) return;
        setPlayer(p);
        setSessions(s?.data ?? []);
        setError(false);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [id, playerKey]);

  return (
    <div>
      <Link
        href={`/games/${id}/players`}
        className="mb-6 inline-flex items-center gap-1.5 font-mono text-[11px] uppercase tracking-wide text-text-muted hover:text-text-secondary"
      >
        <ArrowLeft className="h-3 w-3" />
        {t("back")}
      </Link>

      {error && <Alert variant="destructive">{t("detailError")}</Alert>}
      {!error && !player && <p className="text-text-muted">{t("loading")}</p>}

      {player && (
        <>
          <div className="mb-6 flex items-center gap-3">
            <PersonAvatar seed={player.playerKey} size={40} className="rounded-full overflow-hidden" />
            <div className="flex flex-col gap-0.5">
              <div className="flex items-center gap-2.5">
                <h1 className="text-[24px] font-medium tracking-tight">{player.playerName}</h1>
                <Badge variant={player.online ? "live" : "designed"}>
                  {player.online ? t("online") : t("offline")}
                </Badge>
              </div>
              {player.linkedPerson && (
                <p className="font-mono text-[11px] text-text-muted">
                  {t("linkedPerson", { name: player.linkedPerson.displayName })}
                </p>
              )}
            </div>
          </div>

          <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile icon={User} label={t("sessions")} value={String(player.sessionCount)} />
            <StatTile
              icon={Clock}
              label={t("playtime")}
              value={formatDurationMs(player.totalPlaytimeMs, locale)}
            />
            <StatTile icon={Skull} label={t("deaths")} value={String(player.deaths)} />
            <StatTile
              icon={Clock}
              label={t("lastSeen")}
              value={formatRelativeTime(player.lastSeenAt, locale)}
            />
          </div>

          {player.save ? (
            <>
              <CharacterSheet player={player} />
              <VitalsCard player={player} />
              <CombatRecord player={player} />
            </>
          ) : (
            <Card className="mb-6">
              <CardContent className="p-4">
                <p className="text-[13px] text-text-muted">{t("noSaveYet")}</p>
              </CardContent>
            </Card>
          )}

          <Card className="mb-6">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                {t("deathCausesLabel")}
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <BarList
                items={player.deathCauses.map((d): BarListItem => ({
                  key: d.cause,
                  label: locale.startsWith("zh") ? d.causeZhHant : d.cause,
                  value: d.count,
                  valueLabel: String(d.count),
                }))}
                emptyLabel={t("noDeathsYet")}
                showMoreLabel={(n) => t("showMore", { count: n })}
                showLessLabel={t("showLess")}
              />
            </CardContent>
          </Card>

          {sessions && sessions.length > 0 && (
            <SessionCharts sessions={sessions} locale={locale} />
          )}

          {sessions && sessions.length > 0 && (
            <Card>
              <CardHeader className="p-4 pb-2">
                <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
                  {t("recentSessionsLabel")}
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <ul className="divide-y divide-border">
                  {sessions.slice(0, RECENT_SESSIONS).map((s) => (
                    <li key={s.id} className="flex items-center gap-3 p-3.5">
                      {s.leftAt === null ? (
                        <Badge variant="live">{t("online")}</Badge>
                      ) : (
                        <span className="font-mono text-[11px] text-text-muted">
                          {formatDurationMs(s.durationMs ?? 0, locale)}
                        </span>
                      )}
                      <span className="ml-auto shrink-0 whitespace-nowrap font-mono text-[10px] text-text-muted">
                        {formatDateTime(s.joinedAt, locale)}
                      </span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function CharacterSheet({ player }: { player: PlayerDetail }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const save = player.save;
  if (!save) return null;
  const isZhHant = locale.startsWith("zh");

  return (
    <Card className="mb-6">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
          {t("characterSheetLabel")}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 p-4 pt-0">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="designed">{isZhHant ? save.job.zhHant : save.job.en}</Badge>
          <Badge variant="designed">{isZhHant ? save.location.zhHant : save.location.en}</Badge>
        </div>

        {save.traits.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {save.traits.map((trait) => (
              <span
                key={trait.id}
                className={
                  "rounded-full border px-2 py-0.5 font-mono text-[10px] " +
                  (trait.negative
                    ? "border-danger/30 text-danger"
                    : "border-success/30 text-success")
                }
              >
                {trait.negative ? "−" : "+"} {isZhHant ? trait.zhHant : trait.en}
              </span>
            ))}
          </div>
        )}

        {save.equipment.length > 0 && (
          <div>
            <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wide text-text-muted">
              {t("equipmentLabel")}
            </p>
            <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3">
              {save.equipment.map((item) => (
                <div
                  key={item.id}
                  className="flex items-center justify-between rounded bg-bg-elevated px-2 py-1.5 text-[11px]"
                >
                  <span
                    className={item.known ? "truncate text-text-secondary" : "truncate font-mono text-text-muted"}
                  >
                    {isZhHant ? item.zhHant : item.en}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-text-muted">
                    {formatPercent(item.durability)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {save.skills.length > 0 && (
          <div>
            <p className="mb-1.5 font-mono text-[10px] uppercase tracking-wide text-text-muted">
              {t("skillsLabel")}
            </p>
            <BarList
              items={[...save.skills]
                .sort((a, b) => b.xp - a.xp)
                .map((skill): BarListItem => ({
                  key: String(skill.index),
                  label: `${isZhHant ? skill.zhHant : skill.en} (Lv.${skill.level})`,
                  value: skill.xp,
                  valueLabel: `${skill.xp.toFixed(0)} xp`,
                }))}
              emptyLabel={t("noSkillsYet")}
              showMoreLabel={(n) => t("showMore", { count: n })}
              showLessLabel={t("showLess")}
            />
          </div>
        )}
      </CardContent>
    </Card>
  );
}

const LIMB_LABEL_KEY = {
  head: "limbHead", torso: "limbTorso", leftArm: "limbLeftArm",
  rightArm: "limbRightArm", leftLeg: "limbLeftLeg", rightLeg: "limbRightLeg",
} as const;

function VitalsCard({ player }: { player: PlayerDetail }) {
  const t = useTranslations("games");
  const save = player.save;
  if (!save) return null;

  return (
    <Card className="mb-6">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
          {t("vitalsLabel")}
        </CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-5 p-4 pt-0">
        <div>
          <p className="mb-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
            {t("limbHealthLabel")}
          </p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-3">
            {(Object.keys(LIMB_LABEL_KEY) as (keyof typeof LIMB_LABEL_KEY)[]).map((limb) => (
              <Meter
                key={limb}
                label={t(LIMB_LABEL_KEY[limb])}
                value={save.limbHealth[limb]}
                valueLabel={`${Math.round(save.limbHealth[limb])}%`}
              />
            ))}
          </div>
        </div>
        <div>
          <p className="mb-2 font-mono text-[10px] uppercase tracking-wide text-text-muted">
            {t("survivalLabel")}
          </p>
          <div className="grid grid-cols-2 gap-x-4 gap-y-2.5 sm:grid-cols-3">
            <Meter label={t("vitalHunger")} value={Math.min(100, save.survival.hunger)} />
            <Meter label={t("vitalThirst")} value={Math.min(100, save.survival.thirst)} />
            <Meter label={t("vitalSanity")} value={save.survival.sanity} />
            <Meter label={t("vitalFatigue")} value={save.survival.fatigue} invert />
            <Meter label={t("vitalContinence")} value={save.survival.continence} invert />
            {save.survival.radiation !== undefined && (
              <Meter label={t("vitalRadiation")} value={save.survival.radiation} max={10} invert />
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function CombatRecord({ player }: { player: PlayerDetail }) {
  const t = useTranslations("games");
  const locale = useLocale();
  const save = player.save;
  if (!save || save.killCounts.length === 0) return null;
  const isZhHant = locale.startsWith("zh");

  return (
    <Card className="mb-6">
      <CardHeader className="p-4 pb-2">
        <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
          {t("combatRecordLabel")}
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <BarList
          items={save.killCounts.map((k): BarListItem => ({
            key: k.id,
            label: isZhHant ? k.zhHant : k.en,
            value: k.count,
            valueLabel: String(k.count),
            unknown: !k.known,
          }))}
          emptyLabel={t("noKillsYet")}
          showMoreLabel={(n) => t("showMore", { count: n })}
          showLessLabel={t("showLess")}
        />
      </CardContent>
    </Card>
  );
}

function SessionCharts({ sessions, locale }: { sessions: Session[]; locale: string }) {
  const t = useTranslations("games");

  const dailyMs = new Map<string, number>();
  const now = Date.now();
  for (let i = DAILY_PLAYTIME_DAYS - 1; i >= 0; i--) {
    const day = new Date(now - i * 86_400_000).toISOString().slice(0, 10);
    dailyMs.set(day, 0);
  }
  for (const s of sessions) {
    const day = new Date(s.joinedAt).toISOString().slice(0, 10);
    if (!dailyMs.has(day)) continue;
    const duration = s.durationMs ?? Math.max(0, now - s.joinedAt);
    dailyMs.set(day, (dailyMs.get(day) ?? 0) + duration);
  }
  const dailyValues = Array.from(dailyMs.values()).map((ms) => ms / 3_600_000);

  const recentSessions = sessions.slice(0, 30).reverse();
  const sessionLengths = recentSessions.map((s) =>
    (s.durationMs ?? Math.max(0, now - s.joinedAt)) / 3_600_000,
  );
  const mutedIndexes = new Set(
    recentSessions.map((s, i) => (s.leftAt === null ? i : -1)).filter((i) => i >= 0),
  );

  return (
    <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2">
      <Card>
        <CardHeader className="p-4 pb-2">
          <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
            {t("playtimePerDayLabel")}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          <BarSeries values={dailyValues} />
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="p-4 pb-2">
          <CardTitle className="font-mono text-[10px] font-normal uppercase tracking-wider text-text-muted">
            {t("sessionLengthsLabel")}
          </CardTitle>
        </CardHeader>
        <CardContent className="p-4 pt-0">
          <BarSeries values={sessionLengths} mutedIndexes={mutedIndexes} />
        </CardContent>
      </Card>
    </div>
  );
}
