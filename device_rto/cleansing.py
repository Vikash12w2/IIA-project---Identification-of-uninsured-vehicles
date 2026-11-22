"""Automatic data cleansing, run INSIDE each source device (ANPR, Insurance, RTO, Theft/Shredding).

Every device keeps its raw table exactly as entered and a cleaned copy next to it in the same database
(<table>_clean). Cleaning is automatic and uses the data of ALL devices as evidence:

  1. plate       a wrong plate is corrected only when the other databases show it is a misread (vehicle colour /
                 type / make-model, policy / FIR / certificate / chassis / engine numbers, a look-alike character
                 in the wrong slot, the plate dictionary as supporting evidence). A plate registered in RTO is never
                 changed; a valid plate that is not registered is kept as it is unless the evidence says misread.
  2. duplicates  ANPR double entries (same plate, time and camera) are removed. Registrations with the same chassis
                 and engine, policies with the same number and insurer, theft reports with the same FIR and shredding
                 records of the same vehicle are merged: the record added later wins, gaps are filled from older ones.
  3. validity    records that are impossible or contradict the other databases are left out, with the reason.
                 An ANPR capture is a real sighting: junk or contradicting vehicle details only flag it.

Built for large tables: triggers on the raw tables log every change (GUI or command-line script), and a pass
re-cleans only the records a change can affect (same plate, look-alike plates through an index, the duplicate
group). The other devices are read as deltas: every cleaned row carries a version, a device asks for the rows
changed since the version it saw last. Evidence is their cleaned data BEFORE their validity filter, so one device's
rule can never switch another device's rule on and off. A device tells the others only when its own result changed.
If a device is down nothing is cleaned: the change waits in pending and is cleaned automatically once every device
answers again (checked every CHECK_SECONDS).

ANPR also reports flagged vehicles to MoT, each vehicle state once.
When the devices run on separate machines, copy this file next to each service (like plate_rules.py).
"""
import json
import os
import re
import sqlite3
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from html import escape

import plate_rules as rules
from insurance_rules import evaluate_insurance

TABLES = {
    "ANPR": {"table": "vehicle_capture", "id": "capture_id", "plate": "plate_number", "label": "capture",
             "path": "/clean/vehicle"},
    "RTO": {"table": "registration", "id": "reg_id", "plate": "reg_number", "label": "registration",
            "path": "/clean/vehicle"},
    "Insurance": {"table": "policy", "id": "policy_id", "plate": "vehicle_reg_no", "label": "policy",
                  "path": "/clean/vehicle"},
    "Theft": {"table": "theft_report", "id": "theft_id", "plate": "vehicle_no", "label": "theft report",
              "path": "/clean/theft"},
    "Shredding": {"table": "shredding_record", "id": "shred_id", "plate": "vehicle_no", "label": "shredding record",
                  "path": "/clean/shred"},
}
DEVICE_TABLES = {"ANPR": ["ANPR"], "Insurance": ["Insurance"], "RTO": ["RTO"], "Theft/Shredding": ["Theft", "Shredding"]}
SOURCE_DEVICES = tuple(DEVICE_TABLES)

REQUIRED = {
    "ANPR": ("capture_ts",),
    "RTO": ("owner_name", "chassis_number", "engine_number"),
    "Insurance": ("insurer_name", "policy_number", "start_date", "end_date"),
    "Theft": ("report_date",),
    "Shredding": ("shred_date",),
}
INSURANCE_STATUSES = ("Active", "Lapsed", "Cancelled")
THEFT_STATUSES = ("Reported", "Recovered")
KNOWN_TYPES = {"car", "bike", "motorcycle", "motorbike", "scooter", "moped", "truck", "lorry", "bus", "minibus", "van",
               "auto", "auto rickshaw", "rickshaw", "jeep", "suv", "tractor", "tempo", "pickup", "taxi", "ambulance",
               "trailer"}
KNOWN_MAKES = {"maruti", "suzuki", "hyundai", "honda", "tata", "mahindra", "toyota", "kia", "skoda", "volkswagen",
               "renault", "nissan", "ford", "mg", "jeep", "bmw", "mercedes", "audi", "royal enfield", "bajaj", "hero",
               "tvs", "yamaha", "ktm", "ashok leyland", "eicher", "force", "isuzu", "fiat", "chevrolet", "datsun",
               "citroen", "jawa", "ola", "ather", "piaggio", "volvo", "bharatbenz", "lexus", "jaguar", "land rover",
               "porsche", "mini", "byd"}
FIELD_WORDS = {"color": "colour", "vehicle_type": "vehicle type", "make_model": "make / model"}
DUPLICATE_WHY = {"RTO": "same chassis and engine number", "Insurance": "same policy number and insurer",
                 "Theft": "same FIR number", "Shredding": "the same vehicle (a vehicle is shredded only once)"}
MISMATCH_FLAG = "details contradict the RTO registration"   # ANPR flag behind the PLATE_DETAILS_MISMATCH risk

MAX_ROUNDS = 5          # how many devices one change may ripple through
CHECK_SECONDS = 45      # pending work / missed notifications are picked up this often
RULES_VERSION = 2       # raise when the cleaning rules or index keys change: every device cleans everything once more
CHUNK = 500             # ids per SQL "IN (...)"
PAGE_ROWS = 500         # rows shown on a page


# ============================================================
# Small helpers
# ============================================================

def plate_of(name, row):
    return rules.normalize_plate(row.get(TABLES[name]["plate"]))


def key(value):
    """Identifiers (policy, FIR, certificate, chassis, engine) compared without case, spaces or dashes."""
    return rules.normalize_plate(value)


def moment(value):
    """datetime from 'YYYY-MM-DD[ HH:MM[:SS]]'; None when empty; ValueError when unreadable."""
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError(text)


def safe_day(value):
    try:
        found = moment(value)
    except ValueError:
        return None
    return found.date() if found else None


def record_id(name, row):
    return str(row.get(TABLES[name]["id"]))


def _num(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _dump(obj):
    return json.dumps(obj, sort_keys=True, default=str)


def _join(names):
    names = list(names)
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def recognised_make(make_model, extra_makes=()):
    tokens = [t for t in re.split(r"[^a-z0-9]+", rules.clean_text(make_model)) if t]
    for make in KNOWN_MAKES | set(extra_makes):
        first = [t for t in re.split(r"[^a-z0-9]+", make) if t][:1]
        if first and any(t == first[0] or SequenceMatcher(None, t, first[0]).ratio() >= 0.85 for t in tokens):
            return True
    return False


def record_context(name, row, makes=()):
    """Vehicle details a record carries itself (only ANPR and RTO know the vehicle). Junk ANPR details are ignored."""
    if name == "ANPR":
        vtype, model = row.get("vehicle_type"), row.get("make_model_seen")
        return {"color": row.get("vehicle_color"),
                "vehicle_type": vtype if rules.clean_text(vtype) in KNOWN_TYPES else None,
                "make_model": model if recognised_make(model, makes) else None}
    if name == "RTO":
        return {"make_model": f'{row.get("make") or ""} {row.get("model") or ""}'}
    return {}


def registered_models(world, plate):
    return [m for m in (rules.clean_text(f'{r.get("make") or ""} {r.get("model") or ""}') for r in world.rows("RTO", plate)) if m]


# Look-alike-aware plate index: look-alikes fold to one character (0/O/Q/D -> 0, 8/B -> 8 ...), and every plate is
# also filed under each of its one- and two-character deletions. Two plates up to two slips apart (plus any
# look-alikes) share a key.
FOLD = {ch: group[0] for group in rules.CONFUSION_GROUPS for ch in group}


def index_keys(plate):
    folded = "".join(FOLD.get(ch, ch) for ch in plate)
    once = {folded[:i] + folded[i + 1:] for i in range(len(folded))}
    twice = {k[:i] + k[i + 1:] for k in once for i in range(len(k))}
    return {folded} | once | twice


# ============================================================
# What the databases hold, indexed by plate
# ============================================================

class WorldBase:
    """Questions about the plates the databases hold; subclasses answer rows(name, plate), near(plate), makes."""

    def is_registered(self, plate):
        return bool(plate) and bool(self.rows("RTO", plate))

    def holders(self, plate, skip=()):
        return [name for name in TABLES if name not in skip and self.rows(name, plate)]

    def is_target(self, plate):
        """A plate a record can be corrected to: a real plate that some database holds."""
        return bool(plate) and (rules.plate_format_valid(plate) or self.is_registered(plate)) and bool(self.holders(plate))

    def profile(self, plate):
        registered = registered_models(self, plate)
        contexts = [record_context("RTO", r) for r in self.rows("RTO", plate)]
        for r in self.rows("ANPR", plate):
            ctx = record_context("ANPR", r, self.makes)
            if registered and ctx["make_model"] and not any(rules.text_match(rules.clean_text(ctx["make_model"]), m)
                                                             for m in registered):
                ctx["make_model"] = None            # a sighting that contradicts RTO does not describe the vehicle
            contexts.append(ctx)
        return rules.vehicle_profile(contexts)

    def registration(self, plate):
        """(earliest reg_date, latest reg_valid_upto) of the plate's registrations."""
        regs = self.rows("RTO", plate)
        starts = [d for d in (safe_day(r.get("reg_date")) for r in regs) if d]
        ends = [d for d in (safe_day(r.get("reg_valid_upto")) for r in regs) if d]
        return (min(starts) if starts else None), (max(ends) if ends else None)

    def shredded_on(self, plate):
        days = [d for d in (safe_day(r.get("shred_date")) for r in self.rows("Shredding", plate)) if d]
        return min(days) if days else None

    def support(self, plate, skip=()):
        """How many records of the other databases know this plate."""
        return sum(len(self.rows(name, plate)) for name in TABLES if name not in skip)


class World(WorldBase):
    """In memory: {table name: rows}."""

    def __init__(self, tables):
        self.by_plate = {name: {} for name in TABLES}
        for name, rows in tables.items():
            for row in rows or []:
                self.by_plate[name].setdefault(plate_of(name, row), []).append(row)
        self.makes = {rules.clean_text(r.get("make")) for rows in self.by_plate["RTO"].values() for r in rows} - {""}
        self._keys = {p: index_keys(p) for name in TABLES for p in self.by_plate[name] if p}

    def rows(self, name, plate):
        return self.by_plate[name].get(plate, [])

    def near(self, plate):
        keys = index_keys(plate)
        return {p for p, k in self._keys.items() if k & keys}

    def identity_plates(self, name, record):
        """Plates of the other records of the same table that share an identifying number with `record`."""
        mine = {c: key(record.get(c)) for c in IDENTITY_COLUMNS.get(name, ()) if key(record.get(c))}
        return {plate_of(name, r) for rows in self.by_plate[name].values() for r in rows
                if record_id(name, r) != record_id(name, record) and any(key(r.get(c)) == v for c, v in mine.items())}


# ============================================================
# 1. Plate correction
# ============================================================

IDENTIFIERS = {"Insurance": (("insurer_name", "policy_number"), "policy"),
               "Theft": (("fir_number",), "FIR"),
               "Shredding": (("certificate_no",), "certificate")}
# numbers that identify a record's vehicle whatever its plate looks like
IDENTITY_COLUMNS = {"RTO": ("chassis_number", "engine_number"), "Insurance": ("policy_number",),
                    "Theft": ("fir_number",), "Shredding": ("certificate_no",)}


def identifier_evidence(name, record, candidate, world):
    """(matches, conflicts) between the record's own numbers and what the databases hold for `candidate`."""
    matches, conflicts = [], []
    if name in IDENTIFIERS:
        fields, word = IDENTIFIERS[name]
        mine = tuple(key(record.get(f)) for f in fields)
        if mine[-1] and any(tuple(key(o.get(f)) for f in fields) == mine for o in world.rows(name, candidate)):
            matches.append(f"{word} {record.get(fields[-1])} is already recorded for {candidate}")
    elif name == "RTO":
        regs = world.rows("RTO", candidate)
        chassis, engine = key(record.get("chassis_number")), key(record.get("engine_number"))
        if any((chassis and key(r.get("chassis_number")) == chassis) or (engine and key(r.get("engine_number")) == engine)
               for r in regs):
            matches.append(f"its chassis / engine number is registered to {candidate}")
        elif regs:
            conflicts.append(f"{candidate} is registered with another chassis and engine number")
    return matches, conflicts


def rto_twin(record, n, world):
    """A registration whose chassis / engine number is registered under another plate: the vehicle was entered twice
    and one plate is a typo. The plate the other databases know the vehicle by wins; None when this plate is it."""
    twins = [c for c in world.identity_plates("RTO", record) if c and c != n and world.is_target(c)]
    if not twins:
        return None
    best = max(twins, key=lambda c: (world.support(c, skip=("RTO",)), c))
    mine, theirs = world.support(n, skip=("RTO",)), world.support(best, skip=("RTO",))
    if theirs <= mine:
        return None
    return best, {"action": "corrected", "evidence": [
        f"its chassis / engine number is registered to {best}",
        f"the other databases hold {theirs} record(s) of {best} and {mine} of {n}"]}


def correct_plate(name, record, world, dictionary=None):
    """(plate for the cleaned table, note or None). note["action"]: normalised | corrected | ambiguous."""
    raw = str(record.get(TABLES[name]["plate"]) or "")
    n = rules.normalize_plate(raw)
    tidy = {"action": "normalised"} if n and n != raw.strip() else None
    if not n:
        return n, None
    valid = rules.plate_format_valid(n)
    if name == "RTO":
        twin = rto_twin(record, n, world)
        if twin:
            return twin
        if valid or world.holders(n, skip=("RTO",)):
            return n, tidy                               # a registration is the authority for its plate
    elif world.is_registered(n):
        return n, tidy                                   # a registered plate is never changed
    elif valid and world.holders(n, skip=(name,)):
        return n, tidy                                   # other databases hold this plate too: it is a real plate

    strict = valid or name == "RTO"                      # a valid plate needs evidence from the record itself
    repairs = rules.positional_repair(n)
    hits = {rules.normalize_plate(p) for p in (dictionary(name, n) if dictionary else ())}
    same_numbers = world.identity_plates(name, record)  # e.g. the same policy number recorded under another plate
    pool = set()
    for c in world.near(n) | set(repairs) | hits | same_numbers:
        if c == n or not world.is_target(c):
            continue
        if valid:                                        # up to two slips from a registered vehicle
            if world.is_registered(c) and (c in same_numbers or rules.levenshtein_distance(n, c) <= 2):
                pool.add(c)
        elif (c in repairs or c in hits or c in same_numbers
              or rules.weighted_plate_similarity(n, c) >= rules.MATCH_THRESHOLD):
            pool.add(c)
    context = record_context(name, record, world.makes)
    ranked = []
    for c in pool:
        comparison = rules.context_comparison(context, world.profile(c))
        ids, id_conflicts = identifier_evidence(name, record, c, world)
        if comparison["conflicts"] or id_conflicts:
            continue
        strength = 2 * len(ids) + len(comparison["matches"])
        clear_repair = repairs == [c]
        slip = rules.one_edit_apart(n, c)
        if strict:
            if strength < 2:
                continue
            if name == "ANPR" and ("make_model" not in comparison["matches"]
                                   or (not slip and len(comparison["matches"]) < 3)):
                continue                                 # two slips: colour, type and make / model must all match
            if name != "ANPR" and not slip and not ids:
                continue                                 # beyond one slip only a shared number counts
            if name == "RTO" and not world.holders(c, skip=("RTO",)):
                continue                                 # nothing else knows that plate either
        elif strength < 1 and not clear_repair:
            continue
        evidence = [f"{FIELD_WORDS[k]} matches {c}" for k in comparison["matches"]] + ids
        if clear_repair:
            evidence.append("a look-alike character sits in the wrong position")
        if c in hits:
            evidence.append(f"known noisy version of {c} in the {name} plate dictionary")
        score = strength + clear_repair + (c in hits)
        ranked.append((score, world.is_registered(c), round(rules.weighted_plate_similarity(n, c), 4), c, evidence))
    if not ranked:
        return n, tidy
    ranked.sort(key=lambda t: (-t[0], -t[1], -t[2], t[3]))
    if len(ranked) > 1 and ranked[0][:3] == ranked[1][:3]:
        return n, {"action": "ambiguous", "candidates": [ranked[0][3], ranked[1][3]]}
    return ranked[0][3], {"action": "corrected", "evidence": ranked[0][4]}


# ============================================================
# 2. Duplicates
# ============================================================

def duplicate_key(name, row):
    """The identity key that is NOT the plate (RTO chassis+engine, shredding certificate); None otherwise."""
    if name == "RTO":
        chassis, engine = key(row.get("chassis_number")), key(row.get("engine_number"))
        return f"{chassis}|{engine}" if chassis and engine else None
    if name == "Shredding":
        return key(row.get("certificate_no")) or None
    return None


def _duplicate_keys(name):
    if name == "ANPR":
        return [lambda r: (plate_of(name, r), str(r.get("capture_ts") or "").strip(), key(r.get("camera_id")))]
    if name == "RTO":
        return [lambda r: duplicate_key(name, r)]
    if name == "Insurance":
        return [lambda r: (plate_of(name, r), key(r.get("insurer_name")), key(r.get("policy_number")))
                if key(r.get("policy_number")) else None]
    if name == "Theft":
        return [lambda r: (plate_of(name, r), key(r.get("fir_number"))) if key(r.get("fir_number")) else None]
    return [lambda r: plate_of(name, r) or None, lambda r: ("certificate", duplicate_key(name, r)) if duplicate_key(name, r) else None]


def duplicate_groups(name, rows):
    """Records that describe the same thing (union of every duplicate key of the table)."""
    parent = list(range(len(rows)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for keyfn in _duplicate_keys(name):
        first = {}
        for i, row in enumerate(rows):
            k = keyfn(row)
            if k is None:
                continue
            if k in first:
                parent[find(i)] = find(first[k])
            else:
                first[k] = i
    groups = {}
    for i, row in enumerate(rows):
        groups.setdefault(find(i), []).append(row)
    return sorted(groups.values(), key=lambda g: min(_num(record_id(name, r)) for r in g))


def merge_rows(name, group):
    """One cleaned row: the values of the record added later, gaps filled from older records."""
    ordered = sorted(group, key=lambda r: _num(record_id(name, r)), reverse=True)
    merged = dict(ordered[0])
    for older in ordered[1:]:
        for k, v in older.items():
            if merged.get(k) in (None, "") and v not in (None, ""):
                merged[k] = v
    return merged


def resolve(name, raw_rows, world, dictionary=None):
    """Plate step + duplicate step for records of one table. Returns (resolved rows, {raw id: decision})."""
    spec = TABLES[name]
    decisions, fixed = {}, []
    for row in raw_rows:
        rid = record_id(name, row)
        plate, note = correct_plate(name, row, world, dictionary)
        decisions[rid] = {"source": name, "raw_id": rid, "row_id": rid, "plate_from": str(row.get(spec["plate"]) or ""),
                          "plate_to": plate, "status": "kept", "notes": [note] if note else []}
        fixed.append({**row, spec["plate"]: plate})
    resolved = []
    for group in duplicate_groups(name, fixed):
        ids = sorted((record_id(name, r) for r in group), key=_num)
        if len(group) == 1:
            resolved.append({**group[0], "raw_ids": ids[0]})
        elif name == "ANPR":                              # the first capture is the sighting, the rest double entries
            first = min(group, key=lambda r: _num(record_id(name, r)))
            resolved.append({**first, "raw_ids": ids[0]})
            for other in ids[1:]:
                decisions[other].update(status="removed", row_id=ids[0])
                decisions[other]["notes"].append({"action": "double", "into": ids[0]})
        else:
            row = merge_rows(name, group)
            winner = record_id(name, row)
            resolved.append({**row, "raw_ids": ",".join(ids)})
            for other in ids:
                if other == winner:
                    decisions[other]["notes"].append({"action": "merged", "with": [i for i in ids if i != winner],
                                                      "why": DUPLICATE_WHY[name]})
                else:
                    decisions[other].update(status="merged", row_id=winner)
                    decisions[other]["notes"].append({"action": "merged", "into": winner, "why": DUPLICATE_WHY[name]})
            decisions[winner]["plate_to"] = plate_of(name, row)
    return resolved, decisions


# ============================================================
# 3. Validity
# ============================================================

def invalid_reasons(name, row, world, today, now, notes=()):
    """Why a (plate-corrected, merged) record cannot be in the cleaned table; [] when it is valid."""
    reasons = []
    plate = plate_of(name, row)
    registered = world.is_registered(plate)
    for field in REQUIRED[name]:
        if str(row.get(field) or "").strip() == "":
            reasons.append(f"{field.replace('_', ' ')} is empty")
    if plate and name != "RTO" and not rules.plate_format_valid(plate) and not registered:
        ambiguous = [n for n in notes if n.get("action") == "ambiguous"]
        reasons.append(f"plate {plate} is not a valid plate and "
                       + (f"could be {_join(ambiguous[0]['candidates'])}" if ambiguous
                          else "could not be matched to a known vehicle"))
    unregistered = plate and rules.plate_format_valid(plate) and not registered

    def when(field):
        try:
            found = moment(row.get(field))
        except ValueError:
            reasons.append(f"{field.replace('_', ' ')} '{row.get(field)}' is not a valid date")
            return None
        return found.date() if found else None

    reg_from, reg_upto = world.registration(plate)
    shred = world.shredded_on(plate)

    if name == "ANPR":                                   # a sighting: only an impossible time makes it invalid
        try:
            seen = moment(row.get("capture_ts"))
        except ValueError:
            reasons.append(f"capture time '{row.get('capture_ts')}' is not a valid time")
            seen = None
        if seen and seen > now:
            reasons.append(f"capture time {row.get('capture_ts')} is in the future")

    elif name == "RTO":
        start, upto, fitness = when("reg_date"), when("reg_valid_upto"), when("fitness_valid_upto")
        if start and upto and upto < start:
            reasons.append(f"registration valid upto {upto} is before the registration date {start}")
        if start and fitness and fitness < start:
            reasons.append(f"fitness valid upto {fitness} is before the registration date {start}")
        if start and shred and start > shred:
            reasons.append(f"registered ({start}) after the vehicle was shredded ({shred})")

    elif name == "Insurance":
        start, end = when("start_date"), when("end_date")
        if unregistered:
            reasons.append(f"vehicle {plate} is not registered in RTO")
        if start and end and end < start:
            reasons.append(f"end date {end} is before start date {start}")
        premium = row.get("premium_amount")
        if premium not in (None, ""):
            try:
                if float(premium) < 0:
                    reasons.append(f"premium {premium} is negative")
            except (TypeError, ValueError):
                reasons.append(f"premium '{premium}' is not a number")
        if start and reg_from and start < reg_from:
            reasons.append(f"policy starts ({start}) before the vehicle was registered ({reg_from})")
        if start and reg_upto and start > reg_upto:
            reasons.append(f"policy starts ({start}) after the registration expired ({reg_upto})")
        if start and shred and start > shred:
            reasons.append(f"policy starts ({start}) after the vehicle was shredded ({shred})")
        status = str(row.get("status") or "").strip()
        if status and status not in INSURANCE_STATUSES:
            reasons.append(f"status '{status}' is not Active, Lapsed or Cancelled")
        if status == "Active" and end and end < today:
            reasons.append(f"status is Active but the policy ended on {end}")

    elif name == "Theft":
        reported = when("report_date")
        if unregistered:
            reasons.append(f"vehicle {plate} is not registered in RTO")
        if reported and reported > today:
            reasons.append(f"report date {reported} is in the future")
        if reported and reg_from and reported < reg_from:
            reasons.append(f"theft reported ({reported}) before the vehicle was registered ({reg_from})")
        if reported and shred and reported > shred:
            reasons.append(f"theft reported ({reported}) after the vehicle was shredded ({shred})")
        status = str(row.get("status") or "").strip()
        if status not in THEFT_STATUSES:
            reasons.append(f"status '{status}' is not Reported or Recovered" if status else "status is empty")

    elif name == "Shredding":
        shredded = when("shred_date")
        if unregistered:
            reasons.append(f"vehicle {plate} is not registered in RTO")
        if shredded and shredded > today:
            reasons.append(f"shred date {shredded} is in the future")
        if shredded and reg_from and shredded < reg_from:
            reasons.append(f"shredded ({shredded}) before the vehicle was registered ({reg_from})")
    return reasons


def flags_for(name, row, world):
    """Kept in the cleaned table, but worth a look."""
    plate = plate_of(name, row)
    flags = []
    if name == "ANPR":
        for field, words in (("location", "location"), ("vehicle_type", "vehicle type"), ("make_model_seen", "make / model")):
            if not str(row.get(field) or "").strip():
                flags.append(f"{words} is empty")
        if plate and not world.is_registered(plate):
            flags.append(f"plate {plate} is not registered in RTO")
        vtype = rules.clean_text(row.get("vehicle_type"))
        if vtype and vtype not in KNOWN_TYPES:
            flags.append(f"vehicle type '{row.get('vehicle_type')}' is not a known vehicle type")
        model = rules.clean_text(row.get("make_model_seen"))
        if model:
            known = registered_models(world, plate)
            if known and any(rules.text_match(model, k) for k in known):
                pass
            elif not recognised_make(model, world.makes):
                flags.append(f"make / model '{row.get('make_model_seen')}' is not a recognised vehicle make")
            elif known:
                flags.append(f"{MISMATCH_FLAG}: seen as {row.get('make_model_seen')}, registered as {known[0].title()} "
                             "(possible cloned plate)")
    if name == "RTO":
        if plate and not rules.plate_format_valid(plate) and not world.holders(plate, skip=("RTO",)):
            flags.append(f"plate {plate} is not in the standard plate format")
        chassis = key(row.get("chassis_number"))
        for other in world.rows("RTO", plate):
            if record_id("RTO", other) != record_id("RTO", row) and key(other.get("chassis_number")) != chassis:
                flags.append(f"plate also registered under chassis {other.get('chassis_number')} "
                             f"(registration #{record_id('RTO', other)})")
    return flags


def validate(name, resolved_rows, world, decisions, now):
    """Kept rows (with their flags); the decisions of left-out records get the reasons."""
    kept = []
    for row in resolved_rows:
        decision = decisions[record_id(name, row)]
        reasons = invalid_reasons(name, row, world, now.date(), now, decision["notes"])
        if reasons:
            decision["status"] = "removed"
            decision["notes"].append({"action": "invalid", "reasons": reasons})
            continue
        flags = flags_for(name, row, world)
        if flags:
            decision["notes"].append({"action": "flagged", "reasons": flags})
        kept.append({**row, "flags": "; ".join(flags)})
    return kept


def clean_tables(names, own_raw, peer_tables, dictionary=None, now=None):
    """Clean whole tables in memory (a full pass; the Cleanser does the same incrementally on its database).

    own_raw:     {table name: raw rows} of this device
    peer_tables: {table name: rows} the other devices' cleaned data before their validity filter
    Returns {table name: {"kept": rows, "resolved": rows, "decisions": {raw id: decision}}}.
    """
    now = now or datetime.now()
    own_tidy = {name: [{**r, TABLES[name]["plate"]: plate_of(name, r)} for r in own_raw[name]] for name in names}
    match_world = World({**peer_tables, **own_tidy})
    resolved, decisions = {}, {}
    for name in names:
        resolved[name], decisions[name] = resolve(name, own_raw[name], match_world, dictionary)
    check_world = World({**peer_tables, **resolved})
    return {name: {"kept": validate(name, resolved[name], check_world, decisions[name], now),
                   "resolved": resolved[name], "decisions": decisions[name]} for name in names}


def trivial(decision):
    return not decision or not any(n.get("action") != "ambiguous" for n in decision.get("notes", []))


def describe(decision):
    """What cleansing did with one raw record, in plain words ("" when it is kept as it is)."""
    if trivial(decision):
        return ""
    label = TABLES[decision["source"]]["label"]
    parts = []
    for note in decision["notes"]:
        action = note.get("action")
        if action == "normalised":
            parts.append(f"In cleaned data, plate '{decision['plate_from']}' is written as {decision['plate_to']}.")
        elif action == "corrected":
            parts.append(f"In cleaned data, plate {decision['plate_from']} was corrected to {decision['plate_to']}: "
                         + "; ".join(note.get("evidence") or []) + ".")
        elif action == "double":
            parts.append(f"This {label} was a double entry of {label} #{note['into']} (same plate, time and camera) "
                         "and was removed from cleaned data.")
        elif action == "merged" and note.get("into"):
            parts.append(f"In cleaned data, it was merged into {label} #{note['into']}: {note['why']}. "
                         "The newer values were kept.")
        elif action == "merged":
            parts.append(f"In cleaned data, it was merged with {label} #" + ", #".join(note["with"])
                         + f": {note['why']}. The newer values were kept.")
        elif action == "invalid":
            parts.append("It was left out of cleaned data: " + "; ".join(note["reasons"]) + ".")
        elif action == "flagged":
            parts.append("Kept in cleaned data but flagged: " + "; ".join(note["reasons"]) + ".")
    return " ".join(parts)


RESULT_TEXT = {"normalised": "Plate normalised", "corrected": "Plate corrected", "double": "Removed: double entry",
               "merged": "Merged", "invalid": "Removed: invalid", "flagged": "Flagged"}


# ============================================================
# Vehicle state and MoT report (sent by ANPR)
# ============================================================

def vehicle_view(plate, kept, today):
    """Integrated state of one vehicle from the cleaned data of every device (as in the Vehicle 360 view)."""
    def rows(name):
        return [r for r in kept.get(name, []) if plate_of(name, r) == plate]
    captures, regs, thefts, shreds = rows("ANPR"), rows("RTO"), rows("Theft"), rows("Shredding")
    insurance_status, policy = evaluate_insurance(rows("Insurance"), today)
    stolen = any(t.get("status") == "Reported" for t in thefts)
    flags = []
    if insurance_status in ("NOT_INSURED", "EXPIRED", "CANCELLED"):
        flags.append("UNINSURED")
    if not regs:
        flags.append("UNREGISTERED_OR_FAKE_PLATE")
    if stolen:
        flags.append("STOLEN_VEHICLE_ON_ROAD")
    if shreds:
        flags.append("SHREDDED_VEHICLE_STILL_ON_ROAD")
    if any(MISMATCH_FLAG in str(c.get("flags") or "") for c in captures):
        flags.append("PLATE_DETAILS_MISMATCH")
    return {
        "plate_number": plate,
        "last_seen_on_road": max((c.get("capture_ts") for c in captures if c.get("capture_ts")), default=None),
        "registered": bool(regs),
        "owner_name": regs[0].get("owner_name") if regs else None,
        "make_model": (f'{regs[0].get("make") or ""} {regs[0].get("model") or ""}'.strip() if regs
                       else (captures[0].get("make_model_seen") if captures else None)),
        "insurance_status": insurance_status,
        "theft_status": "REPORTED_STOLEN" if stolen else ("RECOVERED" if thefts else "NONE"),
        "shredded": bool(shreds),
        "risk_flags": flags,
        "suspicious": bool(flags),
    }


def recommend_action(view):
    flags = view["risk_flags"]
    if "SHREDDED_VEHICLE_STILL_ON_ROAD" in flags or "STOLEN_VEHICLE_ON_ROAD" in flags:
        return "IMMEDIATE INTERCEPT - notify police & MoT"
    if "PLATE_DETAILS_MISMATCH" in flags:
        return "VERIFY - possible cloned plate"
    if "UNREGISTERED_OR_FAKE_PLATE" in flags:
        return "IMPOUND & VERIFY - notify MoT & RTO"
    if "UNINSURED" in flags:
        return "ISSUE NOTICE - fine + compulsory insurance within 15 days"
    return "NO ACTION"


def mot_report(view, now):
    return {
        "plate_no": view["plate_number"],
        "generated_on": now.strftime("%Y-%m-%d %H:%M:%S"),
        "owner_name": view["owner_name"],
        "make_model": view["make_model"],
        "insurance_status": view["insurance_status"],
        "theft_status": view["theft_status"],
        "shredded": "YES" if view["shredded"] else "NO",
        "risk_flags": ", ".join(view["risk_flags"]),
        "last_seen_on_road": view["last_seen_on_road"],
        "recommended_action": recommend_action(view),
    }


def view_signature(view):
    """The state MoT is told about; the same state is never sent twice."""
    return [view["registered"], view["insurance_status"], view["theft_status"], view["shredded"], view["risk_flags"]]


# ============================================================
# Storage in the device's own database
# ============================================================

SCHEMA = """
CREATE TABLE IF NOT EXISTS cleansing_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cleansing_changes (change_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL,
    raw_id TEXT, old_plate TEXT, new_plate TEXT);
CREATE TABLE IF NOT EXISTS cleansing_own (source TEXT NOT NULL, raw_id TEXT NOT NULL, plate TEXT NOT NULL, dup TEXT,
    PRIMARY KEY (source, raw_id));
CREATE INDEX IF NOT EXISTS cleansing_own_plate ON cleansing_own (source, plate);
CREATE INDEX IF NOT EXISTS cleansing_own_dup ON cleansing_own (source, dup);
CREATE TABLE IF NOT EXISTS cleansing_resolved (source TEXT NOT NULL, row_id TEXT NOT NULL, plate TEXT, data TEXT,
    kept INTEGER NOT NULL, version INTEGER NOT NULL, PRIMARY KEY (source, row_id));
CREATE INDEX IF NOT EXISTS cleansing_resolved_plate ON cleansing_resolved (source, plate);
CREATE INDEX IF NOT EXISTS cleansing_resolved_version ON cleansing_resolved (version);
CREATE TABLE IF NOT EXISTS cleansing_peer (source TEXT NOT NULL, row_id TEXT NOT NULL, plate TEXT NOT NULL,
    data TEXT NOT NULL, kept INTEGER NOT NULL, PRIMARY KEY (source, row_id));
CREATE INDEX IF NOT EXISTS cleansing_peer_plate ON cleansing_peer (source, plate);
CREATE TABLE IF NOT EXISTS cleansing_index (key TEXT NOT NULL, plate TEXT NOT NULL, PRIMARY KEY (key, plate)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS cleansing_makes (make TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS cleansing_log (source TEXT NOT NULL, raw_id TEXT NOT NULL, row_id TEXT NOT NULL,
    plate_to TEXT, notable INTEGER NOT NULL, corrected INTEGER NOT NULL, decision TEXT NOT NULL,
    updated_at TEXT NOT NULL, PRIMARY KEY (source, raw_id));
CREATE INDEX IF NOT EXISTS cleansing_log_plate ON cleansing_log (source, plate_to);
CREATE INDEX IF NOT EXISTS cleansing_log_row ON cleansing_log (source, row_id);
CREATE TABLE IF NOT EXISTS cleansing_mot (plate TEXT PRIMARY KEY, signature TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cleansing_mot_unsent (plate TEXT PRIMARY KEY, report TEXT NOT NULL);
"""

# raw changes found when a pass rolls over a day: rules that compare a date with today
DATE_RULES = {"Insurance": ("end_date", "end_date >= ? AND end_date < ?"),
              "Theft": ("report_date", "report_date > ? AND report_date <= ?"),
              "Shredding": ("shred_date", "shred_date > ? AND shred_date <= ?"),
              "ANPR": ("capture_ts", "substr(capture_ts, 1, 10) > ? AND substr(capture_ts, 1, 10) <= ?")}


def _chunks(values):
    values = list(values)
    for i in range(0, len(values), CHUNK):
        yield values[i:i + CHUNK]


def _select_in(conn, sql, values, params=()):
    """Run `sql` (with one {} for the placeholders) for every chunk of `values`; all rows."""
    out = []
    for part in _chunks(values):
        out.extend(conn.execute(sql.format(",".join("?" * len(part))), (*params, *part)).fetchall())
    return out


def _raw_by_ids(conn, name, ids):
    spec = TABLES[name]
    return [dict(r) for r in _select_in(conn, f"SELECT * FROM {spec['table']} WHERE {spec['id']} IN ({{}})", ids)]


class DbWorld(WorldBase):
    """The same questions as World, answered from the device's database through indexes (for large tables).

    mode "match": this device's own tables are its raw records (formatting removed) - evidence for the plate step.
    mode "check": its own tables are the cleaned records before validity; `overlay` holds the rows of this pass and
                  `replaced` the row ids they replace.
    The other devices' tables come from the local copy of their evidence (cleansing_peer).
    """

    def __init__(self, conn, own, mode, overlay=None, replaced=None):
        self.conn, self.own, self.mode = conn, set(own), mode
        self.overlay = overlay or {}
        self.replaced = replaced or {}
        self._cache = {}
        self.makes = {r[0] for r in conn.execute("SELECT make FROM cleansing_makes")}

    def rows(self, name, plate):
        if not plate:
            return []
        if (name, plate) not in self._cache:
            self._cache[(name, plate)] = self._load(name, plate)
        return self._cache[(name, plate)]

    def _load(self, name, plate):
        if name not in self.own:
            return [json.loads(r[0]) for r in self.conn.execute(
                "SELECT data FROM cleansing_peer WHERE source=? AND plate=?", (name, plate))]
        if self.mode == "match":
            ids = [r[0] for r in self.conn.execute("SELECT raw_id FROM cleansing_own WHERE source=? AND plate=?", (name, plate))]
            return [{**r, TABLES[name]["plate"]: plate} for r in _raw_by_ids(self.conn, name, ids)]
        replaced = self.replaced.get(name, set())
        rows = [json.loads(data) for row_id, data in self.conn.execute(
            "SELECT row_id, data FROM cleansing_resolved WHERE source=? AND plate=? AND data IS NOT NULL", (name, plate))
            if row_id not in replaced]
        return rows + self.overlay.get(name, {}).get(plate, [])

    def near(self, plate):
        return {r[0] for r in _select_in(self.conn, "SELECT DISTINCT plate FROM cleansing_index WHERE key IN ({})",
                                         index_keys(plate))}

    def identity_plates(self, name, record):
        """Plates of the other raw records of this device's table that share an identifying number with `record`."""
        if name not in self.own:
            return set()
        spec, out = TABLES[name], set()
        for column in IDENTITY_COLUMNS.get(name, ()):
            value = key(record.get(column))
            if not value:
                continue
            for row in self.conn.execute(f"SELECT * FROM {spec['table']} WHERE {column}=?", (record.get(column),)):
                row = dict(row)
                if record_id(name, row) != record_id(name, record) and key(row.get(column)) == value:
                    out.add(plate_of(name, row))
        return out


# ============================================================
# The cleanser of one device: storage, evidence from the other devices, triggers, pages
# ============================================================

class Cleanser:
    def __init__(self, device, db_path, book=None, peers=None, timeout=5, fetch_timeout=60, interval=CHECK_SECONDS):
        if device not in DEVICE_TABLES:
            raise ValueError(f"unknown device {device!r}")
        self.device, self.db_path, self.book = device, db_path, book
        self.names = DEVICE_TABLES[device]
        self.peers = dict(peers or {})
        self.timeout, self.fetch_timeout, self.interval = timeout, fetch_timeout, interval   # a full copy of a big table takes a while
        self.fetch = self._fetch            # callable(device, since, epoch) -> evidence dict or None (replaceable in tests)
        self.post = self._post              # callable(url, payload) -> bool
        self._lock = threading.RLock()
        self._queue_lock = threading.Lock()
        self._queued = None
        self._worker = False

    # ---------- storage (same database as the raw table) ----------

    def _connect(self):
        path = self.db_path()
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        conn = sqlite3.connect(path, timeout=10)
        conn.row_factory = sqlite3.Row
        columns = [r[1] for r in conn.execute("PRAGMA table_info(cleansing_log)")]
        if columns and "row_id" not in columns:            # storage of the first, whole-table version: start over
            conn.execute("DROP TABLE cleansing_log")
            conn.execute("DROP TABLE IF EXISTS cleansing_state")
        conn.executescript(SCHEMA)
        if self._get(conn, "rules_version") != RULES_VERSION:   # new rules / index keys: clean everything once more
            conn.execute("DELETE FROM cleansing_index")
            conn.execute("DELETE FROM cleansing_state WHERE key IN ('built', 'epoch')")
            self._set(conn, "rules_version", RULES_VERSION)
        for name in self.names:
            self._ensure_table(conn, name)
        conn.commit()
        return conn

    @staticmethod
    def _ensure_table(conn, name):
        """Change log triggers on the raw table and the cleaned table next to it."""
        spec = TABLES[name]
        table, ident, plate = spec["table"], spec["id"], spec["plate"]
        types = {r[1]: r[2] for r in conn.execute(f"PRAGMA table_info({table})")}
        columns = list(types)
        if not columns:
            return
        clean_types = {r[1]: r[2] for r in conn.execute(f"PRAGMA table_info({table}_clean)")}
        if clean_types and clean_types.get(ident) != types[ident]:
            # A cleaned table without the raw column types (ids compared as text never matched, rows piled up):
            # drop it and clean the whole device again; a new epoch makes the other devices re-read everything.
            conn.execute(f"DROP TABLE {table}_clean")
            conn.execute("DELETE FROM cleansing_state WHERE key IN ('built', 'epoch')")
        for event, row, old, new in (("insert", "NEW", "NULL", f"NEW.{plate}"), ("update", "NEW", f"OLD.{plate}", f"NEW.{plate}"),
                                     ("delete", "OLD", f"OLD.{plate}", "NULL")):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS cleansing_{table}_{event} AFTER {event.upper()} ON {table} BEGIN "
                         f"INSERT INTO cleansing_changes (source, raw_id, old_plate, new_plate) "
                         f"VALUES ('{name}', {row}.{ident}, {old}, {new}); END")
        typed = [f"{c} {types[c]}".strip() for c in columns] + ["raw_ids TEXT", "flags TEXT"]
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table}_clean ({', '.join(typed)})")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {table}_clean_id ON {table}_clean ({ident})")
        conn.execute(f"CREATE INDEX IF NOT EXISTS {table}_clean_plate ON {table}_clean ({plate})")
        for column in IDENTITY_COLUMNS.get(name, ()):            # same-number lookups on the raw table
            if column in types:
                conn.execute(f"CREATE INDEX IF NOT EXISTS cleansing_{table}_{column} ON {table} ({column})")

    @staticmethod
    def _get(conn, name, default=None):
        row = conn.execute("SELECT value FROM cleansing_state WHERE key=?", (name,)).fetchone()
        return json.loads(row["value"]) if row else default

    @staticmethod
    def _set(conn, name, value):
        if value is None:
            conn.execute("DELETE FROM cleansing_state WHERE key=?", (name,))
        else:
            conn.execute("INSERT OR REPLACE INTO cleansing_state VALUES (?, ?)", (name, _dump(value)))

    @staticmethod
    def _raw_rows(conn, name):
        spec = TABLES[name]
        try:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {spec['table']} ORDER BY {spec['id']}")]
        except sqlite3.OperationalError:
            return []

    def _read(self, fn, default):
        try:
            conn = self._connect()
        except FileNotFoundError:
            return default
        try:
            return fn(conn)
        finally:
            conn.close()

    @staticmethod
    def _index(conn, plates):
        conn.executemany("INSERT OR IGNORE INTO cleansing_index VALUES (?, ?)",
                         [(k, p) for p in set(plates) if p for k in index_keys(p)])

    @staticmethod
    def _near(conn, plate):
        return {r[0] for r in _select_in(conn, "SELECT DISTINCT plate FROM cleansing_index WHERE key IN ({})", index_keys(plate))}

    def built(self):
        return self._read(lambda conn: bool(self._get(conn, "built", False)), False)

    def decision(self, name, raw_id):
        def read(conn):
            row = conn.execute("SELECT decision FROM cleansing_log WHERE source=? AND raw_id=?", (name, str(raw_id))).fetchone()
            return json.loads(row[0]) if row else None
        return self._read(read, None)

    def status(self):
        def read(conn):
            records = {}
            for source, raw_id in conn.execute("SELECT DISTINCT source, raw_id FROM cleansing_changes"):
                records.setdefault(source, []).append(raw_id)
            pending = self._get(conn, "pending")
            if pending:
                pending["records"] = {k: sorted(v, key=_num) for k, v in records.items()}
            return {"device": self.device, "built": bool(self._get(conn, "built", False)), "pending": pending,
                    "last_cleaned": self._get(conn, "last_cleaned")}
        return self._read(read, {"device": self.device, "built": False, "pending": None, "last_cleaned": None})

    def evidence(self, since=0, epoch=None):
        """What other devices clean against: this device's cleaned rows before validity, as a delta since `since`
        (version) when `epoch` matches; the full set otherwise. Before the first clean: the raw rows."""
        def read(conn):
            if not self._get(conn, "built", False):
                tables = {name: [{"id": record_id(name, r), "row": {**r, TABLES[name]["plate"]: plate_of(name, r)}, "kept": True}
                                 for r in self._raw_rows(conn, name)] for name in self.names}
                return {"device": self.device, "built": False, "epoch": None, "version": 0, "full": True, "tables": tables}
            mine, version = self._get(conn, "epoch"), self._get(conn, "version", 0)
            full = not since or epoch != mine
            query = ("SELECT source, row_id, data, kept FROM cleansing_resolved WHERE "
                     + ("data IS NOT NULL" if full else "version > ?"))
            tables = {name: [] for name in self.names}
            for source, row_id, data, kept in conn.execute(query, () if full else (int(since),)):
                tables[source].append({"id": row_id, "row": json.loads(data) if data else None, "kept": bool(kept)})
            return {"device": self.device, "built": True, "epoch": mine, "version": version, "full": full, "tables": tables}
        return self._read(read, {"device": self.device, "built": False, "epoch": None, "version": 0, "full": True,
                                 "tables": {name: [] for name in self.names}})

    def clean_rows(self, name, plate=None):
        """Cleaned records of one table (the raw ones until the first clean), optionally of one plate."""
        spec = TABLES[name]

        def read(conn):
            if not self._get(conn, "built", False):
                rows = self._raw_rows(conn, name)
                return rows if plate is None else [r for r in rows if plate_of(name, r) == rules.normalize_plate(plate)]
            if plate is None:
                return [dict(r) for r in conn.execute(f"SELECT * FROM {spec['table']}_clean ORDER BY {spec['id']}")]
            return [dict(r) for r in conn.execute(f"SELECT * FROM {spec['table']}_clean WHERE {spec['plate']}=? "
                                                  f"ORDER BY {spec['id']}", (rules.normalize_plate(plate),))]
        return self._read(read, [])

    def clean_rows_of(self, name, plates):
        """Cleaned records of many plates at once, through the plate index of the cleaned table."""
        spec = TABLES[name]
        wanted = {rules.normalize_plate(p) for p in plates} - {""}

        def read(conn):
            if not self._get(conn, "built", False):
                return [r for r in self._raw_rows(conn, name) if plate_of(name, r) in wanted]
            return [dict(r) for r in _select_in(conn, f"SELECT * FROM {spec['table']}_clean WHERE {spec['plate']} IN ({{}}) "
                                                      f"ORDER BY {spec['id']}", sorted(wanted))]
        return self._read(read, []) if wanted else []

    def candidate_rows(self, plate, threshold, slips, algorithm, cost):
        """Return this device's records passing either of the client's plate tests."""
        def matches(candidate):
            if not candidate:
                return False
            if rules.levenshtein_distance(plate, candidate) <= slips:
                return True
            if algorithm == "levenshtein":
                return rules.plate_similarity(plate, candidate) >= threshold
            return rules.weighted_plate_similarity(plate, candidate, cost) >= threshold

        def read(conn):
            conn.execute("BEGIN")
            if not self._get(conn, "built", False):
                return {name: [r for r in self._raw_rows(conn, name) if matches(plate_of(name, r))]
                        for name in self.names}
            # Insertions and deletions cost one. At this threshold, even the longest possible
            # match has fewer than three edits after folding look-alike characters.
            indexed = slips <= 2 and threshold > 0 and len(plate) * (1 - threshold) / threshold < 3 - 1e-9
            nearby = self._near(conn, plate) | {plate} if indexed else None
            result = {}
            for name in self.names:
                spec = TABLES[name]
                table, column = spec["table"] + "_clean", spec["plate"]
                pool = nearby if indexed else (r[0] for r in conn.execute(f"SELECT DISTINCT {column} FROM {table}"))
                wanted = sorted(p for p in pool if matches(p))
                result[name] = [dict(r) for r in _select_in(conn, f"SELECT * FROM {table} WHERE {column} IN ({{}}) "
                                                            f"ORDER BY {spec['id']}", wanted)]
            return result

        return self._read(read, {name: [] for name in self.names})

    # ---------- the other devices ----------

    def _fetch(self, device, since=0, epoch=None):
        base = self.peers.get(device)
        if not base:
            return None
        query = urllib.parse.urlencode({"since": since or 0, "epoch": epoch or ""})
        try:
            with urllib.request.urlopen(f"{base.rstrip('/')}/clean/evidence?{query}", timeout=self.fetch_timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) and isinstance(data.get("tables"), dict) else None

    def _post(self, url, payload):
        request = urllib.request.Request(url, data=_dump(payload).encode("utf-8"), method="POST",
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as resp:
                return 200 <= resp.status < 300
        except (OSError, ValueError):
            return False

    def notify_peers(self, round_):
        """Tell the other devices that this device's cleaned data changed (in the background)."""
        for device in SOURCE_DEVICES:
            base = self.peers.get(device)
            if device != self.device and base:
                threading.Thread(target=self.post, daemon=True,
                                 args=(f"{base.rstrip('/')}/clean/notify?round={round_}", {"from": self.device})).start()

    def _apply_peer(self, conn, device, data):
        """Store another device's evidence (delta or full set) locally; returns the plates whose data changed."""
        changed = set()
        for name in DEVICE_TABLES[device]:
            incoming = {str(e["id"]): e for e in data["tables"].get(name) or []}
            if data.get("full"):
                existing = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
                    "SELECT row_id, plate, data, kept FROM cleansing_peer WHERE source=?", (name,))}
                gone = set(existing) - set(incoming)
            else:
                existing = {r[0]: (r[1], r[2], r[3]) for r in _select_in(
                    conn, "SELECT row_id, plate, data, kept FROM cleansing_peer WHERE source=? AND row_id IN ({})",
                    incoming, (name,))}
                gone = set()
            for row_id, entry in incoming.items():
                row, old = entry.get("row"), existing.get(row_id)
                if row is None:
                    gone.add(row_id)
                    continue
                plate, data_json, kept = plate_of(name, row), _dump(row), int(bool(entry.get("kept")))
                if old == (plate, data_json, kept):
                    continue
                conn.execute("INSERT OR REPLACE INTO cleansing_peer VALUES (?, ?, ?, ?, ?)", (name, row_id, plate, data_json, kept))
                changed |= {plate, old[0] if old else ""}
                self._index(conn, [plate])
                if name == "RTO" and rules.clean_text(row.get("make")):
                    conn.execute("INSERT OR IGNORE INTO cleansing_makes VALUES (?)", (rules.clean_text(row.get("make")),))
            for row_id in gone:
                if row_id in existing:
                    conn.execute("DELETE FROM cleansing_peer WHERE source=? AND row_id=?", (name, row_id))
                    changed.add(existing[row_id][0])
        return changed - {""}

    def _dictionary(self, name, plate):
        return list(self.book.lookup(name, plate)) if self.book else []

    # ---------- which records a change can affect ----------

    def _sync_own(self, conn, name, raw_ids):
        """Keep the plate / duplicate-key index of this device's raw records in step with the raw table."""
        rows = {record_id(name, r): r for r in _raw_by_ids(conn, name, raw_ids)}
        for raw_id in raw_ids:
            row = rows.get(raw_id)
            if row is None:
                conn.execute("DELETE FROM cleansing_own WHERE source=? AND raw_id=?", (name, raw_id))
            else:
                plate = plate_of(name, row)
                conn.execute("INSERT OR REPLACE INTO cleansing_own VALUES (?, ?, ?, ?)", (name, raw_id, plate, duplicate_key(name, row)))
                self._index(conn, [plate])
                if name == "RTO" and rules.clean_text(row.get("make")):
                    conn.execute("INSERT OR IGNORE INTO cleansing_makes VALUES (?)", (rules.clean_text(row.get("make")),))

    def _date_plates(self, conn, last_day, today):
        """Plates of records whose date rule may give another answer since the last pass (the day rolled over)."""
        plates = set()
        for name in self.names:
            if name in DATE_RULES:
                spec = TABLES[name]
                plates |= {rules.normalize_plate(r[0]) for r in conn.execute(
                    f"SELECT {spec['plate']} FROM {spec['table']} WHERE {DATE_RULES[name][1]}", (last_day, today))}
        if self.device == "ANPR":
            # Coverage can start or expire even if the Insurance row itself has not changed.
            plates.update(r[0] for r in conn.execute(
                "SELECT DISTINCT plate FROM cleansing_peer WHERE source='Insurance' AND kept=1 AND "
                "((json_extract(data, '$.start_date') > ? AND json_extract(data, '$.start_date') <= ?) OR "
                "(json_extract(data, '$.end_date') >= ? AND json_extract(data, '$.end_date') < ?))",
                (last_day, today, last_day, today)))
        return plates - {""}

    def _affected(self, conn, ids, plates):
        """Grow the changed records to everything a re-clean must look at again: records of the changed plates and of
        look-alike plates, the cleaned rows they belong to, and their duplicate groups."""
        plates = set(plates)
        for plate in list(plates):
            plates |= self._near(conn, plate)
        plates.discard("")
        done = set()
        while True:
            fresh = plates - done
            done |= fresh
            grown = False
            for name in self.names:
                before = len(ids[name])
                ids[name] |= {r[0] for r in _select_in(conn, "SELECT raw_id FROM cleansing_own WHERE source=? AND plate IN ({})", fresh, (name,))}
                ids[name] |= {r[0] for r in _select_in(conn, "SELECT raw_id FROM cleansing_log WHERE source=? AND plate_to IN ({})", fresh, (name,))}
                dups = {r[0] for r in _select_in(conn, "SELECT dup FROM cleansing_own WHERE source=? AND dup IS NOT NULL AND raw_id IN ({})",
                                                 ids[name], (name,))}
                ids[name] |= {r[0] for r in _select_in(conn, "SELECT raw_id FROM cleansing_own WHERE source=? AND dup IN ({})", dups, (name,))}
                groups = {r[0] for r in _select_in(conn, "SELECT row_id FROM cleansing_log WHERE source=? AND raw_id IN ({})", ids[name], (name,))}
                ids[name] |= {r[0] for r in _select_in(conn, "SELECT raw_id FROM cleansing_log WHERE source=? AND row_id IN ({})", groups, (name,))}
                ids[name] |= groups
                plates |= {r[0] for r in _select_in(conn, "SELECT plate FROM cleansing_own WHERE source=? AND raw_id IN ({})", ids[name], (name,))}
                plates |= {r[0] for r in _select_in(conn, "SELECT plate_to FROM cleansing_log WHERE source=? AND raw_id IN ({})", ids[name], (name,))}
                grown = grown or len(ids[name]) != before
            plates.discard("")
            plates.discard(None)
            if not grown and plates <= done:
                return ids

    # ---------- one pass ----------

    def run(self, round_=0):
        """Clean what changed. Returns {"status": cleaned | unchanged | pending, ...}."""
        with self._lock:
            now = datetime.now()
            today, stamp = now.date().isoformat(), now.strftime("%Y-%m-%d %H:%M:%S")
            conn = self._connect()
            result = None
            try:
                peer_state = self._get(conn, "peers", {})
                fetched, down = {}, []
                for device in SOURCE_DEVICES:
                    if device != self.device:
                        seen = peer_state.get(device) or {}
                        data = self.fetch(device, seen.get("version", 0), seen.get("epoch"))
                        if data is None:
                            down.append(device)
                        else:
                            fetched[device] = data
                if down:
                    pending = self._get(conn, "pending") or {"since": stamp}
                    pending["down"] = down
                    self._set(conn, "pending", pending)
                    conn.commit()
                    return {"status": "pending", "down": down, "mot": {}}
                plates = set()
                for device, data in fetched.items():
                    plates |= self._apply_peer(conn, device, data)
                    peer_state[device] = {"version": data.get("version", 0), "epoch": data.get("epoch"),
                                          "built": bool(data.get("built"))}
                self._set(conn, "peers", peer_state)
                built = self._get(conn, "built", False)
                last_change = conn.execute("SELECT MAX(change_id) FROM cleansing_changes").fetchone()[0] or 0
                changes = conn.execute("SELECT source, raw_id, old_plate, new_plate FROM cleansing_changes WHERE change_id<=?",
                                       (last_change,)).fetchall()
                last_day = self._get(conn, "day")
                if built and last_day and last_day != today:
                    plates |= self._date_plates(conn, last_day, today)
                mot_ready = self.device == "ANPR" and all(peer_state.get(d, {}).get("built") for d in fetched)
                mot_everything = mot_ready and not self._get(conn, "mot_coverage_checked", False)
                if built and not changes and not plates and not mot_everything:
                    self._set(conn, "pending", None)
                    self._set(conn, "day", today)
                    mot = self._send_unsent(conn) if self.device == "ANPR" else {}
                    conn.commit()
                    return {"status": "unchanged", "mot": mot}

                ids = {name: set() for name in self.names}
                if not built:                            # first pass: every record, from a clean slate
                    for name in self.names:
                        for table in ("cleansing_own", "cleansing_resolved", "cleansing_log"):
                            conn.execute(f"DELETE FROM {table} WHERE source=?", (name,))
                        conn.execute(f"DELETE FROM {TABLES[name]['table']}_clean")
                        all_ids = [record_id(name, r) for r in self._raw_rows(conn, name)]
                        self._sync_own(conn, name, all_ids)
                        ids[name] = set(all_ids)
                    self._index(conn, [r[0] for r in conn.execute("SELECT DISTINCT plate FROM cleansing_peer")])
                else:
                    by_name = {}
                    for source, raw_id, old, new in changes:
                        if source in ids:
                            by_name.setdefault(source, set()).add(str(raw_id))
                            plates |= {rules.normalize_plate(old), rules.normalize_plate(new)}
                    for name, changed_ids in by_name.items():
                        self._sync_own(conn, name, changed_ids)
                        ids[name] |= changed_ids
                    ids = self._affected(conn, ids, plates - {""})
                result = self._clean_records(conn, ids, now, stamp)
                conn.execute("DELETE FROM cleansing_changes WHERE change_id<=?", (last_change,))
                if not self._get(conn, "epoch"):
                    self._set(conn, "epoch", uuid.uuid4().hex)
                for name, value in (("built", True), ("day", today), ("pending", None), ("last_cleaned", stamp)):
                    self._set(conn, name, value)
                mot = {}
                if self.device == "ANPR":
                    if mot_ready:                        # never report from data the others have not cleaned yet
                        check = None if (mot_everything or not built) else plates | result["plates"]
                        mot = self._check_mot(conn, check, now)
                        self._set(conn, "mot_coverage_checked", True)
                    else:
                        self._send_unsent(conn)
                conn.commit()
            finally:
                conn.close()
            self._learn(result)
            if result["changed"] and round_ < MAX_ROUNDS:
                self.notify_peers(round_ + 1)
            return {"status": "cleaned", "changed": result["changed"], "mot": mot}

    def _clean_records(self, conn, ids, now, stamp):
        """Re-clean the given raw records and replace their cleaned rows, report lines and evidence versions."""
        replaced = {name: set(ids[name]) | {r[0] for r in _select_in(
            conn, "SELECT row_id FROM cleansing_log WHERE source=? AND raw_id IN ({})", ids[name], (name,))}
            for name in self.names}
        match_world = DbWorld(conn, self.names, "match")
        resolved, decisions = {}, {}
        for name in self.names:
            resolved[name], decisions[name] = resolve(name, _raw_by_ids(conn, name, ids[name]), match_world, self._dictionary)
        overlay = {name: {} for name in self.names}
        for name in self.names:
            for row in resolved[name]:
                overlay[name].setdefault(plate_of(name, row), []).append(row)
        check_world = DbWorld(conn, self.names, "check", overlay, replaced)
        kept = {name: validate(name, resolved[name], check_world, decisions[name], now) for name in self.names}

        version = self._get(conn, "version", 0) + 1
        changed, plates = False, set()
        for name in self.names:
            spec = TABLES[name]
            new = {record_id(name, r): r for r in resolved[name]}
            kept_rows = {record_id(name, r): r for r in kept[name]}
            touched = replaced[name] | set(new)
            previous = {r[0]: (r[1], r[2], r[3]) for r in _select_in(
                conn, "SELECT row_id, plate, data, kept FROM cleansing_resolved WHERE source=? AND row_id IN ({})", touched, (name,))}
            for row_id in touched:
                row = kept_rows.get(row_id) or new.get(row_id)
                state = (plate_of(name, row), _dump(row), int(row_id in kept_rows)) if row else (None, None, 0)
                old = previous.get(row_id)
                if old == state or (old is None and row is None):
                    continue
                conn.execute("INSERT OR REPLACE INTO cleansing_resolved VALUES (?, ?, ?, ?, ?, ?)",
                             (name, row_id, state[0], state[1], state[2], version))
                changed = True
                plates |= {state[0], old[0] if old else None}
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({spec['table']}_clean)")]
            _select_in(conn, f"DELETE FROM {spec['table']}_clean WHERE {spec['id']} IN ({{}})", touched)
            conn.executemany(f"INSERT INTO {spec['table']}_clean VALUES ({', '.join('?' for _ in columns)})",
                             [tuple(r.get(c) for c in columns) for r in kept[name]])
            _select_in(conn, "DELETE FROM cleansing_log WHERE source=? AND raw_id IN ({})", ids[name], (name,))
            conn.executemany("INSERT INTO cleansing_log VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
                (name, rid, d["row_id"], d["plate_to"], int(not trivial(d)),
                 int(any(n["action"] in ("corrected", "normalised") for n in d["notes"])), _dump(d), stamp)
                for rid, d in decisions[name].items()])
        if changed:
            self._set(conn, "version", version)
        return {"changed": changed, "plates": plates - {None, ""}, "kept": kept, "decisions": decisions}

    def _learn(self, result):
        """The dictionary grows with the plates kept and the misreads corrected (as supporting evidence only)."""
        if not self.book or not result:
            return
        for name in self.names:
            self.book.add_plates(name, {plate_of(name, row) for row in result["kept"][name]})
            for d in result["decisions"][name].values():
                if any(n.get("action") == "corrected" for n in d["notes"]):
                    self.book.learn(name, d["plate_to"], d["plate_from"], origin="cleansing")

    # ---------- MoT (ANPR only) ----------

    def _kept_for(self, conn, plate):
        kept = {name: [json.loads(r[0]) for r in conn.execute(
            "SELECT data FROM cleansing_peer WHERE source=? AND plate=? AND kept=1", (name, plate))]
            for name in TABLES if name not in self.names}
        for name in self.names:
            spec = TABLES[name]
            kept[name] = [dict(r) for r in conn.execute(f"SELECT * FROM {spec['table']}_clean WHERE {spec['plate']}=?", (plate,))]
        return kept

    def _check_mot(self, conn, plates, now):
        """Report the vehicles on the road whose state changed (`plates`; None = every captured vehicle)."""
        spec = TABLES["ANPR"]
        if plates is None:
            plates = {r[0] for r in conn.execute(f"SELECT DISTINCT {spec['plate']} FROM {spec['table']}_clean")}
        flags = {}
        for plate in sorted(p for p in plates if p):
            kept = self._kept_for(conn, plate)
            if not kept["ANPR"]:
                continue
            view = vehicle_view(plate, kept, now.date())
            signature = _dump(view_signature(view))
            row = conn.execute("SELECT signature FROM cleansing_mot WHERE plate=?", (plate,)).fetchone()
            if row and row[0] == signature:
                continue
            conn.execute("INSERT OR REPLACE INTO cleansing_mot VALUES (?, ?)", (plate, signature))
            if view["suspicious"]:
                conn.execute("INSERT OR REPLACE INTO cleansing_mot_unsent VALUES (?, ?)", (plate, _dump(dict(mot_report(view, now), event_id=uuid.uuid4().hex))))
                flags[plate] = view["risk_flags"]
            else:
                conn.execute("DELETE FROM cleansing_mot_unsent WHERE plate=?", (plate,))
        # Persist the delivery ID before MoT can acknowledge this event.
        conn.commit()
        sent = self._send_unsent(conn)
        return {plate: {"status": sent.get(plate, "waiting"), "flags": f} for plate, f in flags.items()}

    def _send_unsent(self, conn):
        url = self.peers.get("MoT")
        results = {}
        for plate, report in conn.execute("SELECT plate, report FROM cleansing_mot_unsent ORDER BY rowid").fetchall():
            ok = bool(url) and self.post(url.rstrip("/") + "/reports", json.loads(report))
            results[plate] = "sent" if ok else "waiting"
            if ok:
                conn.execute("DELETE FROM cleansing_mot_unsent WHERE plate=?", (plate,))
        return results

    # ---------- triggers ----------

    def after_change(self, name, raw_id=None, before=None):
        """Clean right after a record was added / edited / deleted here; the popup text ("" = nothing to say)."""
        outcome = self.run()
        if outcome["status"] == "pending":
            down = outcome["down"]
            one = len(down) == 1
            return (f"Saved. {_join(down)} {'is' if one else 'are'} down, so data cleaning is pending. "
                    f"It will run automatically when {'it is' if one else 'they are'} back.")
        if raw_id is None:
            return ""
        decision = self.decision(name, raw_id)
        if decision is None or (before is not None and before == decision):
            return ""
        text = describe(decision)
        if not text and not trivial(before):
            text = "It is now kept in cleaned data as it is."
        report = outcome.get("mot", {}).get(decision["plate_to"])
        if report:
            text += (f" {decision['plate_to']} is flagged ({', '.join(report['flags'])}); "
                     + ("a report was sent to MoT." if report["status"] == "sent"
                        else "MoT is not reachable, so the report will be sent automatically later."))
        return ("Saved. " + text.strip()) if text.strip() else ""

    def request_run(self, round_=0):
        """Another device's cleaned data changed: re-clean in the background (requests are coalesced)."""
        with self._queue_lock:
            self._queued = round_ if self._queued is None else max(self._queued, round_)
            if self._worker:
                return
            self._worker = True
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while True:
            with self._queue_lock:
                if self._queued is None:
                    self._worker = False
                    return
                round_, self._queued = self._queued, None
            self._safe_run(round_)

    def _safe_run(self, round_=0):
        try:
            return self.run(round_)
        except Exception:                                   # a failed pass must never stop the service
            traceback.print_exc()
            return None

    def start(self):
        """Service start: clean now, then every `interval` seconds pick up pending work and missed changes."""
        def loop():
            while True:
                self._safe_run()
                time.sleep(self.interval)
        threading.Thread(target=loop, daemon=True).start()

    # ---------- pages ----------

    def banner(self):
        status = self.status()
        pending = status.get("pending")
        if not pending:
            return ""
        down = pending.get("down") or []
        waiting = sum(len(v) for v in pending.get("records", {}).values())
        one = len(down) == 1
        what = f"{waiting} changed record(s) waiting" if status["built"] else "the first cleaning is waiting"
        return (f'<div class="cl-banner">&#9888; {escape(_join(down))} {"is" if one else "are"} currently down, so data '
                f'cleaning is paused ({what} since {escape(str(pending.get("since")))}). '
                f'It will run automatically when {"it is" if one else "they are"} back.</div>')

    def home_html(self, message=""):
        """Pending warning + the popup shown after a record is added / edited / deleted."""
        popup = ""
        if message:
            popup = (f'<div class="cl-overlay" onclick="this.remove()"><div class="cl-popup" onclick="event.stopPropagation()">'
                     f'<h3>Data cleansing</h3><p>{escape(message)}</p><p><a href="/cleansing-report">Open the cleansing report</a></p>'
                     f'<button type="button" onclick="this.closest(\'.cl-overlay\').remove()">OK</button></div></div>')
        return HOME_STYLE + self.banner() + popup

    def _page(self, title, body):
        nav = " &nbsp;|&nbsp; ".join(f'<a href="{href}">{text}</a>' for href, text in (
            ("/", "&larr; Home"), ("/search", "Search"), ("/cleaned", "Cleaned data"),
            ("/cleansing-report", "Cleansing report"), ("/plates/corrected", "Corrected number plates"),
            ("/plates/dictionary", "Plate dictionary")))
        last = self.status().get("last_cleaned")
        return (f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title>{PAGE_STYLE}{HOME_STYLE}'
                f'</head><body><h2>{escape(title)} &mdash; {escape(self.device)}</h2><p>{nav}</p>{self.banner()}'
                f'<p class="muted">Last cleaned: {escape(str(last or "not yet"))}</p>{body}</body></html>')

    def _log_rows(self, conn, name, where, limit=PAGE_ROWS):
        return [json.loads(r[0]) for r in conn.execute(
            f"SELECT decision FROM cleansing_log WHERE source=? AND {where} ORDER BY CAST(raw_id AS INTEGER) LIMIT ?", (name, limit))]

    def search_page(self, query):
        q = rules.normalize_plate(query)
        body = (f'<form method="get"><input type="text" name="q" value="{escape(query)}" placeholder="Plate, e.g. MH12AB1234">'
                '<button type="submit">Search</button></form>')
        if not q:
            return self._page("Search", body + "<p>Exact and approximate plate search over the raw and the cleaned data.</p>")

        def why(plate):
            if plate == q:
                return "exact plate"
            if q in plate:
                return f"contains {q}"
            slips = rules.levenshtein_distance(q, plate)
            return (f"{round(rules.weighted_plate_similarity(q, plate) * 100, 1)}% similar, "
                    f"{slips} character{'s' if slips != 1 else ''} different")

        def read(conn):
            nonlocal body
            # the exact plate and every similar one: a read can be wrong and the right plate may be stored too
            plates = {q} | {p for p in self._near(conn, q) if rules.weighted_plate_similarity(q, p) >= rules.MATCH_THRESHOLD
                            or rules.levenshtein_distance(q, p) <= 2}
            plates |= {r[0] for r in conn.execute("SELECT DISTINCT plate FROM cleansing_own WHERE plate LIKE ? LIMIT ?",
                                                  (f"%{q}%", PAGE_ROWS))}
            for name in self.names:
                spec = TABLES[name]
                raw_ids = [r[0] for r in _select_in(conn, "SELECT raw_id FROM cleansing_own WHERE source=? AND plate IN ({})",
                                                    plates, (name,))][:PAGE_ROWS]
                decisions = {r[0]: json.loads(r[1]) for r in _select_in(
                    conn, "SELECT raw_id, decision FROM cleansing_log WHERE source=? AND raw_id IN ({})", raw_ids, (name,))}
                raw_hits = [{"why matched": why(plate_of(name, r)), **r,
                             "in cleaned data": describe(decisions.get(record_id(name, r))) or "kept as it is"}
                            for r in _raw_by_ids(conn, name, raw_ids)]
                clean_hits = []
                for r in _select_in(conn, f"SELECT * FROM {spec['table']}_clean WHERE {spec['plate']} IN ({{}})", plates):
                    r = dict(r)
                    noisy = sorted({json.loads(d[0])["plate_from"] for d in _select_in(
                        conn, "SELECT decision FROM cleansing_log WHERE source=? AND raw_id IN ({})",
                        str(r.get("raw_ids") or "").split(","), (name,))} - {plate_of(name, r)})
                    noisy = [p for p in noisy if rules.normalize_plate(p) != plate_of(name, r)]
                    clean_hits.append({"why matched": why(plate_of(name, r)) + (f" (cleaned from {', '.join(noisy)})" if noisy else ""), **r})
                body += (f"<h3>{escape(name)}: raw data ({len(raw_hits)})</h3>{_table(raw_hits)}"
                         f"<h3>{escape(name)}: cleaned data ({len(clean_hits)})</h3>{_table(clean_hits[:PAGE_ROWS])}")
        self._read(read, None)
        return self._page("Search", body)

    def cleaned_page(self):
        def read(conn):
            if not self._get(conn, "built", False):
                return "<p>Not cleaned yet.</p>"
            out = ("<p>Raw records stay exactly as entered; this is the cleaned copy. "
                   "<i>raw_ids</i> = the raw records it comes from.</p>")
            for name in self.names:
                spec = TABLES[name]
                total = conn.execute(f"SELECT COUNT(*) FROM {spec['table']}_clean").fetchone()[0]
                rows = [dict(r) for r in conn.execute(f"SELECT * FROM {spec['table']}_clean ORDER BY {spec['id']} LIMIT ?", (PAGE_ROWS,))]
                shown = f" (first {PAGE_ROWS} shown)" if total > PAGE_ROWS else ""
                out += f"<h3>{escape(name)} ({total} records{shown})</h3>{_table(rows)}"
            return out
        return self._page("Cleaned data", self._read(read, "<p>Not cleaned yet.</p>"))

    def report_page(self):
        def read(conn):
            out = ("<p>What automatic cleansing did with the raw records of this database, and why. "
                   "Records kept exactly as they are not listed.</p>")
            for name in self.names:
                rows = []
                for d in self._log_rows(conn, name, "notable=1"):
                    result = ", ".join(dict.fromkeys(RESULT_TEXT[n["action"]] for n in d["notes"] if n["action"] in RESULT_TEXT))
                    rows.append(f'<tr id="{escape(name)}-{escape(d["raw_id"])}"><td>#{escape(d["raw_id"])}</td>'
                                f'<td><b>{escape(d["plate_from"])}</b></td><td>{escape(result)}</td><td>{escape(describe(d))}</td></tr>')
                out += (f"<h3>{escape(name)}</h3>" + ("<table><tr><th>Record</th><th>Raw plate</th><th>Result</th><th>Why</th></tr>"
                        + "".join(rows) + "</table>" if rows else '<p class="ok">Every record is kept as it is.</p>'))
            return out
        return self._page("Cleansing report", self._read(read, ""))

    def corrected_page(self):
        def read(conn):
            out = ("<p>Plates corrected automatically. The noisy plate stays in the raw table; the corrected plate is used "
                   "in the cleaned data.</p>")
            for name in self.names:
                rows = []
                for d in self._log_rows(conn, name, "corrected=1"):
                    note = next(n for n in d["notes"] if n["action"] in ("corrected", "normalised"))
                    how = "; ".join(note.get("evidence") or []) or "only formatting (spaces, case, dashes) removed"
                    rows.append(f'<tr><td>#{escape(d["raw_id"])}</td><td><b>{escape(d["plate_from"])}</b></td>'
                                f'<td><b>{escape(d["plate_to"])}</b></td><td>{escape(how)}</td></tr>')
                out += (f"<h3>{escape(name)}</h3>" + ("<table><tr><th>Record</th><th>Noisy plate (raw)</th><th>Corrected plate</th>"
                        "<th>Evidence</th></tr>" + "".join(rows) + "</table>" if rows else '<p class="ok">No plate needed correcting.</p>'))
            return out
        return self._page("Corrected number plates", self._read(read, ""))

    # ---------- routes ----------

    def register(self, app):
        from flask import jsonify, request

        def notify():
            try:
                round_ = int(request.args.get("round", "0"))
            except ValueError:
                round_ = 0
            self.request_run(round_)
            return jsonify({"queued": True}), 202

        def evidence():
            try:
                since = int(request.args.get("since", "0") or 0)
            except ValueError:
                since = 0
            return jsonify(self.evidence(since, request.args.get("epoch") or None))

        def shaped(data):
            if self.device == "Theft/Shredding":
                return jsonify({"theft_reports": data["Theft"], "shredding_records": data["Shredding"]})
            return jsonify(data[self.names[0]])

        def records():
            return shaped({name: self.clean_rows(name) for name in self.names})

        def lookup():
            """Only the cleaned records of the plates the client asks for (Vehicle 360 sends the plates on the road)."""
            plates = (request.get_json(silent=True) or {}).get("plates") or []
            return shaped({name: self.clean_rows_of(name, plates) for name in self.names})

        def candidates():
            plate = rules.normalize_plate(request.args.get("plate"))
            algorithm = request.args.get("algorithm", "weighted")
            try:
                threshold = float(request.args.get("threshold", rules.MATCH_THRESHOLD))
                slips = int(request.args.get("slips", 2))
                cost = float(request.args.get("cost", rules.CONFUSION_COST))
            except ValueError:
                return jsonify({"error": "Invalid plate matching parameters"}), 400
            if not plate or algorithm not in ("weighted", "levenshtein") or not 0 <= threshold <= 1 or slips < 0 or not 0 <= cost <= 1:
                return jsonify({"error": "Invalid plate matching parameters"}), 400
            return shaped(self.candidate_rows(plate, threshold, slips, algorithm, cost))

        def plates():
            spec = TABLES["ANPR"]
            if not self.built():
                return jsonify([p for p in dict.fromkeys(plate_of("ANPR", r) for r in self.clean_rows("ANPR")) if p])
            return jsonify(self._read(lambda conn: [r[0] for r in conn.execute(
                f"SELECT {spec['plate']} FROM {spec['table']}_clean GROUP BY {spec['plate']} ORDER BY MIN(rowid)")], []))

        app.add_url_rule("/clean/evidence", "clean_evidence", evidence)
        app.add_url_rule("/clean/status", "clean_status", lambda: jsonify(self.status()))
        app.add_url_rule("/clean/notify", "clean_notify", notify, methods=["POST"])
        app.add_url_rule("/clean/records", "clean_records", records)
        app.add_url_rule("/clean/candidates", "clean_candidates", candidates)
        app.add_url_rule("/clean/lookup", "clean_lookup", lookup, methods=["POST"])
        for name in self.names:
            app.add_url_rule(TABLES[name]["path"] + "/<path:plate>", f"clean_{name.lower()}_plate",
                             lambda plate, name=name: jsonify(self.clean_rows(name, plate)))
        if "ANPR" in self.names:
            app.add_url_rule("/clean/plates", "clean_plates", plates)
        app.add_url_rule("/search", "clean_search", lambda: self.search_page(request.args.get("q", "").strip()))
        app.add_url_rule("/cleaned", "clean_cleaned", self.cleaned_page)
        app.add_url_rule("/cleansing-report", "clean_report", self.report_page)
        app.add_url_rule("/plates/corrected", "clean_corrected", self.corrected_page)


def _table(rows):
    if not rows:
        return '<p class="muted">None.</p>'
    columns = list(dict.fromkeys(k for r in rows for k in r))
    head = "".join(f"<th>{escape(str(c))}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{escape('' if r.get(c) is None else str(r.get(c)))}</td>" for c in columns) + "</tr>"
                   for r in rows)
    return f'<div class="wrap"><table><tr>{head}</tr>{body}</table></div>'


PAGE_STYLE = """<style>body{font-family:Arial;max-width:1300px;margin:30px auto;padding:0 20px;color:#222}
table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:6px;text-align:left;font-size:13px;vertical-align:top}
th{background:#1a3d6d;color:#fff}.wrap{overflow-x:auto}.muted{color:#777}.ok{color:#217a45}h3{color:#1a3d6d;margin:24px 0 6px}
input[type=text]{padding:7px;width:260px}button{padding:7px 14px}</style>"""

HOME_STYLE = """<style>.cl-banner{background:#fff7e6;border:1px solid #f0c36d;color:#7a5200;padding:12px 15px;border-radius:8px;margin:14px 0}
.cl-overlay{position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:10000}
.cl-popup{background:#fff;width:min(520px,92%);border-radius:14px;padding:26px;box-shadow:0 10px 35px rgba(0,0,0,.25);font-family:Arial}
.cl-popup h3{margin-top:0;color:#1a3d6d}.cl-popup p{line-height:1.5;color:#333}
.cl-popup button{padding:9px 20px;border:0;border-radius:7px;background:#2563eb;color:#fff;cursor:pointer}</style>"""
