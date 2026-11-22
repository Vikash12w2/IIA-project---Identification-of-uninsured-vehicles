import os
import sys
from concurrent.futures import ThreadPoolExecutor

import requests
from datetime import date
import insurance_rules
from urllib.parse import quote, urlencode
import config

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import plate_rules as rules  # noqa: E402  (shared with the source services)


# The Vehicle 360 view reads the CLEANED data of every device (/clean/...): each device's own automatic
# cleansing has already corrected misread plates, merged duplicates and left out invalid records.
# MoT reports are sent by the ANPR device, not by this client.

# Flag the ANPR device puts on a capture whose make / model contradicts the RTO registration of its plate.
MISMATCH_FLAG = "details contradict the RTO registration"

SOURCE_SERVICES = {
    "ANPR": config.ANPR_SERVICE,
    "Insurance": config.INSURANCE_SERVICE,
    "RTO": config.RTO_SERVICE,
    "Theft/Shredding": config.THEFT_SERVICE,
}


def call(url, default=None):
    try:
        resp = requests.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        print(f"  [WARN] could not reach {url} -> {e}")
        return default if default is not None else []


def _url_plate(plate):
    """Encode a plate for a URL path, so '/', '#', '?' in a stored noisy plate reach the service intact."""
    return quote(str(plate), safe="")


def get_captures(plate):
    return call(f"{config.ANPR_SERVICE}/clean/vehicle/{_url_plate(plate)}")


def get_all_plates():
    return call(f"{config.ANPR_SERVICE}/clean/plates")


def get_policies(plate):
    return call(f"{config.INSURANCE_SERVICE}/clean/vehicle/{_url_plate(plate)}")


def get_registration(plate):
    return call(f"{config.RTO_SERVICE}/clean/vehicle/{_url_plate(plate)}")


def get_theft(plate):
    return call(f"{config.THEFT_SERVICE}/clean/theft/{_url_plate(plate)}")


def get_shred(plate):
    return call(f"{config.THEFT_SERVICE}/clean/shred/{_url_plate(plate)}")


def check_services():
    services = {
        "ANPR (Device 1)": f"{config.ANPR_SERVICE}/health",
        "Insurance (Device 2)": f"{config.INSURANCE_SERVICE}/health",
        "RTO (Device 3)": f"{config.RTO_SERVICE}/health",
        "Theft/Shred (Device 4)": f"{config.THEFT_SERVICE}/health",
        "MoT (Device 5)": f"{config.MOT_SERVICE}/health",
    }

    print("Checking connectivity to remote devices...")
    all_up = True

    for name, url in services.items():
        result = call(url, default=None)
        ok = bool(result)
        print(f"  {'OK  ' if ok else 'DOWN'}  {name:24s} {url}")
        all_up = all_up and ok

    return all_up


def get_service_status():
    services = {
        "ANPR": config.ANPR_SERVICE,
        "Insurance": config.INSURANCE_SERVICE,
        "RTO": config.RTO_SERVICE,
        "Theft/Shred": config.THEFT_SERVICE,
        "MoT": config.MOT_SERVICE,
    }

    status = {}
    for name, base_url in services.items():
        status[name] = bool(call(f"{base_url}/health", default=None))
    return status


def get_cleansing_warnings():
    """One line per device whose data cleaning is paused (a device it needs is down)."""
    warnings = []
    for name, base_url in SOURCE_SERVICES.items():
        status = call(f"{base_url}/clean/status", default=None)
        pending = status.get("pending") if isinstance(status, dict) else None
        if pending:
            down = ", ".join(pending.get("down") or [])
            warnings.append(f"{name}: data cleaning is paused because {down} is down, "
                            "so its latest changes are not cleaned yet.")
    return warnings


def evaluate_insurance(policies):
    return insurance_rules.evaluate_insurance(policies, date.today())


def build_vehicle_view(plate):
    """One vehicle's view, asking each database for that plate (used by a search)."""
    plate = plate.upper()
    return vehicle_view(plate, get_captures(plate), get_policies(plate), get_registration(plate),
                        get_theft(plate), get_shred(plate))


def lookup_records(plates):
    """The cleaned records of these plates from each device: one request per device, sent at the same time, and each
    device answers with only the records of those plates (not its whole table)."""
    payload = {"plates": sorted({str(p).upper() for p in plates})}

    def ask(base):
        try:
            resp = requests.post(f"{base}/clean/lookup", json=payload, timeout=config.REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            print(f"  [WARN] could not reach {base}/clean/lookup -> {e}")
            return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        anpr, rto, insurance, theft = pool.map(ask, [config.ANPR_SERVICE, config.RTO_SERVICE,
                                                     config.INSURANCE_SERVICE, config.THEFT_SERVICE])
    theft = theft if isinstance(theft, dict) else {}
    return {"ANPR": anpr or [], "RTO": rto or [], "Insurance": insurance or [],
            "theft": theft.get("theft_reports") or [], "shredding": theft.get("shredding_records") or []}


def build_views(plates, live=None):
    """Views of many vehicles at once: the plates go to each device in one request, each device sends back only
    their records, and the views are put together here (the main page lists every vehicle on the road)."""
    if not plates:
        return {}
    data = live[0] if live is not None else lookup_records(plates)

    def by_plate(rows, field):
        grouped = {}
        for row in rows:
            grouped.setdefault(rules.normalize_plate(row.get(field)), []).append(row)
        return grouped

    captures, policies = by_plate(data["ANPR"], "plate_number"), by_plate(data["Insurance"], "vehicle_reg_no")
    regs, thefts = by_plate(data["RTO"], "reg_number"), by_plate(data["theft"], "vehicle_no")
    shreds = by_plate(data["shredding"], "vehicle_no")
    views = {}
    for plate in plates:
        p = rules.normalize_plate(plate)
        views[str(plate).upper()] = vehicle_view(str(plate).upper(), captures.get(p, []), policies.get(p, []),
                                                 regs.get(p, []), thefts.get(p, []), shreds.get(p, []))
    return views


def vehicle_view(plate, captures, policies, reg, thefts, shreds):
    """The Vehicle 360 view of one plate from its records in the five tables."""
    insurance_status, active_policy = evaluate_insurance(policies)

    is_registered = len(reg) > 0
    is_stolen_open = any(t["status"] == "Reported" for t in thefts)
    is_shredded = len(shreds) > 0

    # Last seen is taken from the latest ANPR capture timestamp.
    last_seen = max(
        (c["capture_ts"] for c in captures),
        default=None
    )

    risk_flags = []

    if insurance_status in ("NOT_INSURED", "EXPIRED", "CANCELLED"):
        risk_flags.append("UNINSURED")

    if not is_registered:
        risk_flags.append("UNREGISTERED_OR_FAKE_PLATE")

    if is_stolen_open:
        risk_flags.append("STOLEN_VEHICLE_ON_ROAD")

    if is_shredded:
        risk_flags.append("SHREDDED_VEHICLE_STILL_ON_ROAD")

    # A sighting that does not look like the registered vehicle: possibly a cloned plate.
    if any(MISMATCH_FLAG in str(c.get("flags") or "") for c in captures):
        risk_flags.append("PLATE_DETAILS_MISMATCH")

    return {
        "plate_number": plate,
        "last_seen_on_road": last_seen,
        "times_captured": len(captures),
        "registered": is_registered,
        "owner_name": reg[0]["owner_name"] if reg else None,
        "make_model": (
            f'{reg[0]["make"]} {reg[0]["model"]}'
            if reg
            else (captures[0]["make_model_seen"] if captures else None)
        ),
        "reg_valid_upto": reg[0]["reg_valid_upto"] if reg else None,
        "insurance_status": insurance_status,
        "insurer": active_policy["insurer_name"] if active_policy else None,
        "policy_number": active_policy["policy_number"] if active_policy else None,
        "policy_end_date": active_policy["end_date"] if active_policy else None,
        "theft_status": (
            "REPORTED_STOLEN"
            if is_stolen_open
            else ("RECOVERED" if thefts else "NONE")
        ),
        "shredded": is_shredded,
        "risk_flags": risk_flags,
        "suspicious": len(risk_flags) > 0,
    }


def recommend_action(view):
    flags = view["risk_flags"]

    if (
        "SHREDDED_VEHICLE_STILL_ON_ROAD" in flags
        or "STOLEN_VEHICLE_ON_ROAD" in flags
    ):
        return "IMMEDIATE INTERCEPT - notify police & MoT"

    if "PLATE_DETAILS_MISMATCH" in flags:
        return "VERIFY - possible cloned plate"

    if "UNREGISTERED_OR_FAKE_PLATE" in flags:
        return "IMPOUND & VERIFY - notify MoT & RTO"

    if "UNINSURED" in flags:
        return "ISSUE NOTICE - fine + compulsory insurance within 15 days"

    return "NO ACTION"


def integrate_current_vehicles(plates, previous_views=None, baseline=False):
    """
    Query all four source devices and compare the current Vehicle 360 Views
    with the previous integrated state.

    A vehicle counts as changed when it is new or any integrated value has
    changed. On the first Client GUI load, baseline=True only records the
    current state. (Reporting to MoT is done by the ANPR device.)
    """
    previous_views = previous_views or {}
    current_views = {}
    changed = 0
    views = build_views(sorted({str(p).upper() for p in plates}))

    for plate, view in sorted(views.items()):
        view["action"] = recommend_action(view)
        current_views[plate] = view

        if not baseline and previous_views.get(plate) != view:
            changed += 1

    return current_views, changed


def run_integration():
    all_up = check_services()

    if not all_up:
        print(
            "\nOne or more remote devices are unreachable. "
            "Fix config.py / start the missing service(s) before "
            "running the full sweep.\n"
        )

    plates = get_all_plates()
    print(f"\n{len(plates)} distinct plates retrieved from Device 1 (ANPR).\n")

    all_views = [build_vehicle_view(p) for p in plates]
    flagged = [v for v in all_views if v["suspicious"]]

    return all_views, flagged


if __name__ == "__main__":
    all_views, flagged = run_integration()

    print("=" * 100)
    print("VEHICLE 360 VIEW - built from the cleaned data of 4 remote devices")
    print("=" * 100)

    for v in all_views:
        print(
            f"\nPlate: {v['plate_number']}  | "
            f"Owner: {v['owner_name']}  | {v['make_model']}"
        )
        print(
            f"  Insurance: {v['insurance_status']}   "
            f"Theft: {v['theft_status']}   "
            f"Shredded: {v['shredded']}"
        )
        print(
            f"  Risk flags: "
            f"{v['risk_flags'] if v['risk_flags'] else 'None'}"
        )

    print("\n" + "=" * 100)
    print(
        f"{len(flagged)} vehicle(s) flagged "
        "(the ANPR device reports them to MoT Device 5)"
    )
    print("=" * 100)

    for v in flagged:
        print(
            f"  {v['plate_number']:12s} | "
            f"{', '.join(v['risk_flags']):45s} | "
            f"{recommend_action(v)}"
        )


# ============================================================
# PHASE 2 — APPROXIMATE PLATE CANDIDATE DISCOVERY (search only)
# ============================================================
#
# When a searched plate has no exact match, the closest plates of every device are shown as candidates.
# The user selects one to view that vehicle. Nothing is saved, remembered or changed.

normalize_plate = rules.normalize_plate
levenshtein_distance = rules.levenshtein_distance
plate_similarity = rules.plate_similarity
PLATE_FORMAT = rules.PLATE_FORMAT
plate_format_valid = rules.plate_format_valid


def _fetch_live_records(service_name, base_url, endpoint):
    """Fetch a service's current record set; errors are returned, never hidden."""
    url = f"{base_url}{endpoint}"
    try:
        response = requests.get(url, timeout=config.REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, (list, dict)):
            raise ValueError("service response must be a JSON list or object")
        return payload, None
    except (requests.exceptions.RequestException, ValueError) as exc:
        return None, f"{service_name}: {url} — {exc}"


# Better approximate matching: the weighted (look-alike aware) distance is in plate_rules.py;
# the client passes its own PLATE_CONFUSION_COST from config.

def weighted_plate_distance(left, right, confusion_cost=None):
    """Levenshtein distance where OCR look-alike substitutions are cheap (cost from config)."""
    return rules.weighted_plate_distance(left, right, config.PLATE_CONFUSION_COST if confusion_cost is None else confusion_cost)


def weighted_plate_similarity(left, right):
    return rules.weighted_plate_similarity(left, right, config.PLATE_CONFUSION_COST)


def plate_score(left, right):
    """Plate-string score used for candidate discovery (algorithm set in config)."""
    if config.PLATE_MATCH_ALGORITHM == "levenshtein":
        return plate_similarity(left, right)
    return weighted_plate_similarity(left, right)


# ------------------------------------------------------------
# Context (colour, vehicle type, make/model)
#
# Only ANPR (vehicle_color, vehicle_type, make_model_seen) and RTO
# (make, model) carry vehicle attributes. A candidate plate's profile is
# built from every ANPR/RTO record that holds that plate. Observed context
# (from the scanned image) is compared to it.
# ------------------------------------------------------------

normalize_context = rules.normalize_context
context_comparison = rules.context_comparison


def _vehicle_profile(rows_anpr, rows_rto):
    """Cross-database profile for the Vehicle 360 search: ANPR colour/type/model plus RTO make/model."""
    return rules.vehicle_profile(
        [{"color": r.get("vehicle_color"), "vehicle_type": r.get("vehicle_type"), "make_model": r.get("make_model_seen")}
         for r in rows_anpr]
        + [{"make_model": f'{r.get("make") or ""} {r.get("model") or ""}'} for r in rows_rto])


def combined_confidence(plate_sim, ctx_score):
    """Blend plate similarity with context agreement (weight from config). No evidence -> plate score only."""
    return rules.combined_confidence(plate_sim, ctx_score, config.PLATE_CONTEXT_WEIGHT)


def _load_candidate_data(plate, threshold, slips):
    """Send the noisy read to every source and collect only matching records."""
    params = urlencode({"plate": plate, "threshold": threshold, "slips": slips,
                        "algorithm": config.PLATE_MATCH_ALGORITHM, "cost": config.PLATE_CONFUSION_COST})
    endpoint = "/clean/candidates?" + params
    plate_fields = {"ANPR": "plate_number", "RTO": "reg_number", "Insurance": "vehicle_reg_no"}
    data = {"ANPR": [], "RTO": [], "Insurance": [], "theft": [], "shredding": []}
    errors, queried = [], []
    with ThreadPoolExecutor(max_workers=len(SOURCE_SERVICES)) as pool:
        responses = {source: pool.submit(_fetch_live_records, source, base, endpoint)
                     for source, base in SOURCE_SERVICES.items()}
    for source, response in responses.items():
        payload, error = response.result()
        if error:
            errors.append(error)
            continue
        if source != "Theft/Shredding":
            if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
                errors.append(f"{source}: unexpected /clean/candidates response format")
                continue
            if any(plate_fields[source] not in row for row in payload):
                errors.append(f"{source}: one or more records lack the expected plate field")
                continue
            data[source] = payload
        else:
            if (not isinstance(payload, dict)
                    or not isinstance(payload.get("theft_reports"), list)
                    or not isinstance(payload.get("shredding_records"), list)
                    or any(not isinstance(r, dict) for r in payload["theft_reports"])
                    or any(not isinstance(r, dict) for r in payload["shredding_records"])):
                errors.append("Theft/Shredding: unexpected /clean/candidates response format")
                continue
            data["theft"], data["shredding"] = payload["theft_reports"], payload["shredding_records"]
        queried.append(source)
    return data, errors, queried


def find_plate_candidates(entered_plate, threshold=None, max_results=None, context=None, live=None, max_slips=0):
    """Search the cleaned records across ANPR, RTO, insurance, theft and shredding.

    Candidate discovery is read-only. Candidates are filtered by plate score
    (`threshold`); a plate at most `max_slips` characters different (Levenshtein)
    is kept too, so a misread search still shows the plates it may have been.
    Candidates are ranked by `confidence`, which blends the plate score
    with agreement between the observed `context` (color / vehicle_type /
    make_model) and the candidate's ANPR/RTO profile. Without context,
    confidence equals the plate score. `live` lets callers reuse one fetch
    (data, errors, queried) across many searches.
    """
    entered_normalized = normalize_plate(entered_plate)
    threshold = config.PLATE_MATCH_THRESHOLD if threshold is None else float(threshold)
    max_results = config.PLATE_MATCH_MAX_RESULTS if max_results is None else int(max_results)
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1")
    if max_results < 1:
        raise ValueError("max_results must be at least 1")
    ctx = normalize_context(context)
    if not entered_normalized:
        return {
            "query": str(entered_plate or ""), "normalized_query": "",
            "threshold": threshold, "candidates": [], "service_errors": [],
            "services_queried": [], "context": ctx, "query_in_registry": False,
        }

    data, errors, queried = live if live is not None else _load_candidate_data(entered_normalized, threshold, max_slips)
    grouped = {}

    def add_record(source, plate, record, record_type=None):
        normalized = normalize_plate(plate)
        if not normalized:
            return
        score = plate_score(entered_normalized, normalized)
        slips = levenshtein_distance(entered_normalized, normalized)
        if score < threshold and slips > max_slips:
            return
        item = grouped.setdefault(normalized, {
            "candidate_plate": str(plate).strip(),
            "normalized_plate": normalized,
            "similarity": round(score, 4),
            "slips": slips,
            "sources": [],
            "records_by_source": {},
        })
        if source not in item["sources"]:
            item["sources"].append(source)
        bucket = item["records_by_source"].setdefault(source, [])
        decorated = dict(record)
        if record_type:
            decorated["record_type"] = record_type
        if decorated not in bucket:
            bucket.append(decorated)

    for row in data["ANPR"]:
        add_record("ANPR", row.get("plate_number"), row)
    for row in data["RTO"]:
        add_record("RTO", row.get("reg_number"), row)
    for row in data["Insurance"]:
        add_record("Insurance", row.get("vehicle_reg_no"), row)
    for row in data["theft"]:
        add_record("Theft/Shredding", row.get("vehicle_no"), row, "theft")
    for row in data["shredding"]:
        add_record("Theft/Shredding", row.get("vehicle_no"), row, "shredding")

    registry = {normalize_plate(r.get("reg_number")) for r in data["RTO"]}
    for item in grouped.values():
        norm = item["normalized_plate"]
        profile = _vehicle_profile(
            [r for r in data["ANPR"] if normalize_plate(r.get("plate_number")) == norm],
            [r for r in data["RTO"] if normalize_plate(r.get("reg_number")) == norm],
        )
        comparison = context_comparison(ctx, profile)
        item["context"] = comparison
        item["confidence"] = round(combined_confidence(item["similarity"], comparison["score"]), 4)
        item["in_registry"] = norm in registry
        item["format_valid"] = plate_format_valid(norm)

    candidates = sorted(
        grouped.values(),
        key=lambda c: (c["normalized_plate"] != entered_normalized, -c["confidence"], -c["similarity"], c["normalized_plate"]),
    )[:max_results]
    for item in candidates:
        item["sources"].sort()

    return {
        "query": str(entered_plate),
        "normalized_query": entered_normalized,
        "threshold": threshold,
        "candidates": candidates,
        "service_errors": errors,
        "services_queried": queried,
        "context": ctx,
        "query_in_registry": entered_normalized in registry,
    }
