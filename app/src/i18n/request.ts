import { getRequestConfig } from "next-intl/server";
import { hasLocale } from "next-intl";
import { routing } from "./routing";

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = hasLocale(routing.locales, requested) ? requested : routing.defaultLocale;
  return {
    locale,
    // One wall clock for everyone: a shared group board reads the same time
    // wherever the viewer happens to be, and the rhythm bins are bucketed
    // server-side in this zone already.
    timeZone: "Asia/Hong_Kong",
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
