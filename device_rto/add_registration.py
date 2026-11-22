import sqlite3, os
DB_PATH=os.path.join(os.path.dirname(__file__),"rto.db")
def main():
    fields=["reg_number","owner_name","owner_address","chassis_number","engine_number","make","model","reg_date","reg_valid_upto","fitness_valid_upto"]
    data={}
    for f in fields:
        data[f]=input(f"{f.replace('_',' ').title()}: ").strip()
    data["reg_number"]=data["reg_number"].upper()
    for f in ["reg_number","owner_name","chassis_number","engine_number"]:
        if not data[f]:
            print(f"Error: {f.replace('_',' ').title()} is required."); return
    conn=sqlite3.connect(DB_PATH);cur=conn.cursor()
    cur.execute("""INSERT INTO registration(reg_number,owner_name,owner_address,chassis_number,engine_number,make,model,reg_date,reg_valid_upto,fitness_valid_upto) VALUES(?,?,?,?,?,?,?,?,?,?)""",tuple(data[f] for f in fields))
    conn.commit();print(f"Registration added successfully. Registration ID: {cur.lastrowid}");conn.close()
if __name__=="__main__": main()
