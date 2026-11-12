"""Build a new version of a reference pack from CARE's own code (P1-T15a).

    scripts/pack-build.sh care-valuesets [--allow-removal <slug> ...]   (on the host)
    manage.py ihr_pack_build care-valuesets --care-commit <sha> --out <writable folder>
    manage.py ihr_pack_build care-valuesets --check

The pack folder is read-only where Django runs, so the new version file and the updated
MANIFEST.json are written to --out; scripts/pack-build.sh puts them in place. A new version
is written only when the items differ from the newest one. The slugs must be exactly those in
the reviewed packs/<pack>/expected_slugs.txt, and a slug that leaves the pack must be named
with --allow-removal (it is then retired in hospitals, never deleted).

--check exits 1 when CARE's registry differs from the newest committed version (items only,
never the CARE commit): an upstream sync that changes a system value set fails CI until a new
version is built and committed.
"""

import re
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ihr_refdata import manifest, valuesets

BUILDERS = {"care-valuesets": (valuesets.KIND, valuesets.care_registry)}
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
ABOUT = (
    "Reference packs (P1-T15a). Version files are built by scripts/pack-build.sh, never by "
    "hand, and never changed once committed. Applied by manage.py ihr_pack_apply."
)


def expected_slugs(root, pack):
    path = root / pack / "expected_slugs.txt"
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        msg = f"cannot read {path}: {exc}"
        raise CommandError(msg) from None
    return {line.split("#")[0].strip() for line in lines if line.split("#")[0].strip()}


class Command(BaseCommand):
    help = "Build a new version of a reference pack from CARE's code."
    # Reads code only (no database; scripts/pack-build.sh runs it without a network). The
    # URLconf the system checks would import is imported by the builder itself.
    requires_system_checks = []

    def create_parser(self, prog_name, subcommand, **kwargs):
        parser = super().create_parser(prog_name, subcommand, **kwargs)
        parser.allow_abbrev = (
            False  # --care or --ou never stand for --care-commit / --out (S9)
        )
        return parser

    def add_arguments(self, parser):
        parser.add_argument("pack", choices=sorted(BUILDERS))
        parser.add_argument("--check", action="store_true")
        parser.add_argument("--out", help="Writable folder for the new files")
        parser.add_argument("--care-commit", help="CARE's commit (upstream.lock)")
        parser.add_argument(
            "--allow-removal",
            action="append",
            default=[],
            metavar="SLUG",
            help="A slug CARE no longer ships (repeat for each)",
        )

    def handle(self, *args, pack, check, out, care_commit, allow_removal, **options):
        root = manifest.packs_dir()
        kind, build = BUILDERS[pack]
        try:
            items = build()
        except ValueError as exc:
            raise CommandError(str(exc)) from None
        slugs = {item["slug"] for item in items}
        expected = expected_slugs(root, pack)
        if slugs != expected:
            msg = (
                f"CARE's {pack} slugs differ from packs/{pack}/expected_slugs.txt: "
                f"missing {sorted(expected - slugs)}, new {sorted(slugs - expected)}. "
                "Review the change, then update the list."
            )
            raise CommandError(msg)
        try:
            newest = manifest.latest(root).get(pack)
        except manifest.PackError as exc:
            raise CommandError(str(exc)) from None
        same = newest is not None and list(newest.items) == items
        if check:
            if not same:
                where = f"v{newest.version}" if newest else "no version yet"
                msg = (
                    f"CARE's registry differs from {pack} {where}: build a new version "
                    "(scripts/pack-build.sh) and commit it"
                )
                raise CommandError(msg)
            self.stdout.write(f"{pack} v{newest.version} matches CARE's registry")
            return
        if same:
            self.stdout.write(f"{pack} v{newest.version} is current: nothing built")
            return
        if not care_commit or not SHA_RE.match(care_commit):
            raise CommandError("--care-commit must be CARE's 40-character commit")
        if not out:
            raise CommandError("--out is required (the pack folder is read-only here)")
        removed = sorted(
            {item["slug"] for item in newest.items} - slugs if newest else set()
        )
        if unexplained := sorted(set(removed) - set(allow_removal)):
            msg = (
                f"{pack} would drop {unexplained}: name each with --allow-removal "
                "after review (they are retired in hospitals, never deleted)"
            )
            raise CommandError(msg)
        version = newest.version + 1 if newest else 1
        content = {
            "pack": pack,
            "version": version,
            "kind": kind,
            "care_commit": care_commit,
            "item_count": len(items),
            "items": items,
        }
        if removed:
            content["removed"] = removed
        data = manifest.canonical_bytes(content)
        name = f"{pack}/{version}.json"
        target = Path(out) / name
        if target.exists():
            msg = f"{target} exists: version files are never overwritten"
            raise CommandError(msg)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        listing = manifest.read_manifest(root)
        listing["about"] = listing.get("about") or ABOUT
        listing["releases"] = sorted(
            [
                *listing["releases"],
                {
                    "pack": pack,
                    "version": version,
                    "file": name,
                    "sha256": manifest.sha256_of(data),
                },
            ],
            key=lambda entry: (entry["pack"], entry["version"]),
        )
        (Path(out) / manifest.MANIFEST).write_bytes(manifest.canonical_bytes(listing))
        self.stdout.write(
            f"built {name}: {len(items)} items, sha256 {manifest.sha256_of(data)}"
        )
