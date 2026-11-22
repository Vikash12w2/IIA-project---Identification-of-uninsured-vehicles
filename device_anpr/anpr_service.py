import sqlite3
import os
import re
import uuid
import argparse
import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import plate_entry_client as entry
import plate_ocr
import cleansing
import context_search
import anpr_context
import table_browser
import peers
from flask import (
    Flask,
    jsonify,
    request,
    redirect,
    url_for,
    render_template_string,
    send_from_directory
)
from datetime import datetime


DB_PATH = os.path.join(os.path.dirname(__file__), "anpr.db")

# Plate images uploaded with a capture (OCR). Kept next to the database, never inside it:
# plate_images/capture_<id>.png, and plate_images/pending/<token>.png until the capture is saved.
PLATE_IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "plate_images")

app = Flask(__name__)


# This database's plate dictionary (plate_entry_client.py), in its own file next to the database.
desk = entry.PlateDesk(os.path.join(os.path.dirname(os.path.abspath(__file__)), "plate_dictionary.db"), [
    entry.Source("ANPR", lambda: DB_PATH, "vehicle_capture", "capture_id", "plate_number", "capture",
                 context=lambda r: {"color": r.get("vehicle_color"), "vehicle_type": r.get("vehicle_type"),
                                    "make_model": r.get("make_model_seen")},
                 image_url=lambda r: capture_image_url(r["capture_id"])),
])
desk.register(app)

# Automatic data cleansing (cleansing.py): vehicle_capture_clean next to the raw table, using every device's data.
# ANPR is also the device that reports flagged vehicles to MoT.
cleanser = cleansing.Cleanser("ANPR", lambda: DB_PATH, desk.book, peers.PEERS)
cleanser.register(app)


def save_pending_image(data):
    """Keep an uploaded plate image (as PNG) until its capture is saved; returns its token."""
    ok, png = plate_ocr.cv2.imencode(".png", plate_ocr.decode(data))
    token = uuid.uuid4().hex
    os.makedirs(os.path.join(PLATE_IMAGE_DIR, "pending"), exist_ok=True)
    with open(os.path.join(PLATE_IMAGE_DIR, "pending", token + ".png"), "wb") as f:
        f.write(png.tobytes())
    return token


def pending_image_path(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return None
    path = os.path.join(PLATE_IMAGE_DIR, "pending", token + ".png")
    return path if os.path.exists(path) else None


def capture_image_url(capture_id):
    """URL of the plate image kept with a capture, or None (captures typed by hand have no image)."""
    name = f"capture_{capture_id}.png"
    return f"/plate-images/{name}" if os.path.exists(os.path.join(PLATE_IMAGE_DIR, name)) else None


app.jinja_env.globals.update(ocr_available=plate_ocr.available, image_url=capture_image_url)


@app.get("/plate-images/<path:name>")
def plate_image(name):
    return send_from_directory(PLATE_IMAGE_DIR, name)


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def _init_main():
    # Check for the table, not the file: an empty anpr.db (e.g. re-created by a
    # request after the file was deleted) must still get its table and seed rows.
    conn = sqlite3.connect(DB_PATH)
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='vehicle_capture'").fetchone():
        conn.close()
        return

    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE vehicle_capture (
        capture_id      INTEGER PRIMARY KEY AUTOINCREMENT,
        plate_number    TEXT NOT NULL,
        capture_ts      TEXT NOT NULL,
        location        TEXT NOT NULL,
        vehicle_color   TEXT,
        vehicle_type    TEXT,
        make_model_seen TEXT,
        camera_id       TEXT
    );
    """)

    rows = [
        ("MH12AB1234", "2026-09-01 08:12:03", "NH48 Toll Plaza, Pune",
         "White", "Car", "Maruti Swift", "CAM-014"),

        ("MH12AB1234", "2026-09-05 18:40:11", "Katraj Chowk, Pune",
         "White", "Car", "Maruti Swift", "CAM-002"),

        ("DL8CAF5555", "2026-09-02 09:05:44", "Ring Road, Delhi",
         "Black", "Car", "Hyundai Creta", "CAM-101"),

        ("KA05MZ9090", "2026-09-03 07:22:10", "Silk Board, Bengaluru",
         "Red", "Bike", "Royal Enfield", "CAM-055"),

        ("KA05MZ9090", "2026-09-10 21:15:00", "Electronic City, Bengaluru",
         "Red", "Bike", "Royal Enfield", "CAM-058"),

        ("UP16CD4321", "2026-09-04 12:00:00", "Hazratganj, Lucknow",
         "Silver", "Car", "Honda City", "CAM-201"),

        ("TN09EF7777", "2026-09-06 15:30:27", "Anna Salai, Chennai",
         "Blue", "Car", "Tata Nexon", "CAM-303"),

        ("GJ01XY0001", "2026-09-07 06:45:12", "SG Highway, Ahmedabad",
         "White", "Truck", "Tata LPT 1613", "CAM-410"),

        ("RJ14PQ6543", "2026-09-08 19:12:55", "MI Road, Jaipur",
         "Grey", "Car", "Mahindra XUV300", "CAM-511"),

        ("MH14ZZ0000", "2026-09-09 10:10:10", "Shivaji Nagar, Pune",
         "Black", "Car", "Skoda Rapid", "CAM-016"),

        ("PB10AA2222", "2026-09-11 14:14:14", "Mall Road, Ludhiana",
         "Red", "Car", "Hyundai i20", "CAM-620"),

        ("WB20BB3333", "2026-09-12 11:11:11", "Park Street, Kolkata",
         "White", "Car", "Honda Amaze", "CAM-701"),

        ("MH12ST0007", "2026-09-13 08:08:08", "FC Road, Pune",
         "Blue", "Bike", "TVS Apache", "CAM-020"),

        ("KL07LM8899", "2026-09-13 17:45:00", "MG Road, Kochi",
         "Black", "Car", "Toyota Innova", "CAM-812"),

        ("HR26NN4545", "2026-09-14 09:00:00", "Cyber City, Gurugram",
         "White", "Car", "Kia Seltos", "CAM-905"),
    ]

    cur.executemany("""
        INSERT INTO vehicle_capture
        (
            plate_number,
            capture_ts,
            location,
            vehicle_color,
            vehicle_type,
            make_model_seen,
            camera_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)

    conn.commit()
    conn.close()


# ============================================================
# DATABASE QUERY
# ============================================================

def query(plate=None):

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    if plate:
        cur.execute("""
            SELECT *
            FROM vehicle_capture
            WHERE plate_number = ?
            ORDER BY capture_id
        """, (plate,))
    else:
        cur.execute("""
            SELECT DISTINCT plate_number
            FROM vehicle_capture
        """)

    rows = [dict(r) for r in cur.fetchall()]

    conn.close()

    return rows


# ============================================================
# EXISTING API ENDPOINTS
# ============================================================

@app.get("/health")
def health():
    return jsonify({
        "service": "ANPR_DB",
        "status": "up"
    })


@app.get("/vehicle/<path:plate>")
def get_vehicle(plate):
    return jsonify(query(plate.upper()))


@app.get("/plates")
def all_plates():
    """
    Returns every distinct plate ever seen on the road.
    Used by the client device to know which vehicles to evaluate.
    """

    rows = query(None)

    return jsonify([
        r["plate_number"]
        for r in rows
    ])


@app.get("/records")
def all_capture_records():
    """Return current ANPR captures for cross-service candidate discovery."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM vehicle_capture ORDER BY capture_id"
        ).fetchall()]
        return jsonify(rows)
    finally:
        conn.close()


# ============================================================
# COMMON HTML / CSS
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
    background: #f4f6f8;
    color: #222;
}

.container {
    max-width: 1450px;
    margin: auto;
}

.card {
    background: white;
    border-radius: 14px;
    padding: 28px;
    box-shadow: 0 3px 14px rgba(0,0,0,0.08);
}

h1 {
    margin-top: 0;
    margin-bottom: 8px;
    font-size: 30px;
}

h2 {
    margin-top: 30px;
}

.subtitle {
    color: #666;
    margin-bottom: 25px;
}

/* ---------------------------------------------------------
   STAT BOXES
--------------------------------------------------------- */

.stats {
    display: flex;
    gap: 18px;
    margin: 20px 0;
}

.stat {
    background: #f1f3f5;
    padding: 16px 22px;
    border-radius: 10px;
    min-width: 190px;
}

.stat-title {
    font-size: 13px;
    color: #666;
    margin-bottom: 5px;
}

.stat-value {
    font-size: 25px;
    font-weight: bold;
}

/* ---------------------------------------------------------
   BUTTONS
--------------------------------------------------------- */

.buttons {
    display: flex;
    gap: 10px;
    margin: 25px 0;
    flex-wrap: wrap;
}

.btn {
    display: inline-block;
    padding: 11px 18px;
    border: none;
    border-radius: 7px;
    text-decoration: none;
    cursor: pointer;
    font-size: 14px;
    font-weight: 600;
    background: #333;
    color: white;
}

.btn:hover {
    opacity: 0.88;
}

.btn-primary {
    background: #2563eb;
}

.btn-danger {
    background: #dc2626;
}

.btn-secondary {
    background: #6b7280;
}

/* ---------------------------------------------------------
   TABLE
--------------------------------------------------------- */

.table-wrapper {
    overflow-x: auto;
}

table {
    width: 100%;
    border-collapse: collapse;
    background: white;
}

th,
td {
    padding: 12px 13px;
    border-bottom: 1px solid #e5e7eb;
    text-align: left;
    white-space: nowrap;
}

th {
    background: #252525;
    color: white;
    font-size: 13px;
}

td {
    font-size: 14px;
}

tr:hover {
    background: #f8fafc;
}

/* ---------------------------------------------------------
   FORM
--------------------------------------------------------- */

.form-card {
    max-width: 650px;
}

.form-group {
    margin-bottom: 18px;
}

label {
    display: block;
    font-weight: 600;
    margin-bottom: 7px;
}

input,
select {
    width: 100%;
    padding: 11px 12px;
    border: 1px solid #cbd5e1;
    border-radius: 7px;
    font-size: 15px;
    outline: none;
}

input:focus,
select:focus {
    border-color: #2563eb;
}

.form-actions {
    display: flex;
    gap: 10px;
    margin-top: 25px;
}

/* ---------------------------------------------------------
   ERROR POPUP
--------------------------------------------------------- */

.popup-overlay {
    display: none;
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.40);
    z-index: 9999;
    justify-content: center;
    align-items: center;
}

.popup-box {
    background: white;
    width: min(430px, 90%);
    padding: 28px;
    border-radius: 13px;
    text-align: center;
    box-shadow: 0 12px 40px rgba(0,0,0,0.25);
    animation: popupIn 0.15s ease-out;
}

.popup-icon {
    font-size: 34px;
    margin-bottom: 8px;
}

.popup-title {
    font-size: 21px;
    font-weight: bold;
    margin-bottom: 9px;
}

.popup-message {
    color: #555;
    line-height: 1.5;
}

@keyframes popupIn {
    from {
        transform: scale(0.94);
        opacity: 0;
    }
    to {
        transform: scale(1);
        opacity: 1;
    }
}

/* ---------------------------------------------------------
   CAPTURE LIST
--------------------------------------------------------- */

.capture-table {
    margin-top: 20px;
}

.capture-select {
    margin-top: 25px;
}

.info-box {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    padding: 15px;
    border-radius: 8px;
    margin: 18px 0;
}

.warning-box {
    background: #fff7ed;
    border: 1px solid #fed7aa;
    color: #9a3412;
    padding: 15px;
    border-radius: 8px;
    margin: 18px 0;
}

</style>
"""


# ============================================================
# POPUP HTML + JAVASCRIPT
# ============================================================

POPUP = """
<div id="errorPopup" class="popup-overlay">

    <div class="popup-box" onclick="event.stopPropagation()">

        <div class="popup-icon">⚠️</div>

        <div class="popup-title">
            Invalid Input
        </div>

        <div id="errorMessage" class="popup-message">
        </div>

    </div>

</div>

<script>

function showError(message) {

    document.getElementById("errorMessage").textContent = message;

    document.getElementById("errorPopup").style.display = "flex";
}


function closeError() {

    document.getElementById("errorPopup").style.display = "none";
}


document.getElementById("errorPopup").addEventListener(
    "click",
    function() {
        closeError();
    }
);

</script>
"""


# ============================================================
# MAIN DATABASE PAGE
# ============================================================

HTML = """
<!DOCTYPE html>
<html>

<head>

    <title>ANPR Database</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card">

    <h1>ANPR Database</h1>
    {{ clean_html|safe }}

    <div class="subtitle">
        Vehicle capture records stored on Device 1
    </div>


    <div class="stats">

        <div class="stat">

            <div class="stat-title">
                Total Captures
            </div>

            <div class="stat-value">
                {{ total }}
            </div>

        </div>


        <div class="stat">

            <div class="stat-title">
                Unique Vehicles
            </div>

            <div class="stat-value">
                {{ unique }}
            </div>

        </div>

    </div>


    <div class="buttons">

        <a class="btn btn-primary" href="/add">
            + Add Capture
        </a>

        <a class="btn" href="/edit">
            ✎ Edit Capture
        </a>

        <a class="btn btn-danger" href="/delete">
            🗑 Delete Vehicle
        </a>

        <a class="btn" href="/search">Search</a>

        <a class="btn" href="/cleaned">Cleaned Data</a>

        <a class="btn" href="/cleansing-report">Cleansing Report</a>

        <a class="btn" href="/plates/corrected">Corrected Number Plates</a>

        <a class="btn" href="/plates/dictionary">Plate Dictionary</a>

        <a class="btn" href="/schema">Schema &amp; Documents</a>

    </div>


    """ + table_browser.CONTROLS + """
{% if table_view.name == "vehicle_capture" %}
    <div class="table-wrapper">

        <table>

            <tr>

                <th>ID</th>
                <th>Plate Number</th>
                <th>Capture Time</th>
                <th>Location</th>
                <th>Color</th>
                <th>Vehicle Type</th>
                <th>Make / Model</th>
                <th>Camera</th>
                <th>Plate Image</th>

            </tr>


            {% for row in rows %}

            <tr>

                <td>{{ row["capture_id"] }}</td>

                <td>
                    <b>{{ row["plate_number"] }}</b>
                </td>

                <td>{{ row["capture_ts"] }}</td>

                <td>{{ row["location"] }}</td>

                <td>{{ row["vehicle_color"] }}</td>

                <td>{{ row["vehicle_type"] }}</td>

                <td>{{ row["make_model_seen"] }}</td>

                <td>{{ row["camera_id"] }}</td>

                <td>
                    {% set img = image_url(row["capture_id"]) %}
                    {% if img %}<a href="{{ img }}" target="_blank"><img src="{{ img }}" alt="plate image" style="height:28px;border:1px solid #ccd"></a>{% endif %}
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

</body>

</html>
"""


@app.route("/")
def admin_home():

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    table_view = table_browser.browse(conn, ("vehicle_capture", "location", "camera", "capture_violation", "camera_maintenance"))

    # Get all capture records
    rows = []
    if table_view["name"] == "vehicle_capture":
        cur.execute("""
            SELECT *
            FROM vehicle_capture
            ORDER BY capture_id
        """)

        rows = cur.fetchall()

    # Get total number of capture records
    cur.execute("""
        SELECT COUNT(*)
        FROM vehicle_capture
    """)

    total = cur.fetchone()[0]

    # Get total number of unique vehicles
    cur.execute("""
        SELECT COUNT(DISTINCT plate_number)
        FROM vehicle_capture
    """)

    unique = cur.fetchone()[0]

    conn.close()

    return render_template_string(
        HTML,
        style=STYLE, table_view=table_view,
        rows=rows,
        total=total,
        unique=unique,
        clean_html=cleanser.home_html(request.args.get("clean_notice", ""))
    )


# ============================================================
# ADD CAPTURE
# ============================================================

ADD_HTML = """
<!DOCTYPE html>
<html>

<head>

    <title>Add Capture</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card form-card">

    <h1>Add Capture</h1>

    <div class="subtitle">
        Add a new ANPR vehicle observation
    </div>


    <form method="POST" enctype="multipart/form-data">


        <div class="form-group">

            <label>
                Plate Number
            </label>

            {% set image_mode = values.get('plate_source') == 'image' %}

            <div style="margin-bottom:10px">
                <label style="display:inline;font-weight:normal;margin-right:18px">
                    <input type="radio" name="plate_source" value="text" style="width:auto"
                           onchange="plateSource()" {% if not image_mode %}checked{% endif %}>
                    Type the plate
                </label>
                <label style="display:inline;font-weight:normal">
                    <input type="radio" name="plate_source" value="image" style="width:auto"
                           onchange="plateSource()" {% if image_mode %}checked{% endif %}
                           {% if not ocr_available() %}disabled{% endif %}>
                    Upload a plate image (OCR){% if not ocr_available() %} &mdash; not available: Tesseract is not installed{% endif %}
                </label>
            </div>

            <input
                type="text"
                id="plate_text"
                name="plate_number"
                placeholder="e.g. MH12AB1234"
                value="{{ values.get('plate_number', '') }}"
            >

            <div id="plate_upload">
                <input type="file" id="plate_image" name="plate_image" accept="image/*">
                <small>A cropped, straight photo of the plate. Only the plate text is read; type the colour, type and make/model below.</small>
                {% if values.get('ocr_image') %}
                <input type="hidden" name="ocr_image" value="{{ values.get('ocr_image') }}">
                <input type="hidden" name="ocr_confidence" value="{{ values.get('ocr_confidence', '') }}">
                <p>OCR read <b>{{ values.get('plate_number', '') }}</b> from
                   <img src="/plate-images/pending/{{ values.get('ocr_image') }}.png" alt="plate image" style="height:40px;vertical-align:middle;border:1px solid #ccd">
                   (choose another image to read it again)</p>
                {% endif %}
            </div>

            <script>
            function plateSource() {
                var image = document.querySelector('input[name=plate_source][value=image]').checked;
                var kept = {{ 'true' if values.get('ocr_image') else 'false' }};
                document.getElementById('plate_text').style.display = image ? 'none' : '';
                document.getElementById('plate_text').required = !image;
                document.getElementById('plate_upload').style.display = image ? '' : 'none';
                document.getElementById('plate_image').required = image && !kept;
            }
            plateSource();
            </script>

        </div>


        <div class="form-group">

            <label>
                Location
            </label>

            <input
                type="text"
                name="location"
                placeholder="e.g. NH48 Toll Plaza, Pune"
                required
                value="{{ values.get('location', '') }}"
            >

        </div>


        <div class="form-group">

            <label>
                Vehicle Color
            </label>

            <input
                type="text"
                name="vehicle_color"
                placeholder="e.g. White"
                value="{{ values.get('vehicle_color', '') }}"
            >

        </div>


        <div class="form-group">

            <label>
                Vehicle Type
            </label>

            <input
                type="text"
                name="vehicle_type"
                placeholder="e.g. Car"
                value="{{ values.get('vehicle_type', '') }}"
            >

        </div>


        <div class="form-group">

            <label>
                Make / Model
            </label>

            <input
                type="text"
                name="make_model_seen"
                placeholder="e.g. Maruti Swift"
                value="{{ values.get('make_model_seen', '') }}"
            >

        </div>


        <div class="form-group">

            <label>
                Camera ID
            </label>

            <input
                type="text"
                name="camera_id"
                placeholder="e.g. CAM-014"
                value="{{ values.get('camera_id', '') }}"
            >

        </div>


        <div class="form-actions">

            <button
                type="submit"
                class="btn btn-primary"
            >
                Add Capture
            </button>

            <a
                href="/"
                class="btn btn-secondary"
            >
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
def add_capture():

    values = {}

    if request.method == "POST":

        # Plate from an image: OCR fills in the plate text, which then goes
        # through the same check as a typed plate.
        form = request.form.copy()

        if form.get("plate_source") == "image":

            upload = request.files.get("plate_image")

            if upload and upload.filename:

                data = upload.read()

                ocr = plate_ocr.read_plate(data)

                if ocr["error"]:

                    values = form.to_dict()
                    values.pop("ocr_image", None)

                    return render_template_string(
                        ADD_HTML,
                        style=STYLE,
                        popup=POPUP,
                        values=values,
                        error=ocr["error"] + " Type the plate instead, or try a clearer image."
                    )

                form["plate_number"] = ocr["text"]
                form["ocr_image"] = save_pending_image(data)
                form["ocr_confidence"] = str(ocr["confidence"])

            elif not pending_image_path(form.get("ocr_image")):

                return render_template_string(
                    ADD_HTML,
                    style=STYLE,
                    popup=POPUP,
                    values=form.to_dict(),
                    error="Choose a plate image, or switch to typing the plate."
                )

        values = form.to_dict()

        plate = form.get(
            "plate_number", ""
        ).strip().upper()

        location = request.form.get(
            "location", ""
        ).strip()

        color = request.form.get(
            "vehicle_color", ""
        ).strip()

        vehicle_type = request.form.get(
            "vehicle_type", ""
        ).strip()

        make_model = request.form.get(
            "make_model_seen", ""
        ).strip()

        camera = request.form.get(
            "camera_id", ""
        ).strip()


        # Validation

        if not plate:

            return render_template_string(
                ADD_HTML,
                style=STYLE,
                popup=POPUP,
                values=values,
                error="Please enter a vehicle plate number."
            )


        if not location:

            return render_template_string(
                ADD_HTML,
                style=STYLE,
                popup=POPUP,
                values=values,
                error="Please enter the capture location."
            )


        if not vehicle_type:

            return render_template_string(
                ADD_HTML,
                style=STYLE,
                popup=POPUP,
                values=values,
                error="Please enter the vehicle type."
            )


        if not make_model:

            return render_template_string(
                ADD_HTML,
                style=STYLE,
                popup=POPUP,
                values=values,
                error="Please enter the make/model."
            )


        if not camera:

            return render_template_string(
                ADD_HTML,
                style=STYLE,
                popup=POPUP,
                values=values,
                error="Please enter the camera ID."
            )



        # Preserve what the camera saw so cleansing can flag a conflicting observation.
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        capture_ts = datetime.now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )


        cur.execute("""
            INSERT INTO vehicle_capture
            (
                plate_number,
                capture_ts,
                location,
                vehicle_color,
                vehicle_type,
                make_model_seen,
                camera_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            plate,
            capture_ts,
            location,
            color,
            vehicle_type,
            make_model,
            camera
        ))


        conn.commit()

        conn.close()

        capture_id = cur.lastrowid

        # Keep the plate image with its capture
        pending = pending_image_path(form.get("ocr_image"))
        ocr_read = ""
        if form.get("plate_source") == "image" and pending:
            os.replace(pending, os.path.join(PLATE_IMAGE_DIR, f"capture_{capture_id}.png"))
            ocr_read = f"OCR read {plate} from the plate image."

        # Saved as typed; automatic cleansing builds the cleaned copy (and reports to MoT) right after
        notice = cleanser.after_change("ANPR", capture_id)
        if ocr_read:
            notice = f"Saved. {ocr_read} " + notice.removeprefix("Saved. ")
        return redirect(url_for("admin_home", clean_notice=notice.strip()))


    return render_template_string(
        ADD_HTML,
        style=STYLE,
        popup=POPUP,
        values=values,
        error=None
    )


# ============================================================
# EDIT CAPTURE
# ============================================================

# ============================================================
# EDIT CAPTURE
# ============================================================

EDIT_HTML = """
<!DOCTYPE html>
<html>

<head>

    <title>Edit Capture</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card">

    <h1>Edit Capture</h1>

    <div class="subtitle">
        Find a vehicle and select the capture record to edit
    </div>


    <!-- STEP 1: FIND VEHICLE -->

    <form method="POST" action="/edit">

        <div class="form-group">

            <label>
                Vehicle Plate Number
            </label>

            <input
                type="text"
                name="plate_number"
                placeholder="e.g. MH12AB1234"
                value="{{ plate }}"
                required
            >

        </div>


        <button
            type="submit"
            class="btn btn-primary"
        >
            Find Captures
        </button>

    </form>


    {% if searched and captures %}

        <h2>
            Captures for {{ plate }}
        </h2>


        <!-- SHOW ALL CAPTURES -->

        <div class="table-wrapper capture-table">

            <table>

                <tr>

                    <th>ID</th>
                    <th>Time</th>
                    <th>Location</th>
                    <th>Color</th>
                    <th>Type</th>
                    <th>Make / Model</th>
                    <th>Camera</th>

                </tr>


                {% for c in captures %}

                <tr>

                    <td>
                        <b>{{ c["capture_id"] }}</b>
                    </td>

                    <td>
                        {{ c["capture_ts"] }}
                    </td>

                    <td>
                        {{ c["location"] }}
                    </td>

                    <td>
                        {{ c["vehicle_color"] }}
                    </td>

                    <td>
                        {{ c["vehicle_type"] }}
                    </td>

                    <td>
                        {{ c["make_model_seen"] }}
                    </td>

                    <td>
                        {{ c["camera_id"] }}
                    </td>

                </tr>

                {% endfor %}

            </table>

        </div>


        <!-- STEP 2: SELECT CAPTURE ID -->

        <div class="info-box">

            Select the capture you want to edit by entering
            its Capture ID from the table.

        </div>


        <form
            method="POST"
            action="/edit/select"
            class="form-card"
        >

            <input
                type="hidden"
                name="plate_number"
                value="{{ plate }}"
            >


            <div class="form-group">

                <label>
                    Capture ID
                </label>

                <input
                    type="number"
                    name="capture_id"
                    placeholder="e.g. 19"
                    required
                >

            </div>


            <div class="form-actions">

                <button
                    type="submit"
                    class="btn btn-primary"
                >
                    Select Capture
                </button>

            </div>

        </form>

    {% endif %}


    <br>

    <a
        href="/"
        class="btn btn-secondary"
    >
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
# EDIT - FIND VEHICLE
# ============================================================

@app.route("/edit", methods=["GET", "POST"])
def edit_capture():

    captures = []

    plate = ""

    searched = False

    error = None


    if request.method == "POST":

        searched = True

        plate = request.form.get(
            "plate_number", ""
        ).strip().upper()


        if not plate:

            error = (
                "Please enter a vehicle plate number."
            )

        else:

            conn = sqlite3.connect(DB_PATH)

            conn.row_factory = sqlite3.Row

            cur = conn.cursor()


            cur.execute("""
                SELECT *
                FROM vehicle_capture
                WHERE plate_number = ?
                ORDER BY capture_id
            """, (plate,))


            captures = cur.fetchall()

            conn.close()


            if not captures:

                error = (
                    f"No captures found for vehicle {plate}."
                )


    return render_template_string(
        EDIT_HTML,
        style=STYLE,
        popup=POPUP,
        captures=captures,
        plate=plate,
        searched=searched,
        error=error
    )


# ============================================================
# EDIT - SELECT CAPTURE ID
# ============================================================

@app.route("/edit/select", methods=["POST"])
def select_capture():

    plate = request.form.get(
        "plate_number", ""
    ).strip().upper()

    capture_id = request.form.get(
        "capture_id", ""
    ).strip()


    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    cur = conn.cursor()


    # Check whether this capture exists
    # for the selected vehicle.

    cur.execute("""
        SELECT *
        FROM vehicle_capture
        WHERE capture_id = ?
        AND plate_number = ?
    """, (
        capture_id,
        plate
    ))


    capture = cur.fetchone()


    if not capture:

        # Reload captures so the user stays
        # on the same Edit page.

        cur.execute("""
            SELECT *
            FROM vehicle_capture
            WHERE plate_number = ?
            ORDER BY capture_id
        """, (plate,))


        captures = cur.fetchall()

        conn.close()


        return render_template_string(
            EDIT_HTML,
            style=STYLE,
            popup=POPUP,
            captures=captures,
            plate=plate,
            searched=True,
            error=(
                "Invalid capture ID. "
                "Please enter a Capture ID shown in the table."
            )
        )


    conn.close()


    # Capture exists.
    # Go to the actual editing page.

    return render_template_string(
        EDIT_FORM_HTML,
        style=STYLE,
        popup=POPUP,
        capture=capture,
        plate=plate
    )


# ============================================================
# EDIT FORM
# ============================================================

EDIT_FORM_HTML = """
<!DOCTYPE html>
<html>

<head>

    <title>Edit Capture #{{ capture["capture_id"] }}</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card form-card">

    <h1>
        Edit Capture #{{ capture["capture_id"] }}
    </h1>

    <div class="subtitle">
        Vehicle: <b>{{ plate }}</b>
    </div>


    <!-- CAPTURE INFORMATION -->

    <div class="info-box">

        <b>Capture ID:</b>
        {{ capture["capture_id"] }}

        <br><br>

        <b>Capture Time:</b>
        {{ capture["capture_ts"] }}

        <br><br>

        <b>Vehicle Type:</b>
        {{ capture["vehicle_type"] }}

        <br><br>

        <b>Make / Model:</b>
        {{ capture["make_model_seen"] }}

    </div>


    <form method="POST" action="/edit/save">

        <input
            type="hidden"
            name="plate_number"
            value="{{ plate }}"
        >

        <input
            type="hidden"
            name="capture_id"
            value="{{ capture["capture_id"] }}"
        >


        <!-- LOCATION -->

        <div class="form-group">

            <label>
                Location
            </label>

            <input
                type="text"
                name="location"
                value="{{ capture["location"] }}"
                required
            >

        </div>


        <!-- COLOR -->

        <div class="form-group">

            <label>
                Vehicle Color
            </label>

            <input
                type="text"
                name="vehicle_color"
                value="{{ capture["vehicle_color"] }}"
            >

        </div>


        <!-- CAMERA -->

        <div class="form-group">

            <label>
                Camera ID
            </label>

            <input
                type="text"
                name="camera_id"
                value="{{ capture["camera_id"] }}"
                required
            >

        </div>


        <div class="form-actions">

            <button
                type="submit"
                class="btn btn-primary"
            >
                Save Changes
            </button>


            <a
                href="/edit"
                class="btn btn-secondary"
            >
                Cancel
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
# EDIT - SAVE CHANGES
# ============================================================

@app.route("/edit/save", methods=["POST"])
def save_edit():

    plate = request.form.get(
        "plate_number", ""
    ).strip().upper()

    capture_id = request.form.get(
        "capture_id", ""
    ).strip()


    location = request.form.get(
        "location", ""
    ).strip()

    color = request.form.get(
        "vehicle_color", ""
    ).strip()

    camera = request.form.get(
        "camera_id", ""
    ).strip()


    conn = sqlite3.connect(DB_PATH)

    conn.row_factory = sqlite3.Row

    cur = conn.cursor()


    # Make sure the selected capture exists
    # and belongs to the selected vehicle.

    cur.execute("""
        SELECT *
        FROM vehicle_capture
        WHERE capture_id = ?
        AND plate_number = ?
    """, (
        capture_id,
        plate
    ))


    capture = cur.fetchone()


    if not capture:

        conn.close()

        return render_template_string(
            EDIT_HTML,
            style=STYLE,
            popup=POPUP,
            captures=[],
            plate=plate,
            searched=True,
            error=(
                "Invalid capture ID. "
                "Please select a valid capture."
            )
        )


    # Validation

    if not location:

        conn.close()

        return render_template_string(
            EDIT_FORM_HTML,
            style=STYLE,
            popup=POPUP,
            capture=capture,
            plate=plate,
            error="Location cannot be empty."
        )


    if not camera:

        conn.close()

        return render_template_string(
            EDIT_FORM_HTML,
            style=STYLE,
            popup=POPUP,
            capture=capture,
            plate=plate,
            error="Camera ID cannot be empty."
        )


    before = cleanser.decision("ANPR", capture_id)

    # Update ONLY the editable fields.

    cur.execute("""
        UPDATE vehicle_capture

        SET
            location = ?,
            vehicle_color = ?,
            camera_id = ?

        WHERE capture_id = ?
        AND plate_number = ?

    """, (
        location,
        color,
        camera,
        capture_id,
        plate
    ))


    conn.commit()

    conn.close()


    return redirect(
        url_for("admin_home", clean_notice=cleanser.after_change("ANPR", capture_id, before))
    )

# ============================================================
# DELETE VEHICLE
# ============================================================

DELETE_HTML = """
<!DOCTYPE html>
<html>

<head>

    <title>Delete Vehicle</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card form-card">

    <h1>Delete Vehicle</h1>

    <div class="subtitle">
        Remove all ANPR captures belonging to a vehicle
    </div>


    <form method="POST">

        <div class="form-group">

            <label>
                Vehicle Plate Number
            </label>

            <input
                type="text"
                name="plate_number"
                placeholder="e.g. MH12AB1234"
                value="{{ plate }}"
                required
            >

        </div>


        <div class="warning-box">

            This will delete <b>all ANPR capture records</b>
            for the selected vehicle.

            <br><br>

            Information in the Insurance, RTO, and Police/Shredding
            databases will not be affected.

        </div>


        <div class="form-actions">

            <button
                type="submit"
                class="btn btn-danger"
            >
                Delete Vehicle
            </button>

            <a
                href="/"
                class="btn btn-secondary"
            >
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


@app.route("/delete", methods=["GET", "POST"])
def delete_vehicle():

    plate = ""

    error = None


    if request.method == "POST":

        plate = request.form.get(
            "plate_number", ""
        ).strip().upper()


        if not plate:

            error = (
                "Please enter a vehicle plate number."
            )

        else:

            conn = sqlite3.connect(DB_PATH)

            cur = conn.cursor()


            # Check if vehicle exists.

            cur.execute("""
                SELECT COUNT(*)
                FROM vehicle_capture
                WHERE plate_number = ?
            """, (plate,))

            count = cur.fetchone()[0]


            if count == 0:

                conn.close()

                error = (
                    f"No vehicle found with plate number {plate}."
                )

            else:

                # Use confirmation in browser before deletion.

                conn.close()

                return render_template_string(
                    DELETE_CONFIRM_HTML,
                    style=STYLE,
                    popup=POPUP,
                    plate=plate,
                    count=count
                )


    return render_template_string(
        DELETE_HTML,
        style=STYLE,
        popup=POPUP,
        plate=plate,
        error=error
    )


# ============================================================
# DELETE CONFIRMATION
# ============================================================

DELETE_CONFIRM_HTML = """
<!DOCTYPE html>
<html>

<head>

    <title>Confirm Delete</title>

    {{ style|safe }}

</head>

<body>

<div class="container">

<div class="card form-card">

    <h1>Confirm Deletion</h1>

    <div class="warning-box">

        <b>{{ plate }}</b> has
        <b>{{ count }}</b> capture(s).

        <br><br>

        Are you sure you want to permanently delete
        all of these ANPR captures?

    </div>


    <form method="POST" action="/delete/confirm">

        <input
            type="hidden"
            name="plate_number"
            value="{{ plate }}"
        >


        <div class="form-actions">

            <button
                type="submit"
                class="btn btn-danger"
            >
                Yes, Delete Vehicle
            </button>

            <a
                href="/delete"
                class="btn btn-secondary"
            >
                Cancel
            </a>

        </div>

    </form>

</div>

</div>

</body>

</html>
"""


@app.route("/delete/confirm", methods=["POST"])
def confirm_delete():

    plate = request.form.get(
        "plate_number", ""
    ).strip().upper()


    conn = sqlite3.connect(DB_PATH)

    cur = conn.cursor()


    capture_ids = [r[0] for r in cur.execute(
        "SELECT capture_id FROM vehicle_capture WHERE plate_number = ?", (plate,)
    )]

    cur.execute("""
        DELETE FROM vehicle_capture
        WHERE plate_number = ?
    """, (plate,))

    for capture_id in capture_ids:
        image = os.path.join(PLATE_IMAGE_DIR, f"capture_{capture_id}.png")
        if os.path.exists(image):
            os.remove(image)


    conn.commit()

    deleted = cur.rowcount

    conn.close()


    return redirect(
        url_for("admin_home", clean_notice=cleanser.after_change("ANPR"))
    )


# ============================================================
# RUN SERVER
# ============================================================

@app.put("/records/capture/<int:record_id>/plate")
def update_capture_plate_api(record_id):
    payload = request.get_json(silent=True) or {}
    expected, new = str(payload.get("expected_plate", "")), str(payload.get("new_plate", "")).strip().upper()
    if not expected or not new:
        return jsonify({"updated": False, "error": "expected_plate and new_plate are required"}), 400
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.execute("UPDATE vehicle_capture SET plate_number=? WHERE capture_id=? AND plate_number=?", (new, record_id, expected))
        conn.commit()
        if cur.rowcount != 1:
            return jsonify({"updated": False, "error": "record missing or plate changed since review"}), 409
        return jsonify({"updated": True, "source": "ANPR", "record_type": "capture", "record_id": record_id, "old_plate": expected, "new_plate": new})
    finally:
        conn.close()


def init_db():
    """Main table (with its seed records) plus the Phase 3 related tables, also on an existing database."""
    _init_main()
    conn = sqlite3.connect(DB_PATH)
    try:
        anpr_context.ensure(conn)
        conn.commit()
    finally:
        conn.close()


# Phase 3: federated context discovery over this database's FK-PK joins, and search of its own documents.
context_search.register(app, "ANPR", lambda: DB_PATH, anpr_context.discover,
                        context_search.DocIndex(os.path.join(os.path.dirname(os.path.abspath(__file__)), "documents"), "ANPR"), anpr_context.JOINS,
                        count=anpr_context.count, profile=anpr_context.profile)


if __name__ == "__main__":

    init_db()
    desk.start()      # first start: the existing plates seed the dictionary
    cleanser.start()  # clean now, then pick up pending work / command-line edits in the background

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--port",
        type=int,
        default=5001
    )

    parser.add_argument(
        "--host",
        default="0.0.0.0"
    )

    args = parser.parse_args()


    print(
        f"ANPR service (Device 1) running on "
        f"http://{args.host}:{args.port}"
    )


    app.run(
        host=args.host,
        port=args.port
    )
