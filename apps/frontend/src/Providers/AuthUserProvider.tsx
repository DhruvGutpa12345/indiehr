import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSetAtom } from "jotai";
import { navigate } from "raviger";
import { useCallback, useEffect, useMemo, useState } from "react";

import Loading from "@/components/Common/Loading";
import {
  clearOtherUsersFillDrafts,
  clearQuestionnaireFillDrafts,
  sweepExpiredFillDrafts,
} from "@/components/QuestionnaireV2/fill/draft/fillDraftCache";
import { AppearanceUserSync } from "@/context/AppearanceContext";

import { AuthUserContext } from "@/hooks/useAuthUser";

import { LocalStorageKeys } from "@/common/constants";

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
  unauthorized: React.ReactNode;
  otpAuthorized: React.ReactNode;
}

// IndieHR (P1-T4b, ADR-009): staff sign in through Keycloak, run by the backend. The browser
// holds only an HttpOnly cookie: no staff token is stored or refreshed here, CARE's password
// form and its two-factor step are gone, and sign-out clears everything cached in the browser.
export default function AuthUserProvider({
  children,
  unauthorized,
  otpAuthorized,
}: Props) {
  const queryClient = useQueryClient();
  const [patientToken, setPatientToken] = useState<TokenData | null>(
    JSON.parse(
      localStorage.getItem(LocalStorageKeys.patientTokenKey) || "null",
    ),
  );

  // Signed in or not is the backend's answer (a 403 here simply means "not signed in").
  // After a sign-out the backend has not confirmed yet, this browser stays signed out.
  const [pendingSignOut] = useState(signOutPending);
  const { data: currentUser, isLoading } = useQuery({
    queryKey: ["currentUser"],
    queryFn: query(userApi.currentUser, { silent: true }),
    retry: false,
    enabled: !pendingSignOut,
  });
  const user = pendingSignOut ? undefined : currentUser;

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

  const signIn = useCallback(async (): Promise<LoginResponse> => {
    startSignIn(
      getRedirectOr(window.location.pathname + window.location.search),
    );
    return new Promise<LoginResponse>(() => undefined); // the page is leaving
  }, []);

  // Keycloak runs the second factor; CARE's own step no longer exists.
  const verifyMFA = useCallback(async (): Promise<LoginResponse> => {
    navigate("/login");
    return new Promise<LoginResponse>(() => undefined);
  }, []);

  const patientLogin = useCallback(
    (tokenData: TokenData, redirectUrl: string) => {
      setPatientToken(tokenData);
      localStorage.setItem(
        LocalStorageKeys.patientTokenKey,
        JSON.stringify(tokenData),
      );
      navigate(redirectUrl);
    },
    [],
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

  // A patient sign-out in another tab (patient portal) reaches this one too.
  useEffect(() => {
    const listener = (event: StorageEvent) => {
      if (event.key === LocalStorageKeys.patientTokenKey && !event.newValue) {
        setPatientToken(null);
      }
    };
    addEventListener("storage", listener);
    return () => removeEventListener("storage", listener);
  }, []);

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
    }),
    [
      signIn,
      signOut,
      endSessionKeepingDrafts,
      verifyMFA,
      user,
      patientLogin,
      patientToken,
    ],
  );

  return (
    <AuthUserContext.Provider value={contextValue}>
      <AppearanceUserSync user={user} />
      {isLoading ? (
        <Loading />
      ) : user ? (
        children
      ) : patientToken?.token ? (
        otpAuthorized
      ) : (
        unauthorized
      )}
    </AuthUserContext.Provider>
  );
}

const getRedirectURL = () => {
  return new URLSearchParams(window.location.search).get("redirect");
};

const getRedirectOr = (fallback: string) => {
  const url = getRedirectURL();

  if (url) {
    try {
      const redirect = new URL(url);
      if (window.location.origin === redirect.origin) {
        return redirect.pathname + redirect.search;
      }
      console.error("Redirect does not belong to same origin.");
    } catch {
      console.error(`Invalid redirect URL: ${url}`);
    }
  }

  return fallback;
};
