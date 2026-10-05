// The country codes the code sign-in offers (DEC-287). A short list, not the
// whole ITU table: the circle's numbers are Hong Kong's and its neighbours',
// and a number from anywhere else can still be typed whole with its `+`.
// Hong Kong is first and the default.

// `trunk`: the country dials a leading 0 inside its borders that the
// international form drops (`07700 900123` in the UK is `+44 7700 900123`).
export type DialRegion = { region: string; dial: string; trunk?: boolean };

export const DIAL_REGIONS: readonly DialRegion[] = [
  { region: "HK", dial: "852" },
  { region: "MO", dial: "853" },
  { region: "CN", dial: "86", trunk: true },
  { region: "TW", dial: "886", trunk: true },
  { region: "SG", dial: "65" },
  { region: "MY", dial: "60", trunk: true },
  { region: "JP", dial: "81", trunk: true },
  { region: "KR", dial: "82", trunk: true },
  { region: "TH", dial: "66", trunk: true },
  { region: "PH", dial: "63", trunk: true },
  { region: "GB", dial: "44", trunk: true },
  { region: "US", dial: "1" },
  { region: "CA", dial: "1" },
  { region: "AU", dial: "61", trunk: true },
  { region: "NZ", dial: "64", trunk: true },
];

export const DEFAULT_REGION = "HK";

/** The region as given when it is one on the list, else Hong Kong: the form
 * comes back from the browser, so the choice is not trusted. */
export function safeRegion(region: string): DialRegion {
  return DIAL_REGIONS.find((r) => r.region === region) ?? DIAL_REGIONS[0];
}

/** The flag emoji for a two-letter region code. */
export function flag(region: string): string {
  return String.fromCodePoint(...[...region].map((c) => 0x1f1a5 + c.charCodeAt(0)));
}

/** What Nora is asked to send to: the number in E.164 when the visitor typed a
 * local number, or what they typed when it already starts with `+` (a number
 * from anywhere) or is not a phone number at all (a Signal ACI).
 *
 * A local number loses its punctuation, and its one trunk `0` where the
 * country has one; Hong Kong has none, so its numbers pass unchanged. */
export function fullNumber(region: string, typed: string): string {
  const value = typed.trim();
  if (value.startsWith("+")) return value;
  const digits = value.replace(/[\s\-().]/g, "");
  if (!/^[0-9]+$/.test(digits)) return value;
  const { dial, trunk } = safeRegion(region);
  return `+${dial}${trunk ? digits.replace(/^0/, "") : digits}`;
}
