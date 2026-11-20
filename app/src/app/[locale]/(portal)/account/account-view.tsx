"use client";

import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { useRouter } from "@/i18n/routing";
import { PageFrame } from "@/components/shell/app-shell";
import { PageHeader } from "@/components/ui/page-header";
import { Panel, PanelBody, PanelHeader } from "@/components/ui/panel";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useViewer } from "@/components/shell/viewer";
import { useSignOut } from "@/components/shell/account";
import { createLinkRequest, platformLabel, unlinkAccount } from "@/lib/api/holder";

const FIELD =
  "h-11 w-full rounded-control border border-hairline bg-surface px-3 text-body text-ink " +
  "outline-none placeholder:text-ink-muted focus:border-seam focus:ring-2 focus:ring-denim";

export type SessionAccount = {
  platform: string;
  kind: string;
  value: string;
};

export type SignInMethod =
  "discord_oauth" | "telegram_oidc" | "signal_code" | "whatsapp_code";

// The identifiers a platform can be linked by (`nora/domain/identity.py`
// `_ID_KINDS`): only these can name the receiving account — a handle can't.
const KINDS: Record<"signal" | "telegram" | "discord",
  Array<"phone" | "uuid" | "user_id">> = {
  signal: ["phone", "uuid"],
  telegram: ["user_id"],
  discord: ["user_id"],
};

/** The holder's own surface: who this device is signed in as, the managed
 * link requests, and the exact unlink of THIS account. In file mode the
 * managed controls do not exist — identity.toml stays the only truth. */
export function AccountView({
  managed,
  account,
  method,
}: {
  managed: boolean;
  account?: SessionAccount;
  method?: SignInMethod;
}) {
  const t = useTranslations("accountPage");
  const viewer = useViewer();

  return (
    <PageFrame>
      <PageHeader title={t("title")} />
      <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)] lg:gap-6">
        <Panel aria-labelledby="signed-in-title">
          <PanelHeader id="signed-in-title" title={t("signedIn")}
            description={t("signedInLede")} />
          <PanelBody className="grid gap-3 border-t border-hairline pt-4">
            <Row label={t("person")}>
              <span className="truncate font-medium">{viewer.displayName}</span>
              <span className="truncate font-mono text-meta text-ink-muted">
                {viewer.personId}
              </span>
            </Row>
            {account ? (
              <>
                <Row label={t("platform")}>{platformLabel(account.platform)}</Row>
                <Row label={t("identifier")}>
                  <span className="font-mono text-meta">{account.kind}</span>
                  <span className="break-all font-mono text-meta">{account.value}</span>
                </Row>
              </>
            ) : null}
            {method ? (
              <Row label={t("signedIn")}>
                <Badge tone="neutral">{t(`method.${method}`)}</Badge>
              </Row>
            ) : null}
            {!managed ? (
              <p className="text-meta text-ink-muted">{t("fileMode")}</p>
            ) : null}
          </PanelBody>
        </Panel>
        {managed && account ? (
          <div className="grid gap-4 lg:gap-6">
            <LinkControls account={account} personId={viewer.personId} />
            <UnlinkControls account={account} personId={viewer.personId} />
          </div>
        ) : null}
      </div>
    </PageFrame>
  );
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid min-w-0 gap-0.5">
      <span className="text-meta text-ink-muted">{label}</span>
      <span className="grid min-w-0 gap-0.5 text-body text-ink">{children}</span>
    </div>
  );
}

/** The two doors into a request — start one naming the other account
 * exactly, or open one by id. Both end on the request's own page, where the
 * decision actually happens. */
function LinkControls({
  account,
  personId,
}: {
  account: SessionAccount;
  personId: string;
}) {
  const t = useTranslations("accountPage");
  const router = useRouter();
  const [platform, setPlatform] = useState<keyof typeof KINDS>("signal");
  const [kind, setKind] = useState<string>(KINDS.signal[0]);
  const [value, setValue] = useState("");
  const [surviving, setSurviving] = useState(personId);
  const [requestId, setRequestId] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<"denied" | "unavailable" | "incomplete" | null>(null);

  const start = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      const reply = await createLinkRequest({
        request_id: crypto.randomUUID(),
        receiving: { platform, kind, value: value.trim() },
        surviving_person: surviving.trim(),
      });
      if (reply.status === "ok") {
        router.push(`/account/link-requests/${encodeURIComponent(reply.request.request_id)}`);
        return;
      }
      setNote(reply.status);
    } finally {
      setBusy(false);
    }
  };

  const open = (event: FormEvent) => {
    event.preventDefault();
    const id = requestId.trim();
    if (id) router.push(`/account/link-requests/${encodeURIComponent(id)}`);
  };

  return (
    <Panel aria-labelledby="links-title">
      <PanelHeader id="links-title" title={t("createTitle")}
        description={t("linksLede")} />
      <PanelBody className="grid gap-5 border-t border-hairline pt-4">
        <form onSubmit={start} className="grid gap-3">
          <p className="text-meta text-ink-muted">{t("createLede")}</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="grid gap-1">
              <span className="text-meta font-medium text-ink-muted">{t("platform")}</span>
              <select
                value={platform}
                onChange={(e) => {
                  const next = e.target.value as keyof typeof KINDS;
                  setPlatform(next);
                  setKind(KINDS[next][0]);
                }}
                className={FIELD}
              >
                {Object.keys(KINDS).map((name) => (
                  <option key={name} value={name}>{platformLabel(name)}</option>
                ))}
              </select>
            </label>
            <label className="grid gap-1">
              <span className="text-meta font-medium text-ink-muted">{t("identifier")}</span>
              <select value={kind} onChange={(e) => setKind(e.target.value)}
                className={FIELD} aria-describedby="link-kind-hint">
                {KINDS[platform].map((k) => (
                  <option key={k} value={k}>{t(`kind.${k}`)}</option>
                ))}
              </select>
            </label>
          </div>
          <p id="link-kind-hint" className="-mt-2 text-meta text-ink-muted">
            {t("kindHint")}
          </p>
          <label className="grid gap-1">
            <span className="text-meta font-medium text-ink-muted">{t("identifier")}</span>
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              required
              className={FIELD}
              aria-describedby="link-value-hint"
              autoComplete="off"
            />
          </label>
          <p id="link-value-hint" className="-mt-2 text-meta text-ink-muted">
            {t("valueHint")}
          </p>
          <label className="grid gap-1">
            <span className="text-meta font-medium text-ink-muted">{t("surviving")}</span>
            <input
              value={surviving}
              onChange={(e) => setSurviving(e.target.value)}
              required
              className={`${FIELD} font-mono`}
              aria-describedby="link-surviving-hint"
              autoComplete="off"
            />
          </label>
          <p id="link-surviving-hint" className="-mt-2 text-meta text-ink-muted">
            {t("survivingHint")}
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" type="submit" disabled={busy}>
              {t("start")}
            </Button>
            {note ? (
              <p role="status" className="text-meta text-caution">
                {note === "denied" ? t("createDenied")
                  : note === "incomplete" ? t("createIncomplete") : t("unavailable")}
              </p>
            ) : null}
          </div>
          <p className="text-meta text-ink-muted">{t("createNote")}</p>
        </form>

        <form onSubmit={open} className="grid gap-3 border-t border-hairline pt-4">
          <p className="grid gap-0.5">
            <span className="text-body font-medium text-ink">{t("openTitle")}</span>
            <span className="text-meta text-ink-muted">{t("openLede")}</span>
          </p>
          <div className="flex items-start gap-2">
            <label className="min-w-0 flex-1">
              <span className="sr-only">{t("requestId")}</span>
              <input
                value={requestId}
                onChange={(e) => setRequestId(e.target.value)}
                required
                placeholder={t("requestId")}
                className={`${FIELD} font-mono`}
                autoComplete="off"
              />
            </label>
            <Button variant="outline" type="submit">{t("open")}</Button>
          </div>
        </form>
      </PanelBody>
    </Panel>
  );
}

/** Exact unlink: the account is named in full before the button that ends
 * it, and the person check is the session's own — no account is ever typed
 * into this control. */
function UnlinkControls({
  account,
  personId,
}: {
  account: SessionAccount;
  personId: string;
}) {
  const t = useTranslations("accountPage");
  const { signOut } = useSignOut();
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<"denied" | "unavailable" | "incomplete" | null>(null);

  const unlink = async () => {
    setBusy(true);
    try {
      const reply = await unlinkAccount({
        operation_id: crypto.randomUUID(),
        expected_person: personId,
      });
      if (reply.status === "ok") {
        signOut();
        return;
      }
      setNote(reply.status);
      setBusy(false);
    } catch {
      setNote("unavailable");
      setBusy(false);
    }
  };

  return (
    <Panel aria-labelledby="unlink-title">
      <PanelHeader id="unlink-title" title={t("unlinkTitle")}
        description={t("unlinkLede")} />
      <PanelBody className="grid gap-3 border-t border-hairline pt-4">
        {!confirming ? (
          <Button variant="destructive" onClick={() => setConfirming(true)}
            className="w-fit">
            {t("unlinkReveal")}
          </Button>
        ) : (
          <>
            <p className="text-body text-ink">
              {t("unlinkWarning", {
                platform: platformLabel(account.platform),
                kind: account.kind === "phone" || account.kind === "uuid"
                  || account.kind === "user_id"
                  ? t(`kind.${account.kind}`) : account.kind,
                value: account.value,
              })}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="destructive" onClick={unlink} disabled={busy}>
                {t("unlinkConfirm", { value: account.value })}
              </Button>
              <Button variant="quiet" onClick={() => setConfirming(false)}
                disabled={busy}>
                {t("keepIt")}
              </Button>
            </div>
          </>
        )}
        {note ? (
          <p role="status" className="text-meta text-caution">
            {note === "denied" ? t("unlinkDenied")
              : note === "incomplete" ? t("unlinkIncomplete") : t("unavailable")}
          </p>
        ) : null}
      </PanelBody>
    </Panel>
  );
}
