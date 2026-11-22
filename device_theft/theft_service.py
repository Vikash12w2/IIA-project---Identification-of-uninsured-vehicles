import sqlite3
import os
import argparse
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import plate_entry_client as entry
import cleansing
import context_search
import theft_context
import table_browser
import peers
from flask import Flask, jsonify, request, redirect, url_for, render_template_string

DB_PATH = os.path.join(os.path.dirname(__file__), "theft_shred.db")

app = Flask(__name__)


# These databases' plate dictionaries (plate_entry_client.py), in their own file next to the database.
desk = entry.PlateDesk(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plate_dictionary.db"), [
    entry.Source("Theft", lambda: DB_PATH, "theft_report", "theft_id", "vehicle_no", "theft"),
    entry.Source("Shredding", lambda: DB_PATH, "shredding_record", "shred_id", "vehicle_no", "shredding"),
])
desk.register(app)

# Automatic data cleansing (cleansing.py): theft_report_clean / shredding_record_clean next to the raw tables,
# using every device's data.
cleanser = cleansing.Cleanser("Theft/Shredding", lambda: DB_PATH, desk.book, peers.PEERS)
cleanser.register(app)


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def _init_main():
    # Check for the tables, not the file: an empty theft_shred.db (e.g. re-created by a
    # request after the file was deleted) must still get its tables and seed rows.
    conn = sqlite3.connect(DB_PATH)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='theft_report'").fetchone():
        conn.close()
        return

    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE theft_report (
        theft_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_no     TEXT NOT NULL,
        report_date    TEXT NOT NULL,
        police_station TEXT,
        fir_number     TEXT,
        status         TEXT
    );
    """)

    cur.execute("""
    CREATE TABLE shredding_record (
        shred_id       INTEGER PRIMARY KEY AUTOINCREMENT,
        vehicle_no     TEXT NOT NULL,
        shred_date     TEXT NOT NULL,
        scrap_center   TEXT,
        certificate_no TEXT
    );
    """)

    cur.executemany("""
        INSERT INTO theft_report
        (vehicle_no, report_date, police_station, fir_number, status)
        VALUES (?, ?, ?, ?, ?)
    """, [
        ("MH14ZZ0000", "2026-08-20", "Shivaji Nagar PS, Pune",
         "FIR-2026-4471", "Reported"),
        ("KL07LM8899", "2025-12-01", "MG Road PS, Kochi",
         "FIR-2025-9981", "Recovered"),
    ])

    cur.executemany("""
        INSERT INTO shredding_record
        (vehicle_no, shred_date, scrap_center, certificate_no)
        VALUES (?, ?, ?, ?)
    """, [
        ("TN09EF7777", "2026-07-01",
         "GreenScrap Recyclers, Chennai", "SCR-2026-0099"),
    ])

    conn.commit()
    conn.close()


# ============================================================
# DATABASE HELPERS
# ============================================================

def get_theft_reports():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM theft_report
        ORDER BY theft_id
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_shredding_records():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        SELECT *
        FROM shredding_record
        ORDER BY shred_id
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def query(table, plate):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute(
        f"SELECT * FROM {table} WHERE vehicle_no = ? ORDER BY rowid",
        (plate,)
    )

    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


# ============================================================
# COMMON CSS
# ============================================================

STYLE = """
<style>
* {
    box-sizing: border-box;
}

body {
    margin: 0;
    padding: 30px;
    font-family: Arial, Helvetica, sans-serif;
    background: #f5f7fa;
    color: #17202a;
}

.container {
    max-width: 1500px;
    margin: auto;
}

.card {
    background: white;
    border-radius: 14px;
    padding: 30px;
    box-shadow: 0 4px 18px rgba(0,0,0,0.08);
}

h1 {
    margin-top: 0;
    margin-bottom: 6px;
    font-size: 32px;
}

h2 {
    margin-top: 35px;
}

.subtitle {
    color: #6b7280;
    margin-bottom: 25px;
    font-size: 16px;
}

/* ============================================================
   STAT CARDS
   ============================================================ */

.stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 20px;
    margin: 25px 0;
}

.stat-card {
    background: #f1f3f5;
    border-radius: 14px;
    padding: 22px 30px;
}

.stat-title {
    color: #52606d;
    font-size: 16px;
}

.stat-value {
    font-size: 34px;
    font-weight: bold;
    margin-top: 8px;
}

/* ============================================================
   BUTTONS
   ============================================================ */

.buttons {
    display: flex;
    gap: 12px;
    margin: 25px 0;
    flex-wrap: wrap;
}

.btn {
    display: inline-block;
    border: none;
    border-radius: 7px;
    padding: 11px 18px;
    text-decoration: none;
    cursor: pointer;
    font-size: 15px;
    font-weight: 600;
}

.btn-primary {
    background: #2563eb;
    color: white;
}

.btn-primary:hover {
    background: #1d4ed8;
}

.btn-danger {
    background: #dc2626;
    color: white;
}

.btn-danger:hover {
    background: #b91c1c;
}

.btn-secondary {
    background: #6b7280;
    color: white;
}

.btn-secondary:hover {
    background: #4b5563;
}

/* ============================================================
   TABLE
   ============================================================ */

.table-wrapper {
    width: 100%;
    overflow-x: auto;
}

table {
    width: 100%;
    border-collapse: collapse;
    background: white;
}

th {
    background: #222;
    color: white;
    padding: 14px;
    text-align: left;
    white-space: nowrap;
}

td {
    padding: 13px;
    border-bottom: 1px solid #e5e7eb;
    white-space: nowrap;
}

tr:hover td {
    background: #f8fafc;
}

/* ============================================================
   STATUS
   ============================================================ */

.status {
    display: inline-block;
    padding: 5px 10px;
    border-radius: 20px;
    font-size: 13px;
    font-weight: bold;
}

.status-reported {
    background: #fee2e2;
    color: #991b1b;
}

.status-recovered {
    background: #dcfce7;
    color: #166534;
}

/* ============================================================
   FORMS
   ============================================================ */

.form-card {
    max-width: 700px;
}

.form-group {
    margin-bottom: 20px;
}

label {
    display: block;
    font-weight: bold;
    margin-bottom: 8px;
}

input,
select {
    width: 100%;
    padding: 12px 14px;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    font-size: 15px;
    background: white;
}

input:focus,
select:focus {
    outline: none;
    border-color: #2563eb;
}

.form-actions {
    display: flex;
    gap: 10px;
    margin-top: 25px;
}

/* ============================================================
   INFO BOX
   ============================================================ */

.info-box {
    background: #f8fafc;
    border: 1px solid #dbe3ec;
    border-radius: 10px;
    padding: 17px;
    margin: 25px 0;
    color: #334155;
}

/* ============================================================
   POPUP
   ============================================================ */

.popup-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0,0,0,0.35);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 9999;
}

.popup {
    background: white;
    width: min(450px, 90%);
    border-radius: 14px;
    padding: 28px;
    box-shadow: 0 10px 35px rgba(0,0,0,0.25);
    text-align: center;
}

.popup h3 {
    margin-top: 0;
    font-size: 22px;
}

.popup p {
    color: #4b5563;
    line-height: 1.5;
}

.popup-error {
    color: #b91c1c;
}

.popup-close {
    margin-top: 15px;
    padding: 9px 20px;
    border: none;
    border-radius: 7px;
    background: #2563eb;
    color: white;
    cursor: pointer;
}

/* ============================================================
   RESPONSIVE
   ============================================================ */

@media (max-width: 1000px) {
    .stats {
        grid-template-columns: repeat(2, 1fr);
    }
}

@media (max-width: 600px) {
    .stats {
        grid-template-columns: 1fr;
    }

    body {
        padding: 15px;
    }
}
</style>
"""


# ============================================================
# POPUP
# ============================================================

POPUP = """
<script>
function showError(message) {
    const overlay = document.createElement("div");
    overlay.className = "popup-overlay";

    overlay.innerHTML = `
        <div class="popup"
             onclick="event.stopPropagation()">
            <h3 class="popup-error">Invalid Input</h3>
            <p>${message}</p>
            <button
                class="popup-close"
                onclick="closePopup()">
                OK
            </button>
        </div>
    `;

    overlay.onclick = function() {
        closePopup();
    };

    document.body.appendChild(overlay);
}

function closePopup() {
    const popup = document.querySelector(".popup-overlay");
    if (popup) {
        popup.remove();
    }
}
</script>
"""


# ============================================================
# MAIN DATABASE GUI
# ============================================================

HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Theft & Shredding Database</title>
    {{ style|safe }}
</head>

<body>
<div class="container">
<div class="card">

    <h1>Theft &amp; Shredding Database</h1>{{ clean_html|safe }}

    <div class="subtitle">
        Device 4 — Police Theft Reports & Vehicle Shredding Records
    </div>

    <!-- STATISTICS -->

    <div class="stats">

        <div class="stat-card">
            <div class="stat-title">
                Total Theft Reports
            </div>
            <div class="stat-value">
                {{ theft_total }}
            </div>
        </div>

        <div class="stat-card">
            <div class="stat-title">
                Total Shredding Records
            </div>
            <div class="stat-value">
                {{ shred_total }}
            </div>
        </div>

        <div class="stat-card">
            <div class="stat-title">
                Vehicles with Theft Reports
            </div>
            <div class="stat-value">
                {{ theft_unique }}
            </div>
        </div>

        <div class="stat-card">
            <div class="stat-title">
                Vehicles with Shredding Records
            </div>
            <div class="stat-value">
                {{ shred_unique }}
            </div>
        </div>

    </div>

    <!-- BUTTONS -->

    <div class="buttons">

        <a href="/theft/add"
           class="btn btn-primary">
            + Add Theft Report
        </a>

        <a href="/theft/edit"
           class="btn btn-secondary">
            Edit Theft Report
        </a>

        <a href="/theft/delete"
           class="btn btn-danger">
            Delete Theft Report
        </a>

        <a href="/shred/add"
           class="btn btn-primary">
            + Add Shredding Record
        </a>

        <a href="/shred/edit"
           class="btn btn-secondary">
            Edit Shredding Record
        </a>

        <a href="/shred/delete"
           class="btn btn-danger">
            Delete Shredding Record
        </a>

        <a href="/search" class="btn btn-secondary">Search</a>

        <a href="/cleaned" class="btn btn-secondary">Cleaned Data</a>

        <a href="/cleansing-report" class="btn btn-secondary">Cleansing Report</a>

        <a href="/plates/corrected" class="btn btn-secondary">Corrected Number Plates</a>

        <a href="/plates/dictionary" class="btn btn-secondary">Plate Dictionary</a>

        <a href="/schema" class="btn btn-secondary">Schema &amp; Documents</a>

    </div>


    """ + table_browser.CONTROLS + """
{% if table_view.name == "theft_report" %}
    <!-- THEFT TABLE -->

    <h2>All Theft Reports</h2>

    <div class="table-wrapper">
    <table>

        <tr>
            <th>Theft ID</th>
            <th>Vehicle Number</th>
            <th>Report Date</th>
            <th>Police Station</th>
            <th>FIR Number</th>
            <th>Status</th>
        </tr>

        {% for row in theft_rows %}
        <tr>

            <td><b>{{ row["theft_id"] }}</b></td>

            <td><b>{{ row["vehicle_no"] }}</b></td>

            <td>{{ row["report_date"] }}</td>

            <td>{{ row["police_station"] }}</td>

            <td>{{ row["fir_number"] }}</td>

            <td>
                {% if row["status"] == "Reported" %}
                    <span class="status status-reported">
                        Reported
                    </span>
                {% elif row["status"] == "Recovered" %}
                    <span class="status status-recovered">
                        Recovered
                    </span>
                {% else %}
                    {{ row["status"] }}
                {% endif %}
            </td>

        </tr>
        {% endfor %}

    </table>
    </div>


    {% elif table_view.name == 'shredding_record' %}
    <!-- SHREDDING TABLE -->

    <h2>All Shredding Records</h2>

    <div class="table-wrapper">
    <table>

        <tr>
            <th>Shred ID</th>
            <th>Vehicle Number</th>
            <th>Shred Date</th>
            <th>Scrap Center</th>
            <th>Certificate Number</th>
        </tr>

        {% for row in shred_rows %}
        <tr>

            <td><b>{{ row["shred_id"] }}</b></td>

            <td><b>{{ row["vehicle_no"] }}</b></td>

            <td>{{ row["shred_date"] }}</td>

            <td>{{ row["scrap_center"] }}</td>

            <td>{{ row["certificate_no"] }}</td>

        </tr>
        {% endfor %}

    </table>
    </div>
{% else %}
""" + table_browser.RECORDS + """
{% endif %}

</div>
</div>

{{ popup|safe }}

{% if error %}
<script>
window.onload = function() {
    showError({{ error|tojson }});
};
</script>
{% endif %}

</body>
</html>
"""


# ============================================================
# MAIN DATABASE GUI ROUTE
# ============================================================

@app.route("/", methods=["GET"])
def admin_home():

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    table_view = table_browser.browse(conn, ("theft_report", "shredding_record", "police_station", "scrap_center", "theft_recovery", "shredding_handover"), custom_tables=2)

    cur.execute("SELECT COUNT(*) FROM theft_report")
    theft_total = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT vehicle_no)
        FROM theft_report
    """)
    theft_unique = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM shredding_record")
    shred_total = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(DISTINCT vehicle_no)
        FROM shredding_record
    """)
    shred_unique = cur.fetchone()[0]

    theft_rows = []
    if table_view["name"] == "theft_report":
        cur.execute("""
            SELECT *
            FROM theft_report
            ORDER BY theft_id
        """)
        theft_rows = cur.fetchall()

    shred_rows = []
    if table_view["name"] == "shredding_record":
        cur.execute("""
            SELECT *
            FROM shredding_record
            ORDER BY shred_id
        """)
        shred_rows = cur.fetchall()

    conn.close()

    return render_template_string(
        HTML,
        style=STYLE, table_view=table_view,
        popup=POPUP,
        theft_total=theft_total,
        theft_unique=theft_unique,
        shred_total=shred_total,
        shred_unique=shred_unique,
        theft_rows=theft_rows,
        shred_rows=shred_rows,
        error=None,
        clean_html=cleanser.home_html(request.args.get("clean_notice", ""))
    )


# ============================================================
# ADD THEFT REPORT
# ============================================================

THEFT_ADD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Add Theft Report</title>
    {{ style|safe }}
</head>

<body>
<div class="container">
<div class="card form-card">

    <h1>Add Theft Report</h1>

    <div class="subtitle">
        Add a new police theft report
    </div>

    <form method="POST">

        <div class="form-group">
            <label>Vehicle Number</label>
            <input
                type="text"
                name="vehicle_no"
                value="{{ (values or {}).get('vehicle_no', '') }}"
                placeholder="e.g. MH14ZZ0000"
                required>
        </div>

        <div class="form-group">
            <label>Report Date</label>
            <input
                type="date"
                name="report_date"
                value="{{ (values or {}).get('report_date', '') }}"
                required>
        </div>

        <div class="form-group">
            <label>Police Station</label>
            <input
                type="text"
                name="police_station"
                value="{{ (values or {}).get('police_station', '') }}"
                placeholder="e.g. Shivaji Nagar PS, Pune">
        </div>

        <div class="form-group">
            <label>FIR Number</label>
            <input
                type="text"
                name="fir_number"
                value="{{ (values or {}).get('fir_number', '') }}"
                placeholder="e.g. FIR-2026-4471">
        </div>

        <div class="form-group">
            <label>Status</label>
            <select name="status">
                <option value="Reported" {% if (values or {}).get('status') == 'Reported' %}selected{% endif %}>Reported</option>
                <option value="Recovered" {% if (values or {}).get('status') == 'Recovered' %}selected{% endif %}>Recovered</option>
            </select>
        </div>

        <div class="form-actions">

            <button
                type="submit"
                class="btn btn-primary">
                Add Theft Report
            </button>

            <a href="/"
               class="btn btn-secondary">
                ← Back
            </a>

        </div>

    </form>

</div>
</div>

{{ popup|safe }}

{% if error %}
<script>
window.onload = function() {
    showError({{ error|tojson }});
};
</script>
{% endif %}

</body>
</html>
"""


@app.route("/theft/add", methods=["GET", "POST"])
def add_theft():

    error = None

    if request.method == "POST":

        vehicle = request.form.get(
            "vehicle_no", ""
        ).strip().upper()

        report_date = request.form.get(
            "report_date", ""
        ).strip()

        police_station = request.form.get(
            "police_station", ""
        ).strip()

        fir_number = request.form.get(
            "fir_number", ""
        ).strip()

        status = request.form.get(
            "status", ""
        ).strip()

        # Validation
        if not vehicle:
            error = "Vehicle number is required."

        elif not report_date:
            error = "Report date is required."

        if error:
            return render_template_string(
                THEFT_ADD_HTML,
                style=STYLE,
                popup=POPUP,
                values=request.form.to_dict(),
                error=error
            )

        # Saved as typed; automatic cleansing builds the cleaned copy right after
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO theft_report
            (
                vehicle_no,
                report_date,
                police_station,
                fir_number,
                status
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            vehicle,
            report_date,
            police_station,
            fir_number,
            status
        ))

        conn.commit()
        conn.close()

        return redirect(url_for("admin_home", clean_notice=cleanser.after_change("Theft", cur.lastrowid)))

    return render_template_string(
        THEFT_ADD_HTML,
        style=STYLE,
        popup=POPUP,
        error=None
    )


# ============================================================
# EDIT THEFT — STEP 1
# ============================================================

THEFT_EDIT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Edit Theft Report</title>
    {{ style|safe }}
</head>

<body>
<div class="container">
<div class="card">

    <h1>Edit Theft Report</h1>

    <div class="subtitle">
        Find a vehicle and select one of its theft reports
    </div>

    <form method="POST">

        <div class="form-group">
            <label>Vehicle Number</label>

            <input
                type="text"
                name="vehicle_no"
                placeholder="e.g. MH14ZZ0000"
                value="{{ plate }}"
                required>
        </div>

        <button
            type="submit"
            class="btn btn-primary">
            Find Reports
        </button>

    </form>


    {% if searched and reports %}

    <h2>Reports for {{ plate }}</h2>

    <div class="table-wrapper">
    <table>

        <tr>
            <th>Theft ID</th>
            <th>Report Date</th>
            <th>Police Station</th>
            <th>FIR Number</th>
            <th>Status</th>
        </tr>

        {% for r in reports %}
        <tr>

            <td><b>{{ r["theft_id"] }}</b></td>
            <td>{{ r["report_date"] }}</td>
            <td>{{ r["police_station"] }}</td>
            <td>{{ r["fir_number"] }}</td>
            <td>{{ r["status"] }}</td>

        </tr>
        {% endfor %}

    </table>
    </div>

    <div class="info-box">
        Enter the <b>Theft ID</b> from the table
        to select the report you want to edit.
    </div>

    <form method="POST"
          action="/theft/edit/select"
          class="form-card">

        <input
            type="hidden"
            name="vehicle_no"
            value="{{ plate }}">

        <div class="form-group">
            <label>Theft ID</label>

            <input
                type="number"
                name="theft_id"
                placeholder="e.g. 1"
                required>
        </div>

        <button
            type="submit"
            class="btn btn-primary">
            Select Report
        </button>

    </form>

    {% endif %}

    <br>

    <a href="/"
       class="btn btn-secondary">
        ← Back
    </a>

</div>
</div>

{{ popup|safe }}

{% if error %}
<script>
window.onload = function() {
    showError({{ error|tojson }});
};
</script>
{% endif %}

</body>
</html>
"""


@app.route("/theft/edit", methods=["GET", "POST"])
def edit_theft():

    reports = []
    plate = ""
    searched = False
    error = None

    if request.method == "POST":

        searched = True

        plate = request.form.get(
            "vehicle_no", ""
        ).strip().upper()

        if not plate:
            error = "Please enter a vehicle number."

        else:
            reports = query(
                "theft_report",
                plate
            )

            if not reports:
                error = (
                    f"No theft reports found for "
                    f"vehicle {plate}."
                )

    return render_template_string(
        THEFT_EDIT_HTML,
        style=STYLE,
        popup=POPUP,
        reports=reports,
        plate=plate,
        searched=searched,
        error=error
    )


# ============================================================
# EDIT THEFT — STEP 2
# ============================================================

@app.route("/theft/edit/select", methods=["POST"])
def select_theft():

    plate = request.form.get(
        "vehicle_no", ""
    ).strip().upper()

    theft_id = request.form.get(
        "theft_id", ""
    ).strip()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM theft_report
        WHERE theft_id = ?
        AND vehicle_no = ?
    """, (
        theft_id,
        plate
    ))

    report = cur.fetchone()

    if not report:

        cur.execute("""
            SELECT *
            FROM theft_report
            WHERE vehicle_no = ?
            ORDER BY theft_id
        """, (plate,))

        reports = cur.fetchall()
        conn.close()

        return render_template_string(
            THEFT_EDIT_HTML,
            style=STYLE,
            popup=POPUP,
            reports=reports,
            plate=plate,
            searched=True,
            error=(
                "Invalid Theft ID. "
                "Please enter a Theft ID shown in the table."
            )
        )

    conn.close()

    return render_template_string(
        THEFT_EDIT_FORM_HTML,
        style=STYLE,
        popup=POPUP,
        report=report,
        plate=plate,
        error=None
    )


# ============================================================
# EDIT THEFT — STEP 3
# ============================================================

THEFT_EDIT_FORM_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Edit Theft Report</title>
    {{ style|safe }}
</head>

<body>
<div class="container">
<div class="card form-card">

    <h1>Edit Theft Report #{{ report["theft_id"] }}</h1>

    <div class="subtitle">
        Vehicle: <b>{{ plate }}</b>
    </div>

    <div class="info-box">

        <b>Theft ID:</b>
        {{ report["theft_id"] }}

        <br><br>

        <b>Vehicle:</b>
        {{ report["vehicle_no"] }}

    </div>

    <form method="POST"
          action="/theft/edit/save">

        <input
            type="hidden"
            name="vehicle_no"
            value="{{ plate }}">

        <input
            type="hidden"
            name="theft_id"
            value="{{ report["theft_id"] }}">

        <div class="form-group">
            <label>Report Date</label>

            <input
                type="date"
                name="report_date"
                value="{{ report["report_date"] }}"
                required>
        </div>

        <div class="form-group">
            <label>Police Station</label>

            <input
                type="text"
                name="police_station"
                value="{{ report["police_station"] or '' }}">
        </div>

        <div class="form-group">
            <label>FIR Number</label>

            <input
                type="text"
                name="fir_number"
                value="{{ report["fir_number"] or '' }}">
        </div>

        <div class="form-group">
            <label>Status</label>

            <select name="status">

                <option
                    value="Reported"
                    {% if report["status"] == "Reported" %}
                    selected
                    {% endif %}>
                    Reported
                </option>

                <option
                    value="Recovered"
                    {% if report["status"] == "Recovered" %}
                    selected
                    {% endif %}>
                    Recovered
                </option>

            </select>
        </div>

        <div class="form-actions">

            <button
                type="submit"
                class="btn btn-primary">
                Save Changes
            </button>

            <a href="/theft/edit"
               class="btn btn-secondary">
                ← Back
            </a>

        </div>

    </form>

</div>
</div>

{{ popup|safe }}

{% if error %}
<script>
window.onload = function() {
    showError({{ error|tojson }});
};
</script>
{% endif %}

</body>
</html>
"""


@app.route("/theft/edit/save", methods=["POST"])
def save_theft_edit():

    plate = request.form.get(
        "vehicle_no", ""
    ).strip().upper()

    theft_id = request.form.get(
        "theft_id", ""
    ).strip()

    report_date = request.form.get(
        "report_date", ""
    ).strip()

    police_station = request.form.get(
        "police_station", ""
    ).strip()

    fir_number = request.form.get(
        "fir_number", ""
    ).strip()

    status = request.form.get(
        "status", ""
    ).strip()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM theft_report
        WHERE theft_id = ?
        AND vehicle_no = ?
    """, (
        theft_id,
        plate
    ))

    report = cur.fetchone()

    if not report:

        conn.close()

        return render_template_string(
            THEFT_EDIT_HTML,
            style=STYLE,
            popup=POPUP,
            reports=[],
            plate=plate,
            searched=True,
            error="Invalid Theft ID."
        )

    if not report_date:

        conn.close()

        return render_template_string(
            THEFT_EDIT_FORM_HTML,
            style=STYLE,
            popup=POPUP,
            report=report,
            plate=plate,
            error="Report date is required."
        )

    before = cleanser.decision("Theft", theft_id)

    cur.execute("""
        UPDATE theft_report
        SET
            report_date = ?,
            police_station = ?,
            fir_number = ?,
            status = ?
        WHERE theft_id = ?
        AND vehicle_no = ?
    """, (
        report_date,
        police_station,
        fir_number,
        status,
        theft_id,
        plate
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("admin_home", clean_notice=cleanser.after_change("Theft", theft_id, before)))


# ============================================================
# DELETE THEFT
# ============================================================

THEFT_DELETE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Delete Theft Report</title>
    {{ style|safe }}
</head>

<body>
<div class="container">
<div class="card">

    <h1>Delete Theft Report</h1>

    <div class="subtitle">
        Find a vehicle and select the theft report to delete
    </div>

    <form method="POST">

        <div class="form-group">
            <label>Vehicle Number</label>

            <input
                type="text"
                name="vehicle_no"
                placeholder="e.g. MH14ZZ0000"
                value="{{ plate }}"
                required>
        </div>

        <button
            type="submit"
            class="btn btn-danger">
            Find Reports
        </button>

    </form>


    {% if searched and reports %}

    <h2>Reports for {{ plate }}</h2>

    <div class="table-wrapper">
    <table>

        <tr>
            <th>Theft ID</th>
            <th>Report Date</th>
            <th>Police Station</th>
            <th>FIR Number</th>
            <th>Status</th>
        </tr>

        {% for r in reports %}
        <tr>
            <td><b>{{ r["theft_id"] }}</b></td>
            <td>{{ r["report_date"] }}</td>
            <td>{{ r["police_station"] }}</td>
            <td>{{ r["fir_number"] }}</td>
            <td>{{ r["status"] }}</td>
        </tr>
        {% endfor %}

    </table>
    </div>

    <div class="info-box">
        Enter the <b>Theft ID</b> of the report
        you want to delete.
    </div>

    <form method="POST"
          action="/theft/delete/confirm"
          class="form-card">

        <input
            type="hidden"
            name="vehicle_no"
            value="{{ plate }}">

        <div class="form-group">
            <label>Theft ID</label>

            <input
                type="number"
                name="theft_id"
                placeholder="e.g. 1"
                required>
        </div>

        <button
            type="submit"
            class="btn btn-danger">
            Delete Report
        </button>

    </form>

    {% endif %}

    <br>

    <a href="/"
       class="btn btn-secondary">
        ← Back
    </a>

</div>
</div>

{{ popup|safe }}

{% if error %}
<script>
window.onload = function() {
    showError({{ error|tojson }});
};
</script>
{% endif %}

</body>
</html>
"""


@app.route("/theft/delete", methods=["GET", "POST"])
def delete_theft():

    reports = []
    plate = ""
    searched = False
    error = None

    if request.method == "POST":

        searched = True

        plate = request.form.get(
            "vehicle_no", ""
        ).strip().upper()

        if not plate:
            error = "Please enter a vehicle number."

        else:
            reports = query(
                "theft_report",
                plate
            )

            if not reports:
                error = (
                    f"No theft reports found for "
                    f"vehicle {plate}."
                )

    return render_template_string(
        THEFT_DELETE_HTML,
        style=STYLE,
        popup=POPUP,
        reports=reports,
        plate=plate,
        searched=searched,
        error=error
    )


@app.route("/theft/delete/confirm", methods=["POST"])
def confirm_theft_delete():

    plate = request.form.get(
        "vehicle_no", ""
    ).strip().upper()

    theft_id = request.form.get(
        "theft_id", ""
    ).strip()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM theft_report
        WHERE theft_id = ?
        AND vehicle_no = ?
    """, (
        theft_id,
        plate
    ))

    report = cur.fetchone()

    if not report:

        cur.execute("""
            SELECT *
            FROM theft_report
            WHERE vehicle_no = ?
            ORDER BY theft_id
        """, (plate,))

        reports = cur.fetchall()
        conn.close()

        return render_template_string(
            THEFT_DELETE_HTML,
            style=STYLE,
            popup=POPUP,
            reports=reports,
            plate=plate,
            searched=True,
            error=(
                "Invalid Theft ID. "
                "Please enter a Theft ID shown in the table."
            )
        )

    cur.execute("""
        DELETE FROM theft_report
        WHERE theft_id = ?
        AND vehicle_no = ?
    """, (
        theft_id,
        plate
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("admin_home", clean_notice=cleanser.after_change("Theft")))


# ============================================================
# ADD SHREDDING RECORD
# ============================================================

SHRED_ADD_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Add Shredding Record</title>
    {{ style|safe }}
</head>

<body>
<div class="container">
<div class="card form-card">

    <h1>Add Shredding Record</h1>

    <div class="subtitle">
        Add a new vehicle shredding record
    </div>

    <form method="POST">

        <div class="form-group">
            <label>Vehicle Number</label>

            <input
                type="text"
                name="vehicle_no"
                value="{{ (values or {}).get('vehicle_no', '') }}"
                placeholder="e.g. TN09EF7777"
                required>
        </div>

        <div class="form-group">
            <label>Shred Date</label>

            <input
                type="date"
                name="shred_date"
                value="{{ (values or {}).get('shred_date', '') }}"
                required>
        </div>

        <div class="form-group">
            <label>Scrap Center</label>

            <input
                type="text"
                name="scrap_center"
                value="{{ (values or {}).get('scrap_center', '') }}"
                placeholder="e.g. GreenScrap Recyclers, Chennai">
        </div>

        <div class="form-group">
            <label>Certificate Number</label>

            <input
                type="text"
                name="certificate_no"
                value="{{ (values or {}).get('certificate_no', '') }}"
                placeholder="e.g. SCR-2026-0099">
        </div>

        <div class="form-actions">

            <button
                type="submit"
                class="btn btn-primary">
                Add Shredding Record
            </button>

            <a href="/"
               class="btn btn-secondary">
                ← Back
            </a>

        </div>

    </form>

</div>
</div>

{{ popup|safe }}

{% if error %}
<script>
window.onload = function() {
    showError({{ error|tojson }});
};
</script>
{% endif %}

</body>
</html>
"""


@app.route("/shred/add", methods=["GET", "POST"])
def add_shred():

    error = None

    if request.method == "POST":

        vehicle = request.form.get(
            "vehicle_no", ""
        ).strip().upper()

        shred_date = request.form.get(
            "shred_date", ""
        ).strip()

        scrap_center = request.form.get(
            "scrap_center", ""
        ).strip()

        certificate_no = request.form.get(
            "certificate_no", ""
        ).strip()

        # Validation
        if not vehicle:
            error = "Vehicle number is required."

        elif not shred_date:
            error = "Shred date is required."

        if error:
            return render_template_string(
                SHRED_ADD_HTML,
                style=STYLE,
                popup=POPUP,
                values=request.form.to_dict(),
                error=error
            )

        # Saved as typed; automatic cleansing builds the cleaned copy right after
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO shredding_record
            (
                vehicle_no,
                shred_date,
                scrap_center,
                certificate_no
            )
            VALUES (?, ?, ?, ?)
        """, (
            vehicle,
            shred_date,
            scrap_center,
            certificate_no
        ))

        conn.commit()
        conn.close()

        return redirect(url_for("admin_home", clean_notice=cleanser.after_change("Shredding", cur.lastrowid)))

    return render_template_string(
        SHRED_ADD_HTML,
        style=STYLE,
        popup=POPUP,
        error=None
    )


# ============================================================
# EDIT SHREDDING — STEP 1
# ============================================================

SHRED_EDIT_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Edit Shredding Record</title>
    {{ style|safe }}
</head>

<body>
<div class="container">
<div class="card">

    <h1>Edit Shredding Record</h1>

    <div class="subtitle">
        Find a vehicle and select one of its shredding records
    </div>

    <form method="POST">

        <div class="form-group">
            <label>Vehicle Number</label>

            <input
                type="text"
                name="vehicle_no"
                placeholder="e.g. TN09EF7777"
                value="{{ plate }}"
                required>
        </div>

        <button
            type="submit"
            class="btn btn-primary">
            Find Records
        </button>

    </form>


    {% if searched and records %}

    <h2>Records for {{ plate }}</h2>

    <div class="table-wrapper">
    <table>

        <tr>
            <th>Shred ID</th>
            <th>Shred Date</th>
            <th>Scrap Center</th>
            <th>Certificate Number</th>
        </tr>

        {% for r in records %}
        <tr>

            <td><b>{{ r["shred_id"] }}</b></td>
            <td>{{ r["shred_date"] }}</td>
            <td>{{ r["scrap_center"] }}</td>
            <td>{{ r["certificate_no"] }}</td>

        </tr>
        {% endfor %}

    </table>
    </div>

    <div class="info-box">
        Enter the <b>Shred ID</b> from the table
        to select the record you want to edit.
    </div>

    <form method="POST"
          action="/shred/edit/select"
          class="form-card">

        <input
            type="hidden"
            name="vehicle_no"
            value="{{ plate }}">

        <div class="form-group">
            <label>Shred ID</label>

            <input
                type="number"
                name="shred_id"
                placeholder="e.g. 1"
                required>
        </div>

        <button
            type="submit"
            class="btn btn-primary">
            Select Record
        </button>

    </form>

    {% endif %}

    <br>

    <a href="/"
       class="btn btn-secondary">
        ← Back
    </a>

</div>
</div>

{{ popup|safe }}

{% if error %}
<script>
window.onload = function() {
    showError({{ error|tojson }});
};
</script>
{% endif %}

</body>
</html>
"""


@app.route("/shred/edit", methods=["GET", "POST"])
def edit_shred():

    records = []
    plate = ""
    searched = False
    error = None

    if request.method == "POST":

        searched = True

        plate = request.form.get(
            "vehicle_no", ""
        ).strip().upper()

        if not plate:
            error = "Please enter a vehicle number."

        else:
            records = query(
                "shredding_record",
                plate
            )

            if not records:
                error = (
                    f"No shredding records found for "
                    f"vehicle {plate}."
                )

    return render_template_string(
        SHRED_EDIT_HTML,
        style=STYLE,
        popup=POPUP,
        records=records,
        plate=plate,
        searched=searched,
        error=error
    )


# ============================================================
# EDIT SHREDDING — STEP 2
# ============================================================

@app.route("/shred/edit/select", methods=["POST"])
def select_shred():

    plate = request.form.get(
        "vehicle_no", ""
    ).strip().upper()

    shred_id = request.form.get(
        "shred_id", ""
    ).strip()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM shredding_record
        WHERE shred_id = ?
        AND vehicle_no = ?
    """, (
        shred_id,
        plate
    ))

    record = cur.fetchone()

    if not record:

        cur.execute("""
            SELECT *
            FROM shredding_record
            WHERE vehicle_no = ?
            ORDER BY shred_id
        """, (plate,))

        records = cur.fetchall()
        conn.close()

        return render_template_string(
            SHRED_EDIT_HTML,
            style=STYLE,
            popup=POPUP,
            records=records,
            plate=plate,
            searched=True,
            error=(
                "Invalid Shred ID. "
                "Please enter a Shred ID shown in the table."
            )
        )

    conn.close()

    return render_template_string(
        SHRED_EDIT_FORM_HTML,
        style=STYLE,
        popup=POPUP,
        record=record,
        plate=plate,
        error=None
    )


# ============================================================
# EDIT SHREDDING — STEP 3
# ============================================================

SHRED_EDIT_FORM_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Edit Shredding Record</title>
    {{ style|safe }}
</head>

<body>
<div class="container">
<div class="card form-card">

    <h1>Edit Shredding Record #{{ record["shred_id"] }}</h1>

    <div class="subtitle">
        Vehicle: <b>{{ plate }}</b>
    </div>

    <div class="info-box">

        <b>Shred ID:</b>
        {{ record["shred_id"] }}

        <br><br>

        <b>Vehicle:</b>
        {{ record["vehicle_no"] }}

    </div>

    <form method="POST"
          action="/shred/edit/save">

        <input
            type="hidden"
            name="vehicle_no"
            value="{{ plate }}">

        <input
            type="hidden"
            name="shred_id"
            value="{{ record["shred_id"] }}">

        <div class="form-group">
            <label>Shred Date</label>

            <input
                type="date"
                name="shred_date"
                value="{{ record["shred_date"] }}"
                required>
        </div>

        <div class="form-group">
            <label>Scrap Center</label>

            <input
                type="text"
                name="scrap_center"
                value="{{ record["scrap_center"] or '' }}">
        </div>

        <div class="form-group">
            <label>Certificate Number</label>

            <input
                type="text"
                name="certificate_no"
                value="{{ record["certificate_no"] or '' }}">
        </div>

        <div class="form-actions">

            <button
                type="submit"
                class="btn btn-primary">
                Save Changes
            </button>

            <a href="/shred/edit"
               class="btn btn-secondary">
                ← Back
            </a>

        </div>

    </form>

</div>
</div>

{{ popup|safe }}

{% if error %}
<script>
window.onload = function() {
    showError({{ error|tojson }});
};
</script>
{% endif %}

</body>
</html>
"""


@app.route("/shred/edit/save", methods=["POST"])
def save_shred_edit():

    plate = request.form.get(
        "vehicle_no", ""
    ).strip().upper()

    shred_id = request.form.get(
        "shred_id", ""
    ).strip()

    shred_date = request.form.get(
        "shred_date", ""
    ).strip()

    scrap_center = request.form.get(
        "scrap_center", ""
    ).strip()

    certificate_no = request.form.get(
        "certificate_no", ""
    ).strip()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM shredding_record
        WHERE shred_id = ?
        AND vehicle_no = ?
    """, (
        shred_id,
        plate
    ))

    record = cur.fetchone()

    if not record:

        conn.close()

        return render_template_string(
            SHRED_EDIT_HTML,
            style=STYLE,
            popup=POPUP,
            records=[],
            plate=plate,
            searched=True,
            error="Invalid Shred ID."
        )

    if not shred_date:

        conn.close()

        return render_template_string(
            SHRED_EDIT_FORM_HTML,
            style=STYLE,
            popup=POPUP,
            record=record,
            plate=plate,
            error="Shred date is required."
        )

    before = cleanser.decision("Shredding", shred_id)

    cur.execute("""
        UPDATE shredding_record
        SET
            shred_date = ?,
            scrap_center = ?,
            certificate_no = ?
        WHERE shred_id = ?
        AND vehicle_no = ?
    """, (
        shred_date,
        scrap_center,
        certificate_no,
        shred_id,
        plate
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("admin_home", clean_notice=cleanser.after_change("Shredding", shred_id, before)))


# ============================================================
# DELETE SHREDDING
# ============================================================

SHRED_DELETE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Delete Shredding Record</title>
    {{ style|safe }}
</head>

<body>
<div class="container">
<div class="card">

    <h1>Delete Shredding Record</h1>

    <div class="subtitle">
        Find a vehicle and select the shredding record to delete
    </div>

    <form method="POST">

        <div class="form-group">
            <label>Vehicle Number</label>

            <input
                type="text"
                name="vehicle_no"
                placeholder="e.g. TN09EF7777"
                value="{{ plate }}"
                required>
        </div>

        <button
            type="submit"
            class="btn btn-danger">
            Find Records
        </button>

    </form>


    {% if searched and records %}

    <h2>Records for {{ plate }}</h2>

    <div class="table-wrapper">
    <table>

        <tr>
            <th>Shred ID</th>
            <th>Shred Date</th>
            <th>Scrap Center</th>
            <th>Certificate Number</th>
        </tr>

        {% for r in records %}
        <tr>
            <td><b>{{ r["shred_id"] }}</b></td>
            <td>{{ r["shred_date"] }}</td>
            <td>{{ r["scrap_center"] }}</td>
            <td>{{ r["certificate_no"] }}</td>
        </tr>
        {% endfor %}

    </table>
    </div>

    <div class="info-box">
        Enter the <b>Shred ID</b> of the record
        you want to delete.
    </div>

    <form method="POST"
          action="/shred/delete/confirm"
          class="form-card">

        <input
            type="hidden"
            name="vehicle_no"
            value="{{ plate }}">

        <div class="form-group">
            <label>Shred ID</label>

            <input
                type="number"
                name="shred_id"
                placeholder="e.g. 1"
                required>
        </div>

        <button
            type="submit"
            class="btn btn-danger">
            Delete Record
        </button>

    </form>

    {% endif %}

    <br>

    <a href="/"
       class="btn btn-secondary">
        ← Back
    </a>

</div>
</div>

{{ popup|safe }}

{% if error %}
<script>
window.onload = function() {
    showError({{ error|tojson }});
};
</script>
{% endif %}

</body>
</html>
"""


@app.route("/shred/delete", methods=["GET", "POST"])
def delete_shred():

    records = []
    plate = ""
    searched = False
    error = None

    if request.method == "POST":

        searched = True

        plate = request.form.get(
            "vehicle_no", ""
        ).strip().upper()

        if not plate:
            error = "Please enter a vehicle number."

        else:
            records = query(
                "shredding_record",
                plate
            )

            if not records:
                error = (
                    f"No shredding records found for "
                    f"vehicle {plate}."
                )

    return render_template_string(
        SHRED_DELETE_HTML,
        style=STYLE,
        popup=POPUP,
        records=records,
        plate=plate,
        searched=searched,
        error=error
    )


@app.route("/shred/delete/confirm", methods=["POST"])
def confirm_shred_delete():

    plate = request.form.get(
        "vehicle_no", ""
    ).strip().upper()

    shred_id = request.form.get(
        "shred_id", ""
    ).strip()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM shredding_record
        WHERE shred_id = ?
        AND vehicle_no = ?
    """, (
        shred_id,
        plate
    ))

    record = cur.fetchone()

    if not record:

        cur.execute("""
            SELECT *
            FROM shredding_record
            WHERE vehicle_no = ?
            ORDER BY shred_id
        """, (plate,))

        records = cur.fetchall()
        conn.close()

        return render_template_string(
            SHRED_DELETE_HTML,
            style=STYLE,
            popup=POPUP,
            records=records,
            plate=plate,
            searched=True,
            error=(
                "Invalid Shred ID. "
                "Please enter a Shred ID shown in the table."
            )
        )

    cur.execute("""
        DELETE FROM shredding_record
        WHERE shred_id = ?
        AND vehicle_no = ?
    """, (
        shred_id,
        plate
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("admin_home", clean_notice=cleanser.after_change("Shredding")))


# ============================================================
# API — HEALTH
# ============================================================

@app.get("/health")
def health():
    return jsonify({
        "service": "THEFT_SHRED_DB",
        "status": "up"
    })


# ============================================================
# API — THEFT LOOKUP
# ============================================================

@app.get("/theft/<path:plate>")
def get_theft(plate):
    return jsonify(
        query(
            "theft_report",
            plate.upper()
        )
    )


# ============================================================
# API — SHREDDING LOOKUP
# ============================================================

@app.get("/shred/<path:plate>")
def get_shred(plate):
    return jsonify(
        query(
            "shredding_record",
            plate.upper()
        )
    )


@app.get("/records")
def all_theft_shredding_records():
    """Return current theft and shredding records for candidate discovery."""
    return jsonify({
        "theft_reports": get_theft_reports(),
        "shredding_records": get_shredding_records(),
    })


# ============================================================
# START SERVER
# ============================================================

@app.put("/records/theft/<int:record_id>/plate")
def update_theft_plate_api(record_id):
    payload = request.get_json(silent=True) or {}
    expected, new = str(payload.get("expected_plate", "")), str(payload.get("new_plate", "")).strip().upper()
    if not expected or not new:
        return jsonify({"updated": False, "error": "expected_plate and new_plate are required"}), 400
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("UPDATE theft_report SET vehicle_no=? WHERE theft_id=? AND vehicle_no=?", (new, record_id, expected))
        conn.commit()
        if cur.rowcount != 1:
            return jsonify({"updated": False, "error": "record missing or plate changed since review"}), 409
        return jsonify({"updated": True, "source": "Theft/Shredding", "record_type": "theft", "record_id": record_id, "old_plate": expected, "new_plate": new})
    finally:
        conn.close()

@app.put("/records/shredding/<int:record_id>/plate")
def update_shredding_plate_api(record_id):
    payload = request.get_json(silent=True) or {}
    expected, new = str(payload.get("expected_plate", "")), str(payload.get("new_plate", "")).strip().upper()
    if not expected or not new:
        return jsonify({"updated": False, "error": "expected_plate and new_plate are required"}), 400
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("UPDATE shredding_record SET vehicle_no=? WHERE shred_id=? AND vehicle_no=?", (new, record_id, expected))
        conn.commit()
        if cur.rowcount != 1:
            return jsonify({"updated": False, "error": "record missing or plate changed since review"}), 409
        return jsonify({"updated": True, "source": "Theft/Shredding", "record_type": "shredding", "record_id": record_id, "old_plate": expected, "new_plate": new})
    finally:
        conn.close()


def init_db():
    """Main table (with its seed records) plus the Phase 3 related tables, also on an existing database."""
    _init_main()
    conn = sqlite3.connect(DB_PATH)
    try:
        theft_context.ensure(conn)
        conn.commit()
    finally:
        conn.close()


# Phase 3: federated context discovery over this database's FK-PK joins, and search of its own documents.
context_search.register(app, "Theft/Shredding", lambda: DB_PATH, theft_context.discover,
                        context_search.DocIndex(os.path.join(os.path.dirname(os.path.abspath(__file__)), "documents"), "Theft/Shredding"), theft_context.JOINS,
                        count=theft_context.count, profile=theft_context.profile)


if __name__ == "__main__":

    init_db()
    desk.start()      # first start: the existing plates seed the dictionaries
    cleanser.start()  # clean now, then pick up pending work / command-line edits in the background

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--port",
        type=int,
        default=5004
    )

    parser.add_argument(
        "--host",
        default="0.0.0.0"
    )

    args = parser.parse_args()

    print(
        f"Theft/Shredding service (Device 4) "
        f"running on http://{args.host}:{args.port}"
    )

    app.run(
        host=args.host,
        port=args.port
    )
