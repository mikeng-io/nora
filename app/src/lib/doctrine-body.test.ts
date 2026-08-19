import { describe, expect, it } from "vitest";
import { parseDoctrineBody } from "./doctrine-body";

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
      { type: "heading", text: "Who you are" },
      { type: "paragraph", text: "You are Nora. You're in a group chat. You remember the people." },
      { type: "heading", text: "Rules" },
      { type: "list", items: ["First rule", "Second rule"] },
      { type: "paragraph", text: "Closing paragraph." },
    ]);
  });

  it("returns an empty array for an empty body", () => {
    expect(parseDoctrineBody("")).toEqual([]);
  });

  it("flushes a trailing list with no following blank line", () => {
    const body = "# Title\n- one\n- two";
    expect(parseDoctrineBody(body)).toEqual([
      { type: "heading", text: "Title" },
      { type: "list", items: ["one", "two"] },
    ]);
  });
});
