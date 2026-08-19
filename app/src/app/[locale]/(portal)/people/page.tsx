"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/routing";
import { fetchPersonsRoster, type PersonSummary } from "@/lib/persons-schema";
import { Card, CardContent } from "@/components/ui/card";
import { Alert } from "@/components/ui/alert";
import { PersonAvatar } from "@/components/ui/person-avatar";

export default function PeoplePage() {
  const t = useTranslations("people");
  const [roster, setRoster] = useState<PersonSummary[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchPersonsRoster()
      .then((r) => {
        if (!cancelled) setRoster(r);
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
      {!error && !roster && <p className="text-text-muted">{t("loading")}</p>}
      {!error && roster && roster.length === 0 && (
        <p className="text-text-muted">{t("empty")}</p>
      )}

      {roster && roster.length > 0 && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3">
          {roster.map((person) => (
            <Link key={person.personId} href={`/people/${person.personId}`} className="block">
              <Card className="transition-colors hover:bg-bg-elevated">
                <CardContent className="flex items-center gap-3 p-4">
                  <PersonAvatar
                    seed={person.personId} size={36} className="rounded-full overflow-hidden"
                  />
                  <span className="text-[15px] font-medium text-text-primary">
                    {person.displayName}
                  </span>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
