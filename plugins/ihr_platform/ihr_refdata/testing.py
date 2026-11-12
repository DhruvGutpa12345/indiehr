"""Test helpers for reference packs: a throwaway packs folder, value set items, the trail."""

import json
import shutil
import tempfile
from pathlib import Path

from django.db import connection
from django.test import override_settings

from ihr_refdata import manifest

TEST_PACK = "test-pack"
CARE_COMMIT = "0" * 40


def vs(slug, *codes, name=None, status="active"):
    """A value set item as ihr_pack_build writes it (compose as CARE's sync stores it)."""
    return {
        "slug": slug,
        "name": name or slug.replace("-", " ").title(),
        "status": status,
        "compose": {
            "include": [
                {
                    "system": "http://loinc.org",
                    "concept": [{"code": code} for code in codes or ("1-8",)],
                }
            ],
            "exclude": [],
        },
        "is_system_defined": True,
    }


class Packs:
    """A packs folder of our own, in use (IHR_REFDATA_PACKS_DIR) for the rest of a test."""

    def __init__(self, testcase):
        self.root = Path(tempfile.mkdtemp(prefix="ihr-packs-"))
        testcase.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.entries = []
        self._write_manifest(self.entries)
        testcase.enterContext(override_settings(IHR_REFDATA_PACKS_DIR=str(self.root)))

    def add(self, version, *items, pack=TEST_PACK):
        content = {
            "pack": pack,
            "version": version,
            "kind": "valueset",
            "care_commit": CARE_COMMIT,
            "item_count": len(items),
            "items": sorted(items, key=lambda item: item["slug"]),
        }
        data = manifest.canonical_bytes(content)
        name = f"{pack}/{version}.json"
        (self.root / pack).mkdir(exist_ok=True)
        (self.root / name).write_bytes(data)
        self.entries = [
            e for e in self.entries if (e["pack"], e["version"]) != (pack, version)
        ]
        self.entries.append(
            {
                "pack": pack,
                "version": version,
                "file": name,
                "sha256": manifest.sha256_of(data),
            }
        )
        self._write_manifest(self.entries)

    def only(self, *versions, pack=TEST_PACK):
        """List just these versions in the manifest (an older image's packs)."""
        self._write_manifest(
            [e for e in self.entries if e["pack"] != pack or e["version"] in versions]
        )

    def _write_manifest(self, entries):
        (self.root / manifest.MANIFEST).write_bytes(
            manifest.canonical_bytes({"about": "test packs", "releases": entries})
        )


def audit_mark():
    with connection.cursor() as cursor:
        cursor.execute("SELECT coalesce(max(id), 0) FROM ihr_audit_entry")
        return cursor.fetchone()[0]


def audit_since(mark, tbl=None):
    query = "SELECT tbl, op, actor, context, data FROM ihr_audit_entry WHERE id > %s"
    params = [mark]
    if tbl:
        query += " AND tbl = %s"
        params.append(tbl)
    with connection.cursor() as cursor:
        cursor.execute(query + " ORDER BY id", params)
        rows = []
        for tbl_name, op, actor, context, data in cursor.fetchall():
            context = json.loads(context) if isinstance(context, str) else context  # noqa: PLW2901
            rows.append(
                {
                    "tbl": tbl_name,
                    "op": op,
                    "actor": actor,
                    "context": context,
                    "data": data,
                }
            )
        return rows
