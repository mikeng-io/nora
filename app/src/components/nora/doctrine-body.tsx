import { Fragment } from "react";
import { parseDoctrineBody, splitInline, type DoctrineBlock } from "@/lib/doctrine";
import { cn } from "@/lib/cn";

// Renders a doctrine or skill body for reading: her own words, set at a
// comfortable measure. Much of her doctrine is written Cantonese, so each
// block is tagged with the language it is mostly in; zh-Hant text then gets
// its CJK face and a looser line (DESIGN.md §4), English keeps its own.

const CJK_CHAR = /[㐀-鿿豈-﫿]/g;

/** "zh-Hant-HK" when a third or more of the letters are Chinese. */
export function langOf(text: string): "zh-Hant-HK" | "en" {
  const letters = text.replace(/[\s\p{P}\p{S}\d]/gu, "").length;
  const cjk = text.match(CJK_CHAR)?.length ?? 0;
  return letters > 0 && cjk / letters >= 0.3 ? "zh-Hant-HK" : "en";
}

export function Inline({ text }: { text: string }) {
  return (
    <>
      {splitInline(text).map((p, i) =>
        p.code ? (
          <code
            key={i}
            className="rounded-[5px] bg-hoodie px-1 py-px font-mono text-[0.8125em] text-ink-soft"
          >
            {p.text}
          </code>
        ) : (
          <Fragment key={i}>{p.text}</Fragment>
        ),
      )}
    </>
  );
}

const reading =
  "text-[1rem] leading-[1.7] text-ink [&:lang(zh-Hant-HK)]:leading-[1.8] [&:lang(zh-Hant-HK)]:tracking-[0.01em]";

function Block({ block, headingBase }: { block: DoctrineBlock; headingBase: 3 | 4 }) {
  switch (block.type) {
    case "heading": {
      const level = Math.min(6, headingBase + block.level - 1);
      const Tag = `h${level}` as "h3" | "h4" | "h5";
      return (
        <Tag
          lang={langOf(block.text)}
          className={cn(
            "font-semibold text-ink",
            block.level === 1 ? "mt-7 text-[1.0625rem] first:mt-0" : "mt-5 text-[0.9375rem] first:mt-0",
          )}
        >
          <Inline text={block.text} />
        </Tag>
      );
    }
    case "paragraph":
      return (
        <p lang={langOf(block.text)} className={cn(reading, "mt-3 first:mt-0")}>
          <Inline text={block.text} />
        </p>
      );
    case "list": {
      const Tag = block.ordered ? "ol" : "ul";
      return (
        <Tag className={cn("mt-3 grid gap-2 pl-5 first:mt-0", block.ordered ? "list-decimal" : "list-disc", "marker:text-seam")}>
          {block.items.map((item, i) => (
            <li key={i} lang={langOf(item)} className={cn(reading, "pl-1")}>
              <Inline text={item} />
            </li>
          ))}
        </Tag>
      );
    }
    case "code":
      return (
        <pre className="scroll-thin mt-3 overflow-x-auto rounded-control border border-hairline bg-studio p-3 font-mono text-[0.8125rem] leading-relaxed whitespace-pre-wrap text-ink-soft first:mt-0">
          {block.text}
        </pre>
      );
  }
}

export function DoctrineBody({
  body,
  headingBase = 3,
  className,
}: {
  body: string;
  /** The heading level a body's `#` becomes (its container's is one less). */
  headingBase?: 3 | 4;
  className?: string;
}) {
  const blocks = parseDoctrineBody(body);
  return (
    <div className={cn("max-w-[68ch]", className)}>
      {blocks.map((b, i) => (
        <Block key={i} block={b} headingBase={headingBase} />
      ))}
    </div>
  );
}
