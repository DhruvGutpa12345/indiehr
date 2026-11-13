"""Object storage per tenant (TENANCY_DESIGN.md §6.8).

- ``tenant_key_prefix``: CARE calls it through ``settings.FILE_OBJECT_KEY_PREFIX_FUNCTION`` for
  every file and image key. The prefix uses the tenant's immutable UUID, never its schema name
  or slug.
- ``facility_image_url`` (P1-T20b, owner answer Q-T3): CARE calls it through
  ``settings.FACILITY_IMAGE_URL_FUNCTION`` (the logged ``image_url`` helper in
  ``care/utils/file_uploads/cover_image.py``) for every staff photo and facility cover. The
  pictures bucket is private: a picture is reached only by a signed link, and only for a key of
  this hospital's own shape. Design and attack: docs/reviews/p1-t20/design.md (A7, A8).
"""

import hashlib
import re
from datetime import UTC, datetime

import boto3
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.db import connection

_uuid_by_schema = {}

# A signed picture link lives a day; the same link is handed out for half of that, so the
# browser's image cache still works (every upload makes a new key, so a cached link is never
# stale; after a delete it stops working at once: 404 on SeaweedFS, 403 AccessDenied on
# infra/aws, whose per-bucket keys have no s3:ListBucket; docs/reviews/p1-t20/design.md)
LINK_SECONDS = 24 * 3600
CACHE_SECONDS = 12 * 3600
# Temporary (role-based) credentials: a link signed with them dies with them, so it is never
# cached for longer than they have left, less this margin
TEMPORARY_MARGIN_SECONDS = 15 * 60
# The only keys CARE's picture uploads make (upload_cover_image): this hospital's prefix, the
# folder, the facility's or user's external id and 16 random hex digits
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
PICTURE_KEY = re.compile(
    rf"t/(?P<hospital>{_UUID})/(?:cover_images|avatars)/{_UUID}_[0-9a-f]{{16}}\.[A-Za-z0-9]{{1,8}}"
)


def tenant_key_prefix():
    schema = getattr(connection, "schema_name", None)
    if not schema or schema == "public":
        msg = "Files can only be stored inside a tenant (no tenant is active)."
        raise ImproperlyConfigured(msg)
    if schema not in _uuid_by_schema:
        from ihr_platform.models import Hospital

        _uuid_by_schema[schema] = str(
            Hospital.objects.values_list("uuid", flat=True).get(schema_name=schema)
        )
    return f"t/{_uuid_by_schema[schema]}/"


def _role_credentials():
    """The process's own credentials (role-based access: no key in the settings), from boto3's
    default session, which ``boto3.client`` below signs with."""
    if boto3.DEFAULT_SESSION is None:
        boto3.setup_default_session()
    return boto3.DEFAULT_SESSION.get_credentials()


def _cache_seconds(config):
    """How long a link signed now may be handed out again; 0: not at all. Key-based access:
    CACHE_SECONDS. Role-based: never beyond the temporary credentials' own expiry (unknown
    expiry: not cached)."""
    # Key-based access: CARE leaves the key out of the config only for role-based access
    if "aws_access_key_id" in config:
        return CACHE_SECONDS
    credentials = _role_credentials()
    if credentials is None:
        return 0
    if not credentials.get_frozen_credentials().token:
        return CACHE_SECONDS
    expiry = getattr(credentials, "_expiry_time", None)
    if expiry is None:
        return 0
    left = (expiry - datetime.now(UTC)).total_seconds() - TEMPORARY_MARGIN_SECONDS
    return max(0, min(CACHE_SECONDS, int(left)))


def _key_id(config):
    if "aws_access_key_id" in config:
        return config["aws_access_key_id"]
    credentials = _role_credentials()
    return credentials.get_frozen_credentials().access_key if credentials else ""


# One S3 client per process and signing identity (C5): building one costs about twenty
# signatures, so a cold list of pictures must not build one per picture
_signers = {}
_SIGNERS_MAX = 8


def _client(config):
    """The S3 client for this config, reused. Keyed on what it signs with (key id, a digest of
    the secret, never the secret itself, endpoint, region): a rotated key or a moved endpoint gets
    a new one. boto3 clients are thread-safe for signing; with role-based access (no key) the
    client keeps the default session's refreshable credentials, so it is reused too."""
    secret = config.get("aws_secret_access_key") or ""
    name = (
        config.get("aws_access_key_id"),
        hashlib.sha256(secret.encode()).hexdigest(),
        config.get("endpoint_url"),
        config.get("region_name"),
    )
    client = _signers.get(name)
    if client is None:
        if len(_signers) >= _SIGNERS_MAX:
            _signers.clear()
        client = _signers[name] = boto3.client("s3", **config)
    return client


def link_cache_name(prefix, key, *, key_id, endpoint, bucket, seconds):
    """The hospital-prefixed cache name of one signed link. It also hashes what the link was
    signed with (key id, endpoint, bucket, lifetime): a rotated key, a moved endpoint or a new
    lifetime simply misses. The cache's key function adds the schema in front (§6.1)."""
    digest = hashlib.sha256(
        "\n".join((key_id, endpoint, bucket, str(seconds), key)).encode()
    ).hexdigest()
    return f"ihr:picture-link:{prefix.strip('/').replace('/', ':')}:{digest}"


def facility_image_url(key):
    """A signed link to one of this hospital's pictures, or None.

    None, without raising, for: no key; the public schema (no hospital: the console, the
    platform host); a key without this hospital's prefix (another hospital's) or not of the
    shape CARE's picture uploads make. Otherwise a link valid ``LINK_SECONDS``, the same one for
    ``CACHE_SECONDS`` (see ``_cache_seconds``)."""
    from care.utils.csp.config import BucketType, get_client_config

    schema = getattr(connection, "schema_name", None)
    if not key or not schema or schema == "public":
        return None
    prefix = tenant_key_prefix()
    match = PICTURE_KEY.fullmatch(key)
    if not match or not key.startswith(prefix) or f"t/{match['hospital']}/" != prefix:
        return None

    config, bucket = get_client_config(BucketType.FACILITY, external=True)
    endpoint = f"{config.get('endpoint_url') or ''}|{config.get('region_name') or ''}"
    name = link_cache_name(
        prefix,
        key,
        key_id=_key_id(config),
        endpoint=endpoint,
        bucket=bucket,
        seconds=LINK_SECONDS,
    )
    if url := cache.get(name):
        return url
    seconds = _cache_seconds(config)
    url = _client(config).generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=LINK_SECONDS
    )
    if seconds > 0:
        cache.set(name, url, seconds)
    return url
