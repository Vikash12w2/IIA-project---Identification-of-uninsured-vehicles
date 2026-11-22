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
        theft_id = int(input("\nEnter Theft ID to delete: ").strip())
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

    print("\nSelected report:")
    print(f"Vehicle: {report['vehicle_no']}")
    print(f"Date: {report['report_date']}")
    print(f"FIR: {report['fir_number']}")
    print(f"Status: {report['status']}")

    confirm = input("\nDelete this report? (yes/no): ").strip().lower()

    if confirm != "yes":
        print("Deletion cancelled.")
        conn.close()
        return

    cur.execute("""
        DELETE FROM theft_report
        WHERE theft_id = ?
        AND vehicle_no = ?
    """, (theft_id, vehicle_no))

    conn.commit()
    conn.close()

    print("Theft report deleted successfully.")


if __name__ == "__main__":
    main()
