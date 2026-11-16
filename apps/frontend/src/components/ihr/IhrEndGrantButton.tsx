// IndieHR P1-T11c3 (design-c.md §6, §13.3): "End now" for one live records grant, with a
// confirmation. Used by the records page's list and the patient page's banner. The end itself is
// the watcher's one path (IhrGrantWatcher.endGrantNow).
import { useState } from "react";
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
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";

import { endGrantNow } from "./IhrGrantWatcher";

interface IhrEndGrantButtonProps {
  grantId: number;
  patientName: string;
  size?: "sm" | "default";
}

export function IhrEndGrantButton({
  grantId,
  patientName,
  size = "sm",
}: IhrEndGrantButtonProps) {
  const { t } = useTranslation();
  const [busy, setBusy] = useState(false);
  return (
    <AlertDialog>
      <AlertDialogTrigger asChild>
        <Button
          variant="outline"
          size={size}
          disabled={busy}
          data-ihr="end-grant"
        >
          {t("ihr_records_end_now")}
        </Button>
      </AlertDialogTrigger>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{t("ihr_records_end_title")}</AlertDialogTitle>
          <AlertDialogDescription>
            {t("ihr_records_end_body", { name: patientName })}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>{t("cancel")}</AlertDialogCancel>
          <AlertDialogAction
            data-ihr="end-grant-confirm"
            onClick={() => {
              setBusy(true);
              void endGrantNow(grantId).finally(() => setBusy(false));
            }}
          >
            {t("ihr_records_end_confirm")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
