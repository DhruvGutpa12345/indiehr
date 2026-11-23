// IndieHR P1-T31 (docs/reviews/p1-t31/design.md §4): the "Hospitals" group of the staff user
// menu. A person linked to two or more hospitals sees them; choosing another goes, top-level in
// the same tab, to that hospital's own sign-in start, where Keycloak single sign-on signs them in
// (each origin keeps its own cookie, ADR-009). Anyone else sees nothing, and so does a person
// whose list failed to load: the menu is passive and never toasts or redirects (review R7).
import { useQuery } from "@tanstack/react-query";
import { Building2, Check } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
} from "@/components/ui/dropdown-menu";

import useAuthUser from "@/hooks/useAuthUser";

import {
  HOSPITALS_STALE_MS,
  entryKind,
  hospitalsQueryKey,
  menuEntries,
} from "@/Utils/ihr/hospitals";
import { ihrHospitalsApi } from "@/Utils/ihr/ihrApi";
import query from "@/Utils/request/query";

export function IhrHospitalsMenu() {
  const { t } = useTranslation();
  const user = useAuthUser();
  // Per person (the key carries the username) and held in memory only: no `meta` for the
  // local-storage cache, so no list of hospitals is ever written to the browser (F10, Q-K149).
  const { data, isError } = useQuery({
    queryKey: hospitalsQueryKey(user.username),
    queryFn: query(ihrHospitalsApi.list, { silent: true }),
    staleTime: HOSPITALS_STALE_MS,
    retry: false,
  });

  const entries = menuEntries({ data, isError }, window.location.protocol);
  if (entries === null) return null;

  return (
    <>
      <DropdownMenuSeparator />
      <DropdownMenuGroup data-testid="ihr-hospitals-menu">
        <DropdownMenuLabel className="text-xs font-normal text-gray-500">
          {t("ihr_hospitals_menu")}
        </DropdownMenuLabel>
        {entries.map((entry) => {
          const kind = entryKind(entry);
          return (
            <DropdownMenuItem
              key={entry.url}
              data-ihr-hospital={kind}
              disabled={kind !== "other"}
              aria-label={
                kind === "other"
                  ? t("ihr_hospitals_switch_aria", { name: entry.name })
                  : undefined
              }
              onSelect={() => {
                if (kind === "other") window.location.assign(entry.url);
              }}
            >
              <Building2 />
              <span className="flex-1 truncate">{entry.name}</span>
              {kind === "current" && (
                <span className="flex items-center gap-1 text-xs text-gray-500">
                  <Check className="size-3" />
                  {t("ihr_hospitals_current")}
                </span>
              )}
              {kind === "unavailable" && (
                <span className="text-xs text-gray-500">
                  {t("ihr_hospitals_unavailable")}
                </span>
              )}
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuGroup>
    </>
  );
}
