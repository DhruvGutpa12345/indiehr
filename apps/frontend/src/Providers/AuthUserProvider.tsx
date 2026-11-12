import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSetAtom } from "jotai";
import { navigate } from "raviger";
import { useCallback, useEffect, useState } from "react";

import Loading from "@/components/Common/Loading";

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

  const patientLogin = (tokenData: TokenData, redirectUrl: string) => {
    setPatientToken(tokenData);
    localStorage.setItem(
      LocalStorageKeys.patientTokenKey,
      JSON.stringify(tokenData),
    );
    navigate(redirectUrl);
  };

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

  const signOut = useCallback(
    async (why?: unknown) => {
      const idle = why === "idle";
      const everywhere = await requestSignOut(idle ? "idle" : "user");
      // Not confirmed by Keycloak (or the backend unreachable): say so, this may be a
      // shared computer. The app stays signed out here either way.
      const reason: EndReason = everywhere
        ? idle
          ? "idle"
          : "signed_out"
        : "signed_out_here_only";
      announceSignOut(reason);
      endLocally(reason);
    },
    [endLocally],
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

  const signOutIdle = useCallback(() => signOut("idle"), [signOut]);
  useIdleSignOut(!!user, signOutIdle);

  if (isLoading) {
    return <Loading />;
  }

  return (
    <AuthUserContext.Provider
      value={{
        signIn,
        signOut,
        verifyMFA,
        isAuthenticating: false,
        isVerifyingMFA: false,
        user,
        patientLogin,
        patientToken,
      }}
    >
      {user ? children : patientToken?.token ? otpAuthorized : unauthorized}
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
