import { useCallback } from "react";

import { clearStaffStorage } from "@/Utils/ihr/staffSession";

const getRedirectURL = () => {
  return new URLSearchParams(window.location.search).get("redirect");
};

export const usePatientSignOut = () => {
  const signOut = useCallback(async () => {
    // IndieHR (P1-T1d, P2-T0c): clearStaffStorage() removes the patient's token,
    // selectedPatient and the booking "user" (PATIENT_PORTAL_KEYS) with every other cached
    // item, and seals the storage; then a full page load, so no patient session stays in this
    // tab's memory (the auth provider's token, the query cache) for Back to reopen.
    clearStaffStorage();
    const redirectURL = getRedirectURL();
    window.location.replace(
      redirectURL
        ? `/login?${new URLSearchParams({ redirect: redirectURL })}`
        : "/login",
    );
  }, []);

  return signOut;
};
