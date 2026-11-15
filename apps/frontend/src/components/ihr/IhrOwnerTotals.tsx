// IndieHR P1-T11c1: empty shell for the Owner's hospital totals page; P1-T11c3 fills it
// (design-c.md §7, §13.5).
import { useTranslation } from "react-i18next";

import Loading from "@/components/Common/Loading";
import Page from "@/components/Common/Page";

import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";

import { IhrNotForRole } from "./IhrNotForRole";

interface IhrOwnerTotalsProps {
  facilityId: string;
}

export function IhrOwnerTotals({ facilityId }: IhrOwnerTotalsProps) {
  const { t } = useTranslation();
  const access = useIhrAccess();
  if (access.mode === "pending") return <Loading />;
  if (!access.totals) return <IhrNotForRole facilityId={facilityId} />;
  return (
    <Page title={t("ihr_menu_hospital_totals")}>
      <p className="mt-4 text-gray-600">{t("ihr_page_not_ready")}</p>
    </Page>
  );
}
