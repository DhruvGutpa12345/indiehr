"""Erasure of nightly dumps past their retention (P1-T22a; docs/reviews/p1-t22/design.md, "After
the attack" item 1; owner decision Q-K33 pending). Run from the owner's machine (scripts/staging.sh
dumps-expire), never on the server: it needs the dumps bucket's admin key, which can lift the
Governance lock (30 days). Runbook: deploy/staging/README.md, "Hospital dumps".

Retention: every night's dump 14 days (dumps/daily/), each Sunday's 28 days (dumps/weekly/), as
pgBackRest's 28 days; the key-check objects of `dumper.py probe` 30 days.

Safety:
- never under the retention (--allow-short only in a proof on the local stand-in: refused unless
  IHR_DUMP_ENDPOINT is the stand-in's http://s3:8333; review S3);
- a dump name with more than one version, or a delete marker, is reported SUSPECT and left alone:
  the dumper writes each name once, so a second version is a replacement, and erasing by age
  would remove the original first (review S1). Look at it (dumps-check names it), then
  --erase-suspect erases such names too once past their retention;
- an object's age is the younger of its name's date and the bucket's own LastModified, so a name
  that claims to be older than the object is never erased early;
- the machine's clock is compared with the bucket's before anything is deleted;
- the erasure record is written before the first deletion: the objects, and each erased
  manifest's SHA-256 with the manifest before it and its hospital, so `dumper.py check` follows
  every hospital's chain through them. It is authenticated with a key derived from the dump key
  (given on stdin; this script never sees the dump key itself). A second record says how it ended.

    expire.py [--daily-days 14] [--weekly-days 28] [--dry-run] [--erase-suspect] [--allow-short]
stdin, three lines: the admin access key, its secret, the erasure-record key (hex). The bucket's
address from IHR_DUMP_ENDPOINT, _REGION, _BUCKET, _URI_STYLE.
"""

import argparse
import collections
import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import sys


def _shared_lib():
    """The shared modules (deploy/lib): /opt/ihr-lib in a container, else beside this folder."""
    here = os.path.dirname(os.path.abspath(__file__))
    for path in ("/opt/ihr-lib", os.path.normpath(os.path.join(here, "..", "lib"))):
        if os.path.isfile(os.path.join(path, "sealer.py")):
            return path
    sys.exit("the shared sealer (deploy/lib/sealer.py) is missing")


sys.path.insert(0, _shared_lib())
from sealer import record_bytes, signed  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FORMAT = "ihr-dump-erasure/1"
STAND_IN = "http://s3:8333"  # a proof's own SeaweedFS (scripts/staging.sh dumps-expire, IHR_DUMPS=local)
MIN_DAYS = {"daily": 14, "weekly": 28, "probe": 30}
# The dumper's own names (dumper.py KEY_RE): anything else under dumps/ is left alone, and so
# never named in an erasure record the dumper could not date (review S2)
NAME_RE = re.compile(r"^dumps/(?:(daily|weekly)/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
                     r"(\d{4}-\d{2}-\d{2})\.r[1-9]\d{0,3}\.(?:dump\.enc|public\.enc|manifest\.json)"
                     r"|(ihr-key-check)/(\d{4}-\d{2}-\d{2})T\d{6}\.probe)$")


def kind_and_day(key):
    """("daily"|"weekly"|"probe", the date in its name) of a key under dumps/, or None."""
    m = NAME_RE.match(key)
    if not m:
        return None
    kind = m.group(1) or "probe"
    try:
        return kind, dt.date.fromisoformat(m.group(2) or m.group(4))
    except ValueError:
        return None


def put_record(s3, bucket, key, doc):
    import base64

    body = record_bytes(doc)
    md5 = base64.b64encode(hashlib.md5(body).digest()).decode()  # noqa: S324 (S3 integrity header)
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json", ContentMD5=md5)


def erase(s3, bucket, mac_key, now, days, allow_short=False, dry_run=False, out=sys.stdout, erase_suspect=False):
    """Erases every version of each object past its retention; returns (objects, failed). A dump
    name with more than one version or a delete marker is left alone (SUSPECT) unless
    erase_suspect."""
    for kind, least in MIN_DAYS.items():
        if days[kind] < least and not allow_short:
            raise SystemExit(f"refused: {kind} dumps are kept at least {least} days (owner, Q-K33)")
    today = now.astimezone(IST).date()
    doomed, unknown = [], []
    items = collections.defaultdict(list)
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix="dumps/"):
        for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
            items[item["Key"]].append(item)
    suspect = sorted(k for k, vs in items.items() if (kind_and_day(k) or ("",))[0] in ("daily", "weekly")
                     and (len(vs) > 1 or any("Size" not in v for v in vs)))
    for key, vs in sorted(items.items()):
        p = kind_and_day(key)
        if p is None:
            unknown.append(key)
            continue
        if key in suspect and not erase_suspect:
            continue
        for item in vs:
            kind, named = p
            modified = item.get("LastModified")
            seen = modified.astimezone(IST).date() if isinstance(modified, dt.datetime) else named
            # The younger of the two: a name never makes an object older than it is
            age = (today - max(named, seen)).days
            if age > days[kind]:
                doomed.append((item["Key"], item["VersionId"], kind, max(named, seen), "Size" in item))
    erased = []
    for k, v, _kind, _day, is_version in doomed:
        if is_version and k.endswith(".manifest.json"):
            raw = s3.get_object(Bucket=bucket, Key=k, VersionId=v)["Body"].read()
            try:
                doc = json.loads(raw)
                prev, hospital = doc.get("previous"), doc.get("hospital")
            except (ValueError, AttributeError):
                prev = hospital = None
            erased.append({"sha256": hashlib.sha256(raw).hexdigest(), "previous": prev, "hospital": hospital, "key": k})
    print(f"{today}: {len(doomed)} object version(s) past retention "
          f"(nightly {days['daily']} days, Sundays {days['weekly']} days)", file=out)
    for k in sorted({d[0] for d in doomed}):
        print(f"  {k}", file=out)
    for k in sorted(set(unknown)):
        print(f"NOT OURS (left alone) {k}", file=out)
    for k in suspect:
        print(f"SUSPECT {k}: {len(items[k])} versions or delete markers; the dumper writes a name once "
              + ("(erased with the rest: --erase-suspect)" if erase_suspect
                 else "(left alone: look at it with dumps-check, then --erase-suspect)"), file=out)
    if dry_run or not doomed:
        print("dry run: nothing deleted" if dry_run else "nothing to erase", file=out)
        return doomed, []
    stamp = now.astimezone(IST).strftime("%Y-%m-%dT%H%M%S")
    record = {
        "format": FORMAT,
        "status": "started",
        "erased_at": now.astimezone(IST).isoformat(),
        "days": days,
        "allow_short": allow_short,
        "objects": sorted({k for k, _, _, _, _ in doomed}),
        "erased": erased,
        "reason": "nightly dumps past their retention (14 nights, 4 Sundays; owner, Q-K33)",
    }
    # First the record (a failure here stops before anything is deleted)
    put_record(s3, bucket, f"erasure/{stamp}.json", signed(mac_key, record))
    failed = []
    for k, v, _, _, _ in doomed:
        try:
            s3.delete_object(Bucket=bucket, Key=k, VersionId=v, BypassGovernanceRetention=True)
        except Exception as exc:  # noqa: BLE001 (reported below and in the closing record)
            failed.append(f"{k} ({v}): {exc}")
    try:
        put_record(s3, bucket, f"erasure/{stamp}-done.json",
                   signed(mac_key, {**record, "status": "completed", "failed": failed}))
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: the closing record could not be written ({exc}); the first one stands", file=out)
    print(f"erased {len(doomed) - len(failed)} object version(s); record erasure/{stamp}.json", file=out)
    for f in failed:
        print(f"NOT ERASED {f}", file=out)
    return doomed, failed


def main():
    # No abbreviations: `--allow` is not --allow-short (review S3)
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0], allow_abbrev=False)
    p.add_argument("--daily-days", type=int, default=MIN_DAYS["daily"])
    p.add_argument("--weekly-days", type=int, default=MIN_DAYS["weekly"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--erase-suspect", action="store_true",
                   help="also erase dump names with more than one version, once you have looked at them")
    p.add_argument("--allow-short", action="store_true", help="a proof on the local stand-in only")
    a = p.parse_args()
    if a.allow_short and os.environ.get("IHR_DUMP_ENDPOINT") != STAND_IN:
        sys.exit(f"refused: --allow-short is for a proof on the local stand-in ({STAND_IN}) only")
    days = {"daily": a.daily_days, "weekly": a.weekly_days, "probe": MIN_DAYS["probe"]}
    if a.allow_short:
        days["probe"] = min(days["probe"], a.daily_days)
    if min(days.values()) < 0:
        sys.exit("the periods must not be negative")
    for kind, least in MIN_DAYS.items():
        if days[kind] < least and not a.allow_short:
            sys.exit(f"refused: {kind} dumps are kept at least {least} days (owner, Q-K33)")
    key = sys.stdin.readline().strip()
    secret = sys.stdin.readline().strip()
    mac_hex = sys.stdin.readline().strip()
    if not key or not secret or not re.fullmatch(r"[0-9a-f]{64}", mac_hex):
        sys.exit("needed on stdin: the bucket's admin key, its secret, and the erasure-record key")

    import boto3
    from botocore.config import Config

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ.get("IHR_DUMP_ENDPOINT") or None,
        region_name=os.environ.get("IHR_DUMP_REGION", "ap-south-1"),
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4",
                      s3={"addressing_style": os.environ.get("IHR_DUMP_URI_STYLE", "path")}),
    )
    bucket = os.environ["IHR_DUMP_BUCKET"]
    # This machine's clock against the bucket's: a wrong date would erase the wrong dumps
    head = s3.head_bucket(Bucket=bucket)
    their = email.utils.parsedate_to_datetime(head["ResponseMetadata"]["HTTPHeaders"]["date"])
    now = dt.datetime.now(dt.UTC)
    if abs((now - their).total_seconds()) > 3600:
        sys.exit(f"refused: this machine's clock ({now:%Y-%m-%d %H:%M} UTC) is more than an hour off the bucket's "
                 f"({their:%Y-%m-%d %H:%M} UTC)")
    _, failed = erase(s3, bucket, bytes.fromhex(mac_hex), now, days, a.allow_short, a.dry_run,
                      erase_suspect=a.erase_suspect)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
