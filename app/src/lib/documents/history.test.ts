import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api/client";
import { DocumentRevisionSchema, DocumentRevisionsSchema, RestoreRefusedError } from "@/lib/api/document-history";
import { isOldRevision, restoreProblem, revisionParam, shortSha } from "./history";

describe("history helpers", () => {
  it("reads a revision number only when it is a plain positive integer", () => {
    expect(revisionParam("3")).toBe(3);
    for (const bad of ["0", "-1", "01", "1.5", "x", "", "1234567890"]) expect(revisionParam(bad), bad).toBeNull();
  });

  it("calls a revision old only below the current one", () => {
    expect(isOldRevision(1, 3)).toBe(true);
    expect(isOldRevision(3, 3)).toBe(false);
  });

  it("says why a restore did not happen, and never calls an outage a refusal", () => {
    expect(restoreProblem(new RestoreRefusedError("conflict"))).toBe("restoreConflict");
    expect(restoreProblem(new RestoreRefusedError("not_active"))).toBe("restoreArchived");
    expect(restoreProblem(new RestoreRefusedError("invalid"))).toBe("restoreInvalid");
    expect(restoreProblem(new ApiError("x", 404, "not-found"))).toBe("restoreGone");
    expect(restoreProblem(new ApiError("x", 503, "unavailable"))).toBe("restoreFailed");
    expect(restoreProblem(new Error("?"))).toBe("restoreFailed");
    expect(shortSha("a".repeat(64))).toBe("aaaaaaaa");
  });
});

describe("history schemas mirror nora/web/document_history.py", () => {
  it("read the history list and one revision, and refuse a writer that is neither kind", () => {
    const list = {
      id: "x", title: "T", revision: 2, state: "active",
      revisions: [
        { revision: 2, title: "T", sha256: "b".repeat(64), recordedAt: "2026-10-01T00:00:00Z",
          writtenBy: { kind: "person", personId: "mei", name: "Mei" } },
        { revision: 1, title: "T", sha256: "a".repeat(64), recordedAt: "2026-09-30T00:00:00Z", writtenBy: { kind: "nora" } },
      ],
    };
    expect(DocumentRevisionsSchema.parse(list).revisions).toHaveLength(2);
    const bad = structuredClone(list);
    (bad.revisions[1].writtenBy as { kind: string }).kind = "someone";
    expect(DocumentRevisionsSchema.safeParse(bad).success).toBe(false);
    const one = {
      id: "x", revision: 1, currentRevision: 2, state: "archived", title: "T", sha256: "a".repeat(64),
      recordedAt: "2026-09-30T00:00:00Z", writtenBy: { kind: "nora" }, document: { any: 1 }, media: [],
    };
    expect(DocumentRevisionSchema.parse(one).currentRevision).toBe(2);
  });
});
