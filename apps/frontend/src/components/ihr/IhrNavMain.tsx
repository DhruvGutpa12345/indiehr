// IndieHR P1-T11c1 (design-c.md §2.2, §3.2): CARE's sidebar (`NavMain`) with the links this user
// may use, plus our own items. facility-nav.tsx and location-nav.tsx render these instead of
// NavMain: one changed tag each, CARE's long link lists stay as they are.
import { useTranslation } from "react-i18next";

import { NavMain, NavigationLink } from "@/components/ui/sidebar/nav-main";

import { useIhrAccess } from "@/Utils/ihr/useIhrAccess";

import { withIhrFacilityLinks, withIhrLocationLinks } from "./ihrMenu";

interface IhrFacilityNavMainProps {
  links: NavigationLink[];
  facilityId: string | undefined;
}

export function IhrFacilityNavMain({
  links,
  facilityId,
}: IhrFacilityNavMainProps) {
  const { t } = useTranslation();
  const ihr = useIhrAccess();
  return <NavMain links={withIhrFacilityLinks(links, ihr, facilityId, t)} />;
}

interface IhrLocationNavMainProps {
  links: NavigationLink[];
  baseUrl: string;
}

export function IhrLocationNavMain({
  links,
  baseUrl,
}: IhrLocationNavMainProps) {
  const ihr = useIhrAccess();
  return <NavMain links={withIhrLocationLinks(links, ihr, baseUrl)} />;
}
