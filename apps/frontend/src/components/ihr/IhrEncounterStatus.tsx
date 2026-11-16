// IndieHR P1-T11c2 (design-c.md §3.3, §3.1, Q-K50 A, Q-K37a B): the visit status page the front
// desk, the billing desks and MRD (without a grant) see instead of CARE's encounter page, for
// every "View encounter" link and after "create encounter" (IhrEncounterGate).
//
// It shows what the backend's projection keeps (ihr_access/fields/encounter.txt): status, class,
// dates, priority, visit number, status history and the booking time. The Cashier and the TPA
// Desk also get the current bed, the bed stays and a link to the visit's account
// (fields/encounter_billing.txt, Q-K37a B). A field the backend removes is never drawn as empty:
// there is no department, care team, location or tag row at all.
import { useQuery } from "@tanstack/react-query";
import { Link } from "raviger";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

import Loading from "@/components/Common/Loading";
import Page from "@/components/Common/Page";

import { ihrAccountOfVisitQuery, ihrStatusBlocks } from "@/Utils/ihr/frontDesk";
import { indiaDateTime } from "@/Utils/ihr/indiaDateFormats";
import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";
import query from "@/Utils/request/query";
import accountApi from "@/types/billing/account/accountApi";
import encounterApi from "@/types/emr/encounter/encounterApi";

interface IhrEncounterStatusProps {
  facilityId: string;
  patientId: string;
  encounterId: string;
}

function Row({
  label,
  testId,
  children,
}: {
  label: string;
  testId?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="sm:col-span-1" data-ihr={testId}>
      <dt className="text-sm font-normal leading-5 text-gray-500">{label}</dt>
      <dd className="mt-1 text-sm font-semibold leading-5 text-gray-900">
        {children}
      </dd>
    </div>
  );
}

export function IhrEncounterStatus({
  facilityId,
  patientId,
  encounterId,
}: IhrEncounterStatusProps) {
  const { t } = useTranslation();
  const access = useIhrAccess();
  const blocks = ihrStatusBlocks(access);

  const {
    data: encounter,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["ihr", "encounter-status", encounterId, facilityId],
    queryFn: query(encounterApi.get, {
      pathParams: { id: encounterId },
      queryParams: { facility: facilityId },
    }),
    enabled: access.mode === "restricted",
  });

  const { data: accounts } = useQuery({
    queryKey: ["ihr", "visit-account", facilityId, encounterId],
    queryFn: query(accountApi.listAccount, {
      pathParams: { facilityId },
      queryParams: ihrAccountOfVisitQuery(encounterId),
    }),
    enabled: access.mode === "restricted" && blocks.accountLink,
  });
  const account = accounts?.results?.[0];

  const title = t("ihr_encounter_status_title");

  if (isLoading || access.mode === "pending") return <Loading />;
  if (isError || !encounter) {
    return (
      <Page title={title}>
        <p className="mt-4 text-red-700" data-ihr="encounter-status-error">
          {t("ihr_encounter_status_error")}
        </p>
      </Page>
    );
  }

  const patient = encounter.patient;
  const history = encounter.status_history?.history ?? [];
  const booking = encounter.appointment;
  const stays = encounter.location_history ?? [];

  return (
    <Page title={title}>
      <div className="mt-4 space-y-4 md:max-w-4xl" data-ihr="encounter-status">
        <div className="rounded-md bg-white p-5 shadow-sm">
          <dl className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
            <Row label={t("ihr_visit_patient")}>
              <Link
                href={`/facility/${facilityId}/patient/${patientId}/demography`}
                className="text-primary-700 underline"
              >
                {patient?.name}
              </Link>
            </Row>
            <Row label={t("ihr_visit_type")}>
              {t(`encounter_class__${encounter.encounter_class}`)}
            </Row>
            <Row label={t("ihr_visit_status")} testId="visit-status">
              {t(`encounter_status__${encounter.status}`)}
            </Row>
            <Row label={t("ihr_visit_priority")}>
              {t(`encounter_priority__${encounter.priority}`)}
            </Row>
            <Row label={t("ihr_visit_started")}>
              {encounter.period?.start
                ? indiaDateTime(encounter.period.start)
                : t("ihr_visit_not_started")}
            </Row>
            <Row label={t("ihr_visit_ended")}>
              {encounter.period?.end
                ? indiaDateTime(encounter.period.end)
                : t("ihr_visit_ongoing")}
            </Row>
            <Row label={t("ihr_visit_number")}>
              {encounter.external_identifier || "-"}
            </Row>
          </dl>
        </div>

        {booking && (
          <div className="rounded-md bg-white p-5 shadow-sm">
            <h2 className="mb-3 text-lg font-semibold">
              {t("ihr_visit_booking")}
            </h2>
            <dl className="grid grid-cols-1 gap-x-4 gap-y-4 sm:grid-cols-2">
              {booking.booked_on && (
                <Row label={t("ihr_visit_booked_on")}>
                  {indiaDateTime(booking.booked_on)}
                </Row>
              )}
              {booking.token_slot?.start_datetime && (
                <Row label={t("ihr_visit_slot")}>
                  {indiaDateTime(booking.token_slot.start_datetime)}
                  {booking.token_slot.end_datetime &&
                    ` - ${indiaDateTime(booking.token_slot.end_datetime)}`}
                </Row>
              )}
            </dl>
          </div>
        )}

        {history.length > 0 && (
          <div className="rounded-md bg-white p-5 shadow-sm">
            <h2 className="mb-3 text-lg font-semibold">
              {t("ihr_visit_history")}
            </h2>
            <ul className="space-y-1 text-sm" data-ihr="visit-history">
              {history.map((item, index) => (
                <li key={`${item.moved_at}-${index}`}>
                  <span className="font-semibold">
                    {t(`encounter_status__${item.status}`)}
                  </span>{" "}
                  <span className="text-gray-600">
                    {indiaDateTime(item.moved_at)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {blocks.bed && (
          <div
            className="rounded-md bg-white p-5 shadow-sm"
            data-ihr="visit-bed"
          >
            <h2 className="mb-3 text-lg font-semibold">{t("ihr_visit_bed")}</h2>
            <p className="text-sm font-semibold" data-ihr="visit-current-bed">
              {encounter.current_location?.name ?? t("ihr_visit_no_bed")}
            </p>
            {blocks.bedStays && stays.length > 0 && (
              <table className="mt-4 w-full text-left text-sm">
                <caption className="sr-only">
                  {t("ihr_visit_bed_stays")}
                </caption>
                <thead className="text-gray-600">
                  <tr>
                    <th className="py-1 pr-4 font-normal">
                      {t("ihr_visit_stay_bed")}
                    </th>
                    <th className="py-1 pr-4 font-normal">
                      {t("ihr_visit_stay_from")}
                    </th>
                    <th className="py-1 pr-4 font-normal">
                      {t("ihr_visit_stay_to")}
                    </th>
                    <th className="py-1 font-normal">
                      {t("ihr_visit_stay_status")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {stays.map((stay) => (
                    <tr key={stay.id} data-ihr="visit-stay">
                      <td className="py-1 pr-4 font-semibold">
                        {stay.location?.name}
                      </td>
                      <td className="py-1 pr-4">
                        {indiaDateTime(stay.start_datetime)}
                      </td>
                      <td className="py-1 pr-4">
                        {stay.end_datetime
                          ? indiaDateTime(stay.end_datetime)
                          : t("ihr_visit_ongoing")}
                      </td>
                      <td className="py-1">{t(stay.status)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {blocks.accountLink && (
          <div className="rounded-md bg-white p-5 shadow-sm">
            <h2 className="mb-3 text-lg font-semibold">
              {t("ihr_visit_account")}
            </h2>
            {account ? (
              <Button asChild variant="outline">
                <Link
                  href={`/facility/${facilityId}/billing/account/${account.id}`}
                  data-ihr="visit-account-link"
                >
                  {t("ihr_visit_open_account")}
                </Link>
              </Button>
            ) : (
              <p className="text-sm text-gray-600">
                {t("ihr_visit_no_account")}
              </p>
            )}
          </div>
        )}
      </div>
    </Page>
  );
}
