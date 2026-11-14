"""Erasure of archived log days after 400 days (P1-T9c, ADR-014; DPDP Rules 2025, rule 8(3);
owner decision Q-O12). Run monthly from the owner's machine (scripts/staging.sh logs-archive-expire),
never on the server: it needs the bucket's admin key, which can lift the Governance lock.

Safety:
- never fewer than 400 days (--allow-short only in a proof on the local stand-in: refused unless
  IHR_LOGS_ARCHIVE_ENDPOINT is the stand-in's http://s3:8333, and never abbreviated; P1-T22a
  review S3);
- the machine's clock is compared with the bucket's before anything is deleted;
- a version whose own lock still runs for more than a week is not touched (that is not a day
  past its 400 days, whatever its name says);
- the erasure record is written before the first deletion: the days, the objects, and each
  erased manifest's SHA-256 with the manifest before it, so `archive.py check` follows the chain
  through them. It is authenticated with a key derived from the archive key (given on stdin; this
  script never sees the archive key itself). A second record says how it ended.

    expire.py [--older-than-days 400] [--dry-run] [--allow-short]
stdin, three lines: the admin access key, its secret, the erasure-record key (hex). The bucket's
address from IHR_LOGS_ARCHIVE_ENDPOINT, _REGION, _BUCKET, _URI_STYLE.
"""

import argparse
import base64
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
    for path in ("/opt/ihr-lib", os.path.normpath(os.path.join(here, "..", "..", "lib"))):
        if os.path.isfile(os.path.join(path, "sealer.py")):
            return path
    sys.exit("the shared sealer (deploy/lib/sealer.py) is missing")


sys.path.insert(0, _shared_lib())
from sealer import signed  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
FORMAT = "ihr-log-archive-erasure/2"
MIN_DAYS = 400
STAND_IN = "http://s3:8333"  # a proof's own SeaweedFS (scripts/staging.sh logs-archive-expire, IHR_LOGS_ARCHIVE=local)
DAY_RE = re.compile(r"^logs/(?:\d{4}/\d{2}/|ihr-key-check/)(\d{4}-\d{2}-\d{2})[.T]")


def day_of(key):
    """The India day a key under logs/ belongs to (archive files, and probe files named by their
    date), or None."""
    m = DAY_RE.match(key)
    if not m:
        return None
    try:
        return dt.date.fromisoformat(m.group(1))
    except ValueError:
        return None


def sign(mac_key, doc):
    return signed(mac_key, doc)


def put_record(s3, bucket, key, doc):
    body = (json.dumps(doc, indent=1, sort_keys=True) + "\n").encode()
    md5 = base64.b64encode(hashlib.md5(body).digest()).decode()  # noqa: S324 (S3 integrity header)
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json", ContentMD5=md5)


def erase(s3, bucket, mac_key, now, older_than_days, allow_short=False, dry_run=False, out=sys.stdout):
    """Erases every version of each day older than the cut-off; returns (days, failed)."""
    if older_than_days < MIN_DAYS and not allow_short:
        raise SystemExit(f"refused: fewer than {MIN_DAYS} days would erase logs still inside the record "
                         f"(CERT-In 180 days, DPDP one year)")
    today = now.astimezone(IST).date()
    cutoff = today - dt.timedelta(days=older_than_days)
    doomed, skipped = [], []
    for page in s3.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix="logs/"):
        for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
            d = day_of(item["Key"])
            if d is not None and d < cutoff:
                doomed.append((item["Key"], item["VersionId"], d, "LastModified" in item and "Size" in item))
    if not allow_short:
        # A version still locked for more than a week is not a day past its 400 days
        keep = []
        for k, v, d, is_version in doomed:
            if not is_version:
                keep.append((k, v, d, is_version))
                continue
            until = s3.head_object(Bucket=bucket, Key=k, VersionId=v).get("ObjectLockRetainUntilDate")
            if until and until > now + dt.timedelta(days=7):
                skipped.append(f"{k} ({v}): locked until {until:%Y-%m-%d}")
            else:
                keep.append((k, v, d, is_version))
        doomed = keep
    erased = []
    for k, v, d, is_version in doomed:
        if is_version and k.endswith(".manifest.json"):
            raw = s3.get_object(Bucket=bucket, Key=k, VersionId=v)["Body"].read()
            try:
                prev = json.loads(raw).get("previous")
            except (ValueError, AttributeError):
                prev = None
            erased.append({"sha256": hashlib.sha256(raw).hexdigest(), "previous": prev, "key": k, "day": d.isoformat()})
    days = sorted({d for _, _, d, _ in doomed})
    print(f"cut-off {cutoff} ({older_than_days} days before {today}): {len(days)} day(s), "
          f"{len(doomed)} object version(s)", file=out)
    for d in days:
        print(f"  {d}", file=out)
    for s in skipped:
        print(f"SKIPPED {s}", file=out)
    if dry_run or not doomed:
        print("dry run: nothing deleted" if dry_run else "nothing to erase", file=out)
        return days, []
    stamp = now.astimezone(IST).strftime("%Y-%m-%dT%H%M%S")
    record = {
        "format": FORMAT,
        "status": "started",
        "erased_at": now.astimezone(IST).isoformat(),
        "cutoff": cutoff.isoformat(),
        "older_than_days": older_than_days,
        "allow_short": allow_short,
        "days": [d.isoformat() for d in days],
        "objects": sorted({k for k, _, _, _ in doomed}),
        "erased": erased,
        "reason": "DPDP Rules 2025 rule 8(3): logs erased after the retention period (400 days; owner, Q-O3, Q-O12)",
    }
    # First the record (a failure here stops before anything is deleted)
    put_record(s3, bucket, f"erasure/{stamp}.json", sign(mac_key, record))
    failed = []
    for k, v, _, _ in doomed:
        try:
            s3.delete_object(Bucket=bucket, Key=k, VersionId=v, BypassGovernanceRetention=True)
        except Exception as exc:  # noqa: BLE001 (reported below and in the closing record)
            failed.append(f"{k} ({v}): {exc}")
    try:
        put_record(s3, bucket, f"erasure/{stamp}-done.json",
                   sign(mac_key, {**record, "status": "completed", "failed": failed}))
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: the closing record could not be written ({exc}); the first one stands", file=out)
    print(f"erased {len(doomed) - len(failed)} object version(s); record erasure/{stamp}.json", file=out)
    for f in failed:
        print(f"NOT ERASED {f}", file=out)
    return days, failed


def main():
    # No abbreviations: `--allow` is not --allow-short (P1-T22a review S3)
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0], allow_abbrev=False)
    p.add_argument("--older-than-days", type=int, default=MIN_DAYS)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-short", action="store_true", help="a proof on the local stand-in only")
    a = p.parse_args()
    if a.allow_short and os.environ.get("IHR_LOGS_ARCHIVE_ENDPOINT") != STAND_IN:
        sys.exit(f"refused: --allow-short is for a proof on the local stand-in ({STAND_IN}) only")
    if a.older_than_days < 0:
        sys.exit("--older-than-days must not be negative")
    if a.older_than_days < MIN_DAYS and not a.allow_short:
        sys.exit(f"refused: fewer than {MIN_DAYS} days would erase logs still inside the record "
                 f"(CERT-In 180 days, DPDP one year)")
    key = sys.stdin.readline().strip()
    secret = sys.stdin.readline().strip()
    mac_hex = sys.stdin.readline().strip()
    if not key or not secret or not re.fullmatch(r"[0-9a-f]{64}", mac_hex):
        sys.exit("needed on stdin: the bucket's admin key, its secret, and the erasure-record key")

    import boto3
    from botocore.config import Config

    s3 = boto3.client(
        "s3",
        endpoint_url=os.environ.get("IHR_LOGS_ARCHIVE_ENDPOINT") or None,
        region_name=os.environ.get("IHR_LOGS_ARCHIVE_REGION", "ap-south-1"),
        aws_access_key_id=key,
        aws_secret_access_key=secret,
        config=Config(signature_version="s3v4",
                      s3={"addressing_style": os.environ.get("IHR_LOGS_ARCHIVE_URI_STYLE", "path")}),
    )
    bucket = os.environ["IHR_LOGS_ARCHIVE_BUCKET"]
    # This machine's clock against the bucket's: a wrong date would erase the wrong days
    head = s3.head_bucket(Bucket=bucket)
    their = email.utils.parsedate_to_datetime(head["ResponseMetadata"]["HTTPHeaders"]["date"])
    now = dt.datetime.now(dt.UTC)
    if abs((now - their).total_seconds()) > 3600:
        sys.exit(f"refused: this machine's clock ({now:%Y-%m-%d %H:%M} UTC) is more than an hour off the bucket's "
                 f"({their:%Y-%m-%d %H:%M} UTC)")
    _, failed = erase(s3, bucket, bytes.fromhex(mac_hex), now, a.older_than_days, a.allow_short, a.dry_run)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
