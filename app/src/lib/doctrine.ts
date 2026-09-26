// A small line-based reader for her doctrine and skill bodies. Not a general
// markdown renderer: the shipped files use `#` headings (skills also `##`
// and `###`), `-` bullets, numbered steps, fenced examples, inline `code`,
// and blank-line paragraphs, and nothing else. A full markdown library would
// be weight for one reader, and would render things (links, raw HTML) these
// bodies are not meant to carry.

export type DoctrineBlock =
  | { type: "heading"; level: 1 | 2 | 3; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; ordered: boolean; items: string[] }
  | { type: "code"; text: string };

const CJK = /[　-〿㐀-鿿豈-﫿＀-￯]/;

/** Joins wrapped source lines back into one paragraph. Latin lines join with
 * a space; a break between two CJK characters joins with nothing, since
 * Chinese has no spaces and a stray one reads as a typo. */
export function joinLines(lines: string[]): string {
  return lines.reduce((acc, line) => {
    if (!acc) return line;
    const joinTight = CJK.test(acc.at(-1) ?? "") && CJK.test(line[0] ?? "");
    return acc + (joinTight ? "" : " ") + line;
  }, "");
}

export function parseDoctrineBody(body: string): DoctrineBlock[] {
  const blocks: DoctrineBlock[] = [];
  let para: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;
  let code: string[] | null = null;

  const flushPara = () => {
    if (para.length) blocks.push({ type: "paragraph", text: joinLines(para) });
    para = [];
  };
  const flushList = () => {
    if (list) blocks.push({ type: "list", ordered: list.ordered, items: list.items });
    list = null;
  };

  for (const raw of body.split("\n")) {
    if (code !== null) {
      if (raw.trim().startsWith("```")) {
        blocks.push({ type: "code", text: code.join("\n") });
        code = null;
      } else code.push(raw);
      continue;
    }
    const line = raw.trim();
    if (line.startsWith("```")) {
      flushPara();
      flushList();
      code = [];
      continue;
    }
    if (line === "") {
      flushPara();
      flushList();
      continue;
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      flushPara();
      flushList();
      blocks.push({ type: "heading", level: heading[1].length as 1 | 2 | 3, text: heading[2] });
      continue;
    }
    const bullet = /^[-*]\s+(.+)$/.exec(line);
    const numbered = /^\d+[.)]\s+(.+)$/.exec(line);
    if (bullet || numbered) {
      flushPara();
      const ordered = Boolean(numbered);
      if (list && list.ordered !== ordered) flushList();
      if (!list) list = { ordered, items: [] };
      list.items.push((bullet ?? numbered)![1]);
      continue;
    }
    // A wrapped continuation of the last bullet stays in that bullet.
    if (list && raw.startsWith("  ")) {
      const items = list.items;
      items[items.length - 1] = joinLines([items[items.length - 1], line]);
      continue;
    }
    flushList();
    para.push(line);
  }
  if (code !== null) blocks.push({ type: "code", text: (code as string[]).join("\n") });
  flushPara();
  flushList();
  return blocks;
}

export type InlinePart = { code: boolean; text: string };

/** Splits `inline code` out of a line of text; everything else is plain. */
export function splitInline(text: string): InlinePart[] {
  const parts: InlinePart[] = [];
  const re = /`([^`]+)`/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) parts.push({ code: false, text: text.slice(last, m.index) });
    parts.push({ code: true, text: m[1] });
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push({ code: false, text: text.slice(last) });
  return parts;
}
