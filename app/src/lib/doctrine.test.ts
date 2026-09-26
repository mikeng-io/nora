import { describe, expect, it } from "vitest";
import { joinLines, parseDoctrineBody, splitInline } from "./doctrine";

describe("parseDoctrineBody", () => {
  it("splits headings, paragraphs, and lists into separate blocks", () => {
    const body = [
      "# Who you are",
      "",
      "You are Nora. You're in a group chat.",
      "You remember the people.",
      "",
      "# Rules",
      "",
      "- First rule",
      "- Second rule",
      "",
      "Closing paragraph.",
    ].join("\n");

    expect(parseDoctrineBody(body)).toEqual([
      { type: "heading", level: 1, text: "Who you are" },
      { type: "paragraph", text: "You are Nora. You're in a group chat. You remember the people." },
      { type: "heading", level: 1, text: "Rules" },
      { type: "list", ordered: false, items: ["First rule", "Second rule"] },
      { type: "paragraph", text: "Closing paragraph." },
    ]);
  });

  it("returns an empty array for an empty body", () => {
    expect(parseDoctrineBody("")).toEqual([]);
  });

  it("flushes a trailing list with no following blank line", () => {
    expect(parseDoctrineBody("# Title\n- one\n- two")).toEqual([
      { type: "heading", level: 1, text: "Title" },
      { type: "list", ordered: false, items: ["one", "two"] },
    ]);
  });

  it("reads a skill's sub-headings, numbered steps and fenced examples", () => {
    const body = "## Step\n1. first\n2. second\n\n```\nraw  text\n```";
    expect(parseDoctrineBody(body)).toEqual([
      { type: "heading", level: 2, text: "Step" },
      { type: "list", ordered: true, items: ["first", "second"] },
      { type: "code", text: "raw  text" },
    ]);
  });

  it("keeps an indented continuation inside its bullet", () => {
    expect(parseDoctrineBody("- a long\n  bullet")).toEqual([
      { type: "list", ordered: false, items: ["a long bullet"] },
    ]);
  });
});

describe("joinLines", () => {
  it("joins Latin lines with a space and CJK lines with nothing", () => {
    expect(joinLines(["one", "two"])).toBe("one two");
    expect(joinLines(["你把聲係呢班朋友", "入面其中一個"])).toBe("你把聲係呢班朋友入面其中一個");
    expect(joinLines(["用 English", "嘅 software"])).toBe("用 English 嘅 software");
  });
});

describe("splitInline", () => {
  it("separates inline code from plain text", () => {
    expect(splitInline("see `conduct.md` first")).toEqual([
      { code: false, text: "see " },
      { code: true, text: "conduct.md" },
      { code: false, text: " first" },
    ]);
    expect(splitInline("plain")).toEqual([{ code: false, text: "plain" }]);
  });
});
