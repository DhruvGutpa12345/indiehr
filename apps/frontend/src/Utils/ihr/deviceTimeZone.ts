// IndieHR (P1-T7b): the device's time zone, sent to our own API as X-IHR-TZ so the audit
// trail can note changes made from a computer that is not on India Standard Time. The
// backend only records it (and checks its shape); it never changes how anything is saved.
// The warning bar itself comes from /api/ihr/bootstrap.js, before this bundle loads.
import { isOwnApi } from "./idleRules";

export const TZ_HEADER = "X-IHR-TZ";
const MAX_LENGTH = 64;

/** "UTC+05:30" from Date#getTimezoneOffset (minutes WEST of UTC, so IST is -330). */
export function offsetZone(timezoneOffset: number) {
  const east = -Math.trunc(timezoneOffset);
  const sign = east >= 0 ? "+" : "-";
  const minutes = Math.abs(east);
  const hh = String(Math.floor(minutes / 60)).padStart(2, "0");
  const mm = String(minutes % 60).padStart(2, "0");
  return `UTC${sign}${hh}:${mm}`;
}

function intlZone(): string | undefined {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || undefined;
  } catch {
    return undefined;
  }
}

/** The IANA name the browser reports, or "UTC+hh:mm" when it cannot name it. */
export function deviceTimeZone(
  zone: string | undefined = intlZone(),
  timezoneOffset: number = new Date().getTimezoneOffset(),
) {
  return (zone || offsetZone(timezoneOffset)).slice(0, MAX_LENGTH);
}

/** Only our own API gets the header (never a third-party or storage URL). */
export function addTimeZoneHeader(url: string, headers: Headers) {
  if (!isOwnApi(url, window.location.origin)) return;
  headers.set(TZ_HEADER, deviceTimeZone());
}
