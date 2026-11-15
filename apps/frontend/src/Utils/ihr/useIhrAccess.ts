// IndieHR P1-T11c1 (design-c.md §2.1, §13.2): our access rules as hooks. The facility comes from
// CARE's own cached query; the mode from our /api/ihr/my-access/ (never from hasPermission).
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

/**
 * Our access rules for the current facility. Outside a facility route the mode is "care"
 * (our roles always work inside one); while the facility or my-access loads it is "pending".
 */
export function useIhrAccess(): IhrAccess {
  const user = useAuthUser();
  const { facilityId, facility, isFacilityLoading } =
    useCurrentFacilitySilently();
  const isSuperuser = user.is_superuser;

  const { data, isError } = useQuery({
    queryKey: ["ihr", "my-access", facilityId],
    queryFn: query(ihrAccessApi.myAccess, {
      queryParams: { facility: facilityId },
      silent: true,
    }),
    enabled: !!facilityId && !isSuperuser,
    staleTime: 1000 * 60,
    // queryClient's default is no retry. A failure with nothing cached keeps the mode pending
    // (fail closed), so retry, then keep asking every 15 s until an answer comes.
    retry: 3,
    refetchInterval: (q) =>
      q.state.status === "error" && q.state.data === undefined ? 15_000 : false,
  });

  const facilityPerms = facility?.permissions;
  const noFacility = !facilityId;
  return useMemo(() => {
    if (noFacility) {
      return ihrAccess(
        {
          facilityPerms: [],
          myAccess: { restricted: false, bills_here: false },
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
): IhrPatientView {
  const access = useIhrAccess();
  const { facilityId } = useCurrentFacilitySilently();
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
