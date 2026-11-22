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

def parse_query(text):
    """Free text -> {plates, colors, types, makes, intents, terms}; terms are the remaining meaningful words."""
    text = str(text or "")
    plates = plate_mentions(text)
    plate_words = set()
    for s in plate_strings(text):
        if canonical_plate(s) in plates or s in plates:
            plate_words |= set(words(s))
    tokens = [w for w in words(text)]
    plate_tokens = {w for w in tokens if any(w.upper() in p or key(w) == p for p in plates)} | plate_words
    colors = sorted({"grey" if w == "gray" else w for w in tokens if w in COLORS})
    types = sorted({w for w in tokens if w in TYPES})
    makes = [w for w in tokens if w in MAKES]
    intents = sorted({INTENTS[w] for w in tokens if w in INTENTS})
    terms = []
    for w in tokens:
        if w in STOPWORDS or w in plate_tokens or len(w) < 3 or w in COLORS or w in TYPES or w in MAKES:
            continue
        if w not in terms:
            terms.append(w)
    return {"text": text, "plates": plates, "colors": colors, "types": types, "makes": list(dict.fromkeys(makes)),
            "intents": intents, "terms": terms}


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
            docs.append({"name": name, "title": title, "text": text, "tf": Counter(tokens), "length": len(tokens),
                         "plates": plate_strings(text), "flat": key(text), "lower": " " + " ".join(words(text)) + " "})
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
        ids = [key(i) for i in context.get("ids") or [] if len(key(i)) >= 5]
        phrases = [p for p in context.get("phrases") or [] if len(words(p)) >= 2]
        n = len(self.docs)
        scored = []
        for d in self.docs:
            score, matched = 0.0, []
            for t, w in terms.items():
                tf = d["tf"].get(t, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                score += w * idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * d["length"] / self.avgdl))
                matched.append(t)
            links = []
            for p in plates:
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
        keep = [s for s in scored if (s["plates"] or s["ids"])
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


def register(app, device, db_path, discover, docs, joins=()):
    """POST /context, POST /docs/search, GET /docs/<name>, GET /schema and /schema.json for one device.

    joins: the logical joins on natural keys (text columns), shown on /schema next to the declared foreign keys.
    """
    from flask import jsonify, request, abort
    from html import escape

    def connect():
        conn = sqlite3.connect(db_path(), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

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
    app.add_url_rule("/docs/search", "ctx_doc_search", doc_search, methods=["POST"])
    app.add_url_rule("/docs/<path:name>", "ctx_doc_view", doc_view)
    app.add_url_rule("/schema", "ctx_schema", schema_page)
    app.add_url_rule("/schema.json", "ctx_schema_json", schema_json)
