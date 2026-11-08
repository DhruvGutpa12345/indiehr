// IndieHR (P1-T7b): fill state and district in an address form from the PIN code the user
// typed. The lookup goes to our own API (same origin), which answers from our copy of India
// Post's directory; no external host is called. Staff use their session cookie; the
// public appointment page passes the patient's OTP token.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  autofillTarget,
  completePincode,
  lineage,
  pickFromPlaces,
  type PincodePlace,
} from "@/Utils/ihr/pincodeRules";
import query from "@/Utils/request/query";
import { HttpMethod, Type } from "@/Utils/request/types";
import { Organization } from "@/types/organization/organization";

export interface PincodeLookup {
  pincode: string;
  places: PincodePlace<Organization>[];
}

export const ihrPincodeApi = {
  lookup: {
    path: "/api/ihr/pincode/{pincode}/",
    method: HttpMethod.GET,
    TRes: Type<PincodeLookup>(),
  },
} as const;

interface PincodeAutofillOptions {
  /** The PIN code typed by the user; pass nothing until the user has changed it. */
  pincode?: number | string | null;
  /** The patient's OTP token on the public appointment page. */
  authToken?: string;
  /** The organisation currently selected in the form. */
  value?: Organization | null;
  /** The form's own change handler (keeps each form's validity rules). */
  onChange: (organization: Organization | null) => void;
}

/**
 * Looks up a complete PIN code and, once per answer, puts its district (or its state, when
 * the PIN spans several districts) into the form, unless the user's choice already fits.
 * Returns the districts to offer as quick choices. Unknown PINs, a directory that is not
 * loaded and network errors are silent: the user picks by hand as before.
 */
export function usePincodeAutofill({
  pincode,
  authToken,
  value,
  onChange,
}: PincodeAutofillOptions) {
  const { t } = useTranslation();
  const pin = completePincode(pincode);

  const { data } = useQuery({
    queryKey: ["ihr-pincode", pin, !!authToken],
    queryFn: query(ihrPincodeApi.lookup, {
      pathParams: { pincode: pin ?? "" },
      silent: true,
      ...(authToken
        ? { headers: { Authorization: `Bearer ${authToken}` } }
        : {}),
    }),
    enabled: pin !== null,
    retry: false,
    staleTime: Infinity,
  });

  const result = pin !== null && data?.pincode === pin ? data : undefined;
  const pick = pickFromPlaces(result?.places);
  const applied = useRef<string | null>(null);

  useEffect(() => {
    if (!result || applied.current === result.pincode) return;
    applied.current = result.pincode;
    const target = autofillTarget(pick, value);
    if (!target) return;
    onChange(target);
    toast.success(
      pick?.kind === "district"
        ? t("pincode_autofill")
        : t("ihr_pincode_state_filled"),
    );
  }, [result, pick, value, onChange, t]);

  if (pick?.kind !== "state") return { districts: [] as Organization[] };
  const chosen = lineage(value);
  const settled = pick.choices.some((d) => chosen.includes(d.id));
  return { districts: settled ? [] : pick.choices };
}
