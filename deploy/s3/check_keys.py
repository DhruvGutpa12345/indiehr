"""Live checks of our SeaweedFS keys and locks (P1-T8b3): each key can do what it needs and
nothing more, and a locked fingerprint can be removed by nobody, the admin key included.

    S3_ADMIN_KEY ... S3_ANCHOR_READ_SECRET=... python check_keys.py --endpoint http://s3:8333 \
        --patient patient-bucket --facility facility-bucket --anchor ihr-audit-anchors \
        [--public-endpoint https://files.app.example.in] [--origin https://alpha.app.example.in]

Keys (environment): S3_ADMIN_*, S3_FILES_* (CARE's patient files), S3_PICTURES_* (CARE's staff
photos and facility covers; P1-T20b), S3_ANCHOR_WRITE_*, S3_ANCHOR_READ_*, optional
S3_LOGS_ARCHIVE_* and S3_RETIRED_CARE_* (the combined key P1-T20b retired: it must be dead).

Writes small test objects: CARE's under ihr-key-check/ (removed again), one fingerprint-sized
object under anchors/ihr-key-check/ in the anchor bucket, which stays until its retention ends
(the fingerprint check reads only anchors/<platform suffix>/, so it never sees it).
Prints PASS/FAIL per check; exit code 1 on any FAIL.
"""

import argparse
import base64
import hashlib
import os
import ssl
import sys
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

FAILS = 0
SSL = None  # --insecure: an unverified context for the public address


def client(endpoint, who):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ[f"S3_{who}_KEY"],
        aws_secret_access_key=os.environ[f"S3_{who}_SECRET"],
        region_name=os.environ.get("S3_REGION", "ap-south-1"),
        config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 1}),
    )


def outcome(fn):
    """'ok', or the S3 error code / HTTP status of a refusal"""
    try:
        fn()
        return "ok"
    except ClientError as err:
        return err.response.get("Error", {}).get("Code") or str(err.response["ResponseMetadata"]["HTTPStatusCode"])
    except urllib.error.HTTPError as err:
        return str(err.code)
    except urllib.error.URLError as err:
        # Traefik answers an oversized upload with 413 at once and closes the connection while
        # the client is still sending: the client then sees a reset, not the answer
        if isinstance(err.reason, ConnectionResetError | BrokenPipeError):
            return "reset"
        raise


# What a refusal looks like: S3's own (403 / AccessDenied, 412 for a second write of one name,
# 409 for a bucket that is not empty) or Traefik's closed route (418). Anything else (404,
# 5xx, a gateway that is down) is not a refusal and fails the check.
REFUSALS = {"AccessDenied", "403", "418", "PreconditionFailed", "BucketNotEmpty", "SignatureDoesNotMatch"}


def dead(name, got):
    """A retired key: the gateway no longer knows it at all (not merely refuses one call)."""
    global FAILS
    good = got == "InvalidAccessKeyId"
    print(f"{'PASS' if good else 'FAIL'}  {name} ({got})")
    if not good:
        FAILS += 1


def check(name, want, got):
    global FAILS
    good = got == want if want == "ok" else got in REFUSALS
    print(f"{'PASS' if good else 'FAIL'}  {name} ({got})")
    if not good:
        FAILS += 1


def http(method, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15, context=SSL) as resp:
        return resp.status, dict(resp.headers), resp.read()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--endpoint", required=True)
    p.add_argument("--patient", required=True)
    p.add_argument("--facility", required=True)
    p.add_argument("--anchor", required=True)
    p.add_argument("--public-endpoint", help="the address browsers use (presigned URLs)")
    p.add_argument("--origin", help="a web app origin that CORS must allow")
    p.add_argument("--insecure", action="store_true", help="skip TLS checks on the public address")
    p.add_argument("--log-archive", help="the log archive bucket (P1-T9c): its key adds and reads, never deletes")
    p.add_argument("--size-limit", action="store_true",
                   help="through Traefik: a 50 MB upload works, a 120 MB one is refused (413)")
    a = p.parse_args()
    global SSL
    if a.insecure:
        SSL = ssl._create_unverified_context()  # noqa: S323 (the certificate is checked elsewhere)
    admin = client(a.endpoint, "ADMIN")
    files, pictures = client(a.endpoint, "FILES"), client(a.endpoint, "PICTURES")
    writer, reader = client(a.endpoint, "ANCHOR_WRITE"), client(a.endpoint, "ANCHOR_READ")
    run = uuid.uuid4().hex[:12]

    # One object in each of CARE's buckets, written by its own key, for the cross checks
    key = f"ihr-key-check/{run}.txt"
    fkey = f"ihr-key-check/{run}.png"
    print("== Patient files key (ihr-care-files): the patient bucket only")
    check("put in the patient bucket", "ok", outcome(lambda: files.put_object(Bucket=a.patient, Key=key, Body=b"x", ContentType="text/plain")))
    check("read it back", "ok", outcome(lambda: files.get_object(Bucket=a.patient, Key=key)))
    print("== Pictures key (ihr-care-pictures): the pictures bucket only")
    check("put in the pictures bucket", "ok", outcome(lambda: pictures.put_object(Bucket=a.facility, Key=fkey, Body=b"png", ContentType="image/png")))
    check("read it back", "ok", outcome(lambda: pictures.get_object(Bucket=a.facility, Key=fkey)))
    check("read a patient file", "denied", outcome(lambda: pictures.get_object(Bucket=a.patient, Key=key)))
    check("put in the patient bucket", "denied", outcome(lambda: pictures.put_object(Bucket=a.patient, Key=f"ihr-key-check/{run}-evil", Body=b"x")))
    check("delete a patient file", "denied", outcome(lambda: pictures.delete_object(Bucket=a.patient, Key=key)))
    check("list the patient bucket", "denied", outcome(lambda: pictures.list_objects_v2(Bucket=a.patient)))
    check("put in the fingerprint bucket", "denied", outcome(lambda: pictures.put_object(Bucket=a.anchor, Key=f"anchors/ihr-key-check/{run}-p", Body=b"x")))
    check("create a bucket", "denied", outcome(lambda: pictures.create_bucket(Bucket=f"ihr-check-{run}-p")))
    print("== Patient files key: never the pictures")
    check("read a picture", "denied", outcome(lambda: files.get_object(Bucket=a.facility, Key=fkey)))
    check("put in the pictures bucket", "denied", outcome(lambda: files.put_object(Bucket=a.facility, Key=f"ihr-key-check/{run}-evil.png", Body=b"x")))
    check("delete a picture", "denied", outcome(lambda: files.delete_object(Bucket=a.facility, Key=fkey)))
    check("list the pictures bucket", "denied", outcome(lambda: files.list_objects_v2(Bucket=a.facility)))
    check("put in the fingerprint bucket", "denied", outcome(lambda: files.put_object(Bucket=a.anchor, Key=f"anchors/ihr-key-check/{run}", Body=b"x")))
    check("list the fingerprint bucket", "denied", outcome(lambda: files.list_objects_v2(Bucket=a.anchor)))
    check("create a bucket", "denied", outcome(lambda: files.create_bucket(Bucket=f"ihr-check-{run}")))
    if os.environ.get("S3_RETIRED_CARE_KEY"):
        print("== The retired combined key (ihr-care, before P1-T20b) is dead")
        old = client(a.endpoint, "RETIRED_CARE")
        dead("read a patient file", outcome(lambda: old.get_object(Bucket=a.patient, Key=key)))
        dead("read a picture", outcome(lambda: old.get_object(Bucket=a.facility, Key=fkey)))
        dead("put in the pictures bucket", outcome(lambda: old.put_object(Bucket=a.facility, Key=f"ihr-key-check/{run}-old.png", Body=b"x")))
    else:
        print("== No retired combined key on this machine (set up after P1-T20b): nothing to retire")
    check("the patient files key deletes its file", "ok", outcome(lambda: files.delete_object(Bucket=a.patient, Key=key)))

    print("== Fingerprint writer: add only")
    akey = f"anchors/ihr-key-check/{run}.json"
    body = b'{"check": true}'
    md5 = base64.b64encode(hashlib.md5(body).digest()).decode()  # noqa: S324 (S3 Content-MD5)
    check("add a fingerprint (If-None-Match, Content-MD5)", "ok", outcome(lambda: writer.put_object(Bucket=a.anchor, Key=akey, Body=body, ContentMD5=md5, IfNoneMatch="*")))
    check("write the same name twice", "denied", outcome(lambda: writer.put_object(Bucket=a.anchor, Key=akey, Body=b"y", IfNoneMatch="*")))
    check("add outside anchors/", "denied", outcome(lambda: writer.put_object(Bucket=a.anchor, Key=f"other/{run}", Body=b"x")))
    check("read", "denied", outcome(lambda: writer.get_object(Bucket=a.anchor, Key=akey)))
    check("list", "denied", outcome(lambda: writer.list_objects_v2(Bucket=a.anchor)))
    versions = admin.list_object_versions(Bucket=a.anchor, Prefix=akey).get("Versions", [])
    vid = versions[0]["VersionId"] if versions else "none"
    soon = datetime.now(UTC) + timedelta(seconds=5)
    check("delete the version", "denied", outcome(lambda: writer.delete_object(Bucket=a.anchor, Key=akey, VersionId=vid)))
    check("hide it with a delete marker", "denied", outcome(lambda: writer.delete_object(Bucket=a.anchor, Key=akey)))
    check("shorten its retention", "denied", outcome(lambda: writer.put_object_retention(Bucket=a.anchor, Key=akey, VersionId=vid, Retention={"Mode": "GOVERNANCE", "RetainUntilDate": soon})))
    check("delete with the governance bypass", "denied", outcome(lambda: writer.delete_object(Bucket=a.anchor, Key=akey, VersionId=vid, BypassGovernanceRetention=True)))
    check("add with its own short retention", "denied", outcome(lambda: writer.put_object(Bucket=a.anchor, Key=f"{akey}.2", Body=b"x", ObjectLockMode="GOVERNANCE", ObjectLockRetainUntilDate=soon)))
    check("change the bucket's lock", "denied", outcome(lambda: writer.put_object_lock_configuration(Bucket=a.anchor, ObjectLockConfiguration={"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 1}}})))

    if a.log_archive and os.environ.get("S3_LOGS_ARCHIVE_KEY"):
        print("== Log archive key (P1-T9c): add and read under logs/, never delete or unlock")
        arch = client(a.endpoint, "LOGS_ARCHIVE")
        lkey = f"logs/ihr-key-check/{run}.manifest.json"
        lbody = b'{"check": true}'
        lmd5 = base64.b64encode(hashlib.md5(lbody).digest()).decode()  # noqa: S324
        check("add (If-None-Match, Content-MD5)", "ok", outcome(lambda: arch.put_object(Bucket=a.log_archive, Key=lkey, Body=lbody, ContentMD5=lmd5, IfNoneMatch="*")))
        check("read it back", "ok", outcome(lambda: arch.get_object(Bucket=a.log_archive, Key=lkey)))
        check("list the bucket", "ok", outcome(lambda: arch.list_objects_v2(Bucket=a.log_archive, Prefix="logs/ihr-key-check/")))
        check("write the same name twice", "denied", outcome(lambda: arch.put_object(Bucket=a.log_archive, Key=lkey, Body=b"y", IfNoneMatch="*")))
        check("add outside logs/", "denied", outcome(lambda: arch.put_object(Bucket=a.log_archive, Key=f"erasure/{run}.json", Body=b"x")))
        lversions = admin.list_object_versions(Bucket=a.log_archive, Prefix=lkey).get("Versions", [])
        lvid = lversions[0]["VersionId"] if lversions else "none"
        lsoon = datetime.now(UTC) + timedelta(seconds=5)
        check("delete the version", "denied", outcome(lambda: arch.delete_object(Bucket=a.log_archive, Key=lkey, VersionId=lvid)))
        check("hide it with a delete marker", "denied", outcome(lambda: arch.delete_object(Bucket=a.log_archive, Key=lkey)))
        check("delete with the governance bypass", "denied", outcome(lambda: arch.delete_object(Bucket=a.log_archive, Key=lkey, VersionId=lvid, BypassGovernanceRetention=True)))
        check("shorten its retention", "denied", outcome(lambda: arch.put_object_retention(Bucket=a.log_archive, Key=lkey, VersionId=lvid, Retention={"Mode": "GOVERNANCE", "RetainUntilDate": lsoon})))
        check("change the bucket's lock", "denied", outcome(lambda: arch.put_object_lock_configuration(Bucket=a.log_archive, ObjectLockConfiguration={"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": "GOVERNANCE", "Days": 1}}})))
        check("read the fingerprint bucket", "denied", outcome(lambda: arch.list_objects_v2(Bucket=a.anchor)))
        lmode = admin.get_object_retention(Bucket=a.log_archive, Key=lkey, VersionId=lvid)["Retention"]["Mode"]
        print(f"      archive retention: {lmode} (Governance, like E2E's: erasure with the admin key lifts it)")
        check("admin without the bypass cannot delete it", "denied", outcome(lambda: admin.delete_object(Bucket=a.log_archive, Key=lkey, VersionId=lvid)))
        check("admin with the bypass erases it (expire.py)", "ok", outcome(lambda: admin.delete_object(Bucket=a.log_archive, Key=lkey, VersionId=lvid, BypassGovernanceRetention=True)))

    print("== Fingerprint reader: read and list only")
    check("read a version", "ok", outcome(lambda: reader.get_object(Bucket=a.anchor, Key=akey, VersionId=vid)))
    check("list versions", "ok", outcome(lambda: reader.list_object_versions(Bucket=a.anchor, Prefix="anchors/ihr-key-check/")))
    check("read the lock settings", "ok", outcome(lambda: reader.get_object_lock_configuration(Bucket=a.anchor)))
    check("add", "denied", outcome(lambda: reader.put_object(Bucket=a.anchor, Key=f"anchors/ihr-key-check/{run}-r", Body=b"x")))
    check("delete the version", "denied", outcome(lambda: reader.delete_object(Bucket=a.anchor, Key=akey, VersionId=vid)))

    print("== The lock holds against the storage admin key too")
    mode = admin.get_object_retention(Bucket=a.anchor, Key=akey, VersionId=vid)["Retention"]["Mode"]
    print(f"      fingerprint retention: {mode}")
    if mode == "COMPLIANCE":
        check("admin deletes the version", "denied", outcome(lambda: admin.delete_object(Bucket=a.anchor, Key=akey, VersionId=vid)))
        check("admin deletes with the governance bypass", "denied", outcome(lambda: admin.delete_object(Bucket=a.anchor, Key=akey, VersionId=vid, BypassGovernanceRetention=True)))
        check("admin shortens the retention", "denied", outcome(lambda: admin.put_object_retention(Bucket=a.anchor, Key=akey, VersionId=vid, Retention={"Mode": "COMPLIANCE", "RetainUntilDate": soon})))
    else:
        print("      GOVERNANCE (dev): an admin key may lift it, by design; checks skipped")
    check("admin deletes the whole bucket", "denied", outcome(lambda: admin.delete_bucket(Bucket=a.anchor)))
    check("the fingerprint is still there", "ok", outcome(lambda: reader.get_object(Bucket=a.anchor, Key=akey, VersionId=vid)))

    pub = a.public_endpoint or a.endpoint

    def signer(who):
        """A client that signs links for the address browsers use"""
        return boto3.client(
            "s3", endpoint_url=pub, aws_access_key_id=os.environ[f"S3_{who}_KEY"],
            aws_secret_access_key=os.environ[f"S3_{who}_SECRET"], region_name=os.environ.get("S3_REGION", "ap-south-1"),
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )

    print(f"== Anonymous and browser access through {pub}")
    # Pictures (P1-T20b, Q-T3): a private bucket, read only with a signed link
    check("anonymous cannot read a facility picture", "denied", outcome(lambda: http("GET", f"{pub}/{a.facility}/{fkey}")))
    pic_url = signer("PICTURES").generate_presigned_url("get_object", Params={"Bucket": a.facility, "Key": fkey}, ExpiresIn=120)
    check("a signed link reads it", "ok", outcome(lambda: http("GET", pic_url)))
    check("a changed signed link is refused", "denied", outcome(lambda: http("GET", pic_url.replace(fkey, fkey + "x"))))
    files_pic_url = signer("FILES").generate_presigned_url("get_object", Params={"Bucket": a.facility, "Key": fkey}, ExpiresIn=120)
    check("a link signed with the patient files key is refused", "denied", outcome(lambda: http("GET", files_pic_url)))
    check("nobody lists the facility bucket", "denied", outcome(lambda: http("GET", f"{pub}/{a.facility}/")))
    check("nobody writes the facility bucket", "denied", outcome(lambda: http("PUT", f"{pub}/{a.facility}/{run}-evil", data=b"x")))
    check("nobody reads the fingerprint bucket", "denied", outcome(lambda: http("GET", f"{pub}/{a.anchor}/{akey}")))
    check("the pictures key deletes its picture", "ok", outcome(lambda: pictures.delete_object(Bucket=a.facility, Key=fkey)))
    browser = signer("FILES")
    pkey = f"ihr-key-check/{run}-upload.txt"
    put_url = browser.generate_presigned_url("put_object", Params={"Bucket": a.patient, "Key": pkey, "ContentType": "text/plain"}, ExpiresIn=120)
    check("upload with a presigned URL", "ok", outcome(lambda: http("PUT", put_url, data=b"hello", headers={"Content-Type": "text/plain"})))
    get_url = browser.generate_presigned_url("get_object", Params={"Bucket": a.patient, "Key": pkey, "ResponseContentDisposition": "attachment"}, ExpiresIn=120)
    check("download with a presigned URL", "ok", outcome(lambda: http("GET", get_url)))
    check("a changed presigned URL is refused", "denied", outcome(lambda: http("GET", get_url.replace(pkey, pkey + "x"))))
    check("nobody reads that patient file without a signature", "denied", outcome(lambda: http("GET", f"{pub}/{a.patient}/{pkey}")))
    pic_signed = signer("PICTURES").generate_presigned_url("get_object", Params={"Bucket": a.patient, "Key": pkey}, ExpiresIn=120)
    check("a link signed with the pictures key cannot read it", "denied", outcome(lambda: http("GET", pic_signed)))
    if a.origin:
        pre = {"Origin": a.origin, "Access-Control-Request-Method": "PUT"}
        check(f"CORS allows {a.origin}", "ok", outcome(lambda: http("OPTIONS", f"{pub}/{a.patient}/{pkey}", headers=pre)))
        check("CORS refuses another site", "denied", outcome(lambda: http("OPTIONS", f"{pub}/{a.patient}/{pkey}", headers={**pre, "Origin": "https://evil.example.com"})))
    files.delete_object(Bucket=a.patient, Key=pkey)
    if a.size_limit:
        print("== Upload size limit (Traefik)")
        for mb, want in ((50, ("ok",)), (120, ("413", "reset"))):
            bkey = f"ihr-key-check/{run}-{mb}mb.bin"
            url = browser.generate_presigned_url("put_object", Params={"Bucket": a.patient, "Key": bkey, "ContentType": "application/octet-stream"}, ExpiresIn=300)
            got = outcome(lambda url=url, mb=mb: http("PUT", url, data=bytes(mb * 1000 * 1000), headers={"Content-Type": "application/octet-stream"}))
            print(f"{'PASS' if got in want else 'FAIL'}  {mb} MB upload ({got}, want {' or '.join(want)})")
            if got not in want:
                globals()["FAILS"] += 1
            files.delete_object(Bucket=a.patient, Key=bkey)

    print()
    print("All checks passed." if not FAILS else f"{FAILS} check(s) FAILED")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
