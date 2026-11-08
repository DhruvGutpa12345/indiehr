#!/usr/bin/env python3
# ruff: noqa: T201, PTH123, FURB162, PLR0912 (a standalone script for auditors; Python 3.10 reads no "Z")
"""Check an audit trail export (JSON Lines) from IndieHR. Python 3.10+, standard library only.

    python3 verify_export.py audit-<hospital>-<from>-to-<to>.jsonl

Without any access to the hospital's systems it recomputes:
  1. each exported entry's hash from its details (never its values, only their salted hash);
  2. each sealed window's hash from every entry in it, and checks that no entry of the
     export's range (or patient) is given only as a hash;
  3. that the windows given in full run from before the range starts (or from the very first
     seal) to after it ends, with nothing left out in between;
  4. each seal's hash from the previous one, on to a fingerprint kept outside the hospital's
     database in write-once storage (ask the platform for the object named to compare).

Exit status:
  0  everything matches and the whole range is tied to an outside fingerprint
  3  nothing contradicts, but part of the export is not proven (listed under "NOT PROVEN":
     for example the newest hour, not fingerprinted yet; export it again later)
  1  something does not match: treat the export as altered
"""

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

TAGS = {1: "", 2: "v2|", 3: "v3|"}
DETAILED = {3}  # seal formats whose lines carry their time, patients and details hash
GENESIS = "0" * 64
ANCHOR_WITHIN = timedelta(hours=3)  # fingerprints are hourly; sealing takes a minute


def _sha(data):
    return hashlib.sha256(data).digest()


def _time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def seal_hash(prev, seal, entries_hash):
    head = f"{TAGS[seal['version']]}{seal['seq']}|{seal['lower']}|{seal['upper']}|{seal['entries']}"
    return _sha(prev + head.encode() + entries_hash)


class Result:
    def __init__(self):
        self.problems, self.gaps, self.notes, self.entries = [], [], [], []

    @property
    def status(self):
        return 1 if self.problems else 3 if self.gaps else 0


class Export:
    def __init__(self):
        self.result = Result()
        self.header = self.anchor = self.end = None
        self.seals = []
        self.lines = {}  # seal seq (None: unsealed) -> [(leaf bytes, [at, patients, hash])]
        self.exported_lines = 0
        self.ends_after_range = False
        self.patient_ids = {}  # internal id -> external id, as the platform gave them

    def problem(self, text):
        self.result.problems.append(text)

    def in_export(self, outer):
        at, patients = _time(outer[0]), outer[1] or []
        start, end = _time(self.header["from"]), _time(self.header["to_before"])
        patient = self.header.get("patient")
        return start <= at < end and (patient is None or patient in patients)

    def line(self, number, item):
        kind = item.get("type")
        if self.end is not None:
            self.problem(f"line {number}: comes after the end line")
        elif kind != "export" and self.header is None:
            self.problem(f"line {number}: comes before the export header")
            return
        if kind == "export":
            if self.header is not None:
                self.problem(f"line {number}: a second export header")
                return
            if not (
                item.get("patient") is None or isinstance(item.get("patient"), int)
            ):
                self.problem(f"line {number}: the patient filter is not an id")
            self.header = item
        elif kind == "seal":
            self.seals.append(item)
        elif kind in ("entry", "leaf"):
            self.entry_line(number, kind, item)
        elif kind == "anchor":
            if self.anchor is not None:
                self.problem(f"line {number}: a second fingerprint line")
            self.anchor = item
        elif kind == "end":
            self.end = item
        else:
            self.problem(f"line {number}: unknown line type {kind!r}")

    def entry_line(self, number, kind, item):
        if self.header is None:
            self.problem(f"line {number}: entry before the export header")
            return
        text = item.get("canonical")
        leaf = item.get("leaf", "")
        outer = None
        if text is not None:
            if _sha(text.encode()).hex() != leaf:
                self.problem(f"line {number}: does not match its hash")
                return
            outer = json.loads(text)
            if self.in_export(outer) != (kind == "entry"):
                self.problem(
                    f"line {number}: entry outside the export"
                    if kind == "entry"
                    else f"line {number}: an entry of the export is given only as a hash"
                )
            if kind == "entry":
                inner = item.get("inner") or ""
                if _sha(inner.encode()).hex() != outer[2]:
                    self.problem(f"line {number}: details do not match the entry")
                else:
                    self.result.entries.append([outer[0], outer[1], *json.loads(inner)])
                self.check_patient_ids(
                    number, outer[1] or [], item.get("patients") or []
                )
        if kind == "entry":
            self.exported_lines += 1
        self.lines.setdefault(item.get("seal"), []).append((bytes.fromhex(leaf), outer))

    def check_patient_ids(self, number, internal, external):
        """The external patient ids next to an entry are the platform's word, not hashed:
        they must at least line up with the hashed internal ids, the same way everywhere."""
        if len(internal) != len(external):
            self.problem(f"line {number}: patient ids do not line up with the entry")
            return
        for pid, ext in zip(internal, external, strict=True):
            if self.patient_ids.setdefault(pid, ext) != ext:
                self.problem(f"line {number}: patient {pid} is named two ways")

    def finish(self):
        if self.header is None or self.header.get("format") != "ihr-audit-export/1":
            self.problem("not an IndieHR audit export (format ihr-audit-export/1)")
            return
        if self.end is None:
            self.problem("the export is cut short (no end line)")
        elif self.end.get("entries") != self.exported_lines:
            self.problem("the number of entries does not match the end line")
        unsealed = self.lines.pop(None, [])
        if any(outer is None for _, outer in unsealed):
            self.problem("an entry not sealed yet comes without its details")
        if unsealed:
            self.result.notes.append(
                f"{len(unsealed)} entries are not sealed yet: only their own hash is "
                "checked"
            )
        old = sum(
            1
            for seal in self.seals
            if seal["leaves"] and seal["version"] not in DETAILED
            for _ in self.lines.get(seal["seq"], [])
        )
        if old:
            self.result.gaps.append(
                f"{old} entries were sealed before format 3: given only as hashes, so "
                "whether they belong to the export is not proven"
            )
        self.check_seals()
        self.check_edges()
        self.check_anchor()

    def check_seals(self):
        full = {s["seq"] for s in self.seals if s["leaves"]}
        for seq in sorted(set(self.lines) - full):
            self.problem(f"entries name seal {seq}, which has no full header")
        prev = None
        for seal in self.seals:
            where = f"seal {seal['seq']}"
            if seal["version"] not in TAGS:
                self.problem(f"{where}: unknown format {seal['version']}")
                continue
            if prev is not None:
                if seal["seq"] != prev["seq"] + 1:
                    self.problem(f"{where}: does not follow seal {prev['seq']}")
                if seal["lower"] != prev["upper"]:
                    self.problem(f"{where}: window does not continue the previous one")
                if seal["prev_hash"] != prev["hash"]:
                    self.problem(f"{where}: does not link to the previous seal")
                if seal["leaves"] and not prev["leaves"]:
                    self.problem(f"{where}: full seal after a header-only one")
            entries_hash = bytes.fromhex(seal["entries_hash"])
            if seal["leaves"]:
                got = self.lines.get(seal["seq"], [])
                if len(got) != seal["entries"]:
                    self.problem(
                        f"{where}: {seal['entries']} entries, {len(got)} given"
                    )
                if _sha(b"".join(leaf for leaf, _ in got)) != entries_hash:
                    self.problem(f"{where}: entries do not match the seal")
                if seal["version"] in DETAILED and any(o is None for _, o in got):
                    self.problem(
                        f"{where}: a line of this format comes without its time"
                    )
            recomputed = seal_hash(bytes.fromhex(seal["prev_hash"]), seal, entries_hash)
            if recomputed.hex() != seal["hash"]:
                self.problem(f"{where}: hash does not match")
            prev = seal

    def _wholly(self, seal, side):
        """True when every entry of this full window lies before (``side`` "before") or
        after the export's range. Older formats do not say: False."""
        if seal["version"] not in DETAILED:
            return False
        got = [o for _, o in self.lines.get(seal["seq"], []) if o is not None]
        start, end = _time(self.header["from"]), _time(self.header["to_before"])
        if side == "before":
            return all(_time(o[0]) < start for o in got)
        return all(_time(o[0]) >= end for o in got)

    def check_edges(self):
        full = [s for s in self.seals if s["leaves"]]
        if not full:
            return
        first, last = full[0], full[-1]
        if not (
            first["seq"] == 1 and first["prev_hash"] == GENESIS
        ) and not self._wholly(first, "before"):
            self.result.gaps.append(
                f"the full windows do not start before the range (seal {first['seq']}): "
                "earlier entries of the range may be missing"
            )
        self.ends_after_range = self._wholly(last, "after")
        if len(full) < len(self.seals) and not self.ends_after_range:
            start, end = _time(self.header["from"]), _time(self.header["to_before"])
            got = [o for _, o in self.lines.get(last["seq"], []) if o is not None]
            if any(start <= _time(o[0]) < end for o in got):
                self.problem(
                    f"seal {last['seq']} is followed by header-only seals but still holds "
                    "entries of the range"
                )
            else:
                self.result.gaps.append(
                    f"seal {last['seq']} is the last full window but holds no entry after "
                    "the range: later entries of the range are not proven absent"
                )

    def check_anchor(self):
        anchor = self.anchor
        if anchor is None:
            self.problem("no fingerprint line")
            return
        if "missing" in anchor:
            self.result.gaps.append(
                f"not tied to an outside fingerprint: {anchor['missing']}"
            )
            return
        tied = [s for s in self.seals if s["seq"] == anchor["seq"]]
        if not tied or tied[0]["hash"] != anchor["hash"]:
            self.problem("no seal of the export matches the outside fingerprint")
            return
        later = [s for s in self.seals if s["seq"] > anchor["seq"]]
        if later:
            self.result.gaps.append(
                f"seals {later[0]['seq']}-{later[-1]['seq']} came after the fingerprint "
                f"of {anchor['written_at']}: not tied yet (export again later)"
            )
        elif not self.ends_after_range:
            self.result.gaps.append(
                "the export reaches its newest seal, so entries after it are not proven "
                "absent: export again after the range has ended and been fingerprinted"
            )
        full = [s for s in self.seals if s["leaves"]]
        newest = (
            [
                _time(o[0])
                for _, o in self.lines.get(full[-1]["seq"], [])
                if o is not None
            ]
            if full
            else []
        )
        written = _time(anchor.get("stored_at") or anchor["written_at"])
        if newest and written - max(newest) > ANCHOR_WITHIN:
            hours = (written - max(newest)).total_seconds() / 3600
            self.result.gaps.append(
                f"the fingerprint was written {hours:.1f} hours after the last window: a "
                "rewrite in between would not show"
            )
        self.result.notes.append(
            "tied to the outside fingerprint: ask the platform for object "
            f"{anchor['key']} version {anchor.get('version')} (stored "
            f"{anchor.get('stored_at')}) and check that it gives hospital "
            f"{anchor.get('hospital')} seal {anchor['seq']} hash {anchor['hash']}"
        )


def check(lines, export=None):
    """A Result: problems (altered), gaps (not proven), notes, and the exported entries as
    [at, patients, id, txid, kind, actor, context, table, op, row, values hash]."""
    export = export or Export()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except ValueError:
            export.problem(f"line {number}: not JSON")
            continue
        try:
            export.line(number, item)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            export.problem(f"line {number}: malformed ({exc!r})")
    try:
        export.finish()
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        export.problem(f"malformed export ({exc!r})")
    return export.result


def _request_gaps(result, header, asked):
    """What the export says it covers against what the auditor asked for."""
    if header is None:
        return
    said = {
        "hospital": (header.get("hospital") or {}).get("uuid"),
        "from": header.get("from"),
        "to": header.get("to_before"),
        "patient": header.get("patient_id"),
    }
    if not any(asked.values()):
        result.gaps.append(
            "compare the hospital, range and patient above with your request "
            "(or pass --hospital, --from, --to and --patient to check them)"
        )
        return
    for name, value in asked.items():
        if value is None:
            result.gaps.append(f"--{name} not given: not checked")
            continue
        if name in ("from", "to"):
            day = _time(said[name]).astimezone(IST)
            if name == "to":
                day -= timedelta(microseconds=1)  # to_before is exclusive
            ok = day.strftime("%Y-%m-%d") == value
        else:
            ok = str(said[name]) == value
        if not ok:
            result.problems.append(f"the export's {name} is {said[name]}, not {value}")


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("export")
    parser.add_argument("--hospital", help="the hospital's id you asked about")
    parser.add_argument(
        "--from", dest="from_", help="first day asked for (YYYY-MM-DD, IST)"
    )
    parser.add_argument("--to", help="last day asked for (YYYY-MM-DD, IST)")
    parser.add_argument("--patient", help="the patient's id you asked about, if any")
    args = parser.parse_args(argv[1:])
    with open(args.export, encoding="utf-8") as handle:
        export = Export()
        result = check(handle, export)
    header = export.header or {}
    hospital = header.get("hospital") or {}
    print(f"hospital: {hospital.get('slug')} ({hospital.get('uuid')})")
    if header:
        start = _time(header["from"]).astimezone(IST)
        end = _time(header["to_before"]).astimezone(IST)
        print(
            f"range:    {start:%d-%m-%Y %H:%M:%S} to before {end:%d-%m-%Y %H:%M:%S} IST"
        )
    print(f"patient:  {header.get('patient_id') or 'every patient'}")
    _request_gaps(
        result,
        export.header,
        {
            "hospital": args.hospital,
            "from": args.from_,
            "to": args.to,
            "patient": args.patient,
        },
    )
    print(f"{len(result.entries)} exported entries checked")
    for note in result.notes:
        print(f"note: {note}")
    for gap in result.gaps:
        print(f"NOT PROVEN: {gap}")
    for problem in result.problems:
        print(f"PROBLEM: {problem}")
    print(
        {0: "export OK", 3: "export NOT FULLY PROVEN", 1: "export FAILED"}[
            result.status
        ]
    )
    return result.status


if __name__ == "__main__":
    sys.exit(main(sys.argv))
