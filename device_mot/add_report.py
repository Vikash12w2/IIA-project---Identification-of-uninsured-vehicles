import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "mot_report.db")

plate = input("Plate Number: ").strip().upper()
owner_name = input("Owner Name: ").strip()
make_model = input("Make / Model: ").strip()
insurance_status = input("Insurance Status: ").strip()
theft_status = input("Theft Status: ").strip()
shredded = input("Shredded: ").strip()
risk_flags = input("Risk Flags: ").strip()
last_seen = input("Last Seen On Road (YYYY-MM-DD HH:MM:SS, blank if unknown): ").strip()
recommended_action = input("Recommended Action: ").strip()

generated_on = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

required = {
    "plate_no": plate,
    "insurance_status": insurance_status,
    "risk_flags": risk_flags,
    "recommended_action": recommended_action,
}
missing = [x for x, v in required.items() if not v]
if missing:
    print("Missing required fields:", ", ".join(missing))
    raise SystemExit(1)

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute("""INSERT INTO mot_report
(plate_no, generated_on, owner_name, make_model, insurance_status, theft_status, shredded, risk_flags, last_seen_on_road, recommended_action)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
(plate, generated_on, owner_name, make_model, insurance_status, theft_status, shredded, risk_flags, last_seen, recommended_action))
conn.commit()
print("Report added. Report ID:", cur.lastrowid)
print("Generated On:", generated_on)
conn.close()
