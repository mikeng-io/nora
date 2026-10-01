import { afterEach, describe, expect, it } from "vitest";
import { handle, handleDocumentRestore, handleDocumentState, resetDocumentRestores, resetDocumentStates } from "./mock-nora.mjs";
import { DocumentDetailSchema, DocumentsPageSchema } from "../src/lib/api/documents";
import { DocumentRevisionSchema, DocumentRevisionsSchema } from "../src/lib/api/document-history";

// The mock's history routes answer in the shapes the portal parses, behind the same gate
// as the document, and a restore appends without rewriting (nora/web/document_history.py).
const NOW = Date.parse("2026-10-01T12:00:00Z");
const LISBON = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e01";
const MEIS = "5b6f0c3a-2d41-4e8b-9c77-1a2b3c4d5e04";
const call = (path: string, personId = "miles") => handle(path, new URLSearchParams({ personId }), NOW);
const restore = (n: number, baseRevision: unknown, personId = "miles") =>
  handleDocumentRestore(
    `/app/v1/documents/${LISBON}/revisions/${n}/restore`,
    new URLSearchParams({ personId }),
    { baseRevision },
  );

afterEach(() => {
  resetDocumentRestores();
  resetDocumentStates();
});

describe("mock document history", () => {
  it("lists every revision newest first and serves one, in the portal's shapes", () => {
    const list = DocumentRevisionsSchema.parse(call(`/app/v1/documents/${LISBON}/revisions`).body);
    expect(list.revisions.map((r) => r.revision)).toEqual([3, 2, 1]);
    const one = DocumentRevisionSchema.parse(call(`/app/v1/documents/${LISBON}/revisions/1`).body);
    expect(one.currentRevision).toBe(3);
    expect(JSON.stringify(one.document)).toContain("Draft 1.");
    expect(call(`/app/v1/documents/${LISBON}/revisions/4`).status).toBe(404);
  });

  it("answers an unreadable or deleted document's history like an unknown id", () => {
    expect(call(`/app/v1/documents/${MEIS}/revisions`).status).toBe(404);
    handleDocumentState(`/app/v1/documents/${LISBON}/state`, new URLSearchParams({ personId: "miles" }), { state: "deleted" });
    expect(call(`/app/v1/documents/${LISBON}/revisions`).status).toBe(404);
  });

  it("restores as a new revision, keeps the old ones, and refuses a stale base or an archived document", () => {
    expect(restore(1, 2)?.status).toBe(409);
    const ok = restore(1, 3);
    expect(ok).toEqual({ status: 200, body: { status: "ok", revision: 4 } });
    const list = DocumentRevisionsSchema.parse(call(`/app/v1/documents/${LISBON}/revisions`).body);
    expect(list.revisions.map((r) => r.revision)).toEqual([4, 3, 2, 1]);
    expect(list.revisions[0].writtenBy).toEqual({ kind: "person", personId: "miles", name: "Miles" });
    const detail = DocumentDetailSchema.parse(call(`/app/v1/documents/${LISBON}`).body);
    expect(detail.revision).toBe(4);
    expect(JSON.stringify(detail.document)).toContain("Draft 1.");
    const page = DocumentsPageSchema.parse(call("/app/v1/documents").body);
    expect(page.documents.find((d) => d.id === LISBON)?.revision).toBe(4);
    handleDocumentState(`/app/v1/documents/${LISBON}/state`, new URLSearchParams({ personId: "miles" }), { state: "archived" });
    expect(restore(2, 4)).toEqual({ status: 409, body: { status: "not_active" } });
  });
});
