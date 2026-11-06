#!/usr/bin/env python3
"""Keycloak section of deploy/seed/TEST_USERS.md (P1-T3), used by scripts/care-dev.sh.

    keycloak_test_users.py TEST_USERS.md < demo-users.json   # replace the Keycloak section
    keycloak_test_users.py --otp dr.meera TEST_USERS.md      # print the user's current code
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "plugins" / "ihr_platform"))

from ihr_platform.keycloak import totp  # noqa: E402

START, END = "<!-- keycloak:start -->", "<!-- keycloak:end -->"


def read_env(path):
    env = {}
    for line in path.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            env[key] = value
    return env


def section(data):
    env = read_env(ROOT / "deploy" / "dev" / "keycloak.env")
    lines = [
        START,
        "## Keycloak (P1-T3) — staff sign-in",
        "",
        "One login per person; membership decides which hospitals they can enter. Every user",
        "needs a password **and** a 6-digit code. Get the current code with",
        "`scripts/care-dev.sh keycloak otp <username>`, or add the key to an authenticator app.",
        "",
        "- Token viewer: http://alpha.localhost:8082/ and http://beta.localhost:8082/ "
        "(start it with `scripts/care-dev.sh keycloak devtools`)",
        f"- Keycloak admin console: {data['keycloak']}/admin/ (realm `{data['realm']}`)"
        f" — temporary admin `{env.get('KC_BOOTSTRAP_ADMIN_USERNAME', '?')}`,"
        f" password `{env.get('KC_BOOTSTRAP_ADMIN_PASSWORD', '?')}`",
        "",
        "| Username | Name | Signs in as (hospital: CARE user) | Password | Authenticator key |",
        "|---|---|---|---|---|",
    ]
    for u in data["users"]:
        if u["otp_secret"]:
            key = f"`{totp.app_key(u['otp_secret'])}` <!-- raw:{u['otp_secret']} -->"
        else:
            key = "none yet: set up at first sign-in"
        lines.append(
            f"| {u['username']} | {u['name']} |"
            f" {', '.join(f'{h}: {c}' for h, c in u.get('care_users', {}).items())} |"
            f" `{u['password']}` | {key} |"
        )
    lines += ["", END]
    return "\n".join(lines)


def main():
    if sys.argv[1] == "--otp":
        user, path = sys.argv[2], Path(sys.argv[3])
        for line in path.read_text().splitlines():
            if line.startswith(f"| {user} |"):
                match = re.search(r"raw:([0-9a-f]+)", line)
                if match:
                    print(totp.code(match.group(1)))
                    return
                sys.exit(f"{user} has no pre-set authenticator: use your app.")
        sys.exit(f"No Keycloak user {user!r} in {path}; run `care-dev.sh keycloak seed`.")
    path = Path(sys.argv[1])
    data = json.loads(sys.stdin.read())
    text = path.read_text() if path.exists() else ""
    text = re.sub(re.escape(START) + ".*?" + re.escape(END), "", text, flags=re.DOTALL).rstrip()
    path.write_text(f"{text}\n\n{section(data)}\n")
    path.chmod(0o600)  # holds passwords and authenticator keys


if __name__ == "__main__":
    main()
