import sqlite3

DB_PATH = "insurance.db"

vehicle_reg_no = input(
    "Enter vehicle registration number to delete a policy from: "
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
        f"{p['insurer_name']} | {p['status']}"
    )

policy_id = input("\nEnter Policy ID to delete: ").strip()

cur.execute("""
    SELECT * FROM policy
    WHERE policy_id = ? AND vehicle_reg_no = ?
""", (policy_id, vehicle_reg_no))

policy = cur.fetchone()

if not policy:
    print("\nInvalid Policy ID.")
    conn.close()
    exit()

print("\nPolicy selected:")
print(f"  Policy ID : {policy['policy_id']}")
print(f"  Vehicle   : {policy['vehicle_reg_no']}")
print(f"  Policy No.: {policy['policy_number']}")
print(f"  Insurer   : {policy['insurer_name']}")
print(f"  Status    : {policy['status']}")

confirm = input(
    "\nAre you sure you want to delete this policy? (yes/no): "
).strip().lower()

if confirm not in ("yes", "y"):
    print("\nDeletion cancelled.")
    conn.close()
    exit()

cur.execute("""
    DELETE FROM policy
    WHERE policy_id = ? AND vehicle_reg_no = ?
""", (policy_id, vehicle_reg_no))

conn.commit()
print("\nPolicy deleted successfully!")
conn.close()
