// IndieHR P1-T11c: our own API routes in CARE's route-object shape (query(), mutate()).
import type { MyAccess } from "@/Utils/ihr/access";
import { HttpMethod, Type } from "@/Utils/request/types";

export const ihrAccessApi = {
  /** Whether this user is "restricted" in the facility (the backend's decision, Q-K49 A') */
  myAccess: {
    path: "/api/ihr/my-access/",
    method: HttpMethod.GET,
    TRes: Type<MyAccess>(),
  },
} as const;
