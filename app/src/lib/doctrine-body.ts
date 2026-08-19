// A tiny line-based parser for doctrine section bodies — NOT a general
// markdown renderer. The five doctrine files only ever use `# heading`,
// `- bullet` and blank-line-separated paragraphs (confirmed against the
// shipped files: no bold, links, or code blocks), so a full markdown
// library is unneeded weight for this one reader.
export type DoctrineBlock =
  | { type: "heading"; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; items: string[] };

export function parseDoctrineBody(body: string): DoctrineBlock[] {
  const blocks: DoctrineBlock[] = [];
  let paragraphLines: string[] = [];
  let listItems: string[] = [];

  const flushParagraph = () => {
    if (paragraphLines.length > 0) {
      blocks.push({ type: "paragraph", text: paragraphLines.join(" ") });
      paragraphLines = [];
    }
  };
  const flushList = () => {
    if (listItems.length > 0) {
      blocks.push({ type: "list", items: listItems });
      listItems = [];
    }
  };

  for (const rawLine of body.split("\n")) {
    const line = rawLine.trim();
    if (line === "") {
      flushParagraph();
      flushList();
    } else if (line.startsWith("# ")) {
      flushParagraph();
      flushList();
      blocks.push({ type: "heading", text: line.slice(2) });
    } else if (line.startsWith("- ")) {
      flushParagraph();
      listItems.push(line.slice(2));
    } else {
      flushList();
      paragraphLines.push(line);
    }
  }
  flushParagraph();
  flushList();
  return blocks;
}
