"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { fetchTools, tierLabel, sideEffectLabel, type ToolEntry } from "@/lib/tools-schema";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";

// The live tool catalog — what the deployed agent can actually call.
// Rendered from `/api/tools` (`cache: no-store`), which proxies Nora's
// own `build_tool_specs`, so a tool added on the backend shows up here on
// the next page load without a frontend rebuild.
export default function ToolsPage() {
  const t = useTranslations("tools");
  const [tools, setTools] = useState<ToolEntry[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchTools()
      .then((tools) => {
        if (!cancelled) setTools(tools);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        {t("eyebrow")}
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">{t("heading")}</h1>
      <p className="mb-8 max-w-[56ch] text-text-secondary">{t("dek")}</p>

      {error && (
        <Alert variant="destructive" className="mb-6">
          {t("error")}
        </Alert>
      )}
      {!error && !tools && <p className="text-text-muted">{t("loading")}</p>}

      {tools && (
        <div className="flex flex-col gap-3">
          {tools.map((tool) => (
            <Card key={tool.name}>
              <CardHeader className="flex-row items-start justify-between gap-3 space-y-0 p-4 pb-2">
                <div className="flex min-w-0 flex-col gap-1.5">
                  <CardTitle className="truncate font-mono text-[13.5px]">
                    {tool.name}
                  </CardTitle>
                  {tool.description && (
                    <CardDescription className="whitespace-pre-line text-[12.5px] leading-relaxed">
                      {tool.description}
                    </CardDescription>
                  )}
                  {!tool.description && (
                    <CardDescription className="text-text-muted">
                      {t("noDescription")}
                    </CardDescription>
                  )}
                </div>
                <Badge variant="live" className="shrink-0">
                  {t("budget", { budget: tool.budget })}
                </Badge>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center gap-x-3 gap-y-1 p-4 pt-2">
                {tool.terminal && (
                  <Badge variant="danger">{t("terminal")}</Badge>
                )}
                {tool.read_only && (
                  <Badge variant="designed">{t("readOnly")}</Badge>
                )}
                {!tool.read_only && (
                  <Badge variant="designed">{t("writes")}</Badge>
                )}
                <Badge variant="designed">{tierLabel(tool.source_tier)}</Badge>
                {tool.cacheable && (
                  <Badge variant="designed">{t("cacheable")}</Badge>
                )}
                <Badge variant="designed">
                  {t("sideEffect", { state: sideEffectLabel(tool.side_effect_state) })}
                </Badge>
                {tool.output_contract && (
                  <Badge variant="designed">
                    {t("contract", { contract: tool.output_contract })}
                  </Badge>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
