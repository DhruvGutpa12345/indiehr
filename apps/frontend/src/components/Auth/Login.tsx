import careConfig from "@careConfig";
import { useMutation } from "@tanstack/react-query";
import { REGEXP_ONLY_DIGITS } from "input-otp";
import { useQueryParams } from "raviger";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { isValidPhoneNumber } from "react-phone-number-input";
import { toast } from "sonner";

import { cn } from "@/lib/utils";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  InputOTP,
  InputOTPGroup,
  InputOTPSlot,
} from "@/components/ui/input-otp";
import { Label } from "@/components/ui/label";
import { PhoneInput } from "@/components/ui/phone-input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import CircularProgress from "@/components/Common/CircularProgress";
import LanguageSelectorLogin from "@/components/Common/LanguageSelectorLogin";

import { useAuthContext } from "@/hooks/useAuthUser";

import { LocalStorageKeys } from "@/common/constants";

import mutate from "@/Utils/request/mutate";
import { TokenData } from "@/types/otp/otp";
import otpApi from "@/types/otp/otpApi";

import { AuthHero } from "./AuthHero";
import { IhrStaffSignIn } from "./IhrStaffSignIn";

interface OtpLoginData {
  phone_number: string;
  otp: string;
}

interface OtpError {
  type: string;
  loc: string[];
  msg: string;
  input: string;
  ctx: {
    error: string;
  };
  url: string;
}

interface OtpValidationError {
  otp?: string;
  [key: string]: string | undefined;
}

type LoginMode = "staff" | "patient";

const Login = () => {
  // IndieHR (P1-T4b, ADR-009): staff sign in through Keycloak (IhrStaffSignIn); CARE's
  // username/password form, its reCAPTCHA and "forgot password" are gone.
  const { patientLogin } = useAuthContext();
  const {
    urls,
    stateLogo,
    customLogo,
    customLogoAlt,
    resendOtpTimeout,
    disablePatientLogin,
  } = careConfig;
  const [params, setQueryParams] = useQueryParams();
  const { mode } = params;
  const { t } = useTranslation();
  const [isOtpSent, setIsOtpSent] = useState(false);
  const [phone, setPhone] = useState("");
  const [otp, setOtp] = useState("");
  const [otpError, setOtpError] = useState<string>("");
  const [otpValidationError, setOtpValidationError] = useState<string>("");
  const [resendOtpCountdown, setResendOtpCountdown] = useState(0);

  // Timer Function for resend OTP
  useEffect(() => {
    if (resendOtpCountdown <= 0) {
      return;
    }

    const timer = setInterval(() => {
      setResendOtpCountdown((prevTime) => prevTime - 1);
    }, 1000);

    return () => clearInterval(timer);
  }, [resendOtpCountdown]);

  // Remember the last login mode
  useEffect(() => {
    localStorage.setItem(LocalStorageKeys.loginPreference, mode);
  }, [mode]);

  // Send OTP Mutation
  const { mutate: sendOtp, isPending: sendOtpPending } = useMutation({
    mutationFn: mutate(otpApi.send),
    onSuccess: () => {
      setIsOtpSent(true);
      setOtpError("");
      toast.success(t("send_otp_success"));
    },
    onError: (error: any) => {
      const errors = error?.data || [];
      if (Array.isArray(errors) && errors.length > 0) {
        const firstError = errors[0] as OtpError;
        setOtpError(firstError.msg);
      } else {
        setOtpError("send_otp_error");
      }
    },
  });

  // Verify OTP Mutation
  const { mutate: verifyOtp, isPending: verifyOtpPending } = useMutation({
    mutationFn: async (data: OtpLoginData) => {
      const response = await mutate(otpApi.login, { silent: true })(data);
      if ("errors" in response) {
        throw response;
      }
      return response;
    },
    onSuccess: async (response: { access: string }) => {
      const { access } = response;
      if (access) {
        setOtpValidationError("");
        const tokenData: TokenData = {
          token: access,
          phoneNumber: phone,
          createdAt: new Date().toISOString(),
        };
        patientLogin(tokenData, `/patient/home`);
      }
    },
    onError: (error: any) => {
      let errorMessage = "invalid_otp";
      if (
        error.cause &&
        Array.isArray(error.cause.errors) &&
        error.cause.errors.length > 0
      ) {
        const otpError = error.cause.errors.find(
          (e: OtpValidationError) => e.otp,
        );
        if (otpError && otpError.otp) {
          errorMessage = otpError.otp;
        }
      } else if (error.message) {
        errorMessage = error.message;
      }
      setOtpValidationError(errorMessage);
      toast.error(errorMessage);
    },
  });

  // Handle OTP flow
  const handlePatientLogin = async (e: React.SubmitEvent) => {
    e.preventDefault();

    if (!isOtpSent) {
      sendOtp({ phone_number: phone });
      setResendOtpCountdown(resendOtpTimeout);
    } else {
      verifyOtp({ phone_number: phone, otp });
    }
  };

  const resetPatientLogin = () => {
    setIsOtpSent(false);
    setPhone("");
    setOtp("");
    setOtpError("");
    setOtpValidationError("");
  };

  // Loading state derived from mutations
  const isLoading = sendOtpPending || verifyOtpPending;

  const logos = [stateLogo, customLogo].filter(
    (logo) => logo?.light || logo?.dark,
  );

  return (
    <div className="relative flex min-h-screen flex-col md:h-screen md:flex-row">
      <AuthHero />

      {/* Login Forms Section */}
      <div className="login-hero-form my-4 w-full md:mt-0 md:h-full md:w-1/2">
        <div className="relative h-full items-center flex justify-center md:flex">
          <div className="w-full max-w-[400px] space-y-6">
            {/* Logo for Mobile */}
            <div className="px-4 flex items-center mx-auto gap-4 md:hidden">
              {logos.map((logo, index) =>
                logo && logo.dark ? (
                  <div key={index} className="flex items-center">
                    <img
                      src={logo.dark}
                      className="h-14 rounded-lg py-3"
                      alt="state logo"
                    />
                  </div>
                ) : null,
              )}
              {logos.length === 0 && (
                <a
                  href={urls.ohcn}
                  className="inline-block"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <img
                    src={customLogoAlt?.light ?? "/images/ohc_logo_light.svg"}
                    className="h-8"
                    alt="Open Healthcare Network logo"
                  />
                </a>
              )}
            </div>
            <Card className="mx-4">
              <CardHeader className="space-y-1 px-4">
                <CardTitle className="text-2xl font-bold">
                  {t("welcome_back")}
                </CardTitle>
                <CardDescription>
                  {disablePatientLogin
                    ? t("sign_in_to_your_account_to_continue")
                    : t("choose_your_login_method_to_continue")}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {disablePatientLogin ? (
                  <IhrStaffSignIn />
                ) : (
                  <Tabs
                    value={mode === "patient" ? "patient" : "staff"}
                    onValueChange={(value) => {
                      setQueryParams({ mode: value as LoginMode });
                      if (value === "staff") {
                        resetPatientLogin();
                      }
                    }}
                  >
                    <TabsList className="flex w-full">
                      <TabsTrigger className="flex-1" value="staff">
                        {t("staff_login")}
                      </TabsTrigger>
                      {!disablePatientLogin && (
                        <TabsTrigger className="flex-1" value="patient">
                          {t("patient_login")}
                        </TabsTrigger>
                      )}
                    </TabsList>

                    {/* Staff Login */}
                    <TabsContent value="staff">
                      <IhrStaffSignIn />
                    </TabsContent>

                    {/* Patient Login */}
                    <TabsContent value="patient">
                      <form onSubmit={handlePatientLogin} className="space-y-4">
                        <div className="space-y-2">
                          <Label htmlFor="phone">{t("phone_number")}</Label>
                          <PhoneInput
                            id="phone"
                            name="phone"
                            value={phone}
                            onChange={(value) => {
                              setPhone(value ?? "");
                              setOtpError("");
                              setOtpValidationError("");
                            }}
                            disabled={isOtpSent}
                            placeholder={t("enter_phone_number")}
                          />
                          {otpError && (
                            <p className="text-sm text-red-500">
                              {t(otpError)}
                            </p>
                          )}
                        </div>

                        {isOtpSent && (
                          <div className="space-y-2">
                            <Label htmlFor="otp" className="mb-4">
                              {t("enter_otp")}
                            </Label>
                            <div className="flex justify-center">
                              <InputOTP
                                id="otp"
                                value={otp}
                                maxLength={5}
                                pattern={REGEXP_ONLY_DIGITS}
                                autoComplete="one-time-code"
                                autoFocus
                                onChange={(value) => {
                                  setOtp(value);
                                  setOtpValidationError("");
                                }}
                              >
                                <InputOTPGroup>
                                  {[...Array(5)].map((_, index) => (
                                    <InputOTPSlot
                                      key={index}
                                      index={index}
                                      className={cn(
                                        "size-10",
                                        otpValidationError &&
                                          "border-red-500 focus-visible:ring-red-500",
                                      )}
                                    />
                                  ))}
                                </InputOTPGroup>
                              </InputOTP>
                            </div>
                            {otpValidationError && (
                              <p className="text-sm text-red-500 text-center">
                                {t(otpValidationError)}
                              </p>
                            )}
                          </div>
                        )}

                        <Button
                          type="submit"
                          className="w-full"
                          variant="primary"
                          disabled={
                            isLoading ||
                            !isValidPhoneNumber(phone) ||
                            (isOtpSent && otp.length !== 5)
                          }
                        >
                          {isLoading ? (
                            <CircularProgress className="text-white" />
                          ) : isOtpSent ? (
                            t("verify_otp")
                          ) : (
                            t("send_otp")
                          )}
                        </Button>
                        {isOtpSent && (
                          <div className="flex flex-col items-center gap-2 text-center">
                            {resendOtpCountdown <= 0 ? (
                              <Button
                                variant="link"
                                type="button"
                                className="h-auto p-0"
                                onClick={() => {
                                  sendOtp({ phone_number: phone });
                                  setResendOtpCountdown(resendOtpTimeout);
                                }}
                              >
                                {t("resend_otp")}
                              </Button>
                            ) : (
                              <p className="text-sm text-gray-500">
                                {t("resend_otp_timer", {
                                  time: resendOtpCountdown,
                                })}
                              </p>
                            )}
                            <div className="flex items-center text-sm">
                              <Button
                                variant="link"
                                type="button"
                                className="h-auto p-0 text-primary-600"
                                onClick={() => {
                                  setIsOtpSent(false);
                                  setOtp("");
                                  setOtpError("");
                                  setOtpValidationError("");
                                }}
                              >
                                {t("change_phone_number")}
                              </Button>
                            </div>
                          </div>
                        )}
                      </form>
                    </TabsContent>
                  </Tabs>
                )}
              </CardContent>
            </Card>

            <LanguageSelectorLogin />
          </div>
        </div>
      </div>
    </div>
  );
};

export default Login;
