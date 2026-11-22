"""MoT database, Phase 3: related tables and context discovery.

    zone (zone_id PK, state_code UNIQUE) <-- zone_id FK -- officer (officer_id PK)
                                                              ^ officer_id FK
    mot_report (report_id PK) <-- report_id FK -- enforcement_action
                              <-- report_id FK -- challan

A report that arrives is assigned to the officer of the vehicle's state zone (first two letters of the plate), and an
uninsured or unregistered vehicle gets a challan.
"""
from datetime import datetime

import context_search as cs
import plate_rules as rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS zone (
    zone_id INTEGER PRIMARY KEY AUTOINCREMENT, state_code TEXT NOT NULL UNIQUE, name TEXT NOT NULL, headquarters TEXT);
CREATE TABLE IF NOT EXISTS officer (
    officer_id INTEGER PRIMARY KEY AUTOINCREMENT, zone_id INTEGER NOT NULL REFERENCES zone(zone_id),
    name TEXT NOT NULL, rank TEXT, phone TEXT);
CREATE TABLE IF NOT EXISTS enforcement_action (
    action_id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER NOT NULL REFERENCES mot_report(report_id),
    officer_id INTEGER REFERENCES officer(officer_id), assigned_on TEXT, action_type TEXT, status TEXT, remarks TEXT);
CREATE TABLE IF NOT EXISTS challan (
    challan_id INTEGER PRIMARY KEY AUTOINCREMENT, report_id INTEGER NOT NULL REFERENCES mot_report(report_id),
    challan_no TEXT NOT NULL UNIQUE, amount REAL, issued_on TEXT, status TEXT);
CREATE TRIGGER IF NOT EXISTS mot_report_delete_children BEFORE DELETE ON mot_report BEGIN
    DELETE FROM challan WHERE report_id = OLD.report_id;
    DELETE FROM enforcement_action WHERE report_id = OLD.report_id;
END;
"""

JOINS = ["first two letters of mot_report.plate_no = zone.state_code (zone that handles the vehicle)"]

ZONES = [("MH", "Maharashtra West Zone", "Pune"), ("DL", "Delhi Zone", "New Delhi"), ("KA", "Karnataka South Zone", "Bengaluru"),
         ("UP", "Uttar Pradesh Zone", "Lucknow"), ("TN", "Tamil Nadu Zone", "Chennai"), ("GJ", "Gujarat Zone", "Ahmedabad"),
         ("RJ", "Rajasthan Zone", "Jaipur"), ("PB", "Punjab Zone", "Ludhiana"), ("WB", "West Bengal Zone", "Kolkata"),
         ("KL", "Kerala Zone", "Kochi"), ("HR", "Haryana Zone", "Gurugram")]
OFFICERS = [("MH", "S. Deshpande", "Motor Vehicle Inspector", "98220 10001"), ("DL", "R. Bhatia", "Enforcement Officer", "98110 10002"),
            ("KA", "N. Shetty", "Motor Vehicle Inspector", "98450 10003"), ("UP", "A. Tiwari", "Enforcement Officer", "94150 10004"),
            ("TN", "P. Subramanian", "Motor Vehicle Inspector", "98400 10005"), ("GJ", "H. Joshi", "Enforcement Officer", "98250 10006"),
            ("RJ", "V. Rathore", "Motor Vehicle Inspector", "94140 10007"), ("PB", "G. Sandhu", "Enforcement Officer", "98140 10008"),
            ("WB", "S. Ghosh", "Motor Vehicle Inspector", "98300 10009"), ("KL", "M. Nair", "Enforcement Officer", "98470 10010"),
            ("HR", "D. Yadav", "Motor Vehicle Inspector", "98100 10011")]
FINES = {"UNINSURED": 2000.0, "UNREGISTERED_OR_FAKE_PLATE": 5000.0}


def ensure(conn):
    """Create the related tables, seed zones and officers once, and assign reports that have no action yet."""
    conn.executescript(SCHEMA)
    if not conn.execute("SELECT COUNT(*) FROM zone").fetchone()[0]:
        conn.executemany("INSERT INTO zone (state_code, name, headquarters) VALUES (?, ?, ?)", ZONES)
        zones = {code: zid for zid, code in conn.execute("SELECT zone_id, state_code FROM zone")}
        conn.executemany("INSERT INTO officer (zone_id, name, rank, phone) VALUES (?, ?, ?, ?)",
                         [(zones[c], *rest) for c, *rest in OFFICERS])
    for rid, plate, flags, action in conn.execute(
            "SELECT report_id, plate_no, risk_flags, recommended_action FROM mot_report WHERE report_id NOT IN "
            "(SELECT report_id FROM enforcement_action)").fetchall():
        assign(conn, rid, plate, flags, action)


def assign(conn, report_id, plate, risk_flags, recommended_action):
    """A new report: enforcement action for the zone's officer, and a challan for an uninsured / unregistered vehicle."""
    plate = rules.normalize_plate(plate)
    officer = conn.execute("SELECT o.officer_id FROM officer o JOIN zone z ON z.zone_id = o.zone_id WHERE z.state_code=? "
                           "ORDER BY o.officer_id LIMIT 1", (plate[:2],)).fetchone()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    kind = str(recommended_action or "").split(" - ")[0].title() or "Review"
    conn.execute("INSERT INTO enforcement_action (report_id, officer_id, assigned_on, action_type, status, remarks) "
                 "VALUES (?, ?, ?, ?, 'Assigned', ?)", (report_id, officer[0] if officer else None, now, kind, recommended_action))
    flags = [f.strip() for f in str(risk_flags or "").split(",") if f.strip()]
    amount = sum(FINES.get(f, 0.0) for f in flags)
    if amount:
        conn.execute("INSERT INTO challan (report_id, challan_no, amount, issued_on, status) VALUES (?, ?, ?, ?, 'Unpaid')",
                     (report_id, f"CH-{plate}-{report_id}", amount, now[:10]))


def discover(conn, sub):
    """MoT reports that match the subquery, each followed to its enforcement actions, officer, zone and challans."""
    ctx = cs.Context("MoT", sub)
    zones = {r["zone_id"]: dict(r) for r in conn.execute("SELECT * FROM zone")}
    officers = {r["officer_id"]: dict(r) for r in conn.execute("SELECT * FROM officer")}
    actions = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM enforcement_action")], "report_id")
    challans = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM challan")], "report_id")
    for r in cs.main_rows(conn, "mot_report"):
        plate = rules.normalize_plate(r.get("plate_no"))
        acts = actions.get(str(r["report_id"]), [])
        offs = [officers[a["officer_id"]] for a in acts if a.get("officer_id") in officers]
        zns = [zones[o["zone_id"]] for o in offs if o["zone_id"] in zones]
        chs = challans.get(str(r["report_id"]), [])
        found = ctx.facets(places=[z.get("name") for z in zns] + [z.get("headquarters") for z in zns],
                           other=[r.get("risk_flags"), r.get("recommended_action"), r.get("insurance_status"),
                                  r.get("theft_status")] + ["challan" if chs else None] + [o.get("name") for o in offs])
        score, matched = ctx.match(plate, found, names=[o.get("name") for o in offs], ids=[c.get("challan_no") for c in chs])
        if not score:
            continue
        path = [f"mot_report #{r['report_id']} ({r.get('generated_on')})"]
        path += [f"enforcement_action #{a['action_id']}: {a['action_type']}, {a['status']}" for a in acts]
        path += [f"officer #{o['officer_id']} {o['name']} ({o['rank']})" for o in offs]
        path += [f"zone #{z['zone_id']} {z['name']}" for z in zns]
        path += [f"challan #{c['challan_id']} {c['challan_no']}: Rs {c['amount']:.0f}, {c['status']}" for c in chs]
        summary = f"{plate} reported to MoT {r.get('generated_on')}: {r.get('risk_flags')}; {r.get('recommended_action')}"
        ctx.add("mot_report", r["report_id"], plate, score, matched, path, summary, r,
                related={"enforcement_action": acts, "officer": offs, "zone": zns, "challan": chs},
                keys={"staff": [o.get("name") for o in offs], "places": [z.get("headquarters") for z in zns],
                      "words": [r.get("risk_flags")], "ids": [c.get("challan_no") for c in chs]})
    return ctx.result()


def count(conn, q):
    """Challans issued, or reports received, per vehicle in a period (list queries)."""
    event = q.get("event")
    if event not in ("challan", "report"):
        return None
    bounds, wanted = cs.period_bounds(q), cs.wanted_plates(q)
    reports = {str(r["report_id"]): r for r in cs.main_rows(conn, "mot_report")}
    tally = cs.Tally()

    def plate_of(r):
        plate = rules.normalize_plate(r.get("plate_no"))
        return plate if wanted is None or plate in wanted else None

    if event == "report":
        for r in reports.values():
            if cs.in_period(r.get("generated_on"), bounds):
                tally.add(plate_of(r), None, r.get("risk_flags"), r.get("generated_on"))
    else:
        for ch in conn.execute("SELECT * FROM challan"):
            r = reports.get(str(ch["report_id"]))
            if r and cs.in_period(ch["issued_on"], bounds):
                tally.add(plate_of(r), None, f"{ch['status']} Rs {ch['amount'] or 0:.0f}", ch["issued_on"])
    return tally.result()
