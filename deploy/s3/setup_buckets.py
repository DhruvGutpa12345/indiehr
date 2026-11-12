"""Buckets of our SeaweedFS (P1-T8b3): CARE's two file buckets, the audit fingerprint bucket and,
in a proof, the backup and log-archive buckets (P1-T8c, P1-T9c).

Idempotent; run with the storage admin key, from any image that has boto3 (dev: care_local,
staging: the backend image). Nothing here is IndieHR-specific Python: it needs no Django.

    S3_ADMIN_KEY=... S3_ADMIN_SECRET=... python setup_buckets.py --endpoint http://s3:8333 \
        --patient patient-bucket --facility facility-bucket --anchor ihr-audit-anchors \
        --lock-mode GOVERNANCE --lock-days 30 --cors-origin 'http://*.localhost:8080'

- patient bucket: private; CORS for the web app (uploads and downloads go straight from the
  browser with presigned URLs);
- facility bucket: anyone may GET an object (hospital pictures, as upstream CARE expects),
  nobody may list it anonymously; same CORS;
- anchor bucket: Object Lock (versioned), default retention <mode> for <days>. Created locked or
  refused: a bucket without a lock is never used. Weakening the retention of an existing bucket
  (mode or days) is refused; locks already on stored objects never change anyway.
"""

import argparse
import json
import os
import sys

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

STRENGTH = {"GOVERNANCE": 0, "COMPLIANCE": 1}


def client(endpoint):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["S3_ADMIN_KEY"],
        aws_secret_access_key=os.environ["S3_ADMIN_SECRET"],
        region_name=os.environ.get("S3_REGION", "ap-south-1"),
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 5}),
    )


def code(err):
    return err.response.get("Error", {}).get("Code", "")


def exists(s3, bucket):
    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except ClientError as err:
        if code(err) in ("404", "NoSuchBucket", "NotFound"):
            return False
        raise


def ensure_plain(s3, bucket):
    if exists(s3, bucket):
        print(f"bucket {bucket}: exists")
    else:
        s3.create_bucket(Bucket=bucket)
        print(f"bucket {bucket}: created")


def cors(s3, bucket, origins):
    rules = [{
        "AllowedOrigins": origins,
        "AllowedMethods": ["GET", "PUT", "HEAD"],
        "AllowedHeaders": ["*"],
        "ExposeHeaders": ["ETag"],
        "MaxAgeSeconds": 3600,
    }]
    s3.put_bucket_cors(Bucket=bucket, CORSConfiguration={"CORSRules": rules})
    print(f"bucket {bucket}: CORS for {', '.join(origins)}")


def no_policy(s3, bucket):
    try:
        s3.delete_bucket_policy(Bucket=bucket)
    except ClientError as err:
        if code(err) not in ("NoSuchBucketPolicy", "NoSuchBucket"):
            raise
    print(f"bucket {bucket}: private (no bucket policy)")


def public_read(s3, bucket):
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Sid": "PublicReadObjectsOnly",
            "Effect": "Allow",
            "Principal": "*",
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{bucket}/*"],
        }],
    }
    s3.put_bucket_policy(Bucket=bucket, Policy=json.dumps(policy))
    print(f"bucket {bucket}: objects readable by anyone, no anonymous listing")


def anchor_bucket(s3, bucket, mode, days):
    if not exists(s3, bucket):
        s3.create_bucket(Bucket=bucket, ObjectLockEnabledForBucket=True)
        print(f"bucket {bucket}: created with Object Lock")
    try:
        current = s3.get_object_lock_configuration(Bucket=bucket)["ObjectLockConfiguration"]
    except ClientError as err:
        sys.exit(f"bucket {bucket} exists without Object Lock ({code(err)}): refusing to use it")
    if current.get("ObjectLockEnabled") != "Enabled":
        sys.exit(f"bucket {bucket} exists without Object Lock: refusing to use it")
    rule = (current.get("Rule") or {}).get("DefaultRetention") or {}
    # A rule may be in Years instead of Days: compare in days
    have_mode = rule.get("Mode")
    have_days = rule.get("Days") or (365 * rule["Years"] if rule.get("Years") else None)
    if have_mode == mode and have_days == days:
        print(f"bucket {bucket}: default retention {mode} {days} days (unchanged)")
        return
    if have_mode and (STRENGTH[mode] < STRENGTH[have_mode] or (have_days or 0) > days):
        sys.exit(f"bucket {bucket}: has {have_mode} {have_days} days; refusing to weaken it to {mode} {days}")
    s3.put_object_lock_configuration(
        Bucket=bucket,
        ObjectLockConfiguration={
            "ObjectLockEnabled": "Enabled",
            "Rule": {"DefaultRetention": {"Mode": mode, "Days": days}},
        },
    )
    print(f"bucket {bucket}: default retention {mode} {days} days")
    if s3.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
        sys.exit(f"bucket {bucket}: versioning is not on")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--endpoint", required=True)
    p.add_argument("--patient", required=True)
    p.add_argument("--facility", required=True)
    p.add_argument("--anchor", required=True)
    p.add_argument("--backups", help="a private bucket for pgBackRest (proofs; P1-T8c)")
    p.add_argument("--log-archive", help="the locked log archive (proofs; P1-T9c)")
    p.add_argument("--log-archive-mode", choices=sorted(STRENGTH), default="GOVERNANCE")
    p.add_argument("--log-archive-days", type=int, default=400)
    p.add_argument("--lock-mode", choices=sorted(STRENGTH), required=True)
    p.add_argument("--lock-days", type=int, required=True)
    p.add_argument("--cors-origin", action="append", required=True)
    a = p.parse_args()
    if a.lock_days < 1:
        sys.exit("--lock-days must be at least 1")
    s3 = client(a.endpoint)
    for bucket in (a.patient, a.facility):
        ensure_plain(s3, bucket)
        cors(s3, bucket, a.cors_origin)
    no_policy(s3, a.patient)
    public_read(s3, a.facility)
    anchor_bucket(s3, a.anchor, a.lock_mode, a.lock_days)
    if a.backups:
        ensure_plain(s3, a.backups)
        no_policy(s3, a.backups)
    if a.log_archive:
        # Locked like E2E's (Governance, 400 days, owner Q-O10): erasure lifts it (expire.py)
        anchor_bucket(s3, a.log_archive, a.log_archive_mode, a.log_archive_days)
        no_policy(s3, a.log_archive)


if __name__ == "__main__":
    main()
