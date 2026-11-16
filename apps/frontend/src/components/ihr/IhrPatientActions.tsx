// IndieHR P1-T11c3 (design-c.md §6, §13.3): on the patient page and the patient home, MRD
// (anyone holding ihr_request_records_access) without a live grant on this patient gets "Ask for
// records access"; with one, a banner "Records access · mm:ss left · End now". Other roles see
// nothing. The count-down is the watcher's (IhrGrantWatcher): no timer here.
import { Link } from "raviger";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

import {
  countdownText,
  liveGrantFor,
  patientIdForGrant,
} from "@/Utils/ihr/records";
import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";

import { IhrEndGrantButton } from "./IhrEndGrantButton";
import { useIhrGrants } from "./IhrGrantWatcher";

interface IhrPatientActionsProps {
  /** the patient body CARE loaded for the page (never a search row) */
  patient: { id?: string; name: string; partial?: boolean };
  facilityId?: string;
}

export function IhrPatientActions({
  patient,
  facilityId,
}: IhrPatientActionsProps) {
  const { t } = useTranslation();
  const access = useIhrAccess();
  const grants = useIhrGrants();
  const patientId = patientIdForGrant(patient);
  if (!facilityId || !patientId) return null;
  if (access.mode === "pending" || !access.records) return null;
  // the first look has not answered yet: show neither the button nor the banner
  if (grants.status !== "ready") return null;

  const grant = liveGrantFor(grants.grants, patientId);
  if (grant) {
    return (
      <div
        role="status"
        data-ihr="grant-banner"
        className="my-2 flex flex-wrap items-center gap-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900"
      >
        <span className="font-medium">{t("ihr_records_banner")}</span>
        <span aria-hidden="true">·</span>
        <span data-ihr="banner-time-left">
          {t("ihr_records_time_left", {
            time: countdownText(grants.secondsLeft(grant)),
          })}
        </span>
        <IhrEndGrantButton grantId={grant.id} patientName={patient.name} />
      </div>
    );
  }
  return (
    <div className="my-2" data-ihr="ask-records">
      <Button asChild variant="outline" size="sm">
        <Link href={`/facility/${facilityId}/ihr/records?patient=${patientId}`}>
          {t("ihr_records_ask")}
        </Link>
      </Button>
    </div>
  );
}
