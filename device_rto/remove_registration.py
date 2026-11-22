import sqlite3, os
DB_PATH=os.path.join(os.path.dirname(__file__),"rto.db")
def main():
    plate=input("Registration number: ").strip().upper()
    if not plate: print("Error: Registration number is required.");return
    conn=sqlite3.connect(DB_PATH);conn.row_factory=sqlite3.Row;cur=conn.cursor()
    rows=cur.execute("SELECT * FROM registration WHERE reg_number=? ORDER BY reg_id",(plate,)).fetchall()
    if not rows: print(f"No registration found for {plate}.");conn.close();return
    for r in rows: print(f"ID: {r['reg_id']} | Owner: {r['owner_name']} | {r['make']} {r['model']}")
    try: rid=int(input("Enter Registration ID to delete: ").strip())
    except ValueError: print("Error: Registration ID must be a number.");conn.close();return
    r=cur.execute("SELECT * FROM registration WHERE reg_id=? AND reg_number=?",(rid,plate)).fetchone()
    if not r: print("Error: Invalid Registration ID for this vehicle.");conn.close();return
    print(f"Selected: {r['reg_number']} | {r['owner_name']} | {r['make']} {r['model']}")
    if input("Delete this registration? (yes/no): ").strip().lower()!="yes": print("Deletion cancelled.");conn.close();return
    cur.execute("DELETE FROM registration WHERE reg_id=? AND reg_number=?",(rid,plate));conn.commit();conn.close();print("Registration deleted successfully.")
if __name__=="__main__": main()
