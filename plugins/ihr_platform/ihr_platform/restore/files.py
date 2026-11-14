"""A restored hospital's files, from the buckets' versions (P1-T22b; design P1-T22b item 2).

Both file buckets keep every version (patient files: ``FILE_UPLOAD_*``; pictures:
``FACILITY_S3_*``; versioning on, old versions expire after 35 days: infra/aws/storage.tf,
deploy/s3/setup_buckets.py). After a restore, every object under the hospital's own prefix
(``t/<hospital uuid>/``, ihr_platform/storage.py) is brought back as it was at the dump's time:

- unchanged since the dump: left alone;
- replaced or deleted after the dump: the version current at the dump's time is copied back
  over it (a new version; the newer ones stay in the history);
- first written after the dump: kept, and counted (the restored rows do not point at it; the
  operator decides; the parked schema still does, for 7 days).

Listing versions needs a key the app's per-bucket keys may not hold (on AWS they have no
s3:ListBucket): ``IHR_RESTORE_S3_ACCESS_KEY`` / ``_SECRET`` (the buckets' admin key, given by
scripts/staging.sh for this run only) is used when set, else the bucket's own key. A bucket that
is not versioned, or that cannot be listed, fails the step (closed): the hospital stays held.
"""

import os
from datetime import UTC

from django.conf import settings

BUCKETS = (("files", "FILE_UPLOAD"), ("pictures", "FACILITY_S3"))


class FilesError(RuntimeError):
    pass


def _client(prefix):
    import boto3
    from botocore.config import Config

    key = os.environ.get("IHR_RESTORE_S3_ACCESS_KEY") or getattr(
        settings, f"{prefix}_KEY", ""
    )
    secret = os.environ.get("IHR_RESTORE_S3_SECRET") or getattr(
        settings, f"{prefix}_SECRET", ""
    )
    region = getattr(settings, f"{prefix}_REGION", "") or getattr(
        settings, "BUCKET_REGION", ""
    )
    return boto3.client(
        "s3",
        endpoint_url=getattr(settings, f"{prefix}_BUCKET_ENDPOINT", "") or None,
        region_name=region or "ap-south-1",
        aws_access_key_id=key or None,
        aws_secret_access_key=secret or None,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3},
        ),
    )


CLIENT = {"factory": _client}  # tests put their own in


def _plan(versions, dump_at):
    """For one key's versions [(when, version id or None for a delete marker, latest?)]: what
    to do. ("keep" | "new" | "restore", version id to copy back)."""
    ordered = sorted(versions, key=lambda v: v[0])
    at_dump = [v for v in ordered if v[0] <= dump_at]
    latest = next((v for v in ordered if v[2]), ordered[-1])
    if not at_dump:
        return "new", None
    then = at_dump[-1]
    if then[1] is None:  # deleted before the dump: anything now is newer
        return ("new", None) if latest[1] is not None else ("keep", None)
    if latest is then:
        return "keep", None
    return "restore", then[1]


def restore(hospital, dump_at, dry_run=False):  # noqa: PLR0912 (one pass per bucket)
    """Bring both buckets' objects of ``hospital`` back to ``dump_at``. Returns the counts."""
    dump_at = dump_at.astimezone(UTC)
    prefix = f"t/{hospital.uuid}/"
    out = {}
    for name, setting in BUCKETS:
        bucket = getattr(settings, f"{setting}_BUCKET", "")
        if not bucket:
            msg = f"{setting}_BUCKET is not set: the {name} bucket cannot be restored"
            raise FilesError(msg)
        client = CLIENT["factory"](setting)
        try:
            if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
                msg = f"the {name} bucket keeps no versions: its files cannot be brought back"
                raise FilesError(msg)
            keys = {}
            pages = client.get_paginator("list_object_versions").paginate(
                Bucket=bucket, Prefix=prefix
            )
            for page in pages:
                for item in page.get("Versions", []):
                    keys.setdefault(item["Key"], []).append(
                        (
                            item["LastModified"],
                            item["VersionId"],
                            item.get("IsLatest", False),
                        )
                    )
                for item in page.get("DeleteMarkers", []):
                    keys.setdefault(item["Key"], []).append(
                        (item["LastModified"], None, item.get("IsLatest", False))
                    )
        except FilesError:
            raise
        except Exception as exc:
            msg = f"the {name} bucket could not be read: {type(exc).__name__}"
            raise FilesError(msg) from None
        counts = {"objects": len(keys), "unchanged": 0, "restored": 0, "newer_kept": 0}
        for key, versions in sorted(keys.items()):
            action, version = _plan(versions, dump_at)
            if action == "keep":
                counts["unchanged"] += 1
            elif action == "new":
                counts["newer_kept"] += 1
            else:
                if not dry_run:
                    try:
                        client.copy_object(
                            Bucket=bucket,
                            Key=key,
                            CopySource={
                                "Bucket": bucket,
                                "Key": key,
                                "VersionId": version,
                            },
                        )
                    except Exception as exc:
                        msg = f"the {name} bucket: an object could not be brought back ({type(exc).__name__})"
                        raise FilesError(msg) from None
                counts["restored"] += 1
        out[name] = counts
    return out
