// IndieHR P1-T11c1: empty shell for the MRD records-access page; P1-T11c3 fills it
// (design-c.md §6, §13.3, §13.4).
import { useTranslation } from "react-i18next";

import Loading from "@/components/Common/Loading";
import Page from "@/components/Common/Page";

import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";

import { IhrNotForRole } from "./IhrNotForRole";

interface IhrRecordsAccessProps {
  facilityId: string;
}

export function IhrRecordsAccess({ facilityId }: IhrRecordsAccessProps) {
  const { t } = useTranslation();
  const access = useIhrAccess();
  if (access.mode === "pending") return <Loading />;
  if (!access.records) return <IhrNotForRole facilityId={facilityId} />;
  return (
    <Page title={t("ihr_menu_records_access")}>
      <p className="mt-4 text-gray-600">{t("ihr_page_not_ready")}</p>
    </Page>
  );
}
