import sqlite3

DB_PATH = "insurance.db"

vehicle_reg_no = input(
    "Enter vehicle registration number to edit: "
).strip().upper()

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("""
    SELECT * FROM policy
    WHERE vehicle_reg_no = ?
    ORDER BY policy_id
""", (vehicle_reg_no,))

policies = cur.fetchall()

if not policies:
    print(f"\nNo policies found for {vehicle_reg_no}.")
    conn.close()
    exit()

print(f"\nFound {len(policies)} policy/policies:\n")
for p in policies:
    print(
        f"  ID: {p['policy_id']} | {p['policy_number']} | "
        f"{p['insurer_name']} | {p['policy_type']} | "
        f"{p['start_date']} to {p['end_date']} | "
        f"₹{p['premium_amount'] if p['premium_amount'] is not None else 'N/A'} | "
        f"{p['status']}"
    )

policy_id = input("\nEnter Policy ID to edit: ").strip()

cur.execute("""
    SELECT * FROM policy
    WHERE policy_id = ? AND vehicle_reg_no = ?
""", (policy_id, vehicle_reg_no))

policy = cur.fetchone()

if not policy:
    print("\nInvalid Policy ID.")
    conn.close()
    exit()

print("\nPress ENTER to keep the existing value.\n")

insurer_name = input(
    f"Insurer Name [{policy['insurer_name']}]: "
).strip() or policy["insurer_name"]

policy_number = input(
    f"Policy Number [{policy['policy_number']}]: "
).strip() or policy["policy_number"]

policy_type = input(
    f"Policy Type [{policy['policy_type']}]: "
).strip() or policy["policy_type"]

start_date = input(
    f"Start Date [{policy['start_date']}]: "
).strip() or policy["start_date"]

end_date = input(
    f"End Date [{policy['end_date']}]: "
).strip() or policy["end_date"]

premium_input = input(
    f"Premium Amount [{policy['premium_amount']}]: "
).strip()

try:
    premium_amount = float(premium_input) if premium_input else policy["premium_amount"]
except ValueError:
    print("\nInvalid premium amount. Must be a number.")
    conn.close()
    exit()

status = input(
    f"Status [{policy['status']}]: "
).strip() or policy["status"]

cur.execute("""
    UPDATE policy
    SET insurer_name = ?, policy_number = ?, policy_type = ?,
        start_date = ?, end_date = ?, premium_amount = ?, status = ?
    WHERE policy_id = ? AND vehicle_reg_no = ?
""", (insurer_name, policy_number, policy_type, start_date, end_date,
      premium_amount, status, policy_id, vehicle_reg_no))

conn.commit()
print("\nPolicy updated successfully!")
conn.close()
