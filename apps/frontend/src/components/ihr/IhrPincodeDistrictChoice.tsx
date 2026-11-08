// IndieHR (P1-T7b): when a PIN code covers more than one district (e.g. 110001: Central
// Delhi and New Delhi), the state is filled and these buttons offer its districts.
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

import { Organization } from "@/types/organization/organization";

interface IhrPincodeDistrictChoiceProps {
  districts: Organization[];
  onPick: (district: Organization) => void;
}

export function IhrPincodeDistrictChoice({
  districts,
  onPick,
}: IhrPincodeDistrictChoiceProps) {
  const { t } = useTranslation();
  if (!districts.length) return null;
  return (
    <div className="col-span-full mt-2" data-testid="ihr-pincode-districts">
      <p className="mb-2 text-sm text-gray-600">{t("ihr_pincode_districts")}</p>
      <div className="flex flex-wrap gap-2">
        {districts.map((district) => (
          <Button
            key={district.id}
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onPick(district)}
          >
            {district.name}
          </Button>
        ))}
      </div>
    </div>
  );
}
