import sqlite3

DB_PATH = "anpr.db"

plate_number = input("Enter vehicle plate number to edit: ").strip().upper()

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# Find existing captures
cur.execute("""
    SELECT *
    FROM vehicle_capture
    WHERE plate_number = ?
    ORDER BY capture_id
""", (plate_number,))

captures = cur.fetchall()

if not captures:
    print(f"\nNo captures found for {plate_number}.")
    conn.close()
    exit()

print(f"\nFound {len(captures)} capture(s):")

for capture in captures:
    print(
        f"  ID: {capture['capture_id']} | "
        f"{capture['capture_ts']} | "
        f"{capture['location']} | "
        f"{capture['vehicle_color']} | "
        f"{capture['vehicle_type']} | "
        f"{capture['make_model_seen']} | "
        f"{capture['camera_id']}"
    )

# Select which capture to edit
capture_id = input("\nEnter capture ID to edit: ").strip()

cur.execute("""
    SELECT *
    FROM vehicle_capture
    WHERE capture_id = ? AND plate_number = ?
""", (capture_id, plate_number))

capture = cur.fetchone()

if not capture:
    print("\nInvalid capture ID.")
    conn.close()
    exit()

print("\nPress ENTER to keep the existing value.")

# Editable fields
location = input(
    f"Location [{capture['location']}]: "
).strip() or capture["location"]

vehicle_color = input(
    f"Vehicle color [{capture['vehicle_color']}]: "
).strip() or capture["vehicle_color"]

camera_id = input(
    f"Camera ID [{capture['camera_id']}]: "
).strip() or capture["camera_id"]

# Update only the selected capture
cur.execute("""
    UPDATE vehicle_capture
    SET
        location = ?,
        vehicle_color = ?,
        camera_id = ?
    WHERE capture_id = ?
""", (
    location,
    vehicle_color,
    camera_id,
    capture_id
))

conn.commit()

print("\nCapture updated successfully!")
print(f"Capture ID : {capture_id}")
print(f"Plate      : {plate_number}")
print(f"Location   : {location}")
print(f"Color      : {vehicle_color}")
print(f"Type       : {capture['vehicle_type']}")
print(f"Make/Model : {capture['make_model_seen']}")
print(f"Camera     : {camera_id}")

conn.close()