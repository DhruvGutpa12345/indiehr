// IndieHR (P1-T1d): runs once before the browser specs. A "refused" flag left by an earlier run
// (lib/totp.ts) would otherwise stop every later run in preflight; this run gets a fresh
// chance, and a refusal in it still stops all of its later sign-ins.
import { clearRefused } from "./totp";

export default function globalSetup() {
  clearRefused();
}
