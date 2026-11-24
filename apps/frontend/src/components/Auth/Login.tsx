import careConfig from "@careConfig";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { AuthCard } from "@/components/Auth/AuthCard";
import { AuthLayout } from "@/components/Auth/AuthLayout";
import { IhrStaffSignIn } from "@/components/Auth/IhrStaffSignIn";
import { PatientLoginForm } from "@/components/Auth/PatientLoginForm";

import { LocalStorageKeys } from "@/common/constants";

import { useQueryParams } from "@/lib/router/queryParams";
import { loginPageSearch } from "@/lib/router/search/auth";

type LoginMode = "staff" | "patient";

interface LoginProps {
  /** Upstream's password-reset entry. IndieHR: ignored (the route redirects to /login). */
  forgot?: boolean;
}

const Login = (_props: LoginProps) => {
  // IndieHR (P1-T4b, ADR-009; P2-T0c): staff sign in through Keycloak (IhrStaffSignIn); CARE's
  // username/password form (StaffLoginForm), its reCAPTCHA and "forgot password"
  // (ForgotPassword, the `forgot` prop) are not rendered. The patient OTP tab is unchanged.
  const { t } = useTranslation();
  const [{ mode }, setQueryParams] = useQueryParams(loginPageSearch);

  const activeMode: LoginMode =
    !careConfig.disablePatientLogin && mode === "patient" ? "patient" : "staff";

  // Remembered so the landing page's sign-in button reopens the same tab.
  useEffect(() => {
    localStorage.setItem(LocalStorageKeys.loginPreference, activeMode);
  }, [activeMode]);

  const staffPanel = (
    <AuthCard title={t("staff_sign_in")}>
      <IhrStaffSignIn />
    </AuthCard>
  );

  return (
    <AuthLayout>
      {careConfig.disablePatientLogin ? (
        staffPanel
      ) : (
        <Tabs
          value={activeMode}
          onValueChange={(value) => {
            setQueryParams({ mode: value });
          }}
          className="w-full gap-4"
        >
          <TabsList
            aria-label={t("sign_in_method")}
            className="h-12 w-full md:mt-4"
          >
            <TabsTrigger value="staff" className="min-h-11 text-base">
              {t("staff_sign_in")}
            </TabsTrigger>
            <TabsTrigger value="patient" className="min-h-11 text-base">
              {t("patient_sign_in")}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="staff" className="flex-none">
            {staffPanel}
          </TabsContent>
          <TabsContent value="patient" className="flex-none">
            <PatientLoginForm />
          </TabsContent>
        </Tabs>
      )}
    </AuthLayout>
  );
};

export default Login;
