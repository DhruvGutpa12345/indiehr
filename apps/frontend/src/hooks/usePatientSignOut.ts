import { navigate } from "raviger";
import { useCallback } from "react";

import { LocalStorageKeys } from "@/common/constants";

const getRedirectURL = () => {
  return new URLSearchParams(window.location.search).get("redirect");
};

export const usePatientSignOut = () => {
  const signOut = useCallback(async () => {
    localStorage.removeItem(LocalStorageKeys.patientTokenKey);
    localStorage.removeItem("selectedPatient");
    // IndieHR (P1-T1d): the practitioner picked while booking (name, username) goes too
    localStorage.removeItem("user");

    const redirectURL = getRedirectURL();
    navigate(redirectURL ? `/login?redirect=${redirectURL}` : "/login");
  }, []);

  return signOut;
};
