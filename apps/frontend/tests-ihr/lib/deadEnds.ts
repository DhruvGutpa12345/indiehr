// IndieHR P1-T11c2 (design-c.md §9.1): the dead-end listener. A web screen offers only what the
// backend will answer, so on the paths the specs walk no /api/ answer may be 400, 403, 404 or a
// 5xx, unless the step declares it. The failure message lists every undeclared answer (method,
// path, status; never a query string, a body or a header), so one run shows them all.
import type { Page } from "@playwright/test";

import { expect } from "@playwright/test";

interface Declared {
  method?: string;
  path: RegExp;
  status?: number;
  reason: string;
}

export interface DeadEnds {
  /** An answer this step expects (e.g. the one refusal a spec provokes on purpose). */
  declare(declared: Declared): void;
  /** Stop recording (a step that provokes refusals on purpose), then record again. */
  pause(): void;
  resume(): void;
  /** What was recorded so far, as "status METHOD /path". */
  seen(): string[];
  /** Fails when an undeclared dead end was recorded since the last call; then clears. */
  expectNone(where: string): void;
}

function isDeadEnd(status: number): boolean {
  return status === 400 || status === 403 || status === 404 || status >= 500;
}

export function watchDeadEnds(page: Page): DeadEnds {
  const recorded: string[] = [];
  const declared: Declared[] = [];
  let paused = false;
  page.on("response", (response) => {
    if (paused) return;
    const url = new URL(response.url());
    if (!url.pathname.startsWith("/api/")) return;
    const status = response.status();
    if (!isDeadEnd(status)) return;
    const method = response.request().method();
    const allowed = declared.some(
      (d) =>
        (d.method === undefined || d.method === method) &&
        d.path.test(url.pathname) &&
        (d.status === undefined || d.status === status),
    );
    if (!allowed) recorded.push(`${status} ${method} ${url.pathname}`);
  });
  return {
    declare: (d) => void declared.push(d),
    pause: () => void (paused = true),
    resume: () => void (paused = false),
    seen: () => [...recorded],
    expectNone(where: string) {
      const found = recorded.splice(0, recorded.length);
      expect(found, `dead-end /api/ answers ${where}`).toEqual([]);
    },
  };
}
