// IndieHR P1-T11c1: our menu rules (src/Utils/ihr/menu.ts, pure) applied to CARE's sidebar link
// lists, with the names and icons of our own items.
import type { TFunction } from "i18next";
import {
  FolderOpen,
  MapPin,
  PackageCheck,
  ScrollText,
  TrendingUp,
} from "lucide-react";

import type { NavigationLink } from "@/components/ui/sidebar/nav-main";

import type { IhrAccess } from "@/Utils/ihr/access";
import {
  type OurItem,
  type OurItemKey,
  ihrFacilityLinks,
  ihrLocationLinks,
} from "@/Utils/ihr/menu";

function ourLink(t: TFunction, item: OurItem): NavigationLink {
  const byKey: Record<OurItemKey, { name: string; icon: React.ReactNode }> = {
    records: { name: t("ihr_menu_records_access"), icon: <FolderOpen /> },
    totals: { name: t("ihr_menu_hospital_totals"), icon: <TrendingUp /> },
    places: { name: t("ihr_menu_my_places"), icon: <MapPin /> },
    audit: { name: t("ihr_menu_audit_trail"), icon: <ScrollText /> },
    packs: { name: t("ihr_menu_pack_updates"), icon: <PackageCheck /> },
  };
  return { ...byKey[item.key], url: item.url };
}

/** CARE's facility links as this user may use them, then our items. */
export function withIhrFacilityLinks(
  links: NavigationLink[],
  access: IhrAccess,
  facilityId: string | undefined,
  t: TFunction,
): NavigationLink[] {
  if (!facilityId) return links;
  return ihrFacilityLinks(links, access, `/facility/${facilityId}`, (item) =>
    ourLink(t, item),
  );
}

/** CARE's location links as this user may use them (restricted roles only). */
export function withIhrLocationLinks(
  links: NavigationLink[],
  access: IhrAccess,
  baseUrl: string,
): NavigationLink[] {
  return ihrLocationLinks(links, access, baseUrl);
}
