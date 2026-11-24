// IndieHR P1-T24b (docs/reviews/p1-t24/design.md §2.2 and "T24b as decided"; attack.md "Decision"):
// the "Sign-in" card on a staff member's user page. A hospital's admins use it to send an
// invitation again, cancel one, or reset a colleague's lost sign-in.
//
// - The BACKEND decides what may be offered (`offers`, GET /api/ihr/staff/<username>/access/):
//   a button exists only for an offered action, and the card is hidden when there are none (for
//   oneself, for a role that may not add staff, for a hospital-wide admin as the target of a
//   reset). It also hides after a 403 or 404. Restricted roles never ask: wantsCard().
// - A status or a problem is a fixed phrase chosen from a code (staffAccess.ts); the worker's
//   own text never reaches this page.
// - Reset and cancel ask first, with a plain confirm that says what happens. The reset's
//   warning is the whole point of the dialog: what is removed, who is signed out, who to check.
// - After an action the card stays, says what was asked, and re-reads the state every 10 s
//   (at most 6 times) while the provisioner works.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import { ihrStaffApi } from "@/Utils/ihr/ihrApi";
import {
  OFFER_LABEL_KEYS,
  OFFER_NOTICE_KEYS,
  type StaffAccess,
  type StaffOffer,
  actionLabelKey,
  cardVisible,
  confirmNeeded,
  formatIst,
  offerButtons,
  pollDelay,
  problemKey,
  problemShown,
  refusalKey,
  requestStatusKey,
  stateLine,
  wantsCard,
} from "@/Utils/ihr/staffAccess";
import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";
import mutate from "@/Utils/request/mutate";
import query from "@/Utils/request/query";
import { HTTPError } from "@/Utils/request/types";

interface IhrStaffAccessCardProps {
  username: string;
  /** shown in the dialogs ("First Last") */
  displayName: string;
  isOwnAccount: boolean;
  isServiceAccount: boolean;
}

export function IhrStaffAccessCard(props: IhrStaffAccessCardProps) {
  const access = useIhrAccess();
  const wanted = wantsCard({
    mode: access.mode,
    isOwn: props.isOwnAccount,
    isServiceAccount: props.isServiceAccount,
    hasUsername: props.username !== "",
  });
  // key: another person's page starts with a clean card
  return wanted ? (
    <StaffAccessCardBody key={props.username} {...props} />
  ) : null;
}

function StaffAccessCardBody({
  username,
  displayName,
}: IhrStaffAccessCardProps) {
  const { t } = useTranslation();
  const queryClient = useQueryClient();
  const queryKey = ["ihr", "staff-access", username];
  const polls = useRef(0);
  const [asked, setAsked] = useState<StaffOffer | null>(null);
  const [confirming, setConfirming] = useState<StaffOffer | null>(null);
  const [errorLine, setErrorLine] = useState<string | null>(null);

  const access = useQuery<StaffAccess>({
    queryKey,
    queryFn: query(ihrStaffApi.get, {
      pathParams: { username },
      silent: true,
    }),
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: 0,
    refetchInterval: (q) => pollDelay(q.state.data, polls.current),
  });

  // count the re-reads made while waiting; a settled answer starts the count again
  useEffect(() => {
    if (access.dataUpdatedAt === 0) return;
    if (pollDelay(access.data, 0) === false) polls.current = 0;
    else polls.current += 1;
  }, [access.dataUpdatedAt, access.data]);

  const act = useMutation({
    mutationFn: (action: StaffOffer) =>
      mutate(ihrStaffApi.act, {
        pathParams: { username, action },
        silent: true,
      })({}),
    onSuccess: (_result, action) => {
      setAsked(action);
      setErrorLine(null);
      polls.current = 0;
      void queryClient.invalidateQueries({ queryKey });
    },
    onError: (error) => {
      const status = error instanceof HTTPError ? error.status : undefined;
      if (status === 403) setErrorLine(t("ihr_staff_error_forbidden"));
      else if (status === 400)
        setErrorLine(
          t(refusalKey(error instanceof HTTPError ? error.cause : undefined)),
        );
      else setErrorLine(t("ihr_staff_error_request"));
      // what was offered may have changed under us
      void queryClient.invalidateQueries({ queryKey });
    },
  });

  const loadError = access.error;
  const loadStatus = loadError instanceof HTTPError ? loadError.status : 0;
  // not for this viewer or this person: nothing shows (the backend's answer, not a message)
  if (loadError && (loadStatus === 403 || loadStatus === 404)) return null;

  // Nothing while loading: a viewer who may not add staff gets a 403 and no card (the binding
  // Decision, attack.md), so a skeleton would flash and vanish (review W2)
  if (access.isLoading) return null;

  if (loadError && !access.data) {
    return (
      <Card data-ihr="staff-card" data-ihr-state="error">
        <CardHeader className="px-4 sm:px-6">
          <CardTitle>{t("ihr_staff_title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 px-4 sm:px-6">
          <p className="text-sm text-destructive" role="alert">
            {t("ihr_staff_error_load")}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void access.refetch()}
            data-ihr="staff-retry"
          >
            {t("ihr_staff_retry")}
          </Button>
        </CardContent>
      </Card>
    );
  }

  const data = access.data;
  if (!data || !cardVisible(data, asked !== null)) return null;

  const line = stateLine(data);
  const buttons = offerButtons(data);
  const invitationProblem = problemShown(data.state)
    ? problemKey(data.invitation?.problem)
    : null;

  function choose(action: StaffOffer) {
    setErrorLine(null);
    if (confirmNeeded(action)) setConfirming(action);
    else act.mutate(action);
  }

  return (
    <Card data-ihr="staff-card" data-ihr-state={data.state}>
      <CardHeader className="px-4 sm:px-6">
        <CardTitle>{t("ihr_staff_title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4 px-4 sm:px-6">
        <p className="text-sm" data-ihr="staff-state">
          {t(line.key, line.params ?? {})}
        </p>
        {invitationProblem && (
          <p className="text-sm text-amber-700" data-ihr="staff-problem">
            {t(invitationProblem)}
          </p>
        )}

        {asked && (
          <p
            className="text-sm text-green-700"
            role="status"
            data-ihr="staff-notice"
          >
            {t(OFFER_NOTICE_KEYS[asked])}
          </p>
        )}
        {errorLine && (
          <p
            className="text-sm text-destructive"
            role="alert"
            data-ihr="staff-error"
          >
            {errorLine}
          </p>
        )}

        {buttons.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {buttons.map((action) => (
              <Button
                key={action}
                variant={action === "reset-sign-in" ? "destructive" : "outline"}
                size="sm"
                disabled={act.isPending}
                onClick={() => choose(action)}
                data-ihr={`staff-offer-${action}`}
              >
                {t(OFFER_LABEL_KEYS[action])}
              </Button>
            ))}
          </div>
        )}

        {data.requests.length > 0 && (
          <div data-ihr="staff-requests">
            <h3 className="mb-1 text-sm font-medium">
              {t("ihr_staff_recent")}
            </h3>
            <ul className="space-y-1 text-sm text-gray-600">
              {data.requests.map((row, index) => {
                const problem = problemKey(row.problem);
                return (
                  <li key={`${row.requested_at}-${index}`}>
                    {t(actionLabelKey(row.action))} ·{" "}
                    {t(requestStatusKey(row.status))} ·{" "}
                    {formatIst(row.requested_at)}
                    {problem && <> · {t(problem)}</>}
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </CardContent>

      <AlertDialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open) setConfirming(null);
        }}
      >
        <AlertDialogContent data-ihr="staff-confirm">
          <AlertDialogHeader>
            <AlertDialogTitle>
              {confirming === "reset-sign-in"
                ? t("ihr_staff_reset_title", { name: displayName })
                : t("ihr_staff_cancel_title")}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {confirming === "reset-sign-in"
                ? t("ihr_staff_reset_warning")
                : t("ihr_staff_cancel_body")}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>
              {confirming === "reset-sign-in"
                ? t("cancel")
                : t("ihr_staff_keep")}
            </AlertDialogCancel>
            <AlertDialogAction
              data-ihr="staff-confirm-go"
              onClick={() => {
                if (confirming) act.mutate(confirming);
                setConfirming(null);
              }}
            >
              {confirming === "reset-sign-in"
                ? t("ihr_staff_reset")
                : t("ihr_staff_cancel")}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
}
