"""Phase 3: federated context discovery and search of unstructured documents.

Shared by every device and the client (copy it next to each service, like plate_rules.py).

  parse_query    a free-text user query -> plates, colours, vehicle types, makes, keywords
  Context        what ONE database found for a (sub)query: entities reached through its FK-PK joins, the keys and
                 terms they carry (plates, names, places, FIR / policy numbers ...), scored by how closely they match
  DocIndex       a device's own unstructured documents (plain-text files); searched with the accumulated context:
                 BM25 over the weighted context terms, plus plates (also misread ones) and identifiers in the text
  register       adds POST /context, POST /docs/search, GET /docs/<name> and GET /schema to a device
"""
import json
import math
import os
import re
import sqlite3
import time
from collections import Counter
from datetime import date, timedelta
from functools import lru_cache

import plate_rules as rules

COLORS = {"white", "black", "red", "blue", "grey", "gray", "silver", "green", "yellow", "brown", "orange", "maroon"}
TYPES = {"car", "bike", "motorcycle", "scooter", "truck", "lorry", "bus", "van", "suv", "auto", "jeep"}
MAKES = {"maruti", "suzuki", "swift", "hyundai", "creta", "i20", "honda", "city", "amaze", "tata", "nexon", "lpt",
         "mahindra", "xuv300", "toyota", "innova", "crysta", "kia", "seltos", "skoda", "rapid", "royal", "enfield",
         "classic", "tvs", "apache", "bajaj", "hero", "yamaha", "volkswagen", "renault", "nissan", "ford", "mg"}
# words that point at one device's data (they steer the scoring; every device is still asked)
INTENTS = {"stolen": "Theft/Shredding", "theft": "Theft/Shredding", "fir": "Theft/Shredding", "police": "Theft/Shredding",
           "recovered": "Theft/Shredding", "scrapped": "Theft/Shredding", "shredded": "Theft/Shredding",
           "scrap": "Theft/Shredding", "insurance": "Insurance", "insured": "Insurance", "uninsured": "Insurance",
           "policy": "Insurance", "claim": "Insurance", "premium": "Insurance", "registration": "RTO",
           "registered": "RTO", "owner": "RTO", "rto": "RTO", "chassis": "RTO", "puc": "RTO", "fitness": "RTO",
           "camera": "ANPR", "seen": "ANPR", "captured": "ANPR", "speeding": "ANPR", "violation": "ANPR",
           "mot": "MoT", "challan": "MoT", "officer": "MoT", "report": "MoT"}
STOPWORDS = {"a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "with", "by", "from", "is", "was",
             "are", "were", "be", "been", "has", "have", "had", "this", "that", "these", "those", "it", "its", "as",
             "show", "find", "me", "all", "any", "which", "what", "who", "where", "when", "near", "about", "their",
             "his", "her", "there", "into", "after", "before", "not", "no", "vehicle", "vehicles", "details", "info",
             "information", "list", "get", "give", "please", "also", "very", "than", "then", "but", "if", "did",
             "does", "do", "can", "could", "would", "should", "will", "said", "per", "via", "our", "your", "we", "you", "new"}

TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6", "A": "4"}
TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G", "4": "A"}
WORD = re.compile(r"[A-Za-z0-9]+")


def words(text):
    return list(_words(str(text or "")))


@lru_cache(maxsize=200000)
def _words(text):
    """Lower-case words of a text; cached, since the same values (cities, colours, makes) repeat on every row."""
    return tuple(w.lower() for w in WORD.findall(text))


@lru_cache(maxsize=200000)
def _phrase(text):
    return " " + " ".join(_words(text)) + " "


def key(value):
    return rules.normalize_plate(value)


# ============================================================
# Plates written in free text
# ============================================================

def canonical_plate(text):
    """The valid plate a written (maybe misread) plate stands for, by slot: MHI4ZZOOOO -> MH14ZZ0000; else None."""
    s = key(text)
    district = len(s) - 8
    if district not in (1, 2):
        return None
    parts = (s[:2], s[2:2 + district], s[2 + district:4 + district], s[4 + district:])
    fixed = ("".join(TO_LETTER.get(c, c) for c in parts[0]) + "".join(TO_DIGIT.get(c, c) for c in parts[1])
             + "".join(TO_LETTER.get(c, c) for c in parts[2]) + "".join(TO_DIGIT.get(c, c) for c in parts[3]))
    return fixed if rules.plate_format_valid(fixed) else None


def plate_strings(text, max_tokens=4):
    """Every run of 1-4 neighbouring words that could be a written plate (8-11 letters/digits, starting with letters)."""
    tokens = WORD.findall(str(text or ""))
    found = set()
    for i in range(len(tokens)):
        joined = ""
        for j in range(i, min(i + max_tokens, len(tokens))):
            joined += tokens[j].upper()
            if 8 <= len(joined) <= 11 and joined[:2].isalpha() and sum(c.isdigit() for c in joined) + \
                    sum(c in TO_DIGIT for c in joined[2:]) >= 4:
                found.add(joined)
            if len(joined) > 11:
                break
    return found


def plate_mentions(text):
    """Plates in a query: the slot-repaired plate when it can be repaired, else the string as written."""
    out = []
    for s in sorted(plate_strings(text), key=len, reverse=True):
        plate = canonical_plate(s) or (s if sum(c.isdigit() for c in s) >= 3 else None)
        if plate and plate not in out and not any(plate in o for o in out):
            out.append(plate)
    return out


# ============================================================
# The user query
# ============================================================

def parse_query(text, today=None):
    """Free text -> {plates, colors, types, makes, intents, terms} and what the user wants done with it:
    aggregate (count events per vehicle against a threshold), period, age (owner age range), origin (where the
    vehicle is registered / its owner lives), places (where the events happened), statuses, limit, action."""
    text = str(text or "")
    request = parse_request(text, today)
    plate_text = request["rest"] if list_mode(request) else text
    plates = plate_mentions(plate_text)
    plate_words = set()
    for s in plate_strings(plate_text):
        if canonical_plate(s) in plates or s in plates:
            plate_words |= set(words(s))
    tokens = [w for w in words(text)]
    plate_tokens = {w for w in tokens if any(w.upper() in p or key(w) == p for p in plates)} | plate_words
    colors = sorted({"grey" if w == "gray" else w for w in tokens if w in COLORS})
    types = sorted({TYPE_ALIASES.get(w, w) for w in tokens if w in TYPES or w in TYPE_ALIASES})
    makes = [w for w in tokens if w in MAKES]
    intents = sorted({INTENTS[w] for w in tokens if w in INTENTS})
    terms = []
    for w in words(request.pop("rest")):            # the words left once counts, periods and ages are taken out
        w = TYPE_ALIASES.get(w, w)
        if w in STOPWORDS or w in plate_tokens or len(w) < 3 or w in COLORS or w in TYPES or w in MAKES:
            continue
        if w not in terms:
            terms.append(w)
    parsed = {"text": text, "plates": plates, "colors": colors, "types": types, "makes": list(dict.fromkeys(makes)),
              "intents": intents, "terms": terms, **request}
    parsed["action"] = describe(parsed)
    return parsed


# ============================================================
# What the user wants done: counts over a period, owner age, where the vehicle comes from
# ============================================================

TYPE_ALIASES = {"cars": "car", "bikes": "bike", "motorcycles": "bike", "motorcycle": "bike", "motorbike": "bike",
                "scooters": "scooter", "trucks": "truck", "lorry": "truck", "lorries": "truck", "buses": "bus",
                "vans": "van", "suvs": "suv", "jeeps": "jeep"}
NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8",
                "nine": "9", "ten": "10", "eleven": "11", "twelve": "12", "fifteen": "15", "twenty": "20",
                "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "hundred": "100", "once": "1 time",
                "twice": "2 times", "thrice": "3 times"}
# the event a number counts, and the device that records it
EVENT_WORDS = {"violation": "violation", "violations": "violation", "offence": "violation", "offences": "violation",
               "offense": "violation", "offenses": "violation", "fine": "violation", "fines": "violation",
               "time": "capture", "times": "capture", "sighting": "capture", "sightings": "capture",
               "capture": "capture", "captures": "capture", "challan": "challan", "challans": "challan",
               "claim": "claim", "claims": "claim", "theft": "theft", "thefts": "theft", "fir": "theft", "firs": "theft",
               "transfer": "transfer", "transfers": "transfer", "report": "report", "reports": "report"}
EVENTS = {"violation": ("ANPR", "traffic violations recorded by cameras"), "capture": ("ANPR", "camera sightings"),
          "challan": ("MoT", "challans issued"), "report": ("MoT", "reports sent to MoT"),
          "claim": ("Insurance", "insurance claims"), "theft": ("Theft/Shredding", "theft reports (FIRs)"),
          "transfer": ("RTO", "ownership transfers")}
# kinds of violation (the text searched in capture_violation.violation_type)
DETAILS = {"speeding": "speeding", "overspeeding": "speeding", "over speeding": "speeding", "helmet": "helmet",
           "red light": "red light", "signal jump": "red light", "seat belt": "seat belt", "seatbelt": "seat belt",
           "mobile": "mobile", "phone": "mobile", "wrong lane": "wrong lane"}
STATUS_WORDS = {"uninsured": "uninsured", "insured": "insured", "stolen": "stolen", "recovered": "recovered",
                "scrapped": "scrapped", "shredded": "scrapped", "unregistered": "unregistered",
                "registered": "registered"}
MONTHS = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                       "september", "october", "november", "december"], 1)}
MONTHS.update({m[:3]: i for m, i in list(MONTHS.items())})
MONTHS["sept"] = 9
_MONTH = "|".join(sorted(MONTHS, key=len, reverse=True))
_DATE = (rf"(?:\d{{4}}-\d{{1,2}}-\d{{1,2}}|\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{4}}"
         rf"|\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH})\b(?:,?\s+\d{{4}})?|(?:{_MONTH})\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?"
         rf"|(?:{_MONTH})\b(?:\s+\d{{4}})?|(?:19|20)\d{{2}})")
_OP = (r"(?P<op>no more than|no less than|more than|greater than|exceeding|over|above|at least|minimum of|"
       r"less than|fewer than|under|below|at most|up to|maximum of|exactly|>=|<=|>|<|=)")
_DETAIL_WORDS = (r"speeding|overspeeding|over|red|light|signal|jump|helmet|seat|belt|seatbelt|mobile|phone|wrong|lane|"
                 r"traffic|separate|different|recorded|camera|insurance|theft|ownership|mot|no|riding|without|using")
AGGREGATE = re.compile(rf"(?:{_OP}\s*)?(?P<n>\d+)\s*(?P<plus>\+|or more|or above|or less|or fewer)?\s+"
                       rf"(?P<mid>(?:(?:{_DETAIL_WORDS})\s+){{0,2}})(?P<event>{'|'.join(sorted(EVENT_WORDS, key=len, reverse=True))})\b"
                       r"(?:\s+(?P<after>or more|or above|or less|or fewer))?")
OPS = {"more than": ">", "greater than": ">", "exceeding": ">", "over": ">", "above": ">", ">": ">",
       "at least": ">=", "minimum of": ">=", "no less than": ">=", ">=": ">=", "+": ">=", "or more": ">=", "or above": ">=",
       "less than": "<", "fewer than": "<", "under": "<", "below": "<", "<": "<",
       "at most": "<=", "no more than": "<=", "up to": "<=", "maximum of": "<=", "<=": "<=", "or less": "<=",
       "or fewer": "<=", "exactly": "=", "=": "="}
OP_WORDS = {">": "more than", ">=": "at least", "<": "fewer than", "<=": "at most", "=": "exactly"}
COUNT_TEST = {">": lambda a, n: a > n, ">=": lambda a, n: a >= n, "<": lambda a, n: a < n, "<=": lambda a, n: a <= n,
              "=": lambda a, n: a == n}
# where a phrase that names a place ends
_PLACE_END = (r"(?=\s+(?:with|who|whose|that|which|having|has|have|and|or|aged?|owners?|drivers?|more|over|under|"
              r"between|since|during|last|past|this|in|at|near|seen|registered|cars?|vehicles?|bikes?|trucks?|"
              r"scooters?|for|where|than|from|uninsured|insured|stolen|scrapped|top)\b|\s*[,.;!?]|\s*$)")
ORIGIN = re.compile(r"\b(?:coming from|comes from|come from|from|registered (?:in|at|from)|based in|belonging to|"
                    r"belongs to|living in|lives in|residents? of)\s+(?:the\s+)?(?P<place>[a-z][a-z0-9 .-]*?)" + _PLACE_END)
PLACE = re.compile(r"\b(?:in|at|near|on)\s+(?:the\s+)?(?P<place>[a-z][a-z0-9 .-]*?)" + _PLACE_END)
NOT_PLACES = {"the", "a", "an", "all", "total", "any", "road", "their", "his", "her", "my", "our", "this", "that",
              "last", "past", "time", "times", "insurance", "force"}


def add_months(d, months):
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    days = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return date(y, m, min(d.day, days))


def date_range(text, today):
    """(first day, last day) a written date stands for: a day, a month (September 2026), or a year (2025)."""
    s = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", str(text).strip().lower().replace(",", " "))
    s = re.sub(r"\s+", " ", s)
    try:
        m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
        if m:
            d = date(int(m[1]), int(m[2]), int(m[3]))
            return d, d
        m = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", s)
        if m:
            d = date(int(m[3]), int(m[2]), int(m[1]))
            return d, d
        m = re.fullmatch(rf"(\d{{1,2}}) ({_MONTH})(?: (\d{{4}}))?", s) or re.fullmatch(rf"({_MONTH}) (\d{{1,2}})(?: (\d{{4}}))?", s)
        if m:
            day_, month = (m[1], m[2]) if m[1].isdigit() else (m[2], m[1])
            d = date(int(m[3]) if m[3] else today.year, MONTHS[month], int(day_))
            if not m[3] and d > today:
                d = d.replace(year=d.year - 1)
            return d, d
        m = re.fullmatch(rf"({_MONTH})(?: (\d{{4}}))?", s)
        if m:
            first = date(int(m[2]) if m[2] else today.year, MONTHS[m[1]], 1)
            if not m[2] and first > today:
                first = first.replace(year=first.year - 1)
            return first, add_months(first, 1) - timedelta(days=1)
        m = re.fullmatch(r"((?:19|20)\d{2})", s)
        if m:
            return date(int(m[1]), 1, 1), date(int(m[1]), 12, 31)
    except ValueError:
        return None
    return None


def _blank(text, span):
    return text[:span[0]] + " " * (span[1] - span[0]) + text[span[1]:]


def _period(low, today):
    """The time period a query names, and the text with it taken out."""
    patterns = [
        (rf"\bbetween\s+({_DATE})\s+(?:and|to|-)\s+({_DATE})", "two"),
        (rf"\bfrom\s+({_DATE})\s+(?:to|till|until|-)\s+({_DATE})", "two"),
        (r"\b(?:in|during|over|within|for)?\s*(?:the\s+)?(?:last|past|previous)\s+(?:(\d+)\s+)?(day|week|month|year)s?\b", "last"),
        (r"\b(?:this|current)\s+(week|month|year)\b", "this"),
        (r"\b(today|yesterday)\b", "day"),
        (rf"\b(since|after|from)\s+({_DATE})", "since"),
        (rf"\b(before|until|till|up to)\s+({_DATE})", "until"),
        (rf"\b(?:in|during|of)\s+({_DATE})", "in"),
    ]
    for pattern, kind in patterns:
        m = re.search(pattern, low)
        if not m:
            continue
        if kind == "two":
            a, b = date_range(m[1], today), date_range(m[2], today)
            if not a or not b:
                continue
            start, end = a[0], b[1]
        elif kind == "last":
            n, unit = int(m[1] or 1), m[2]
            start = (today - timedelta(days=n) if unit == "day" else today - timedelta(weeks=n) if unit == "week"
                     else add_months(today, -n) if unit == "month" else add_months(today, -12 * n))
            end = today
        elif kind == "this":
            start = (today - timedelta(days=today.weekday()) if m[1] == "week" else today.replace(day=1)
                     if m[1] == "month" else today.replace(month=1, day=1))
            end = today
        elif kind == "day":
            start = end = today if m[1] == "today" else today - timedelta(days=1)
        elif kind == "since":
            r = date_range(m[2], today)
            if not r:
                continue
            start, end = (r[0] if m[1] != "after" else r[1] + timedelta(days=1)), today
        elif kind == "until":
            r = date_range(m[2], today)
            if not r:
                continue
            start, end = None, (r[0] - timedelta(days=1) if m[1] == "before" else r[1])
        else:
            r = date_range(m[1], today)
            if not r:
                continue
            start, end = r
        found = {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
                 "label": m[0].strip()}
        return found, _blank(low, m.span())
    return None, low


AGE_PATTERNS = [
    (r"\b(?:aged?|age group|ages?)\s*(?:of\s+|between\s+|from\s+)?(\d{1,2})\s*(?:-|to|and)\s*(\d{1,2})"
     r"(?:\s*(?:years?|yrs?)(?:\s+old)?)?", "range"),
    (r"\b(?:between\s+)?(\d{1,2})\s*(?:-|to|and)\s*(\d{1,2})\s*(?:years?|yrs?)(?:\s+old|\s+of\s+age)?", "range"),
    (r"\b(?:owners?|drivers?|people|persons?|users?|holders?|aged?|who are|whose owners? (?:is|are))\s+"
     r"(?:aged\s+|of age\s+)?(under|below|younger than|less than|over|above|older than|more than|at least|at most)\s+"
     r"(\d{1,2})\b(?:\s*(?:years?|yrs?)(?:\s+old)?)?", "bound"),
    (r"\b(under|below|younger than|over|above|older than|at least)\s+(\d{1,2})\s*(?:years?|yrs?)(?:\s+old|\s+of\s+age)?",
     "bound"),
    (r"\bin\s+(?:their|his|her)\s+(\d)0s\b", "decade"),
    (r"\byoung(?:er)?\s+(?:owners?|drivers?|people|users?)\b|\bteen(?:age)?(?:rs?)?\b", "young"),
    (r"\bmiddle[- ]aged\b", "middle"),
    (r"\bsenior citizens?\b|\bseniors\b|\belderly\b|\bold(?:er)?\s+(?:owners?|drivers?|people)\b", "senior"),
]


def _age(low):
    for pattern, kind in AGE_PATTERNS:
        m = re.search(pattern, low)
        if not m:
            continue
        if kind == "range":
            lo, hi = sorted((int(m[1]), int(m[2])))
        elif kind == "bound":
            n = int(m[2])
            lo, hi = {"under": (0, n - 1), "below": (0, n - 1), "younger than": (0, n - 1), "less than": (0, n - 1),
                      "at most": (0, n), "at least": (n, 120)}.get(m[1], (n + 1, 120))
        elif kind == "decade":
            lo, hi = int(m[1]) * 10, int(m[1]) * 10 + 9
        else:
            lo, hi = {"young": (18, 25), "middle": (40, 60), "senior": (60, 120)}[kind]
        lo = max(lo, 18) if hi >= 18 else lo          # owners hold a driving licence
        label = f"{lo}-{hi}" if hi < 120 else f"{lo} and over"
        return {"min": lo, "max": hi, "label": label, "text": m[0].strip()}, _blank(low, m.span())
    return None, low


def _aggregate(low):
    m = re.search(r"\b(?:repeat(?:ed)?|habitual|multiple|frequent)\s+(offenders?|violators?|violations?|offences?)\b", low)
    if m:
        return {"event": "violation", "op": ">=", "n": 2, "text": m[0]}, _blank(low, m.span())
    m = re.search(r"\bmost\s+(violations|challans|claims|sightings|thefts|transfers)\b", low)
    if m:
        return {"event": EVENT_WORDS[m[1]], "op": ">=", "n": 1, "text": m[0], "most": True}, _blank(low, m.span())
    m = AGGREGATE.search(low)
    if not m:
        m = re.search(rf"\b(?:with|having|has|have|had|got|committed|caught for)\s+(?:any\s+|some\s+)?"
                      rf"(?:(?:{_DETAIL_WORDS})\s+){{0,2}}(?P<event>violations?|offen[cs]es?|fines?|challans?|claims?|"
                      rf"thefts?|firs?|transfers?)\b", low) or \
            re.search(rf"\b(?:{'|'.join(DETAILS)})\s+(?P<event>violations|offen[cs]es|fines)\b", low)
        if not m:
            return None, low
        return {"event": EVENT_WORDS[m["event"]], "op": ">=", "n": 1, "text": m[0].strip()}, _blank(low, m.span())
    event = EVENT_WORDS[m["event"]]
    if event == "capture" and re.search(r"\bviolat|\boffend|\bfined\b", low):
        event = "violation"                            # "violated more than 3 times"
    op = OPS.get(m["plus"] or m["after"] or m["op"] or "", ">=")
    return {"event": event, "op": op, "n": int(m["n"]), "text": m[0].strip()}, _blank(low, m.span())


def _places(pattern, low):
    found = []
    while True:
        m = next((x for x in pattern.finditer(low) if x["place"].split()[0] not in NOT_PLACES
                  and not re.fullmatch(r"[\d .-]+", x["place"])), None)
        if not m:
            return found, low
        place = m["place"].strip(" .-")
        if place and place not in found:
            found.append(place)
        low = _blank(low, m.span())


def parse_request(text, today=None):
    """What the user wants done, read from the query; each part found is taken out of the text ("rest")."""
    today = today or date.today()
    if isinstance(today, str):
        today = date.fromisoformat(today)
    low = " ".join(NUMBER_WORDS.get(w, w) for w in re.sub(r"[^a-z0-9<>=+/,.;!?-]+", " ", str(text).lower()).split())
    period, low = _period(low, today)
    age, low = _age(low)
    detail = next((v for k, v in sorted(DETAILS.items(), key=lambda kv: -len(kv[0])) if re.search(rf"\b{k}\b", low)), None)
    aggregate, low = _aggregate(low)
    limit = None
    m = re.search(r"\btop\s+(\d+)\b", low)
    if m:
        limit, low = int(m[1]), _blank(low, m.span())
    if aggregate and detail and aggregate["event"] in ("violation", "capture"):
        aggregate["event"], aggregate["detail"] = "violation", detail
    statuses = []
    if re.search(r"\b(?:not|un)\s*insured\b|\bwithout\s+(?:an?\s+)?insurance\b|\bno\s+insurance\b", low):
        statuses.append("uninsured")
    for w in words(low):
        s = STATUS_WORDS.get(w)
        if s and s not in statuses and not (s == "insured" and "uninsured" in statuses) \
                and not (s == "registered" and re.search(r"\bregistered\s+(?:in|at|from)\b", low)):
            statuses.append(s)
    rest = low                                         # place words stay search terms for the context search
    origin, low = _places(ORIGIN, low)
    places, low = _places(PLACE, low) if (aggregate or age) else ([], low)
    return {"aggregate": aggregate, "period": period, "age": age, "origin": origin, "places": places,
            "statuses": statuses, "limit": limit, "rest": rest}


def list_mode(parsed):
    """Queries that ask for a list of vehicles (a count against a threshold, or an owner age group)."""
    return bool(parsed.get("aggregate") or parsed.get("age"))


def describe(parsed):
    """The user's intent in words: what is asked for, over which period, with which filters."""
    parts = []
    agg, period, age = parsed.get("aggregate"), parsed.get("period"), parsed.get("age")
    if agg:
        device, what = EVENTS[agg["event"]]
        kind = f" ({agg['detail']})" if agg.get("detail") else ""
        if agg.get("most"):
            parts.append(f"rank vehicles by the number of {what}{kind} [{device}]")
        else:
            parts.append(f"list vehicles with {OP_WORDS[agg['op']]} {agg['n']} {what}{kind} [{device}]")
    elif age:
        parts.append("list vehicles")
    elif parsed.get("plates"):
        parts.append("find everything about " + ", ".join(parsed["plates"]))
    else:
        parts.append("find records and documents that match the description")
    if period:
        parts.append("between " + (period["start"] or "the first record") + " and " + (period["end"] or "today"))
    if parsed.get("places") and agg:
        parts.append("happening in/at " + ", ".join(parsed["places"]))
    if age:
        parts.append(f"owner aged {age['label']} [RTO driving licence]")
    if parsed.get("origin"):
        parts.append("vehicle from " + ", ".join(parsed["origin"]) + " [RTO office / owner address]")
    if parsed.get("statuses") and (agg or age):
        parts.append("only " + ", ".join(parsed["statuses"]))
    if parsed.get("colors") or parsed.get("types") or parsed.get("makes"):
        parts.append("vehicle: " + " ".join(parsed.get("colors", []) + parsed.get("makes", []) + parsed.get("types", [])))
    if parsed.get("limit"):
        parts.append(f"top {parsed['limit']}")
    return {"kind": "count" if agg else "filter" if age else "lookup" if parsed.get("plates") else "search",
            "text": "; ".join(parts)}


# ============================================================
# Counting events and describing vehicles on a device (for list queries)
# ============================================================

def period_bounds(q):
    return (q.get("start") or "0000-00-00"), (q.get("end") or "9999-12-31") + " 23:59:59"


def in_period(value, bounds):
    v = str(value or "").strip()[:19]
    return bool(v) and bounds[0] <= v <= bounds[1]


def wanted_plates(q):
    plates = q.get("plates")
    return None if plates is None else {key(p) for p in plates}


def today_of(q):
    try:
        return date.fromisoformat(str(q.get("today"))[:10])
    except ValueError:
        return date.today()


def age_on(dob, today):
    try:
        born = date.fromisoformat(str(dob)[:10])
    except ValueError:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def matches_vehicle_type(actual, wanted):
    kind = str(actual or "").strip().casefold()
    kind = TYPE_ALIASES.get(kind, kind)
    families = {"sedan": "car", "hatchback": "car", "suv": "car", "compact suv": "car",
                "van": "car", "muv": "car", "jeep": "car"}
    return not wanted or kind in wanted or families.get(kind) in wanted or (kind == "compact suv" and "suv" in wanted)


def age_group(age):
    if age is None:
        return "unknown"
    for hi, label in ((17, "under 18"), (25, "18-25"), (35, "26-35"), (45, "36-45"), (60, "46-60")):
        if age <= hi:
            return label
    return "over 60"


def origin_ok(origins, plate, office_code, texts):
    """Whether the vehicle comes from one of the places asked for: state / office code, or a place word."""
    for o in origins:
        code = key(o)
        if re.fullmatch(r"[A-Z]{2}\d{0,2}", code) and (plate.startswith(code) or str(office_code or "") == code):
            return True
        if hits(texts, [o]):
            return True
    return False


class Tally:
    """Events per plate: how many, where, what kind, and the latest."""

    def __init__(self):
        self.plates = {}

    def add(self, plate, where=None, what=None, when=None):
        if not plate:
            return
        t = self.plates.setdefault(plate, {"count": 0, "where": Counter(), "what": Counter(), "last": ""})
        t["count"] += 1
        if where:
            t["where"][where] += 1
        if what:
            t["what"][what] += 1
        if when and str(when) > t["last"]:
            t["last"] = str(when)

    def result(self):
        return {p: {"count": t["count"], "where": [w for w, _ in t["where"].most_common(3)],
                    "what": [f"{w} x{n}" if n > 1 else w for w, n in t["what"].most_common()], "last": t["last"]}
                for p, t in self.plates.items()}


# ============================================================
# What one database found
# ============================================================

def plate_score(plate, wanted, wanted_counts=None):
    """(score, reason) of a stored plate against the query plates: exact 1.0, one slip 0.75, two slips 0.6."""
    best = (0.0, None)
    if not plate:
        return best
    counts = None
    for w in wanted:
        if not w:
            continue
        if w == plate:
            d = 0
        else:
            if abs(len(w) - len(plate)) > 2:
                continue
            counts = counts or Counter(plate)
            other = (wanted_counts or {}).get(w) or Counter(w)
            if sum((counts - other).values()) + sum((other - counts).values()) > 4:
                continue                       # each slip changes at most two character counts: more than 2 slips away
            d = rules.levenshtein_distance(plate, w)
        score = {0: 1.0, 1: 0.75, 2: 0.6}.get(d, 0.0)
        if score > best[0]:
            best = (score, f"plate {plate}" + ("" if d == 0 else f" ({d} character{'s' if d > 1 else ''} from {w})"))
    return best


def hits(texts, vocabulary):
    """Words of `vocabulary` (single words or phrases) that occur in any of `texts`, as whole words."""
    if not vocabulary:
        return []
    blob = "".join(_phrase(str(t)) for t in texts if t)
    return [v for v in vocabulary if v and _phrase(str(v)) in blob]


class Found(list):
    """Matched parts of the query, with the parts the record contradicts in `.conflicts`."""

    def __init__(self, *args):
        super().__init__(*args)
        self.conflicts = []


class Context:
    """Entities one database reached for a subquery, with the keys and terms they carry.

    Key kinds: plates, names (owners / holders: they identify the vehicle and travel to round 2), ids (FIR, policy,
    chassis ... numbers: travel too), staff (officers: describe the context only), places, models, orgs, words.
    """

    def __init__(self, device, sub):
        self.device, self.sub = device, sub
        keys = sub.get("keys") or {}
        self.plates = list(dict.fromkeys(list(sub.get("plates") or []) + list(keys.get("plates") or [])))
        # plates the user typed may be misread: matched up to two slips; plates another database found are its
        # cleaned plates: matched exactly (an index lookup instead of an edit distance per record)
        self._typed = [p for p in dict.fromkeys(sub.get("plates") or []) if p]
        self._typed_counts = {p: Counter(p) for p in self._typed}
        self._known = {p for p in keys.get("plates") or [] if p}
        self.names = [n for n in keys.get("names") or [] if n]
        self.ids = [key(i) for i in keys.get("ids") or [] if key(i)]
        self._name_keys = {key(n): n for n in self.names}
        self._id_keys = set(self.ids)
        self.free = list(sub.get("terms") or [])
        self.colors, self.types = list(sub.get("colors") or []), list(sub.get("types") or [])
        self.makes = list(sub.get("makes") or [])
        self.places = [p for p in keys.get("places") or [] if p]
        self.entities = {}

    def facets(self, color=None, vtype=None, model=None, places=(), other=()):
        """Which parts of the query a record matches: colour, type, make/model, place, other words.

        `.conflicts` lists what the record contradicts: the query names a colour, type or make and the record has
        another one (a black Maruti is no answer to "black Skoda").
        """
        found = Found()
        if color and hits([color], self.colors):
            found.append(f"colour {color}")
        elif color and self.colors:
            found.conflicts.append(f"colour {color}")
        if vtype and hits([vtype], self.types):
            found.append(f"type {vtype}")
        elif vtype and self.types:
            found.conflicts.append(f"type {vtype}")
        if model:
            model_hits = hits([model], self.makes)
            found += [f"model '{m}'" for m in model_hits]
            if self.makes and not model_hits and words(model):
                found.conflicts.append(f"model {model}")
        place_hits = hits(places, self.free + self.places)
        found += [f"place '{p}'" for p in place_hits]
        found += [f"'{w}'" for w in hits(other, self.free) if w not in place_hits]
        return found

    def match(self, plate, found, names=(), ids=(), base=0.3, step=0.15):
        """Score of one record: plate / key matches first, else at least two matched parts of the query."""
        if plate and plate in self._known:
            score, why = 1.0, f"plate {plate}"
        else:
            score, why = plate_score(plate, self._typed, self._typed_counts)
        reasons = [why] if why else []
        name_hits = [self._name_keys[key(m)] for m in names if m and key(m) in self._name_keys]
        id_hits = [key(m) for m in ids if m and key(m) in self._id_keys]
        reasons += [f"name {n}" for n in dict.fromkeys(name_hits)] + [f"number {i}" for i in dict.fromkeys(id_hits)]
        if id_hits:                                    # a policy / FIR / chassis number belongs to one vehicle
            score = max(score, 0.9)
        elif name_hits:                                # people share names: on its own a weak link
            score = max(score, 0.5)
        if score:
            return min(1.0, score + 0.05 * len(found)), reasons + list(found)
        if getattr(found, "conflicts", None):
            return 0.0, list(found)
        if len(found) >= 2:
            return min(0.8, base + step * (len(found) - 1)), found
        return 0.0, found

    def add(self, table, rid, plate, score, matched, path, summary, data, related=None, keys=None):
        if score <= 0:
            return
        entry = {"device": self.device, "table": table, "id": str(rid), "plate": plate, "score": round(score, 3),
                 "matched": matched, "path": path, "summary": summary, "data": data, "related": related or {},
                 "keys": {k: [v for v in vs if v] for k, vs in (keys or {}).items()}}
        entry["keys"].setdefault("plates", [])
        if plate and plate not in entry["keys"]["plates"]:
            entry["keys"]["plates"].append(plate)
        old = self.entities.get((table, str(rid)))
        if old is None or old["score"] < entry["score"]:
            self.entities[(table, str(rid))] = entry

    def result(self, limit=12):
        top = sorted(self.entities.values(), key=lambda e: (-e["score"], e["table"], e["id"]))[:limit]
        keys, terms = {}, {}
        for e in top:
            for kind, values in e["keys"].items():
                for v in values:
                    keys.setdefault(kind, [])
                    if v not in keys[kind]:
                        keys[kind].append(v)
                    if kind in ("places", "names", "staff", "models", "orgs", "words"):
                        for w in words(v):
                            if w not in STOPWORDS and len(w) > 2:
                                terms[w] = max(terms.get(w, 0.0), e["score"])
        return {"device": self.device, "entities": top, "keys": keys, "terms": terms}


# ============================================================
# Reading a device's main table (cleaned when cleansing has built it)
# ============================================================

def cleaned_ready(conn, table):
    try:
        built = conn.execute("SELECT value FROM cleansing_state WHERE key='built'").fetchone()
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table + "_clean",)).fetchone()
        return bool(exists) and bool(built) and json.loads(built[0]) is True
    except sqlite3.OperationalError:
        return False


def main_rows(conn, table):
    """The device's main records: the cleaned copy (Phase 2) when it is built, otherwise the raw table."""
    source = table + "_clean" if cleaned_ready(conn, table) else table
    return [dict(r) for r in conn.execute(f"SELECT * FROM {source}")]


def raw_ids(row, id_column):
    """Raw ids behind a (cleaned) row: related rows hang off every merged raw record."""
    ids = [i for i in str(row.get("raw_ids") or "").split(",") if i]
    return ids or [str(row.get(id_column))]


def group_by(rows, column):
    out = {}
    for r in rows:
        out.setdefault(str(r.get(column)), []).append(r)
    return out


# ============================================================
# Unstructured documents
# ============================================================

SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")


class DocIndex:
    """The plain-text documents of one device (one file = one document; first line = title), searched with BM25."""

    def __init__(self, folder, source, k1=1.4, b=0.75):
        self.folder, self.source, self.k1, self.b = folder, source, k1, b
        self._stamp, self.docs = None, []

    def _scan(self):
        if not os.path.isdir(self.folder):
            return ()
        return tuple(sorted((f, os.path.getmtime(os.path.join(self.folder, f)))
                            for f in os.listdir(self.folder) if f.endswith(".txt")))

    def load(self):
        stamp = self._scan()
        if stamp == self._stamp:
            return
        docs = []
        for name, _ in stamp:
            with open(os.path.join(self.folder, name), encoding="utf-8") as fh:
                text = fh.read()
            title = next((line.strip() for line in text.splitlines() if line.strip()), name)
            tokens = [w for w in words(text) if w not in STOPWORDS]
            candidates = plate_strings(text)
            docs.append({"name": name, "title": title, "text": text, "tf": Counter(tokens), "length": len(tokens),
                         "plates": candidates, "has_vehicle_plate": any(canonical_plate(p) for p in candidates),
                         "flat": key(text), "lower": " " + " ".join(words(text)) + " "})
        self.docs, self._stamp = docs, stamp
        self.df = Counter(t for d in docs for t in d["tf"])
        self.avgdl = (sum(d["length"] for d in docs) / len(docs)) if docs else 1.0

    def get(self, name):
        self.load()
        return next((d for d in self.docs if d["name"] == name), None)

    def search(self, context, limit=5):
        """context: {terms: {word: weight}, plates: [...], ids: [...], phrases: [...]} -> ranked documents."""
        self.load()
        terms = {t: float(w) for t, w in (context.get("terms") or {}).items() if t and t not in STOPWORDS}
        plates = [p for p in context.get("plates") or [] if p]
        exact_plates = set(plates) if context.get("exact_result_plates") else None
        background_terms = set(context.get("background_terms") or [])
        ids = [key(i) for i in context.get("ids") or [] if len(key(i)) >= 5]
        phrases = [p for p in context.get("phrases") or [] if len(words(p)) >= 2]
        n = len(self.docs)
        scored = []
        for d in self.docs:
            if background_terms and (d["has_vehicle_plate"] or not background_terms.intersection(d["tf"])):
                continue
            if exact_plates is not None:
                matched_plates = sorted(exact_plates.intersection(d["plates"]))
                if not matched_plates:
                    continue
            score, matched = 0.0, []
            for t, w in terms.items():
                tf = d["tf"].get(t, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                score += w * idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * d["length"] / self.avgdl))
                matched.append(t)
            links = ([{"plate": p, "written": p, "how": "exact"} for p in matched_plates]
                     if exact_plates is not None else [])
            score += 6.0 * len(links)
            for p in (plates if exact_plates is None else ()):
                best = min((rules.levenshtein_distance(p, s), s) for s in d["plates"]) if d["plates"] else (99, "")
                if best[0] == 0:
                    score += 6.0
                    links.append({"plate": p, "written": best[1], "how": "exact"})
                elif best[0] <= 2:
                    score += 4.0 - best[0]
                    links.append({"plate": p, "written": best[1], "how": f"possible misread ({best[0]} character"
                                                                        f"{'s' if best[0] > 1 else ''} different)"})
            id_hits = [i for i in ids if i in d["flat"]]
            score += 4.0 * len(id_hits)
            phrase_hits = [p for p in phrases if f" {' '.join(words(p))} " in d["lower"]]
            score += 1.5 * len(phrase_hits)
            if score > 0:
                scored.append({"doc": d, "score": score, "matched": matched, "plates": links, "ids": id_hits,
                               "phrases": phrase_hits})
        if not scored:
            return []
        top = max(s["score"] for s in scored)
        keep = [s for s in scored if (s["plates"] or s["ids"] or background_terms)
                or (len(set(s["matched"]) | set(s["phrases"])) >= 2 and s["score"] >= 0.35 * top)]
        keep.sort(key=lambda s: (-s["score"], s["doc"]["name"]))
        return [self._result(s) for s in keep[:limit]]

    def _result(self, s):
        d = s["doc"]
        marks = set(s["matched"]) | {w for p in s["phrases"] for w in words(p)}
        written = {l["written"] for l in s["plates"]}
        best, best_hits = "", -1
        for sentence in SENTENCE.split(d["text"]):
            sentence = sentence.strip()
            if not sentence or sentence == d["title"]:
                continue
            count = sum(1 for w in words(sentence) if w in marks) + 3 * sum(
                1 for p in written if p in key(sentence)) + 3 * sum(1 for i in s["ids"] if i in key(sentence))
            if count > best_hits:
                best, best_hits = sentence, count
        return {"source": self.source, "name": d["name"], "title": d["title"], "score": round(s["score"], 3),
                "snippet": best[:400], "matched_terms": sorted(set(s["matched"])), "plates": s["plates"],
                "ids": s["ids"], "phrases": s["phrases"], "highlight": sorted(marks)}


# ============================================================
# Endpoints of a device
# ============================================================

def schema_info(conn):
    """Tables of the database with their columns, foreign keys and row counts (Phase 3, item 1)."""
    out = []
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                "AND name NOT LIKE 'cleansing_%' AND name NOT LIKE '%_clean' ORDER BY name"):
        columns = [{"name": r[1], "type": r[2], "pk": bool(r[5])} for r in conn.execute(f"PRAGMA table_info({name})")]
        fks = [{"column": r[3], "references": f"{r[2]}({r[4]})"} for r in conn.execute(f"PRAGMA foreign_key_list({name})")]
        rows = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        out.append({"table": name, "columns": columns, "foreign_keys": fks, "rows": rows})
    return out


def register(app, device, db_path, discover, docs, joins=(), count=None, profile=None):
    """POST /context, POST /docs/search, GET /docs/<name>, GET /schema and /schema.json for one device, and for list
    queries POST /context/aggregate (events per vehicle in a period) and POST /context/profile (what this database
    says about given vehicles: owner, age, origin, insurance, theft).

    joins: the logical joins on natural keys (text columns), shown on /schema next to the declared foreign keys.
    count(conn, q) -> {plate: {count, where, what, last}}, or None when the event is not recorded here;
    profile(conn, q) -> {plate: {...}}.
    """
    from flask import jsonify, request, abort
    from html import escape

    def connect():
        conn = sqlite3.connect(db_path(), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def answer(fn, field, keep=None):
        q = request.get_json(silent=True) or {}
        start = time.time()
        out = None
        if fn is not None:
            conn = connect()
            try:
                out = fn(conn, q)
            finally:
                conn.close()
        extra = {}
        if out is not None and keep is not None:
            extra["counted"] = len(out)            # vehicles counted here; only those that pass are sent back
            out = keep(q, out)
        return jsonify({"device": device, field: out or {}, "supported": out is not None, **extra,
                        "elapsed_ms": round((time.time() - start) * 1000, 1)})

    def threshold(q, counts):
        """The count the user asked for ("more than 2") is applied here, so only the vehicles that pass leave
        this device; the client then asks the other devices about those plates only."""
        op, n = q.get("op"), q.get("n")
        if op in COUNT_TEST and n is not None:
            counts = {p: c for p, c in counts.items() if COUNT_TEST[op](c["count"], int(n))}
        if q.get("top"):                           # "top 10 ...": only the 10 highest counts
            counts = dict(sorted(counts.items(), key=lambda pc: (-pc[1]["count"], pc[0]))[:int(q["top"])])
        return counts

    def aggregate():
        return answer(count, "counts", keep=threshold)

    def profiles():
        return answer(profile, "profiles")

    def context():
        sub = request.get_json(silent=True) or {}
        start = time.time()
        conn = connect()
        try:
            out = discover(conn, sub)
        finally:
            conn.close()
        out["elapsed_ms"] = round((time.time() - start) * 1000, 1)
        return jsonify(out)

    def doc_search():
        body = request.get_json(silent=True) or {}
        return jsonify({"device": device, "results": docs.search(body, int(body.get("limit") or 5))})

    def doc_view(name):
        d = docs.get(name)
        if d is None:
            abort(404)
        return (f"<!doctype html><meta charset='utf-8'><title>{escape(d['title'])}</title>"
                f"<body style='font-family:Georgia,serif;max-width:720px;margin:40px auto;padding:0 16px'>"
                f"<p><a href='/schema'>{escape(device)}</a> &middot; documents/{escape(name)}</p>"
                f"<pre style='white-space:pre-wrap;font-family:inherit;font-size:16px;line-height:1.6'>{escape(d['text'])}</pre></body>")

    def schema_json():
        conn = connect()
        try:
            return jsonify({"device": device, "tables": schema_info(conn), "joins": list(joins)})
        finally:
            conn.close()

    def schema_page():
        conn = connect()
        try:
            tables = schema_info(conn)
        finally:
            conn.close()
        docs.load()
        parts = []
        for t in tables:
            cols = ", ".join(("<u>" + escape(c["name"]) + "</u>") if c["pk"] else escape(c["name"]) for c in t["columns"])
            fks = "<br>".join(f"{escape(f['column'])} &rarr; {escape(f['references'])}" for f in t["foreign_keys"]) or "&mdash;"
            parts.append(f"<tr><td><b>{escape(t['table'])}</b></td><td>{cols}</td><td>{fks}</td><td style='text-align:right'>{t['rows']}</td></tr>")
        join_rows = "".join(f"<li>{escape(j)}</li>" for j in joins)
        doc_rows = "".join(f"<li><a href='/docs/{escape(d['name'])}'>{escape(d['title'])}</a></li>" for d in docs.docs)
        return (f"<!doctype html><meta charset='utf-8'><title>{escape(device)} schema</title>"
                "<style>body{font-family:Arial;max-width:1100px;margin:30px auto;padding:0 16px}table{border-collapse:collapse;width:100%}"
                "td,th{border:1px solid #999;padding:6px;text-align:left;vertical-align:top;font-size:14px}</style>"
                f"<h2>{escape(device)} database</h2><p><a href='/'>&larr; Home</a></p>"
                "<p>Primary keys are underlined. Declared foreign keys are listed per table; the joins on natural keys follow.</p>"
                "<table><tr><th>Table</th><th>Columns</th><th>Foreign keys</th><th>Rows</th></tr>" + "".join(parts) + "</table>"
                f"<h3>Joins on natural keys</h3><ul>{join_rows or '<li>none</li>'}</ul>"
                f"<h3>Unstructured documents ({len(docs.docs)})</h3><ul>{doc_rows or '<li>none</li>'}</ul>")

    app.add_url_rule("/context", "ctx_context", context, methods=["POST"])
    app.add_url_rule("/context/aggregate", "ctx_aggregate", aggregate, methods=["POST"])
    app.add_url_rule("/context/profile", "ctx_profile", profiles, methods=["POST"])
    app.add_url_rule("/docs/search", "ctx_doc_search", doc_search, methods=["POST"])
    app.add_url_rule("/docs/<path:name>", "ctx_doc_view", doc_view)
    app.add_url_rule("/schema", "ctx_schema", schema_page)
    app.add_url_rule("/schema.json", "ctx_schema_json", schema_json)
