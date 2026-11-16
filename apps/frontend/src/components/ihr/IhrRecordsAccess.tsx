// IndieHR P1-T11c3 (design-c.md §6, §13.3, §13.4): MRD's records-access page. The patient comes
// only from ?patient=<id>, set by "Ask for records access" on the patient's own page (Q-K51 A):
// MRD reaches that page through CARE's own search and year-of-birth check, so there is no
// second copy of that knowledge check here. The page shows the patient's name and year of birth
// from the patient body CARE already loaded for the page (the same query the patient page uses).
// The live list comes from the grant watcher (ids and times only); the names and reasons come
// from this page's own GET ?names=1, asked on opening it and when the set of live grants changes
// (review BA1: the watcher's poll shows no patient).
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link, navigate, useQueryParams } from "raviger";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import Loading from "@/components/Common/Loading";
import Page from "@/components/Common/Page";

import { ihrRecordsApi } from "@/Utils/ihr/ihrApi";
import {
  type FormErrors,
  type Grant,
  NAMES_QUERY,
  REASON_CODES,
  type ReasonCode,
  countdownText,
  detailOf,
  detailsKey,
  formatStarted,
  noticeMinutes,
  patientFromQuery,
  patientIdForGrant,
  reasonLabelKey,
  referenceRequired,
  serverErrorField,
  validateRequest,
  withDetails,
} from "@/Utils/ihr/records";
import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";
import mutate from "@/Utils/request/mutate";
import query from "@/Utils/request/query";
import { HTTPError } from "@/Utils/request/types";
import patientApi from "@/types/emr/patient/patientApi";

import { IhrEndGrantButton } from "./IhrEndGrantButton";
import {
  announceGrantsChanged,
  noteGrant,
  refreshGrants,
  useIhrGrants,
} from "./IhrGrantWatcher";
import { IhrNotForRole } from "./IhrNotForRole";

interface IhrRecordsAccessProps {
  facilityId: string;
}

const SELECT_CLASS =
  "h-9 w-full rounded-md border border-gray-300 bg-white px-2 text-base shadow-xs focus:border-primary-500 focus:ring-primary-500 disabled:cursor-not-allowed disabled:opacity-50 md:text-sm";

export function IhrRecordsAccess({ facilityId }: IhrRecordsAccessProps) {
  const { t } = useTranslation();
  const access = useIhrAccess();
  if (access.mode === "pending") return <Loading />;
  if (!access.records) return <IhrNotForRole facilityId={facilityId} />;
  return (
    <Page title={t("ihr_menu_records_access")}>
      <div className="mt-4 space-y-8" data-ihr="records-page">
        <AskForAccess facilityId={facilityId} />
        <LiveGrants facilityId={facilityId} />
      </div>
    </Page>
  );
}

function AskForAccess({ facilityId }: { facilityId: string }) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [params] = useQueryParams();
  const patientParam = patientFromQuery(params.patient);
  const grants = useIhrGrants();

  const [reason, setReason] = useState<string>("");
  const [reference, setReference] = useState("");
  const [errors, setErrors] = useState<FormErrors>({});
  const [serverError, setServerError] = useState<{
    field: "reference" | "reason" | "form";
    message: string;
  } | null>(null);
  const [forbidden, setForbidden] = useState(false);

  // The patient body CARE loads for the patient page (same key as the page's own query)
  const patientQuery = useQuery({
    queryKey: ["patient", patientParam, facilityId],
    queryFn: query(patientApi.get, {
      pathParams: { id: patientParam ?? "" },
      queryParams: { facility: facilityId },
    }),
    enabled: !!patientParam,
  });
  const patient = patientQuery.data;
  // never an id out of a search row (partial: true): only the full body loaded for the page
  const patientId = patientIdForGrant(patient);

  const create = useMutation({
    mutationFn: mutate(ihrRecordsApi.create, { silent: true }),
    onSuccess: async (grant: Grant) => {
      // CARE's cached patient bodies hold the old (reduced) permissions
      queryClient.removeQueries({ queryKey: ["patient"] });
      // held at once from this answer (review F2); the look after it confirms it
      noteGrant(grant);
      announceGrantsChanged();
      void refreshGrants();
      navigate(`/facility/${facilityId}/patient/${grant.patient}`);
    },
    onError: (error: Error) => {
      if (error instanceof HTTPError && error.status === 403) {
        setForbidden(true);
        return;
      }
      const detail =
        error instanceof HTTPError ? detailOf(error.cause) : undefined;
      if (detail) {
        setServerError({ field: serverErrorField(detail), message: detail });
      } else {
        setServerError({ field: "form", message: t("ihr_records_failed") });
      }
    },
  });

  function submit(event: React.SyntheticEvent) {
    event.preventDefault();
    setServerError(null);
    setForbidden(false);
    const result = validateRequest({ patient: patientId, reason, reference });
    setErrors(result.errors);
    // a field error: NO request is sent
    if (!result.request) return;
    create.mutate(result.request);
  }

  const minutes = noticeMinutes(grants.grantMinutes);
  const needsReference = referenceRequired(reason);
  const referenceError = errors.reference
    ? t(errors.reference)
    : serverError?.field === "reference"
      ? serverError.message
      : undefined;
  const reasonError = errors.reason
    ? t(errors.reason)
    : serverError?.field === "reason"
      ? serverError.message
      : undefined;

  return (
    <section data-ihr="records-ask" className="max-w-xl">
      <h2 className="text-lg font-semibold text-gray-900">
        {t("ihr_records_ask_heading")}
      </h2>
      {!patientParam ? (
        <div className="mt-2 space-y-3">
          <p className="text-gray-600">{t("ihr_records_ask_hint")}</p>
          <Button asChild variant="outline">
            <Link href={`/facility/${facilityId}/patients`}>
              {t("ihr_records_search_patients")}
            </Link>
          </Button>
        </div>
      ) : (
        <form className="mt-3 space-y-4" onSubmit={submit} noValidate>
          <div data-ihr="records-patient">
            <span className="block text-sm font-medium text-gray-700">
              {t("patient")}
            </span>
            {patientQuery.isLoading ? (
              <span className="text-gray-500">...</span>
            ) : patient ? (
              <span className="text-gray-900">
                {patient.name}
                {patient.year_of_birth
                  ? ` · ${t("ihr_records_patient_born", { year: patient.year_of_birth })}`
                  : ""}
              </span>
            ) : (
              <span className="text-red-700">
                {t("ihr_records_error_patient")}
              </span>
            )}
            {errors.patient && patient && (
              <p className="text-sm text-red-700">{t(errors.patient)}</p>
            )}
          </div>

          <div>
            <label
              htmlFor="ihr-records-reason"
              className="block text-sm font-medium text-gray-700"
            >
              {t("reason")}
            </label>
            <select
              id="ihr-records-reason"
              className={SELECT_CLASS}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              aria-invalid={!!reasonError}
              data-ihr="records-reason"
            >
              <option value="">{t("ihr_records_reason_placeholder")}</option>
              {REASON_CODES.map((code: ReasonCode) => (
                <option key={code} value={code}>
                  {t(reasonLabelKey(code))}
                </option>
              ))}
            </select>
            {reasonError && (
              <p className="text-sm text-red-700" data-ihr="error-reason">
                {reasonError}
              </p>
            )}
          </div>

          <div>
            <label
              htmlFor="ihr-records-reference"
              className="block text-sm font-medium text-gray-700"
            >
              {needsReference
                ? t("ihr_records_reference_label")
                : t("ihr_records_reference_optional")}
            </label>
            <Input
              id="ihr-records-reference"
              value={reference}
              maxLength={100}
              required={needsReference}
              aria-required={needsReference}
              aria-invalid={!!referenceError}
              onChange={(e) => setReference(e.target.value)}
              data-ihr="records-reference"
            />
            {referenceError && (
              <p className="text-sm text-red-700" data-ihr="error-reference">
                {referenceError}
              </p>
            )}
          </div>

          <p className="text-sm text-gray-600" data-ihr="records-notice">
            {minutes !== null
              ? t("ihr_records_notice", { minutes })
              : t("ihr_records_notice", { minutes: "…" })}
          </p>

          {serverError?.field === "form" && (
            <p className="text-sm text-red-700" role="alert">
              {serverError.message}
            </p>
          )}
          {forbidden && (
            <p
              className="text-sm text-red-700"
              role="alert"
              data-ihr="forbidden"
            >
              {t("ihr_records_forbidden")}
            </p>
          )}

          <Button
            type="submit"
            disabled={create.isPending || !patient}
            data-ihr="records-submit"
          >
            {t("ihr_records_submit")}
          </Button>
        </form>
      )}
    </section>
  );
}

function LiveGrants({ facilityId }: { facilityId: string }) {
  const { t } = useTranslation();
  const grants = useIhrGrants();
  // one list with names per set of live grants (on opening the page, and when one is made or
  // ended); the rows follow the watcher at once, and keep the last details meanwhile
  const key = detailsKey(grants.grants);
  const details = useQuery({
    queryKey: ["ihr", "records-access", "names", key],
    queryFn: query(ihrRecordsApi.listWithNames, {
      queryParams: NAMES_QUERY,
      silent: true,
    }),
    enabled: grants.status === "ready" && key !== "",
    staleTime: Infinity,
    placeholderData: keepPreviousData,
  });
  const rows = withDetails(grants.grants, details.data?.results);
  return (
    <section data-ihr="records-live">
      <h2 className="text-lg font-semibold text-gray-900">
        {t("ihr_records_live_title")}
      </h2>
      {grants.status === "loading" && <Loading />}
      {grants.status === "ready" && grants.grants.length === 0 && (
        <p className="mt-2 text-gray-600" data-ihr="records-live-empty">
          {t("ihr_records_live_empty")}
        </p>
      )}
      {rows.length > 0 && (
        <ul className="mt-3 space-y-3">
          {rows.map(({ grant, details: about }) => (
            <li
              key={grant.id}
              className="flex flex-col gap-2 rounded-md border border-gray-200 bg-white p-3 md:flex-row md:items-center md:justify-between"
              data-ihr="grant-row"
            >
              <div className="text-sm">
                <div className="font-medium text-gray-900">
                  {about ? about.patient_name : "…"}
                  {about?.patient_year_of_birth
                    ? ` · ${t("ihr_records_patient_born", { year: about.patient_year_of_birth })}`
                    : ""}
                </div>
                {about && (
                  <div className="text-gray-600">
                    {t(reasonLabelKey(about.reason as ReasonCode), {
                      defaultValue: about.reason,
                    })}
                    {about.reference ? ` · ${about.reference}` : ""}
                  </div>
                )}
                <div className="text-gray-600">
                  {about && (
                    <>
                      {t("ihr_records_started")}:{" "}
                      {formatStarted(about.created_at)} IST ·{" "}
                    </>
                  )}
                  <span data-ihr="grant-time-left">
                    {t("ihr_records_time_left", {
                      time: countdownText(grants.secondsLeft(grant)),
                    })}
                  </span>
                </div>
              </div>
              <div className="flex gap-2">
                <Button asChild variant="outline" size="sm">
                  <Link
                    href={`/facility/${facilityId}/patient/${grant.patient}`}
                    data-ihr="grant-open"
                  >
                    {t("ihr_records_open")}
                  </Link>
                </Button>
                <IhrEndGrantButton
                  grantId={grant.id}
                  patientName={about?.patient_name ?? ""}
                />
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
