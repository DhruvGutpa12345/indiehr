#!/usr/bin/env python3
"""Is this server the instance the folder is? (P1-T32 E3, ADR-020 §3; design
docs/reviews/p1-t32/design.md §5.2.) Run by scripts/deploy.sh on the deploying machine, before
anything is written to or sent to the server.

    python3 -I scripts/instance_check.py <environment> <suffix> <file>

<environment> and <suffix> are the folder's (staging.sh init --check). <file> is deploy.sh's one
remote read of the server: a header per source (``#instance``, ``#deployed``, ``#env``, each
``missing`` or ``present <owner> <mode>``), the source's lines prefixed with ``|``, and ``#end``.

Exit 0: ok. Exit 1: refused, the reason on stdout. Exit 2: a wrong command line.

Rules (no "new server ok", no override; a server changes instance only by being rebuilt):
- no ``#end``, or anything unexpected in the answer: "cannot read the server";
- ``/srv/indiehr/INSTANCE`` (deploy/host/bootstrap.sh --instance) must exist, be root's and not
  writable by group or others; a deploy-user INSTANCE counts only when it says ``proof`` (the
  throwaway VM, written by ``deploy.sh --local-proof``);
- every source present (INSTANCE, DEPLOYED, the server .env) must name the folder's environment
  and suffix. A DEPLOYED or .env from before E3/E1 may lack the environment (INSTANCE then
  decides) but never the suffix; an environment without a suffix, an empty DEPLOYED and two
  different values in one source are refused.
"""

import re
import sys

# One regex, equal to plugins/ihr_platform/ihr_platform/environment.py SERVER_PATTERN
# (scripts/test_environment_regex.py). Matched with fullmatch: a trailing newline never passes.
SERVER_PATTERN = r"^(production-[1-9][0-9]{0,2}|staging|proof)$"
# staging.sh DOMAIN_RE
DOMAIN_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
SECTIONS = ("instance", "deployed", "env")
# The keys each source states its environment and suffix with
KEYS = {
    "instance": ("environment", "suffix"),
    "deployed": ("environment", "suffix"),
    "env": ("IHR_ENVIRONMENT", "IHR_TENANT_DOMAIN_SUFFIX"),
}
LABEL = {"instance": "INSTANCE", "deployed": "DEPLOYED", "env": "the server .env"}
INSTANCE_PATH = "/srv/indiehr/INSTANCE"
RULE = "an instance is never deployed over another (ADR-020 §3 E3)"


class Unreadable(Exception):
    pass


class Refused(Exception):
    pass


def valid_env(value):
    return re.fullmatch(SERVER_PATTERN, value) is not None


def valid_suffix(value):
    return re.fullmatch(DOMAIN_PATTERN, value) is not None


def shown(value):
    """A value as it may be printed: plain ones as they are, anything odd quoted (no raw \\r)."""
    if value is None:
        return "unknown"
    if re.fullmatch(r"[A-Za-z0-9._-]+", value):
        return value
    return ascii(value)


def parse(text):
    """{section: None (missing) or (owner, mode, [lines])}; Unreadable if the read did not finish."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines or lines[-1] != "#end":
        raise Unreadable("the read of the server did not finish (no #end)")
    sections, current = {}, None
    for line in lines[:-1]:
        if line.startswith("|"):
            if current is None:
                raise Unreadable("a line outside a present source")
            current[2].append(line[1:])
            continue
        if not line.startswith("#"):
            raise Unreadable(f"an unexpected line in the answer: {shown(line)}")
        parts = line[1:].split(" ")
        name = parts[0]
        if name not in SECTIONS or name in sections:
            raise Unreadable(f"an unexpected header in the answer: {shown(line)}")
        if parts[1:] == ["missing"]:
            sections[name], current = None, None
        elif len(parts) == 4 and parts[1] == "present" and parts[2] and re.fullmatch(r"[0-7]{3,4}", parts[3]):
            sections[name] = current = (parts[2], parts[3], [])
        else:
            raise Unreadable(f"an unexpected header in the answer: {shown(line)}")
    if set(sections) != set(SECTIONS):
        raise Unreadable("a source is missing from the answer")
    return sections


def stated(name, lines):
    """(environment or None, suffix or None) as the source states them; Refused on two values."""
    env_key, suffix_key = KEYS[name]
    found = {env_key: None, suffix_key: None}
    for line in lines:
        if name == "instance" and (not line.strip() or line.startswith("#")):
            continue
        key, sep, value = line.partition("=")
        if key in found:
            if found[key] is not None and found[key] != value:
                raise Refused(f"{LABEL[name]} on this server names two different {key} values")
            found[key] = value
        elif name == "instance":
            raise Refused(f"{INSTANCE_PATH} on this server has an unknown line ({shown(line)}): "
                          "only deploy/host/bootstrap.sh --instance writes it")
    return found[env_key], found[suffix_key]


def compare(folder_env, folder_suffix, sections):
    """None when the server is the folder's instance; otherwise Refused with the reason."""
    folder = f"the folder is {folder_env} at {folder_suffix}"
    inst = sections["instance"]
    if inst is None:
        raise Refused(f"this server has no {INSTANCE_PATH}: run deploy/host/bootstrap.sh --instance "
                      f"<its environment> --suffix <its suffix> on it as root first; {folder}. "
                      f"Nothing was sent ({RULE})")
    owner, mode, lines = inst
    inst_env, inst_suffix = stated("instance", lines)
    if inst_env is None or inst_suffix is None:
        raise Refused(f"{INSTANCE_PATH} on this server names {shown(inst_env)} at {shown(inst_suffix)}: "
                      "it needs both an environment and a suffix; re-run deploy/host/bootstrap.sh "
                      f"--instance <environment> --suffix <suffix> as root; {folder} ({RULE})")
    if not valid_env(inst_env) or not valid_suffix(inst_suffix):
        raise Refused(f"{INSTANCE_PATH} on this server names {shown(inst_env)} at {shown(inst_suffix)}, "
                      f"not a valid environment and suffix; {folder} ({RULE})")
    roots = owner == "root" and int(mode, 8) & 0o022 == 0
    if not roots and inst_env != "proof":
        raise Refused(f"{INSTANCE_PATH} on this server is not root's (owner {shown(owner)}, mode {mode}): "
                      "only a proof's may be the deploy user's; re-run deploy/host/bootstrap.sh "
                      f"--instance <environment> --suffix <suffix> as root; {folder} ({RULE})")
    statements = [("instance", inst_env, inst_suffix)]
    for name in ("deployed", "env"):
        source = sections[name]
        if source is None:
            continue
        lines = [line for line in source[2] if line.strip()]
        if name == "deployed" and not lines:
            raise Refused(f"DEPLOYED on this server is empty: what it runs cannot be told; {folder} ({RULE})")
        env, suffix = stated(name, lines)
        if suffix is None:
            what = "an environment without a suffix" if env is not None else "no suffix"
            raise Refused(f"{LABEL[name]} on this server names {what} ({shown(env)}); {folder} ({RULE})")
        statements.append((name, env, suffix))
    wrong = [(name, env, suffix) for name, env, suffix in statements
             if suffix != folder_suffix or (env is not None and env != folder_env)]
    if wrong:
        name, env, suffix = wrong[0]
        sources = "; ".join(f"{LABEL[n]} says {shown(e)} at {shown(s)}" for n, e, s in statements)
        raise Refused(f"this server runs {shown(env)} at {shown(suffix)} ({LABEL[name]}); {folder}: "
                      f"{RULE}. What the server says: {sources}")


def main(argv):
    if len(argv) != 4:
        print("usage: instance_check.py <environment> <suffix> <file>", file=sys.stderr)
        return 2
    folder_env, folder_suffix, path = argv[1:]
    if not valid_env(folder_env) or not valid_suffix(folder_suffix):
        print(f"instance_check.py: not a valid environment and suffix: {shown(folder_env)} {shown(folder_suffix)}",
              file=sys.stderr)
        return 2
    try:
        try:
            with open(path, encoding="utf-8", errors="replace", newline="") as f:  # a \r stays in the value
                text = f.read()
        except OSError as e:
            raise Unreadable(f"the answer could not be read ({e.strerror})") from None
        compare(folder_env, folder_suffix, parse(text))
    except Unreadable as e:
        print(f"cannot read the server: {e}; nothing was sent ({RULE})")
        return 1
    except Refused as e:
        print(str(e))
        return 1
    print(f"ok: this server is {folder_env} at {folder_suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
