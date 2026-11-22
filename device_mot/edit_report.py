import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "mot_report.db")

plate = input("Plate Number: ").strip().upper()
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT * FROM mot_report WHERE plate_no=? ORDER BY report_id", (plate,)).fetchall()
if not rows:
    conn.close()
    print("No report found for", plate)
    raise SystemExit(1)

for r in rows:
    print(r["report_id"], r["generated_on"], r["owner_name"], r["risk_flags"])

rid = input("Report ID to edit: ").strip()
r = conn.execute("SELECT * FROM mot_report WHERE report_id=? AND plate_no=?", (rid, plate)).fetchone()
if not r:
    conn.close()
    print("Invalid Report ID")
    raise SystemExit(1)

names = ["owner_name", "make_model", "insurance_status", "theft_status", "shredded", "risk_flags", "last_seen_on_road", "recommended_action"]
data = {}
for name in names:
    value = input(f"{name.replace('_', ' ').title()} [{r[name] or ''}]: ").strip()
    data[name] = value if value else (r[name] or "")

for x in ["insurance_status", "risk_flags", "recommended_action"]:
    if not data[x]:
        conn.close()
        print(x.replace('_', ' ').title(), "cannot be empty")
        raise SystemExit(1)

conn.execute("""UPDATE mot_report SET owner_name=?, make_model=?, insurance_status=?, theft_status=?, shredded=?, risk_flags=?, last_seen_on_road=?, recommended_action=? WHERE report_id=? AND plate_no=?""", tuple(data[n] for n in names) + (rid, plate))
conn.commit()
conn.close()
print("Report updated successfully.")
