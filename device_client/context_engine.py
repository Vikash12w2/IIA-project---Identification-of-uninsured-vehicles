"""Phase 3: a free-text query federated to the five device databases, the context they discover through their FK-PK
joins accumulated across databases, and the unstructured documents that match that context integrated with it.

  round 1  the parsed query (plates, colours, makes, places, keywords) goes to every device; each device matches its
           own records and follows its FK-PK joins outwards
  round 2  the keys round 1 discovered (plates, owner / holder names, FIR, policy, chassis numbers) go to every device,
           so a database finds what another database knows about the same vehicle (scores x HOP_DECAY)
  round 3  the accumulated context (weighted terms, plates, identifiers, places and names) searches every device's
           documents and the client's news; documents are linked to the vehicles they mention

List queries ("cars with more than 2 violations in the last 3 months, owners under 30, from Pune") go another way:
  count    the device that records the event counts it per vehicle over the period (/context/aggregate)
  profile  RTO answers owner, age (driving licence) and origin of the vehicles that pass, keeping only those that match
           the age / origin asked for; Insurance and Theft/Shredding answer their status (/context/profile)
Every result is kept for a while, so the next query can narrow it down ("within" a previous result).
"""
import os
import time
import uuid
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import requests

import config
import context_search as cs

DEVICES = {"ANPR": config.ANPR_SERVICE, "Insurance": config.INSURANCE_SERVICE, "RTO": config.RTO_SERVICE,
           "Theft/Shredding": config.THEFT_SERVICE, "MoT": config.MOT_SERVICE}
HOP_DECAY = 0.7          # context found through another database's keys counts a little less
KEY_SCORE = 0.45         # only entities at least this related to the query pass their keys on to round 2
MAX_KEY_ENTITIES = 8     # ... and at most this many of them
MAX_VEHICLES = 6
MAX_TERMS = 25
MAX_DOCUMENTS = 12
MIN_RELEVANCE = 0.2      # a document below this share of the best score needs a plate or number link to stay
NEWS = cs.DocIndex(os.path.join(os.path.dirname(os.path.abspath(__file__)), "documents"), "News")


def http_post(base, path, payload):
    response = requests.post(base.rstrip("/") + path, json=payload, timeout=config.REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def federate(path, payload, post=http_post, devices=None):
    """The same request to every device at once: {device: {"result", "error", "ms", "address"}}."""
    devices = devices or DEVICES

    def one(item):
        device, base = item
        start = time.time()
        try:
            result, error = post(base, path, payload), None
        except Exception as exc:                  # a device that is down must not stop the others
            result, error = None, str(exc)
        return device, {"result": result, "error": error, "ms": round((time.time() - start) * 1000, 1), "address": base}

    with ThreadPoolExecutor(max_workers=len(devices)) as pool:
        return dict(pool.map(one, devices.items()))


def _merge(entities, answers, factor, hop):
    """Add one round's entities; one found in both rounds keeps the best score and the reasons of both."""
    for device, answer in answers.items():
        for e in (answer["result"] or {}).get("entities", []):
            e = dict(e, score=round(e["score"] * factor, 3), hops=[hop])
            old = entities.get((device, e["table"], e["id"]))
            if old is not None:
                e = dict(old, score=max(old["score"], e["score"]), hops=sorted(set(old["hops"]) | {hop}),
                         matched=list(dict.fromkeys(old["matched"] + e["matched"])))
            entities[(device, e["table"], e["id"])] = e


def _round(hop, sub, answers):
    return {"hop": hop, "subquery": sub, "devices": [
        {"device": d, "address": a["address"], "ok": a["error"] is None, "error": a["error"], "ms": a["ms"],
         "found": len((a["result"] or {}).get("entities", []))} for d, a in answers.items()]}


def _next_keys(entities, parsed):
    """Keys of the entities most related to the query, for round 2 (only those round 1 did not already ask for)."""
    top = sorted((e for e in entities.values() if e["score"] >= KEY_SCORE), key=lambda e: -e["score"])[:MAX_KEY_ENTITIES]
    keys = {"plates": [], "names": [], "ids": []}
    for e in top:
        for kind in keys:
            for value in e["keys"].get(kind, []):
                if value and value not in keys[kind] and value not in parsed["plates"]:
                    keys[kind].append(value)
    keys["ids"] = [i for i in keys["ids"] if len(cs.key(i)) >= 6]          # skip short codes such as camera ids
    return keys


def _vehicles(entities):
    vehicles = {}
    for e in entities.values():
        if not e.get("plate"):
            continue
        v = vehicles.setdefault(e["plate"], {"plate": e["plate"], "entities": [], "devices": set(), "documents": []})
        v["entities"].append(e)
        v["devices"].add(e["device"])
    out = []
    for v in vehicles.values():
        best = max(e["score"] for e in v["entities"])
        v["score"] = round(min(1.0, best + 0.05 * (len(v["devices"]) - 1)), 3)
        v["devices"] = sorted(v["devices"])
        v["entities"].sort(key=lambda e: (-e["score"], e["device"]))
        if best >= 0.4:
            out.append(v)
    out.sort(key=lambda v: (-v["score"], v["plate"]))
    return out[:MAX_VEHICLES]


def _context(parsed, vehicles):
    """Weighted terms, plates, identifiers and phrases that describe what the query is about."""
    terms = {}
    for word in parsed["terms"] + parsed["colors"] + parsed["makes"] + parsed["types"]:
        terms[word] = 2.0
    ids, phrases = [], []
    for v in vehicles:
        for e in v["entities"]:
            for kind, values in e["keys"].items():
                for value in values:
                    if kind == "ids" and value not in ids:
                        ids.append(value)
                    if kind in ("places", "names", "staff", "orgs") and len(cs.words(value)) >= 2 and value not in phrases:
                        phrases.append(value)
                    if kind in ("places", "names", "staff", "models", "orgs", "words"):
                        for w in cs.words(value):
                            if w not in cs.STOPWORDS and len(w) > 2 and not w.isdigit():
                                terms[w] = min(3.0, terms.get(w, 0.0) + e["score"])
    top = dict(sorted(terms.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_TERMS])
    return {"terms": {k: round(v, 3) for k, v in top.items()}, "plates": [v["plate"] for v in vehicles],
            "ids": ids, "phrases": phrases[:20]}


def _summary_counts(values):
    counts, labels = Counter(), {}
    for value in values:
        label = " ".join(str(value or "Unknown").split()) or "Unknown"
        key = label.casefold()
        labels.setdefault(key, label)
        counts[key] += 1
    return [(labels[key], count) for key, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))]


def _search_summary(rows, extra, **totals):
    groups = {
        "Vehicle colour": _summary_counts(r.get("color") for r in rows),
        "Vehicle type": _summary_counts(r.get("type") for r in rows),
        "Colour and vehicle type": _summary_counts(
            f"{r.get('color') or 'Unknown colour'} / {r.get('type') or 'Unknown type'}" for r in rows),
        "Make and model": _summary_counts(r.get("vehicle") for r in rows),
        "Registration city": _summary_counts(r.get("city") for r in rows),
        "Registration state": _summary_counts(r.get("state") for r in rows),
        "Registration office": _summary_counts(r.get("office") for r in rows),
        "Latest sighting location": _summary_counts(r.get("place") for r in rows),
        "Vehicle type and registration city": _summary_counts(
            f"{r.get('type') or 'Unknown type'} / {r.get('city') or 'Unknown city'}" for r in rows),
        "Vehicle type and sighting location": _summary_counts(
            f"{r.get('type') or 'Unknown type'} / {r.get('place') or 'Unknown location'}" for r in rows),
        **extra,
    }
    return {"vehicles": len(rows), **totals, "groups": {title: counts for title, counts in groups.items() if counts}}


def _context_summary(vehicles, documents, today):
    rows, records, related_types = [], [], []
    for vehicle in vehicles:
        entities = vehicle["entities"]
        records.extend(entities)
        for entity in entities:
            for kind, records_found in entity.get("related", {}).items():
                count = len(records_found) if isinstance(records_found, list) else int(bool(records_found))
                related_types.extend([kind.replace("_", " ")] * count)
        capture = max((e for e in entities if e["table"] == "vehicle_capture"),
                      key=lambda e: e["data"].get("capture_ts") or "", default={})
        registration = next((e for e in entities if e["table"] == "registration"), {})
        seen, registered = capture.get("data", {}), registration.get("data", {})
        office = registration.get("related", {}).get("rto_office") or {}
        location = capture.get("related", {}).get("location") or {}
        model = registration.get("related", {}).get("vehicle_model") or {}
        licences = registration.get("related", {}).get("driving_licence") or []
        age = cs.age_on(licences[0].get("date_of_birth"), today) if licences else None
        rows.append({"color": seen.get("vehicle_color"), "type": seen.get("vehicle_type") or model.get("body_type"),
                     "vehicle": " ".join(v for v in (registered.get("make"), registered.get("model")) if v)
                     or seen.get("make_model_seen"), "city": office.get("city"), "state": office.get("state"),
                     "office": office.get("name"), "place": location.get("city") or seen.get("location"),
                     "age_group": cs.age_group(age)})
    return _search_summary(rows, {
        "Owner age group": _summary_counts(r["age_group"] for r in rows),
        "Records by device": _summary_counts(e["device"] for e in records),
        "Records by table": _summary_counts(e["table"].replace("_", " ") for e in records),
        "Linked entries by type (references)": _summary_counts(related_types),
        "Insurance policy status (records)": _summary_counts(e["data"].get("status") for e in records if e["table"] == "policy"),
        "Insurer (policy records)": _summary_counts(e["data"].get("insurer_name") for e in records if e["table"] == "policy"),
        "Policy type (records)": _summary_counts(e["data"].get("policy_type") for e in records if e["table"] == "policy"),
        "Theft report status (records)": _summary_counts(e["data"].get("status") for e in records if e["table"] == "theft_report"),
        "Documents by source": _summary_counts(d["device"] for d in documents),
    }, records=len(records), documents=len(documents))


def run(text, post=http_post, devices=None, within=None, today=None):
    """The whole pipeline for one user query; within: the id of a previous result the query narrows down."""
    started = time.time()
    parsed = cs.parse_query(text, today)
    previous = RESULTS.get(within) if within else None
    if previous is not None and (not parsed["plates"] or cs.list_mode(parsed)):
        merged = refine(previous["parsed"], parsed)
        return run_list(text, merged, post, devices, set(previous["plates"]), previous, today)
    if cs.list_mode(parsed):
        out = run_list(text, parsed, post, devices, today=today)
        if within and previous is None:
            out["notes"].insert(0, "The earlier result has expired; this query was run on all vehicles.")
        return out
    entities, rounds = {}, []
    sub1 = {**parsed, "keys": {}, "hop": 1}
    answers = federate("/context", sub1, post, devices)
    rounds.append(_round(1, sub1, answers))
    _merge(entities, answers, 1.0, 1)
    keys = _next_keys(entities, parsed)
    if any(keys.values()):
        sub2 = {"text": text, "plates": [], "colors": [], "types": [], "makes": [], "terms": [], "keys": keys, "hop": 2}
        answers = federate("/context", sub2, post, devices)
        rounds.append(_round(2, sub2, answers))
        _merge(entities, answers, HOP_DECAY, 2)
    vehicles = _vehicles(entities)
    context = _context(parsed, vehicles)

    if vehicles:
        documents, answers = search_documents(context, post, devices)
        rounds.append({"hop": 3, "subquery": context, "devices": [
            {"device": dv, "address": a["address"], "ok": a["error"] is None, "error": a["error"], "ms": a["ms"],
             "found": len((a["result"] or {}).get("results", []))} for dv, a in answers.items()]})
    else:
        documents = []
    background, query, answers = search_background(parsed, context["terms"], documents, post, devices)
    if query:
        rounds.append({"hop": 3, "subquery": query, "devices": [
            {"device": dv, "address": a["address"], "ok": a["error"] is None, "error": a["error"], "ms": a["ms"],
             "found": len((a["result"] or {}).get("results", []))} for dv, a in answers.items()]})
    by_plate = {v["plate"]: v for v in vehicles}
    for d in documents:
        d["vehicles"] = sorted({link["plate"] for link in d["plates"] if link["plate"] in by_plate})
        for plate in d["vehicles"]:
            by_plate[plate]["documents"].append({"title": d["title"], "device": d["device"], "url": d["url"],
                                                 "how": next(link["how"] for link in d["plates"] if link["plate"] == plate)})
    return {"mode": "context", "query": text, "parsed": parsed, "action": parsed["action"], "rounds": rounds,
            "vehicles": vehicles, "context": context, "documents": documents, "background_documents": background,
            "summary": _context_summary(vehicles, documents + background, cs.today_of({"today": today})),
            "entities": sorted(entities.values(), key=lambda e: -e["score"]),
            "result_id": remember(text, parsed, [v["plate"] for v in vehicles]),
            "elapsed_ms": round((time.time() - started) * 1000, 1)}


# ============================================================
# List queries: vehicles counted over a period, filtered by owner age, origin and status; refining a result
# ============================================================

def search_documents(context, post, devices):
    request = {**context, "limit": 5}
    answers = federate("/docs/search", request, post, devices)
    documents = []
    for device, answer in answers.items():
        for d in (answer["result"] or {}).get("results", []):
            documents.append(dict(d, device=device, url=f"{answer['address'].rstrip('/')}/docs/{d['name']}"))
    for d in NEWS.search(request, 5):
        documents.append(dict(d, device="Client (news)", url=f"/docs/{d['name']}"))
    top = max((d["score"] for d in documents), default=1.0) or 1.0
    for d in documents:
        d["relevance"] = round(d["score"] / top, 3)
    documents = [d for d in documents if d["relevance"] >= MIN_RELEVANCE or d["plates"] or d["ids"]]
    documents.sort(key=lambda d: (-d["score"], d["device"], d["name"]))
    return documents[:MAX_DOCUMENTS], answers


EVENT_BACKGROUND = {
    "violation": ("speeding", "helmet", "helmets", "challan", "challans", "enforcement"),
    "capture": ("camera", "cameras", "anpr", "recorded", "spotted"),
    "challan": ("penalty", "penalties", "fine", "fines"),
}


def search_background(parsed, result_terms, documents, post, devices):
    """Rank general documents using the event and filters the user asked for."""
    agg = parsed.get("aggregate") or {}
    event = agg.get("event")
    topics = {word for word, kind in cs.EVENT_WORDS.items()
              if kind == event and word not in {"time", "times"}}
    topics.update(EVENT_BACKGROUND.get(event, ()))
    filters = [value for field in ("terms", "origin", "places", "statuses", "makes", "colors")
               for value in parsed.get(field) or []]
    focus = {word for value in filters + [agg.get("detail") or ""]
             for word in cs.words(value) if len(word) >= 3 and word not in cs.STOPWORDS}
    required = topics | focus
    if not required or parsed.get("plates"):
        return [], None, {}
    query = {
        "background_terms": sorted(required),
        "terms": {**result_terms, **dict.fromkeys(topics, 3.0), **dict.fromkeys(focus, 4.0)},
    }
    matches, answers = search_documents(query, post, devices)
    shown = {(doc["device"], doc["name"]) for doc in documents}
    background = [dict(doc, background=True) for doc in matches if (doc["device"], doc["name"]) not in shown]
    return background[:3], query, answers


MAX_LIST_ROWS = 300
MAX_RESULTS = 40          # earlier results kept for "refine these results"
RESULTS = OrderedDict()
REFINE_FIELDS = ("plates", "aggregate", "period", "age", "origin", "places", "statuses", "colors", "types", "makes", "limit")
COMPARE = {">": lambda a, n: a > n, ">=": lambda a, n: a >= n, "<": lambda a, n: a < n, "<=": lambda a, n: a <= n,
           "=": lambda a, n: a == n}
AGE_GROUPS = ["18-25", "26-35", "36-45", "46-60", "over 60", "unknown"]


def remember(text, parsed, plates):
    """Keep a result so the next query can narrow it down; returns its id."""
    rid = uuid.uuid4().hex[:10]
    RESULTS[rid] = {"text": text, "parsed": parsed, "plates": list(plates)}
    while len(RESULTS) > MAX_RESULTS:
        RESULTS.popitem(last=False)
    return rid


def refine(previous, parsed):
    """The previous request with what the new query adds or changes. Words that are not a recognised filter are
    read as the place the vehicles come from ("Pune" alone narrows to vehicles from Pune)."""
    merged = dict(previous)
    for field in REFINE_FIELDS:
        if parsed.get(field):
            merged[field] = parsed[field]
    if not any(parsed.get(f) for f in REFINE_FIELDS):
        known = set(cs.STATUS_WORDS) | set(cs.EVENT_WORDS) | {"owners", "owner", "vehicles", "only", "those", "them"}
        words = [w for w in parsed.get("terms", []) if w not in known]
        if words:
            merged["origin"] = [" ".join(words)]
    if parsed.get("aggregate") and not parsed.get("period"):
        merged["period"] = previous.get("period")
    merged["text"] = parsed["text"]
    merged["action"] = cs.describe(merged)
    return merged


def _status_ok(statuses, rto, ins, theft):
    for s in statuses:
        if (s == "uninsured" and ins.get("insured")) or (s == "insured" and not ins.get("insured")) \
                or (s == "stolen" and theft.get("theft") != "Reported") or (s == "recovered" and theft.get("theft") != "Recovered") \
                or (s == "scrapped" and not theft.get("scrapped")) or (s == "unregistered" and rto) \
                or (s == "registered" and not rto):
            return False
    return True


def _sent(path, payload):
    plates = payload.get("plates")
    shown = {k: v for k, v in payload.items() if k != "plates" and v not in (None, [], {}, "")}
    return {"path": path, "fields": shown, "plates": None if plates is None else len(plates)}


def _list_round(name, path, payload, answers, field):
    return {"name": name, "sent": _sent(path, payload), "devices": [
        {"device": d, "address": a["address"], "ok": a["error"] is None, "error": a["error"], "ms": a["ms"],
         "supported": bool((a["result"] or {}).get("supported")), "found": len((a["result"] or {}).get(field) or {}),
         "counted": (a["result"] or {}).get("counted")}
        for d, a in answers.items()]}


def run_list(text, parsed, post=None, devices=None, scope=None, previous=None, today=None):
    """Vehicles that pass a count over a period, filtered by owner age, origin and status, with where they come from
    and how old their owners are. scope: the plates of a previous result being refined."""
    post, devices = post or http_post, devices or DEVICES
    started, rounds, notes = time.time(), [], []
    today = (today or date.today()).isoformat() if not isinstance(today, str) else today
    agg, period = parsed.get("aggregate"), parsed.get("period") or {}
    if parsed.get("plates"):
        requested = set(parsed["plates"])
        scope = requested if scope is None else scope & requested
    counts = {}
    passes = (lambda n: COMPARE[agg["op"]](n, agg["n"])) if agg else (lambda n: True)
    zero_ok = bool(agg) and passes(0)
    if agg:
        # the counting device applies the threshold itself and sends back only the vehicles that pass; when zero
        # events also pass ("fewer than 2"), the client needs the failing vehicles to leave them out, so then the
        # device sends every vehicle it counted
        owner = cs.EVENTS[agg["event"]][0]
        camera_count = agg["event"] in ("violation", "capture")
        later_filters = (parsed.get("age") or parsed.get("origin") or parsed.get("statuses") or
                         (not camera_count and any(parsed.get(k) for k in ("makes", "types", "colors"))))
        req = {"event": agg["event"], "detail": agg.get("detail"), "start": period.get("start"), "end": period.get("end"),
               "places": parsed.get("places") or [], "colors": parsed.get("colors") or [],
               "types": parsed.get("types") or [], "makes": parsed.get("makes") or [],
               "op": None if zero_ok else agg["op"], "n": None if zero_ok else agg["n"],
               # "top N" can be cut at the device only when no later filter (age, origin, status) can drop a row
               "top": None if zero_ok or later_filters else parsed.get("limit"),
               "plates": sorted(scope) if scope is not None else None, "today": today}
        answers = federate("/context/aggregate", req, post, {owner: devices[owner]}) if owner in devices else {}
        rounds.append(_list_round(f"count {cs.EVENTS[agg['event']][1]}", "/context/aggregate", req, answers, "counts"))
        if not ((answers.get(owner) or {}).get("result") or {}).get("supported"):
            notes.append(f"{owner} is unavailable. The requested count could not be verified.")
            background, query, doc_answers = search_background(parsed, {}, [], post, devices)
            if query:
                rounds.append(_list_round("background documents", "/docs/search", query, doc_answers, "results"))
            return {"mode": "list", "query": text, "parsed": parsed, "action": parsed["action"], "rounds": rounds,
                    "rows": [], "total": None, "summary": None, "breakdown": {}, "notes": notes, "documents": [],
                    "background_documents": background,
                    "result_id": None, "elapsed_ms": round((time.time() - started) * 1000, 1)}
        counts = answers[owner]["result"]["counts"]
        if not camera_count and parsed.get("places"):
            notes.append("The event location filter is available for camera events only.")
    if agg and not zero_ok:
        candidates = sorted(p for p, c in counts.items() if passes(c["count"]))
    else:
        candidates = sorted(scope) if scope is not None else None
    # the registration side: owner, age and origin (RTO filters by age, origin and make itself)
    rto_filters = bool(parsed.get("age") or parsed.get("origin") or (parsed.get("makes") and not
                                                                       (agg and agg["event"] in ("violation", "capture"))))
    rto_req = {"plates": candidates, "age": parsed.get("age"), "origin": parsed.get("origin") or [],
               "makes": parsed.get("makes") if rto_filters else [], "today": today}
    rto_devices = {d: devices[d] for d in ("RTO",) if d in devices}
    answers = federate("/context/profile", rto_req, post, rto_devices) if rto_devices else {}
    rounds.append(_list_round("owner, age and origin", "/context/profile", rto_req, answers, "profiles"))
    rto_answer = answers.get("RTO") or {"error": "RTO not configured", "result": None}
    rto = (rto_answer["result"] or {}).get("profiles") or {}
    if rto_answer["error"]:
        notes.append("RTO is down: owner, age and origin are unknown" + (", so the age / origin filter could not be applied."
                                                                         if rto_filters else "."))
    if candidates is None:
        survivors = sorted(rto)
    elif rto_filters and not rto_answer["error"]:
        survivors = [p for p in candidates if p in rto]
    else:
        survivors = list(candidates)
    if zero_ok:
        survivors = [p for p in survivors if passes(counts.get(p, {}).get("count", 0))]
    # insurance and theft status of what is left
    status_devices = {d: devices[d] for d in ("Insurance", "Theft/Shredding") if d in devices}
    status_req = {"plates": survivors, "today": today}
    answers = federate("/context/profile", status_req, post, status_devices) if survivors and status_devices else {}
    if answers:
        rounds.append(_list_round("insurance and theft status", "/context/profile", status_req, answers, "profiles"))
    ins = ((answers.get("Insurance") or {}).get("result") or {}).get("profiles") or {}
    theft = ((answers.get("Theft/Shredding") or {}).get("result") or {}).get("profiles") or {}
    for d, a in answers.items():
        if a["error"]:
            notes.append(f"{d} is down: its status is unknown" + (" and the status filter was not applied." if parsed.get("statuses") else "."))
    statuses = [s for s in parsed.get("statuses") or []
                if not ((s in ("insured", "uninsured") and (answers.get("Insurance") or {}).get("error"))
                        or (s in ("stolen", "recovered", "scrapped") and (answers.get("Theft/Shredding") or {}).get("error")))]
    rows = []
    for p in survivors:
        r, i, t = rto.get(p) or {}, ins.get(p) or {}, theft.get(p) or {}
        if not _status_ok(statuses, r, i, t):
            continue
        c = counts.get(p) or {"count": 0, "where": [], "what": [], "last": ""}
        rows.append({"plate": p, "count": c["count"], "where": c["where"], "what": c["what"], "last": c["last"],
                     "registered": bool(r), "owner": r.get("owner"), "age": r.get("age"),
                     "age_group": r.get("age_group") or "unknown", "dob": r.get("dob"), "licence_no": r.get("licence_no"),
                     "office": r.get("office"), "office_code": r.get("office_code") or p[:2], "city": r.get("city"),
                     "state": r.get("state") or STATE_NAMES.get(p[:2], p[:2]), "address": r.get("address"),
                     "vehicle": " ".join(x for x in (r.get("make"), r.get("model")) if x), "type": r.get("type"),
                     "insured": i.get("insured") if i else None, "policy": i.get("policy"),
                     "insurer": i.get("insurer"),
                     "theft": t.get("theft"), "scrapped": t.get("scrapped")})
    rows.sort(key=lambda x: (-x["count"], x["plate"]))
    missing = [r["plate"] for r in rows if not counts.get(r["plate"], {}).get("observation")]
    observations = {}
    if missing and "ANPR" in devices:
        req = {"plates": missing}
        answers = federate("/context/profile", req, post, {"ANPR": devices["ANPR"]})
        rounds.append(_list_round("camera observations", "/context/profile", req, answers, "profiles"))
        observations = (answers["ANPR"]["result"] or {}).get("profiles") or {}
    for row in rows:
        plate = row["plate"]
        observation = counts.get(plate, {}).get("observation") or observations.get(plate) or {}
        row.update({field: observation[field] for field in ("color", "type", "place") if observation.get(field)})
    colors = set(parsed.get("colors") or [])
    rows = [r for r in rows if cs.matches_vehicle_type(r.get("type"), parsed.get("types"))
            and (not colors or str(r.get("color") or "").strip().casefold().replace("gray", "grey") in colors)]
    if zero_ok and parsed.get("makes"):
        rows = [r for r in rows if r["count"] or cs.hits([observations.get(r["plate"], {}).get("make_model"),
                                                           r["vehicle"]], parsed["makes"])]
    if parsed.get("limit"):
        rows = rows[:parsed["limit"]]
    insured = _summary_counts("Insured" if r["insured"] else "Not insured" if r["insured"] is False else "Unknown"
                              for r in rows)
    documents, result_terms = [], {}
    if rows:
        terms = Counter()
        for row in rows:
            labels = row["where"] + [value.rsplit(" x", 1)[0] for value in row["what"]]
            labels.extend([row["vehicle"], row["city"]])
            terms.update({w for label in labels if label for w in cs.words(label) if w not in cs.STOPWORDS})
        result_terms = {w: 1 + n / len(rows) for w, n in terms.most_common(MAX_TERMS)}
        context = {"plates": [row["plate"] for row in rows], "exact_result_plates": True, "terms": result_terms}
        documents, answers = search_documents(context, post, devices)
        rounds.append(_list_round("related documents", "/docs/search", context, answers, "results"))
    background, background_context, answers = search_background(parsed, result_terms, documents, post, devices)
    if background_context:
        rounds.append(_list_round("background documents", "/docs/search", background_context, answers, "results"))
    summary = _search_summary(rows, {
        "Owner age group": _summary_counts(r["age_group"] for r in rows),
        "Documents by source": _summary_counts(d["device"] for d in documents + background),
        "Registration status": _summary_counts("Unknown" if rto_answer["error"] else
                                                "Registered" if r["registered"] else "No registration found" for r in rows),
        "Insurance today": insured,
        "Insurer": _summary_counts(r["insurer"] for r in rows),
        "Theft status": _summary_counts(r["theft"] or "No report returned" for r in rows),
        "Scrap status": _summary_counts("Scrapped" if r["scrapped"] else "No scrap record returned" for r in rows),
        "Most frequent event location": _summary_counts(r["where"][0] for r in rows if r["where"]),
        "Event details (vehicles)": _summary_counts(
            label for r in rows for label in dict.fromkeys(
                value.rsplit(" x", 1)[0] if value.rsplit(" x", 1)[-1].isdigit() else value for value in r["what"])),
    }, events=sum(r["count"] for r in rows) if agg else None, event_label=cs.EVENTS[agg["event"]][1] if agg else None)
    breakdown = {
        "age": [(g, n) for g in AGE_GROUPS for n in [sum(1 for x in rows if x["age_group"] == g)] if n],
        "state": Counter(x["state"] for x in rows).most_common(),
        "city": Counter(x["city"] or "not registered" for x in rows).most_common(10),
        "seen": Counter(w for x in rows for w in x["where"][:1]).most_common(10),
        "insured": Counter("insured" if x["insured"] else "not insured" if x["insured"] is False else "no policy found"
                           for x in rows).most_common(),
    }
    rid = remember(text, parsed, [x["plate"] for x in rows])
    return {"mode": "list", "query": text, "parsed": parsed, "action": parsed["action"], "rounds": rounds,
            "rows": rows[:MAX_LIST_ROWS], "total": len(rows), "breakdown": breakdown, "notes": notes, "summary": summary,
            "documents": documents, "background_documents": background,
            "result_id": rid, "within": previous and {"query": previous["text"], "count": len(previous["plates"])},
            "elapsed_ms": round((time.time() - started) * 1000, 1)}


STATE_NAMES = {"AP": "Andhra Pradesh", "AS": "Assam", "BR": "Bihar", "CG": "Chhattisgarh", "DL": "Delhi", "GA": "Goa",
               "GJ": "Gujarat", "HR": "Haryana", "HP": "Himachal Pradesh", "JH": "Jharkhand", "KA": "Karnataka",
               "KL": "Kerala", "MP": "Madhya Pradesh", "MH": "Maharashtra", "OD": "Odisha", "PB": "Punjab",
               "RJ": "Rajasthan", "TN": "Tamil Nadu", "TS": "Telangana", "UK": "Uttarakhand", "UP": "Uttar Pradesh",
               "WB": "West Bengal"}
