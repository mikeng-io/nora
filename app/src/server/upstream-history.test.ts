import { describe, expect, it } from "vitest";
import { resolveUpstream } from "./upstream";

// The two history reads (design/artifact §34) in the proxy table: the session's person,
// a UUID, a positive revision number, and nothing else the browser sends.
const ID = "9c48dbf1-7c25-4467-84b3-ac9d8d61a584";
const q = (s = "") => new URLSearchParams(s);

describe("document history routes", () => {
  it("scope the history and one revision to the session's person, dropping the browser's", () => {
    const list = resolveUpstream(["documents", ID, "revisions"], q("personId=ken&evil=1"), "jo");
    expect(list).toMatchObject({ ok: true, upstreamPath: `/app/v1/documents/${ID}/revisions?personId=jo` });
    const one = resolveUpstream(["documents", ID, "revisions", "3"], q("personId=ken"), "jo");
    expect(one).toMatchObject({ ok: true, upstreamPath: `/app/v1/documents/${ID}/revisions/3?personId=jo` });
  });

  it("answer anything that is not a UUID and a positive number as not found, before asking Nora", () => {
    for (const segments of [
      ["documents", "9e1d2c3b4a5f", "revisions"],
      ["documents", "not-a-uuid", "revisions"],
      ["documents", ID, "revisions", "0"],
      ["documents", ID, "revisions", "-1"],
      ["documents", ID, "revisions", "01"],
      ["documents", ID, "revisions", "1.5"],
      ["documents", ID, "revisions", "x"],
      ["documents", ID, "revisions", "1234567890"],
      ["documents", ID, "revisions", "3", "restore"],
    ]) {
      expect(resolveUpstream(segments, q(), "jo"), segments.join("/")).toEqual({ ok: false, status: 404 });
    }
  });
});
