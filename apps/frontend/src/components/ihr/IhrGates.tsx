// IndieHR P1-T11c1, P2-T0c (docs/reviews/p2-t0c/design.md §3.4): the one gate in front of every
// staff page (IhrStaffPage.tsx). Pending: CARE's loader. Not for this role: IhrNotForRole. The
// front desk on the encounter page: our status page. Otherwise CARE's own page (`children`),
// always at the same position, so CARE's pages stay mounted across tab changes.
//
// What each route id gets and what each gate shows is routeGates.ts (pure, unit-tested). The
// facility is the route match's (the leaf), never the address bar's; while the address bar
// already names another facility the gate waits (row 1).
import type { ReactNode } from "react";

import Loading from "@/components/Common/Loading";

import useAuthUser from "@/hooks/useAuthUser";

import type { IhrPatientView } from "@/Utils/ihr/access";
import {
  type Leaf,
  gateOutcome,
  isPatientFamily,
  routeGateOf,
} from "@/Utils/ihr/routeGates";
import {
  useIhrAccess,
  useIhrPatientView,
  useRestrictedAnywhere,
} from "@/Utils/ihr/useIhrAccess";
import { useCurrentFacilitySilently } from "@/pages/Facility/utils/useCurrentFacility";

import { IhrEncounterStatus } from "./IhrEncounterStatus";
import { useIhrOpenChart } from "./IhrGrantWatcher";
import { IhrNotForRole } from "./IhrNotForRole";

/**
 * A restricted records user (MRD) sees a patient in CARE's own view only under a grant: tell the
 * grant watcher this chart is open, so its end (or a list it cannot get) clears it even when no
 * list has named the grant yet (review F2).
 */
function useChartUnderGrant(
  patientId: string | undefined,
  view: IhrPatientView,
  facilityId: string | undefined,
) {
  const access = useIhrAccess({ facilityId });
  const underGrant =
    access.mode === "restricted" && access.records && view === "care";
  useIhrOpenChart(underGrant ? patientId : undefined);
}

export function IhrRouteGate({
  leaf,
  children,
}: {
  leaf: Leaf;
  children: ReactNode;
}) {
  const user = useAuthUser();
  const gate = routeGateOf(leaf.routeId);
  const family = isPatientFamily(gate);
  const patientId = family ? leaf.params.patientId : undefined;
  const facilityId = leaf.facilityId;

  // every hook unconditionally, whatever the gate (one component type, rules of hooks)
  const access = useIhrAccess({ facilityId });
  const view = useIhrPatientView(patientId, { facilityId });
  useChartUnderGrant(patientId, view, facilityId);
  const { facilityId: currentFacilityId } = useCurrentFacilitySilently();
  const restrictedAnywhere = useRestrictedAnywhere(
    family && !facilityId && !user.is_superuser,
  );

  const outcome = gateOutcome({
    gate,
    tab: leaf.params.tab,
    leafFacilityId: facilityId,
    currentFacilityId,
    patientId,
    access,
    view,
    restrictedAnywhere,
    isSuperuser: !!user.is_superuser,
  });

  switch (outcome) {
    case "loading":
      return <Loading />;
    case "notForRole":
      return <IhrNotForRole facilityId={facilityId} />;
    case "status":
      // gate "encounter" (ENCOUNTER_ID): the leaf has all three params
      if (facilityId && patientId && leaf.params.encounterId) {
        return (
          <IhrEncounterStatus
            facilityId={facilityId}
            patientId={patientId}
            encounterId={leaf.params.encounterId}
          />
        );
      }
      return <IhrNotForRole facilityId={facilityId} />;
    case "care":
      return <>{children}</>;
  }
}
