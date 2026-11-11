# ADR-012: Signed-key client authentication (private_key_jwt)

- **Status:** Accepted
- **Date:** 2026-11-11
- **Decided by:** product owner (Q-K17…Q-K20), on Keycloak 26.8.2 read from its source
- **Amends:** ADR-008 (hospital and console clients are no longer public; worker accounts no
  longer use secrets), ADR-009 §7 (the deferred `private_key_jwt`)

## Context

Until P1-T8d2 the hospitals' sign-in clients (`ihr-h-<slug>`) and the operator console
(`ihr-console`) were **public** clients: anyone holding a code and its PKCE verifier, or a
refresh token, could use them from anywhere. The two workers' service accounts used a
**client secret**, read from Keycloak at every `up` and kept in env files on the deploying
machine and the server. ADR-009 deferred signed-key authentication to P1-T8.

## Decision

1. **Every client our processes use authenticates with a signed assertion** (RFC 7523,
   `private_key_jwt`, Keycloak `client-jwt`): the hospitals' clients, the console, and both
   worker accounts. PKCE stays on the browser clients. The bootstrap admin of `master`
   (operator one-offs only) keeps its secret: it is the root that enrols keys.
2. **Three keys, one per process role:** `backend` (all hospital clients and the console —
   one process, so separate keys would add nothing), `onboarding`, `staff`. ES256 (P-256).
   Key id `<purpose>-<16 characters of the RFC 7638 thumbprint>`: a new key always has a new
   id. Assertions: `iss = sub = client`, audience the realm's **public** issuer (Keycloak
   builds it from its own hostname, never the back-channel address), 30 s lifetime (Keycloak
   allows 60), single-use id.
3. **Keycloak learns the public keys by value** (`jwks.string` in each client), written by
   `ihr_keycloak_sync` (and by the onboarding worker for a new hospital) and drift-checked.
   Not by URL: Keycloak would fetch the backend's key list in the middle of the backend's own
   token request (one or two web workers: a deadlock), a URL cannot be drift-checked, and a
   client update already clears Keycloak's key cache at once.
4. **Private keys live on the server only** (owner, Q-K17), each in its own Docker volume
   mounted read-only into its own container (backend, provisioner, staff-worker); never on the
   deploying machine, never in env files, never backed up. A lost key is replaced by a new one,
   enrolled with the bootstrap credential. A one-off `keytool` (no network, no logs) is the only
   writer.
5. **Rotation is manual, every 180 days** (owner, Q-K18): `staging.sh keys rotate
   <backend|onboarding|staff|all>` stages a key, publishes both, proves the new one, switches
   (signers notice the change on their next call: no restart), waits out the old assertions,
   retires and proves the old key refused, deletes it. `smoke` warns at 150 days, fails at 365.
6. **Suspected theft** (owner, Q-K20): `keys rotate <purpose> --compromised` always makes a
   brand-new key (a key left staged by a stopped rotation sat in the same folder, so it is
   discarded), switches at once (in-flight calls may fail for seconds) and, for the backend key,
   ends every staff and operator session (realm "not before" + logout of all sessions) **before**
   publishing, so a sync that stops on another problem cannot leave stolen sessions alive. Until
   it has finished, an ordinary rotation of that key is refused. `keys revoke-sessions` does the
   session part on its own. The workers' accounts get one-minute access tokens, so tokens minted
   with a stolen worker key die within a minute of the switch.
7. **The dev token viewer is retired** (owner, Q-K19): it could not sign.

## Consequences

- A stolen code, refresh token or worker secret alone no longer gets tokens: the thief also
  needs the key, which never leaves its container's volume.
- `up` now creates keys (first run), starts the new backend (clients still public on an older
  server ignore its assertions), publishes the keys, and only then starts the workers; after a
  database restore, sign-ins wait only for that sync, seconds later.
- A restarted Keycloak forgets which assertion ids it has seen: an assertion younger than 60 s
  could be replayed once. Accepted on a single node.
- Keycloak 26.8.2 limits an assertion's age (now − issued-at ≤ 60 s), not exp − iat; our
  probes test the age.
- Key ages and rotations are logged to the journal (purpose, key id, time; never the key).
