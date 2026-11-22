"""Insurance database, Phase 3: related tables and context discovery.

    insurer (insurer_id PK, name UNIQUE)
      ^ insurer_name (natural key)
    policy (policy_id PK) <-- policy_id FK -- policy_holder
                          <-- policy_id FK -- claim
                          <-- policy_id FK -- premium_payment
"""
from datetime import date

import context_search as cs
import insurance_rules
import plate_rules as rules

SCHEMA = """
CREATE TABLE IF NOT EXISTS insurer (
    insurer_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE, irdai_reg_no TEXT, head_office TEXT,
    claims_email TEXT);
CREATE TABLE IF NOT EXISTS policy_holder (
    holder_id INTEGER PRIMARY KEY AUTOINCREMENT, policy_id INTEGER NOT NULL REFERENCES policy(policy_id),
    holder_name TEXT NOT NULL, phone TEXT, address TEXT, kyc_id TEXT);
CREATE TABLE IF NOT EXISTS claim (
    claim_id INTEGER PRIMARY KEY AUTOINCREMENT, policy_id INTEGER NOT NULL REFERENCES policy(policy_id),
    claim_no TEXT NOT NULL UNIQUE, claim_date TEXT, claim_type TEXT, amount_claimed REAL, amount_settled REAL,
    status TEXT, surveyor TEXT);
CREATE TABLE IF NOT EXISTS premium_payment (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT, policy_id INTEGER NOT NULL REFERENCES policy(policy_id),
    paid_on TEXT, amount REAL, mode TEXT, receipt_no TEXT);
"""

JOINS = ["policy.insurer_name = insurer.name (insurer of a policy)"]

INSURERS = [("ICICI Lombard", "IRDAI-115", "Mumbai", "claims@icicilombard.example"),
            ("HDFC Ergo", "IRDAI-146", "Mumbai", "claims@hdfcergo.example"),
            ("Bajaj Allianz", "IRDAI-113", "Pune", "claims@bajajallianz.example"),
            ("New India Assurance", "IRDAI-190", "Mumbai", "claims@newindia.example"),
            ("United India Ins.", "IRDAI-545", "Chennai", "claims@uiic.example"),
            ("SBI General", "IRDAI-144", "Mumbai", "claims@sbigeneral.example"),
            ("Reliance General", "IRDAI-103", "Mumbai", "claims@reliancegeneral.example"),
            ("Tata AIG", "IRDAI-108", "Mumbai", "claims@tataaig.example")]
# keyed by policy number, so they attach to the right policy in an existing database
HOLDERS = [("ICL-2025-889231", "Rohan Deshmukh", "98220 41120", "Kothrud, Pune", "KYC-PN-55121"),
           ("HDE-2024-112233", "Aarav Sharma", "98110 77231", "Karol Bagh, New Delhi", "KYC-DL-11873"),
           ("BAJ-2026-004411", "Priya Nair", "98450 32019", "Koramangala, Bengaluru", "KYC-KA-30411"),
           ("NIA-2023-556677", "Karthik Raja", "98400 11902", "T. Nagar, Chennai", "KYC-TN-90233"),
           ("UII-2026-778899", "Kiran Patel Transport Co.", "79 2658 1100", "Naroda, Ahmedabad", "KYC-GJ-70021"),
           ("SBI-2025-334455", "Anita Sharma", "94140 22871", "Malviya Nagar, Jaipur", "KYC-RJ-44120"),
           ("ICL-2023-990011", "Vikas Kulkarni", "98230 66014", "Shivaji Nagar, Pune", "KYC-PN-61907"),
           ("REL-2026-221100", "Manpreet Singh", "98140 55302", "Sarabha Nagar, Ludhiana", "KYC-PB-21876"),
           ("TAT-2025-887766", "Debashish Roy", "98300 73519", "Ballygunge, Kolkata", "KYC-WB-66310"),
           ("BAJ-2026-556600", "Thomas Varkey", "98470 18264", "Kadavanthra, Kochi", "KYC-KL-50817"),
           ("HDE-2026-778800", "Neha Verma", "98100 90442", "Sector 54, Gurugram", "KYC-HR-38025")]
CLAIMS = [("ICL-2025-889231", "CLM-ICL-26-0412", "2026-01-18", "Accident", 42000.0, 31500.0, "Settled", "A. Gokhale"),
          ("ICL-2023-990011", "CLM-ICL-26-0977", "2026-08-22", "Theft", 640000.0, 0.0, "Rejected", "A. Gokhale"),
          ("HDE-2024-112233", "CLM-HDE-24-1180", "2024-09-03", "Accident", 88000.0, 70000.0, "Settled", "R. Malhotra"),
          ("NIA-2023-556677", "CLM-NIA-23-2214", "2023-11-27", "Flood damage", 310000.0, 0.0, "Rejected", "S. Iyer"),
          ("UII-2026-778899", "CLM-UII-26-0051", "2026-04-09", "Cargo damage", 125000.0, None, "Under survey", "D. Shah"),
          ("BAJ-2026-556600", "CLM-BAJ-26-0733", "2026-06-14", "Windshield", 18000.0, 18000.0, "Settled", "J. Mathew")]
PAYMENTS = [("ICL-2025-889231", "2025-06-01", 8200.0, "UPI", "RCPT-77120"),
            ("HDE-2024-112233", "2024-01-15", 3400.0, "Card", "RCPT-55102"),
            ("BAJ-2026-004411", "2026-02-10", 1500.0, "UPI", "RCPT-90215"),
            ("NIA-2023-556677", "2023-03-01", 2900.0, "Cash", "RCPT-11873"),
            ("UII-2026-778899", "2026-01-01", 7800.0, "NEFT", "RCPT-31207"),
            ("UII-2026-778899", "2026-07-01", 7800.0, "NEFT", "RCPT-31988"),
            ("SBI-2025-334455", "2025-09-01", 7100.0, "Card", "RCPT-60219"),
            ("ICL-2023-990011", "2023-05-01", 3200.0, "UPI", "RCPT-40871"),
            ("REL-2026-221100", "2026-04-01", 6600.0, "UPI", "RCPT-71230"),
            ("TAT-2025-887766", "2025-05-15", 2100.0, "Cash", "RCPT-82016"),
            ("BAJ-2026-556600", "2026-03-01", 9800.0, "Card", "RCPT-90877"),
            ("HDE-2026-778800", "2026-06-01", 8800.0, "UPI", "RCPT-66218")]


def ensure(conn):
    """Create the related tables and seed them once (attached to existing policies by policy number)."""
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM insurer").fetchone()[0]:
        return
    conn.executemany("INSERT INTO insurer (name, irdai_reg_no, head_office, claims_email) VALUES (?, ?, ?, ?)", INSURERS)
    policy = {number: pid for pid, number in conn.execute("SELECT policy_id, policy_number FROM policy")}
    conn.executemany("INSERT INTO policy_holder (policy_id, holder_name, phone, address, kyc_id) VALUES (?, ?, ?, ?, ?)",
                     [(policy[n], *rest) for n, *rest in HOLDERS if n in policy])
    conn.executemany("INSERT INTO claim (policy_id, claim_no, claim_date, claim_type, amount_claimed, amount_settled, status, "
                     "surveyor) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [(policy[n], *rest) for n, *rest in CLAIMS if n in policy])
    conn.executemany("INSERT INTO premium_payment (policy_id, paid_on, amount, mode, receipt_no) VALUES (?, ?, ?, ?, ?)",
                     [(policy[n], *rest) for n, *rest in PAYMENTS if n in policy])


def discover(conn, sub):
    """Policies that match the subquery, each followed to its insurer, holder, claims and premium payments."""
    ctx = cs.Context("Insurance", sub)
    insurers = {r["name"].lower(): dict(r) for r in conn.execute("SELECT * FROM insurer")}
    holders = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM policy_holder")], "policy_id")
    claims = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM claim")], "policy_id")
    payments = cs.group_by([dict(r) for r in conn.execute("SELECT * FROM premium_payment")], "policy_id")
    for p in cs.main_rows(conn, "policy"):
        plate = rules.normalize_plate(p.get("vehicle_reg_no"))
        ids = cs.raw_ids(p, "policy_id")
        insurer = insurers.get(str(p.get("insurer_name") or "").lower()) or {}
        hs = [h for i in ids for h in holders.get(i, [])]
        cl = [c for i in ids for c in claims.get(i, [])]
        pay = [x for i in ids for x in payments.get(i, [])]
        in_force = insurance_rules.policy_status(p, date.today()) == "VALID"
        found = ctx.facets(places=[h.get("address") for h in hs] + [insurer.get("head_office")],
                           other=[p.get("insurer_name"), p.get("status"), p.get("policy_type"),
                                  "insured" if in_force else "uninsured"]
                           + [c.get("claim_type") for c in cl] + [c.get("status") for c in cl]
                           + [h.get("holder_name") for h in hs] + ["claim" if cl else None])
        score, matched = ctx.match(plate, found, names=[h.get("holder_name") for h in hs],
                                   ids=[p.get("policy_number")] + [c.get("claim_no") for c in cl])
        if not score:
            continue
        path = [f"policy #{p['policy_id']} {p.get('policy_number')}"]
        if insurer:
            path.append(f"insurer #{insurer['insurer_id']} {insurer['name']} ({insurer['irdai_reg_no']})")
        path += [f"policy_holder #{h['holder_id']} {h['holder_name']}" for h in hs]
        path += [f"claim #{c['claim_id']} {c['claim_no']}: {c['claim_type']}, {c['status']}" for c in cl]
        if pay:
            path.append(f"premium_payment x{len(pay)} (last {max(x['paid_on'] for x in pay)})")
        summary = (f"{plate}: {p.get('insurer_name')} {p.get('policy_type') or ''} policy {p.get('policy_number')}, "
                   f"{p.get('start_date')} to {p.get('end_date')}, {p.get('status')}"
                   + (f"; claims: " + ", ".join(f"{c['claim_type']} ({c['status']})" for c in cl) if cl else ""))
        ctx.add("policy", p["policy_id"], plate, score, matched, path, summary, p,
                related={"insurer": insurer, "policy_holder": hs, "claim": cl, "premium_payment": pay},
                keys={"names": [h.get("holder_name") for h in hs], "orgs": [p.get("insurer_name")],
                      "ids": [p.get("policy_number")] + [c.get("claim_no") for c in cl],
                      "words": [p.get("status")] + [c.get("claim_type") for c in cl] + [c.get("status") for c in cl],
                      "places": [h.get("address") for h in hs]})
    return ctx.result()


def count(conn, q):
    """Insurance claims per vehicle in a period (list queries)."""
    if q.get("event") != "claim":
        return None
    bounds, wanted = cs.period_bounds(q), cs.wanted_plates(q)
    plate_of = {}
    for p in cs.main_rows(conn, "policy"):
        for i in cs.raw_ids(p, "policy_id"):
            plate_of[i] = rules.normalize_plate(p.get("vehicle_reg_no"))
    tally = cs.Tally()
    for c in conn.execute("SELECT * FROM claim"):
        plate = plate_of.get(str(c["policy_id"]))
        if plate and (wanted is None or plate in wanted) and cs.in_period(c["claim_date"], bounds):
            tally.add(plate, None, f"{c['claim_type']} ({c['status']})", c["claim_date"])
    return tally.result()


def profile(conn, q):
    """Whether each vehicle is insured today, with the policy that says so (or its latest policy)."""
    wanted, today = cs.wanted_plates(q), cs.today_of(q)
    policies = {}
    for p in cs.main_rows(conn, "policy"):
        plate = rules.normalize_plate(p.get("vehicle_reg_no"))
        if wanted is None or plate in wanted:
            policies.setdefault(plate, []).append(p)
    out = {}
    for plate, rows in policies.items():
        status, policy = insurance_rules.evaluate_insurance(rows, today)
        out[plate] = {"insured": status == "VALID", "policy": policy.get("policy_number"),
                      "insurer": policy.get("insurer_name"), "valid_to": policy.get("end_date"),
                      "status": policy.get("status")}
    return out
