// IndieHR P2-T0c (docs/reviews/p2-t0c/design.md §4.1-§4.3): our own pages and CARE's auth-page
// redirects, added to CARE's generated route tree by one marked call in src/router.tsx
// (`routeTree: withIhrRouteTree(routeTree)`) before the router is built. src/routeTree.gen.ts
// stays byte-identical to upstream's.
//
// - Our five pages are code routes under `/_app` (ids routeGates.ts IHR_PAGE_IDS). They take the
//   staff guard, the staff shell and the facility sidebar like CARE's own facility pages (which
//   are direct children of `/_app` too), get gate "open" (they gate themselves) and the
//   facility grant watcher (IhrStaffPage.tsx).
// - `/2fa`, `/forgot-password` and `/password_reset/$token` are replaced, under the same ids, by
//   routes whose beforeLoad redirects to /login: staff sign in through Keycloak (ADR-009), so
//   CARE's two-factor, password reset and forgot-password forms never render. `publicOnly` on
//   `/_public` runs first (a signed-in staff user goes to `/`).
//
// Idempotent (routeGates.ts mergeChildren): a second call (Vite HMR re-evaluating router.tsx)
// replaces our routes in place and adds nothing. Importing CARE's route modules makes a renamed
// or removed upstream file a build error.
import {
  type AnyRoute,
  createRoute,
  redirect,
  useParams,
} from "@tanstack/react-router";
import type { ComponentType } from "react";

import { IHR_PAGE_PATHS, mergeChildren } from "@/Utils/ihr/routeGates";
import { Route as AppRoute } from "@/routes/_app";
import { Route as PublicRoute } from "@/routes/_public";
import { Route as TwoFaRoute } from "@/routes/_public/2fa";
import { Route as ForgotRoute } from "@/routes/_public/forgot-password";
import { Route as ResetRoute } from "@/routes/_public/password_reset.$token";

import { IhrAuditTrail } from "./IhrAuditTrail";
import { IhrMyPlaces } from "./IhrMyPlaces";
import { IhrOwnerTotals } from "./IhrOwnerTotals";
import { IhrPackUpdates } from "./IhrPackUpdates";
import { IhrRecordsAccess } from "./IhrRecordsAccess";

type IhrPageComponent = ComponentType<{ facilityId: string }>;

const IHR_PAGES: Record<(typeof IHR_PAGE_PATHS)[number], IhrPageComponent> = {
  "/facility/$facilityId/ihr/audit": IhrAuditTrail,
  "/facility/$facilityId/ihr/packs": IhrPackUpdates,
  "/facility/$facilityId/ihr/places": IhrMyPlaces,
  "/facility/$facilityId/ihr/records": IhrRecordsAccess,
  "/facility/$facilityId/ihr/totals": IhrOwnerTotals,
};

/** The one place our pages read their param (hooks only in a named component). */
function IhrPageRoute({ Page }: { Page: IhrPageComponent }) {
  const { facilityId } = useParams({ strict: false });
  return <Page facilityId={facilityId ?? ""} />;
}

/**
 * A route's id below its parent, before the router has built the tree (`route.id` is set only
 * then): the generated tree gives file routes `options.id`, our code routes have `options.path`.
 */
function localId(route: AnyRoute): string {
  const options = route.options as { id?: string; path?: string };
  return String(options.id ?? options.path ?? "").replace(/^\/+/, "");
}

function pathOf(route: AnyRoute): string {
  const options = route.options as { path?: string };
  if (!options.path) {
    throw new Error("ihr-route-tree: a CARE auth route has no path");
  }
  return options.path;
}

/** CARE's route tree with our pages and the auth redirects (design §4.1). */
export function withIhrRouteTree<T>(routeTree: T): T {
  const pages = IHR_PAGE_PATHS.map((path) => {
    const Page = IHR_PAGES[path];
    return createRoute({
      getParentRoute: () => AppRoute,
      path,
      component: function IhrPage() {
        return <IhrPageRoute Page={Page} />;
      },
    }) as unknown as AnyRoute;
  });
  AppRoute.addChildren(
    mergeChildren(AppRoute.children as AnyRoute[] | undefined, pages, localId),
  );

  const toLogin = (
    [TwoFaRoute, ForgotRoute, ResetRoute] as unknown as AnyRoute[]
  ).map(
    (route) =>
      createRoute({
        getParentRoute: () => PublicRoute,
        path: pathOf(route),
        beforeLoad: () => {
          throw redirect({ to: "/login", replace: true });
        },
      }) as unknown as AnyRoute,
  );
  PublicRoute.addChildren(
    mergeChildren(
      PublicRoute.children as AnyRoute[] | undefined,
      toLogin,
      localId,
    ),
  );
  return routeTree;
}
