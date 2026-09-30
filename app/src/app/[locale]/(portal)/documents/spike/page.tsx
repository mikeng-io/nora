import { notFound } from "next/navigation";
import { DocumentsSpike } from "@/components/documents/spike";

// Fixture route for the document renderer (design/artifact/00-design.md). It
// exists under `next dev` and in the e2e run, never in a production build.
export default async function SpikePage({ searchParams }: { searchParams: Promise<{ demo?: string }> }) {
  if (process.env.NODE_ENV === "production") notFound();
  const { demo } = await searchParams;
  return <DocumentsSpike demo={demo ?? "trip"} />;
}
