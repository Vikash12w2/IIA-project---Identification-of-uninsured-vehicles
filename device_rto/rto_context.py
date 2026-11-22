"""RTO database, Phase 3: related tables and context discovery.

    rto_office (office_code PK, e.g. MH12)       vehicle_model (model_id PK, UNIQUE make + model)
      ^ state + district code of reg_number        ^ make, model (natural key)
    registration (reg_id PK) <-- reg_id FK -- ownership_transfer
                             <-- reg_id FK -- puc_certificate
                             <-- reg_id FK -- driving_licence (the registered owner's licence: date of birth)
"""
import re

import context_search as cs
import plate_rules as rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS rto_office (
    office_code TEXT PRIMARY KEY, name TEXT NOT NULL, city TEXT, state TEXT, address TEXT);
CREATE TABLE IF NOT EXISTS vehicle_model (
    model_id INTEGER PRIMARY KEY AUTOINCREMENT, make TEXT NOT NULL, model TEXT NOT NULL, fuel_type TEXT,
    vehicle_class TEXT, body_type TEXT, UNIQUE (make, model));
CREATE TABLE IF NOT EXISTS ownership_transfer (
    transfer_id INTEGER PRIMARY KEY AUTOINCREMENT, reg_id INTEGER NOT NULL REFERENCES registration(reg_id),
    previous_owner TEXT, new_owner TEXT, transfer_date TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS puc_certificate (
    puc_id INTEGER PRIMARY KEY AUTOINCREMENT, reg_id INTEGER NOT NULL REFERENCES registration(reg_id),
    certificate_no TEXT NOT NULL UNIQUE, tested_on TEXT, valid_upto TEXT, co_percent REAL, result TEXT);
CREATE TABLE IF NOT EXISTS driving_licence (
    licence_id INTEGER PRIMARY KEY AUTOINCREMENT, reg_id INTEGER NOT NULL REFERENCES registration(reg_id),
    licence_no TEXT NOT NULL UNIQUE, holder_name TEXT NOT NULL, date_of_birth TEXT NOT NULL, issued_on TEXT,
    valid_upto TEXT);
"""

JOINS = ["state + district code of registration.reg_number = rto_office.office_code (MH12AB1234 -> MH12)",
         "registration.make, registration.model = vehicle_model.make, vehicle_model.model"]

OFFICES = [("MH12", "RTO Pune", "Pune", "Maharashtra", "Sangamwadi, Pune"),
           ("MH14", "RTO Pimpri-Chinchwad", "Pune", "Maharashtra", "Akurdi, Pimpri-Chinchwad"),
           ("DL8", "Zonal Office Wazirpur", "New Delhi", "Delhi", "Wazirpur, New Delhi"),
           ("KA05", "RTO Bengaluru South", "Bengaluru", "Karnataka", "Jayanagar, Bengaluru"),
           ("UP16", "ARTO Noida", "Noida", "Uttar Pradesh", "Sector 33, Noida"),
           ("TN09", "RTO Chennai West", "Chennai", "Tamil Nadu", "K.K. Nagar, Chennai"),
           ("GJ01", "RTO Ahmedabad", "Ahmedabad", "Gujarat", "Subhash Bridge, Ahmedabad"),
           ("RJ14", "RTO Jaipur", "Jaipur", "Rajasthan", "Jhalana, Jaipur"),
           ("PB10", "RTO Ludhiana", "Ludhiana", "Punjab", "Gill Road, Ludhiana"),
           ("WB20", "PVD Kolkata South", "Kolkata", "West Bengal", "Behala, Kolkata"),
           ("KL07", "RTO Ernakulam", "Kochi", "Kerala", "Kakkanad, Kochi"),
           ("HR26", "RTA Gurugram", "Gurugram", "Haryana", "Sector 44, Gurugram")]
MODELS = [("Maruti Suzuki", "Swift", "Petrol", "LMV", "Hatchback"), ("Hyundai", "Creta", "Diesel", "LMV", "SUV"),
          ("Royal Enfield", "Classic 350", "Petrol", "MCWG", "Motorcycle"), ("Honda", "City", "Petrol", "LMV", "Sedan"),
          ("Tata", "Nexon", "Diesel", "LMV", "Compact SUV"), ("Tata", "LPT 1613", "Diesel", "HGV", "Truck"),
          ("Mahindra", "XUV300", "Petrol", "LMV", "Compact SUV"), ("Skoda", "Rapid", "Petrol", "LMV", "Sedan"),
          ("Hyundai", "i20", "Petrol", "LMV", "Hatchback"), ("Honda", "Amaze", "Petrol", "LMV", "Sedan"),
          ("Toyota", "Innova Crysta", "Diesel", "LMV", "MUV"), ("Kia", "Seltos", "Petrol", "LMV", "SUV")]
TRANSFERS = [("MH14ZZ0000", "Sunil Kulkarni", "Vikas Kulkarni", "2019-03-11", "Sale within family"),
             ("GJ01XY0001", "Kiran Patel", "Kiran Patel Transport Co.", "2016-02-01", "Transfer to company"),
             ("KL07LM8899", "Joseph Varkey", "Thomas Varkey", "2021-08-30", "Inheritance")]
PUCS = [("MH12AB1234", "PUC-MH12-55120", "2026-03-02", "2026-09-01", 0.21, "Pass"),
        ("DL8CAF5555", "PUC-DL8-90021", "2025-02-11", "2025-08-10", 0.48, "Pass"),
        ("KA05MZ9090", "PUC-KA05-33018", "2026-05-20", "2027-05-19", 0.30, "Pass"),
        ("UP16CD4321", "PUC-UP16-11007", "2026-01-15", "2026-07-14", 3.90, "Fail"),
        ("TN09EF7777", "PUC-TN09-71102", "2024-01-05", "2024-07-04", 0.40, "Pass"),
        ("MH14ZZ0000", "PUC-MH14-40933", "2026-06-28", "2027-06-27", 0.18, "Pass"),
        ("HR26NN4545", "PUC-HR26-12855", "2026-02-02", "2027-02-01", 0.12, "Pass")]
# the registered owner's driving licence, keyed by plate (a company owner has none)
LICENCES = [("MH12AB1234", "MH12 20120045871", "1994-03-12", "2012-05-02"),
            ("DL8CAF5555", "DL08 20060031207", "1988-07-01", "2006-09-11"),
            ("KA05MZ9090", "KA05 20190077120", "2001-02-14", "2019-03-20"),
            ("UP16CD4321", "UP16 19880012907", "1969-10-30", "1988-12-01"),
            ("TN09EF7777", "TN09 19970055018", "1979-05-21", "1997-07-15"),
            ("RJ14PQ6543", "RJ14 20160020981", "1997-12-03", "2016-01-10"),
            ("MH14ZZ0000", "MH14 19770009120", "1958-09-09", "1977-02-14"),
            ("PB10AA2222", "PB10 20200061532", "2002-06-18", "2020-07-01"),
            ("WB20BB3333", "WB20 20030018745", "1985-01-26", "2003-04-04"),
            ("KL07LM8899", "KL07 20080040917", "1990-08-15", "2008-10-20"),
            ("HR26NN4545", "HR26 20170087422", "1999-04-04", "2017-05-05")]
OFFICE_CODE = re.compile(r"^([A-Z]{2}\d{1,2})")


def office_code(plate):
    found = OFFICE_CODE.match(rules.normalize_plate(plate))
    return found.group(1) if found else None


def ensure(conn):
    """Create the related tables and seed them once (attached to existing registrations by plate)."""
    conn.executescript(SCHEMA)
    if not conn.execute("SELECT COUNT(*) FROM driving_licence").fetchone()[0]:
        owners = {}
        for rid, plate, owner in conn.execute("SELECT reg_id, reg_number, owner_name FROM registration ORDER BY reg_id"):
            owners.setdefault(plate, (rid, owner))
        conn.executemany("INSERT OR IGNORE INTO driving_licence (reg_id, licence_no, holder_name, date_of_birth, issued_on, "
                         "valid_upto) VALUES (?, ?, ?, ?, ?, ?)",
                         [(owners[p][0], no, owners[p][1], dob, issued, f"{int(issued[:4]) + 20}{issued[4:]}")
                          for p, no, dob, issued in LICENCES if p in owners])
    if conn.execute("SELECT COUNT(*) FROM rto_office").fetchone()[0]:
        return
    conn.executemany("INSERT INTO rto_office VALUES (?, ?, ?, ?, ?)", OFFICES)
    conn.executemany("INSERT OR IGNORE INTO vehicle_model (make, model, fuel_type, vehicle_class, body_type) "
                     "VALUES (?, ?, ?, ?, ?)", MODELS)
    reg = {}
    for rid, plate in conn.execute("SELECT reg_id, reg_number FROM registration ORDER BY reg_id"):
        reg.setdefault(plate, rid)
    conn.executemany("INSERT INTO ownership_transfer (reg_id, previous_owner, new_owner, transfer_date, reason) "
                     "VALUES (?, ?, ?, ?, ?)", [(reg[p], *rest) for p, *rest in TRANSFERS if p in reg])
    conn.executemany("INSERT INTO puc_certificate (reg_id, certificate_no, tested_on, valid_upto, co_percent, result) "
                     "VALUES (?, ?, ?, ?, ?, ?)", [(reg[p], *rest) for p, *rest in PUCS if p in reg])


def discover(conn, sub):
    """Registrations that match the subquery, each followed to its RTO office, model, ownership transfers and PUC."""
    ctx = cs.Context("RTO", sub)
    offices = {r["office_code"]: dict(r) for r in conn.execute("SELECT * FROM rto_office")}
    models = {(r["make"].lower(), r["model"].lower()): dict(r) for r in conn.execute("SELECT * FROM vehicle_model")}
    transfers = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM ownership_transfer")], "reg_id")
    pucs = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM puc_certificate")], "reg_id")
    licences = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM driving_licence")], "reg_id")
    for r in cs.main_rows(conn, "registration"):
        plate = rules.normalize_plate(r.get("reg_number"))
        ids = cs.raw_ids(r, "reg_id")
        office = offices.get(office_code(plate)) or {}
        model = models.get((str(r.get("make") or "").lower(), str(r.get("model") or "").lower())) or {}
        tr = [t for i in ids for t in transfers.get(i, [])]
        pc = [p for i in ids for p in pucs.get(i, [])]
        dl = [x for i in ids for x in licences.get(i, [])][:1]
        found = ctx.facets(model=f"{r.get('make') or ''} {r.get('model') or ''}",
                           places=[r.get("owner_address"), office.get("name"), office.get("city"), office.get("state")],
                           other=[r.get("owner_name"), model.get("fuel_type"), model.get("body_type"),
                                  model.get("vehicle_class")] + [t.get("previous_owner") for t in tr])
        names = [r.get("owner_name")] + [t.get("previous_owner") for t in tr] + [t.get("new_owner") for t in tr]
        score, matched = ctx.match(plate, found, names=names,
                                   ids=[r.get("chassis_number"), r.get("engine_number")] + [p.get("certificate_no") for p in pc])
        if not score:
            continue
        path = [f"registration #{r['reg_id']} {plate}"]
        if office:
            path.append(f"rto_office {office['office_code']} {office['name']}, {office['city']}")
        if model:
            path.append(f"vehicle_model #{model['model_id']} {model['make']} {model['model']} ({model['fuel_type']}, {model['body_type']})")
        path += [f"ownership_transfer #{t['transfer_id']}: {t['previous_owner']} -> {t['new_owner']} ({t['transfer_date']})" for t in tr]
        path += [f"puc_certificate #{p['puc_id']} {p['certificate_no']}: {p['result']}, valid to {p['valid_upto']}" for p in pc]
        path += [f"driving_licence #{x['licence_id']} {x['licence_no']}: {x['holder_name']}, born {x['date_of_birth']}" for x in dl]
        summary = (f"{plate} registered to {r.get('owner_name')} ({r.get('owner_address')}): {r.get('make')} {r.get('model')}, "
                   f"chassis {r.get('chassis_number')}, valid to {r.get('reg_valid_upto')}")
        ctx.add("registration", r["reg_id"], plate, score, matched, path, summary, r,
                related={"rto_office": office, "vehicle_model": model, "ownership_transfer": tr, "puc_certificate": pc,
                         "driving_licence": dl},
                keys={"names": names, "places": [r.get("owner_address"), office.get("city")],
                      "models": [f"{r.get('make') or ''} {r.get('model') or ''}"],
                      "ids": [r.get("chassis_number"), r.get("engine_number")]})
    return ctx.result()


def count(conn, q):
    """Ownership transfers per vehicle in a period (list queries)."""
    if q.get("event") != "transfer":
        return None
    bounds, wanted = cs.period_bounds(q), cs.wanted_plates(q)
    plate_of = {}
    for r in cs.main_rows(conn, "registration"):
        for i in cs.raw_ids(r, "reg_id"):
            plate_of[i] = rules.normalize_plate(r.get("reg_number"))
    tally = cs.Tally()
    for t in conn.execute("SELECT * FROM ownership_transfer"):
        plate = plate_of.get(str(t["reg_id"]))
        if plate and (wanted is None or plate in wanted) and cs.in_period(t["transfer_date"], bounds):
            tally.add(plate, None, t["reason"], t["transfer_date"])
    return tally.result()


def profile(conn, q):
    """Owner, owner's age (driving licence), and where each vehicle comes from (RTO office of the plate, owner's
    address). With q["age"] / q["origin"] / q["makes"] only the vehicles that match are returned."""
    wanted, today = cs.wanted_plates(q), cs.today_of(q)
    age, origins, makes = q.get("age"), q.get("origin") or [], q.get("makes") or []
    offices = {r["office_code"]: dict(r) for r in conn.execute("SELECT * FROM rto_office")}
    types = {(r["make"].casefold(), r["model"].casefold()): r["body_type"]
             for r in conn.execute("SELECT make, model, body_type FROM vehicle_model")}
    licences = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM driving_licence")], "reg_id")
    out = {}
    for r in cs.main_rows(conn, "registration"):
        plate = rules.normalize_plate(r.get("reg_number"))
        if plate in out or (wanted is not None and plate not in wanted):
            continue
        code = office_code(plate)
        office = offices.get(code) or {}
        lic = next((x for i in cs.raw_ids(r, "reg_id") for x in licences.get(i, [])), {})
        years = cs.age_on(lic.get("date_of_birth"), today) if lic else None
        if age and (years is None or not age["min"] <= years <= age["max"]):
            continue
        if origins and not cs.origin_ok(origins, plate, code, [r.get("owner_address"), office.get("name"),
                                                                office.get("city"), office.get("state")]):
            continue
        if makes and not cs.hits([f"{r.get('make') or ''} {r.get('model') or ''}"], makes):
            continue
        out[plate] = {"owner": r.get("owner_name"), "address": r.get("owner_address"), "office_code": code,
                      "office": office.get("name"), "city": office.get("city"), "state": office.get("state"),
                      "make": r.get("make"), "model": r.get("model"), "dob": lic.get("date_of_birth"),
                      "type": types.get(tuple((r.get(field) or "").casefold() for field in ("make", "model"))),
                      "licence_no": lic.get("licence_no"), "age": years, "age_group": cs.age_group(years)}
    return out
