"""Theft/Shredding database, Phase 3: related tables and context discovery.

    police_station (station_id PK, name UNIQUE)       scrap_center (center_id PK, name UNIQUE)
      ^ police_station (natural key)                    ^ scrap_center (natural key)
    theft_report (theft_id PK) <-- theft_id FK --       shredding_record (shred_id PK) <-- shred_id FK --
                     theft_recovery                                      shredding_handover
"""
import context_search as cs
import plate_rules as rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS police_station (
    station_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, city TEXT, state TEXT, phone TEXT,
    officer_in_charge TEXT);
CREATE TABLE IF NOT EXISTS scrap_center (
    center_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, city TEXT, state TEXT,
    authorization_no TEXT);
CREATE TABLE IF NOT EXISTS theft_recovery (
    recovery_id INTEGER PRIMARY KEY AUTOINCREMENT, theft_id INTEGER NOT NULL REFERENCES theft_report(theft_id),
    recovered_on TEXT, recovered_at TEXT, vehicle_condition TEXT, recovered_by TEXT);
CREATE TABLE IF NOT EXISTS shredding_handover (
    handover_id INTEGER PRIMARY KEY AUTOINCREMENT, shred_id INTEGER NOT NULL REFERENCES shredding_record(shred_id),
    handed_over_on TEXT, handed_over_by TEXT, documents_checked TEXT, odometer_km INTEGER);
"""

JOINS = ["theft_report.police_station = police_station.name (station that registered the FIR)",
         "shredding_record.scrap_center = scrap_center.name (centre that scrapped the vehicle)"]

STATIONS = [("Shivaji Nagar PS, Pune", "Pune", "Maharashtra", "020 2553 1000", "Insp. R. Jadhav"),
            ("MG Road PS, Kochi", "Kochi", "Kerala", "0484 239 4100", "CI Anil Kumar"),
            ("Silk Board PS, Bengaluru", "Bengaluru", "Karnataka", "080 2552 3100", "PI M. Gowda"),
            ("Anna Salai PS, Chennai", "Chennai", "Tamil Nadu", "044 2852 0000", "Insp. K. Murugan")]
CENTERS = [("GreenScrap Recyclers, Chennai", "Chennai", "Tamil Nadu", "RVSF-TN-0042"),
           ("Maharashtra Auto Recyclers, Pune", "Pune", "Maharashtra", "RVSF-MH-0117")]
HANDOVERS = [("SCR-2026-0099", "2026-06-28", "Karthik Raja", "RC, insurance copy, owner ID, chassis imprint", 142310)]
RECOVERIES = [("FIR-2025-9981", "2025-12-20", "Aluva, Ernakulam", "Minor damage, original plates", "MG Road PS, Kochi")]


def ensure(conn):
    """Create the related tables and seed them once (recoveries attach to existing theft reports by FIR number)."""
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM police_station").fetchone()[0]:
        return
    conn.executemany("INSERT INTO police_station (name, city, state, phone, officer_in_charge) VALUES (?, ?, ?, ?, ?)", STATIONS)
    conn.executemany("INSERT INTO scrap_center (name, city, state, authorization_no) VALUES (?, ?, ?, ?)", CENTERS)
    theft = {fir: tid for tid, fir in conn.execute("SELECT theft_id, fir_number FROM theft_report")}
    conn.executemany("INSERT INTO theft_recovery (theft_id, recovered_on, recovered_at, vehicle_condition, recovered_by) "
                     "VALUES (?, ?, ?, ?, ?)", [(theft[f], *rest) for f, *rest in RECOVERIES if f in theft])
    shred = {cert: sid for sid, cert in conn.execute("SELECT shred_id, certificate_no FROM shredding_record")}
    conn.executemany("INSERT INTO shredding_handover (shred_id, handed_over_on, handed_over_by, documents_checked, odometer_km) "
                     "VALUES (?, ?, ?, ?, ?)", [(shred[c], *rest) for c, *rest in HANDOVERS if c in shred])


def discover(conn, sub):
    """Theft reports (with station and recovery) and shredding records (with scrap centre) that match the subquery."""
    ctx = cs.Context("Theft/Shredding", sub)
    stations = {r["name"].lower(): dict(r) for r in conn.execute("SELECT * FROM police_station")}
    centers = {r["name"].lower(): dict(r) for r in conn.execute("SELECT * FROM scrap_center")}
    recoveries = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM theft_recovery")], "theft_id")
    handovers = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM shredding_handover")], "shred_id")
    for t in cs.main_rows(conn, "theft_report"):
        plate = rules.normalize_plate(t.get("vehicle_no"))
        station = stations.get(str(t.get("police_station") or "").lower()) or {}
        rec = [x for i in cs.raw_ids(t, "theft_id") for x in recoveries.get(i, [])]
        state_words = ["stolen", "theft", "fir"] + (["recovered"] if rec or t.get("status") == "Recovered" else [])
        found = ctx.facets(places=[t.get("police_station"), station.get("city"), station.get("state")]
                           + [x.get("recovered_at") for x in rec], other=state_words + [t.get("status")])
        score, matched = ctx.match(plate, found, ids=[t.get("fir_number")])
        if not score:
            continue
        path = [f"theft_report #{t['theft_id']} {t.get('fir_number')} ({t.get('status')})"]
        if station:
            path.append(f"police_station #{station['station_id']} {station['name']} ({station['officer_in_charge']})")
        path += [f"theft_recovery #{x['recovery_id']}: {x['recovered_on']} at {x['recovered_at']}" for x in rec]
        summary = (f"{plate} reported stolen {t.get('report_date')} at {t.get('police_station')}, "
                   f"{t.get('fir_number')}, {t.get('status')}" + (f"; recovered {rec[0]['recovered_on']} at {rec[0]['recovered_at']}" if rec else ""))
        ctx.add("theft_report", t["theft_id"], plate, score, matched, path, summary, t,
                related={"police_station": station, "theft_recovery": rec},
                keys={"ids": [t.get("fir_number")], "places": [t.get("police_station"), station.get("city")]
                      + [x.get("recovered_at") for x in rec], "staff": [station.get("officer_in_charge")],
                      "words": ["stolen", t.get("status")]})
    for s in cs.main_rows(conn, "shredding_record"):
        plate = rules.normalize_plate(s.get("vehicle_no"))
        center = centers.get(str(s.get("scrap_center") or "").lower()) or {}
        hand = [h for i in cs.raw_ids(s, "shred_id") for h in handovers.get(i, [])]
        found = ctx.facets(places=[s.get("scrap_center"), center.get("city"), center.get("state")],
                           other=["scrapped", "shredded", "scrap", "shredding"])
        score, matched = ctx.match(plate, found, ids=[s.get("certificate_no")], names=[h.get("handed_over_by") for h in hand])
        if not score:
            continue
        path = [f"shredding_record #{s['shred_id']} {s.get('certificate_no')}"]
        if center:
            path.append(f"scrap_center #{center['center_id']} {center['name']} ({center['authorization_no']})")
        path += [f"shredding_handover #{h['handover_id']}: {h['handed_over_on']} by {h['handed_over_by']}" for h in hand]
        summary = f"{plate} scrapped {s.get('shred_date')} at {s.get('scrap_center')}, certificate {s.get('certificate_no')}"
        ctx.add("shredding_record", s["shred_id"], plate, score, matched, path, summary, s,
                related={"scrap_center": center, "shredding_handover": hand},
                keys={"ids": [s.get("certificate_no")], "places": [s.get("scrap_center"), center.get("city")],
                      "names": [h.get("handed_over_by") for h in hand],
                      "words": ["scrapped"]})
    return ctx.result()


def count(conn, q):
    """Theft reports (FIRs) per vehicle in a period (list queries)."""
    if q.get("event") != "theft":
        return None
    bounds, wanted = cs.period_bounds(q), cs.wanted_plates(q)
    tally = cs.Tally()
    for t in cs.main_rows(conn, "theft_report"):
        plate = rules.normalize_plate(t.get("vehicle_no"))
        if (wanted is None or plate in wanted) and cs.in_period(t.get("report_date"), bounds):
            tally.add(plate, t.get("police_station"), t.get("status"), t.get("report_date"))
    return tally.result()


def profile(conn, q):
    """Whether each vehicle is reported stolen (or recovered) and whether it was scrapped."""
    wanted = cs.wanted_plates(q)
    out = {}
    for t in cs.main_rows(conn, "theft_report"):
        plate = rules.normalize_plate(t.get("vehicle_no"))
        if wanted is not None and plate not in wanted:
            continue
        p = out.setdefault(plate, {})
        if p.get("theft") != "Reported":
            p.update(theft=t.get("status"), fir=t.get("fir_number"))
    for s in cs.main_rows(conn, "shredding_record"):
        plate = rules.normalize_plate(s.get("vehicle_no"))
        if wanted is None or plate in wanted:
            out.setdefault(plate, {})["scrapped"] = s.get("shred_date")
    return out
