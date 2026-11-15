// IndieHR P1-T11c1: placeholder for the visit status page the front desk and billing desks see
// instead of CARE's encounter page; P1-T11c2 fills it (design-c.md §3.3).
import { useTranslation } from "react-i18next";

import Page from "@/components/Common/Page";

interface IhrEncounterStatusProps {
  facilityId: string;
  patientId: string;
  encounterId: string;
}

export function IhrEncounterStatus(_props: IhrEncounterStatusProps) {
  const { t } = useTranslation();
  return (
    <Page title={t("ihr_encounter_status_title")}>
      <p className="mt-4 text-gray-600">{t("ihr_page_not_ready")}</p>
    </Page>
  );
}
