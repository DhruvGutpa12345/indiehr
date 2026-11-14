"""A bucket written once per name and read back, shared by the log archive (P1-T9c) and the
nightly dumps (P1-T22a). Moved here from deploy/logs/archive/archive.py.

- Every write sends Content-MD5 (a bucket with Object Lock refuses a write without an integrity
  header) and If-None-Match: * (a second write of a name is refused rather than becoming a new
  version; <PREFIX>_IF_NONE_MATCH=off for a store without it).
- A file larger than `multipart_bytes` (default 4 GiB: one PUT takes at most 5 GiB) goes up in
  parts of 64 MiB, each with its own Content-MD5; the name is checked free first and the upload
  completed with If-None-Match.
- A write refused because the name exists is checked against what is there: our own write whose
  reply was lost and retried by the client (the retry then meets If-None-Match) is the same bytes,
  and counts as done (P1-T22a review S4/C6); anything else stays refused.
- Each write returns the version the store gave it (None without versioning); get and size read
  a given version, and `versions` lists every version and delete marker under a prefix, so a
  reader can take the version it recorded and see a name written twice (P1-T22a review S1).
- lock_state: whether the bucket has Object Lock with at least N days and versioning.

Configuration from the environment under a prefix (IHR_LOGS_ARCHIVE, IHR_DUMP): _ENDPOINT,
_REGION, _BUCKET, _KEY (the dumps: _ACCESS_KEY, as IHR_DUMP_KEY is their encryption key), _SECRET,
_URI_STYLE, _IF_NONE_MATCH, _MULTIPART_BYTES. Needs boto3.
"""

import base64
import hashlib
import os
import sys

CHUNK = 1024 * 1024
PART = 64 * CHUNK
MULTIPART = 4 * 1024 * CHUNK


def taken_error(exc):
    """A write refused because the name exists (If-None-Match)."""
    err = getattr(exc, "response", None) or {}
    code = str((err.get("Error") or {}).get("Code", ""))
    status = (err.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return code in ("PreconditionFailed", "412") or status == 412 or "PreconditionFailed" in str(exc)


def error_code(exc):
    return str(((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code", ""))


def _md5(data):
    return base64.b64encode(hashlib.md5(data).digest()).decode()  # noqa: S324 (S3's integrity header)


def _version(response):
    """The version a write created (None without versioning)."""
    v = (response or {}).get("VersionId") if isinstance(response, dict) else None
    return v if v not in (None, "null") else None


class Bucket:
    def __init__(self, client, name, once=True, multipart_bytes=MULTIPART):
        self.s3 = client
        self.name = name
        self.once = once
        self.multipart_bytes = multipart_bytes

    @classmethod
    def from_env(cls, prefix, who="bucket", key_name="KEY"):
        import boto3
        from botocore.config import Config

        def need(name, default=None):
            v = os.environ.get(f"{prefix}_{name}", default)
            if v is None:
                sys.exit(f"{who}: {prefix}_{name} is not set")
            return v

        client = boto3.client(
            "s3",
            endpoint_url=os.environ.get(f"{prefix}_ENDPOINT") or None,
            region_name=need("REGION", "ap-south-1"),
            aws_access_key_id=need(key_name),
            aws_secret_access_key=need("SECRET"),
            config=Config(signature_version="s3v4",
                          s3={"addressing_style": os.environ.get(f"{prefix}_URI_STYLE", "path")},
                          connect_timeout=10, read_timeout=120, retries={"max_attempts": 3}),
        )
        return cls(client, need("BUCKET"), once=os.environ.get(f"{prefix}_IF_NONE_MATCH", "on") != "off",
                   multipart_bytes=int(os.environ.get(f"{prefix}_MULTIPART_BYTES", str(MULTIPART))))

    def _once(self):
        return {"IfNoneMatch": "*"} if self.once else {}

    def put_file(self, key, path, content_type):
        """Uploads a file once under key; returns the version the store gave it."""
        md5, sha = hashlib.md5(), hashlib.sha256()  # noqa: S324 (the S3 integrity header a locked bucket needs)
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                md5.update(block)
                sha.update(block)
        size = os.path.getsize(path)
        if size > self.multipart_bytes:
            return self._put_parts(key, path, content_type, sha.hexdigest(), size)
        try:
            with open(path, "rb") as f:
                r = self.s3.put_object(Bucket=self.name, Key=key, Body=f, ContentType=content_type,
                                       ContentMD5=base64.b64encode(md5.digest()).decode(), **self._once())
        except Exception as exc:
            return self._ours_after_all(exc, key, sha.hexdigest(), size)
        return _version(r)

    def _put_parts(self, key, path, content_type, sha, size):
        if self.once and self.exists(key):
            raise KeyTaken(key)
        up = self.s3.create_multipart_upload(Bucket=self.name, Key=key, ContentType=content_type)
        uid, parts = up["UploadId"], []
        try:
            with open(path, "rb") as f:
                n = 0
                for block in iter(lambda: f.read(PART), b""):
                    n += 1
                    r = self.s3.upload_part(Bucket=self.name, Key=key, UploadId=uid, PartNumber=n,
                                            Body=block, ContentMD5=_md5(block))
                    parts.append({"ETag": r["ETag"], "PartNumber": n})
            try:
                r = self.s3.complete_multipart_upload(Bucket=self.name, Key=key, UploadId=uid,
                                                      MultipartUpload={"Parts": parts}, **self._once())
            except Exception as exc:
                return self._ours_after_all(exc, key, sha, size)
            return _version(r)
        except BaseException:
            try:
                self.s3.abort_multipart_upload(Bucket=self.name, Key=key, UploadId=uid)
            except Exception:  # noqa: BLE001, S110 (the first error is the one reported)
                pass
            raise

    def put_bytes(self, key, body, content_type):
        """Writes body once under key; returns the version the store gave it."""
        try:
            r = self.s3.put_object(Bucket=self.name, Key=key, Body=body, ContentType=content_type,
                                   ContentMD5=_md5(body), **self._once())
        except Exception as exc:
            return self._ours_after_all(exc, key, hashlib.sha256(body).hexdigest(), len(body))
        return _version(r)

    def _ours_after_all(self, exc, key, sha, size):
        """A write refused as taken: when the name holds exactly these bytes, it is our own write
        whose reply was lost (the client retried it into If-None-Match), so it is done; any other
        error, or other bytes, is raised as it came. Encrypted bytes never repeat (each token has
        a random IV), so equal bytes are ours."""
        if not taken_error(exc):
            raise exc
        version = self.same_as(key, sha, size)
        if version is False:
            raise exc
        return version

    def same_as(self, key, sha, size):
        """The current version of key when it holds exactly these bytes (its version id, or None
        without versioning); False when it does not (or cannot be read)."""
        try:
            head = self.s3.head_object(Bucket=self.name, Key=key)
            if head.get("ContentLength") != size:
                return False
            version = head.get("VersionId") if head.get("VersionId") not in (None, "null") else None
            body = self.get(key, version)
            digest = hashlib.sha256()
            for block in iter(lambda: body.read(CHUNK), b""):
                digest.update(block)
        except Exception:  # noqa: BLE001 (unreadable: not shown to be ours)
            return False
        return version if digest.hexdigest() == sha else False

    def get(self, key, version=None):
        """The object's body: the given version, else the current one."""
        kw = {"VersionId": version} if version else {}
        return self.s3.get_object(Bucket=self.name, Key=key, **kw)["Body"]

    def size(self, key, version=None):
        kw = {"VersionId": version} if version else {}
        return self.s3.head_object(Bucket=self.name, Key=key, **kw)["ContentLength"]

    def versions(self, prefix):
        """Every version and delete marker under prefix, as dicts: key, version, marker (a delete
        marker), size, modified, order (the listing's position: newest first within a key)."""
        n = 0
        for page in self.s3.get_paginator("list_object_versions").paginate(Bucket=self.name, Prefix=prefix):
            for kind in ("Versions", "DeleteMarkers"):
                for item in page.get(kind, []):
                    n += 1
                    yield {"key": item["Key"], "version": item.get("VersionId"), "marker": kind == "DeleteMarkers",
                           "size": item.get("Size"), "modified": item.get("LastModified"), "order": n}

    def exists(self, key):
        try:
            self.s3.head_object(Bucket=self.name, Key=key)
            return True
        except Exception as exc:  # noqa: BLE001
            if error_code(exc) in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def keys(self, prefix):
        for page in self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.name, Prefix=prefix):
            for item in page.get("Contents", []):
                yield item["Key"]


class KeyTaken(Exception):
    """The name exists already (the multipart path's own If-None-Match)."""

    def __init__(self, key):
        super().__init__(f"PreconditionFailed: {key} exists")
        self.response = {"Error": {"Code": "PreconditionFailed"}}


def lock_state(bucket, min_days):
    """1: Object Lock with at least min_days and versioning on; 0: not; -1: this key cannot read it."""
    try:
        conf = bucket.s3.get_object_lock_configuration(Bucket=bucket.name)["ObjectLockConfiguration"]
        versioning = bucket.s3.get_bucket_versioning(Bucket=bucket.name).get("Status")
    except Exception as exc:  # noqa: BLE001
        return -1 if error_code(exc) in ("AccessDenied", "403") else 0
    rule = (conf.get("Rule") or {}).get("DefaultRetention") or {}
    days = rule.get("Days") or 365 * (rule.get("Years") or 0)
    ok = (conf.get("ObjectLockEnabled") == "Enabled" and rule.get("Mode") in ("GOVERNANCE", "COMPLIANCE")
          and days >= min_days and versioning == "Enabled")
    return 1 if ok else 0
