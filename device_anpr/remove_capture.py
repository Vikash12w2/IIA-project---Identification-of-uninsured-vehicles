import sqlite3

DB_PATH = "anpr.db"

plate_number = input("Enter vehicle plate number to remove: ").strip().upper()

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

# Check whether the vehicle exists
cur.execute(
    "SELECT COUNT(*) FROM vehicle_capture WHERE plate_number = ?",
    (plate_number,)
)

count = cur.fetchone()[0]

if count == 0:
    print(f"\nNo captures found for {plate_number}.")
else:
    print(f"\nFound {count} capture(s) for {plate_number}.")

    confirm = input(
        f"Are you sure you want to remove ALL captures for {plate_number}? (yes/no): "
    ).strip().lower()

    if confirm == "yes":
        cur.execute(
            "DELETE FROM vehicle_capture WHERE plate_number = ?",
            (plate_number,)
        )

        conn.commit()

        print(f"\nSuccessfully removed {cur.rowcount} capture(s) for {plate_number}.")
    else:
        print("\nDeletion cancelled.")

conn.close()