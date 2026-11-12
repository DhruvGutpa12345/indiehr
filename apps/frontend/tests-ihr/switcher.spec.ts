// IndieHR (P1-T1d): the hospital switcher (TENANCY_DESIGN §9, an ihr_* frontend plugin) is not
// built yet. This placeholder is skipped, so it never counts as passing; it becomes a real
// test with the switcher (Phase 1 review finding R1-05).
import { test } from "./lib/fixtures";

test.skip("hospital switcher keeps each hospital's session separate (R1-05: switcher not built yet)", async () => {
  // To write with the switcher: dr.meera switches alpha -> beta -> alpha; each switch is a
  // top-level sign-in at the other address, no cookie or storage crosses over, and signing out
  // in one hospital ends both (single sign-on).
});
