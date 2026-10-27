"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import * as artifacts from "./api/artifacts";
import * as documents from "./api/documents";
import * as downloads from "./api/downloads";
import * as games from "./api/games";
import * as nora from "./api/nora";
import * as people from "./api/people";
import * as presence from "./api/presence";

// Every read the portal makes, with how fresh it has to be. Live things
// (where people are, who is playing) poll while the page is visible and
// stop when it is hidden; the rest refresh on focus. Every result is kept
// on the device (providers.tsx persists the cache) so a page opened offline
// shows what it last knew, with that time on it.
const S = 1000;
const M = 60 * S;

/** Keep showing the last answer while a new one loads, but only for the
 * same subject (another window or page of it): never one person's route or
 * week drawn under another person's name while theirs arrives, never one
 * server's players under another's. `at` is the key position naming the
 * subject; every subject-keyed read below puts it at 1. */
export function keepFor(subject: string, at = 1) {
  return <T,>(previous: T | undefined, previousQuery?: { queryKey: readonly unknown[] }): T | undefined =>
    previousQuery?.queryKey[at] === subject ? previous : undefined;
}

export const qk = {
  status: ["status"] as const,
  memory: ["memory"] as const,
  doctrine: ["doctrine"] as const,
  skills: ["skills"] as const,
  tools: ["tools"] as const,
  roster: ["roster"] as const,
  person: (id: string) => ["person", id] as const,
  plans: ["plans"] as const,
  documents: (page: number, state: documents.DocumentFilter = "active", search = "") =>
    ["documents", page, state, search] as const,
  document: (id: string) => ["document", id] as const,
  artifacts: (page: number, search = "") => ["artifacts", page, search] as const,
  artifact: (id: string) => ["artifact", id] as const,
  downloads: ["downloads"] as const,
  plan: (id: string) => ["plan", id] as const,
  presence: ["presence"] as const,
  savedPlaces: ["saved-places"] as const,
  history: (id: string, hours: number) => ["history", id, hours] as const,
  derivedPlaces: ["derived-places"] as const,
  rhythm: (id: string, days: number) => ["rhythm", id, days] as const,
  routes: (id: string, days: number) => ["routes", id, days] as const,
  servers: ["servers"] as const,
  server: (id: string) => ["server", id] as const,
  graph: (id: string, hours: number) => ["server", id, "graph", hours] as const,
  weekly: (id: string) => ["server", id, "weekly"] as const,
  world: (id: string) => ["server", id, "world"] as const,
  progress: (id: string) => ["server", id, "progress"] as const,
  saveHistory: (id: string, hours: number) => ["server", id, "save-history", hours] as const,
  insights: (id: string, days: number) => ["server", id, "insights", days] as const,
  players: (id: string, page: number, online?: boolean) =>
    ["server", id, "players", page, online ?? "all"] as const,
  player: (id: string, key: string) => ["server", id, "player", key] as const,
  activity: (id: string, page: number) => ["server", id, "activity", page] as const,
  sessions: (id: string, page: number, playerKey?: string) =>
    ["server", id, "sessions", page, playerKey ?? "all"] as const,
};

// ------------------------------------------------------------------ nora

export const useStatus = () =>
  useQuery({
    queryKey: qk.status,
    queryFn: ({ signal }) => nora.fetchStatus(signal),
    refetchInterval: 30 * S,
  });

export const useMemory = () =>
  useQuery({
    queryKey: qk.memory,
    queryFn: ({ signal }) => nora.fetchMemory(signal),
    staleTime: 5 * M,
  });

export const useDoctrine = () =>
  useQuery({
    queryKey: qk.doctrine,
    queryFn: ({ signal }) => nora.fetchDoctrine(signal),
    staleTime: 10 * M,
  });

export const useSkills = () =>
  useQuery({
    queryKey: qk.skills,
    queryFn: ({ signal }) => nora.fetchSkills(signal),
    staleTime: 10 * M,
  });

export const useTools = () =>
  useQuery({
    queryKey: qk.tools,
    queryFn: ({ signal }) => nora.fetchTools(signal),
    staleTime: 10 * M,
  });

// ---------------------------------------------------------------- people

export const useRoster = () =>
  useQuery({
    queryKey: qk.roster,
    queryFn: ({ signal }) => people.fetchRoster(signal),
    staleTime: 10 * M,
  });

export const usePerson = (id: string) =>
  useQuery({
    queryKey: qk.person(id),
    queryFn: ({ signal }) => people.fetchPerson(id, signal),
    staleTime: 5 * M,
  });

export const usePlans = () =>
  useQuery({
    queryKey: qk.plans,
    queryFn: ({ signal }) => people.fetchPlans(signal),
    staleTime: 1 * M,
  });

export const usePlan = (id: string) =>
  useQuery({
    queryKey: qk.plan(id),
    queryFn: ({ signal }) => people.fetchPlan(id, signal),
    staleTime: 1 * M,
  });

// ------------------------------------------------------------- artifacts

export const useArtifacts = (page: number, search = "") =>
  useQuery({
    queryKey: qk.artifacts(page, search),
    queryFn: ({ signal }) => artifacts.fetchArtifacts(page, search, signal),
    staleTime: 1 * M,
    // A new page or search keeps the last list on screen until the next arrives; the
    // view says so (`isPlaceholderData`).
    placeholderData: keepPreviousData,
  });

export const useArtifact = (id: string) =>
  useQuery({
    queryKey: qk.artifact(id),
    queryFn: ({ signal }) => artifacts.fetchArtifact(id, signal),
    // A link can be revised by Nora; a minute is fresh enough.
    staleTime: 1 * M,
  });

// ------------------------------------------------------------- downloads

const ACTIVE_DOWNLOAD: downloads.DownloadStatus[] = ["queued", "probing", "downloading"];

export const useDownloads = () =>
  useQuery({
    queryKey: qk.downloads,
    queryFn: ({ signal }) => downloads.fetchDownloads(signal),
    // While anything is queued or moving, poll briskly so the progress bars move;
    // idle, a slow tick keeps a second window honest. No data yet → the slow tick.
    refetchInterval: (query) =>
      query.state.data?.jobs.some((j) => ACTIVE_DOWNLOAD.includes(j.status)) ? 2 * S : 15 * S,
  });

/** One mutation helper: every change invalidates the list so the page always shows
 * what the store now holds, never a guess. */
function useDownloadsMutation<TArgs, TResult = void>(fn: (args: TArgs) => Promise<TResult>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => client.invalidateQueries({ queryKey: qk.downloads }),
  });
}

export const useSubmitDownloads = () =>
  useDownloadsMutation((urls: string[]) => downloads.submitDownloads(urls));

export const useDeleteDownload = () =>
  useDownloadsMutation((id: string) => downloads.deleteDownload(id));

export const useRetryDownload = () =>
  useDownloadsMutation((id: string) => downloads.retryDownload(id));

// ------------------------------------------------------------- documents

export const useDocuments = (page: number, state: documents.DocumentFilter = "active", search = "") =>
  useQuery({
    queryKey: qk.documents(page, state, search),
    queryFn: ({ signal }) => documents.fetchDocuments(page, state, search, signal),
    staleTime: 1 * M,
    // A new page, filter or search keeps the last list on screen until the next arrives.
    // The view must say so (`isPlaceholderData`): an old list is not the new one.
    placeholderData: keepPreviousData,
  });

export const useDocument = (id: string) =>
  useQuery({
    queryKey: qk.document(id),
    queryFn: ({ signal }) => documents.fetchDocument(id, signal),
    // A document is revised by Nora between visits; a minute is fresh enough to read
    // and short enough that a revision she just made is the one you open.
    staleTime: 1 * M,
  });

/** Archive, delete or restore a document. On success every documents list and this
 * document are read again, so what is shown is what Nora now holds, never a guess. */
export const useSetDocumentState = (id: string) => {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (state: documents.DocumentStateChange) => documents.setDocumentState(id, state),
    onSuccess: async (_done, state) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: ["documents"] }),
        // Every cached copy of a document, by id and by a link's handle alike. A deleted
        // one is only marked stale: reading it again now is a 404 that would flash "may be
        // out of date" on the page it is leaving; the next visit reads it fresh.
        client.invalidateQueries({
          queryKey: ["document"],
          refetchType: state === "deleted" ? "none" : "active",
        }),
      ]);
    },
  });
};

// -------------------------------------------------------------- presence

export const PRESENCE_POLL_MS = 10 * S;

export const usePresence = () =>
  useQuery({
    queryKey: qk.presence,
    queryFn: ({ signal }) => presence.fetchPresence(signal),
    refetchInterval: PRESENCE_POLL_MS,
    staleTime: 5 * S,
  });

export const useSavedPlaces = () =>
  useQuery({
    queryKey: qk.savedPlaces,
    queryFn: ({ signal }) => presence.fetchSavedPlaces(signal),
    staleTime: 30 * M,
  });

export const useHistory = (id: string | null, hours: number) =>
  useQuery({
    queryKey: qk.history(id ?? "", hours),
    queryFn: ({ signal }) => presence.fetchHistory(id!, hours, signal),
    enabled: Boolean(id),
    // A short window follows someone who is moving; a long one barely changes.
    refetchInterval: hours <= 6 ? 20 * S : 2 * M,
    placeholderData: keepFor(id ?? ""),
  });

export const useDerivedPlaces = () =>
  useQuery({
    queryKey: qk.derivedPlaces,
    queryFn: ({ signal }) => presence.fetchDerivedPlaces(signal),
    staleTime: 10 * M,
  });

export const useRhythm = (id: string, days: number) =>
  useQuery({
    queryKey: qk.rhythm(id, days),
    queryFn: ({ signal }) => presence.fetchRhythm(id, days, signal),
    staleTime: 10 * M,
    placeholderData: keepFor(id),
  });

export const useRoutes = (id: string, days: number) =>
  useQuery({
    queryKey: qk.routes(id, days),
    queryFn: ({ signal }) => presence.fetchRoutes(id, days, signal),
    staleTime: 10 * M,
    placeholderData: keepFor(id),
  });

// ----------------------------------------------------------------- games

export const useServers = () =>
  useQuery({
    queryKey: qk.servers,
    queryFn: ({ signal }) => games.fetchServers(signal),
    refetchInterval: 20 * S,
  });

export const useServerStatus = (id: string) =>
  useQuery({
    queryKey: qk.server(id),
    queryFn: ({ signal }) => games.fetchServerStatus(id, signal),
    refetchInterval: 15 * S,
  });

export const useServerGraph = (id: string, hours: number, enabled = true) =>
  useQuery({
    queryKey: qk.graph(id, hours),
    queryFn: ({ signal }) => games.fetchServerGraph(id, hours, signal),
    refetchInterval: 2 * M,
    placeholderData: keepFor(id),
    enabled,
  });

export const useWeekly = (id: string, enabled = true) =>
  useQuery({
    queryKey: qk.weekly(id),
    queryFn: ({ signal }) => games.fetchServerWeekly(id, signal),
    staleTime: 5 * M,
    enabled,
  });

export const useWorld = (id: string) =>
  useQuery({
    queryKey: qk.world(id),
    queryFn: ({ signal }) => games.fetchServerWorld(id, signal),
    staleTime: 5 * M,
  });

export const useProgress = (id: string, enabled = true) =>
  useQuery({
    queryKey: qk.progress(id),
    queryFn: ({ signal }) => games.fetchServerProgress(id, signal),
    staleTime: 5 * M,
    enabled,
  });

export const useSaveHistory = (id: string, hours: number, enabled = true) =>
  useQuery({
    queryKey: qk.saveHistory(id, hours),
    queryFn: ({ signal }) => games.fetchServerSaveHistory(id, hours, signal),
    staleTime: 5 * M,
    placeholderData: keepFor(id),
    enabled,
  });

export const useInsights = (id: string, days: number) =>
  useQuery({
    queryKey: qk.insights(id, days),
    queryFn: ({ signal }) => games.fetchServerInsights(id, days, signal),
    staleTime: 5 * M,
    placeholderData: keepFor(id),
  });

export const usePlayers = (id: string, page: number, online?: boolean) =>
  useQuery({
    queryKey: qk.players(id, page, online),
    queryFn: ({ signal }) => games.fetchServerPlayers(id, { page, pageSize: 24, online }, signal),
    refetchInterval: 30 * S,
    placeholderData: keepFor(id),
  });

export const usePlayer = (id: string, key: string) =>
  useQuery({
    queryKey: qk.player(id, key),
    queryFn: ({ signal }) => games.fetchServerPlayer(id, key, signal),
    staleTime: 1 * M,
  });

export const useActivity = (id: string, page: number) =>
  useQuery({
    queryKey: qk.activity(id, page),
    queryFn: ({ signal }) => games.fetchServerActivity(id, { page, pageSize: 30 }, signal),
    refetchInterval: page === 1 ? 30 * S : false,
    placeholderData: keepFor(id),
  });

export const useSessions = (id: string, page: number, playerKey?: string) =>
  useQuery({
    queryKey: qk.sessions(id, page, playerKey),
    queryFn: ({ signal }) =>
      games.fetchServerSessions(id, { page, pageSize: 20, playerKey }, signal),
    staleTime: 30 * S,
    placeholderData: keepFor(id),
  });
