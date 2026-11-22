import sqlite3
import os
import argparse
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import plate_entry_client as entry
import cleansing
import context_search
import insurance_context
import table_browser
import peers

from flask import (
    Flask,
    jsonify,
    request,
    redirect,
    url_for,
    render_template_string
)


DB_PATH = os.path.join(
    os.path.dirname(__file__),
    "insurance.db"
)

app = Flask(__name__)


# This database's plate dictionary (plate_entry_client.py), in its own file next to the database.
desk = entry.PlateDesk(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plate_dictionary.db"), [
    entry.Source("Insurance", lambda: DB_PATH, "policy", "policy_id", "vehicle_reg_no", "policy"),
])
desk.register(app)

# Automatic data cleansing (cleansing.py): policy_clean next to the raw table, using every device's data.
cleanser = cleansing.Cleanser("Insurance", lambda: DB_PATH, desk.book, peers.PEERS)
cleanser.register(app)


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def _init_main():

    # Check for the table, not the file: an empty insurance.db (e.g. re-created by a
    # request after the file was deleted) must still get its table and seed rows.
    conn = sqlite3.connect(DB_PATH)

    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='policy'").fetchone():
        conn.close()
        return

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE policy (

            policy_id       INTEGER PRIMARY KEY AUTOINCREMENT,

            vehicle_reg_no  TEXT NOT NULL,

            insurer_name    TEXT NOT NULL,

            policy_number   TEXT NOT NULL,

            policy_type     TEXT,

            start_date      TEXT NOT NULL,

            end_date        TEXT NOT NULL,

            premium_amount  REAL,

            status          TEXT

        );
    """)

    rows = [

        (
            "MH12AB1234",
            "ICICI Lombard",
            "ICL-2025-889231",
            "Comprehensive",
            "2025-06-01",
            "2026-05-31",
            8200.0,
            "Active"
        ),

        (
            "DL8CAF5555",
            "HDFC Ergo",
            "HDE-2024-112233",
            "Third-Party",
            "2024-01-15",
            "2025-01-14",
            3400.0,
            "Lapsed"
        ),

        (
            "KA05MZ9090",
            "Bajaj Allianz",
            "BAJ-2026-004411",
            "Comprehensive",
            "2026-02-10",
            "2027-02-09",
            1500.0,
            "Active"
        ),

        (
            "TN09EF7777",
            "New India Assurance",
            "NIA-2023-556677",
            "Third-Party",
            "2023-03-01",
            "2024-02-29",
            2900.0,
            "Lapsed"
        ),

        (
            "GJ01XY0001",
            "United India Ins.",
            "UII-2026-778899",
            "Comprehensive",
            "2026-01-01",
            "2026-12-31",
            15600.0,
            "Active"
        ),

        (
            "RJ14PQ6543",
            "SBI General",
            "SBI-2025-334455",
            "Comprehensive",
            "2025-09-01",
            "2026-08-31",
            7100.0,
            "Active"
        ),

        (
            "MH14ZZ0000",
            "ICICI Lombard",
            "ICL-2023-990011",
            "Third-Party",
            "2023-05-01",
            "2024-04-30",
            3200.0,
            "Cancelled"
        ),

        (
            "PB10AA2222",
            "Reliance General",
            "REL-2026-221100",
            "Comprehensive",
            "2026-04-01",
            "2027-03-31",
            6600.0,
            "Active"
        ),

        (
            "WB20BB3333",
            "Tata AIG",
            "TAT-2025-887766",
            "Third-Party",
            "2025-05-15",
            "2026-05-14",
            2100.0,
            "Active"
        ),

        (
            "KL07LM8899",
            "Bajaj Allianz",
            "BAJ-2026-556600",
            "Comprehensive",
            "2026-03-01",
            "2027-02-28",
            9800.0,
            "Active"
        ),

        (
            "HR26NN4545",
            "HDFC Ergo",
            "HDE-2026-778800",
            "Comprehensive",
            "2026-06-01",
            "2027-05-31",
            8800.0,
            "Active"
        )

    ]

    cur.executemany("""
        INSERT INTO policy
        (
            vehicle_reg_no,
            insurer_name,
            policy_number,
            policy_type,
            start_date,
            end_date,
            premium_amount,
            status
        )

        VALUES (?, ?, ?, ?, ?, ?, ?, ?)

    """, rows)

    conn.commit()

    conn.close()


# ============================================================
# API QUERY
# ============================================================

def query(plate):

    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM policy
        WHERE vehicle_reg_no = ?
        ORDER BY policy_id
    """, (plate,))

    rows = [
        dict(r)
        for r in cur.fetchall()
    ]

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

    font-family:
        Arial,
        Helvetica,
        sans-serif;

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

    box-shadow:
        0 4px 18px
        rgba(0,0,0,0.08);

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

    grid-template-columns:
        repeat(3, 1fr);

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


.status-active {

    background: #dcfce7;

    color: #166534;

}


.status-lapsed {

    background: #fef3c7;

    color: #92400e;

}


.status-cancelled {

    background: #fee2e2;

    color: #991b1b;

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

    box-shadow:
        0 10px 35px
        rgba(0,0,0,0.25);

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

@media (max-width: 800px) {

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

    const overlay =
        document.createElement("div");

    overlay.className =
        "popup-overlay";


    overlay.innerHTML = `

        <div class="popup"
             onclick="event.stopPropagation()">

            <h3 class="popup-error">
                Invalid Input
            </h3>

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

    const popup =
        document.querySelector(".popup-overlay");

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

    <title>Insurance Database</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card">

    <h1>Insurance Database</h1>
    {{ clean_html|safe }}

    <div class="subtitle">

        Device 2 — Motor Insurance Records

    </div>


    <!-- STATISTICS -->

    <div class="stats">

        <div class="stat-card">

            <div class="stat-title">
                Total Policies
            </div>

            <div class="stat-value">
                {{ total }}
            </div>

        </div>


        <div class="stat-card">

            <div class="stat-title">
                Unique Vehicles
            </div>

            <div class="stat-value">
                {{ unique }}
            </div>

        </div>


        <div class="stat-card">

            <div class="stat-title">
                Active Policies
            </div>

            <div class="stat-value">
                {{ active }}
            </div>

        </div>

    </div>


    <!-- BUTTONS -->

    <div class="buttons">

        <a
            href="/add"
            class="btn btn-primary">

            + Add Policy

        </a>


        <a
            href="/edit"
            class="btn btn-secondary">

            Edit Policy

        </a>


        <a
            href="/delete"
            class="btn btn-danger">

            Delete Policy

        </a>


        <a href="/search" class="btn btn-secondary">Search</a>

        <a href="/cleaned" class="btn btn-secondary">Cleaned Data</a>

        <a href="/cleansing-report" class="btn btn-secondary">Cleansing Report</a>

        <a href="/plates/corrected" class="btn btn-secondary">Corrected Number Plates</a>

        <a href="/plates/dictionary" class="btn btn-secondary">Plate Dictionary</a>

        <a href="/schema" class="btn btn-secondary">Schema &amp; Documents</a>

    </div>


    <!-- DATABASE TABLE -->

    """ + table_browser.CONTROLS + """
{% if table_view.name == "policy" %}
    <h2>All Insurance Policies</h2>


    <div class="table-wrapper">

    <table>

        <tr>

            <th>Policy ID</th>

            <th>Vehicle Registration</th>

            <th>Insurer</th>

            <th>Policy Number</th>

            <th>Policy Type</th>

            <th>Start Date</th>

            <th>End Date</th>

            <th>Premium</th>

            <th>Status</th>

        </tr>


        {% for row in rows %}

        <tr>

            <td>
                <b>{{ row["policy_id"] }}</b>
            </td>

            <td>
                <b>{{ row["vehicle_reg_no"] }}</b>
            </td>

            <td>
                {{ row["insurer_name"] }}
            </td>

            <td>
                {{ row["policy_number"] }}
            </td>

            <td>
                {{ row["policy_type"] }}
            </td>

            <td>
                {{ row["start_date"] }}
            </td>

            <td>
                {{ row["end_date"] }}
            </td>

            <td>
                ₹{{ "%.2f"|format(row["premium_amount"] or 0) }}
            </td>

            <td>

                {% if row["status"] == "Active" %}

                    <span class="status status-active">
                        Active
                    </span>

                {% elif row["status"] == "Cancelled" %}

                    <span class="status status-cancelled">
                        Cancelled
                    </span>

                {% else %}

                    <span class="status status-lapsed">
                        {{ row["status"] }}
                    </span>

                {% endif %}

            </td>

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
# MAIN DATABASE GUI
# ============================================================

@app.route("/", methods=["GET"])
def admin_home():

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    table_view = table_browser.browse(conn, ("policy", "insurer", "policy_holder", "claim", "premium_payment"))

    # Total policies
    cur.execute("SELECT COUNT(*) FROM policy")
    total = cur.fetchone()[0]

    # Unique vehicles
    cur.execute(
        "SELECT COUNT(DISTINCT vehicle_reg_no) FROM policy"
    )
    unique = cur.fetchone()[0]

    # Active policies
    cur.execute(
        "SELECT COUNT(*) FROM policy WHERE status = 'Active'"
    )
    active = cur.fetchone()[0]

    # All policies
    rows = []
    if table_view["name"] == "policy":
        cur.execute("""
            SELECT *
            FROM policy
            ORDER BY policy_id
        """)

        rows = cur.fetchall()

    conn.close()

    return render_template_string(
        HTML,
        style=STYLE, table_view=table_view,
        popup=POPUP,
        total=total,
        unique=unique,
        active=active,
        rows=rows,
        error=None,
        clean_html=cleanser.home_html(request.args.get("clean_notice", ""))
    )

# ============================================================
# ADD POLICY
# ============================================================

ADD_HTML = """
<!DOCTYPE html>

<html>

<head>

    <title>Add Policy</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card form-card">

    <h1>Add Policy</h1>

    <div class="subtitle">

        Add a new insurance policy record

    </div>


    <form method="POST">


        <div class="form-group">

            <label>
                Vehicle Registration Number
            </label>

            <input
                type="text"
                name="vehicle_reg_no"
                value="{{ (values or {}).get('vehicle_reg_no', '') }}"
                placeholder="e.g. MH12AB1234"
                required
            >

        </div>


        <div class="form-group">

            <label>
                Insurer Name
            </label>

            <input
                type="text"
                name="insurer_name"
                value="{{ (values or {}).get('insurer_name', '') }}"
                placeholder="e.g. ICICI Lombard"
                required
            >

        </div>


        <div class="form-group">

            <label>
                Policy Number
            </label>

            <input
                type="text"
                name="policy_number"
                value="{{ (values or {}).get('policy_number', '') }}"
                placeholder="e.g. ICL-2026-123456"
                required
            >

        </div>


        <div class="form-group">

            <label>
                Policy Type
            </label>

            <select name="policy_type">

                <option value="Comprehensive" {% if (values or {}).get('policy_type') == 'Comprehensive' %}selected{% endif %}>
                    Comprehensive
                </option>

                <option value="Third-Party" {% if (values or {}).get('policy_type') == 'Third-Party' %}selected{% endif %}>
                    Third-Party
                </option>

            </select>

        </div>


        <div class="form-group">

            <label>
                Start Date
            </label>

            <input
                type="date"
                name="start_date"
                value="{{ (values or {}).get('start_date', '') }}"
                required
            >

        </div>


        <div class="form-group">

            <label>
                End Date
            </label>

            <input
                type="date"
                name="end_date"
                value="{{ (values or {}).get('end_date', '') }}"
                required
            >

        </div>


        <div class="form-group">

            <label>
                Premium Amount
            </label>

            <input
                type="number"
                step="0.01"
                name="premium_amount"
                value="{{ (values or {}).get('premium_amount', '') }}"
                placeholder="e.g. 8200"
            >

        </div>


        <div class="form-group">

            <label>
                Status
            </label>

            <select name="status">

                <option value="Active" {% if (values or {}).get('status') == 'Active' %}selected{% endif %}>
                    Active
                </option>

                <option value="Lapsed" {% if (values or {}).get('status') == 'Lapsed' %}selected{% endif %}>
                    Lapsed
                </option>

                <option value="Cancelled" {% if (values or {}).get('status') == 'Cancelled' %}selected{% endif %}>
                    Cancelled
                </option>

            </select>

        </div>


        <div class="form-actions">

            <button
                type="submit"
                class="btn btn-primary">

                Add Policy

            </button>


            <a
                href="/"
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


@app.route("/add", methods=["GET", "POST"])
def add_policy():

    error = None

    if request.method == "POST":

        vehicle = request.form.get(
            "vehicle_reg_no", ""
        ).strip().upper()

        insurer = request.form.get(
            "insurer_name", ""
        ).strip()

        policy_number = request.form.get(
            "policy_number", ""
        ).strip()

        policy_type = request.form.get(
            "policy_type", ""
        ).strip()

        start_date = request.form.get(
            "start_date", ""
        ).strip()

        end_date = request.form.get(
            "end_date", ""
        ).strip()

        premium = request.form.get(
            "premium_amount", ""
        ).strip()

        status = request.form.get(
            "status", ""
        ).strip()

        values = request.form.to_dict()


        # Validation

        if not vehicle:

            error = "Vehicle registration number is required."

        elif not insurer:

            error = "Insurer name is required."

        elif not policy_number:

            error = "Policy number is required."

        elif not start_date or not end_date:

            error = "Start date and end date are required."


        if error:

            return render_template_string(
                ADD_HTML,
                style=STYLE,
                popup=POPUP,
                values=values,
                error=error
            )


        try:

            premium_value = (
                float(premium)
                if premium
                else None
            )

        except ValueError:

            return render_template_string(
                ADD_HTML,
                style=STYLE,
                popup=POPUP,
                values=values,
                error="Premium amount must be a valid number."
            )


        # Saved as typed; automatic cleansing builds the cleaned copy right after
        conn = sqlite3.connect(DB_PATH)

        cur = conn.cursor()


        cur.execute("""
            INSERT INTO policy
            (
                vehicle_reg_no,
                insurer_name,
                policy_number,
                policy_type,
                start_date,
                end_date,
                premium_amount,
                status
            )

            VALUES (?, ?, ?, ?, ?, ?, ?, ?)

        """, (
            vehicle,
            insurer,
            policy_number,
            policy_type,
            start_date,
            end_date,
            premium_value,
            status
        ))


        conn.commit()

        conn.close()


        return redirect(url_for("admin_home", clean_notice=cleanser.after_change("Insurance", cur.lastrowid)))


    return render_template_string(
        ADD_HTML,
        style=STYLE,
        popup=POPUP,
        error=None
    )


# ============================================================
# EDIT — STEP 1: FIND VEHICLE
# ============================================================

EDIT_HTML = """
<!DOCTYPE html>

<html>

<head>

    <title>Edit Policy</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card">

    <h1>Edit Policy</h1>

    <div class="subtitle">

        Find a vehicle and select one of its policies

    </div>


    <form method="POST">

        <div class="form-group">

            <label>
                Vehicle Registration Number
            </label>

            <input
                type="text"
                name="vehicle_reg_no"
                placeholder="e.g. MH12AB1234"
                value="{{ plate }}"
                required
            >

        </div>


        <button
            type="submit"
            class="btn btn-primary">

            Find Policies

        </button>

    </form>


    {% if searched and policies %}

        <h2>
            Policies for {{ plate }}
        </h2>


        <div class="table-wrapper">

        <table>

            <tr>

                <th>Policy ID</th>

                <th>Policy Number</th>

                <th>Insurer</th>

                <th>Type</th>

                <th>Start</th>

                <th>End</th>

                <th>Premium</th>

                <th>Status</th>

            </tr>


            {% for p in policies %}

            <tr>

                <td>
                    <b>{{ p["policy_id"] }}</b>
                </td>

                <td>
                    {{ p["policy_number"] }}
                </td>

                <td>
                    {{ p["insurer_name"] }}
                </td>

                <td>
                    {{ p["policy_type"] }}
                </td>

                <td>
                    {{ p["start_date"] }}
                </td>

                <td>
                    {{ p["end_date"] }}
                </td>

                <td>
                    ₹{{ "%.2f"|format(p["premium_amount"] or 0) }}
                </td>

                <td>
                    {{ p["status"] }}
                </td>

            </tr>

            {% endfor %}

        </table>

        </div>


        <div class="info-box">

            Enter the <b>Policy ID</b> from the table
            to select the policy you want to edit.

        </div>


        <form
            method="POST"
            action="/edit/select"
            class="form-card">

            <input
                type="hidden"
                name="vehicle_reg_no"
                value="{{ plate }}"
            >


            <div class="form-group">

                <label>
                    Policy ID
                </label>

                <input
                    type="number"
                    name="policy_id"
                    placeholder="e.g. 7"
                    required
                >

            </div>


            <button
                type="submit"
                class="btn btn-primary">

                Select Policy

            </button>

        </form>

    {% endif %}


    <br>

    <a
        href="/"
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


# ============================================================
# EDIT — FIND VEHICLE
# ============================================================

@app.route("/edit", methods=["GET", "POST"])
def edit_policy():

    policies = []

    plate = ""

    searched = False

    error = None


    if request.method == "POST":

        searched = True

        plate = request.form.get(
            "vehicle_reg_no", ""
        ).strip().upper()


        if not plate:

            error = (
                "Please enter a vehicle registration number."
            )

        else:

            conn = sqlite3.connect(DB_PATH)

            conn.row_factory = sqlite3.Row

            cur = conn.cursor()


            cur.execute("""
                SELECT *
                FROM policy
                WHERE vehicle_reg_no = ?
                ORDER BY policy_id
            """, (plate,))


            policies = cur.fetchall()

            conn.close()


            if not policies:

                error = (
                    f"No policies found for vehicle {plate}."
                )


    return render_template_string(
        EDIT_HTML,
        style=STYLE,
        popup=POPUP,
        policies=policies,
        plate=plate,
        searched=searched,
        error=error
    )


# ============================================================
# EDIT — STEP 2: SELECT POLICY
# ============================================================

@app.route("/edit/select", methods=["POST"])
def select_policy():

    plate = request.form.get(
        "vehicle_reg_no", ""
    ).strip().upper()

    policy_id = request.form.get(
        "policy_id", ""
    ).strip()


    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    cur = conn.cursor()


    cur.execute("""
        SELECT *
        FROM policy
        WHERE policy_id = ?
        AND vehicle_reg_no = ?
    """, (
        policy_id,
        plate
    ))


    policy = cur.fetchone()


    if not policy:

        cur.execute("""
            SELECT *
            FROM policy
            WHERE vehicle_reg_no = ?
            ORDER BY policy_id
        """, (plate,))

        policies = cur.fetchall()

        conn.close()


        return render_template_string(
            EDIT_HTML,
            style=STYLE,
            popup=POPUP,
            policies=policies,
            plate=plate,
            searched=True,
            error=(
                "Invalid Policy ID. "
                "Please enter a Policy ID shown in the table."
            )
        )


    conn.close()


    return render_template_string(
        EDIT_FORM_HTML,
        style=STYLE,
        popup=POPUP,
        policy=policy,
        plate=plate,
        error=None
    )


# ============================================================
# EDIT — STEP 3: ACTUAL EDIT FORM
# ============================================================

EDIT_FORM_HTML = """
<!DOCTYPE html>

<html>

<head>

    <title>Edit Policy</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card form-card">

    <h1>
        Edit Policy #{{ policy["policy_id"] }}
    </h1>

    <div class="subtitle">

        Vehicle:
        <b>{{ plate }}</b>

    </div>


    <div class="info-box">

        <b>Policy ID:</b>
        {{ policy["policy_id"] }}

        <br><br>

        <b>Vehicle:</b>
        {{ policy["vehicle_reg_no"] }}

    </div>


    <form method="POST" action="/edit/save">


        <input
            type="hidden"
            name="vehicle_reg_no"
            value="{{ plate }}"
        >


        <input
            type="hidden"
            name="policy_id"
            value="{{ policy["policy_id"] }}"
        >


        <div class="form-group">

            <label>
                Insurer Name
            </label>

            <input
                type="text"
                name="insurer_name"
                value="{{ policy["insurer_name"] }}"
                required
            >

        </div>


        <div class="form-group">

            <label>
                Policy Number
            </label>

            <input
                type="text"
                name="policy_number"
                value="{{ policy["policy_number"] }}"
                required
            >

        </div>


        <div class="form-group">

            <label>
                Policy Type
            </label>

            <select name="policy_type">

                <option
                    value="Comprehensive"
                    {% if policy["policy_type"] == "Comprehensive" %}
                    selected
                    {% endif %}
                >
                    Comprehensive
                </option>

                <option
                    value="Third-Party"
                    {% if policy["policy_type"] == "Third-Party" %}
                    selected
                    {% endif %}
                >
                    Third-Party
                </option>

            </select>

        </div>


        <div class="form-group">

            <label>
                Start Date
            </label>

            <input
                type="date"
                name="start_date"
                value="{{ policy["start_date"] }}"
                required
            >

        </div>


        <div class="form-group">

            <label>
                End Date
            </label>

            <input
                type="date"
                name="end_date"
                value="{{ policy["end_date"] }}"
                required
            >

        </div>


        <div class="form-group">

            <label>
                Premium Amount
            </label>

            <input
                type="number"
                step="0.01"
                name="premium_amount"
                value="{{ policy["premium_amount"] or '' }}"
            >

        </div>


        <div class="form-group">

            <label>
                Status
            </label>

            <select name="status">

                <option
                    value="Active"
                    {% if policy["status"] == "Active" %}
                    selected
                    {% endif %}
                >
                    Active
                </option>

                <option
                    value="Lapsed"
                    {% if policy["status"] == "Lapsed" %}
                    selected
                    {% endif %}
                >
                    Lapsed
                </option>

                <option
                    value="Cancelled"
                    {% if policy["status"] == "Cancelled" %}
                    selected
                    {% endif %}
                >
                    Cancelled
                </option>

            </select>

        </div>


        <div class="form-actions">

            <button
                type="submit"
                class="btn btn-primary">

                Save Changes

            </button>


            <a
                href="/edit"
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


# ============================================================
# EDIT — SAVE
# ============================================================

@app.route("/edit/save", methods=["POST"])
def save_edit():

    plate = request.form.get(
        "vehicle_reg_no", ""
    ).strip().upper()

    policy_id = request.form.get(
        "policy_id", ""
    ).strip()


    insurer = request.form.get(
        "insurer_name", ""
    ).strip()

    policy_number = request.form.get(
        "policy_number", ""
    ).strip()

    policy_type = request.form.get(
        "policy_type", ""
    ).strip()

    start_date = request.form.get(
        "start_date", ""
    ).strip()

    end_date = request.form.get(
        "end_date", ""
    ).strip()

    premium = request.form.get(
        "premium_amount", ""
    ).strip()

    status = request.form.get(
        "status", ""
    ).strip()


    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    cur = conn.cursor()


    # Verify policy

    cur.execute("""
        SELECT *
        FROM policy
        WHERE policy_id = ?
        AND vehicle_reg_no = ?
    """, (
        policy_id,
        plate
    ))


    policy = cur.fetchone()


    if not policy:

        conn.close()

        return render_template_string(
            EDIT_HTML,
            style=STYLE,
            popup=POPUP,
            policies=[],
            plate=plate,
            searched=True,
            error="Invalid Policy ID."
        )


    # Validate

    if not insurer:

        conn.close()

        return render_template_string(
            EDIT_FORM_HTML,
            style=STYLE,
            popup=POPUP,
            policy=policy,
            plate=plate,
            error="Insurer name cannot be empty."
        )


    if not policy_number:

        conn.close()

        return render_template_string(
            EDIT_FORM_HTML,
            style=STYLE,
            popup=POPUP,
            policy=policy,
            plate=plate,
            error="Policy number cannot be empty."
        )


    if not start_date or not end_date:

        conn.close()

        return render_template_string(
            EDIT_FORM_HTML,
            style=STYLE,
            popup=POPUP,
            policy=policy,
            plate=plate,
            error="Start date and end date are required."
        )


    try:

        premium_value = (
            float(premium)
            if premium
            else None
        )

    except ValueError:

        conn.close()

        return render_template_string(
            EDIT_FORM_HTML,
            style=STYLE,
            popup=POPUP,
            policy=policy,
            plate=plate,
            error="Premium amount must be a valid number."
        )


    before = cleanser.decision("Insurance", policy_id)

    # Update selected policy only

    cur.execute("""
        UPDATE policy

        SET
            insurer_name = ?,
            policy_number = ?,
            policy_type = ?,
            start_date = ?,
            end_date = ?,
            premium_amount = ?,
            status = ?

        WHERE policy_id = ?

        AND vehicle_reg_no = ?

    """, (
        insurer,
        policy_number,
        policy_type,
        start_date,
        end_date,
        premium_value,
        status,
        policy_id,
        plate
    ))


    conn.commit()

    conn.close()


    return redirect(
        url_for("admin_home", clean_notice=cleanser.after_change("Insurance", policy_id, before))
    )


# ============================================================
# DELETE — STEP 1
# ============================================================

DELETE_HTML = """
<!DOCTYPE html>

<html>

<head>

    <title>Delete Policy</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card">

    <h1>Delete Policy</h1>

    <div class="subtitle">

        Find a vehicle and select the policy to delete

    </div>


    <form method="POST">

        <div class="form-group">

            <label>
                Vehicle Registration Number
            </label>

            <input
                type="text"
                name="vehicle_reg_no"
                placeholder="e.g. MH12AB1234"
                value="{{ plate }}"
                required
            >

        </div>


        <button
            type="submit"
            class="btn btn-danger">

            Find Policies

        </button>

    </form>


    {% if searched and policies %}

        <h2>
            Policies for {{ plate }}
        </h2>


        <div class="table-wrapper">

        <table>

            <tr>

                <th>Policy ID</th>

                <th>Policy Number</th>

                <th>Insurer</th>

                <th>Type</th>

                <th>Start</th>

                <th>End</th>

                <th>Status</th>

            </tr>


            {% for p in policies %}

            <tr>

                <td>
                    <b>{{ p["policy_id"] }}</b>
                </td>

                <td>
                    {{ p["policy_number"] }}
                </td>

                <td>
                    {{ p["insurer_name"] }}
                </td>

                <td>
                    {{ p["policy_type"] }}
                </td>

                <td>
                    {{ p["start_date"] }}
                </td>

                <td>
                    {{ p["end_date"] }}
                </td>

                <td>
                    {{ p["status"] }}
                </td>

            </tr>

            {% endfor %}

        </table>

        </div>


        <div class="info-box">

            Enter the <b>Policy ID</b> of the policy
            you want to delete.

        </div>


        <form
            method="POST"
            action="/delete/confirm"
            class="form-card">

            <input
                type="hidden"
                name="vehicle_reg_no"
                value="{{ plate }}"
            >


            <div class="form-group">

                <label>
                    Policy ID
                </label>

                <input
                    type="number"
                    name="policy_id"
                    placeholder="e.g. 7"
                    required
                >

            </div>


            <button
                type="submit"
                class="btn btn-danger">

                Select Policy

            </button>

        </form>

    {% endif %}


    <br>

    <a
        href="/"
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


@app.route("/delete", methods=["GET", "POST"])
def delete_policy():

    policies = []

    plate = ""

    searched = False

    error = None


    if request.method == "POST":

        searched = True

        plate = request.form.get(
            "vehicle_reg_no", ""
        ).strip().upper()


        conn = sqlite3.connect(DB_PATH)

        conn.row_factory = sqlite3.Row

        cur = conn.cursor()


        cur.execute("""
            SELECT *
            FROM policy
            WHERE vehicle_reg_no = ?
            ORDER BY policy_id
        """, (plate,))


        policies = cur.fetchall()

        conn.close()


        if not policies:

            error = (
                f"No policies found for vehicle {plate}."
            )


    return render_template_string(
        DELETE_HTML,
        style=STYLE,
        popup=POPUP,
        policies=policies,
        plate=plate,
        searched=searched,
        error=error
    )


# ============================================================
# DELETE — CONFIRM
# ============================================================

@app.route("/delete/confirm", methods=["POST"])
def confirm_delete():

    plate = request.form.get(
        "vehicle_reg_no", ""
    ).strip().upper()

    policy_id = request.form.get(
        "policy_id", ""
    ).strip()


    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    cur = conn.cursor()


    cur.execute("""
        SELECT *
        FROM policy
        WHERE policy_id = ?
        AND vehicle_reg_no = ?
    """, (
        policy_id,
        plate
    ))


    policy = cur.fetchone()


    if not policy:

        conn.close()

        return render_template_string(
            DELETE_HTML,
            style=STYLE,
            popup=POPUP,
            policies=[],
            plate=plate,
            searched=True,
            error=(
                "Invalid Policy ID. "
                "Please enter a Policy ID shown in the table."
            )
        )


    cur.execute("""
        DELETE FROM policy

        WHERE policy_id = ?

        AND vehicle_reg_no = ?

    """, (
        policy_id,
        plate
    ))


    conn.commit()

    conn.close()


    return redirect(
        url_for("admin_home", clean_notice=cleanser.after_change("Insurance"))
    )


# ============================================================
# API — HEALTH
# ============================================================

@app.get("/health")
def health():

    return jsonify({
        "service": "INSURANCE_DB",
        "status": "up"
    })


# ============================================================
# API — VEHICLE LOOKUP
# ============================================================

@app.get("/vehicle/<path:plate>")
def get_vehicle(plate):

    return jsonify(
        query(
            plate.upper()
        )
    )


@app.get("/records")
def all_policy_records():
    """Return current policies for cross-service candidate discovery."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM policy ORDER BY policy_id"
        ).fetchall()]
        return jsonify(rows)
    finally:
        conn.close()


# ============================================================
# START SERVER
# ============================================================

@app.put("/records/policy/<int:record_id>/plate")
def update_policy_plate_api(record_id):
    payload = request.get_json(silent=True) or {}
    expected, new = str(payload.get("expected_plate", "")), str(payload.get("new_plate", "")).strip().upper()
    if not expected or not new:
        return jsonify({"updated": False, "error": "expected_plate and new_plate are required"}), 400
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("UPDATE policy SET vehicle_reg_no=? WHERE policy_id=? AND vehicle_reg_no=?", (new, record_id, expected))
        conn.commit()
        if cur.rowcount != 1:
            return jsonify({"updated": False, "error": "record missing or plate changed since review"}), 409
        return jsonify({"updated": True, "source": "Insurance", "record_type": "policy", "record_id": record_id, "old_plate": expected, "new_plate": new})
    finally:
        conn.close()


def init_db():
    """Main table (with its seed records) plus the Phase 3 related tables, also on an existing database."""
    _init_main()
    conn = sqlite3.connect(DB_PATH)
    try:
        insurance_context.ensure(conn)
        conn.commit()
    finally:
        conn.close()


# Phase 3: federated context discovery over this database's FK-PK joins, and search of its own documents.
context_search.register(app, "Insurance", lambda: DB_PATH, insurance_context.discover,
                        context_search.DocIndex(os.path.join(os.path.dirname(os.path.abspath(__file__)), "documents"), "Insurance"), insurance_context.JOINS,
                        count=insurance_context.count, profile=insurance_context.profile)


if __name__ == "__main__":

    init_db()
    desk.start()      # first start: the existing plates seed the dictionary
    cleanser.start()  # clean now, then pick up pending work / command-line edits in the background

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--port",
        type=int,
        default=5002
    )

    parser.add_argument(
        "--host",
        default="0.0.0.0"
    )

    args = parser.parse_args()


    print(
        f"Insurance service (Device 2) "
        f"running on http://{args.host}:{args.port}"
    )


    app.run(
        host=args.host,
        port=args.port
    )
