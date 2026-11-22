import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "mot_report.db")

plate = input("Plate Number: ").strip().upper()
conn = sqlite3.connect(DB_PATH)
rows = conn.execute("SELECT report_id, generated_on, risk_flags FROM mot_report WHERE plate_no=? ORDER BY report_id", (plate,)).fetchall()
if not rows:
    conn.close()
    print("No report found for", plate)
    raise SystemExit(1)

for r in rows:
    print(f"ID: {r[0]} | Generated: {r[1]} | Risk Flags: {r[2]}")

rid = input("Report ID to delete: ").strip()
exists = conn.execute("SELECT 1 FROM mot_report WHERE report_id=? AND plate_no=?", (rid, plate)).fetchone()
if not exists:
    conn.close()
    print("Invalid Report ID")
    raise SystemExit(1)

confirm = input("Delete this report? (y/n): ").strip().lower()
if confirm == "y":
    conn.execute("DELETE FROM mot_report WHERE report_id=? AND plate_no=?", (rid, plate))
    conn.commit()
    print("Report deleted successfully.")
else:
    print("Deletion cancelled.")
conn.close()
