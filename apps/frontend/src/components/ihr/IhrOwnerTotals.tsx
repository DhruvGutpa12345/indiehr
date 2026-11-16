// IndieHR P1-T11c3 (design-c.md §7, §13.5): the Owner's hospital totals. From and To in
// DD-MM-YYYY, By day or By month, the state in the address (?from=&to=&by=). Client checks
// (totals.ts, the mirror of the backend's parse_range) name a mistake without a request; the
// server stays the authority and its 400 messages are shown beside the box. Tiles show the
// compact amount (lakh, crore) with the exact figure as the tile's title and in the "Exact
// figures" table; a figure the answer lacks is "Not available", never ₹0.00. No patient, bill
// or id is ever shown: the API returns none.
import { useQuery } from "@tanstack/react-query";
import { useQueryParams } from "raviger";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

import Loading from "@/components/Common/Loading";
import Page from "@/components/Common/Page";

import { ihrTotalsApi } from "@/Utils/ihr/ihrApi";
import { autoDashDate } from "@/Utils/ihr/indiaDateTimeInput";
import {
  formatInr,
  formatInrCompact,
  groupIndianDigits,
} from "@/Utils/ihr/money";
import {
  type By,
  type Tile,
  type Totals,
  buildQuery,
  checkRange,
  fieldErrorsOf,
  humanize,
  parseBy,
  readUrlState,
  tileLabelKey,
  tiles,
} from "@/Utils/ihr/totals";
import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";
import query from "@/Utils/request/query";
import { HTTPError } from "@/Utils/request/types";

import { IhrNotForRole } from "./IhrNotForRole";

interface IhrOwnerTotalsProps {
  facilityId: string;
}

export function IhrOwnerTotals({ facilityId }: IhrOwnerTotalsProps) {
  const { t } = useTranslation();
  const access = useIhrAccess();
  if (access.mode === "pending") return <Loading />;
  if (!access.totals) return <IhrNotForRole facilityId={facilityId} />;
  return (
    <Page title={t("ihr_menu_hospital_totals")}>
      <TotalsBody facilityId={facilityId} />
    </Page>
  );
}

function TotalsBody({ facilityId }: { facilityId: string }) {
  const { t } = useTranslation();
  const [params, setParams] = useQueryParams();
  // the clock is read once per mount: "this month" does not move under an open page
  const [nowMs] = useState(() => Date.now());
  const url = useMemo(() => readUrlState(params, nowMs), [params, nowMs]);
  const urlCheck = useMemo(
    () => checkRange(url.from, url.to, nowMs),
    [url.from, url.to, nowMs],
  );

  const [from, setFrom] = useState(url.from);
  const [to, setTo] = useState(url.to);
  const [by, setBy] = useState<By>(url.by);
  const [draftErrors, setDraftErrors] = useState<{
    from?: string;
    to?: string;
  }>({});
  // Back and Forward change the address, not this component: the boxes follow the address the
  // figures follow, so a range's totals never show under another range's dates (review F5)
  const urlKey = `${url.from}|${url.to}|${url.by}`;
  const [shownKey, setShownKey] = useState(urlKey);
  if (shownKey !== urlKey) {
    setShownKey(urlKey);
    setFrom(url.from);
    setTo(url.to);
    setBy(url.by);
    setDraftErrors({});
  }

  const range = urlCheck.range;
  const totals = useQuery({
    queryKey: ["ihr", "totals", facilityId, range?.from, range?.to, url.by],
    queryFn: query(ihrTotalsApi.get, {
      queryParams: buildQuery({
        facility: facilityId,
        from: range?.from ?? "",
        to: range?.to ?? "",
        by: url.by,
      }),
      silent: true,
    }),
    enabled: range !== null,
    retry: false,
  });

  function show(event: React.SyntheticEvent) {
    event.preventDefault();
    const check = checkRange(from, to, nowMs);
    setDraftErrors(check.errors);
    // a mistake: NO request (the address is not changed, so the query does not run)
    if (!check.range) return;
    setParams({ from: check.range.from, to: check.range.to, by });
  }

  const error = totals.error;
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

  // 400 on a date: shown beside its box; 403 and anything else: one line
  let errorLine: string | null = null;
  if (totals.isError) {
    if (status === 403) errorLine = t("ihr_totals_error_forbidden");
    else if (status === 400) {
      errorLine =
        serverFields.from || serverFields.to
          ? null
          : (serverFields.by ??
            serverFields.facility ??
            serverFields.other ??
            t("ihr_totals_error_load"));
    } else errorLine = t("ihr_totals_error_load");
  }

  return (
    <div className="mt-4 space-y-6" data-ihr="totals-page">
      <form
        className="flex flex-wrap items-start gap-4"
        onSubmit={show}
        noValidate
      >
        <DateBox
          id="ihr-totals-from"
          label={t("ihr_totals_from")}
          value={from}
          error={fromError}
          onChange={(value) => {
            setFrom(value);
            setDraftErrors({});
          }}
          dataIhr="totals-from"
        />
        <DateBox
          id="ihr-totals-to"
          label={t("ihr_totals_to")}
          value={to}
          error={toError}
          onChange={(value) => {
            setTo(value);
            setDraftErrors({});
          }}
          dataIhr="totals-to"
        />
        <div>
          <label
            htmlFor="ihr-totals-by"
            className="block text-sm font-medium text-gray-700"
          >
            {t("ihr_totals_by")}
          </label>
          <select
            id="ihr-totals-by"
            className="h-9 rounded-md border border-gray-300 bg-white px-2 text-base shadow-xs md:text-sm"
            value={by}
            onChange={(e) => setBy(parseBy(e.target.value))}
            data-ihr="totals-by"
          >
            <option value="day">{t("ihr_totals_by_day")}</option>
            <option value="month">{t("ihr_totals_by_month")}</option>
          </select>
        </div>
        <div className="pt-6">
          <Button type="submit" data-ihr="totals-show">
            {t("ihr_totals_show")}
          </Button>
        </div>
      </form>

      <p className="max-w-3xl text-sm text-gray-600" data-ihr="totals-help">
        {t("ihr_totals_help")}
      </p>

      {totals.isLoading && range && <TotalsSkeleton />}
      {errorLine && (
        <p className="text-red-700" role="alert" data-ihr="totals-error">
          {errorLine}
        </p>
      )}
      {totals.data && <TotalsView totals={totals.data} />}
    </div>
  );
}

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

function TotalsSkeleton() {
  return (
    <div
      className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5"
      data-ihr="totals-loading"
    >
      {Array.from({ length: 9 }).map((_, index) => (
        <Skeleton key={index} className="h-20 w-full" />
      ))}
    </div>
  );
}

function useFigures() {
  const { t } = useTranslation();
  const labels = { lakh: t("ihr_amount_lakh"), crore: t("ihr_amount_crore") };
  const missing = t("ihr_totals_not_available");
  /** [compact text, exact text] of a tile */
  function show(tile: Tile): { compact: string; exact: string } {
    if (tile.value === null) return { compact: missing, exact: missing };
    if (tile.kind === "beds") {
      const text = t("ihr_totals_beds_of", {
        occupied: groupIndianDigits(String(tile.value)),
        total:
          tile.of === null || tile.of === undefined
            ? "-"
            : groupIndianDigits(String(tile.of)),
      });
      return { compact: text, exact: text };
    }
    if (tile.kind === "count") {
      const text = groupIndianDigits(String(tile.value));
      return { compact: text, exact: text };
    }
    return {
      compact: formatInrCompact(tile.value, labels),
      exact: formatInr(tile.value),
    };
  }
  return { show, money: (value: string) => formatInr(value) };
}

function TotalsView({ totals }: { totals: Totals }) {
  const { t } = useTranslation();
  const { show, money } = useFigures();
  const list = tiles(totals);
  const word = (prefix: string, code: string) =>
    t(`${prefix}${code}`, { defaultValue: humanize(code) });

  return (
    <div className="space-y-8" data-ihr="totals-result">
      <h2
        className="text-lg font-semibold text-gray-900"
        data-ihr="totals-range"
      >
        {t("ihr_totals_range", { from: totals.from, to: totals.to })}
      </h2>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {list.map((tile) => {
          const figure = show(tile);
          return (
            <div
              key={tile.key}
              className="rounded-md border border-gray-200 bg-white p-3"
              title={figure.exact}
              data-ihr={`tile-${tile.key}`}
            >
              <div className="text-sm text-gray-600">
                {t(tileLabelKey(tile.key))}
              </div>
              <div className="mt-1 text-xl font-semibold text-gray-900">
                {figure.compact}
              </div>
            </div>
          );
        })}
      </div>

      <section>
        <h2 className="text-lg font-semibold text-gray-900">
          {t("ihr_totals_exact_title")}
        </h2>
        <Table data-ihr="totals-exact">
          <TableHeader>
            <TableRow>
              <TableHead>{t("ihr_totals_figure")}</TableHead>
              <TableHead className="text-right">
                {t("ihr_totals_col_amount")}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {list.map((tile) => (
              <TableRow key={tile.key}>
                <TableCell>{t(tileLabelKey(tile.key))}</TableCell>
                <TableCell className="text-right">{show(tile).exact}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-gray-900">
          {t("ihr_totals_visits_by_class")}
        </h2>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("ihr_totals_col_class")}</TableHead>
              <TableHead>{t("ihr_totals_col_status")}</TableHead>
              <TableHead className="text-right">
                {t("ihr_totals_col_count")}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {totals.encounters.length === 0 && <EmptyRow cols={3} />}
            {totals.encounters.map((row) => (
              <TableRow key={`${row.encounter_class}-${row.status}`}>
                <TableCell>
                  {word("encounter_class__", row.encounter_class)}
                </TableCell>
                <TableCell>{word("encounter_status__", row.status)}</TableCell>
                <TableCell className="text-right">
                  {groupIndianDigits(String(row.count))}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-gray-900">
          {t("ihr_totals_bills_by_status")}
        </h2>
        <Table>
          <TableCaption>{t("ihr_totals_bills_caption")}</TableCaption>
          <TableHeader>
            <TableRow>
              <TableHead>{t("ihr_totals_col_status")}</TableHead>
              <TableHead className="text-right">
                {t("ihr_totals_col_count")}
              </TableHead>
              <TableHead className="text-right">
                {t("ihr_totals_col_amount")}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {totals.invoices_by_status.length === 0 && <EmptyRow cols={3} />}
            {totals.invoices_by_status.map((row) => (
              <TableRow key={row.status}>
                <TableCell>{word("invoice_status__", row.status)}</TableCell>
                <TableCell className="text-right">
                  {groupIndianDigits(String(row.count))}
                </TableCell>
                <TableCell className="text-right">
                  {money(row.amount)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-gray-900">
          {t("ihr_totals_collected_by_method")}
        </h2>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("method")}</TableHead>
              <TableHead className="text-right">
                {t("ihr_totals_col_count")}
              </TableHead>
              <TableHead className="text-right">
                {t("ihr_totals_col_amount")}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {totals.collected_by_method.length === 0 && <EmptyRow cols={3} />}
            {totals.collected_by_method.map((row) => (
              <TableRow key={row.method}>
                <TableCell>{word("", row.method)}</TableCell>
                <TableCell className="text-right">
                  {groupIndianDigits(String(row.count))}
                </TableCell>
                <TableCell className="text-right">
                  {money(row.amount)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-gray-900">
          {totals.by === "month"
            ? t("ihr_totals_series_month")
            : t("ihr_totals_series_day")}
        </h2>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("ihr_totals_col_period")}</TableHead>
              <TableHead className="text-right">
                {t("ihr_totals_col_admissions")}
              </TableHead>
              <TableHead className="text-right">
                {t("ihr_totals_col_discharges")}
              </TableHead>
              <TableHead className="text-right">
                {t("ihr_totals_col_invoiced")}
              </TableHead>
              <TableHead className="text-right">
                {t("ihr_totals_col_collected")}
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {totals.series.length === 0 && <EmptyRow cols={5} />}
            {totals.series.map((row) => (
              <TableRow key={row.period}>
                <TableCell>{row.period}</TableCell>
                <TableCell className="text-right">
                  {groupIndianDigits(String(row.admissions))}
                </TableCell>
                <TableCell className="text-right">
                  {groupIndianDigits(String(row.discharges))}
                </TableCell>
                <TableCell className="text-right">
                  {money(row.invoiced)}
                </TableCell>
                <TableCell className="text-right">
                  {money(row.collected)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </section>
    </div>
  );
}

function EmptyRow({ cols }: { cols: number }) {
  const { t } = useTranslation();
  return (
    <TableRow>
      <TableCell colSpan={cols} className="text-gray-600">
        {t("ihr_totals_empty")}
      </TableCell>
    </TableRow>
  );
}
