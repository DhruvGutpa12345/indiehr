// IndieHR P1-T11c2 (design-c.md §3.3): CARE's NavTabs, or nothing when no tab is visible.
// NavTabs moves to "the first visible tab" when the address names a hidden one; with no visible
// tab that would be navigate(undefined) and a crash, so a role that may open the account but
// none of its tabs (the Receptionist: can_read_account only) sees the account's header only.
import type { ComponentProps } from "react";

import { NavTabs } from "@/components/ui/nav-tabs";

import { ihrAnyTabVisible } from "@/Utils/ihr/frontDesk";

export function IhrNavTabs(props: ComponentProps<typeof NavTabs>) {
  if (!ihrAnyTabVisible(props.tabs)) return null;
  return <NavTabs {...props} />;
}
