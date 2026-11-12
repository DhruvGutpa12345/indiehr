"""The committed pack files (P1-T15a): no database. Every file matches its sha256 in the
manifest, is written in canonical bytes with one final newline (so pre-commit's fixers never
touch it), and is listed; the care-valuesets slugs are the reviewed list."""

import json
import unittest

from ihr_refdata import manifest
from ihr_refdata.management.commands.ihr_pack_build import expected_slugs

ROOT = manifest.PACKS_DIR


class CommittedPackTests(unittest.TestCase):
    def test_every_file_matches_its_sha256_and_is_canonical(self):
        listed = manifest.read_manifest(ROOT)["releases"]
        self.assertTrue(listed)
        for entry in listed:
            with self.subTest(file=entry["file"]):
                data = (ROOT / entry["file"]).read_bytes()
                self.assertEqual(manifest.sha256_of(data), entry["sha256"])
                self.assertEqual(data, manifest.canonical_bytes(json.loads(data)))
                self.assertTrue(data.endswith(b"}\n"))
                self.assertFalse(data.endswith(b"\n\n"))
                self.assertNotIn(b"\r", data)
        data = (ROOT / manifest.MANIFEST).read_bytes()
        self.assertEqual(data, manifest.canonical_bytes(json.loads(data)))

    def test_every_version_file_is_listed_and_every_release_reads(self):
        listed = {e["file"] for e in manifest.read_manifest(ROOT)["releases"]}
        on_disk = {
            str(path.relative_to(ROOT))
            for path in ROOT.glob("*/*.json")
            if path.stem.isdigit()
        }
        self.assertEqual(on_disk, listed)
        versions = manifest.releases(ROOT)
        for pack, releases in versions.items():
            self.assertEqual(
                [r.version for r in releases], list(range(1, len(releases) + 1)), pack
            )

    def test_the_newest_value_set_pack_ships_exactly_the_reviewed_slugs(self):
        newest = manifest.latest(ROOT)["care-valuesets"]
        self.assertEqual(
            {item["slug"] for item in newest.items},
            expected_slugs(ROOT, "care-valuesets"),
        )
        self.assertEqual(len(newest.care_commit), 40)
