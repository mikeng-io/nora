import { describe, expect, it } from "vitest";
import {
  SEARCH_MAX,
  deletedNoticeShown,
  freshness,
  isRevised,
  isTyping,
  normalizeSearch,
  pageCount,
  platformLabel,
  roomName,
} from "./list";

describe("documents list helpers", () => {
  it("names a platform the way a person would, and never drops an unknown one", () => {
    expect(platformLabel("signal")).toBe("Signal");
    expect(platformLabel("WhatsApp")).toBe("WhatsApp");
    expect(platformLabel("matrix")).toBe("Matrix");
    expect(platformLabel("")).toBe("");
  });

  it("counts pages, with an empty list as page one and nonsense as page one", () => {
    expect(pageCount(0, 20)).toBe(1);
    expect(pageCount(20, 20)).toBe(1);
    expect(pageCount(21, 20)).toBe(2);
    expect(pageCount(5, 0)).toBe(1);
    expect(pageCount(Number.NaN, 20)).toBe(1);
  });

  it("calls only a later revision revised", () => {
    expect(isRevised(1)).toBe(false);
    expect(isRevised(2)).toBe(true);
  });
});

describe("room name (e)", () => {
  it("shows the room's label when Nora sent one, and the platform when she did not", () => {
    expect(roomName({ roomLabel: "Trip planning", platform: "signal" })).toBe("Trip planning");
    expect(roomName({ roomLabel: "", platform: "signal" })).toBe("Signal");
    expect(roomName({ roomLabel: "   ", platform: "discord" })).toBe("Discord");
    // A Nora from before the label sends none at all.
    expect(roomName({ platform: "whatsapp" })).toBe("WhatsApp");
  });
});

describe("search text", () => {
  it("is trimmed and collapsed the way Nora collapses it, so what is asked is what is shown", () => {
    expect(normalizeSearch("  lisbon   trip \n")).toBe("lisbon trip");
    expect(normalizeSearch("   ")).toBe("");
    expect(normalizeSearch("里斯本  旅行")).toBe("里斯本 旅行");
  });

  it("is cut at the length Nora refuses above, so a long paste still lists", () => {
    expect(normalizeSearch("x".repeat(SEARCH_MAX + 50))).toHaveLength(SEARCH_MAX);
  });

  it("is `typing` while the box holds something other than what was asked", () => {
    expect(isTyping("lisbon", "lisbon")).toBe(false);
    expect(isTyping("  lisbon ", "lisbon")).toBe(false);
    expect(isTyping("lisbon trip", "lisbon")).toBe(true);
    expect(isTyping("", "lisbon")).toBe(true);
  });
});

describe("freshness (c) (g)", () => {
  it("says `loading` while the rows shown belong to the previous filter, page or search", () => {
    expect(freshness({ isPlaceholderData: true, isError: false, hasData: true })).toBe("loading");
  });

  it("says `outdated` when the last read failed and an earlier read's rows are what is shown", () => {
    expect(freshness({ isError: true, hasData: true })).toBe("outdated");
    expect(freshness({ isPlaceholderData: false, isError: true, hasData: true })).toBe("outdated");
  });

  it("says nothing for a current list, and nothing to say when there are no rows to be stale", () => {
    expect(freshness({ isError: false, hasData: true })).toBe("current");
    expect(freshness({ isError: true, hasData: false })).toBe("current");
    expect(freshness({ isPlaceholderData: true, isError: false, hasData: false })).toBe("current");
  });

  it("puts the pending cue ahead of the failure: an old filter's rows are not 'out of date', they are another list", () => {
    expect(freshness({ isPlaceholderData: true, isError: true, hasData: true })).toBe("loading");
  });
});

describe("the delete notice (d)", () => {
  it("is shown only for the flag a finished delete leaves", () => {
    expect(deletedNoticeShown("deleted=1")).toBe(true);
    expect(deletedNoticeShown("page=2&deleted=1")).toBe(true);
    expect(deletedNoticeShown("")).toBe(false);
    expect(deletedNoticeShown("deleted=0")).toBe(false);
    expect(deletedNoticeShown("deleted=yes")).toBe(false);
  });
});
