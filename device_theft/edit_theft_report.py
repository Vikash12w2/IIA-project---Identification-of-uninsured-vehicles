import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "theft_shred.db")


def main():
    vehicle_no = input("Vehicle number: ").strip().upper()

    if not vehicle_no:
        print("Error: Vehicle number is required.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM theft_report
        WHERE vehicle_no = ?
        ORDER BY theft_id
    """, (vehicle_no,))

    reports = cur.fetchall()

    if not reports:
        print(f"No theft reports found for {vehicle_no}.")
        conn.close()
        return

    print("\nTheft reports:")
    print("-" * 90)

    for r in reports:
        print(
            f"ID: {r['theft_id']} | "
            f"Date: {r['report_date']} | "
            f"Station: {r['police_station']} | "
            f"FIR: {r['fir_number']} | "
            f"Status: {r['status']}"
        )

    try:
        theft_id = int(input("\nEnter Theft ID to edit: ").strip())
    except ValueError:
        print("Error: Theft ID must be a number.")
        conn.close()
        return

    cur.execute("""
        SELECT *
        FROM theft_report
        WHERE theft_id = ?
        AND vehicle_no = ?
    """, (theft_id, vehicle_no))

    report = cur.fetchone()

    if not report:
        print("Error: Invalid Theft ID for this vehicle.")
        conn.close()
        return

    print("\nPress Enter to keep the existing value.")

    report_date = input(
        f"Report date [{report['report_date']}]: "
    ).strip() or report["report_date"]

    police_station = input(
        f"Police station [{report['police_station'] or ''}]: "
    ).strip() or report["police_station"]

    fir_number = input(
        f"FIR number [{report['fir_number'] or ''}]: "
    ).strip() or report["fir_number"]

    status = input(
        f"Status [{report['status']}]: "
    ).strip() or report["status"]

    if status not in ("Reported", "Recovered"):
        print("Error: Status must be Reported or Recovered.")
        conn.close()
        return

    cur.execute("""
        UPDATE theft_report
        SET
            report_date = ?,
            police_station = ?,
            fir_number = ?,
            status = ?
        WHERE theft_id = ?
        AND vehicle_no = ?
    """, (
        report_date,
        police_station,
        fir_number,
        status,
        theft_id,
        vehicle_no
    ))

    conn.commit()
    conn.close()

    print("Theft report updated successfully.")


if __name__ == "__main__":
    main()
