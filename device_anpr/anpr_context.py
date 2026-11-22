"""ANPR database, Phase 3: related tables and context discovery.

    location (location_id PK)
      ^ location_id FK
    camera (camera_id PK) <-- camera_id FK -- camera_maintenance
      ^ camera_id (natural key)
    vehicle_capture (capture_id PK) <-- capture_id FK -- capture_violation
"""
import context_search as cs
import plate_rules as rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS location (
    location_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, area TEXT, city TEXT, state TEXT,
    latitude REAL, longitude REAL, road_type TEXT);
CREATE TABLE IF NOT EXISTS camera (
    camera_id TEXT PRIMARY KEY, location_id INTEGER NOT NULL REFERENCES location(location_id),
    camera_model TEXT, installed_on TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS capture_violation (
    violation_id INTEGER PRIMARY KEY AUTOINCREMENT, capture_id INTEGER NOT NULL REFERENCES vehicle_capture(capture_id),
    violation_type TEXT NOT NULL, recorded_speed INTEGER, speed_limit INTEGER, fine_amount REAL);
CREATE TABLE IF NOT EXISTS camera_maintenance (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT, camera_id TEXT NOT NULL REFERENCES camera(camera_id),
    serviced_on TEXT, issue TEXT, technician TEXT);
"""

JOINS = ["vehicle_capture.camera_id = camera.camera_id (camera of a capture)",
         "vehicle_capture.location = location.name (as recorded by the camera)"]

LOCATIONS = [
    ("NH48 Toll Plaza, Pune", "Khed Shivapur", "Pune", "Maharashtra", 18.3523, 73.8520, "National highway"),
    ("Katraj Chowk, Pune", "Katraj", "Pune", "Maharashtra", 18.4529, 73.8652, "Arterial road"),
    ("Shivaji Nagar, Pune", "Shivaji Nagar", "Pune", "Maharashtra", 18.5308, 73.8475, "City road"),
    ("FC Road, Pune", "Deccan", "Pune", "Maharashtra", 18.5236, 73.8411, "City road"),
    ("Ring Road, Delhi", "Dhaula Kuan", "New Delhi", "Delhi", 28.5921, 77.1610, "Ring road"),
    ("Silk Board, Bengaluru", "Silk Board Junction", "Bengaluru", "Karnataka", 12.9172, 77.6229, "Junction"),
    ("Electronic City, Bengaluru", "Electronic City Phase 1", "Bengaluru", "Karnataka", 12.8452, 77.6602, "Elevated expressway"),
    ("Hazratganj, Lucknow", "Hazratganj", "Lucknow", "Uttar Pradesh", 26.8505, 80.9450, "City road"),
    ("Anna Salai, Chennai", "Teynampet", "Chennai", "Tamil Nadu", 13.0418, 80.2484, "Arterial road"),
    ("SG Highway, Ahmedabad", "Thaltej", "Ahmedabad", "Gujarat", 23.0504, 72.5167, "State highway"),
    ("MI Road, Jaipur", "MI Road", "Jaipur", "Rajasthan", 26.9157, 75.8091, "City road"),
    ("Mall Road, Ludhiana", "Mall Road", "Ludhiana", "Punjab", 30.9052, 75.8414, "City road"),
    ("Park Street, Kolkata", "Park Street", "Kolkata", "West Bengal", 22.5530, 88.3520, "City road"),
    ("MG Road, Kochi", "Ravipuram", "Kochi", "Kerala", 9.9667, 76.2870, "City road"),
    ("Cyber City, Gurugram", "DLF Cyber City", "Gurugram", "Haryana", 28.4950, 77.0890, "Business district"),
]
CAMERAS = [("CAM-014", "NH48 Toll Plaza, Pune", "Vivotek IP9191-HP", "2023-04-11", "Active"),
           ("CAM-01", "NH48 Toll Plaza, Pune", "Vivotek IP9191-HP", "2025-10-02", "Active"),
           ("CAM-002", "Katraj Chowk, Pune", "Hikvision iDS-TCM403", "2022-08-19", "Active"),
           ("CAM-016", "Shivaji Nagar, Pune", "Hikvision iDS-TCM403", "2022-08-19", "Active"),
           ("CAM-020", "FC Road, Pune", "Hikvision iDS-TCM403", "2024-01-05", "Active"),
           ("CAM-101", "Ring Road, Delhi", "Bosch DINION 7100", "2021-11-30", "Active"),
           ("CAM-055", "Silk Board, Bengaluru", "Axis Q1700-LE", "2023-02-14", "Active"),
           ("CAM-058", "Electronic City, Bengaluru", "Axis Q1700-LE", "2023-02-14", "Under repair"),
           ("CAM-201", "Hazratganj, Lucknow", "CP Plus ANPR-500", "2022-06-01", "Active"),
           ("CAM-303", "Anna Salai, Chennai", "Hikvision iDS-TCM403", "2021-09-09", "Active"),
           ("CAM-410", "SG Highway, Ahmedabad", "Bosch DINION 7100", "2022-12-12", "Active"),
           ("CAM-511", "MI Road, Jaipur", "CP Plus ANPR-500", "2023-07-21", "Active"),
           ("CAM-620", "Mall Road, Ludhiana", "CP Plus ANPR-500", "2024-03-03", "Active"),
           ("CAM-701", "Park Street, Kolkata", "Axis Q1700-LE", "2022-05-17", "Active"),
           ("CAM-812", "MG Road, Kochi", "Vivotek IP9191-HP", "2023-10-10", "Active"),
           ("CAM-905", "Cyber City, Gurugram", "Bosch DINION 7100", "2024-06-25", "Active")]
VIOLATIONS = [("MH12AB1234", "2026-09-01 08:12:03", "Speeding", 96, 80, 2000.0),
              ("KA05MZ9090", "2026-09-03 07:22:10", "Riding without helmet", None, None, 1000.0),
              ("TN09EF7777", "2026-09-06 15:30:27", "Red light jump", None, None, 1000.0),
              ("MH14ZZ0000", "2026-09-09 10:10:10", "Speeding", 74, 50, 2000.0),
              ("UP16CD4321", "2026-09-04 12:00:00", "Wrong lane", None, None, 500.0)]
MAINTENANCE = [("CAM-014", "2026-08-28", "Lens fogging at night; plate misreads of B as 8 reported", "S. Pawar"),
               ("CAM-016", "2026-09-02", "IR illuminator replaced", "S. Pawar"),
               ("CAM-058", "2026-09-12", "Camera knocked out of alignment by a truck", "K. Rao"),
               ("CAM-303", "2026-07-30", "Firmware update to ANPR 4.2", "M. Selvam")]


def ensure(conn):
    """Create the related tables and seed them once (existing captures are found by plate and time)."""
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM location").fetchone()[0]:
        return
    conn.executemany("INSERT INTO location (name, area, city, state, latitude, longitude, road_type) VALUES (?, ?, ?, ?, ?, ?, ?)",
                     LOCATIONS)
    ids = {name: lid for lid, name in conn.execute("SELECT location_id, name FROM location")}
    conn.executemany("INSERT OR IGNORE INTO camera VALUES (?, ?, ?, ?, ?)",
                     [(c, ids[loc], model, installed, status) for c, loc, model, installed, status in CAMERAS])
    for plate, ts, kind, speed, limit, fine in VIOLATIONS:
        row = conn.execute("SELECT capture_id FROM vehicle_capture WHERE plate_number=? AND capture_ts=?", (plate, ts)).fetchone()
        if row:
            conn.execute("INSERT INTO capture_violation (capture_id, violation_type, recorded_speed, speed_limit, fine_amount) "
                         "VALUES (?, ?, ?, ?, ?)", (row[0], kind, speed, limit, fine))
    conn.executemany("INSERT INTO camera_maintenance (camera_id, serviced_on, issue, technician) VALUES (?, ?, ?, ?)", MAINTENANCE)


def discover(conn, sub):
    """Captures that match the subquery, each followed through camera -> location, violations and camera service log."""
    ctx = cs.Context("ANPR", sub)
    locations = {r["location_id"]: dict(r) for r in conn.execute("SELECT * FROM location")}
    by_name = {r["name"].lower(): r for r in locations.values()}
    cameras = {r["camera_id"]: dict(r) for r in conn.execute("SELECT * FROM camera")}
    violations = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM capture_violation")], "capture_id")
    service = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM camera_maintenance")], "camera_id")
    for c in cs.main_rows(conn, "vehicle_capture"):
        plate = rules.normalize_plate(c.get("plate_number"))
        cam = cameras.get(c.get("camera_id")) or {}
        loc = locations.get(cam.get("location_id")) or by_name.get(str(c.get("location") or "").lower()) or {}
        found_violations = [v for rid in cs.raw_ids(c, "capture_id") for v in violations.get(rid, [])]
        logs = service.get(str(c.get("camera_id")), [])
        places = [c.get("location"), loc.get("name"), loc.get("area"), loc.get("city"), loc.get("state")]
        found = ctx.facets(c.get("vehicle_color"), c.get("vehicle_type"), c.get("make_model_seen"), places,
                           other=[v["violation_type"] for v in found_violations] + [c.get("camera_id")])
        score, matched = ctx.match(plate, found)
        if not score:
            continue
        path = [f"vehicle_capture #{c['capture_id']}"]
        if cam:
            path.append(f"camera {cam['camera_id']} ({cam.get('camera_model')})")
        if loc:
            path.append(f"location #{loc['location_id']} {loc['name']} ({loc['road_type']})")
        path += [f"capture_violation #{v['violation_id']}: {v['violation_type']}" for v in found_violations]
        path += [f"camera_maintenance #{m['log_id']}: {m['issue']}" for m in logs]
        summary = (f"{plate} seen {c.get('capture_ts')} at {c.get('location')} by {c.get('camera_id')}: "
                   f"{c.get('vehicle_color')} {c.get('vehicle_type')}, {c.get('make_model_seen')}")
        ctx.add("vehicle_capture", c["capture_id"], plate, score, matched, path, summary, c,
                related={"camera": cam, "location": loc, "violations": found_violations, "camera_maintenance": logs},
                keys={"places": [loc.get("name"), loc.get("city")], "models": [c.get("make_model_seen")],
                      "words": [v["violation_type"] for v in found_violations] + [c.get("vehicle_color")],
                      "ids": [c.get("camera_id")]})
    return ctx.result()


def profile(conn, q):
    """Latest camera observation for each requested plate."""
    wanted = cs.wanted_plates(q)
    if not wanted:
        return {}
    cities = {r["name"].casefold(): r["city"] for r in conn.execute("SELECT name, city FROM location")}
    latest = {}
    for c in cs.main_rows(conn, "vehicle_capture"):
        plate = rules.normalize_plate(c.get("plate_number"))
        if plate not in wanted:
            continue
        stamp = str(c.get("capture_ts") or "")
        if plate not in latest or stamp > latest[plate][0]:
            latest[plate] = (stamp, c)
    return {plate: {"color": c.get("vehicle_color"), "type": c.get("vehicle_type"),
                    "make_model": c.get("make_model_seen"),
                    "place": cities.get(str(c.get("location") or "").casefold()) or c.get("location")}
            for plate, (_, c) in latest.items()}


def count(conn, q):
    """Events per vehicle in a period, for list queries: camera sightings, or the violations recorded on them.
    Sightings can be narrowed to a place, colour, type or make seen by the camera."""
    event = q.get("event")
    if event not in ("violation", "capture"):
        return None
    bounds, wanted = cs.period_bounds(q), cs.wanted_plates(q)
    places = q.get("places") or []
    colors = {str(c).lower() for c in q.get("colors") or []}
    types = q.get("types") or []
    makes = q.get("makes") or []
    detail = str(q.get("detail") or "").lower()
    locations = {r["location_id"]: dict(r) for r in conn.execute("SELECT * FROM location")}
    by_name = {r["name"].lower(): r for r in locations.values()}
    cameras = {r["camera_id"]: dict(r) for r in conn.execute("SELECT * FROM camera")}
    violations = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM capture_violation")], "capture_id") \
        if event == "violation" else {}
    tally = cs.Tally()
    observations = {}
    for c in cs.main_rows(conn, "vehicle_capture"):
        if not cs.in_period(c.get("capture_ts"), bounds):
            continue
        plate = rules.normalize_plate(c.get("plate_number"))
        if wanted is not None and plate not in wanted:
            continue
        seen_type = str(c.get("vehicle_type") or "").lower()
        if (colors and str(c.get("vehicle_color") or "").strip().lower().replace("gray", "grey") not in colors) or \
                not cs.matches_vehicle_type(seen_type, types) or \
                (makes and not cs.hits([c.get("make_model_seen")], makes)):
            continue
        cam = cameras.get(c.get("camera_id")) or {}
        loc = locations.get(cam.get("location_id")) or by_name.get(str(c.get("location") or "").lower()) or {}
        if places and not cs.hits([c.get("location"), loc.get("name"), loc.get("area"), loc.get("city"),
                                   loc.get("state")], places):
            continue
        where = loc.get("city") or c.get("location")
        observation = {"color": c.get("vehicle_color"), "type": c.get("vehicle_type"), "place": where}
        if event == "capture":
            tally.add(plate, where, None, c.get("capture_ts"))
        else:
            kinds = set()                 # a capture saved twice keeps one violation of each kind
            for rid in cs.raw_ids(c, "capture_id"):
                for v in violations.get(rid, []):
                    kind = v.get("violation_type")
                    if kind in kinds or (detail and detail not in str(kind or "").lower()):
                        continue
                    kinds.add(kind)
                    tally.add(plate, where, kind, c.get("capture_ts"))
            if not kinds:
                continue
        stamp = str(c.get("capture_ts") or "")
        if plate not in observations or stamp > observations[plate][0]:
            observations[plate] = (stamp, observation)
    results = tally.result()
    for plate, result in results.items():
        result["observation"] = observations[plate][1]
    return results
