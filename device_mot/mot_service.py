import sqlite3
import hashlib
import json
from contextlib import closing
import os
import argparse
from datetime import datetime
from flask import Flask, jsonify, request, render_template_string, redirect, url_for
import context_search
import mot_context
import table_browser

DB_PATH = os.path.join(os.path.dirname(__file__), "mot_report.db")
app = Flask(__name__)


def delivery_key(values):
    # Older ANPR queues have no event ID. Their immutable payload identifies a retry.
    return "payload:" + hashlib.sha256(json.dumps(values, ensure_ascii=True).encode()).hexdigest()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE IF NOT EXISTS mot_report (
            report_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            plate_no             TEXT NOT NULL,
            generated_on         TEXT NOT NULL,
            owner_name           TEXT,
            make_model           TEXT,
            insurance_status     TEXT,
            theft_status         TEXT,
            shredded             TEXT,
            risk_flags           TEXT,
            last_seen_on_road    TEXT,
            recommended_action   TEXT
        )
        """)
        conn.execute("BEGIN IMMEDIATE")
        if "delivery_key" not in {r[1] for r in conn.execute("PRAGMA table_info(mot_report)")}:
            conn.execute("ALTER TABLE mot_report ADD COLUMN delivery_key TEXT")
            conn.execute("CREATE UNIQUE INDEX mot_report_delivery ON mot_report(delivery_key)")
            for row in conn.execute("SELECT report_id, " + ", ".join(REPORT_COLUMNS) + " FROM mot_report ORDER BY report_id"):
                conn.execute("UPDATE OR IGNORE mot_report SET delivery_key=? WHERE report_id=?",
                             (delivery_key(row[1:]), row[0]))
        mot_context.ensure(conn)      # Phase 3 related tables: zone, officer, enforcement_action, challan
        conn.commit()
    finally:
        conn.close()


def get_rows(plate=None):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    if plate:
        cur.execute("SELECT * FROM mot_report WHERE plate_no=? ORDER BY report_id", (plate,))
    else:
        cur.execute("SELECT * FROM mot_report ORDER BY report_id")
    rows = cur.fetchall()
    conn.close()
    return rows


def insert_report(data):
    values = tuple(data.get(field) for field in REPORT_COLUMNS)
    key = "event:" + str(data["event_id"]) if data.get("event_id") else delivery_key(values)
    columns = ", ".join((*REPORT_COLUMNS, "delivery_key"))
    placeholders = ", ".join("?" for _ in range(len(values) + 1))
    with closing(sqlite3.connect(DB_PATH, timeout=10)) as conn, conn:
        inserted = conn.execute(f"INSERT INTO mot_report ({columns}) VALUES ({placeholders}) "
                                "ON CONFLICT(delivery_key) DO NOTHING", (*values, key))
        if not inserted.rowcount:
            return conn.execute("SELECT report_id FROM mot_report WHERE delivery_key=?", (key,)).fetchone()[0]
        report_id = inserted.lastrowid
        mot_context.assign(conn, report_id, data.get("plate_no"), data.get("risk_flags"), data.get("recommended_action"))
        return report_id


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
.form-card{max-width:750px}.form-group{margin-bottom:18px}label{display:block;font-weight:bold;margin-bottom:8px}input,textarea{width:100%;padding:12px 14px;border:1px solid #cbd5e1;border-radius:7px;font-size:15px;background:white;font-family:Arial}textarea{min-height:90px;resize:vertical}
input:focus,textarea:focus{outline:none;border-color:#2563eb}.form-actions{display:flex;gap:10px;margin-top:25px}.info-box{background:#f8fafc;border:1px solid #dbe3ec;border-radius:10px;padding:17px;margin:25px 0;color:#334155}
.popup-overlay{position:fixed;inset:0;background:rgba(0,0,0,.35);display:flex;align-items:center;justify-content:center;z-index:9999}
.popup{background:white;width:min(450px,90%);border-radius:14px;padding:28px;box-shadow:0 10px 35px rgba(0,0,0,.25);text-align:center}.popup h3{margin-top:0;font-size:22px}.popup p{color:#4b5563;line-height:1.5}.popup-error{color:#b91c1c}.popup-close{margin-top:15px;padding:9px 20px;border:0;border-radius:7px;background:#2563eb;color:white;cursor:pointer}
@media(max-width:900px){.stats{grid-template-columns:1fr}}body{overflow-x:hidden}
</style>"""

POPUP = """<script>
function showError(message){
    const o=document.createElement('div');
    o.className='popup-overlay';
    o.innerHTML=`<div class="popup" onclick="event.stopPropagation()"><h3 class="popup-error">Invalid Input</h3><p>${message}</p><button class="popup-close" onclick="closePopup()">OK</button></div>`;
    o.onclick=closePopup;
    document.body.appendChild(o);
}
function closePopup(){const p=document.querySelector('.popup-overlay');if(p)p.remove()}
</script>"""

HOME = """<!DOCTYPE html><html><head><title>MoT Database</title>{{style|safe}}</head><body><div class="container"><div class="card">
<h1>MoT Database</h1><div class="subtitle">Device 5 — Motor Vehicle Report Records</div>
<div class="stats"><div class="stat-card"><div class="stat-title">Total Reports</div><div class="stat-value">{{total}}</div></div>
<div class="stat-card"><div class="stat-title">Unique Vehicles</div><div class="stat-value">{{unique}}</div></div>
<div class="stat-card"><div class="stat-title">Reports With Risk Flags</div><div class="stat-value">{{flagged}}</div></div></div>
<div class="buttons"><a href="/add" class="btn btn-primary">+ Add Report</a><a href="/edit" class="btn btn-secondary">Edit Report</a><a href="/delete" class="btn btn-danger">Delete Report</a><a href="/schema" class="btn btn-secondary">Schema &amp; Documents</a></div>
""" + table_browser.CONTROLS + """
{% if table_view.name == "mot_report" %}
    <h2>All MoT Reports</h2><div class="table-wrapper"><table><tr>
<th>Report ID</th><th>Plate No</th><th>Generated On</th><th>Owner</th><th>Make / Model</th><th>Insurance Status</th><th>Theft Status</th><th>Shredded</th><th>Risk Flags</th><th>Last Seen On Road</th><th>Recommended Action</th></tr>
{%for r in rows%}<tr><td><b>{{r["report_id"]}}</b></td><td><b>{{r["plate_no"]}}</b></td><td>{{r["generated_on"]}}</td><td>{{r["owner_name"] or ""}}</td><td>{{r["make_model"] or ""}}</td><td>{{r["insurance_status"] or ""}}</td><td>{{r["theft_status"] or ""}}</td><td>{{r["shredded"] or ""}}</td><td>{{r["risk_flags"] or ""}}</td><td>{{r["last_seen_on_road"] or ""}}</td><td>{{r["recommended_action"] or ""}}</td></tr>{%endfor%}</table></div>
{% else %}
""" + table_browser.RECORDS + """
{% endif %}
</div></div>{{popup|safe}}{%if error%}<script>window.onload=function(){showError({{error|tojson}})}</script>{%endif%}</body></html>"""

FORM = """<!DOCTYPE html><html><head><title>{{title}}</title>{{style|safe}}</head><body><div class="container"><div class="card form-card">
<h1>{{title}}</h1><div class="subtitle">{{subtitle}}</div><form method="POST" action="{{action}}">
{%for f in fields%}<div class="form-group"><label>{{f[1]}}</label>{%if f[2]=='textarea'%}<textarea name="{{f[0]}}" {%if f[4]%}required{%endif%} {%if f[5]%}readonly{%endif%}>{{f[3]}}</textarea>{%else%}<input type="{{f[2]}}" name="{{f[0]}}" value="{{f[3]}}" {%if f[4]%}required{%endif%} {%if f[5]%}readonly{%endif%}>{%endif%}</div>{%endfor%}
<div class="form-actions"><button class="btn btn-primary" type="submit">{{button}}</button><a href="{{back}}" class="btn btn-secondary">← Back</a></div></form>
</div></div>{{popup|safe}}{%if error%}<script>window.onload=function(){showError({{error|tojson}})}</script>{%endif%}</body></html>"""

SELECT = """<!DOCTYPE html><html><head><title>{{title}}</title>{{style|safe}}</head><body><div class="container"><div class="card">
<h1>{{title}}</h1><div class="subtitle">{{subtitle}}</div><form method="POST"><div class="form-group"><label>Plate Number</label><input name="plate_no" value="{{plate}}" placeholder="e.g. MH12AB1234" required></div><button class="btn btn-primary">Find Reports</button></form>
{%if searched and rows%}<h2>Reports for {{plate}}</h2><div class="table-wrapper"><table><tr><th>Report ID</th><th>Generated On</th><th>Owner</th><th>Insurance</th><th>Theft</th><th>Risk Flags</th><th>Recommended Action</th></tr>{%for r in rows%}<tr><td><b>{{r["report_id"]}}</b></td><td>{{r["generated_on"]}}</td><td>{{r["owner_name"] or ""}}</td><td>{{r["insurance_status"] or ""}}</td><td>{{r["theft_status"] or ""}}</td><td>{{r["risk_flags"] or ""}}</td><td>{{r["recommended_action"] or ""}}</td></tr>{%endfor%}</table></div>
<div class="info-box">Enter the <b>Report ID</b> from the table to select the record.</div><form method="POST" action="{{select_url}}" class="form-card"><input type="hidden" name="plate_no" value="{{plate}}"><div class="form-group"><label>Report ID</label><input type="number" name="report_id" required></div><button class="btn {{btnclass}}">{{select_button}}</button></form>{%endif%}
<br><a href="/" class="btn btn-secondary">← Back</a></div></div>{{popup|safe}}{%if error%}<script>window.onload=function(){showError({{error|tojson}})}</script>{%endif%}</body></html>"""


@app.route("/")
def admin_home():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM mot_report")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT plate_no) FROM mot_report")
    unique = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM mot_report WHERE risk_flags IS NOT NULL AND TRIM(risk_flags) != '' AND LOWER(TRIM(risk_flags)) NOT IN ('none','no','false')")
    flagged = cur.fetchone()[0]
    table_view = table_browser.browse(conn, ("mot_report", "zone", "officer", "enforcement_action", "challan"))
    conn.close()
    rows = get_rows() if table_view["name"] == "mot_report" else []
    return render_template_string(HOME, style=STYLE, table_view=table_view, popup=POPUP, rows=rows, total=total, unique=unique, flagged=flagged, error=None)


def render_form(title, subtitle, fields, button, back="/", error=None, action=None):
    if action is None:
        action = request.path
    return render_template_string(FORM, style=STYLE, popup=POPUP, title=title, subtitle=subtitle, fields=fields, button=button, back=back, error=error, action=action)


def db_datetime_to_html(value):
    if not value:
        return ""
    return str(value).replace(" ", "T", 1)[:16]


def html_datetime_to_db(value):
    if not value:
        return ""
    value = value.strip().replace("T", " ", 1)
    if len(value) == 16:
        value += ":00"
    return value


REPORT_FIELDS = [
    ("plate_no", "Plate Number", "text"),
    ("generated_on", "Generated On", "datetime-local"),
    ("owner_name", "Owner Name", "text"),
    ("make_model", "Make / Model", "text"),
    ("insurance_status", "Insurance Status", "text"),
    ("theft_status", "Theft Status", "text"),
    ("shredded", "Shredded", "text"),
    ("risk_flags", "Risk Flags", "textarea"),
    ("last_seen_on_road", "Last Seen On Road", "datetime-local"),
    ("recommended_action", "Recommended Action", "textarea"),
]


REPORT_COLUMNS = tuple(name for name, _, _ in REPORT_FIELDS)


@app.route("/add", methods=["GET", "POST"])
def add_report():
    if request.method == "POST":
        data = {n: request.form.get(n, "").strip() for n, _, _ in REPORT_FIELDS}
        data["plate_no"] = data["plate_no"].upper()
        # Generated On is always the actual current server datetime.
        data["generated_on"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data["last_seen_on_road"] = html_datetime_to_db(data["last_seen_on_road"])
        required = ["plate_no", "insurance_status", "risk_flags", "recommended_action"]
        missing = next((x for x in required if not data[x]), None)
        if missing:
            fields = []
            for n, l, t in REPORT_FIELDS:
                value = db_datetime_to_html(data[n]) if t == "datetime-local" else data[n]
                fields.append((n, l, t, value, n in required, n == "generated_on"))
            return render_form("Add Report", "Add a new MoT report", fields, "Add Report", "/", f"{missing.replace('_', ' ').title()} is required.")
        insert_report(data)
        return redirect(url_for("admin_home"))
    fields = []
    for n, l, t in REPORT_FIELDS:
        value = datetime.now().strftime("%Y-%m-%dT%H:%M") if n == "generated_on" else ""
        required = n in ["plate_no", "insurance_status", "risk_flags", "recommended_action"]
        readonly = n == "generated_on"
        fields.append((n, l, t, value, required, readonly))
    return render_form("Add Report", "Add a new MoT report", fields, "Add Report")


@app.route("/edit", methods=["GET", "POST"])
def edit_search():
    rows = []
    plate = ""
    searched = False
    error = None
    if request.method == "POST":
        searched = True
        plate = request.form.get("plate_no", "").strip().upper()
        if not plate:
            error = "Please enter a plate number."
        else:
            rows = get_rows(plate)
            if not rows:
                error = f"No report found for vehicle {plate}."
    return render_template_string(SELECT, style=STYLE, popup=POPUP, title="Edit Report", subtitle="Find a vehicle and select its MoT report", rows=rows, plate=plate, searched=searched, error=error, select_url="/edit/select", btnclass="btn-primary", select_button="Select Report")


@app.route("/edit/select", methods=["POST"])
def edit_select():
    plate = request.form.get("plate_no", "").strip().upper()
    rid = request.form.get("report_id", "").strip()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM mot_report WHERE report_id=? AND plate_no=?", (rid, plate))
    r = cur.fetchone()
    if not r:
        rows = cur.execute("SELECT * FROM mot_report WHERE plate_no=? ORDER BY report_id", (plate,)).fetchall()
        conn.close()
        return render_template_string(SELECT, style=STYLE, popup=POPUP, title="Edit Report", subtitle="Find a vehicle and select its MoT report", rows=rows, plate=plate, searched=True, error="Invalid Report ID. Please enter an ID shown in the table.", select_url="/edit/select", btnclass="btn-primary", select_button="Select Report")
    conn.close()
    fields = []
    for n, l, t in REPORT_FIELDS:
        value = db_datetime_to_html(r[n]) if t == "datetime-local" else str(r[n] or "")
        required = n in ["plate_no", "insurance_status", "risk_flags", "recommended_action"]
        readonly = n in ["plate_no", "generated_on"]
        fields.append((n, l, t, value, required, readonly))
    fields.append(("report_id", "Report ID", "number", str(r["report_id"]), False, True))
    return render_form(f"Edit Report #{r['report_id']}", f"Vehicle: {plate}", fields, "Save Changes", "/edit", action="/edit/save")


@app.route("/edit/save", methods=["POST"])
def edit_save():
    plate = request.form.get("plate_no", "").strip().upper()
    rid = request.form.get("report_id", "").strip()
    names = [n for n, _, _ in REPORT_FIELDS if n != "plate_no"]
    data = {n: request.form.get(n, "").strip() for n in names}
    data["generated_on"] = html_datetime_to_db(data["generated_on"])
    data["last_seen_on_road"] = html_datetime_to_db(data["last_seen_on_road"])
    required = ["generated_on", "insurance_status", "risk_flags", "recommended_action"]
    missing = next((x for x in required if not data[x]), None)

    if not plate or not rid:
        return render_form(f"Edit Report #{rid or '?'}", f"Vehicle: {plate}", [], "Save Changes", "/edit", "Invalid report selection.", action="/edit/save")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM mot_report WHERE report_id=? AND plate_no=?", (rid, plate)).fetchone()
    conn.close()

    if not r:
        return redirect(url_for("admin_home"))

    if missing:
        fields = []
        for n, l, t in REPORT_FIELDS:
            original = db_datetime_to_html(r[n]) if t == "datetime-local" else str(r[n] or "")
            value = data.get(n, original)
            fields.append((n, l, t, value, n in ["plate_no", "insurance_status", "risk_flags", "recommended_action"], n in ["plate_no", "generated_on"]))
        fields.append(("report_id", "Report ID", "number", rid, False, True))
        return render_form(f"Edit Report #{rid}", f"Vehicle: {plate}", fields, "Save Changes", "/edit", f"{missing.replace('_', ' ').title()} is required.", action="/edit/save")

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """UPDATE mot_report SET generated_on=?, owner_name=?, make_model=?, insurance_status=?, theft_status=?, shredded=?, risk_flags=?, last_seen_on_road=?, recommended_action=? WHERE report_id=? AND plate_no=?""",
        tuple(data[n] for n in names) + (rid, plate)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("admin_home"))


@app.route("/delete", methods=["GET", "POST"])
def delete_search():
    rows = []
    plate = ""
    searched = False
    error = None
    if request.method == "POST":
        searched = True
        plate = request.form.get("plate_no", "").strip().upper()
        if not plate:
            error = "Please enter a plate number."
        else:
            rows = get_rows(plate)
            if not rows:
                error = f"No report found for vehicle {plate}."
    return render_template_string(SELECT, style=STYLE, popup=POPUP, title="Delete Report", subtitle="Find a vehicle and select the MoT report to delete", rows=rows, plate=plate, searched=searched, error=error, select_url="/delete/confirm", btnclass="btn-danger", select_button="Delete Report")


@app.route("/delete/confirm", methods=["POST"])
def delete_confirm():
    plate = request.form.get("plate_no", "").strip().upper()
    rid = request.form.get("report_id", "").strip()
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT * FROM mot_report WHERE report_id=? AND plate_no=?", (rid, plate))
    r = cur.fetchone()
    if not r:
        rows = cur.execute("SELECT * FROM mot_report WHERE plate_no=? ORDER BY report_id", (plate,)).fetchall()
        conn.close()
        return render_template_string(SELECT, style=STYLE, popup=POPUP, title="Delete Report", subtitle="Find a vehicle and select the MoT report to delete", rows=rows, plate=plate, searched=True, error="Invalid Report ID. Please enter an ID shown in the table.", select_url="/delete/confirm", btnclass="btn-danger", select_button="Delete Report")
    cur.execute("DELETE FROM mot_report WHERE report_id=? AND plate_no=?", (rid, plate))
    conn.commit()
    conn.close()
    return redirect(url_for("admin_home"))


# Existing API endpoints — kept for the mediator/client.
@app.get("/health")
def health():
    return jsonify({"service": "MOT_REPORT_DB", "status": "up"})


@app.post("/reports")
def create_report():
    data = request.get_json()
    if not data:
        return jsonify({"error": "JSON body required"}), 400
    required = ["plate_no", "generated_on", "insurance_status", "risk_flags", "recommended_action"]
    missing = [field for field in required if field not in data]
    if missing:
        return jsonify({"error": "Missing required fields", "fields": missing}), 400
    report_id = insert_report(data)
    return jsonify({"status": "report_received", "report_id": report_id, "plate_no": data["plate_no"]}), 201


@app.get("/reports")
def get_reports():
    # Ascending order to match the admin GUI and other device GUIs.
    return jsonify([dict(r) for r in get_rows()])


@app.get("/reports/<plate>")
def get_report_for_vehicle(plate):
    return jsonify([dict(r) for r in get_rows(plate.upper())])


# Phase 3: federated context discovery over this database's FK-PK joins, and search of its own documents.
context_search.register(app, "MoT", lambda: DB_PATH, mot_context.discover,
                        context_search.DocIndex(os.path.join(os.path.dirname(os.path.abspath(__file__)), "documents"), "MoT"),
                        mot_context.JOINS, count=mot_context.count)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=5005)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    init_db()
    print(f"MoT service (Device 5) running on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port)
