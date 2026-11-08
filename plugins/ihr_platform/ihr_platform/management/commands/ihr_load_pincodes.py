"""Load India Post's PIN code directory, downloaded from data.gov.in (P1-T7a).

    manage.py ihr_load_pincodes /path/all-india-pincode-directory.csv --released 01-10-2026
    manage.py ihr_load_pincodes --show

Replaces the stored directory in one transaction. Then run ``ihr_load_geography --all`` so
every hospital has the states and districts it names.
"""

import re
from datetime import date
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ihr_platform import pincodes

MAX_BYTES = 64 * 1024 * 1024  # the real directory is about 20 MB; parsed in memory
SHOW_SKIPPED = 10


def _date(value):
    match = re.fullmatch(r"(\d{2})-(\d{2})-(\d{4})", value)
    try:
        day, month, year = (int(part) for part in match.groups())
        return date(year, month, day)
    except (AttributeError, ValueError) as e:
        msg = f"--released must be DD-MM-YYYY, not {value!r}"
        raise CommandError(msg) from e


class Command(BaseCommand):
    help = "Load the All India Pincode Directory CSV (data.gov.in)."

    def add_arguments(self, parser):
        parser.add_argument("csv", nargs="?", help="The CSV file from data.gov.in")
        parser.add_argument(
            "--released", help="The date data.gov.in shows for the file (DD-MM-YYYY)"
        )
        parser.add_argument(
            "--source-name", help="Name to record for the file (default: its file name)"
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Load even if the file covers fewer states or PIN codes than the stored one",
        )
        parser.add_argument(
            "--show", action="store_true", help="Show the loaded directory"
        )

    def handle(self, *args, csv, released, source_name, force, show, **options):
        if show or not csv:
            loaded = pincodes.current()
            if loaded is None:
                self.stdout.write("No PIN directory loaded.")
            else:
                self.stdout.write(
                    f"{loaded.source_name}: {loaded.pincodes} PIN codes, "
                    f"{loaded.offices} post offices, released {loaded.released_on or '?'}, "
                    f"loaded {loaded.loaded_at:%d-%m-%Y %H:%M} UTC, sha256 {loaded.sha256}"
                )
            return
        path = Path(csv)
        if not path.is_file():
            msg = f"No such file: {path}"
            raise CommandError(msg)
        if path.stat().st_size > MAX_BYTES:
            msg = f"{path} is larger than {MAX_BYTES // 2**20} MB"
            raise CommandError(msg)
        try:
            record, parsed = pincodes.load(
                path.read_bytes(),
                source_name or path.name,
                _date(released) if released else None,
                force=force,
            )
        except ValueError as e:
            raise CommandError(str(e)) from e
        for line, reason in parsed.skipped[:SHOW_SKIPPED]:
            self.stdout.write(f"skipped line {line}: {reason}")
        if len(parsed.skipped) > SHOW_SKIPPED:
            self.stdout.write(f"... {len(parsed.skipped) - SHOW_SKIPPED} more skipped")
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {record.pincodes} PIN codes ({record.offices} post offices, "
                f"{record.skipped} rows skipped, {parsed.duplicates} repeated rows "
                f"merged) in {len({o['state_code'] for o in parsed.offices})} states "
                f"from {record.source_name}."
            )
        )
