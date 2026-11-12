"""Reference pack files and their manifest (P1-T15a).

    packs/MANIFEST.json            {"about": ..., "releases": [{pack, version, file, sha256}]}
    packs/<pack>/<version>.json    {pack, version, kind, care_commit, item_count, items: [...]}

Version files are built by ``ihr_pack_build`` (never by hand), written as canonical bytes and
never changed once committed (a pre-commit hook refuses a modified one; a test checks every
sha256). Every file is checked against the manifest before anything is applied.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

PACKS_DIR = Path(__file__).with_name("packs")
MANIFEST = "MANIFEST.json"
KINDS = ("valueset",)


class PackError(Exception):
    """A fault in the pack files themselves (the same for every hospital)."""


def packs_dir():
    """The folder holding MANIFEST.json (a setting, so tests can use their own packs)."""
    return Path(getattr(settings, "IHR_REFDATA_PACKS_DIR", None) or PACKS_DIR)


def canonical_bytes(obj):
    """The only byte form a pack file or manifest is written in: pre-commit's whitespace and
    end-of-file fixers leave it as it is, so its sha256 never goes stale."""
    return (
        json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n"
    ).encode()


def sha256_of(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Release:
    pack: str
    version: int
    sha256: str
    file: str
    kind: str
    care_commit: str
    items: tuple  # dicts, sorted by "slug"

    @property
    def item_count(self):
        return len(self.items)

    def by_item(self):
        return {item["slug"]: item for item in self.items}


def read_manifest(root=None):
    path = (root or packs_dir()) / MANIFEST
    try:
        manifest = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        msg = f"cannot read {path}: {exc}"
        raise PackError(msg) from None
    if not isinstance(manifest.get("releases"), list):
        msg = f"{path}: no list of releases"
        raise PackError(msg)
    return manifest


def _check_release(entry, data, path):
    if (data.get("pack"), data.get("version")) != (entry["pack"], entry["version"]):
        msg = f"{path} names {data.get('pack')} v{data.get('version')}, not the manifest's"
        raise PackError(msg)
    if data.get("kind") not in KINDS:
        msg = f"{path}: unknown kind {data.get('kind')!r}"
        raise PackError(msg)
    items = data.get("items")
    if not isinstance(items, list) or data.get("item_count") != len(items):
        msg = f"{path}: items and item_count disagree"
        raise PackError(msg)
    slugs = [item.get("slug") for item in items]
    if slugs != sorted(set(slugs)) or not all(isinstance(s, str) and s for s in slugs):
        msg = f"{path}: items must have unique slugs, sorted"
        raise PackError(msg)


def releases(root=None):
    """Every release in the manifest, each file checked against its sha256: {pack: [Release,
    oldest first]}. Any mismatch refuses them all (PackError)."""
    root = root or packs_dir()
    found = {}
    for entry in read_manifest(root)["releases"]:
        if not (
            isinstance(entry.get("pack"), str)
            and isinstance(entry.get("version"), int)
            and entry["version"] > 0
            and isinstance(entry.get("file"), str)
            and isinstance(entry.get("sha256"), str)
        ):
            msg = f"bad manifest entry: {entry!r}"
            raise PackError(msg)
        path = (root / entry["file"]).resolve()
        if root.resolve() not in path.parents:
            msg = f"manifest entry outside the packs folder: {entry['file']}"
            raise PackError(msg)
        try:
            data = path.read_bytes()
        except OSError as exc:
            msg = f"cannot read {entry['file']}: {exc}"
            raise PackError(msg) from None
        if sha256_of(data) != entry["sha256"]:
            msg = (
                f"{entry['file']} does not match its sha256 in the manifest: pack files "
                "are never changed once released (build a new version instead)"
            )
            raise PackError(msg)
        content = json.loads(data)
        _check_release(entry, content, entry["file"])
        release = Release(
            pack=entry["pack"],
            version=entry["version"],
            sha256=entry["sha256"],
            file=entry["file"],
            kind=content["kind"],
            care_commit=content.get("care_commit", ""),
            items=tuple(content["items"]),
        )
        versions = found.setdefault(release.pack, [])
        if any(r.version == release.version for r in versions):
            msg = f"{release.pack} v{release.version} is listed twice"
            raise PackError(msg)
        versions.append(release)
    for versions in found.values():
        versions.sort(key=lambda r: r.version)
    return found


def latest(root=None):
    """The newest release of each pack (the only one ever applied): {pack: Release}."""
    return {pack: versions[-1] for pack, versions in releases(root).items()}
