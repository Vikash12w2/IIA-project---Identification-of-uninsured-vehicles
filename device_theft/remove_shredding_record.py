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
        FROM shredding_record
        WHERE vehicle_no = ?
        ORDER BY shred_id
    """, (vehicle_no,))

    records = cur.fetchall()

    if not records:
        print(f"No shredding records found for {vehicle_no}.")
        conn.close()
        return

    print("\nShredding records:")
    print("-" * 90)

    for r in records:
        print(
            f"ID: {r['shred_id']} | "
            f"Date: {r['shred_date']} | "
            f"Scrap center: {r['scrap_center']} | "
            f"Certificate: {r['certificate_no']}"
        )

    try:
        shred_id = int(input("\nEnter Shred ID to delete: ").strip())
    except ValueError:
        print("Error: Shred ID must be a number.")
        conn.close()
        return

    cur.execute("""
        SELECT *
        FROM shredding_record
        WHERE shred_id = ?
        AND vehicle_no = ?
    """, (shred_id, vehicle_no))

    record = cur.fetchone()

    if not record:
        print("Error: Invalid Shred ID for this vehicle.")
        conn.close()
        return

    print("\nSelected record:")
    print(f"Vehicle: {record['vehicle_no']}")
    print(f"Date: {record['shred_date']}")
    print(f"Scrap center: {record['scrap_center']}")
    print(f"Certificate: {record['certificate_no']}")

    confirm = input(
        "\nDelete this record? (yes/no): "
    ).strip().lower()

    if confirm != "yes":
        print("Deletion cancelled.")
        conn.close()
        return

    cur.execute("""
        DELETE FROM shredding_record
        WHERE shred_id = ?
        AND vehicle_no = ?
    """, (shred_id, vehicle_no))

    conn.commit()
    conn.close()

    print("Shredding record deleted successfully.")


if __name__ == "__main__":
    main()
