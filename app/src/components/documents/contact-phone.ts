/** A `tel:` href from a phone as authored: digits and a leading + only. The schema
 * already restricts the characters; this is the last line of defence. Null when fewer
 * than 5 digits survive, so the number is shown as plain text instead of a dead link. */
export function telHref(phone: string): string | null {
  const trimmed = phone.trim();
  const digits = trimmed.replace(/\D/g, "");
  if (digits.length < 5) return null;
  return `tel:${trimmed.startsWith("+") ? "+" : ""}${digits}`;
}

const segmenter = typeof Intl !== "undefined" && "Segmenter" in Intl ? new Intl.Segmenter("en", { granularity: "grapheme" }) : null;

/** The first grapheme of a name, upper-cased for a Latin one: one CJK character, one
 * emoji sequence or one accented letter is never cut in half. */
export function firstGrapheme(name: string): string {
  const s = name.trim();
  if (!s) return "?";
  const first = segmenter ? (segmenter.segment(s)[Symbol.iterator]().next().value?.segment ?? "") : (Array.from(s)[0] ?? "");
  return (first || "?").toLocaleUpperCase("en-US");
}
