import sqlite3

DB_PATH = "insurance.db"

vehicle_reg_no = input("Enter vehicle registration number: ").strip().upper()
insurer_name = input("Enter insurer name: ").strip()
policy_number = input("Enter policy number: ").strip()
policy_type = input("Enter policy type (Comprehensive/Third-Party): ").strip()
start_date = input("Enter start date (YYYY-MM-DD): ").strip()
end_date = input("Enter end date (YYYY-MM-DD): ").strip()
premium_input = input("Enter premium amount: ").strip()
status = input("Enter status (Active/Lapsed/Cancelled): ").strip()

if not vehicle_reg_no:
    print("\nInvalid input: registration number cannot be empty.")
    exit()
if not insurer_name:
    print("\nInvalid input: insurer name cannot be empty.")
    exit()
if not policy_number:
    print("\nInvalid input: policy number cannot be empty.")
    exit()
if not start_date or not end_date:
    print("\nInvalid input: start date and end date are required.")
    exit()

try:
    premium_amount = float(premium_input) if premium_input else None
except ValueError:
    print("\nInvalid input: premium amount must be a number.")
    exit()

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()

cur.execute("""
    INSERT INTO policy
    (vehicle_reg_no, insurer_name, policy_number, policy_type,
     start_date, end_date, premium_amount, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
""", (vehicle_reg_no, insurer_name, policy_number, policy_type,
      start_date, end_date, premium_amount, status))

conn.commit()
print("\nPolicy added successfully!")
print(f"Policy ID: {cur.lastrowid}")
conn.close()
