import sqlite3
from datetime import datetime

DB_PATH = "anpr.db"

plate_number = input("Enter vehicle plate number: ").strip().upper()

location = input("Enter capture location: ").strip()
vehicle_color = input("Enter vehicle color: ").strip()
vehicle_type = input("Enter vehicle type: ").strip()
make_model = input("Enter make/model: ").strip()
camera_id = input("Enter camera ID: ").strip()

capture_ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("""
    INSERT INTO vehicle_capture
    (
        plate_number,
        capture_ts,
        location,
        vehicle_color,
        vehicle_type,
        make_model_seen,
        camera_id
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
""", (
    plate_number,
    capture_ts,
    location,
    vehicle_color,
    vehicle_type,
    make_model,
    camera_id
))

conn.commit()
conn.close()

print("\nCapture added successfully!")
print(f"Plate      : {plate_number}")
print(f"Time       : {capture_ts}")
print(f"Location   : {location}")
print(f"Vehicle    : {make_model}")
print(f"Camera     : {camera_id}")