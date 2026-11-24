import type { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";

import ErrorPage from "@/components/ErrorPages/DefaultErrorPage";
// IndieHR P2-T0c: our pages and the auth-page redirects (docs/reviews/p2-t0c/design.md §4.1)
import { withIhrRouteTree } from "@/components/ihr/ihrRouteTree";

import { type RouterAuth, routerAuth } from "@/lib/router/auth";
import { setRouter } from "@/lib/router/routerRef";
import { parseSearch, stringifySearch } from "@/lib/router/searchParams";
import "@/lib/router/staticData";
import { routeTree } from "@/routeTree.gen";
import queryClient from "@/Utils/request/queryClient";

export interface RouterContext {
  queryClient: QueryClient;
  auth: RouterAuth;
}

export const router = createRouter({
  routeTree: withIhrRouteTree(routeTree), // IndieHR P2-T0c
  context: { queryClient, auth: routerAuth },
  defaultPreload: "intent",
  defaultPreloadStaleTime: 0,
  scrollRestoration: true,
  // IndieHR P2-T0c (design §6.4): the default key falls back to `location.href` (patient ids in
  // paths) in sessionStorage "tsr-scroll-restoration-v1_3"; only the random history key is used.
  getScrollRestorationKey: (location) =>
    location.state.__TSR_key ?? "ihr-unkeyed",
  parseSearch,
  stringifySearch,
  // Some params hold comma-separated ids (`$prescriptionIds`, `$invoiceIds`).
  // Keep the comma as is when a link is built, as raviger did, so typed links
  // and legacy links give the same URL. A `%2C` in a URL still matches.
  pathParamsAllowedCharacters: [","],
  defaultNotFoundComponent: () => <ErrorPage />,
});

setRouter(router);

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}
