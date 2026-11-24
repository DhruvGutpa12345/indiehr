// IndieHR P2-T0c (docs/reviews/p2-t0c/design.md §3.1, §3.2): the one hook point of our route
// gates on CARE's TanStack router. AppShell.tsx (marked patch) wraps the staff page outlet in
// it, so it covers every staff page (`/_app/**` and the root-level shared routes rendered for
// staff) and never a patient-portal or public page. It sits inside AppShell's ErrorBoundary: a
// crash here shows CARE's error page, never the page.
//
// It reads the rendered (committed) matches and takes the deepest match that did not fail (the
// leaf, routeGates.ts leafSignature); the gate (IhrRouteGate) and the one grant watcher take
// the route id and the facility only from that leaf. The watcher is always rendered (scope null
// = inert), so one instance lives across every staff navigation.
import { useMatches } from "@tanstack/react-router";
import { type ReactNode, useMemo } from "react";

import { leafSignature, parseLeaf, watcherScope } from "@/Utils/ihr/routeGates";

import { IhrRouteGate } from "./IhrGates";
import { IhrGrantWatcher } from "./IhrGrantWatcher";

export function IhrStaffPage({ children }: { children: ReactNode }) {
  // one primitive: stable between router store updates (as AreaChrome.tsx does)
  const signature = useMatches({ select: leafSignature });
  const leaf = useMemo(() => parseLeaf(signature), [signature]);
  return (
    <IhrGrantWatcher
      scope={watcherScope(leaf.routeId)}
      facilityId={leaf.facilityId}
    >
      <IhrRouteGate leaf={leaf}>{children}</IhrRouteGate>
    </IhrGrantWatcher>
  );
}
