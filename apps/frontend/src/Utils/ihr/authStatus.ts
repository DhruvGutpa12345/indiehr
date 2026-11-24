// IndieHR P2-T0c (docs/reviews/p2-t0c/design.md §5.1): the session status of CARE's router
// (lib/router/auth.ts AuthStatus) from AuthUserProvider's own state: a thin function, not a
// second state machine. The router's guards read it synchronously (setAuthStatusResolver, from
// the query cache) and the rendered context derives `user`/`isUserLoading` from it, so
// AreaChrome's rendered status and the guards always agree.
//
// Pure, no "@/" import: run by `node --import tsx --test` (authStatus.test.ts).

/** Same values as lib/router/auth.ts AuthStatus (AuthUserProvider checks the assignment). */
export type IhrAuthStatus = "loading" | "user" | "patient" | "anonymous";

export interface IhrAuthInput {
  /** a sign-out the backend has not confirmed yet (staffSession.ts signOutPending) */
  pendingSignOut: boolean;
  /** the `["currentUser"]` query's state; undefined before it was ever asked */
  user:
    { status: "pending" | "error" | "success"; hasData: boolean } | undefined;
  /** the patient portal's OTP token */
  patientToken: { token?: string } | null | undefined;
}

export function ihrAuthStatus({
  pendingSignOut,
  user,
  patientToken,
}: IhrAuthInput): IhrAuthStatus {
  const signedOutAs: IhrAuthStatus = patientToken?.token
    ? "patient"
    : "anonymous";
  // the app stays signed out here until the backend confirms (and the query never runs)
  if (pendingSignOut) return signedOutAs;
  // a refetch error keeps the data: still signed in (a server-ended session is found by the
  // 403 handler, errorHandler.ts endExpiredSession)
  if (user?.hasData) return "user";
  if (!user || user.status === "pending") return "loading";
  return signedOutAs;
}
