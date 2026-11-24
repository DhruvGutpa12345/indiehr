// IndieHR P1-T11c1 (design-c.md §2.1, §13.2): our access rules as hooks. The facility comes from
// CARE's own cached query; the mode from our /api/ihr/my-access/ (never from hasPermission).
// P2-T0c (design §3.4): the route gate and the grant watcher pass the facility of the route match
// they guard (`{ facilityId }`), never the address bar's; CARE screens call them without it.
import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import useAuthUser from "@/hooks/useAuthUser";

import {
  type IhrAccess,
  type IhrPatientView,
  type MyAccess,
  ihrAccess,
  ihrPatientView,
  myAccessFrom,
} from "@/Utils/ihr/access";
import { ihrAccessApi } from "@/Utils/ihr/ihrApi";
import { IHR_SLUG_SETS } from "@/Utils/ihr/ihrSlugSets";
import query from "@/Utils/request/query";
import { useCurrentFacilitySilently } from "@/pages/Facility/utils/useCurrentFacility";
import patientApi from "@/types/emr/patient/patientApi";
import facilityApi from "@/types/facility/facilityApi";

/**
 * Which facility to answer for. Absent: the one the address bar names (CARE screens). Present:
 * exactly `facilityId`, also when it is undefined (no facility) — the route gate's own match.
 */
export interface IhrFacilityOption {
  facilityId: string | undefined;
}

// queryClient's default is no retry. A failure with nothing cached keeps the mode pending (fail
// closed), so retry, then keep asking every 15 s until an answer comes.
const MY_ACCESS_RETRY = {
  retry: 3,
  refetchInterval: (q: {
    state: { status: string; data: MyAccess | undefined };
  }) =>
    q.state.status === "error" && q.state.data === undefined ? 15_000 : false,
} as const;

/** The facility (CARE's query and key, useCurrentFacility.tsx) the hooks answer for. */
function useIhrFacility(opts: IhrFacilityOption | undefined) {
  const current = useCurrentFacilitySilently();
  const facilityId = opts ? opts.facilityId : current.facilityId;
  const { data: facility, isLoading: isFacilityLoading } = useQuery({
    queryKey: ["facility", facilityId],
    queryFn: query(facilityApi.get, {
      pathParams: { facilityId: facilityId ?? "" },
    }),
    staleTime: 1000 * 60 * 5,
    enabled: !!opts && !!facilityId,
  });
  if (!opts) return current;
  return { facilityId, facility, isFacilityLoading };
}

/**
 * Our access rules for the current facility (or `opts.facilityId`). Outside a facility the mode
 * is "care" (our roles always work inside one); while the facility or my-access loads it is
 * "pending".
 */
export function useIhrAccess(opts?: IhrFacilityOption): IhrAccess {
  const user = useAuthUser();
  const { facilityId, facility, isFacilityLoading } = useIhrFacility(opts);
  const isSuperuser = user.is_superuser;

  // explicit type: the refetchInterval callback otherwise leaves `data` inferred as unknown
  const { data, isError } = useQuery<MyAccess>({
    queryKey: ["ihr", "my-access", facilityId],
    queryFn: query(ihrAccessApi.myAccess, {
      queryParams: { facility: facilityId },
      silent: true,
    }),
    enabled: !!facilityId && !isSuperuser,
    staleTime: 1000 * 60,
    ...MY_ACCESS_RETRY,
  });

  const facilityPerms = facility?.permissions;
  const noFacility = !facilityId;
  return useMemo(() => {
    if (noFacility) {
      return ihrAccess(
        {
          facilityPerms: [],
          myAccess: {
            restricted: false,
            bills_here: false,
            hospital_admin: false,
          },
          isSuperuser,
        },
        IHR_SLUG_SETS,
      );
    }
    const myAccess: MyAccess | null | undefined = myAccessFrom({
      data,
      isError,
    });
    return ihrAccess(
      {
        // a facility that failed to load: no slugs, CARE's own errors show
        facilityPerms: isFacilityLoading ? undefined : (facilityPerms ?? []),
        myAccess,
        isSuperuser,
      },
      IHR_SLUG_SETS,
    );
  }, [
    noFacility,
    isSuperuser,
    isFacilityLoading,
    facilityPerms,
    data,
    isError,
  ]);
}

/**
 * What the user may be shown of one patient. Roles that never see patient screens are
 * answered without fetching the patient; the others share CARE's own patient query.
 */
export function useIhrPatientView(
  patientId: string | undefined,
  opts?: IhrFacilityOption,
): IhrPatientView {
  const access = useIhrAccess(opts);
  const current = useCurrentFacilitySilently();
  const facilityId = opts ? opts.facilityId : current.facilityId;
  const needsPatient =
    access.mode === "restricted" && access.demographics && !!patientId;

  const { data, isError } = useQuery({
    queryKey: ["patient", patientId, facilityId],
    queryFn: query(patientApi.get, {
      pathParams: { id: patientId ?? "" },
      queryParams: { facility: facilityId },
    }),
    enabled: needsPatient,
  });

  if (!needsPatient) return ihrPatientView(access, []);
  return ihrPatientView(access, isError ? [] : data?.permissions);
}

/**
 * Whether this person is restricted anywhere (GET /api/ihr/my-access/ without a facility: the
 * backend's per-person rule). Undefined while unknown or disabled. Used where no facility is
 * in the address: the route gate on organization patient pages (design §3.4 row 3) and the
 * grant watcher on the facility-less charts. Superusers: the callers pass `enabled` false.
 */
export function useRestrictedAnywhere(enabled: boolean): boolean | undefined {
  const { data } = useQuery<MyAccess>({
    queryKey: ["ihr", "my-access", undefined],
    queryFn: query(ihrAccessApi.myAccess, { silent: true }),
    enabled,
    staleTime: 1000 * 60,
    ...MY_ACCESS_RETRY,
  });
  if (!enabled || data === undefined) return undefined;
  return !!data.restricted;
}
