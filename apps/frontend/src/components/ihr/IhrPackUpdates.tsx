// IndieHR P1-T24c (docs/reviews/p1-t24/design.md §2.3 and "T24c as decided", attack.md
// "Decision"): the reference-list updates for hospital-wide admins. When a new version of a
// shared list (value sets, forms, ...) differs from the hospital's own, it waits here: the admin
// accepts the pack's version or keeps ours, with a reason. Tabs: Waiting, Kept as ours, History.
//
// - The gate is the inline pattern of IhrAuditTrail.tsx: the backend's `hospital_admin`
//   (useIhrAccess). A typed address by anyone else shows "not part of your role" and asks for
//   nothing; the backend refuses them anyway.
// - Every answer of the decision POST is one fixed phrase chosen by status and code
//   (Utils/ihr/packs.ts `answerOf`); the server's own text is never shown.
// - "Keep ours" is offered on a Waiting item only; Accept is off while a conflict, data check or
//   missing link holds the item (the backend refuses it too).
// - The sheet is read afresh each time it opens (no cache): the POST sends the version and the
//   hashes it was shown, so a stale sheet is refused (409) and reloaded, never applied.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useQueryParams } from "raviger";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";

import Loading from "@/components/Common/Loading";
import Page from "@/components/Common/Page";

import { ihrPacksApi } from "@/Utils/ihr/ihrApi";
import {
  type Answer,
  CHANGE_WORDS,
  MAX_REASON,
  OUTCOME_WORDS,
  PACK_TABS,
  PAGE_SIZE,
  type PackAction,
  type PackDetail,
  type PackHistoryEntry,
  type PackSummary,
  type PackTab,
  answerOf,
  canAccept,
  canKeep,
  canRetryLoad,
  checkLine,
  clampOffset,
  decisionBody,
  effectKey,
  facilityText,
  fieldValue,
  historyBy,
  historySummary,
  loadErrorKey,
  pageRange,
  readTab,
  reasonCheck,
  sinceText,
  whyKey,
} from "@/Utils/ihr/packs";
import { formatIst } from "@/Utils/ihr/staffAccess";
import { humanize } from "@/Utils/ihr/totals";
import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";
import mutate from "@/Utils/request/mutate";
import query from "@/Utils/request/query";
import { HTTPError } from "@/Utils/request/types";

import { IhrNotForRole } from "./IhrNotForRole";

interface IhrPackUpdatesProps {
  facilityId: string;
}

export function IhrPackUpdates({ facilityId }: IhrPackUpdatesProps) {
  const { t } = useTranslation();
  const access = useIhrAccess();
  if (access.mode === "pending") return <Loading />;
  if (!access.hospitalAdmin) return <IhrNotForRole facilityId={facilityId} />;
  return (
    <Page title={t("ihr_packs_title")}>
      <PacksBody />
    </Page>
  );
}

// Lists are read afresh when the page opens and after every decision; nothing refetches because
// the window got focus (a decision sheet must not change under the admin's hands)
const FRESH = {
  staleTime: 0,
  refetchOnWindowFocus: false,
  refetchOnReconnect: false,
  retry: false,
} as const;

const ROOT_KEY = ["ihr", "packs"] as const;

interface Notice {
  key: string;
  ok: boolean;
}

function PacksBody() {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const [params, setParams] = useQueryParams();
  const tab = readTab(params.tab);
  const [offset, setOffset] = useState(0);
  const [openId, setOpenId] = useState<number | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);

  function chooseTab(next: string) {
    setOffset(0);
    setNotice(null);
    setParams(next === "pending" ? {} : { tab: next });
  }

  function decided(answer: Answer) {
    setOpenId(null);
    setNotice({ key: answer.key, ok: answer.ok });
    void queryClient.invalidateQueries({ queryKey: ROOT_KEY });
  }

  return (
    <div className="mt-4 space-y-6" data-ihr="packs-page">
      <p className="max-w-3xl text-sm text-gray-600" data-ihr="packs-intro">
        {t("ihr_packs_intro")}
      </p>

      {notice && (
        <p
          role={notice.ok ? "status" : "alert"}
          className={notice.ok ? "text-green-800" : "text-red-700"}
          data-ihr="packs-notice"
        >
          {t(notice.key)}
        </p>
      )}

      <Tabs value={tab} onValueChange={chooseTab}>
        <TabsList className="h-auto flex-wrap" data-ihr="packs-tabs">
          {PACK_TABS.map((name) => (
            <TabsTrigger key={name} value={name} data-ihr={`packs-tab-${name}`}>
              {t(`ihr_packs_tab_${name}`)}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value={tab} className="mt-4">
          {tab === "history" ? (
            <HistoryPanel offset={offset} setOffset={setOffset} />
          ) : (
            <ListPanel
              state={tab}
              offset={offset}
              setOffset={setOffset}
              onOpen={setOpenId}
            />
          )}
        </TabsContent>
      </Tabs>

      <PackSheet
        id={openId}
        onClose={() => setOpenId(null)}
        onDecided={decided}
      />
    </div>
  );
}

// --- the lists --------------------------------------------------------------------------------

function errorOf(error: unknown): { status?: number; body: unknown } {
  return error instanceof HTTPError
    ? { status: error.status, body: error.cause }
    : { body: undefined };
}

function Pager({
  offset,
  count,
  setOffset,
}: {
  offset: number;
  count: number;
  setOffset: (offset: number) => void;
}) {
  const { t } = useTranslation();
  const range = pageRange(offset, count);
  if (count <= PAGE_SIZE) return null;
  return (
    <div
      className="flex flex-wrap items-center gap-3 text-sm"
      data-ihr="packs-pager"
    >
      <Button
        variant="outline"
        disabled={!range.hasPrev}
        onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
        data-ihr="packs-prev"
      >
        {t("ihr_packs_prev")}
      </Button>
      <span>
        {t("ihr_packs_range", {
          from: range.from,
          to: range.to,
          count,
        })}
      </span>
      <Button
        variant="outline"
        disabled={!range.hasNext}
        onClick={() => setOffset(offset + PAGE_SIZE)}
        data-ihr="packs-next"
      >
        {t("ihr_packs_next")}
      </Button>
    </div>
  );
}

function ListError({
  error,
  retry,
  dataIhr,
}: {
  error: unknown;
  retry: () => void;
  dataIhr: string;
}) {
  const { t } = useTranslation();
  const { status, body } = errorOf(error);
  return (
    <div className="space-y-2" role="alert" data-ihr={dataIhr}>
      <p className="text-red-700">{t(loadErrorKey(status, body))}</p>
      {canRetryLoad(status) && (
        <Button variant="outline" onClick={retry} data-ihr="packs-retry">
          {t("ihr_packs_retry")}
        </Button>
      )}
    </div>
  );
}

function ListSkeleton() {
  return (
    <div className="space-y-2" data-ihr="packs-loading">
      {Array.from({ length: 4 }).map((_, index) => (
        <Skeleton key={index} className="h-10 w-full" />
      ))}
    </div>
  );
}

function ListPanel({
  state,
  offset,
  setOffset,
  onOpen,
}: {
  state: "pending" | "kept";
  offset: number;
  setOffset: (offset: number) => void;
  onOpen: (id: number) => void;
}) {
  const { t } = useTranslation();
  const list = useQuery({
    queryKey: [...ROOT_KEY, "updates", state, offset],
    queryFn: query(ihrPacksApi.list, {
      queryParams: {
        state,
        limit: String(PAGE_SIZE),
        offset: String(offset),
      },
      silent: true,
    }),
    ...FRESH,
  });
  const rows = list.data?.results ?? [];
  const count = list.data?.count ?? 0;
  // a decision can empty the last page: step back to a page that has rows (it has a Previous)
  const back = list.data ? clampOffset(offset, count) : offset;
  useEffect(() => {
    if (back !== offset) setOffset(back);
  }, [back, offset, setOffset]);
  if (list.isLoading) return <ListSkeleton />;
  if (list.isError) {
    return (
      <ListError
        error={list.error}
        retry={() => void list.refetch()}
        dataIhr="packs-error"
      />
    );
  }
  if (back !== offset) return <ListSkeleton />;
  if (rows.length === 0) {
    return (
      <p className="text-gray-700" data-ihr="packs-empty">
        {t(`ihr_packs_empty_${state}`)}
      </p>
    );
  }
  return (
    <div className="space-y-4">
      <UpdateRows rows={rows} onOpen={onOpen} />
      <Pager offset={offset} count={count} setOffset={setOffset} />
    </div>
  );
}

function OpenButton({
  item,
  onOpen,
}: {
  item: PackSummary;
  onOpen: (id: number) => void;
}) {
  const { t } = useTranslation();
  const label = item.label || item.item;
  return (
    <button
      type="button"
      className="text-left font-medium break-words text-primary-700 underline-offset-2 hover:underline"
      aria-label={t("ihr_packs_open", { label })}
      onClick={() => onOpen(item.id)}
      data-ihr="packs-open"
    >
      {label}
    </button>
  );
}

function UpdateRows({
  rows,
  onOpen,
}: {
  rows: PackSummary[];
  onOpen: (id: number) => void;
}) {
  const { t } = useTranslation();
  const whole = t("ihr_packs_whole_hospital");
  return (
    <>
      <div className="hidden md:block" data-ihr="packs-table">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t("ihr_packs_col_label")}</TableHead>
              <TableHead>{t("ihr_packs_col_pack")}</TableHead>
              <TableHead>{t("ihr_packs_col_facility")}</TableHead>
              <TableHead>{t("ihr_packs_col_version")}</TableHead>
              <TableHead>{t("ihr_packs_col_since")}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((item) => (
              <TableRow key={item.id} data-ihr="packs-row">
                <TableCell className="max-w-xs whitespace-normal">
                  <OpenButton item={item} onOpen={onOpen} />
                </TableCell>
                <TableCell className="font-mono text-xs">{item.pack}</TableCell>
                <TableCell>{facilityText(item, whole)}</TableCell>
                <TableCell>{item.pending_version ?? "-"}</TableCell>
                <TableCell className="whitespace-nowrap">
                  {sinceText(item.changed_at)}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      <ul className="space-y-3 md:hidden" data-ihr="packs-cards">
        {rows.map((item) => (
          <li
            key={item.id}
            className="space-y-1 rounded-md border border-gray-200 p-3 text-sm"
            data-ihr="packs-card"
          >
            <OpenButton item={item} onOpen={onOpen} />
            <p className="font-mono text-xs break-all">{item.pack}</p>
            <p>{facilityText(item, whole)}</p>
            <p className="text-gray-600">
              {t("ihr_packs_card_version", {
                version: item.pending_version ?? "-",
                since: sinceText(item.changed_at),
              })}
            </p>
          </li>
        ))}
      </ul>
    </>
  );
}

// --- history ----------------------------------------------------------------------------------

function HistoryPanel({
  offset,
  setOffset,
}: {
  offset: number;
  setOffset: (offset: number) => void;
}) {
  const { t } = useTranslation();
  const history = useQuery({
    queryKey: [...ROOT_KEY, "history", offset],
    queryFn: query(ihrPacksApi.history, {
      queryParams: { limit: String(PAGE_SIZE), offset: String(offset) },
      silent: true,
    }),
    ...FRESH,
  });
  if (history.isLoading) return <ListSkeleton />;
  if (history.isError) {
    return (
      <ListError
        error={history.error}
        retry={() => void history.refetch()}
        dataIhr="packs-history-error"
      />
    );
  }
  const rows = history.data?.results ?? [];
  if (rows.length === 0) {
    return (
      <p className="text-gray-700" data-ihr="packs-history-empty">
        {t("ihr_packs_empty_history")}
      </p>
    );
  }
  return (
    <div className="space-y-4">
      <ul className="space-y-3" data-ihr="packs-history">
        {rows.map((entry) => (
          <HistoryRow key={entry.id} entry={entry} />
        ))}
      </ul>
      <Pager
        offset={offset}
        count={history.data?.count ?? 0}
        setOffset={setOffset}
      />
    </div>
  );
}

const SHOWN_CHANGES = 50;

function HistoryRow({ entry }: { entry: PackHistoryEntry }) {
  const { t } = useTranslation();
  const by = historyBy(entry.by);
  const summary = historySummary(entry);
  const outcome = OUTCOME_WORDS.has(entry.outcome)
    ? t(`ihr_packs_outcome__${entry.outcome}`)
    : humanize(entry.outcome);
  const hidden = Math.max(0, summary.listed.length - SHOWN_CHANGES);
  return (
    <li
      className="space-y-1 rounded-md border border-gray-200 p-3 text-sm"
      data-ihr="packs-history-row"
      data-ihr-outcome={entry.outcome}
    >
      <p className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{formatIst(entry.at)}</span>
        <Badge variant="outline" data-ihr="packs-history-outcome">
          {outcome}
        </Badge>
        <span className="font-mono text-xs break-all">{entry.pack}</span>
        {entry.version !== null && (
          <span>{t("ihr_packs_hist_version", { version: entry.version })}</span>
        )}
      </p>
      <p className="text-gray-700">
        {t("ihr_packs_hist_by", {
          who: "name" in by ? by.name : t(by.key),
        })}
      </p>
      {summary.item !== undefined && (
        <p data-ihr="packs-history-item">
          {t("ihr_packs_hist_item", { item: summary.item })}
        </p>
      )}
      {summary.reason !== undefined && (
        <p className="break-words" data-ihr="packs-history-reason">
          {t("ihr_packs_hist_reason", { reason: summary.reason })}
        </p>
      )}
      {summary.notRecorded && (
        <p className="text-gray-600">{t("ihr_packs_hist_not_recorded")}</p>
      )}
      {summary.total !== null && (
        <p data-ihr="packs-history-total">
          {t("ihr_packs_hist_changes", { count: summary.total })}
        </p>
      )}
      {entry.note && (
        <p className="text-amber-700">{t("ihr_packs_hist_failed_note")}</p>
      )}
      {summary.listed.length > 0 && (
        <details>
          <summary className="cursor-pointer text-primary-700">
            {t("ihr_packs_hist_show_changes")}
          </summary>
          <ul className="mt-1 space-y-0.5">
            {summary.listed.slice(0, SHOWN_CHANGES).map((change, index) => (
              <li key={index} className="break-all">
                <span className="font-mono text-xs">{change.item}</span>
                {": "}
                {CHANGE_WORDS.has(change.change)
                  ? t(`ihr_packs_change__${change.change}`)
                  : humanize(change.change)}
              </li>
            ))}
          </ul>
          {hidden > 0 && (
            <p className="text-gray-600">
              {t("ihr_packs_hist_more", { count: hidden })}
            </p>
          )}
        </details>
      )}
    </li>
  );
}

// --- one item ---------------------------------------------------------------------------------

function PackSheet({
  id,
  onClose,
  onDecided,
}: {
  id: number | null;
  onClose: () => void;
  onDecided: (answer: Answer) => void;
}) {
  const { t } = useTranslation();
  return (
    <Sheet open={id !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-2xl"
        data-ihr="packs-sheet"
      >
        <SheetHeader>
          <SheetTitle>{t("ihr_packs_sheet_title")}</SheetTitle>
          <SheetDescription>{t("ihr_packs_sheet_note")}</SheetDescription>
        </SheetHeader>
        {id !== null && <SheetBody key={id} id={id} onDecided={onDecided} />}
      </SheetContent>
    </Sheet>
  );
}

function SheetBody({
  id,
  onDecided,
}: {
  id: number;
  onDecided: (answer: Answer) => void;
}) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const detailKey = [...ROOT_KEY, "update", id];
  // the hashes the POST sends must be the ones of this very reading: no cache kept
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: query(ihrPacksApi.detail, {
      pathParams: { id: String(id) },
      silent: true,
    }),
    gcTime: 0,
    ...FRESH,
  });
  const [reason, setReason] = useState("");
  const [reasonError, setReasonError] = useState(false);
  const [answer, setAnswer] = useState<Answer | null>(null);

  const decide = useMutation({
    mutationFn: ({ action, item }: { action: PackAction; item: PackDetail }) =>
      mutate(ihrPacksApi.decide, {
        pathParams: { id: String(id) },
        silent: true,
      })(decisionBody(item, action, reason)),
    onSuccess: (result, { action }) => {
      onDecided(answerOf(action, 200, result));
    },
    onError: (error, { action }) => {
      const { status, body } = errorOf(error);
      const next = answerOf(action, status, body);
      if (next.after === "list") {
        onDecided(next);
        return;
      }
      setAnswer(next);
      // what was shown may have changed: read it again (the reason typed stays)
      if (next.after === "detail") {
        void queryClient.invalidateQueries({ queryKey: detailKey });
      }
    },
  });

  if (detail.isLoading) {
    return (
      <div className="mt-4 space-y-2" data-ihr="packs-sheet-loading">
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }
  if (detail.isError || !detail.data) {
    const { status, body } = errorOf(detail.error);
    return (
      <div className="mt-4 space-y-2" role="alert" data-ihr="packs-sheet-error">
        <p className="text-red-700">
          {status === 404
            ? t("ihr_packs_error_gone")
            : t(loadErrorKey(status, body))}
        </p>
        {status !== 404 && canRetryLoad(status) && (
          <Button
            variant="outline"
            onClick={() => void detail.refetch()}
            data-ihr="packs-sheet-retry"
          >
            {t("ihr_packs_retry")}
          </Button>
        )}
      </div>
    );
  }

  const item = detail.data;
  const busy = decide.isPending || detail.isFetching;

  function submit(action: PackAction) {
    const check = reasonCheck(reason);
    setAnswer(null);
    setReasonError(!check.ok);
    // a missing reason: NO request
    if (!check.ok) return;
    decide.mutate({ action, item });
  }

  return (
    <div className="mt-4 flex min-h-0 flex-1 flex-col" data-ihr="packs-detail">
      {/* the differences scroll here; the reason box and the buttons below stay in view (P1-T24d).
          min-h-0 lets this column shrink to the sheet's height (a flex item's default minimum is its
          content); when even the 6 rem of details plus the footer cannot fit, the overflow makes the
          whole sheet scroll (overflow-y-auto above), so the buttons stay reachable. */}
      <div
        className="min-h-24 flex-1 space-y-5 overflow-y-auto pb-4 pr-1"
        data-ihr="packs-detail-scroll"
        role="region"
        aria-label={t("ihr_packs_sheet_title")}
        tabIndex={0}
      >
        <ItemFacts item={item} />
        <Why item={item} />
        <FieldDifference item={item} />
      </div>

      <div
        className="space-y-3 border-t border-gray-200 pt-4"
        data-ihr="packs-footer"
      >
        <div className="space-y-2">
          <label
            htmlFor="ihr-packs-reason"
            className="block text-sm font-medium text-gray-700"
          >
            {t("ihr_packs_reason_label")}
          </label>
          <Textarea
            id="ihr-packs-reason"
            rows={3}
            value={reason}
            maxLength={MAX_REASON * 2}
            aria-invalid={reasonError}
            onChange={(e) => {
              setReason(e.target.value);
              setReasonError(false);
            }}
            data-ihr="packs-reason"
          />
          <p className="text-xs text-gray-600">
            {t("ihr_packs_reason_hint", { max: MAX_REASON })}
          </p>
          {reasonError && (
            <p
              className="text-sm text-red-700"
              role="alert"
              data-ihr="packs-reason-error"
            >
              {t("ihr_packs_error_reason", { max: MAX_REASON })}
            </p>
          )}
        </div>

        {answer && (
          <p
            role={answer.ok ? "status" : "alert"}
            className={answer.ok ? "text-green-800" : "text-red-700"}
            data-ihr="packs-answer"
          >
            {t(answer.key, { max: MAX_REASON })}
          </p>
        )}

        <div className="flex flex-wrap gap-3">
          <Button
            disabled={busy || !canAccept(item)}
            onClick={() => submit("accept")}
            data-ihr="packs-accept"
          >
            {t("ihr_packs_accept")}
          </Button>
          {canKeep(item) ? (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => submit("keep")}
              data-ihr="packs-keep"
            >
              {t("ihr_packs_keep")}
            </Button>
          ) : (
            <p
              className="self-center text-sm text-gray-600"
              data-ihr="packs-kept"
            >
              {t("ihr_packs_kept_note")}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function ItemFacts({ item }: { item: PackDetail }) {
  const { t } = useTranslation();
  const facts: { key: string; label: string; value: string }[] = [
    {
      key: "item",
      label: t("ihr_packs_col_label"),
      value: item.label || item.item,
    },
    { key: "pack", label: t("ihr_packs_col_pack"), value: item.pack },
    {
      key: "facility",
      label: t("ihr_packs_col_facility"),
      value: facilityText(item, t("ihr_packs_whole_hospital")),
    },
    {
      key: "version",
      label: t("ihr_packs_col_version"),
      value: String(item.pending_version ?? "-"),
    },
    {
      key: "since",
      label: t("ihr_packs_col_since"),
      value: sinceText(item.changed_at),
    },
  ];
  return (
    <div className="space-y-3">
      <dl className="space-y-1 text-sm" data-ihr="packs-facts">
        {facts.map((fact) => (
          <div key={fact.key} className="grid grid-cols-3 gap-2">
            <dt className="text-gray-600">{fact.label}</dt>
            <dd
              className="col-span-2 break-words"
              data-ihr={`packs-fact-${fact.key}`}
            >
              {fact.value}
            </dd>
          </div>
        ))}
      </dl>
      <p className="rounded-md bg-gray-50 p-3 text-sm" data-ihr="packs-effect">
        <span className="font-semibold">{t("ihr_packs_effect_title")}</span>{" "}
        {t(effectKey(item.effect))}
      </p>
    </div>
  );
}

function Why({ item }: { item: PackDetail }) {
  const { t } = useTranslation();
  const why = whyKey(item.reason);
  const check = checkLine(item.conflicts);
  const held = item.conflicts;
  const system = [held.conflict, held.missing_link].filter(
    (text): text is string => typeof text === "string" && text !== "",
  );
  const stranded = item.notes?.stranded;
  if (!why && !item.blocked && !check && system.length === 0 && !stranded) {
    return null;
  }
  return (
    <div className="space-y-2 text-sm" data-ihr="packs-why">
      {why && (
        <p data-ihr="packs-why-reason">
          <span className="font-semibold">{t("ihr_packs_why_title")}</span>{" "}
          {t(why)}
        </p>
      )}
      {item.blocked && (
        <p className="text-red-700" role="alert" data-ihr="packs-blocked">
          {t("ihr_packs_blocked")}
        </p>
      )}
      {check && <p data-ihr="packs-check">{t(check.key, check.params)}</p>}
      {held.checked_at && (
        <p className="text-gray-600">
          {t("ihr_packs_check_at", { when: formatIst(held.checked_at) })}
        </p>
      )}
      {system.map((text) => (
        <p key={text} className="text-gray-700" data-ihr="packs-system-note">
          <span className="font-semibold">{t("ihr_packs_system_note")}</span>{" "}
          <span className="font-mono text-xs break-words">{text}</span>
        </p>
      ))}
      {stranded && (
        <p data-ihr="packs-stranded">
          {t("ihr_packs_stranded", {
            drafts: stranded.draft_submissions ?? 0,
            templates: stranded.response_templates ?? 0,
          })}
        </p>
      )}
    </div>
  );
}

function ThreeValues({
  hospital,
  pack,
  previous,
}: {
  hospital: unknown;
  pack: unknown;
  previous: unknown;
}) {
  const { t } = useTranslation();
  const columns: [string, unknown][] = [
    ["ihr_packs_col_ours", hospital],
    ["ihr_packs_col_pack_new", pack],
    ["ihr_packs_col_pack_prev", previous],
  ];
  return (
    <dl className="mt-1 grid gap-2 sm:grid-cols-3">
      {columns.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-gray-600">{t(label)}</dt>
          <dd className="text-sm break-words whitespace-pre-wrap">
            {fieldValue(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

function FieldDifference({ item }: { item: PackDetail }) {
  const { t } = useTranslation();
  if (item.fields.length === 0) return null;
  return (
    <div className="space-y-2" data-ihr="packs-fields">
      <h3 className="text-sm font-semibold text-gray-900">
        {t("ihr_packs_fields_title")}
      </h3>
      {item.fields.map((field) => (
        <div
          key={field.field}
          className="rounded-md border border-gray-200 p-3"
          data-ihr="packs-field"
        >
          <p className="font-mono text-xs font-semibold">{field.field}</p>
          {field.changes ? (
            field.changes.length === 0 ? (
              <p className="text-sm text-gray-600">
                {t("ihr_packs_field_same")}
              </p>
            ) : (
              field.changes.map((change) => (
                <div key={change.path} className="mt-2">
                  <p className="font-mono text-xs break-all text-gray-700">
                    {change.path}
                  </p>
                  <ThreeValues
                    hospital={change.hospital}
                    pack={change.pack}
                    previous={change.previous}
                  />
                </div>
              ))
            )
          ) : (
            <ThreeValues
              hospital={field.hospital}
              pack={field.pack}
              previous={field.previous}
            />
          )}
          {field.truncated && (
            <p className="mt-1 text-xs text-gray-600">
              {t("ihr_packs_field_truncated")}
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

export type { PackTab };
