"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { fetchTools, tierLabel, sideEffectLabel, type ToolEntry } from "@/lib/tools-schema";
import { fetchSkills, type SkillEntry } from "@/lib/skills-schema";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Tab = "tools" | "skills";

// The live capability catalog — what the deployed agent can actually call
// (tools) and what it can load on demand (skills). Rendered from `/api/tools`
// and `/api/skills` (`cache: no-store`), which proxy Nora's own
// `build_tool_specs` / `build_skills_payload`, so a tool or skill added on
// the backend shows up here on the next page load without a frontend rebuild.
export default function ToolsPage() {
  const t = useTranslations("tools");
  const [tab, setTab] = useState<Tab>("tools");
  const [tools, setTools] = useState<ToolEntry[] | null>(null);
  const [skills, setSkills] = useState<SkillEntry[] | null>(null);
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
    fetchSkills()
      .then((skills) => {
        if (!cancelled) setSkills(skills);
      })
      .catch(() => {
        if (!cancelled) setError(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const tabButton = (key: Tab, label: string, count: number | null) => (
    <button
      type="button"
      onClick={() => setTab(key)}
      className={cn(
        "rounded-md px-3 py-1.5 text-[13px] font-medium transition-colors",
        tab === key
          ? "bg-accent text-bg-base"
          : "text-text-secondary hover:bg-bg-elevated hover:text-text-primary",
      )}
    >
      {label}
      {count !== null && (
        <span className="ml-1.5 font-mono text-[11px] opacity-70">{count}</span>
      )}
    </button>
  );

  return (
    <div>
      <p className="mb-2.5 font-mono text-[10px] uppercase tracking-wider text-text-muted">
        {t("eyebrow")}
      </p>
      <h1 className="mb-1.5 text-[26px] font-medium tracking-tight">{t("heading")}</h1>
      <p className="mb-6 max-w-[56ch] text-text-secondary">{t("dek")}</p>

      <div className="mb-6 flex items-center gap-1 border-b border-border-strong pb-2">
        {tabButton("tools", t("tabTools"), tools?.length ?? null)}
        {tabButton("skills", t("tabSkills"), skills?.length ?? null)}
      </div>

      {error && (
        <Alert variant="destructive" className="mb-6">
          {t("error")}
        </Alert>
      )}

      {tab === "tools" && (
        <>
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
                    {tool.read_only ? (
                      <Badge variant="designed">{t("readOnly")}</Badge>
                    ) : (
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
        </>
      )}

      {tab === "skills" && (
        <>
          {!error && !skills && <p className="text-text-muted">{t("loading")}</p>}
          {skills && skills.length === 0 && (
            <p className="text-text-muted">{t("noSkills")}</p>
          )}
          {skills && skills.length > 0 && (
            <div className="flex flex-col gap-3">
              {skills.map((skill) => (
                <Card key={skill.name}>
                  <CardHeader className="p-4 pb-2">
                    <CardTitle className="font-mono text-[13.5px]">
                      {skill.name}
                    </CardTitle>
                    <CardDescription className="text-[12.5px] leading-relaxed">
                      {skill.description}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="p-4 pt-2">
                    <pre className="whitespace-pre-wrap rounded-md bg-bg-elevated p-3 font-mono text-[12px] leading-relaxed text-text-secondary">
                      {skill.instructions}
                    </pre>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
