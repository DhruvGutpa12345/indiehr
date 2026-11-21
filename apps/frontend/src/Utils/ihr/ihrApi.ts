// IndieHR P1-T11c: our own API routes in CARE's route-object shape (query(), mutate()).
import type { MyAccess } from "@/Utils/ihr/access";
import type {
  AuditEntryDetail,
  AuditList,
  AuditStatus,
} from "@/Utils/ihr/audit";
import type { Grant, GrantList, GrantRequest } from "@/Utils/ihr/records";
import type { StaffAccess, StaffActionResult } from "@/Utils/ihr/staffAccess";
import type { Totals } from "@/Utils/ihr/totals";
import { HttpMethod, Type } from "@/Utils/request/types";

export const ihrAccessApi = {
  /** Whether this user is "restricted" in the facility (the backend's decision, Q-K49 A') */
  myAccess: {
    path: "/api/ihr/my-access/",
    method: HttpMethod.GET,
    TRes: Type<MyAccess>(),
  },
} as const;

/** MRD's records-access grants (P1-T11c3, design-c.md §6, §13.3) */
export const ihrRecordsApi = {
  /** The watcher's poll: ids and times only, shows no patient (review BA1) */
  list: {
    path: "/api/ihr/records-access/",
    method: HttpMethod.GET,
    TRes: Type<GrantList>(),
  },
  /** With each patient's name and year of birth: the records page only (queryParams NAMES_QUERY) */
  listWithNames: {
    path: "/api/ihr/records-access/",
    method: HttpMethod.GET,
    TRes: Type<GrantList<Grant>>(),
  },
  create: {
    path: "/api/ihr/records-access/",
    method: HttpMethod.POST,
    TRes: Type<Grant>(),
    TBody: Type<GrantRequest>(),
  },
  end: {
    path: "/api/ihr/records-access/{id}/",
    method: HttpMethod.DELETE,
    TRes: Type<Record<string, never>>(),
  },
} as const;

/** The Owner's totals (P1-T11c3, design-c.md §7) */
export const ihrTotalsApi = {
  get: {
    path: "/api/ihr/owner/totals/",
    method: HttpMethod.GET,
    TRes: Type<Totals>(),
  },
} as const;

/**
 * The audit trail (P1-T24a; hospital-wide admins only, the backend refuses everyone else).
 * Every call is itself recorded, so the page never polls and never refetches on focus.
 */
export const ihrAuditApi = {
  entries: {
    path: "/api/ihr/audit/entries/",
    method: HttpMethod.GET,
    TRes: Type<AuditList>(),
  },
  /** One entry with its old and new values: only on opening an entry */
  entry: {
    path: "/api/ihr/audit/entries/{id}/",
    method: HttpMethod.GET,
    TRes: Type<AuditEntryDetail>(),
  },
  status: {
    path: "/api/ihr/audit/status/",
    method: HttpMethod.GET,
    TRes: Type<AuditStatus>(),
  },
} as const;

/**
 * The staff "Sign-in" card (P1-T24b). `get` says what may be offered for one person (`offers`);
 * `act` is reset-sign-in, resend-invitation or cancel-invitation and is queued for the
 * provisioner (202). The backend refuses any action it did not offer.
 */
export const ihrStaffApi = {
  get: {
    path: "/api/ihr/staff/{username}/access/",
    method: HttpMethod.GET,
    TRes: Type<StaffAccess>(),
  },
  act: {
    path: "/api/ihr/staff/{username}/{action}/",
    method: HttpMethod.POST,
    TRes: Type<StaffActionResult>(),
    TBody: Type<Record<string, never>>(),
  },
} as const;
