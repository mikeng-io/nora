"use client";

import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import {
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  MessageSquareLock,
  RotateCw,
  XCircle,
} from "lucide-react";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Panel, PanelBody, PanelHeader, Rows } from "@/components/ui/panel";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, UnavailableState } from "@/components/ui/states";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import {
  availableVerbs,
  decisionTransport,
  fetchLinkRequest,
  platformLabel,
  type LinkConsentOutcome,
  type LinkRequestView,
} from "@/lib/api/holder";
import { formatDateTime } from "@/lib/format";

// The consent endpoint the native form navigates to — the same origin, the
// same endpoint JSON clients use. Nothing in the form chooses what gets
// consented: the server re-reads the request and names the account itself.
const CONSENT_ACTION = "/api/v1/holder/link-consent";

const stateTone: Record<LinkRequestView["state"], "caution" | "denim" | "success" | "danger" | "neutral"> = {
  pending: "caution",
  committing: "denim",
  committed: "success",
  refused: "danger",
  cancelled: "neutral",
  expired: "neutral",
};

/** One link request, seen from the side the signed-in account is on. Every
 * word of it is the server's view — the browser only ever named the id. */
export function RequestView({
  requestId,
  managed,
  result,
}: {
  requestId: string;
  managed: boolean;
  result?: LinkConsentOutcome;
}) {
  const t = useTranslations("accountPage");
  const query = useQuery({
    queryKey: ["link-request", requestId],
    queryFn: ({ signal }) => fetchLinkRequest(requestId, signal),
    enabled: managed,
    // A live request keeps asking until somebody answers — or the clock wins.
    refetchInterval: (q) =>
      q.state.data?.status === "ok"
      && ["pending", "committing"].includes(q.state.data.request.state)
        ? 10_000
        : false,
  });

  const header = (
    <PageHeader
      title={t("requestTitle")}
      back={{ href: "/account", label: t("back") }}
      description={t("requestLede")}
      actions={
        query.data?.status === "ok" ? (
          <Button variant="quiet" size="sm" onClick={() => query.refetch()}
            disabled={query.isFetching}>
            <RotateCw aria-hidden="true" className={cn(query.isFetching && "animate-spin")} />
            {t("refresh")}
          </Button>
        ) : null
      }
    />
  );

  if (!managed) {
    return (
      <PageFrame>
        {header}
        <EmptyState>{t("fileMode")}</EmptyState>
      </PageFrame>
    );
  }
  if (query.isPending) {
    return (
      <PageFrame>
        {header}
        <Skeleton className="h-64 rounded-panel" />
      </PageFrame>
    );
  }
  const reply = query.data;
  if (!reply || reply.status !== "ok") {
    return (
      <PageFrame>
        {header}
        {reply?.status === "denied" ? (
          <EmptyState art="lost" title={t("refusedTitle")}>
            {t("refused")}
          </EmptyState>
        ) : (
          <UnavailableState onRetry={() => query.refetch()} />
        )}
      </PageFrame>
    );
  }
  const view = reply.request;
  return (
    <PageFrame>
      {header}
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] lg:gap-6">
        <div className="grid gap-4 lg:gap-6">
          {result ? <ResultBanner result={result} view={view} /> : null}
          <SidesPanel view={view} />
          <DecidePanel view={view} />
        </div>
        <StatePanel view={view} />
      </div>
    </PageFrame>
  );
}

/** What the fresh-provider flow recorded, sealed by the server on the way
 * back. It reports the decision's own outcome — complete/pending is not
 * guessed from state, and a denial is never softened into success. */
function ResultBanner({
  result,
  view,
}: {
  result: LinkConsentOutcome;
  view: LinkRequestView;
}) {
  const t = useTranslations("accountPage");
  const other = platformLabel(
    view.pair[view.side === "initiating" ? "receiving" : "initiating"].platform);
  const verb = t(`verb.${result.action}`);

  let icon = <CircleDashed aria-hidden="true" className="size-5 shrink-0 text-denim" />;
  let text: string;
  if (result.outcome === "complete") {
    if (result.state === "committed") {
      icon = <CheckCircle2 aria-hidden="true" className="size-5 shrink-0 text-success" />;
      text = t("result.linked");
    } else if (result.action === "approve"
      && (result.state === "pending" || result.state === "committing")) {
      text = t("result.approved", { other });
    } else if (result.action !== "approve") {
      text = t("result.decided", { verb });
    } else {
      text = t("result.recorded", { verb });
    }
  } else {
    icon = result.outcome === "denied"
      ? <XCircle aria-hidden="true" className="size-5 shrink-0 text-danger" />
      : <AlertTriangle aria-hidden="true" className="size-5 shrink-0 text-caution" />;
    text = t(`result.${result.outcome}`);
  }
  return (
    <div
      role="status"
      className="flex items-start gap-3 rounded-panel border border-hairline bg-surface px-4 py-3"
    >
      {icon}
      <p className="text-body text-ink">{text}</p>
    </div>
  );
}

/** The exact pair the request names — each side labelled, own side marked.
 * The server picked both accounts; nothing here is editable. */
function SidesPanel({ view }: { view: LinkRequestView }) {
  const t = useTranslations("accountPage");
  return (
    <Panel aria-labelledby="link-sides-title">
      <PanelHeader id="link-sides-title" title={t("sidesTitle")} />
      <Rows className="border-t border-hairline">
        {(["initiating", "receiving"] as const).map((side) => {
          const account = view.pair[side];
          return (
            <li key={side} className="flex items-center gap-3 px-4 py-3 sm:px-5">
              <div className="grid min-w-0 flex-1 gap-0.5">
                <span className="flex min-w-0 items-center gap-2 text-body text-ink">
                  <span className="font-medium">{platformLabel(account.platform)}</span>
                  <span className="truncate font-mono text-meta text-ink-muted">
                    {account.kind} {account.value}
                  </span>
                </span>
                <span className="text-meta text-ink-muted">{t(side)}</span>
              </div>
              {side === view.side ? <Badge tone="denim">{t("you")}</Badge> : null}
            </li>
          );
        })}
      </Rows>
    </Panel>
  );
}

/** Where the request stands: state, the surviving person, the clock and who
 * has already said yes. */
function StatePanel({ view }: { view: LinkRequestView }) {
  const t = useTranslations("accountPage");
  const locale = useLocale();
  const expired = view.state === "pending" && Date.parse(view.expires_at) <= Date.now();
  const rows: Array<[string, ReactNode]> = [
    [t("survivesAs"),
      <span key="s" className="break-all font-mono text-meta">{view.surviving_person}</span>],
    [t("created"), formatDateTime(Date.parse(view.created_at), locale)],
    [t("expires"), formatDateTime(Date.parse(view.expires_at), locale)],
    [t("approvals"), view.approved.length
      ? view.approved.map((side) => t(`side.${side}`)).join(", ")
      : "—"],
  ];
  return (
    <Panel aria-labelledby="link-state-title">
      <PanelHeader
        id="link-state-title"
        title={t("stateTitle")}
        action={<Badge tone={stateTone[view.state]}>{t(`state.${view.state}`)}</Badge>}
      />
      <PanelBody className="grid gap-3 border-t border-hairline pt-4">
        {rows.map(([label, value]) => (
          <div key={label} className="grid min-w-0 gap-0.5">
            <span className="text-meta text-ink-muted">{label}</span>
            <span className="min-w-0 text-body text-ink">{value}</span>
          </div>
        ))}
        {expired ? (
          <p className="text-meta text-caution">{t("expiredNote")}</p>
        ) : null}
        {view.state === "pending" && view.approved.includes(view.side) ? (
          <p className="text-meta text-ink-soft">
            {t("waiting", {
              other: platformLabel(
                view.pair[view.side === "initiating" ? "receiving" : "initiating"].platform),
            })}
          </p>
        ) : null}
      </PanelBody>
    </Panel>
  );
}

/** The decision itself, in the transport that side's account requires:
 * Discord/Telegram consent is a fresh provider sign-in through the sealed
 * intent; Signal's consent is the private message — the challenge words the
 * server wrote, sent by the holder. */
function DecidePanel({ view }: { view: LinkRequestView }) {
  const t = useTranslations("accountPage");
  const locale = useLocale();
  const verbs = availableVerbs(view);
  if (verbs.length === 0) return null;
  const own = view.pair[view.side];

  return (
    <Panel aria-labelledby="link-decide-title">
      <PanelHeader id="link-decide-title" title={t("decideTitle")} />
      <PanelBody className="grid gap-4 border-t border-hairline pt-4">
        {decisionTransport(view) === "provider" ? (
          <>
            <p className="text-body text-ink-soft">
              {t("providerLede", { platform: platformLabel(own.platform) })}
            </p>
            <form method="post" action={CONSENT_ACTION}
              className="flex flex-wrap items-center gap-2">
              <input type="hidden" name="request_id" value={view.request_id} />
              <input type="hidden" name="locale" value={locale} />
              {verbs.map((verb) => (
                <Button
                  key={verb}
                  type="submit"
                  name="action"
                  value={verb}
                  variant={verb === "approve" ? "primary" : "destructive"}
                >
                  {t(verb)}
                </Button>
              ))}
            </form>
          </>
        ) : (
          <>
            <p className="flex items-start gap-2 text-body text-ink-soft">
              <MessageSquareLock aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
              {t("signalLede")}
            </p>
            {verbs.map((verb) => (
              <div key={verb} className="grid gap-1">
                <span className="text-meta font-medium text-ink-muted">
                  {verb === "approve" ? t("signalApprove")
                    : verb === "refuse" ? t("signalRefuse") : t("signalCancel")}
                </span>
                <code className="break-all rounded-control border border-hairline bg-hoodie px-3 py-2 font-mono text-meta text-ink">
                  {verb === "approve" ? view.challenge.approve : view.challenge.refuse}
                </code>
              </div>
            ))}
          </>
        )}
      </PanelBody>
    </Panel>
  );
}
