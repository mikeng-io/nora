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
//
// Collapsed by default: each entry is a compact row (name + one-line
// description + expand chevron); clicking it spans the full detail. A flat
// list of every tool's full description forces the reader to scroll past
// everything to reach the bottom.
export default function ToolsPage() {
  const t = useTranslations("tools");
  const [tab, setTab] = useState<Tab>("tools");
  const [tools, setTools] = useState<ToolEntry[] | null>(null);
  const [skills, setSkills] = useState<SkillEntry[] | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
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

  const toggle = (key: string) => setExpanded((cur) => (cur === key ? null : key));

  const chevron = (open: boolean) => (
    <span
      className={cn(
        "shrink-0 text-[11px] text-text-muted transition-transform",
        open && "rotate-90",
      )}
    >
      ▶
    </span>
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
            <div className="flex flex-col gap-2">
              {tools.map((tool) => {
                const open = expanded === `tool:${tool.name}`;
                return (
                  <Card key={tool.name} className="p-0">
                    <button
                      type="button"
                      onClick={() => toggle(`tool:${tool.name}`)}
                      className="flex w-full items-center gap-3 p-3 text-left"
                    >
                      {chevron(open)}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-[13px]">
                          {tool.name}
                        </span>
                        {tool.description && (
                          <span className="block truncate text-[12px] text-text-secondary">
                            {tool.description}
                          </span>
                        )}
                      </span>
                      <Badge variant="live" className="shrink-0">
                        {t("budget", { budget: tool.budget })}
                      </Badge>
                    </button>
                    {open && (
                      <div className="border-t border-border px-3 pb-3 pt-2">
                        {tool.description && (
                          <p className="mb-2 whitespace-pre-line text-[12.5px] leading-relaxed text-text-secondary">
                            {tool.description}
                          </p>
                        )}
                        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
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
                        </div>
                      </div>
                    )}
                  </Card>
                );
              })}
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
            <div className="flex flex-col gap-2">
              {skills.map((skill) => {
                const open = expanded === `skill:${skill.name}`;
                return (
                  <Card key={skill.name} className="p-0">
                    <button
                      type="button"
                      onClick={() => toggle(`skill:${skill.name}`)}
                      className="flex w-full items-center gap-3 p-3 text-left"
                    >
                      {chevron(open)}
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-mono text-[13px]">
                          {skill.name}
                        </span>
                        <span className="block truncate text-[12px] text-text-secondary">
                          {skill.description}
                        </span>
                      </span>
                    </button>
                    {open && (
                      <div className="border-t border-border px-3 pb-3 pt-2">
                        <p className="mb-2 text-[12.5px] leading-relaxed text-text-secondary">
                          {skill.description}
                        </p>
                        <pre className="whitespace-pre-wrap rounded-md bg-bg-elevated p-3 font-mono text-[12px] leading-relaxed text-text-secondary">
                          {skill.instructions}
                        </pre>
                      </div>
                    )}
                  </Card>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}
