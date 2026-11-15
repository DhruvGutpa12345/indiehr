// IndieHR P1-T11c1 (design-c.md §2.3, §13.2): our routes, and gates around some of CARE's, added
// in one call around CARE's route table (AppRouter.tsx: `withIhrRoutes({ ... })`).
//
// CARE's routes are wrapped BY KEY and CARE's own route function is called for the element, so
// no copy of its tree is kept. raviger takes the first key that matches, in insertion order:
// wrapping keeps every key where it was, and our own keys come last (none overlaps a CARE key).
// Which key gets which gate is src/Utils/ihr/routeKeys.ts (pure, unit-tested). A required key
// CARE no longer defines throws in development and logs "ihr-routes: missing key ..." in
// production (every Playwright spec fails on that line, tests-ihr/lib/fixtures.ts);
// scripts/check-ihr-frontend.py checks the same keys against CARE's source in CI.
import type { ReactNode } from "react";

import type { AppRoutes } from "@/Routers/AppRouter";

import { REQUIRED_KEYS, routeGate } from "@/Utils/ihr/routeKeys";

import {
  IhrBedBoardGate,
  IhrEncounterGate,
  IhrEncounterListGate,
  IhrPatientGate,
} from "./IhrGates";
import { IhrMyPlaces } from "./IhrMyPlaces";
import { IhrOwnerTotals } from "./IhrOwnerTotals";
import { IhrRecordsAccess } from "./IhrRecordsAccess";

type Params = Record<string, string>;
type AnyRoute = (params: Params) => ReactNode;

function reportMissing(key: string) {
  const message = `ihr-routes: missing key ${key}`;
  if (import.meta.env.DEV) throw new Error(message);
  console.error(message);
}

/** CARE's route wrapped in the gate its key gets (src/Utils/ihr/routeKeys.ts). */
function wrap(key: string, route: AnyRoute): AnyRoute {
  switch (routeGate(key)) {
    case "encounter":
      return function encounterRoute(params: Params) {
        return (
          <IhrEncounterGate
            facilityId={params.id}
            patientId={params.patientId}
            encounterId={params.encounterId}
            render={() => route(params)}
          />
        );
      };
    case "encounterPage":
      // questionnaires, reports, consents, print pages: clinical, CARE's only for "care"
      return function encounterPageRoute(params: Params) {
        return (
          <IhrPatientGate
            facilityId={params.facilityId}
            patientId={params.patientId}
            demographicsAllowed={false}
            render={() => route(params)}
          />
        );
      };
    case "bedBoardTab":
      return function bedBoardTabRoute(params: Params) {
        if (params.tab !== "locations") return route(params);
        return (
          <IhrBedBoardGate
            facilityId={params.facilityId}
            render={() => route(params)}
          />
        );
      };
    case "bedBoardLocation":
      return function bedBoardLocationRoute(params: Params) {
        return (
          <IhrBedBoardGate
            facilityId={params.facilityId}
            render={() => route(params)}
          />
        );
      };
    case "encounterList":
      return function encounterListRoute(params: Params) {
        return (
          <IhrEncounterListGate
            facilityId={params.facilityId}
            render={() => route(params)}
          />
        );
      };
    case "patient":
    case "patientClinical": {
      const demographicsAllowed = routeGate(key) === "patient";
      return function patientRoute(params: Params) {
        return (
          <IhrPatientGate
            facilityId={params.facilityId}
            patientId={params.id ?? params.patientId}
            demographicsAllowed={demographicsAllowed}
            render={() => route(params)}
          />
        );
      };
    }
    default:
      return route;
  }
}

const OUR_ROUTES: Record<string, AnyRoute> = {
  "/facility/:facilityId/ihr/places": ({ facilityId }) => (
    <IhrMyPlaces facilityId={facilityId} />
  ),
  "/facility/:facilityId/ihr/records": ({ facilityId }) => (
    <IhrRecordsAccess facilityId={facilityId} />
  ),
  "/facility/:facilityId/ihr/totals": ({ facilityId }) => (
    <IhrOwnerTotals facilityId={facilityId} />
  ),
};

/** CARE's route table with our gates around some routes and our own routes added. */
export function withIhrRoutes(routes: AppRoutes): AppRoutes {
  const table = routes as unknown as Record<string, AnyRoute>;
  for (const key of REQUIRED_KEYS) {
    if (!(key in table)) reportMissing(key);
  }
  const wrapped: Record<string, AnyRoute> = {};
  for (const [key, route] of Object.entries(table)) {
    wrapped[key] = wrap(key, route);
  }
  return { ...wrapped, ...OUR_ROUTES } as unknown as AppRoutes;
}
