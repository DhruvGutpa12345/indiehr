import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSetAtom } from "jotai";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  clearOtherUsersFillDrafts,
  clearQuestionnaireFillDrafts,
  sweepExpiredFillDrafts,
} from "@/components/QuestionnaireV2/fill/draft/fillDraftCache";
import { AppearanceUserSync } from "@/context/AppearanceContext";

import { AuthUserContext } from "@/hooks/useAuthUser";

import { LocalStorageKeys } from "@/common/constants";

import { type AuthStatus, setAuthStatusResolver } from "@/lib/router/auth";
import { navigate } from "@/lib/router/navigate";
import { getRouter } from "@/lib/router/routerRef";

import { type IhrAuthInput, ihrAuthStatus } from "@/Utils/ihr/authStatus";
import {
  EndReason,
  announceSignOut,
  clearStaffStorage,
  forgetLegacyTokens,
  onSignOutElsewhere,
  requestSignOut,
  setStaffSignedIn,
  signOutPending,
  startSignIn,
} from "@/Utils/ihr/staffSession";
import { useIdleSignOut } from "@/Utils/ihr/useIdleSignOut";
import query from "@/Utils/request/query";
import { userAtom } from "@/atoms/user-atom";
import { LoginResponse } from "@/types/auth/auth";
import { TokenData } from "@/types/otp/otp";
import userApi from "@/types/user/userApi";

interface Props {
  children: React.ReactNode;
}

const CURRENT_USER_KEY = ["currentUser"];

/** The `["currentUser"]` query's state as ihrAuthStatus reads it. */
function userState(state: {
  status: "pending" | "error" | "success";
  data: unknown;
}): IhrAuthInput["user"] {
  return { status: state.status, hasData: state.data !== undefined };
}

// IndieHR (P1-T4b, ADR-009; P2-T0c): staff sign in through Keycloak, run by the backend. The
// browser holds only an HttpOnly cookie: no staff token is stored or refreshed here, CARE's
// password form and its two-factor step are gone, and sign-out clears everything cached in the
// browser (always a full page load). Upstream's router integration is kept: the router's guards
// read the session status through `setAuthStatusResolver`, and a status change re-runs them.
// Guards and the rendered chrome both use ihrAuthStatus (Utils/ihr/authStatus.ts), so they
// agree (docs/reviews/p2-t0c/design.md §5.1).
export default function AuthUserProvider({ children }: Props) {
  const queryClient = useQueryClient();
  const [patientToken, setPatientTokenState] = useState<TokenData | null>(
    JSON.parse(
      localStorage.getItem(LocalStorageKeys.patientTokenKey) || "null",
    ),
  );
  // Mirror of the patient token, updated in the same call as the state, so the router's
  // resolver is current before the next render.
  const patientTokenRef = useRef(patientToken);
  const setPatientToken = useCallback((token: TokenData | null) => {
    patientTokenRef.current = token;
    setPatientTokenState(token);
  }, []);

  // Signed in or not is the backend's answer (a 403 here simply means "not signed in").
  // After a sign-out the backend has not confirmed yet, this browser stays signed out.
  const [pendingSignOut] = useState(signOutPending);

  useState(() =>
    setAuthStatusResolver((): AuthStatus => {
      const state = queryClient.getQueryState(CURRENT_USER_KEY);
      return ihrAuthStatus({
        pendingSignOut,
        user: state ? userState(state) : undefined,
        patientToken: patientTokenRef.current,
      });
    }),
  );

  const currentUserQuery = useQuery({
    queryKey: CURRENT_USER_KEY,
    queryFn: query(userApi.currentUser, { silent: true }),
    retry: false,
    enabled: !pendingSignOut,
  });

  const status: AuthStatus = ihrAuthStatus({
    pendingSignOut,
    user: userState(currentUserQuery),
    patientToken,
  });
  const user = status === "user" ? currentUserQuery.data : undefined;
  const isUserLoading = status === "loading";

  // Run the route guards again when the session changes without a navigation (the current
  // user loaded, a patient token removed in another tab).
  const lastStatus = useRef(status);
  useLayoutEffect(() => {
    if (lastStatus.current === status) return;
    lastStatus.current = status;
    void getRouter().invalidate();
  }, [status]);

  useEffect(() => {
    forgetLegacyTokens(); // CARE's old staff tokens are useless now
    if (pendingSignOut) requestSignOut("user"); // retry until the backend confirms
  }, [pendingSignOut]);

  useEffect(() => {
    setStaffSignedIn(!!user);
  }, [user]);

  const setUser = useSetAtom(userAtom);
  useEffect(() => {
    setUser(user);
  }, [user, setUser]);

  // Boot-time housekeeping — drop any fill drafts that outlived their TTL,
  // regardless of which user (or logged-out session) wrote them. Fresh
  // drafts (e.g. one saved from the login form) are left alone.
  useEffect(() => {
    sweepExpiredFillDrafts();
  }, []);

  // IndieHR: sign-in happens at the backend (Keycloak), so the first point the account is
  // known here is the current-user answer. A draft left by a DIFFERENT account on this
  // computer must not survive into this session; this person's own draft (left by an
  // expiry) is the recovery case and stays.
  const userId = user?.id;
  useEffect(() => {
    if (userId) clearOtherUsersFillDrafts(userId);
  }, [userId]);

  // `redirect` (absolute, from the router's staff guard) or this page; startSignIn keeps
  // same-origin paths only (idleRules.ts safeNext).
  const signIn = useCallback(async (): Promise<LoginResponse> => {
    startSignIn(
      new URLSearchParams(window.location.search).get("redirect") ??
        window.location.pathname + window.location.search,
    );
    return new Promise<LoginResponse>(() => undefined); // the page is leaving
  }, []);

  // Keycloak runs the second factor; CARE's own step no longer exists.
  const verifyMFA = useCallback(async (): Promise<LoginResponse> => {
    navigate({ href: "/login" });
    return new Promise<LoginResponse>(() => undefined);
  }, []);

  const patientLogin = useCallback(
    (tokenData: TokenData, redirectUrl: string) => {
      setPatientToken(tokenData);
      localStorage.setItem(
        LocalStorageKeys.patientTokenKey,
        JSON.stringify(tokenData),
      );
      navigate({ href: redirectUrl });
    },
    [setPatientToken],
  );

  const endLocally = useCallback(
    (reason: EndReason) => {
      setStaffSignedIn(false);
      queryClient.clear();
      clearStaffStorage(); // also the patient portal's token and booking "user" (P1-T1d)
      // A full page load, so nothing of the session stays in memory either.
      window.location.replace(`/login?${new URLSearchParams({ reason })}`);
    },
    [queryClient],
  );

  // `clearDrafts` keeps upstream's split between a deliberate sign-out and the involuntary
  // ends (idle time-out, the session-expired page), but on our staff hosts every end also runs
  // clearStaffStorage(), which removes the questionnaire fill drafts (`care_qn_fill_draft--`,
  // answers about a patient) whatever the reason: cached data goes at sign-out (Q-T9, P1-T1d;
  // P2-T0b). Upstream's recovery of a draft after an idle end is therefore not offered here.
  const endSession = useCallback(
    async (idle: boolean, clearDrafts: boolean) => {
      const everywhere = await requestSignOut(idle ? "idle" : "user");
      // Not confirmed by Keycloak (or the backend unreachable): say so, this may be a
      // shared computer. The app stays signed out here either way.
      const reason: EndReason = everywhere
        ? idle
          ? "idle"
          : "signed_out"
        : "signed_out_here_only";
      if (clearDrafts) clearQuestionnaireFillDrafts();
      announceSignOut(reason);
      endLocally(reason);
    },
    [endLocally],
  );

  const signOut = useCallback(() => endSession(false, true), [endSession]);
  const endSessionKeepingDrafts = useCallback(
    () => endSession(false, false),
    [endSession],
  );

  // A sign-out in another tab of this hospital ends this one at once.
  useEffect(() => onSignOutElsewhere(endLocally), [endLocally]);

  // A patient sign-out in another tab (patient portal) reaches this one too; the status
  // change re-runs the guards.
  useEffect(() => {
    const listener = (event: StorageEvent) => {
      if (event.key === LocalStorageKeys.patientTokenKey && !event.newValue) {
        setPatientToken(null);
      }
    };
    addEventListener("storage", listener);
    return () => removeEventListener("storage", listener);
  }, [setPatientToken]);

  const signOutIdle = useCallback(() => endSession(true, false), [endSession]);
  useIdleSignOut(!!user, signOutIdle);

  const contextValue = useMemo(
    () => ({
      signIn,
      signOut,
      endSessionKeepingDrafts,
      verifyMFA,
      isAuthenticating: false,
      isVerifyingMFA: false,
      user,
      patientLogin,
      patientToken,
      isUserLoading,
    }),
    [
      signIn,
      signOut,
      endSessionKeepingDrafts,
      verifyMFA,
      user,
      patientLogin,
      patientToken,
      isUserLoading,
    ],
  );

  // The root route shows the loading screen and picks the chrome (staff, patient, public).
  return (
    <AuthUserContext.Provider value={contextValue}>
      <AppearanceUserSync user={user} />
      {children}
    </AuthUserContext.Provider>
  );
}
