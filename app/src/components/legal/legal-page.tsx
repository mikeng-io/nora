import type { ReactNode } from "react";
import { NoraMark, Wordmark } from "@/components/ui/avatar";
import { Link } from "@/i18n/routing";
import { LEGAL_CONTACT } from "@/lib/legal";

export type LegalSection = {
  id: string;
  title: string;
  body: string;
  // A list under the body, for the sections that name categories.
  items?: string[];
  // A link under the body, for the sections that point at Google.
  link?: { href: string; label: string };
};

type Labels = {
  title: string;
  effective: string;
  contact: string;
  back: string;
  privacyLink: string;
  termsLink: string;
};

// A public page: no session, no data, no client JS of its own. It renders
// outside the (portal) group on purpose, because that group's layout is the
// sign-in gate; Google's consent screen needs these two URLs readable by
// anyone.
export function LegalPage({
  locale,
  labels,
  sections,
}: {
  locale: string;
  labels: Labels;
  sections: LegalSection[];
}): ReactNode {
  const siteHome = locale === "en" ? "/en/" : "/";
  return (
    <div className="min-h-dvh bg-studio">
      <div className="mx-auto w-full max-w-[720px] px-4 pb-16 sm:px-6">
        <header className="safe-top flex h-16 items-center gap-2">
          <a href={siteHome} className="flex items-center gap-2 rounded-full pr-2" aria-label={labels.back}>
            <NoraMark size={30} />
            <Wordmark />
          </a>
        </header>
        <main className="page-enter grid gap-8 py-6">
          <div className="grid gap-2">
            <h1 className="text-[2rem] leading-[1.08] font-[650] tracking-[-0.03em] text-ink sm:text-[2.5rem] lang-zh:tracking-normal">
              {labels.title}
            </h1>
            <p className="text-meta text-ink-soft">{labels.effective}</p>
          </div>
          {sections.map((s) => (
            <section key={s.id} aria-labelledby={`legal-${s.id}`} className="grid gap-2">
              <h2 id={`legal-${s.id}`} className="text-title font-semibold text-ink">
                {s.title}
              </h2>
              <p className="text-body text-ink-soft">{s.body}</p>
              {s.items ? (
                <ul className="grid list-disc gap-1 pl-5 text-body text-ink-soft">
                  {s.items.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}
              {s.link ? (
                <p>
                  <a
                    href={s.link.href}
                    rel="noopener noreferrer"
                    className="text-body font-semibold text-denim underline-offset-4 hover:underline"
                  >
                    {s.link.label}
                  </a>
                </p>
              ) : null}
            </section>
          ))}
        </main>
        <footer className="grid gap-3 border-t border-hairline pt-6 text-meta text-ink-soft">
          <p>
            <a href={`mailto:${LEGAL_CONTACT}`} className="font-semibold text-denim underline-offset-4 hover:underline">
              {labels.contact}
            </a>
          </p>
          <nav aria-label="Legal" className="flex gap-4">
            <Link href="/privacy" className="font-semibold text-denim underline-offset-4 hover:underline">
              {labels.privacyLink}
            </Link>
            <Link href="/terms" className="font-semibold text-denim underline-offset-4 hover:underline">
              {labels.termsLink}
            </Link>
            <a href={siteHome} className="font-semibold text-denim underline-offset-4 hover:underline">
              {labels.back}
            </a>
          </nav>
        </footer>
      </div>
    </div>
  );
}
