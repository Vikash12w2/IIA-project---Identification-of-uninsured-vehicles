import sqlite3, os
DB_PATH=os.path.join(os.path.dirname(__file__),"rto.db")
def main():
    plate=input("Registration number: ").strip().upper()
    if not plate: print("Error: Registration number is required."); return
    conn=sqlite3.connect(DB_PATH);conn.row_factory=sqlite3.Row;cur=conn.cursor()
    rows=cur.execute("SELECT * FROM registration WHERE reg_number=? ORDER BY reg_id",(plate,)).fetchall()
    if not rows: print(f"No registration found for {plate}.");conn.close();return
    for r in rows: print(f"ID: {r['reg_id']} | Owner: {r['owner_name']} | {r['make']} {r['model']} | Reg valid: {r['reg_valid_upto']} | Fitness: {r['fitness_valid_upto']}")
    try: rid=int(input("Enter Registration ID to edit: ").strip())
    except ValueError: print("Error: Registration ID must be a number.");conn.close();return
    r=cur.execute("SELECT * FROM registration WHERE reg_id=? AND reg_number=?",(rid,plate)).fetchone()
    if not r: print("Error: Invalid Registration ID for this vehicle.");conn.close();return
    fields=["owner_name","owner_address","chassis_number","engine_number","make","model","reg_date","reg_valid_upto","fitness_valid_upto"]
    data={}
    for f in fields: data[f]=input(f"{f.replace('_',' ').title()} [{r[f] or ''}]: ").strip() or (r[f] or "")
    for f in ["owner_name","chassis_number","engine_number"]:
        if not data[f]: print(f"Error: {f.replace('_',' ').title()} cannot be empty.");conn.close();return
    cur.execute("""UPDATE registration SET owner_name=?,owner_address=?,chassis_number=?,engine_number=?,make=?,model=?,reg_date=?,reg_valid_upto=?,fitness_valid_upto=? WHERE reg_id=? AND reg_number=?""",tuple(data[f] for f in fields)+(rid,plate))
    conn.commit();conn.close();print("Registration updated successfully.")
if __name__=="__main__": main()
