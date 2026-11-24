// IndieHR (P2-T0b review F2): CARE 3.1's appearance sync (AppearanceContext save) binds
// each save to a bearer token read from localStorage. Our staff sign-in holds only an
// HttpOnly cookie (ADR-009), so there is never a token and every save failed. A save on
// a cookie session uses this marker instead: the owner check (current user = the user
// the draft belongs to) still runs once per page, and no Authorization header is sent,
// so the request goes with the cookie and the session headers callApi adds.

export const COOKIE_SESSION = "ihr-cookie-session";

/** The bearer header when CARE has one (never on our staff hosts), else the marker. */
export function sessionAuthorization(bearer: string | null): string {
  return bearer ?? COOKIE_SESSION;
}

/** Headers for a save bound to `authorization`: none for the cookie session. */
export function authorizationHeaders(
  authorization: string,
): Record<string, string> {
  return authorization === COOKIE_SESSION
    ? {}
    : { Authorization: authorization };
}
