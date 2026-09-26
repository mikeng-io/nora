// Reads Nora's real /app/v1 API from where the portal runs and checks each
// answer against the portal's own schemas (src/lib/api/*.ts). Prints only
// the endpoint, PASS/FAIL/ERROR and, on FAIL, the paths of the fields that
// did not match: never a value. Real answers carry people's positions and
// memory, and they never leave the deploy host.
//
// Build:  npx esbuild scripts/check-live-schemas.ts --bundle --platform=node --format=esm --outfile=/tmp/check-live-schemas.mjs
// Run on the host, beside Nora:  docker compose exec -T app node - < /tmp/check-live-schemas.mjs
//   env NORA_API_BASE (default http://nora:9100), CHECK_PERSON (a person_id for
//   the self-scoped reads) or CHECK_PERSON_NAME (the start of a display name,
//   resolved through /persons); else the roster's first person. The id is
//   never printed.
import type { z } from "zod";
import * as games from "../src/lib/api/games";
import * as nora from "../src/lib/api/nora";
import * as people from "../src/lib/api/people";
import * as presence from "../src/lib/api/presence";
import { z as zod } from "zod";

const BASE = process.env.NORA_API_BASE ?? "http://nora:9100";
let PERSON = process.env.CHECK_PERSON ?? "";

type Check = { path: string; schema: z.ZodTypeAny; optional404?: boolean; knownGames?: string[] };

async function get(path: string): Promise<{ status: number; body: unknown }> {
  const res = await fetch(`${BASE}/app/v1${path}`, { signal: AbortSignal.timeout(20_000) });
  let body: unknown = null;
  try {
    body = await res.json();
  } catch {
    body = undefined;
  }
  return { status: res.status, body };
}

async function run(c: Check): Promise<string> {
  try {
    const { status, body } = await get(c.path);
    if (status === 404 && c.optional404) return `SKIP  ${c.path} (404, not applicable)`;
    if (status !== 200) return `ERROR ${c.path} HTTP ${status}`;
    const r = c.schema.safeParse(body);
    if (r.success) return `PASS  ${c.path}`;
    // A world the portal has no view for is shown as such, not failed.
    const game = body && typeof body === "object" ? (body as { game?: unknown }).game : undefined;
    if (c.knownGames && typeof game === "string" && !c.knownGames.includes(game)) {
      return `SKIP  ${c.path} (no world view for game "${game}")`;
    }
    const paths = [...new Set(r.error.issues.map((i) => `${i.path.join(".")}: ${i.code}`))].slice(0, 8);
    return `FAIL  ${c.path}\n        ${paths.join("\n        ")}`;
  } catch (e) {
    return `ERROR ${c.path} ${(e as Error).name}`;
  }
}

async function main() {
  const lines: string[] = [];
  // The self-scoped reads need someone real: a name given, else the first
  // person on the roster. Either way the id is masked in the output.
  if (!PERSON) {
    const wanted = process.env.CHECK_PERSON_NAME?.toLowerCase();
    const r = (await get("/persons")).body as Array<{ personId: string; displayName: string }> | null;
    PERSON = (wanted ? r?.find((p) => p.displayName.toLowerCase().startsWith(wanted)) : r?.[0])?.personId ?? "";
    lines.push(PERSON ? "PASS  resolved a viewer from /persons" : "ERROR no viewer to check the self-scoped reads as");
  }
  const q = (p: string) => (PERSON ? `${p}?personId=${encodeURIComponent(PERSON)}` : p);
  const shown = (path: string) => (PERSON ? path.replaceAll(encodeURIComponent(PERSON), "<viewer>") : path);
  const base: Check[] = [
    { path: "/status", schema: nora.StatusSchema },
    { path: q("/memory"), schema: nora.MemorySchema },
    { path: "/doctrine", schema: nora.DoctrineSchema },
    { path: "/skills", schema: nora.SkillsSchema },
    { path: "/tools", schema: nora.ToolsSchema },
    { path: "/persons", schema: people.RosterSchema },
    { path: q("/events"), schema: people.PlansSchema },
    { path: "/presence", schema: zod.array(presence.PresenceEntrySchema) },
    { path: "/presence/places", schema: zod.array(presence.SavedPlaceSchema) },
    { path: "/presence/analytics/places", schema: presence.DerivedPlacesSchema },
    { path: "/games/servers", schema: zod.array(games.ServerSchema) },
  ];
  for (const c of base) lines.push(await run(c));

  // Follow the lists one step, so every schema meets a real answer.
  const roster = (await get("/persons")).body as Array<{ personId: string }> | null;
  const firstPerson = PERSON || roster?.[0]?.personId;
  if (firstPerson) lines.push(await run({ path: `/persons/${firstPerson}`, schema: people.PersonProfileSchema }));

  const pres = (await get("/presence")).body as Array<{ personId: string }> | null;
  const subject = pres?.[0]?.personId;
  if (subject) {
    lines.push(await run({ path: `/presence/${subject}/history?hours=24`, schema: presence.HistorySchema }));
    lines.push(await run({ path: `/presence/analytics/rhythm?subjectId=${subject}&days=30`, schema: presence.RhythmSchema }));
    lines.push(await run({ path: `/presence/analytics/routes?subjectId=${subject}&days=30`, schema: presence.RoutesSchema }));
  }

  const plans = (await get(q("/events"))).body as Array<{ id: string }> | null;
  if (PERSON && plans?.[0]) {
    lines.push(await run({ path: `/events/${plans[0].id}?personId=${encodeURIComponent(PERSON)}`, schema: people.PlanDetailSchema }));
  }

  const servers = ((await get("/games/servers")).body as Array<{ id: string; current: unknown }> | null) ?? [];
  const status = zod.union([games.ServerStatusSchema, games.LocalSaveStatusSchema]);
  const world = zod.union([games.WorldStateSchema, games.BigWalkWorldSchema]);
  for (const s of servers) {
    const id = encodeURIComponent(s.id);
    const local = s.current === null;
    lines.push(await run({ path: `/games/servers/${id}/status`, schema: status }));
    lines.push(await run({ path: `/games/servers/${id}/world`, schema: world, optional404: true, knownGames: ["big-walk"] }));
    lines.push(await run({ path: `/games/servers/${id}/activity`, schema: games.ActivityPageSchema, optional404: true }));
    if (local) {
      lines.push(await run({ path: `/games/servers/${id}/progress`, schema: games.GameProgressSchema, optional404: true }));
      lines.push(await run({ path: `/games/servers/${id}/save-history?hours=168`, schema: games.SaveHistorySchema, optional404: true }));
      continue;
    }
    lines.push(await run({ path: `/games/servers/${id}/graph?hours=24`, schema: games.GraphSchema, optional404: true }));
    lines.push(await run({ path: `/games/servers/${id}/weekly`, schema: games.WeeklySummarySchema, optional404: true }));
    lines.push(await run({ path: `/games/servers/${id}/insights?days=30`, schema: games.InsightsSchema, optional404: true }));
    lines.push(await run({ path: `/games/servers/${id}/sessions`, schema: games.SessionsPageSchema, optional404: true }));
    const players = (await get(`/games/servers/${id}/players`)).body as { data?: Array<{ playerKey: string }> } | null;
    lines.push(await run({ path: `/games/servers/${id}/players`, schema: games.PlayersPageSchema, optional404: true }));
    const key = players?.data?.[0]?.playerKey;
    if (key) lines.push(await run({ path: `/games/servers/${id}/players/${encodeURIComponent(key)}`, schema: games.PlayerDetailSchema }));
  }

  console.log(lines.map(shown).join("\n"));
  const failed = lines.filter((l) => l.startsWith("FAIL") || l.startsWith("ERROR")).length;
  console.log(`\n${lines.length} checked, ${failed} not passing`);
  process.exitCode = failed ? 1 : 0;
}

void main();
