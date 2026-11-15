// IndieHR P1-T11c1 (design-c.md §13.5, Q-K48 A): the places (laboratory, store ...) the Lab
// Technician and Store Keeper belong to, as CARE lists them with `?mine=true`. Each opens the
// first page of the place the role may use (the same rule as the location menu).
import { useQuery } from "@tanstack/react-query";
import { Link } from "raviger";
import { useTranslation } from "react-i18next";

import {
  Card,
  CardContent,
  CardDescription,
  CardTitle,
} from "@/components/ui/card";

import Loading from "@/components/Common/Loading";
import Page from "@/components/Common/Page";

import { ihrPlacePage } from "@/Utils/ihr/menu";
import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";
import query from "@/Utils/request/query";
import locationApi from "@/types/location/locationApi";

import { IhrNotForRole } from "./IhrNotForRole";

interface IhrMyPlacesProps {
  facilityId: string;
}

export function IhrMyPlaces({ facilityId }: IhrMyPlacesProps) {
  const { t } = useTranslation();
  const access = useIhrAccess();
  const page = ihrPlacePage(access);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["ihr", "my-places", facilityId],
    queryFn: query(locationApi.list, {
      pathParams: { facility_id: facilityId },
      queryParams: { mine: "true", limit: 100 },
    }),
    enabled: access.mode !== "pending" && page !== undefined,
  });

  if (access.mode === "pending") return <Loading />;
  if (page === undefined) return <IhrNotForRole facilityId={facilityId} />;

  return (
    <Page title={t("ihr_menu_my_places")}>
      <div className="mt-4 space-y-3" data-ihr="my-places">
        {isLoading && <Loading />}
        {isError && <p className="text-red-700">{t("ihr_my_places_error")}</p>}
        {data && data.results.length === 0 && (
          <p className="text-gray-600">{t("ihr_my_places_empty")}</p>
        )}
        {data?.results.map((place) => (
          <Link
            key={place.id}
            href={`/facility/${facilityId}/locations/${place.id}${page}`}
            className="block"
          >
            <Card className="transition hover:bg-gray-50">
              <CardContent className="p-4">
                <CardTitle className="text-base">{place.name}</CardTitle>
                {place.description && (
                  <CardDescription>{place.description}</CardDescription>
                )}
              </CardContent>
            </Card>
          </Link>
        ))}
      </div>
    </Page>
  );
}
