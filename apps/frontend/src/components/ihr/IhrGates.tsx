// IndieHR P1-T11c1 (design-c.md §2.3, §13.2 SS4, §13.8): gates in front of CARE's route elements.
// Pending: CARE's loader. Not for this role: IhrNotForRole. Otherwise CARE's own element
// (`render` calls CARE's route function, so no copy of its tree and no hook in a route function).
import type { ReactNode } from "react";

import Loading from "@/components/Common/Loading";

import type { IhrPatientView } from "@/Utils/ihr/access";
import {
  type GateOutcome,
  bedBoardOutcome,
  encounterGateOutcome,
  encounterListOutcome,
  patientGateOutcome,
} from "@/Utils/ihr/routeKeys";
import { useIhrAccess, useIhrPatientView } from "@/Utils/ihr/useIhrAccess";

import { IhrEncounterStatus } from "./IhrEncounterStatus";
import { useIhrOpenChart } from "./IhrGrantWatcher";
import { IhrNotForRole } from "./IhrNotForRole";

interface GateProps {
  facilityId: string;
  render: () => ReactNode;
}

function show(
  outcome: GateOutcome,
  facilityId: string,
  render: () => ReactNode,
  status?: () => ReactNode,
) {
  if (outcome === "loading") return <Loading />;
  if (outcome === "notForRole")
    return <IhrNotForRole facilityId={facilityId} />;
  if (outcome === "status" && status) return <>{status()}</>;
  if (outcome === "care") return <>{render()}</>;
  return <IhrNotForRole facilityId={facilityId} />;
}

/**
 * A restricted records user (MRD) sees a patient in CARE's own view only under a grant: tell the
 * grant watcher this chart is open, so its end (or a list it cannot get) clears it even when no
 * list has named the grant yet (review F2).
 */
function useChartUnderGrant(
  patientId: string | undefined,
  view: IhrPatientView,
) {
  const access = useIhrAccess();
  const underGrant =
    access.mode === "restricted" && access.records && view === "care";
  useIhrOpenChart(underGrant ? patientId : undefined);
}

/** The encounter page: the status view for the front desk, nothing for roles without patients. */
export function IhrEncounterGate({
  facilityId,
  patientId,
  encounterId,
  render,
}: GateProps & { patientId: string; encounterId: string }) {
  const view = useIhrPatientView(patientId);
  useChartUnderGrant(patientId, view);
  return show(encounterGateOutcome(view), facilityId, render, () => (
    <IhrEncounterStatus
      facilityId={facilityId}
      patientId={patientId}
      encounterId={encounterId}
    />
  ));
}

/**
 * Patient pages and the encounter's other pages. A demographics view opens only the keys
 * `demographicsAllowed` marks (routeKeys.ts DEMOGRAPHICS_KEYS); roles without patient screens
 * none.
 */
export function IhrPatientGate({
  facilityId,
  patientId,
  demographicsAllowed,
  render,
}: GateProps & { patientId?: string; demographicsAllowed: boolean }) {
  const view = useIhrPatientView(patientId);
  useChartUnderGrant(patientId, view);
  return show(
    patientGateOutcome(view, demographicsAllowed),
    facilityId,
    render,
  );
}

/** The bed board shows who occupies each bed: refused to restricted roles (Q-K37). */
export function IhrBedBoardGate({ facilityId, render }: GateProps) {
  const access = useIhrAccess();
  return show(bedBoardOutcome(access), facilityId, render);
}

/** The facility's encounter lists: restricted roles only with the encounter-status slug. */
export function IhrEncounterListGate({ facilityId, render }: GateProps) {
  const access = useIhrAccess();
  return show(encounterListOutcome(access), facilityId, render);
}
