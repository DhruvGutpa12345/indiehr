"""India Post's PIN code directory: read the data.gov.in CSV and store it (P1-T7a).

Source: "All India Pincode Directory" on data.gov.in (Department of Posts), Government Open
Data License - India, which asks for attribution. The file is downloaded by a person (the
site wants a name, e-mail and captcha) and loaded with ``manage.py ihr_load_pincodes``.
Lookups are answered from our own database; nothing is fetched at run time.
"""

import csv
import hashlib
import io
import re
from dataclasses import dataclass, field

from django.db import transaction
from ihr_tenant_settings.india import GST_STATE_CODES
from ihr_tenant_settings.validators import PIN_RE

# Columns we need, by their data.gov.in names (headers are matched case-insensitively).
REQUIRED_COLUMNS = ("pincode", "officename", "district", "statename")
# Names older releases of the directory use for the same columns.
COLUMN_ALIASES = {"districtname": "district", "deliverystatus": "delivery"}

# State names as written in the directory's past and present releases -> GST state code.
STATE_ALIASES = {
    "ANDAMAN AND NICOBAR": "35",
    "ANDAMAN NICOBAR ISLANDS": "35",
    "CHATTISGARH": "22",
    "DADRA AND NAGAR HAVELI": "26",
    "DAMAN AND DIU": "26",
    "DELHI NCT": "07",
    "NCT OF DELHI": "07",
    "ORISSA": "21",
    "PONDICHERRY": "34",
    "UTTARANCHAL": "05",
}
_STATE_CODES = {name.upper(): code for code, name in GST_STATE_CODES.items()}
del _STATE_CODES["OTHER TERRITORY"]


def _key(name):
    name = re.sub(r"[^A-Z0-9]+", " ", name.upper().replace("&", " AND ")).strip()
    return re.sub(r"^THE ", "", name)


def state_code(name):
    """The GST state code for a state name as the directory writes it, or None."""
    key = _key(name)
    return _STATE_CODES.get(key) or STATE_ALIASES.get(key)


def district_name(name):
    """Directory districts are in capitals; store them as names are written elsewhere:
    "Medchal-Malkajgiri", "K.V.Rangareddy", "Bijapur(Kar)". Single letters (initials such
    as "Y.S.R.") stay in capitals."""
    name = re.sub(r"\s+", " ", name).strip()
    return re.sub(r"[^\W\d_]+", lambda m: m.group().capitalize(), name)


@dataclass
class Parsed:
    offices: list = field(default_factory=list)  # dicts of PostOffice fields
    unknown_states: dict = field(default_factory=dict)  # name -> row count
    skipped: list = field(default_factory=list)  # (line number, reason)
    duplicates: int = 0


def parse(data):
    """Read the CSV bytes. Rows with a bad PIN or no district are skipped (and reported);
    any state name we cannot place is collected, and ``load`` then refuses the file."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode(
            "cp1252", errors="replace"
        )  # older exports from spreadsheets
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        msg = "The file is empty."
        raise ValueError(msg)
    columns = {}
    for name in reader.fieldnames:
        key = (name or "").strip().lower()
        columns.setdefault(COLUMN_ALIASES.get(key, key), name)
    missing = [c for c in REQUIRED_COLUMNS if c not in columns]
    if missing:
        msg = f"Not the PIN directory: missing column(s) {', '.join(missing)}."
        raise ValueError(msg)

    def get(row, column):
        return (row.get(columns.get(column, ""), "") or "").strip()

    parsed, seen = Parsed(), set()
    for line, row in enumerate(reader, start=2):
        pincode, state = get(row, "pincode"), get(row, "statename")
        district = get(row, "district")
        if not PIN_RE.match(pincode):
            parsed.skipped.append((line, f"PIN code {pincode[:10]!r}"))
            continue
        if not district or district.upper() in {"NA", "N/A"}:
            parsed.skipped.append((line, "no district"))
            continue
        code = state_code(state)
        if code is None:
            parsed.unknown_states[state] = parsed.unknown_states.get(state, 0) + 1
            continue
        office = {
            "pincode": pincode,
            "name": get(row, "officename")[:255],
            "office_type": get(row, "officetype")[:8],
            "delivery": not get(row, "delivery").lower().startswith("non"),
            "district": district_name(district)[:100],
            "state_code": code,
        }
        # Rows that differ only in columns we do not keep (coordinates, division) are one office.
        key = tuple(office.values())
        if key in seen:
            parsed.duplicates += 1
            continue
        seen.add(key)
        parsed.offices.append(office)
    return parsed


# A new release may lose a few PIN codes, never this many: a smaller file is most likely a
# one-state extract, which would leave every other state without districts.
MIN_SHARE_OF_PREVIOUS = 0.9


def load(data, source_name, released_on=None, *, force=False):
    """Replace the stored directory with this file, in one transaction. Returns the
    ``PinDirectoryLoad`` row and the parse result. Raises ValueError, changing nothing, if
    the file is not the directory, names a state we cannot place, holds no offices, or
    (unless ``force``) covers fewer states or far fewer PIN codes than the stored one."""
    from ihr_platform.models import PinDirectoryLoad, PostOffice

    parsed = parse(data)
    if parsed.unknown_states:
        names = ", ".join(
            f"{name!r} ({count} rows)"
            for name, count in sorted(parsed.unknown_states.items())
        )
        msg = f"Unknown state name(s): {names}. Add them to STATE_ALIASES."
        raise ValueError(msg)
    if not parsed.offices:
        msg = "The file has no usable post offices."
        raise ValueError(msg)
    states = {o["state_code"] for o in parsed.offices}
    pins = len({o["pincode"] for o in parsed.offices})
    previous = current()
    if previous is not None and not force:
        stored = set(PostOffice.objects.values_list("state_code", flat=True).distinct())
        if missing := stored - states:
            names = ", ".join(sorted(GST_STATE_CODES[c] for c in missing))
            msg = f"The file has no offices in {names}, which the stored directory has. Is it the whole directory? (--force to load anyway)"
            raise ValueError(msg)
        if pins < previous.pincodes * MIN_SHARE_OF_PREVIOUS:
            msg = f"The file has {pins} PIN codes, the stored directory {previous.pincodes}. Is it the whole directory? (--force to load anyway)"
            raise ValueError(msg)
    with transaction.atomic():
        PostOffice.objects.all().delete()
        PostOffice.objects.bulk_create(
            (PostOffice(**office) for office in parsed.offices), batch_size=5000
        )
        record = PinDirectoryLoad.objects.create(
            source_name=source_name[:255],
            sha256=hashlib.sha256(data).hexdigest(),
            released_on=released_on,
            offices=len(parsed.offices),
            pincodes=pins,
            skipped=len(parsed.skipped),
        )
    return record, parsed


def current():
    """The load the stored offices came from, or None if the directory was never loaded."""
    from ihr_platform.models import PinDirectoryLoad

    return PinDirectoryLoad.objects.first()
