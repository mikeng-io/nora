"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { fetchDoctrine, type DoctrineSection } from "@/lib/doctrine-schema";
import { parseDoctrineBody } from "@/lib/doctrine-body";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";

// name -> title-case display name. Small and closed (design/17's five
// authored files plus the one generated `capability` section) — not worth
// a general title-caser for six known values.
const SECTION_TITLES: Record<string, string> = {
  identity: "Identity",
  voice: "Voice",
  silence: "Silence",
  conduct: "Conduct",
  capability: "Capability",
  constraints: "Constraints",
};

export default function DoctrinePage() {
  const t = useTranslations("doctrine");
  const [sections, setSections] = useState<DoctrineSection[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchDoctrine()
      .then((s) => {
        if (!cancelled) setSections(s);
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
      {!error && !sections && <p className="text-text-muted">{t("loading")}</p>}

      {sections && (
        <div className="flex flex-col gap-4">
          {sections.map((section) => (
            <Card key={section.name}>
              <CardHeader className="flex-row items-start justify-between gap-2 space-y-0">
                <div className="flex flex-col gap-1">
                  <CardTitle>{SECTION_TITLES[section.name] ?? section.name}</CardTitle>
                  <CardDescription>{section.description}</CardDescription>
                </div>
                <Badge variant="live" className="shrink-0">
                  {t("version", { version: section.version })}
                </Badge>
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                {parseDoctrineBody(section.body).map((block, i) => {
                  if (block.type === "heading") {
                    return (
                      <h3 key={i} className="text-[14px] font-semibold text-text-primary">
                        {block.text}
                      </h3>
                    );
                  }
                  if (block.type === "list") {
                    return (
                      <ul key={i} className="list-disc pl-5 text-[13.5px] text-text-secondary">
                        {block.items.map((item, j) => (
                          <li key={j}>{item}</li>
                        ))}
                      </ul>
                    );
                  }
                  return (
                    <p key={i} className="text-[13.5px] text-text-secondary">
                      {block.text}
                    </p>
                  );
                })}
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
