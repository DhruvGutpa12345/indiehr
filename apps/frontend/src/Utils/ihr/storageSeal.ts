// IndieHR (P2-T0b review F1, Q-T9): once a staff session has ended in this page,
// clearStaffStorage() seals the browser storage until the page is replaced. A writer
// that runs after the sweep (CARE's questionnaire fill drafts flush on `pagehide` and on
// unmount, i.e. during the navigation to the sign-in page) must check this and write
// nothing, or answers about a patient would be left behind on a shared computer.
// Dependency-free so CARE files can import it without pulling in the session code.

let sealed = false;

export function sealStaffStorage() {
  sealed = true;
}

export function staffStorageSealed() {
  return sealed;
}

/** Tests only: a fresh page starts unsealed. */
export function unsealForTest() {
  sealed = false;
}
