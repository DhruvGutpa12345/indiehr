// IndieHR (P1-T1d): the synthetic Keycloak users written by `scripts/care-dev.sh keycloak seed`
// into the git-ignored deploy/seed/TEST_USERS.md. Only the Keycloak section is read. Nothing
// here is ever printed: error messages name the user and the file, never a value.
import { readFileSync } from "node:fs";
import { join } from "node:path";

export interface TestUser {
  username: string;
  password: string;
  rawSecret: string | null; // the secret exactly as Keycloak stores it (the `raw:` comment)
  hospitals: string[];
}

const START = "<!-- keycloak:start -->";
const END = "<!-- keycloak:end -->";

export function testUsersPath(): string {
  return (
    process.env.IHR_TEST_USERS ??
    join(__dirname, "..", "..", "..", "..", "deploy", "seed", "TEST_USERS.md")
  );
}

let cache: Map<string, TestUser> | null = null;

export function readTestUsers(path = testUsersPath()): Map<string, TestUser> {
  if (cache) return cache;
  const text = readFileSync(path, "utf8");
  const start = text.indexOf(START);
  const end = text.indexOf(END, start);
  if (start < 0 || end < 0) {
    throw new Error(
      `${path} has no Keycloak section: run scripts/care-dev.sh keycloak seed`,
    );
  }
  const users = new Map<string, TestUser>();
  for (const line of text.slice(start, end).split("\n")) {
    // | username | name | alpha: care-doctor, beta: … | `password` | `APPKEY` <!-- raw:hex --> |
    const cells = line.split("|").map((cell) => cell.trim());
    if (cells.length < 7 || !/^[a-z0-9._-]+$/.test(cells[1])) continue;
    const password = /^`([^`]+)`$/.exec(cells[4])?.[1];
    if (!password) continue;
    users.set(cells[1], {
      username: cells[1],
      password,
      rawSecret: /raw:([0-9a-f]+)/.exec(cells[5])?.[1] ?? null,
      hospitals: cells[3]
        .split(",")
        .map((part) => part.split(":")[0].trim())
        .filter(Boolean),
    });
  }
  cache = users;
  return users;
}

export function testUser(username: string): TestUser {
  const user = readTestUsers().get(username);
  if (!user) {
    throw new Error(
      `No Keycloak test user ${username} in ${testUsersPath()}: run scripts/care-dev.sh keycloak seed`,
    );
  }
  return user;
}

/** Every secret value from the file, for scrubbing failure evidence. */
export function secretValues(): string[] {
  const values: string[] = [];
  for (const user of readTestUsers().values()) {
    values.push(user.password);
    if (user.rawSecret) values.push(user.rawSecret);
  }
  return values;
}
