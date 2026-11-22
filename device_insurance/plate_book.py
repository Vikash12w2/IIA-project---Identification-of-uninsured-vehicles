"""A device's plate dictionary and review history, in its OWN file next to the source database.

e.g. device_anpr/plate_dictionary.db (never inside anpr.db). One file can hold several databases'
dictionaries (Theft and Shredding share device_theft/plate_dictionary.db), kept apart by `source`.

  plates     correct plates of a database (initial = there when the dictionary was first built)
  variants   noisy version -> correct plate. Generated ones only for the initial plates; later ones only
             when a person confirms them (Yes, a typed plate, or a fix on the cleansing page)
  history    review actions at entry / cleansing (rejected, confirmed, unresolved)
  unresolved records saved as typed through the explicit "unresolved" path, until they are fixed
"""
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from plate_rules import normalize_plate, plate_format_valid, format_breaking_variants

SCHEMA = """
CREATE TABLE IF NOT EXISTS plates (
    source TEXT NOT NULL, correct_normalized TEXT NOT NULL, correct_plate TEXT NOT NULL,
    origin TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (source, correct_normalized));
CREATE TABLE IF NOT EXISTS variants (
    source TEXT NOT NULL, correct_normalized TEXT NOT NULL, variant_normalized TEXT NOT NULL,
    origin TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (source, correct_normalized, variant_normalized));
CREATE INDEX IF NOT EXISTS idx_variant_lookup ON variants(source, variant_normalized);
CREATE TABLE IF NOT EXISTS built (source TEXT PRIMARY KEY, built_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, observed_plate TEXT NOT NULL,
    observed_normalized TEXT NOT NULL, action TEXT NOT NULL, proposed_plate TEXT, at TEXT NOT NULL,
    record_type TEXT, record_id TEXT, details_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_history_lookup ON history(source, at, observed_normalized);
CREATE TABLE IF NOT EXISTS unresolved (
    source TEXT NOT NULL, record_type TEXT NOT NULL, record_id TEXT NOT NULL, plate TEXT NOT NULL,
    created_at TEXT NOT NULL, PRIMARY KEY (source, record_type, record_id));
"""

# An entry ends (and its tries / rejected suggestions restart) with one of these.
END_ACTIONS = ("confirmed", "unresolved")


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class PlateBook:
    def __init__(self, path):
        self.path = path
        self._ready = None                  # the path whose tables exist (checked once, not on every lookup)

    @contextmanager
    def _db(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            if self._ready != self.path:
                conn.executescript(SCHEMA)
                self._ready = self.path
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---------- dictionary ----------

    def build(self, source, plates):
        """Called at service start. The FIRST build seeds every valid plate with its generated noisy variants;
        later starts only add plates that are missing, without variants. Returns the number of variants added."""
        valid = sorted({normalize_plate(p) for p in plates if plate_format_valid(p)})
        now = now_utc()
        with self._db() as conn:
            first = conn.execute("SELECT 1 FROM built WHERE source=?", (source,)).fetchone() is None
            conn.executemany("INSERT OR IGNORE INTO plates VALUES (?, ?, ?, ?, ?)",
                             [(source, p, p, "initial" if first else "saved", now) for p in valid])
            if not first:
                return 0
            rows = [(source, p, v, "generated", now) for p in valid for v in sorted(format_breaking_variants(p))]
            conn.executemany("INSERT OR IGNORE INTO variants VALUES (?, ?, ?, ?, ?)", rows)
            conn.execute("INSERT INTO built VALUES (?, ?)", (source, now))
            return len(rows)

    def add_plate(self, source, plate, origin="saved"):
        """A correct plate joins the dictionary with no variants (plates added through the GUI are not expanded)."""
        norm = normalize_plate(plate)
        if not plate_format_valid(norm):
            return
        with self._db() as conn:
            conn.execute("INSERT OR IGNORE INTO plates VALUES (?, ?, ?, ?, ?)", (source, norm, norm, origin, now_utc()))

    def add_plates(self, source, plates, origin="saved"):
        """Many correct plates at once (no variants)."""
        rows = sorted({normalize_plate(p) for p in plates if plate_format_valid(p)})
        if rows:
            with self._db() as conn:
                now = now_utc()
                conn.executemany("INSERT OR IGNORE INTO plates VALUES (?, ?, ?, ?, ?)",
                                 [(source, p, p, origin, now) for p in rows])

    def learn(self, source, correct_plate, noisy_plate, origin="confirmed"):
        """A person confirmed noisy -> correct. A noisy version that is itself a valid plate is never stored."""
        correct, noisy = normalize_plate(correct_plate), normalize_plate(noisy_plate)
        if not plate_format_valid(correct) or not noisy or noisy == correct or plate_format_valid(noisy):
            return False
        now = now_utc()
        with self._db() as conn:
            conn.execute("INSERT OR IGNORE INTO plates VALUES (?, ?, ?, ?, ?)", (source, correct, correct, "saved", now))
            conn.execute("INSERT OR REPLACE INTO variants VALUES (?, ?, ?, ?, ?)", (source, correct, noisy, origin, now))
        return True

    def lookup(self, source, noisy_plate):
        """{correct_plate: origin} that this noisy version maps to in one database."""
        with self._db() as conn:
            rows = conn.execute("SELECT correct_normalized, origin FROM variants WHERE source=? AND variant_normalized=? "
                                "ORDER BY correct_normalized", (source, normalize_plate(noisy_plate))).fetchall()
        return {r["correct_normalized"]: r["origin"] for r in rows}

    def grouped(self, source, search=""):
        """[{plate, origin, variants: [(variant, origin)]}] of one database, for its dictionary page."""
        q = normalize_plate(search)
        with self._db() as conn:
            plates = conn.execute("SELECT correct_normalized, origin FROM plates WHERE source=? ORDER BY correct_normalized",
                                  (source,)).fetchall()
            variants = conn.execute("SELECT correct_normalized, variant_normalized, origin FROM variants WHERE source=? "
                                    "ORDER BY correct_normalized, origin <> 'generated', variant_normalized", (source,)).fetchall()
        by_plate = {}
        for v in variants:
            by_plate.setdefault(v["correct_normalized"], []).append((v["variant_normalized"], v["origin"]))
        out = []
        for p in plates:
            vs = by_plate.get(p["correct_normalized"], [])
            if q and q not in p["correct_normalized"] and not any(q in v for v, _ in vs):
                continue
            out.append({"plate": p["correct_normalized"], "origin": p["origin"], "variants": vs})
        return out

    # ---------- review history ----------

    def record(self, source, observed_plate, action, proposed_plate=None, at="entry",
               record_type=None, record_id=None, details=None):
        with self._db() as conn:
            conn.execute("""INSERT INTO history (source, observed_plate, observed_normalized, action, proposed_plate, at,
                record_type, record_id, details_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (source, str(observed_plate or ""), normalize_plate(observed_plate), action,
                 normalize_plate(proposed_plate) or None, at, record_type,
                 None if record_id is None else str(record_id), json.dumps(details or {}, sort_keys=True), now_utc()))

    def entry_state(self, source, observed_plate):
        """(tries used, rejected suggestions) of the CURRENT entry of this observed plate."""
        with self._db() as conn:
            rows = conn.execute("SELECT action, proposed_plate FROM history WHERE source=? AND at='entry' "
                                "AND observed_normalized=? ORDER BY history_id",
                                (source, normalize_plate(observed_plate))).fetchall()
        attempts, rejected = 0, set()
        for r in rows:
            if r["action"] in END_ACTIONS:
                attempts, rejected = 0, set()
            elif r["action"] == "rejected":
                attempts += 1
                if r["proposed_plate"]:
                    rejected.add(r["proposed_plate"])
        return attempts, rejected

    def cleanse_rejected(self, source, record_type, record_id):
        """Suggestions already answered No for this record on the cleansing page."""
        with self._db() as conn:
            rows = conn.execute("SELECT proposed_plate FROM history WHERE source=? AND at='cleanse' AND action='rejected' "
                                "AND record_type=? AND record_id=?", (source, record_type, str(record_id))).fetchall()
        return {r["proposed_plate"] for r in rows if r["proposed_plate"]}

    def history(self, source, limit=50):
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM history WHERE source=? ORDER BY history_id DESC LIMIT ?",
                                (source, int(limit))).fetchall()
        return [dict(r) for r in rows]

    # ---------- unresolved records ----------

    def mark_unresolved(self, source, record_type, record_id, plate):
        with self._db() as conn:
            conn.execute("INSERT OR REPLACE INTO unresolved VALUES (?, ?, ?, ?, ?)",
                         (source, record_type, str(record_id), str(plate), now_utc()))

    def resolve(self, source, record_type, record_id):
        with self._db() as conn:
            conn.execute("DELETE FROM unresolved WHERE source=? AND record_type=? AND record_id=?",
                         (source, record_type, str(record_id)))

    def unresolved(self, source):
        """{(record_type, record_id): plate saved as typed} still waiting for review."""
        with self._db() as conn:
            rows = conn.execute("SELECT record_type, record_id, plate FROM unresolved WHERE source=?", (source,)).fetchall()
        return {(r["record_type"], r["record_id"]): r["plate"] for r in rows}
