// IndieHR (P1-T4b, ADR-009): staff sign in through Keycloak, run by the backend. This replaces
// CARE's username/password form; the patient OTP sign-in is unchanged.
import { useQueryParams } from "raviger";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

import { startSignIn } from "@/Utils/ihr/staffSession";

// Outcomes the backend reports on /login?error=… and the reasons we add on sign-out.
const MESSAGES: Record<string, string> = {
  cancelled: "ihr_sign_in_cancelled",
  expired: "ihr_sign_in_expired",
  refused: "ihr_sign_in_refused",
  not_linked: "ihr_sign_in_not_linked",
  reauth: "ihr_sign_in_reauth",
  unavailable: "ihr_sign_in_unavailable",
  idle: "ihr_signed_out_idle",
  signed_out: "ihr_signed_out",
  signed_out_here_only: "ihr_signed_out_here_only",
  session_ended: "ihr_session_ended",
};

export function IhrStaffSignIn() {
  const { t } = useTranslation();
  const [params] = useQueryParams();
  const notice = MESSAGES[params.error ?? params.reason ?? ""];
  const isError = Boolean(params.error);

  return (
    <div className="space-y-4">
      {notice && (
        <p
          role={isError ? "alert" : "status"}
          className={isError ? "text-sm text-red-600" : "text-sm text-gray-700"}
        >
          {t(notice)}
        </p>
      )}
      <Button
        type="button"
        className="w-full"
        variant="primary"
        onClick={() =>
          startSignIn(
            params.redirect ??
              (window.location.pathname === "/login"
                ? null
                : window.location.pathname + window.location.search),
          )
        }
      >
        {t("ihr_sign_in_with_hospital_account")}
      </Button>
      <p className="text-xs text-gray-500">{t("ihr_sign_in_help")}</p>
    </div>
  );
}
