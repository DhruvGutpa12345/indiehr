# shellcheck shell=bash
# Signing keys for Keycloak client authentication (P1-T8d2, private_key_jwt with ES256).
# Sourced by scripts/staging.sh (the server) and scripts/care-dev.sh (this machine), so both
# run the very same rotation; never run on its own.
#
# Three keys, one per process role, each in its own Docker volume mounted read-only at
# /run/ihr-keys in its holder only (owner decision D1, 11-11-2026: server only, never on the
# deploying machine, never backed up):
#   backend     signs for every hospital client ihr-h-<slug> and the console (backend)
#   onboarding  signs for ihr-onboarding (provisioner)
#   staff       signs for ihr-staff-worker (staff-worker)
# The "keytool" one-off (no network, no logs) runs ihr_platform.client_keys on them. After every
# change the purpose's public set is written to <KEY_JWKS_DIR>/<purpose>.jwks.json, and
# ihr_keycloak_sync gives that set to Keycloak (jwks.string).
#
# The caller defines:
#   kt <purpose> <command> [args]   python -m ihr_platform.client_keys <command> --purpose P
#                                   --dir <P's volume> [args], in the keytool one-off
#   key_publish_sync                ihr_keycloak_sync --realm --all (bootstrap credential)
#   key_check_sync                  ihr_keycloak_sync --all --check
#   key_probe_in <service> [args]   manage.py ihr_client_keys probe in that running container:
#                                   exit 0 = Keycloak accepted the client, 1 = it refused it
#                                   (invalid_client), anything else = the probe itself failed
#   key_revoke_sessions             manage.py ihr_client_keys revoke-sessions (bootstrap credential)
#   key_log <text>                  one line to the journal where there is one (kid and time only)
#   die <text>
# and sets KEY_JWKS_DIR (the folder mounted at /run/ihr-jwks).

KEY_PURPOSES=(backend onboarding staff)
# An assertion lives at most 60 s (max.exp on every client) and Keycloak allows 15 s of clock
# skew, so 75 s after the switch no call can still carry the old key; 120 s leaves room.
KEY_RETIRE_WAIT=120

key_holder() {  # the container that holds a purpose's key and signs with it
  case "$1" in
    backend) echo backend ;;
    onboarding) echo provisioner ;;
    staff) echo staff-worker ;;
    *) die "no such key: $1 (backend, onboarding or staff)" ;;
  esac
}

key_kid_ok() {  # key_kid_ok <purpose> <kid>: <purpose>-<16 characters of the key's thumbprint>
  [[ "$2" =~ ^$1-[A-Za-z0-9_-]{16}$ ]]
}

key_status_json() {  # key_status_json <purpose>: kids, states, ages and the verdict on the age
  local out
  out="$(kt "$1" status --json)" || die "could not read the $1 keys"
  echo "$out"
}

key_with_state() {  # key_with_state <purpose> <staged|active|previous|retired>: kids, one per line
  local out
  out="$(key_status_json "$1")"
  python3 -c 'import json,sys
for k in json.load(sys.stdin)["keys"]:
    if k["state"] == sys.argv[1]:
        print(k["kid"])' "$2" <<< "$out" || die "the $1 key status is unreadable"
}

# Checks the key tool's public output before it is written anywhere: exactly the public fields
# of an ES256 key (never "d", the private part), "use":"sig" (Keycloak ignores a key without it
# in jwks.string) and a kid of this purpose. Written back in one canonical form.
KEY_PUBLIC_CHECK='
import json, re, sys
purpose = sys.argv[1]
try:
    jwks = json.load(sys.stdin)
    keys = jwks["keys"]
    assert set(jwks) == {"keys"} and keys
    for k in keys:
        assert set(k) == {"kty", "crv", "x", "y", "kid", "use", "alg"}
        assert (k["kty"], k["crv"], k["use"], k["alg"]) == ("EC", "P-256", "sig", "ES256")
        assert re.fullmatch(re.escape(purpose) + r"-[A-Za-z0-9_-]{16}", k["kid"])
except Exception:
    sys.exit(1)
keys.sort(key=lambda k: k["kid"])
print(json.dumps({"keys": keys}, sort_keys=True, separators=(",", ":")))
'

kt_change() {  # kt_change <purpose> <ensure|stage|activate|retire|delete|public> [args]: run it,
  # then write the purpose's public set to <KEY_JWKS_DIR>/<purpose>.jwks.json (mode 644)
  local out jwks tmp
  # What a change prints (kids) is not needed: the tool's own line on stderr says what it did
  [[ "$2" == public ]] || kt "$1" "${@:2}" >/dev/null || die "the key tool could not $2 the $1 key (above)"
  out="$(kt "$1" public)" || die "could not read the $1 public keys"
  # The output is never echoed: if it is not the expected public set, nothing is written
  jwks="$(python3 -c "$KEY_PUBLIC_CHECK" "$1" <<< "$out")" \
    || die "the key tool's output for $1 is not a public ES256 key set; nothing written"
  mkdir -p "$KEY_JWKS_DIR"
  tmp="$(mktemp "$KEY_JWKS_DIR/.jwks.XXXXXX")"
  printf '%s\n' "$jwks" > "$tmp"
  chmod 644 "$tmp"
  mv "$tmp" "$KEY_JWKS_DIR/$1.jwks.json"
}

keys_ensure() {  # a key for every holder whose volume is still empty; every public set written
  local p
  for p in "${KEY_PURPOSES[@]}"; do kt_change "$p" ensure; done
}

keys_status() {  # every key: kid, state, created (IST), age; then the verdict on its age
  local p rc=0
  for p in "${KEY_PURPOSES[@]}"; do kt "$p" status || rc=1; done
  return "$rc"
}

keys_age_check() {  # smoke: ok, WARN (150 days or more) or FAIL (365 or more, or no active key)
  local p out verdict message rc=0
  for p in "${KEY_PURPOSES[@]}"; do
    out="$(key_status_json "$p")"
    IFS=$'\t' read -r verdict message < <(python3 -c 'import json,sys
s = json.load(sys.stdin)
print(s["verdict"], s["message"], sep="\t")' <<< "$out" || echo FAIL)
    case "$verdict" in
      ok) echo "ok    client key $p: $message" ;;
      WARN) echo "WARN  client key $p: $message" ;;
      *) echo "FAIL  client key $p: ${message:-status unreadable}"; rc=1 ;;
    esac
  done
  return "$rc"
}

keys_publish() {  # every client in Keycloak gets the public sets now in KEY_JWKS_DIR
  key_publish_sync >/dev/null || die "the sync could not publish the keys (run it again: it changes only what differs)"
}

keys_probe() {  # keys_probe [--negative]: each holder signs in with its active key
  local p svc
  [[ $# -eq 0 || ( $# -eq 1 && "$1" == --negative ) ]] || die "usage: keys probe [--negative]"
  for p in "${KEY_PURPOSES[@]}"; do
    svc="$(key_holder "$p")"
    key_probe_in "$svc" || die "FAIL  $p key: Keycloak refused $svc, or the probe failed (above)"
    echo "ok    $p key: Keycloak accepts $svc"
  done
  # The refusals (a wrong key under a known kid, expired, replayed, a secret, the internal URL
  # as audience, another algorithm) once, in the web app, which signs for most clients
  if [[ "${1:-}" == --negative ]]; then
    key_probe_in backend --negative || die "FAIL  Keycloak accepted an assertion it must refuse (above)"
    echo "ok    every bad assertion refused"
  fi
}

keys_withdraw_retired() {  # keys_withdraw_retired <purpose...>: each retired key refused, then deleted
  local p kid rc kids
  for p in "$@"; do
    kids="$(key_with_state "$p" retired)"
    for kid in $kids; do
      key_kid_ok "$p" "$kid" || die "the key tool gave no valid kid for $p"
      rc=0; key_probe_in "$(key_holder "$p")" --kid "$kid" >/dev/null || rc=$?
      ((rc == 1)) || die "Keycloak does not refuse the retired $p key $kid yet (probe exit $rc); it stays on disk. Check with sync --check, then run keys rotate $p again"
      kt_change "$p" delete --kid "$kid"
      key_log "rotate $p: $kid refused by Keycloak and deleted"
    done
  done
}

keys_unfinished() {  # keys_unfinished <purpose...>: the ones whose last rotation stopped after its switch
  local p
  for p in "$@"; do
    [[ -z "$(key_with_state "$p" previous)$(key_with_state "$p" retired)" ]] || echo "$p"
  done
}

keys_refresh() {  # keys_refresh <purpose...>: their public files rebuilt from the key state.
  # A run interrupted between a key change and writing the file would otherwise publish a stale
  # set (a retired key again, or a staged one missing) on every later run.
  local p
  for p in "$@"; do kt_change "$p" public; done
}

key_marker() { echo "$KEY_JWKS_DIR/$1.compromised"; }

keys_finish() {  # keys_finish <purpose...>: retire their previous keys, publish, withdraw
  local p waited=0
  keys_refresh "$@"
  for p in "$@"; do
    [[ -n "$(key_with_state "$p" previous)" ]] || continue
    if ((!waited)); then
      # The switch may have been seconds ago: assertions signed before it may still arrive
      echo "Waiting $KEY_RETIRE_WAIT s before withdrawing the old keys..."
      sleep "$KEY_RETIRE_WAIT"; waited=1
    fi
    kt_change "$p" retire
    key_log "rotate $p: previous key retired"
  done
  keys_publish
  keys_withdraw_retired "$@"
}

keys_rotate() {  # keys_rotate <backend|onboarding|staff|all> [--compromised]
  local compromised=0 p purposes=() unfinished=()
  local -A old_kid new_kid
  [[ "${2:-}" == --compromised ]] && compromised=1
  [[ -n "${1:-}" && $# -le 2 && ( $# -eq 1 || $compromised == 1 ) ]] \
    || die "usage: keys rotate <backend|onboarding|staff|all> [--compromised]"
  if [[ "$1" == all ]]; then purposes=("${KEY_PURPOSES[@]}"); else key_holder "$1" >/dev/null; purposes=("$1"); fi
  keys_refresh "${purposes[@]}"

  # A compromise rotation that did not finish (publish or session ending failed) is never
  # completed as an ordinary one: that could skip ending the sessions
  if ((!compromised)); then
    for p in "${purposes[@]}"; do
      [[ ! -e "$(key_marker "$p")" ]] \
        || die "the compromise rotation of the $p key has not finished: run keys rotate $p --compromised again"
    done
  fi

  # A run that stopped after its switch is finished first: its new key is already active, so
  # that completes the rotation. (--compromised goes ahead regardless: its switch retires them.)
  mapfile -t unfinished < <(keys_unfinished "${purposes[@]}")
  if ((${#unfinished[@]} && !compromised)); then
    keys_finish "${unfinished[@]}"
    echo "Finished the rotation that had stopped (${unfinished[*]}): their new keys are active. Run again for a new rotation."
    return 0
  fi

  # 1. Stage: a new key next to the active one (one a stopped run left staged is used again;
  # never after a suspected theft: it was in the same folder as the stolen key)
  local -A before
  for p in "${purposes[@]}"; do
    old_kid[$p]="$(key_with_state "$p" active)"
    key_kid_ok "$p" "${old_kid[$p]}" || die "$p has no single active key: run keys ensure first"
    before[$p]=" $(kt "$p" status | awk '{print $1}' | tr '\n' ' ') "
    if ((compromised)); then
      touch "$(key_marker "$p")"
      kt_change "$p" stage --fresh
    else
      kt_change "$p" stage
    fi
    new_kid[$p]="$(key_with_state "$p" staged)"
    key_kid_ok "$p" "${new_kid[$p]}" || die "the key tool gave no single staged kid for $p"
    if ((compromised)) && [[ "${before[$p]}" == *" ${new_kid[$p]} "* ]]; then
      die "the key tool reused $p key ${new_kid[$p]} after a suspected theft; nothing switched"
    fi
    key_log "rotate $p: staged ${new_kid[$p]}"
  done

  if ((compromised)); then
    # Suspected compromise (D4): the old key stops at once. Calls signed in the few seconds
    # until Keycloak has the new set fail, and are retried by people and the workers' queues.
    for p in "${purposes[@]}"; do
      kt_change "$p" activate --kid "${new_kid[$p]}" --at-once
      key_log "rotate $p (compromised): ${new_kid[$p]} active, ${old_kid[$p]} retired"
    done
    if [[ " ${purposes[*]} " == *" backend "* ]]; then
      # Stolen refresh tokens must die with the key: every staff member and operator signs in
      # again. First, before publishing: a sync that stops on some other problem must never
      # leave the sessions alive.
      key_revoke_sessions || die "could not end the sessions: run keys rotate backend --compromised again (or keys revoke-sessions)"
      key_log "rotate backend (compromised): every staff and operator session ended"
    fi
    keys_publish
  else
    # 2. Publish {old, new}; 3. verify before anything signs with the new key
    keys_publish
    key_check_sync >/dev/null || die "Keycloak does not match the keys (sync --check, above). Nothing switched; the new keys stay staged (harmless). Run keys rotate again"
    for p in "${purposes[@]}"; do
      key_probe_in "$(key_holder "$p")" --kid "${new_kid[$p]}" >/dev/null \
        || die "Keycloak refused $p's new key ${new_kid[$p]}. Nothing switched; it stays staged (harmless). Run keys rotate again"
    done
    # 4. Switch: the signers read the key state on every call, so nothing restarts. The old
    # key stays published ("previous") for assertions already signed with it.
    for p in "${purposes[@]}"; do
      kt_change "$p" activate --kid "${new_kid[$p]}"
      key_log "rotate $p: ${new_kid[$p]} active"
    done
    # 5. Retire once no assertion signed with the old key can still arrive
    echo "Waiting $KEY_RETIRE_WAIT s for assertions signed with the old keys to expire..."
    sleep "$KEY_RETIRE_WAIT"
    for p in "${purposes[@]}"; do
      kt_change "$p" retire --kid "${old_kid[$p]}"
      key_log "rotate $p: ${old_kid[$p]} retired"
    done
    keys_publish
  fi

  key_check_sync >/dev/null || die "Keycloak does not match the keys after the switch (sync --check, above)"
  for p in "${purposes[@]}"; do
    key_probe_in "$(key_holder "$p")" --kid "${new_kid[$p]}" >/dev/null || die "Keycloak refuses $p's new key ${new_kid[$p]}"
  done
  # 6. The old key: refused by Keycloak, then deleted
  keys_withdraw_retired "${purposes[@]}"
  for p in "${purposes[@]}"; do
    rm -f "$(key_marker "$p")"
    echo "rotated $p: ${old_kid[$p]} -> ${new_kid[$p]}"
  done
}

keys_cmd() {  # keys <ensure|status|rotate <purpose|all> [--compromised]|probe [--negative]>
  case "${1:-}" in
    ensure) keys_ensure ;;
    status) keys_status ;;
    rotate) keys_rotate "${@:2}" ;;
    probe) keys_probe "${@:2}" ;;
    # On its own too: ends every staff and operator session in both realms (suspected theft)
    revoke-sessions)
      key_revoke_sessions || die "could not end the sessions (above)"
      key_log "every staff and operator session ended (keys revoke-sessions)" ;;
    *) die "usage: keys <ensure | status | rotate <backend|onboarding|staff|all> [--compromised] | probe [--negative] | revoke-sessions>" ;;
  esac
}
