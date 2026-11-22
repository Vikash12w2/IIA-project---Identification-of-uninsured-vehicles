import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "theft_shred.db")


def main():
    vehicle_no = input("Vehicle number: ").strip().upper()
    shred_date = input("Shred date (YYYY-MM-DD): ").strip()
    scrap_center = input("Scrap center: ").strip()
    certificate_no = input("Certificate number: ").strip()

    if not vehicle_no:
        print("Error: Vehicle number is required.")
        return

    if not shred_date:
        print("Error: Shred date is required.")
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO shredding_record
        (vehicle_no, shred_date, scrap_center, certificate_no)
        VALUES (?, ?, ?, ?)
    """, (
        vehicle_no,
        shred_date,
        scrap_center,
        certificate_no
    ))

    conn.commit()
    print(
        f"Shredding record added successfully. "
        f"Shred ID: {cur.lastrowid}"
    )
    conn.close()


if __name__ == "__main__":
    main()
