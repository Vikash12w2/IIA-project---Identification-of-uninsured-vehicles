from datetime import date
import sqlite3, os, argparse, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import plate_entry_client as entry
import cleansing
import context_search
import rto_context
import table_browser
import peers
from flask import Flask, jsonify, request, redirect, url_for, render_template_string

DB_PATH = os.path.join(os.path.dirname(__file__), "rto.db")
app = Flask(__name__)


# This database's plate dictionary (plate_entry_client.py), in its own file next to the database.
desk = entry.PlateDesk(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plate_dictionary.db"), [
    entry.Source("RTO", lambda: DB_PATH, "registration", "reg_id", "reg_number", "registration",
                 context=lambda r: {"make_model": f'{r.get("make") or ""} {r.get("model") or ""}'}),
])
desk.register(app)

# Automatic data cleansing (cleansing.py): registration_clean next to the raw table, using every device's data.
cleanser = cleansing.Cleanser("RTO", lambda: DB_PATH, desk.book, peers.PEERS)
cleanser.register(app)

def _init_main():
    # Check for the table, not the file: an empty rto.db must still get its table and seed rows.
    conn = sqlite3.connect(DB_PATH)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='registration'").fetchone():
        conn.close(); return
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE registration (
        reg_id INTEGER PRIMARY KEY AUTOINCREMENT,
        reg_number TEXT NOT NULL,
        owner_name TEXT NOT NULL,
        owner_address TEXT,
        chassis_number TEXT NOT NULL,
        engine_number TEXT NOT NULL,
        make TEXT,
        model TEXT,
        reg_date TEXT,
        reg_valid_upto TEXT,
        fitness_valid_upto TEXT
    );
    """)
    rows = [
        ("MH12AB1234","Rohan Deshmukh","Pune, MH","MA3ER123ABC0001","ENGX0001","Maruti Suzuki","Swift","2018-06-15","2033-06-14","2027-06-14"),
        ("DL8CAF5555","Aarav Sharma","New Delhi","MALC1234DEF0002","ENGX0002","Hyundai","Creta","2017-03-10","2032-03-09","2025-03-09"),
        ("KA05MZ9090","Priya Nair","Bengaluru, KA","ME4KC123GHI0003","ENGX0003","Royal Enfield","Classic 350","2020-11-05","2035-11-04","2028-11-04"),
        ("UP16CD4321","Suresh Yadav","Lucknow, UP","MAKHC123JKL0004","ENGX0004","Honda","City","2019-07-22","2034-07-21","2026-07-21"),
        ("TN09EF7777","Karthik Raja","Chennai, TN","MAT600123MNO0005","ENGX0005","Tata","Nexon","2016-01-30","2031-01-29","2024-01-29"),
        ("GJ01XY0001","Kiran Patel Transport Co.","Ahmedabad, GJ","MAT460123PQR0006","ENGX0006","Tata","LPT 1613","2015-09-18","2030-09-17","2025-09-17"),
        ("RJ14PQ6543","Anita Sharma","Jaipur, RJ","MA1YE123STU0007","ENGX0007","Mahindra","XUV300","2021-04-12","2036-04-11","2029-04-11"),
        ("MH14ZZ0000","Vikas Kulkarni","Pune, MH","MA3FHEB1S00008","ENGX0008","Skoda","Rapid","2014-02-20","2029-02-19","2024-02-19"),
        ("PB10AA2222","Manpreet Singh","Ludhiana, PB","MALFB123VWX0009","ENGX0009","Hyundai","i20","2022-08-08","2037-08-07","2030-08-07"),
        ("WB20BB3333","Debashish Roy","Kolkata, WB","MAKGB123YZA0010","ENGX0010","Honda","Amaze","2020-12-01","2035-11-30","2027-11-30"),
        ("KL07LM8899","Thomas Varkey","Kochi, KL","MHFXX123BCD0011","ENGX0011","Toyota","Innova Crysta","2019-05-05","2034-05-04","2026-05-04"),
        ("HR26NN4545","Neha Verma","Gurugram, HR","MYKKJ123EFG0012","ENGX0012","Kia","Seltos","2023-01-20","2038-01-19","2031-01-19"),
    ]
    cur.executemany("""INSERT INTO registration
        (reg_number,owner_name,owner_address,chassis_number,engine_number,make,model,reg_date,reg_valid_upto,fitness_valid_upto)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit(); conn.close()

def get_rows(reg_number=None):
    conn=sqlite3.connect(DB_PATH); conn.row_factory=sqlite3.Row; cur=conn.cursor()
    if reg_number:
        cur.execute("SELECT * FROM registration WHERE reg_number=? ORDER BY reg_id",(reg_number,))
    else:
        cur.execute("SELECT * FROM registration ORDER BY reg_id")
    rows=cur.fetchall(); conn.close(); return rows

STYLE = """<style>
*{box-sizing:border-box}body{margin:0;padding:30px;font-family:Arial;background:#f5f7fa;color:#17202a}
.container{max-width:1600px;margin:auto}.card{background:white;border-radius:14px;padding:30px;box-shadow:0 4px 18px rgba(0,0,0,.08)}
h1{margin-top:0;font-size:32px}h2{margin-top:35px}.subtitle{color:#6b7280;margin-bottom:25px}
.stats{display:grid;grid-template-columns:repeat(3,1fr);gap:20px;margin:25px 0}
.stat-card{background:#f1f3f5;border-radius:14px;padding:22px 30px}.stat-title{color:#52606d}.stat-value{font-size:34px;font-weight:bold;margin-top:8px}
.buttons{display:flex;gap:12px;margin:25px 0;flex-wrap:wrap}.btn{display:inline-block;border:0;border-radius:7px;padding:11px 18px;text-decoration:none;cursor:pointer;font-size:15px;font-weight:600}
.btn-primary{background:#2563eb;color:white}.btn-primary:hover{background:#1d4ed8}.btn-danger{background:#dc2626;color:white}.btn-danger:hover{background:#b91c1c}
.btn-secondary{background:#6b7280;color:white}.btn-secondary:hover{background:#4b5563}
.table-wrapper{width:100%;overflow-x:auto}table{width:100%;border-collapse:collapse;background:white}th{background:#222;color:white;padding:14px;text-align:left;white-space:nowrap}td{padding:13px;border-bottom:1px solid #e5e7eb;white-space:nowrap}tr:hover td{background:#f8fafc}
.form-card{max-width:750px}.form-group{margin-bottom:18px}label{display:block;font-weight:bold;margin-bottom:8px}input{width:100%;padding:12px 14px;border:1px solid #cbd5e1;border-radius:7px;font-size:15px;background:white}
input:focus{outline:none;border-color:#2563eb}.form-actions{display:flex;gap:10px;margin-top:25px}.info-box{background:#f8fafc;border:1px solid #dbe3ec;border-radius:10px;padding:17px;margin:25px 0;color:#334155}
.popup-overlay{position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:9999}
.popup{background:white;width:min(450px,90%);border-radius:14px;padding:28px;box-shadow:0 10px 35px rgba(0,0,0,.25);text-align:center}.popup h3{margin-top:0;font-size:22px}.popup p{color:#4b5563;line-height:1.5}.popup-error{color:#b91c1c}.popup-close{margin-top:15px;padding:9px 20px;border:0;border-radius:7px;background:#2563eb;color:white;cursor:pointer}
@media(max-width:900px){.stats{grid-template-columns:1fr}}body{overflow-x:hidden}
</style>"""

POPUP = """<script>
function showError(message){const o=document.createElement('div');o.className='popup-overlay';o.innerHTML=`<div class="popup" onclick="event.stopPropagation()"><h3 class="popup-error">Invalid Input</h3><p>${message}</p><button class="popup-close" onclick="closePopup()">OK</button></div>`;o.onclick=closePopup;document.body.appendChild(o)}
function closePopup(){const p=document.querySelector('.popup-overlay');if(p)p.remove()}
</script>"""

HOME = """<!DOCTYPE html><html><head><title>RTO Database</title>{{style|safe}}</head><body><div class="container"><div class="card">
<h1>RTO Database</h1>{{ clean_html|safe }}<div class="subtitle">Device 3 — Vehicle Registration Records</div>
<div class="stats"><div class="stat-card"><div class="stat-title">Total Registrations</div><div class="stat-value">{{total}}</div></div>
<div class="stat-card"><div class="stat-title">Unique Registration Numbers</div><div class="stat-value">{{unique}}</div></div>
<div class="stat-card"><div class="stat-title">Valid Fitness Records</div><div class="stat-value">{{fitness}}</div></div></div>
<div class="buttons"><a href="/add" class="btn btn-primary">+ Add Registration</a><a href="/edit" class="btn btn-secondary">Edit Registration</a><a href="/delete" class="btn btn-danger">Delete Registration</a><a href="/search" class="btn btn-secondary">Search</a><a href="/cleaned" class="btn btn-secondary">Cleaned Data</a><a href="/cleansing-report" class="btn btn-secondary">Cleansing Report</a><a href="/plates/corrected" class="btn btn-secondary">Corrected Number Plates</a><a href="/plates/dictionary" class="btn btn-secondary">Plate Dictionary</a><a href="/schema" class="btn btn-secondary">Schema &amp; Documents</a></div>
""" + table_browser.CONTROLS + """
{% if table_view.name == "registration" %}
    <h2>All Vehicle Registrations</h2><div class="table-wrapper"><table><tr>
<th>Reg ID</th><th>Registration Number</th><th>Owner</th><th>Address</th><th>Chassis</th><th>Engine</th><th>Make</th><th>Model</th><th>Reg Date</th><th>Reg Valid Upto</th><th>Fitness Valid Upto</th></tr>
{%for r in rows%}<tr><td><b>{{r["reg_id"]}}</b></td><td><b>{{r["reg_number"]}}</b></td><td>{{r["owner_name"]}}</td><td>{{r["owner_address"]}}</td><td>{{r["chassis_number"]}}</td><td>{{r["engine_number"]}}</td><td>{{r["make"]}}</td><td>{{r["model"]}}</td><td>{{r["reg_date"]}}</td><td>{{r["reg_valid_upto"]}}</td><td>{{r["fitness_valid_upto"]}}</td></tr>{%endfor%}</table></div>
{% else %}
""" + table_browser.RECORDS + """
{% endif %}
</div></div>{{popup|safe}}{%if error%}<script>window.onload=function(){showError({{error|tojson}})}</script>{%endif%}</body></html>"""

FORM = """<!DOCTYPE html><html><head><title>{{title}}</title>{{style|safe}}</head><body><div class="container"><div class="card form-card">
<h1>{{title}}</h1><div class="subtitle">{{subtitle}}</div><form method="POST" action="{{action}}">
{%for f in fields%}<div class="form-group"><label>{{f[1]}}</label><input type="{{f[2]}}" name="{{f[0]}}" value="{{f[3]}}" {%if f[4]%}required{%endif%} {%if f[5]%}readonly{%endif%}></div>{%endfor%}
<div class="form-actions"><button class="btn btn-primary" type="submit">{{button}}</button><a href="{{back}}" class="btn btn-secondary">← Back</a></div></form>
</div></div>{{popup|safe}}{%if error%}<script>window.onload=function(){showError({{error|tojson}})}</script>{%endif%}</body></html>"""

SELECT = """<!DOCTYPE html><html><head><title>{{title}}</title>{{style|safe}}</head><body><div class="container"><div class="card">
<h1>{{title}}</h1><div class="subtitle">{{subtitle}}</div><form method="POST"><div class="form-group"><label>Registration Number</label><input name="reg_number" value="{{plate}}" placeholder="e.g. MH12AB1234" required></div><button class="btn btn-primary">Find Registrations</button></form>
{%if searched and rows%}<h2>Registrations for {{plate}}</h2><div class="table-wrapper"><table><tr><th>Reg ID</th><th>Owner</th><th>Make</th><th>Model</th><th>Reg Valid Upto</th><th>Fitness Valid Upto</th></tr>{%for r in rows%}<tr><td><b>{{r["reg_id"]}}</b></td><td>{{r["owner_name"]}}</td><td>{{r["make"]}}</td><td>{{r["model"]}}</td><td>{{r["reg_valid_upto"]}}</td><td>{{r["fitness_valid_upto"]}}</td></tr>{%endfor%}</table></div>
<div class="info-box">Enter the <b>Registration ID</b> from the table to select the record.</div><form method="POST" action="{{select_url}}" class="form-card"><input type="hidden" name="reg_number" value="{{plate}}"><div class="form-group"><label>Registration ID</label><input type="number" name="reg_id" required></div><button class="btn {{btnclass}}">{{select_button}}</button></form>{%endif%}
<br><a href="/" class="btn btn-secondary">← Back</a></div></div>{{popup|safe}}{%if error%}<script>window.onload=function(){showError({{error|tojson}})}</script>{%endif%}</body></html>"""

@app.route("/")
def admin_home():
    conn=sqlite3.connect(DB_PATH); cur=conn.cursor()
    cur.execute("SELECT COUNT(*) FROM registration"); total=cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT reg_number) FROM registration"); unique=cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM registration WHERE fitness_valid_upto >= date('now')"); fitness=cur.fetchone()[0]
    table_view = table_browser.browse(conn, ("registration", "rto_office", "vehicle_model", "ownership_transfer", "puc_certificate", "driving_licence"))
    conn.close()
    rows = get_rows() if table_view["name"] == "registration" else []
    return render_template_string(HOME,style=STYLE, table_view=table_view,popup=POPUP,rows=rows,total=total,unique=unique,fitness=fitness,error=None,clean_html=cleanser.home_html(request.args.get("clean_notice", "")))

def render_form(title, subtitle, fields, button, back="/", error=None, action=None):
    if action is None:
        action = request.path
    return render_template_string(FORM,style=STYLE,popup=POPUP,title=title,subtitle=subtitle,fields=fields,button=button,back=back,error=error,action=action)

@app.route("/add",methods=["GET","POST"])
def add():
    names=[("reg_number","Registration Number","text"),("owner_name","Owner Name","text"),("owner_address","Owner Address","text"),("chassis_number","Chassis Number","text"),("engine_number","Engine Number","text"),("make","Make","text"),("model","Model","text"),("reg_date","Registration Date","date"),("reg_valid_upto","Registration Valid Upto","date"),("fitness_valid_upto","Fitness Valid Upto","date")]
    # the owner's driving licence (optional): its date of birth gives the owner's age for the list queries
    extra=[("owner_dob","Owner Date of Birth (optional, from the driving licence)","date"),("licence_no","Driving Licence Number (optional)","text")]
    if request.method=="POST":
        data={n:request.form.get(n,"").strip() for n,_,_ in names+extra}
        data["reg_number"]=data["reg_number"].upper();data["licence_no"]=data["licence_no"].upper()
        required=["reg_number","owner_name","chassis_number","engine_number"]
        missing=next((x for x in required if not data[x]),None)
        error=f"{missing.replace('_',' ').title()} is required." if missing else licence_error(data)
        if error: return render_form("Add Registration","Add a new vehicle registration",[(n,l,t,data[n],n in required,False) for n,l,t in names+extra],"Add Registration","/",error)
        conn=sqlite3.connect(DB_PATH);cur=conn.cursor()   # saved as typed; cleansing builds the cleaned copy
        cur.execute("""INSERT INTO registration(reg_number,owner_name,owner_address,chassis_number,engine_number,make,model,reg_date,reg_valid_upto,fitness_valid_upto) VALUES(?,?,?,?,?,?,?,?,?,?)""",tuple(data[n] for n,_,_ in names))
        rid=cur.lastrowid
        if data["owner_dob"]:
            number=data["licence_no"] or f"{rto_context.office_code(data['reg_number']) or data['reg_number'][:4]} {date.today().year}{rid:07d}"
            cur.execute("INSERT INTO driving_licence (reg_id, licence_no, holder_name, date_of_birth) VALUES (?,?,?,?)",(rid,number,data["owner_name"],data["owner_dob"]))
        conn.commit();conn.close();return redirect(url_for("admin_home", clean_notice=cleanser.after_change("RTO", rid)))
    return render_form("Add Registration","Add a new vehicle registration",[(n,l,t,"",n in ["reg_number","owner_name","chassis_number","engine_number"],False) for n,l,t in names+extra],"Add Registration")


def licence_error(data):
    """The optional driving licence: an owner at least 18 years old, and a licence number not already used."""
    if not data["owner_dob"]:
        return "Enter the owner's date of birth for the driving licence number." if data["licence_no"] else None
    try:
        born=date.fromisoformat(data["owner_dob"])
    except ValueError:
        return "Owner Date of Birth is not a valid date."
    on=date.today()
    try:
        on=min(on,date.fromisoformat(data["reg_date"])) if data["reg_date"] else on
    except ValueError:
        pass
    if (on.year-born.year-((on.month,on.day)<(born.month,born.day)))<18:
        return "The owner must be at least 18 years old (on the registration date) to hold a driving licence."
    if data["licence_no"]:
        conn=sqlite3.connect(DB_PATH)
        try:
            used=conn.execute("SELECT 1 FROM driving_licence WHERE licence_no=?",(data["licence_no"],)).fetchone()
        finally:
            conn.close()
        if used: return f"Driving licence {data['licence_no']} is already recorded for another registration."
    return None

@app.route("/edit",methods=["GET","POST"])
def edit():
    rows=[];plate="";searched=False;error=None
    if request.method=="POST":
        searched=True;plate=request.form.get("reg_number","").strip().upper()
        if not plate:error="Please enter a registration number."
        else:
            rows=get_rows(plate)
            if not rows:error=f"No registration found for vehicle {plate}."
    return render_template_string(SELECT,style=STYLE,popup=POPUP,title="Edit Registration",subtitle="Find a vehicle and select its registration record",rows=rows,plate=plate,searched=searched,error=error,select_url="/edit/select",btnclass="btn-primary",select_button="Select Registration")

@app.route("/edit/select",methods=["POST"])
def edit_select():
    plate=request.form.get("reg_number","").strip().upper(); rid=request.form.get("reg_id","").strip()
    conn=sqlite3.connect(DB_PATH);conn.row_factory=sqlite3.Row;cur=conn.cursor();cur.execute("SELECT * FROM registration WHERE reg_id=? AND reg_number=?",(rid,plate));r=cur.fetchone()
    if not r:
        rows=cur.execute("SELECT * FROM registration WHERE reg_number=? ORDER BY reg_id",(plate,)).fetchall();conn.close()
        return render_template_string(SELECT,style=STYLE,popup=POPUP,title="Edit Registration",subtitle="Find a vehicle and select its registration record",rows=rows,plate=plate,searched=True,error="Invalid Registration ID. Please enter an ID shown in the table.",select_url="/edit/select",btnclass="btn-primary",select_button="Select Registration")
    conn.close()
    names=[("reg_number","Registration Number","text"),("owner_name","Owner Name","text"),("owner_address","Owner Address","text"),("chassis_number","Chassis Number","text"),("engine_number","Engine Number","text"),("make","Make","text"),("model","Model","text"),("reg_date","Registration Date","date"),("reg_valid_upto","Registration Valid Upto","date"),("fitness_valid_upto","Fitness Valid Upto","date")]
    fields=[(n,l,t,str(r[n] or ""),n in ["reg_number","owner_name","chassis_number","engine_number"],n=="reg_number") for n,l,t in names]
    fields.append(("reg_id","Registration ID","number",str(r["reg_id"]),False,True))
    return render_form(f"Edit Registration #{r['reg_id']}",f"Vehicle: {plate}",fields,"Save Changes","/edit",action="/edit/save")

@app.route("/edit/save",methods=["POST"])
def edit_save():
    plate=request.form.get("reg_number","").strip().upper();rid=request.form.get("reg_id","").strip()
    names=["owner_name","owner_address","chassis_number","engine_number","make","model","reg_date","reg_valid_upto","fitness_valid_upto"]
    data={n:request.form.get(n,"").strip() for n in names}
    if not data["owner_name"] or not data["chassis_number"] or not data["engine_number"]:
        conn=sqlite3.connect(DB_PATH);conn.row_factory=sqlite3.Row;r=conn.execute("SELECT * FROM registration WHERE reg_id=? AND reg_number=?",(rid,plate)).fetchone();conn.close()
        return render_form(f"Edit Registration #{rid}",f"Vehicle: {plate}",[(n,n.replace("_"," ").title(),"date" if "date" in n or "upto" in n else "text",str(r[n] or ""),n in ["owner_name","chassis_number","engine_number"],n=="reg_number") for n in ["reg_number"]+names],"Save Changes","/edit","Required fields cannot be empty.",action="/edit/save")
    before=cleanser.decision("RTO",rid)
    conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute("""UPDATE registration SET owner_name=?,owner_address=?,chassis_number=?,engine_number=?,make=?,model=?,reg_date=?,reg_valid_upto=?,fitness_valid_upto=? WHERE reg_id=? AND reg_number=?""",tuple(data[n] for n in names)+(rid,plate));conn.commit();conn.close();return redirect(url_for("admin_home", clean_notice=cleanser.after_change("RTO",rid,before)))

@app.route("/delete",methods=["GET","POST"])
def delete():
    rows=[];plate="";searched=False;error=None
    if request.method=="POST":
        searched=True;plate=request.form.get("reg_number","").strip().upper()
        if not plate:error="Please enter a registration number."
        else:
            rows=get_rows(plate)
            if not rows:error=f"No registration found for vehicle {plate}."
    return render_template_string(SELECT,style=STYLE,popup=POPUP,title="Delete Registration",subtitle="Find a vehicle and select the registration record to delete",rows=rows,plate=plate,searched=searched,error=error,select_url="/delete/confirm",btnclass="btn-danger",select_button="Delete Registration")

@app.route("/delete/confirm",methods=["POST"])
def delete_confirm():
    plate=request.form.get("reg_number","").strip().upper();rid=request.form.get("reg_id","").strip()
    conn=sqlite3.connect(DB_PATH);cur=conn.cursor();cur.execute("SELECT * FROM registration WHERE reg_id=? AND reg_number=?",(rid,plate));r=cur.fetchone()
    if not r:
        rows=cur.execute("SELECT * FROM registration WHERE reg_number=? ORDER BY reg_id",(plate,)).fetchall();conn.close()
        return render_template_string(SELECT,style=STYLE,popup=POPUP,title="Delete Registration",subtitle="Find a vehicle and select the registration record to delete",rows=rows,plate=plate,searched=True,error="Invalid Registration ID. Please enter an ID shown in the table.",select_url="/delete/confirm",btnclass="btn-danger",select_button="Delete Registration")
    cur.execute("DELETE FROM registration WHERE reg_id=? AND reg_number=?",(rid,plate));conn.commit();conn.close();return redirect(url_for("admin_home", clean_notice=cleanser.after_change("RTO")))

@app.get("/health")
def health(): return jsonify({"service":"RTO_DB","status":"up"})

@app.get("/vehicle/<path:plate>")
def get_vehicle(plate): return jsonify([dict(r) for r in get_rows(plate.upper())])


@app.get("/records")
def all_registration_records():
    """Return current registration records for approximate plate matching."""
    return jsonify([dict(row) for row in get_rows()])

@app.put("/records/registration/<int:record_id>/plate")
def update_registration_plate_api(record_id):
    payload = request.get_json(silent=True) or {}
    expected, new = str(payload.get("expected_plate", "")), str(payload.get("new_plate", "")).strip().upper()
    if not expected or not new:
        return jsonify({"updated": False, "error": "expected_plate and new_plate are required"}), 400
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("UPDATE registration SET reg_number=? WHERE reg_id=? AND reg_number=?", (new, record_id, expected))
        conn.commit()
        if cur.rowcount != 1:
            return jsonify({"updated": False, "error": "record missing or plate changed since review"}), 409
        return jsonify({"updated": True, "source": "RTO", "record_type": "registration", "record_id": record_id, "old_plate": expected, "new_plate": new})
    finally:
        conn.close()


def init_db():
    """Main table (with its seed records) plus the Phase 3 related tables, also on an existing database."""
    _init_main()
    conn = sqlite3.connect(DB_PATH)
    try:
        rto_context.ensure(conn)
        conn.commit()
    finally:
        conn.close()


# Phase 3: federated context discovery over this database's FK-PK joins, and search of its own documents.
context_search.register(app, "RTO", lambda: DB_PATH, rto_context.discover,
                        context_search.DocIndex(os.path.join(os.path.dirname(os.path.abspath(__file__)), "documents"), "RTO"), rto_context.JOINS,
                        count=rto_context.count, profile=rto_context.profile)


if __name__=="__main__":
    init_db()
    desk.start()      # first start: the existing plates seed the dictionary
    cleanser.start()  # clean now, then pick up pending work / command-line edits in the background
    parser=argparse.ArgumentParser();parser.add_argument("--port",type=int,default=5003);parser.add_argument("--host",default="0.0.0.0");args=parser.parse_args()
    print(f"RTO service (Device 3) running on http://{args.host}:{args.port}")
    app.run(host=args.host,port=args.port)
