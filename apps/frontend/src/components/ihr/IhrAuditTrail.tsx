// IndieHR P1-T24a (docs/reviews/p1-t24/design.md §2.1, attack.md "Decision"): the audit trail for
// hospital-wide admins. A status strip (seals, triggers, outside fingerprints), filters kept in
// the address (?from=&to=&kind=&op=&patient=&actor=, days in DD-MM-YYYY, India time), the list
// newest first with "Load older", and a side sheet for ONE entry with its old and new values.
//
// - The gate is the inline pattern of IhrOwnerTotals.tsx: the backend's `hospital_admin`
//   (useIhrAccess). A typed address by anyone else shows "not part of your role" and asks for
//   nothing; the backend refuses them anyway.
// - Every call here is itself recorded, so nothing polls or refetches on focus, and values are
//   asked for only when an entry is opened.
// - No typed "Who" filter: an entry's actor (and a patient's name) is a link that filters on it.
// - Downloads (P1-T24c): CSV, JSON and the verifier script are plain same-origin links (the
//   sign-in cookie goes with the click; the audit routes refuse a cross-site one). They carry the
//   days and the patient filter held in the address, at most 31 days.
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import Loading from "@/components/Common/Loading";
import Page from "@/components/Common/Page";

import {
  AUDIT_KINDS,
  AUDIT_OPS,
  type AuditEntry,
  type AuditFilters,
  type AuditStatus,
  VERIFIER_URL,
  actorParts,
  auditFilterKey,
  auditUrlParams,
  buildAuditQuery,
  checkAuditRange,
  checkExportRange,
  exportUrl,
  patientsShown,
  readAuditUrl,
  resultOf,
  statusStrip,
  valueRows,
  whatOf,
  withDraftDates,
} from "@/Utils/ihr/audit";
import { ihrAuditApi } from "@/Utils/ihr/ihrApi";
import { autoDashDate } from "@/Utils/ihr/indiaDateTimeInput";
import { fieldErrorsOf, humanize } from "@/Utils/ihr/totals";
import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";
import query from "@/Utils/request/query";
import { HTTPError } from "@/Utils/request/types";
import { useQueryParams } from "@/lib/router/queryParams";

import { IhrNotForRole } from "./IhrNotForRole";

interface IhrAuditTrailProps {
  facilityId: string;
}

export function IhrAuditTrail({ facilityId }: IhrAuditTrailProps) {
  const { t } = useTranslation();
  const access = useIhrAccess();
  if (access.mode === "pending") return <Loading />;
  if (!access.hospitalAdmin) return <IhrNotForRole facilityId={facilityId} />;
  return (
    <Page title={t("ihr_audit_title")}>
      <AuditBody />
    </Page>
  );
}

// The audit calls are recorded: no refetch because the window got focus or the network came back
const QUIET = {
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
  retry: false,
} as const;

function AuditBody() {
  const { t } = useTranslation();
  const [params, setParams] = useQueryParams();
  // the clock is read once per mount: "today" does not move under an open page
  const [nowMs] = useState(() => Date.now());
  const url = useMemo(() => readAuditUrl(params, nowMs), [params, nowMs]);
  const urlCheck = useMemo(
    () => checkAuditRange(url.from, url.to),
    [url.from, url.to],
  );

  const [from, setFrom] = useState(url.from);
  const [to, setTo] = useState(url.to);
  const [draftErrors, setDraftErrors] = useState<{
    from?: string;
    to?: string;
  }>({});
  const [openId, setOpenId] = useState<number | null>(null);
  // Back and Forward change the address, not this component: the boxes follow the address
  const urlKey = auditFilterKey(url);
  const [shownKey, setShownKey] = useState(urlKey);
  if (shownKey !== urlKey) {
    setShownKey(urlKey);
    setFrom(url.from);
    setTo(url.to);
    setDraftErrors({});
  }

  const range = urlCheck.range;
  const entries = useInfiniteQuery({
    queryKey: ["ihr", "audit", "entries", urlKey],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam, signal }) =>
      query(ihrAuditApi.entries, {
        queryParams: buildAuditQuery(
          url,
          range ?? { from: "", to: "" },
          pageParam,
        ),
        silent: true,
      })({ signal }),
    getNextPageParam: (last) => last.next_before ?? undefined,
    enabled: range !== null,
    staleTime: 60_000,
    ...QUIET,
  });

  function apply(next: AuditFilters) {
    setParams(auditUrlParams(next));
  }

  // The draft dates go with every change of a filter: the boxes are reset to the address when
  // it changes, so typed dates must be checked and sent now or they are lost (review W2).
  function applyDraft(change: Partial<AuditFilters>) {
    const { errors, next } = withDraftDates(url, from, to, change);
    setDraftErrors(errors);
    // a mistake: NO request (the address is not changed, so the query does not run)
    if (next) apply(next);
  }

  function show(event: React.SyntheticEvent) {
    event.preventDefault();
    applyDraft({});
  }

  const error = entries.error;
  const status = error instanceof HTTPError ? error.status : undefined;
  const serverFields =
    status === 400 && error instanceof HTTPError
      ? fieldErrorsOf(error.cause)
      : {};
  const fromError = draftErrors.from
    ? t(draftErrors.from)
    : urlCheck.errors.from
      ? t(urlCheck.errors.from)
      : serverFields.from;
  const toError = draftErrors.to
    ? t(draftErrors.to)
    : urlCheck.errors.to
      ? t(urlCheck.errors.to)
      : serverFields.to;
  let errorLine: string | null = null;
  if (entries.isError) {
    if (status === 403) errorLine = t("ihr_audit_error_forbidden");
    else if (status === 400) {
      errorLine =
        serverFields.from || serverFields.to
          ? null
          : (serverFields.other ?? t("ihr_audit_error_load"));
    } else errorLine = t("ihr_audit_error_load");
  }

  const rows: AuditEntry[] = (entries.data?.pages ?? []).flatMap(
    (page) => page.results,
  );

  return (
    <div className="mt-4 space-y-6" data-ihr="audit-page">
      <p className="max-w-3xl text-sm text-gray-600" data-ihr="audit-intro">
        {t("ihr_audit_intro")}
      </p>

      <StatusStrip />

      <form
        className="flex flex-wrap items-start gap-4"
        onSubmit={show}
        noValidate
      >
        <DateBox
          id="ihr-audit-from"
          label={t("ihr_totals_from")}
          value={from}
          error={fromError}
          onChange={(value) => {
            setFrom(value);
            setDraftErrors({});
          }}
          dataIhr="audit-from"
        />
        <DateBox
          id="ihr-audit-to"
          label={t("ihr_totals_to")}
          value={to}
          error={toError}
          onChange={(value) => {
            setTo(value);
            setDraftErrors({});
          }}
          dataIhr="audit-to"
        />
        <SelectBox
          id="ihr-audit-kind"
          label={t("ihr_audit_kind")}
          value={url.kind}
          anyLabel={t("ihr_audit_kind_any")}
          options={AUDIT_KINDS.map((kind) => ({
            value: kind,
            label: t(`ihr_audit_kind__${kind}`),
          }))}
          onChange={(kind) => applyDraft({ kind })}
          dataIhr="audit-kind"
        />
        <SelectBox
          id="ihr-audit-op"
          label={t("ihr_audit_op")}
          value={url.op}
          anyLabel={t("ihr_audit_op_any")}
          options={AUDIT_OPS.map((op) => ({
            value: op,
            label: t(`ihr_audit_op__${op}`),
          }))}
          onChange={(op) => applyDraft({ op })}
          dataIhr="audit-op"
        />
        <div className="pt-6">
          <Button type="submit" data-ihr="audit-show">
            {t("ihr_audit_show")}
          </Button>
        </div>
      </form>

      {(url.patient || url.actor) && (
        <div className="flex flex-wrap gap-2" data-ihr="audit-chips">
          {url.patient && (
            <Chip
              text={t("ihr_audit_filter_patient")}
              clear={t("ihr_audit_filter_clear")}
              onClear={() => apply({ ...url, patient: "" })}
              dataIhr="audit-chip-patient"
            />
          )}
          {url.actor && (
            <Chip
              text={t("ihr_audit_filter_actor", {
                actor: actorParts(url.actor).label,
              })}
              clear={t("ihr_audit_filter_clear")}
              onClear={() => apply({ ...url, actor: "" })}
              dataIhr="audit-chip-actor"
            />
          )}
        </div>
      )}

      <Downloads filters={url} range={range !== null} />

      {entries.isLoading && range && <ListSkeleton />}
      {errorLine && (
        <div className="space-y-2" role="alert" data-ihr="audit-error">
          <p className="text-red-700">{errorLine}</p>
          {status !== 403 && (
            <Button
              variant="outline"
              onClick={() => entries.refetch()}
              data-ihr="audit-retry"
            >
              {t("ihr_audit_retry")}
            </Button>
          )}
        </div>
      )}
      {entries.isSuccess && rows.length === 0 && (
        <p className="text-gray-700" data-ihr="audit-empty">
          {t("ihr_audit_empty")}
        </p>
      )}
      {rows.length > 0 && (
        <EntryList
          rows={rows}
          onOpen={setOpenId}
          onActor={(actor) => apply({ ...url, actor })}
          onPatient={(patient) => apply({ ...url, patient })}
        />
      )}
      {entries.hasNextPage && (
        <Button
          variant="outline"
          disabled={entries.isFetchingNextPage}
          onClick={() => entries.fetchNextPage()}
          data-ihr="audit-older"
        >
          {entries.isFetchingNextPage
            ? t("ihr_audit_loading")
            : t("ihr_audit_load_older")}
        </Button>
      )}
      {entries.isSuccess && rows.length > 0 && !entries.hasNextPage && (
        <p className="text-sm text-gray-600" data-ihr="audit-end">
          {t("ihr_audit_end")}
        </p>
      )}

      <EntrySheet
        id={openId}
        onClose={() => setOpenId(null)}
        onActor={(actor) => {
          setOpenId(null);
          apply({ ...url, actor });
        }}
        onPatient={(patient) => {
          setOpenId(null);
          apply({ ...url, patient });
        }}
      />
    </div>
  );
}

// --- status strip -----------------------------------------------------------------------

function StatusStrip() {
  const { t } = useTranslation();
  const status = useQuery<AuditStatus>({
    queryKey: ["ihr", "audit", "status"],
    queryFn: query(ihrAuditApi.status, { silent: true }),
    staleTime: 60_000,
    ...QUIET,
  });
  if (status.isLoading) {
    return <Skeleton className="h-8 w-full max-w-3xl" />;
  }
  if (status.isError || !status.data) {
    return (
      <p
        className="text-sm text-red-700"
        role="alert"
        data-ihr="audit-status-error"
      >
        {t("ihr_audit_status_error")}
      </p>
    );
  }
  const strip = statusStrip(status.data);
  const items: { key: string; text: string; bad: boolean }[] = [
    {
      key: "sealed",
      text: strip.lastSealed
        ? t("ihr_audit_status_last_sealed", { when: strip.lastSealed })
        : t("ihr_audit_status_never_sealed"),
      bad: false,
    },
    {
      key: "unsealed",
      text:
        strip.unsealed === null
          ? t("ihr_audit_status_unsealed_unknown")
          : t("ihr_audit_status_unsealed", { count: strip.unsealed }),
      bad: strip.unsealed === null,
    },
    {
      key: "triggers",
      text: strip.triggersOk
        ? t("ihr_audit_status_triggers_ok")
        : strip.triggerProblems > 0
          ? t("ihr_audit_status_triggers_problems", {
              count: strip.triggerProblems,
            })
          : t("ihr_audit_status_triggers_unknown"),
      bad: !strip.triggersOk,
    },
    {
      key: "fingerprints",
      text: t(`ihr_audit_status_fp_${strip.fingerprints}`),
      bad: strip.fingerprints !== "locked",
    },
  ];
  return (
    <ul className="flex flex-wrap gap-2" data-ihr="audit-status">
      {items.map((item) => (
        <li key={item.key} data-ihr={`audit-status-${item.key}`}>
          <Badge variant={item.bad ? "destructive" : "secondary"}>
            {item.text}
          </Badge>
        </li>
      ))}
    </ul>
  );
}

// --- downloads --------------------------------------------------------------------------

function Downloads({
  filters,
  range,
}: {
  filters: AuditFilters;
  range: boolean;
}) {
  const { t } = useTranslation();
  // the days of the address, not the boxes: the link is what the list below shows
  const problem = range
    ? checkExportRange(filters.from, filters.to)
    : "ihr_audit_export_need_range";
  const csv = exportUrl(filters, "csv");
  const json = exportUrl(filters, "json");
  return (
    <section className="space-y-2" data-ihr="audit-downloads">
      <h2 className="text-sm font-semibold text-gray-900">
        {t("ihr_audit_export_title")}
      </h2>
      <p className="max-w-3xl text-sm text-gray-600">
        {t("ihr_audit_export_note")}
      </p>
      {problem ? (
        <p className="text-sm text-red-700" data-ihr="audit-export-problem">
          {t(problem)}
        </p>
      ) : (
        <div className="flex flex-wrap gap-2">
          {csv && (
            <Button asChild variant="outline">
              <a href={csv} download data-ihr="audit-export-csv">
                {t("ihr_audit_export_csv")}
              </a>
            </Button>
          )}
          {json && (
            <Button asChild variant="outline">
              <a href={json} download data-ihr="audit-export-json">
                {t("ihr_audit_export_json")}
              </a>
            </Button>
          )}
        </div>
      )}
      <a
        href={VERIFIER_URL}
        download
        className="inline-block text-sm text-primary-700 underline-offset-2 hover:underline"
        data-ihr="audit-verifier"
      >
        {t("ihr_audit_export_verifier")}
      </a>
    </section>
  );
}

// --- list -------------------------------------------------------------------------------

function ListSkeleton() {
  return (
    <div className="space-y-2" data-ihr="audit-loading">
      {Array.from({ length: 6 }).map((_, index) => (
        <Skeleton key={index} className="h-10 w-full" />
      ))}
    </div>
  );
}

interface ListActions {
  onOpen: (id: number) => void;
  onActor: (actor: string) => void;
  onPatient: (patient: string) => void;
}

function EntryList({ rows, ...actions }: { rows: AuditEntry[] } & ListActions) {
  const { t } = useTranslation();
  return (
    <>
      <div className="hidden md:block" data-ihr="audit-table">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("ihr_audit_col_when")}</TableHead>
              <TableHead>{t("ihr_audit_col_who")}</TableHead>
              <TableHead>{t("ihr_audit_col_what")}</TableHead>
              <TableHead>{t("ihr_audit_col_patients")}</TableHead>
              <TableHead>{t("ihr_audit_col_result")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((entry) => (
              <TableRow key={entry.id} data-ihr="audit-row">
                <TableCell className="whitespace-nowrap">
                  <OpenButton entry={entry} onOpen={actions.onOpen} />
                </TableCell>
                <TableCell>
                  <ActorLink entry={entry} onActor={actions.onActor} />
                </TableCell>
                <TableCell className="max-w-xs break-words whitespace-normal">
                  <What entry={entry} />
                </TableCell>
                <TableCell>
                  <Patients entry={entry} onPatient={actions.onPatient} />
                </TableCell>
                <TableCell className="max-w-xs break-words whitespace-normal">
                  <Result entry={entry} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <ul className="space-y-3 md:hidden" data-ihr="audit-cards">
        {rows.map((entry) => (
          <li
            key={entry.id}
            className="space-y-1 rounded-md border border-gray-200 p-3 text-sm"
            data-ihr="audit-card"
          >
            <OpenButton entry={entry} onOpen={actions.onOpen} />
            <div>
              <ActorLink entry={entry} onActor={actions.onActor} />
            </div>
            <div className="break-words">
              <What entry={entry} />
            </div>
            <Patients entry={entry} onPatient={actions.onPatient} />
            <div className="break-words text-gray-700">
              <Result entry={entry} />
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}

function OpenButton({
  entry,
  onOpen,
}: {
  entry: AuditEntry;
  onOpen: (id: number) => void;
}) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      className="font-medium text-primary-700 underline-offset-2 hover:underline"
      aria-label={t("ihr_audit_open_entry", { id: entry.id })}
      onClick={() => onOpen(entry.id)}
      data-ihr="audit-open"
    >
      {entry.at_ist} IST
    </button>
  );
}

function ActorLink({
  entry,
  onActor,
}: {
  entry: Pick<AuditEntry, "actor">;
  onActor: (actor: string) => void;
}) {
  const { t } = useTranslation();
  const parts = actorParts(entry.actor);
  return (
    <button
      type="button"
      className="text-left break-all text-gray-900 underline-offset-2 hover:underline"
      title={t("ihr_audit_filter_by_actor", { actor: parts.label })}
      onClick={() => onActor(parts.actor)}
      data-ihr="audit-actor"
    >
      {parts.label}
    </button>
  );
}

function What({ entry }: { entry: AuditEntry }) {
  const { t } = useTranslation();
  const what = whatOf(entry);
  if (what.type === "request") {
    return (
      <span>
        {what.purpose && (
          <Badge variant="outline" className="mr-1">
            {t(`ihr_audit_purpose__${what.purpose}`, {
              defaultValue: humanize(what.purpose),
            })}
          </Badge>
        )}
        <span className="font-mono text-xs">
          {[what.method, what.path].filter(Boolean).join(" ")}
        </span>
      </span>
    );
  }
  return (
    <span>
      <span className="font-mono text-xs">{what.table ?? "-"}</span>
      {what.op && (
        <Badge variant="outline" className="ml-1">
          {t(`ihr_audit_op__${what.op}`, { defaultValue: humanize(what.op) })}
        </Badge>
      )}
    </span>
  );
}

function Patients({
  entry,
  onPatient,
}: {
  entry: AuditEntry;
  onPatient: (patient: string) => void;
}) {
  const { t } = useTranslation();
  const { shown, more } = patientsShown(entry);
  if (shown.length === 0 && more === 0) return <span>-</span>;
  return (
    <span className="space-x-2">
      {shown.map((patient, index) =>
        patient.id ? (
          <button
            key={patient.id}
            type="button"
            className="text-left underline-offset-2 hover:underline"
            title={t("ihr_audit_filter_by_patient")}
            onClick={() => onPatient(patient.id as string)}
            data-ihr="audit-patient"
          >
            {patient.name ?? "-"}
          </button>
        ) : (
          <span key={index}>{patient.name ?? "-"}</span>
        ),
      )}
      {more > 0 && (
        <span className="text-gray-600">
          {t("ihr_audit_patients_more", { count: more })}
        </span>
      )}
    </span>
  );
}

function Result({ entry }: { entry: AuditEntry }) {
  const { t } = useTranslation();
  const result = resultOf(entry);
  if (result.type === "status") {
    return <>{t("ihr_audit_result_status", { status: result.status })}</>;
  }
  if (result.type === "fields") {
    return (
      <>{t("ihr_audit_result_fields", { fields: result.fields.join(", ") })}</>
    );
  }
  return <>-</>;
}

// --- filters ----------------------------------------------------------------------------

function DateBox({
  id,
  label,
  value,
  error,
  onChange,
  dataIhr,
}: {
  id: string;
  label: string;
  value: string;
  error?: string;
  onChange: (value: string) => void;
  dataIhr: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-gray-700">
        {label}
      </label>
      <Input
        id={id}
        className="w-40"
        inputMode="numeric"
        autoComplete="off"
        placeholder="DD-MM-YYYY"
        value={value}
        aria-invalid={!!error}
        onChange={(e) => onChange(autoDashDate(e.target.value))}
        data-ihr={dataIhr}
      />
      {error && (
        <p
          className="max-w-52 text-sm text-red-700"
          data-ihr={`${dataIhr}-error`}
        >
          {error}
        </p>
      )}
    </div>
  );
}

function SelectBox({
  id,
  label,
  value,
  anyLabel,
  options,
  onChange,
  dataIhr,
}: {
  id: string;
  label: string;
  value: string;
  anyLabel: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  dataIhr: string;
}) {
  return (
    <div>
      <label htmlFor={id} className="block text-sm font-medium text-gray-700">
        {label}
      </label>
      <select
        id={id}
        className="h-9 max-w-full rounded-md border border-gray-300 bg-white pl-2 pr-8 text-base shadow-xs md:text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-ihr={dataIhr}
      >
        <option value="">{anyLabel}</option>
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </div>
  );
}

function Chip({
  text,
  clear,
  onClear,
  dataIhr,
}: {
  text: string;
  clear: string;
  onClear: () => void;
  dataIhr: string;
}) {
  return (
    <span
      className="inline-flex items-center gap-2 rounded-md border border-gray-300 bg-gray-50 px-2 py-1 text-sm"
      data-ihr={dataIhr}
    >
      <span className="break-all">{text}</span>
      <button
        type="button"
        className="font-medium text-primary-700 underline-offset-2 hover:underline"
        onClick={onClear}
      >
        {clear}
      </button>
    </span>
  );
}

// --- one entry --------------------------------------------------------------------------

function EntrySheet({
  id,
  onClose,
  onActor,
  onPatient,
}: {
  id: number | null;
  onClose: () => void;
  onActor: (actor: string) => void;
  onPatient: (patient: string) => void;
}) {
  const { t } = useTranslation();
  // Values are fetched only here, for the one entry opened (and that opening is recorded)
  const detail = useQuery({
    queryKey: ["ihr", "audit", "entry", id],
    queryFn: query(ihrAuditApi.entry, {
      pathParams: { id: String(id) },
      silent: true,
    }),
    enabled: id !== null,
    staleTime: 60_000,
    ...QUIET,
  });
  const error = detail.error;
  const status = error instanceof HTTPError ? error.status : undefined;
  const entry = detail.data;
  const rows = entry ? valueRows(entry.op, entry.values, entry.kind) : [];
  const isUpdate = entry?.op === "UPDATE";

  return (
    <Sheet open={id !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        className="w-full overflow-y-auto sm:max-w-xl"
        data-ihr="audit-sheet"
      >
        <SheetHeader>
          <SheetTitle>
            {t("ihr_audit_entry_title", { id: id ?? "" })}
          </SheetTitle>
          <SheetDescription>{t("ihr_audit_entry_note")}</SheetDescription>
        </SheetHeader>
        <div className="mt-4 space-y-4">
          {detail.isLoading && (
            <div className="space-y-2" data-ihr="audit-entry-loading">
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-6 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          )}
          {detail.isError && (
            <div
              role="alert"
              className="space-y-2"
              data-ihr="audit-entry-error"
            >
              <p className="text-red-700">
                {status === 404
                  ? t("ihr_audit_entry_missing")
                  : status === 403
                    ? t("ihr_audit_error_forbidden")
                    : t("ihr_audit_entry_error")}
              </p>
              {status !== 404 && status !== 403 && (
                <Button variant="outline" onClick={() => detail.refetch()}>
                  {t("ihr_audit_retry")}
                </Button>
              )}
            </div>
          )}
          {entry && (
            <>
              <EntryFacts
                entry={entry}
                onActor={onActor}
                onPatient={onPatient}
              />
              <h3 className="text-sm font-semibold text-gray-900">
                {t("ihr_audit_values")}
              </h3>
              {rows.length === 0 ? (
                <p
                  className="text-sm text-gray-600"
                  data-ihr="audit-values-none"
                >
                  {t("ihr_audit_values_none")}
                </p>
              ) : (
                <Table data-ihr="audit-values">
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("ihr_audit_col_field")}</TableHead>
                      {isUpdate ? (
                        <>
                          <TableHead>{t("ihr_audit_col_old")}</TableHead>
                          <TableHead>{t("ihr_audit_col_new")}</TableHead>
                        </>
                      ) : (
                        <TableHead>{t("ihr_audit_col_value")}</TableHead>
                      )}
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.map((row) => (
                      <TableRow key={row.field}>
                        <TableCell className="font-mono text-xs">
                          {row.field}
                        </TableCell>
                        {isUpdate ? (
                          <>
                            <TableCell className="max-w-40 break-words whitespace-pre-wrap">
                              {row.old}
                            </TableCell>
                            <TableCell className="max-w-40 break-words whitespace-pre-wrap">
                              {row.new}
                            </TableCell>
                          </>
                        ) : (
                          <TableCell className="max-w-64 break-words whitespace-pre-wrap">
                            {row.value}
                          </TableCell>
                        )}
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </>
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function EntryFacts({
  entry,
  onActor,
  onPatient,
}: {
  entry: AuditEntry;
  onActor: (actor: string) => void;
  onPatient: (patient: string) => void;
}) {
  const { t } = useTranslation();
  const facts: { key: string; label: string; value: React.ReactNode }[] = [
    {
      key: "when",
      label: t("ihr_audit_col_when"),
      value: `${entry.at_ist} IST`,
    },
    {
      key: "actor",
      label: t("ihr_audit_col_who"),
      value: <ActorLink entry={entry} onActor={onActor} />,
    },
    {
      key: "kind",
      label: t("ihr_audit_kind"),
      value: t(`ihr_audit_kind__${entry.kind}`, {
        defaultValue: humanize(entry.kind),
      }),
    },
    {
      key: "what",
      label: t("ihr_audit_col_what"),
      value: <What entry={entry} />,
    },
    {
      key: "result",
      label: t("ihr_audit_col_result"),
      value: <Result entry={entry} />,
    },
  ];
  const optional: [string, string, unknown][] = [
    ["table", "ihr_audit_f_table", entry.table],
    ["record", "ihr_audit_f_record", entry.record],
    ["via", "ihr_audit_f_via", entry.via],
    ["ip", "ihr_audit_f_ip", entry.ip],
    ["query", "ihr_audit_f_query", entry.query],
    ["task", "ihr_audit_f_task", entry.task],
    ["request_id", "ihr_audit_f_request_id", entry.request_id],
    ["device_tz", "ihr_audit_f_device_tz", entry.device_tz],
    [
      "grants",
      "ihr_audit_f_grants",
      entry.grants ? JSON.stringify(entry.grants) : null,
    ],
  ];
  for (const [key, label, value] of optional) {
    if (value === null || value === undefined || value === "") continue;
    facts.push({ key, label: t(label), value: String(value) });
  }
  return (
    <dl className="space-y-2 text-sm" data-ihr="audit-facts">
      {facts.map((fact) => (
        <div key={fact.key} className="grid grid-cols-3 gap-2">
          <dt className="text-gray-600">{fact.label}</dt>
          <dd
            className="col-span-2 break-words"
            data-ihr={`audit-fact-${fact.key}`}
          >
            {fact.value}
          </dd>
        </div>
      ))}
      <div className="grid grid-cols-3 gap-2">
        <dt className="text-gray-600">{t("ihr_audit_col_patients")}</dt>
        <dd className="col-span-2" data-ihr="audit-fact-patients">
          <Patients entry={entry} onPatient={onPatient} />
        </dd>
      </div>
    </dl>
  );
}
