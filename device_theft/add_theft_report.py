import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "theft_shred.db")


def main():
    vehicle_no = input("Vehicle number: ").strip().upper()
    report_date = input("Report date (YYYY-MM-DD): ").strip()
    police_station = input("Police station: ").strip()
    fir_number = input("FIR number: ").strip()
    status = input("Status (Reported/Recovered): ").strip()

    if not vehicle_no:
        print("Error: Vehicle number is required.")
        return

    if not report_date:
        print("Error: Report date is required.")
        return

    if not status:
        print("Error: Status is required.")
        return

    if status not in ("Reported", "Recovered"):
        print("Error: Status must be Reported or Recovered.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO theft_report
        (vehicle_no, report_date, police_station, fir_number, status)
        VALUES (?, ?, ?, ?, ?)
    """, (
        vehicle_no,
        report_date,
        police_station,
        fir_number,
        status
    ))

    conn.commit()
    print(f"Theft report added successfully. Theft ID: {cur.lastrowid}")
    conn.close()


if __name__ == "__main__":
    main()
