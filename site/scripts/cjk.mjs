// The CJK characters a page can show: CJK ideographs, CJK punctuation and
// fullwidth forms. Shared by the subsetter and the build's coverage check so
// the two can never disagree about what counts.
// Includes the supplementary ideograph planes (Ext B onward), where written
// Cantonese keeps characters like 𨋢 and 𠵱.
const CJK = /[　-〿㐀-䶿一-鿿豈-﫿＀-￯\u{20000}-\u{323af}]/gu;

export function cjkChars(text) {
  return new Set(text.match(CJK) ?? []);
}
