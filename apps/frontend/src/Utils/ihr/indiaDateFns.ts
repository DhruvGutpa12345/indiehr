// IndieHR (P1-T7b): stands in for "date-fns" in our app source. The Vite plugin in
// indiaDateFnsVite.ts sends every bare `import ... from "date-fns"` under src/ here (this file
// itself and node_modules, e.g. react-day-picker, still get the real package).
// Everything is re-exported unchanged except `format` / `formatDate`, which first pass the
// pattern through the owner's India date table (indiaDateFormats.ts), and `formatRelative`,
// which uses India patterns ("08-11-2026", "today at 09:30 PM") unless a locale is passed.
// Patterns not in the table, such as the API formats "yyyy-MM-dd" and "HH:mm", are untouched.
import {
  type DateArg,
  type FormatOptions,
  type FormatRelativeOptions,
  type Locale,
  format as dateFnsFormat,
  formatRelative as dateFnsFormatRelative,
} from "date-fns";
import { enUS } from "date-fns/locale";

import { mapDateFnsFormat } from "./indiaDateFormats";

export * from "date-fns";

export function format(
  date: DateArg<Date> & {},
  formatStr: string,
  options?: FormatOptions,
): string {
  return dateFnsFormat(date, mapDateFnsFormat(formatStr), options);
}

export { format as formatDate };

// en-US words, India date and 12-hour time (en-US uses "P" = MM/dd/yyyy and "p").
const INDIA_RELATIVE = {
  lastWeek: "'last' eeee 'at' hh:mm a",
  yesterday: "'yesterday at' hh:mm a",
  today: "'today at' hh:mm a",
  tomorrow: "'tomorrow at' hh:mm a",
  nextWeek: "eeee 'at' hh:mm a",
  other: "dd-MM-yyyy",
} as const;

const indiaRelativeLocale: Locale = {
  ...enUS,
  formatRelative: (token) => INDIA_RELATIVE[token],
};

export function formatRelative(
  date: DateArg<Date> & {},
  baseDate: DateArg<Date> & {},
  options?: FormatRelativeOptions,
): string {
  return dateFnsFormatRelative(date, baseDate, {
    ...options,
    locale: options?.locale ?? indiaRelativeLocale,
  });
}
