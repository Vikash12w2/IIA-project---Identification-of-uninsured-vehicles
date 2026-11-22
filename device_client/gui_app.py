import re

from flask import Flask, request, render_template_string, jsonify, abort
from markupsafe import Markup, escape
import integration_engine as engine
import context_engine
import plate_ocr  # shared with ANPR (project root, on the path through integration_engine)

app = Flask(__name__)

MAX_ROWS = 500           # rows of the "All Vehicles" table (a big fleet would make the page heavy)

# Complete integrated state from the previous Client GUI refresh.
# It is used to detect changes in ANPR, Insurance, RTO, and Theft/Shredding.
previous_views = None

PAGE = """
<!DOCTYPE html>
<html>
<head>
<title>Vehicle 360 View - Client</title>
<meta charset="UTF-8">
<style>
* { box-sizing: border-box; }

body {
    font-family: Arial, sans-serif;
    margin: 0;
    background: #f4f6f8;
    color: #222;
}

.header {
    background: #1a3d6d;
    color: white;
    padding: 24px 40px;
}

.header h1 { margin: 0 0 7px 0; font-size: 28px; }
.header p { margin: 0; opacity: .9; }

.container {
    max-width: 1450px;
    margin: 28px auto;
    padding: 0 24px;
}

.stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px;
    margin-bottom: 20px;
}

.stat {
    background: white;
    border-radius: 8px;
    padding: 18px;
    box-shadow: 0 1px 4px rgba(0,0,0,.15);
}

.stat .number {
    font-size: 28px;
    font-weight: bold;
    color: #1a3d6d;
}

.stat .label {
    color: #666;
    margin-top: 5px;
}

.card {
    background: white;
    border-radius: 8px;
    padding: 22px;
    margin-bottom: 20px;
    box-shadow: 0 1px 4px rgba(0,0,0,.15);
}

h2 {
    color: #1a3d6d;
    margin-top: 0;
}

.search-row {
    display: flex;
    gap: 10px;
}

input[type=text] {
    padding: 10px 12px;
    font-size: 16px;
    width: 280px;
    text-transform: uppercase;
    border: 1px solid #bbb;
    border-radius: 4px;
}

button {
    padding: 10px 20px;
    font-size: 15px;
    background: #1a3d6d;
    color: white;
    border: none;
    border-radius: 4px;
    cursor: pointer;
}

button:hover { background: #14315a; }

.status-grid {
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 10px;
}

.status {
    padding: 10px;
    border-radius: 5px;
    text-align: center;
    font-weight: bold;
    font-size: 14px;
}

.up { background: #eafaf1; color: #217a45; }
.down { background: #fdecea; color: #b52b27; }

.vehicle-title {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-bottom: 18px;
}

.badge {
    padding: 5px 10px;
    border-radius: 4px;
    color: white;
    font-size: 12px;
    font-weight: bold;
}

.badge-ok { background: #2e9e5b; }
.badge-bad { background: #d9534f; }

.details {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0 40px;
}

.detail {
    padding: 8px 0;
    border-bottom: 1px solid #eee;
}

.detail .label {
    display: inline-block;
    font-weight: bold;
    width: 170px;
}

.not-found {
    color: #d9534f;
    font-weight: bold;
}

.notice {
    padding: 12px 15px;
    border-radius: 5px;
    margin-bottom: 15px;
    background: #eafaf1;
    color: #217a45;
    font-weight: bold;
}

.notice-warning {
    background: #fff7e6;
    color: #9a6700;
}

table {
    border-collapse: collapse;
    width: 100%;
    margin-top: 10px;
}

th, td {
    border: 1px solid #ddd;
    padding: 9px 10px;
    text-align: left;
    font-size: 13px;
}

th {
    background: #1a3d6d;
    color: white;
}

tr.flagged { background: #fdecea; }
tr.clean { background: #eafaf1; }

.flag-text {
    color: #b52b27;
    font-weight: bold;
}

.action { font-size: 12px; }
.candidate-note { background: #eef5ff; color: #1a3d6d; padding: 12px 15px; border-radius: 5px; margin: 12px 0; }
.service-errors { background: #fdecea; color: #9f2522; padding: 12px 15px; border-radius: 5px; margin: 12px 0; }
.candidate-details { min-width: 300px; white-space: normal; line-height: 1.5; }

@media (max-width: 900px) {
    .stats, .status-grid { grid-template-columns: 1fr 1fr; }
    .details { grid-template-columns: 1fr; }
    .search-row { flex-direction: column; }
    input[type=text] { width: 100%; }
}
</style>
</head>

<body>

<div class="header">
    <h1>Identification of Uninsured Vehicles</h1>
    <p>Client Device — Integrated Vehicle 360 View &nbsp;·&nbsp; <a href="/context" style="color:white;">Federated context search</a></p>
</div>

<div class="container">

{% if integration_message %}
<div class="notice">{{ integration_message }}</div>
{% endif %}
{% for warning in cleansing_warnings %}<div class="notice notice-warning">{{ warning }}</div>{% endfor %}

{% if show_fleet %}<div class="stats">
    <div class="stat">
        <div class="number">{{ all_views|length }}</div>
        <div class="label">Vehicles on Road</div>
    </div>
    <div class="stat">
        <div class="number">{{ flagged_count }}</div>
        <div class="label">Flagged Vehicles</div>
    </div>
    <div class="stat">
        <div class="number">{{ service_up_count }}/5</div>
        <div class="label">Devices Online</div>
    </div>
    <div class="stat">
        <div class="number">{{ capture_count }}</div>
        <div class="label">Total ANPR Captures</div>
    </div>
</div>

{% else %}<p><a href="/">Show fleet overview</a></p>{% endif %}
<div class="card">
    <h2>Device Connectivity</h2>
    <div class="status-grid">
    {% for name, ok in service_status.items() %}
        <div class="status {{ 'up' if ok else 'down' }}">
            {{ name }}<br>
            {{ 'UP' if ok else 'DOWN' }}
        </div>
    {% endfor %}
    </div>
</div>

<div class="card">
    <h2>Look Up a Vehicle</h2>
    <form method="get" action="/" id="search-form" enctype="multipart/form-data">
        <div style="margin-bottom:10px;">
            <label style="margin-right:18px;">
                <input type="radio" name="plate_source" value="text" onchange="plateSource()" {% if not ocr %}checked{% endif %}>
                Type the plate
            </label>
            <label>
                <input type="radio" name="plate_source" value="image" onchange="plateSource()" {% if ocr %}checked{% endif %}
                       {% if not ocr_available %}disabled{% endif %}>
                Upload a plate image (OCR){% if not ocr_available %} &mdash; not available: Tesseract is not installed{% endif %}
            </label>
        </div>
        <div class="search-row">
            <input type="text"
                   id="plate_text"
                   name="plate"
                   placeholder="e.g. MH14ZZ0000"
                   value="{{ searched_plate or '' }}">
            <input type="file" id="plate_image" name="plate_image" accept="image/*" style="padding:8px 0;">
            <button type="submit">Search Vehicle</button>
        </div>
        <div class="search-row" style="margin-top:8px;">
            <input type="text" name="color" placeholder="Observed colour (optional)" value="{{ context_in.color or '' }}">
            <input type="text" name="vehicle_type" placeholder="Type, e.g. Car (optional)" value="{{ context_in.vehicle_type or '' }}">
            <input type="text" name="make_model" placeholder="Make / model (optional)" value="{{ context_in.make_model or '' }}">
        </div>
    </form>
    <script>
    function plateSource() {
        var image = document.querySelector('input[name=plate_source][value=image]').checked;
        var form = document.getElementById('search-form');
        form.method = image ? 'post' : 'get';
        document.getElementById('plate_text').style.display = image ? 'none' : '';
        document.getElementById('plate_text').required = !image;
        document.getElementById('plate_image').style.display = image ? '' : 'none';
        document.getElementById('plate_image').required = image;
    }
    plateSource();
    </script>
    {% if ocr %}
        {% if ocr.error %}
        <div class="notice notice-warning" style="margin-top:12px;">{{ ocr.error }} Type the plate instead, or try a clearer image.</div>
        {% else %}
        <div class="candidate-note" style="display:flex;gap:14px;align-items:center;">
            {% if ocr.image %}<img src="{{ ocr.image }}" alt="plate image" style="max-height:70px;border:1px solid #ccd;border-radius:4px;">{% endif %}
            <div>OCR read <b>{{ ocr.text }}</b> from the plate image (confidence {{ (ocr.confidence * 100)|round|int }}%).</div>
        </div>
        {% endif %}
    {% endif %}
</div>

{% if view %}
<div class="card">
    {% if not view_found %}
        <div class="notice notice-warning">
            No exact match for '{{ searched_plate }}'.
            {% if has_close_candidates %}Check the Approximate Plate Candidates below for the closest plate numbers.
            {% else %}No close plate number was found in any database either.{% endif %}
        </div>
    {% else %}
        {% if has_close_candidates %}
        <div class="notice notice-warning">Exact match found for '{{ searched_plate }}', but {{ close_count }} other plate(s) in the databases are close to it. If the plate was misread, the vehicle may be one of them: see Similar Plates below.</div>
        {% else %}
        <div class="candidate-note">Exact match found. No similar plate exists in any database.</div>
        {% endif %}
        <div class="vehicle-title">
            <h2 style="margin:0;">{{ view.plate_number }}</h2>
            {% if view.suspicious %}
                <span class="badge badge-bad">FLAGGED</span>
            {% else %}
                <span class="badge badge-ok">CLEAN</span>
            {% endif %}
        </div>

        <div class="details">
            <div class="detail"><span class="label">Owner:</span>{{ view.owner_name or '—' }}</div>
            <div class="detail"><span class="label">Vehicle:</span>{{ view.make_model or '—' }}</div>
            <div class="detail"><span class="label">Last Seen on Road:</span>{{ view.last_seen_on_road or '—' }}</div>
            <div class="detail"><span class="label">Times Captured:</span>{{ view.times_captured }}</div>
            <div class="detail"><span class="label">Registered:</span>{{ 'Yes' if view.registered else 'No' }}</div>
            <div class="detail"><span class="label">Registration Valid:</span>{{ view.reg_valid_upto or '—' }}</div>
            <div class="detail"><span class="label">Insurance:</span>{{ view.insurance_status }}</div>
            <div class="detail"><span class="label">Insurer:</span>{{ view.insurer or '—' }}</div>
            <div class="detail"><span class="label">Policy Number:</span>{{ view.policy_number or '—' }}</div>
            <div class="detail"><span class="label">Policy End Date:</span>{{ view.policy_end_date or '—' }}</div>
            <div class="detail"><span class="label">Theft Status:</span>{{ view.theft_status }}</div>
            <div class="detail"><span class="label">Shredded:</span>{{ 'Yes' if view.shredded else 'No' }}</div>
        </div>

        <div class="detail" style="margin-top:15px;">
            <span class="label">Risk Flags:</span>
            {% if view.risk_flags %}
                <span class="flag-text">{{ view.risk_flags | join(', ') }}</span>
            {% else %}None{% endif %}
        </div>

        <div class="detail">
            <span class="label">Recommended Action:</span>{{ recommended_action }}
        </div>
        <p><a href="/context?q={{ view.plate_number|urlencode }}">Context and documents for this vehicle from every database</a></p>
    {% endif %}
</div>
{% endif %}

{% if candidate_search and not candidate_search.only_exact %}
<div class="card">
    <h2>{{ 'Similar Plates' if view_found else 'Approximate Plate Candidates' }}</h2>
    <p>Entered plate: <b>{{ candidate_search.query }}</b> · Normalized: <b>{{ candidate_search.normalized_query }}</b> · Shown: similarity of at least <b>{{ (candidate_search.threshold * 100)|round(1) }}%</b>, or up to <b>{{ max_slips }}</b> characters different</p>
    <div class="candidate-note">These are the closest plates in the cleaned data of every device. Similarity does not confirm vehicle identity: select a candidate to view that vehicle. Nothing is saved or changed.</div>
    {% if candidate_search.service_errors %}
        <div class="service-errors"><b>Some services could not be searched. Results may be incomplete:</b><ul>{% for error in candidate_search.service_errors %}<li>{{ error }}</li>{% endfor %}</ul></div>
    {% endif %}
    {% if candidate_search.candidates %}
    <table>
        <tr><th>Candidate Plate</th><th>Characters different</th><th>Plate similarity</th><th>Confidence</th><th>Context check</th><th>Found In</th><th>Available Vehicle Details</th><th>Select</th></tr>
        {% for candidate in candidate_search.candidates %}
        <tr>
            <td><b>{{ candidate.candidate_plate }}</b></td>
            <td>{{ 'exact' if candidate.slips == 0 else candidate.slips }}</td>
            <td>{{ (candidate.similarity * 100)|round(1) }}%</td>
            <td><b>{{ (candidate.confidence * 100)|round(1) }}%</b></td>
            <td>
                {% if candidate.context.matches %}<span style="color:#217a45;">&#10003; {{ candidate.context.matches|join(', ') }}</span><br>{% endif %}
                {% if candidate.context.conflicts %}<span style="color:#b52b27;">&#10007; {{ candidate.context.conflicts|join(', ') }}</span><br>{% endif %}
                {% if candidate.context.unknown %}<small>no data: {{ candidate.context.unknown|join(', ') }}</small><br>{% endif %}
                {% if not candidate.context.matches and not candidate.context.conflicts and not candidate.context.unknown %}<small>no context given</small><br>{% endif %}
                <small>{{ 'in RTO registry' if candidate.in_registry else 'not in RTO registry' }}</small>
            </td>
            <td>{{ candidate.sources | join(', ') }}</td>
            <td class="candidate-details">
                {% for source, records in candidate.records_by_source.items() %}
                    <b>{{ source }}</b><br>
                    {% for record in records %}
                        {% if source == 'ANPR' %}
                            Capture {{ record.capture_id or '—' }}; {{ record.make_model_seen or 'make/model unknown' }}; {{ record.vehicle_color or 'color unknown' }}; {{ record.vehicle_type or 'type unknown' }}; {{ record.location or 'location unknown' }}<br>
                        {% elif source == 'RTO' %}
                            {{ record.make or '' }} {{ record.model or '' }}; owner: {{ record.owner_name or '—' }}; chassis: {{ record.chassis_number or '—' }}; engine: {{ record.engine_number or '—' }}<br>
                        {% elif source == 'Insurance' %}
                            {{ record.insurer_name or 'Insurer unknown' }}; policy: {{ record.policy_number or '—' }}; status: {{ record.status or '—' }}; end: {{ record.end_date or '—' }}<br>
                        {% else %}
                            {{ record.record_type or 'record' }}; status: {{ record.status or '—' }}; FIR: {{ record.fir_number or '—' }}; certificate: {{ record.certificate_no or '—' }}<br>
                        {% endif %}
                    {% endfor %}
                {% endfor %}
            </td>
            <td>
                {% if candidate.normalized_plate != candidate_search.normalized_query %}
                <a href="/?plate={{ candidate.normalized_plate|urlencode }}">View this vehicle</a>
                {% else %}<small>Same normalized plate as the search.</small>{% endif %}
            </td>
        </tr>
        {% endfor %}
    </table>
    {% else %}
        <p>No candidates met the configured similarity threshold in the services that responded.</p>
    {% endif %}
    <p><small>Services searched successfully: {{ candidate_search.services_queried | join(', ') if candidate_search.services_queried else 'none' }}.</small></p>
</div>
{% endif %}

{% if show_fleet %}<div class="card">
    <h2>All Vehicles Currently on the Road</h2>
    {% if all_views|length > shown_views|length %}<p>Showing the first {{ shown_views|length }} of {{ all_views|length }} vehicles, flagged ones first. Search a plate above for any other vehicle.</p>{% endif %}

    <table>
        <tr>
            <th>Plate</th><th>Owner</th><th>Vehicle</th><th>Last Seen</th>
            <th>Insurance</th><th>Registration</th><th>Theft</th>
            <th>Shredded</th><th>Risk Flags</th><th>Action</th>
        </tr>

        {% for v in shown_views %}
        <tr class="{{ 'flagged' if v.suspicious else 'clean' }}">
            <td>{{ v.plate_number }}</td>
            <td>{{ v.owner_name or '—' }}</td>
            <td>{{ v.make_model or '—' }}</td>
            <td>{{ v.last_seen_on_road or '—' }}</td>
            <td>{{ v.insurance_status }}</td>
            <td>{{ 'Yes' if v.registered else 'No' }}</td>
            <td>{{ v.theft_status }}</td>
            <td>{{ 'Yes' if v.shredded else 'No' }}</td>
            <td>{{ v.risk_flags | join(', ') if v.risk_flags else '—' }}</td>
            <td class="action">{{ v.action }}</td>
        </tr>
        {% endfor %}
    </table>
</div>

{% endif %}<div class="card">
    <h2>Cleaned Data and MoT</h2>
    <p>
        Every Client GUI refresh re-queries the cleaned data of the distributed
        source devices and shows which vehicles changed since the last refresh.
        Each device cleans its own data automatically, and the ANPR device
        reports flagged vehicles on the road to MoT Device 5.
    </p>
</div>

</div>
</body>
</html>
"""


def process_current_state(current_plates):
    """
    Re-query all source devices and compare the resulting Vehicle 360 Views
    with the previous refresh.

    The first load establishes a baseline. Later refreshes count every
    vehicle whose integrated state changed. (MoT is told by ANPR.)
    """
    global previous_views

    baseline = previous_views is None

    current_views, changed_count = engine.integrate_current_vehicles(
        current_plates,
        previous_views=previous_views,
        baseline=baseline,
    )

    previous_views = current_views

    return changed_count, baseline, current_views


def ocr_search():
    """Vehicle 360 search from a plate image: {text, confidence, error, image} (the image is shown back, not stored)."""
    upload = request.files.get("plate_image")
    if not upload or not upload.filename:
        return {"text": "", "confidence": 0.0, "error": "Choose a plate image, or switch to typing the plate.", "image": ""}
    data = upload.read()
    result = plate_ocr.read_plate(data)
    result["image"] = plate_ocr.preview_data_uri(data) if not result["error"] else ""
    return result


@app.route("/", methods=["GET", "POST"])
def index():
    service_status = engine.get_service_status()

    params = request.values
    ocr = ocr_search() if request.method == "POST" and params.get("plate_source") == "image" else None
    searched_plate = (ocr["text"] if ocr else params.get("plate", "")).strip().upper()
    show_fleet = not searched_plate and ocr is None
    changed_count, baseline, current = 0, False, {}
    if show_fleet:
        plates = engine.get_all_plates()
        if isinstance(plates, list):
            changed_count, baseline, current = process_current_state(plates)

    all_views = sorted(current.values(), key=lambda v: (not v["suspicious"], v["plate_number"]))

    flagged_count = sum(1 for v in all_views if v["suspicious"])
    capture_count = sum(v["times_captured"] for v in all_views)
    service_up_count = sum(1 for ok in service_status.values() if ok)

    context_in = {k: params.get(k, "").strip() for k in ("color", "vehicle_type", "make_model")}
    view = None
    recommended_action = None
    candidate_search = None
    view_found = has_close_candidates = False
    close_count = 0

    if searched_plate:
        # Closest plates of every device; the user can select one (nothing is saved or remembered)
        # Always with the similar ones, even after an exact match: the plate may have been misread
        candidate_search = engine.find_plate_candidates(searched_plate, context=context_in,
                                                        max_slips=engine.config.PLATE_MATCH_MAX_SLIPS)
        # Nothing to choose from when the only candidate is the searched plate itself
        candidate_search["only_exact"] = bool(candidate_search["candidates"]) and all(
            c["normalized_plate"] == candidate_search["normalized_query"] for c in candidate_search["candidates"])
        view = engine.build_vehicle_view(searched_plate)
        recommended_action = engine.recommend_action(view)
        # The plate is stored somewhere (any of the databases, not only ANPR / RTO)
        view_found = bool(view["times_captured"] or view["registered"] or view["policy_number"]
                          or view["theft_status"] != "NONE" or view["shredded"])
        close_count = sum(1 for c in candidate_search["candidates"]
                          if c["normalized_plate"] != candidate_search["normalized_query"])
        has_close_candidates = close_count > 0

    if baseline:
        integration_message = (
            "Initial federation complete. The current vehicles are the "
            "baseline for detecting changes."
        )
    elif changed_count:
        integration_message = (
            f"{changed_count} vehicle state change(s) detected since the "
            "last refresh."
        )
    else:
        integration_message = None

    return render_template_string(
        PAGE,
        show_fleet=show_fleet,
        service_status=service_status,
        cleansing_warnings=engine.get_cleansing_warnings(),
        service_up_count=service_up_count,
        all_views=all_views,
        shown_views=all_views[:MAX_ROWS],
        flagged_count=flagged_count,
        capture_count=capture_count,
        view=view,
        recommended_action=recommended_action,
        searched_plate=searched_plate,
        context_in=context_in,
        candidate_search=candidate_search,
        integration_message=integration_message,
        view_found=view_found,
        has_close_candidates=has_close_candidates,
        close_count=close_count,
        max_slips=engine.config.PLATE_MATCH_MAX_SLIPS,
        ocr=ocr,
        ocr_available=plate_ocr.available(),
    )


# ============================================================
# PHASE 3 — FEDERATED CONTEXT SEARCH WITH UNSTRUCTURED DOCUMENTS
# ============================================================

def highlight(text, marks, written=()):
    """Escape a snippet and mark the context words and the plates as written in it."""
    out = str(escape(text))
    for plate in sorted(written, key=len, reverse=True):
        pattern = r"\s*".join(re.escape(ch) for ch in plate)
        out = re.sub(f"({pattern})", r"<mark>\1</mark>", out, flags=re.IGNORECASE)
    if marks:
        pattern = r"\b(" + "|".join(re.escape(m) for m in sorted(marks, key=len, reverse=True)) + r")\b"
        out = re.sub(pattern, r"<mark>\1</mark>", out, flags=re.IGNORECASE)
    return Markup(out)


CONTEXT_PAGE = """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Federated Context Search - Client</title>
<style>
* { box-sizing: border-box; }
body { font-family: Arial, sans-serif; margin: 0; background: #f4f6f8; color: #222; }
.header { background: #1a3d6d; color: white; padding: 22px 40px; }
.header h1 { margin: 0 0 6px 0; font-size: 26px; } .header a { color: white; }
.container { max-width: 1300px; margin: 24px auto; padding: 0 24px; }
.card { background: white; border-radius: 8px; padding: 20px 22px; margin-bottom: 18px; box-shadow: 0 1px 4px rgba(0,0,0,.15); }
h2 { color: #1a3d6d; margin: 0 0 12px 0; font-size: 20px; } h3 { margin: 14px 0 6px 0; font-size: 16px; }
input[type=text] { padding: 10px 12px; font-size: 16px; width: min(640px, 100%); border: 1px solid #bbb; border-radius: 4px; }
button { padding: 10px 20px; font-size: 15px; background: #1a3d6d; color: white; border: none; border-radius: 4px; cursor: pointer; }
.examples a { margin-right: 14px; font-size: 14px; }
table { border-collapse: collapse; width: 100%; } th, td { border: 1px solid #ddd; padding: 7px 9px; text-align: left; font-size: 13px; vertical-align: top; }
th { background: #1a3d6d; color: white; }
.chip { display: inline-block; border: 1px solid #1a3d6d; color: #1a3d6d; border-radius: 12px; padding: 2px 9px; margin: 2px 4px 2px 0; font-size: 13px; }
.vehicle { border-top: 3px solid #1a3d6d; padding-top: 10px; margin-top: 14px; }
.path { font-family: Consolas, monospace; font-size: 12px; color: #333; }
.score { font-weight: bold; color: #1a3d6d; }
.why { font-size: 12px; color: #555; }
.doc { border-left: 4px solid #1a3d6d; padding: 6px 12px; margin: 10px 0; background: #f8fafc; }
.doc .meta { font-size: 12px; color: #555; }
mark { background: #ffe58a; padding: 0 1px; }
.err { color: #b52b27; } .ok { color: #217a45; }
.wrap { overflow-x: auto; }
.intent { font-size: 16px; padding: 10px 12px; background: #eef3f9; border-left: 4px solid #1a3d6d; margin: 10px 0; }
.refine input[type=text] { width: min(520px, 100%); }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
.summary-group { max-height: 15rem; overflow: auto; }
.notes { color: #b52b27; }
.num { text-align: right; }
</style></head><body>
<div class="header"><h1>Federated Context Search</h1>
<p>One query, federated to the five device databases; context from their FK-PK joins; matching unstructured documents.
&nbsp;·&nbsp; <a href="/">Vehicle 360 view</a></p></div>
<div class="container">
<div class="card">
<form method="get" action="/context">
  <input type="text" name="q" value="{{ q if not within else '' }}" placeholder="e.g. cars with more than 2 violations in the last 3 months" autofocus>
  <button type="submit">Search all databases</button>
</form>
<p class="examples">Try:
{% for e in examples %}<a href="/context?q={{ e|urlencode }}">{{ e }}</a>{% endfor %}</p>
</div>

{% if out %}
<div class="card">
  <h2>1. The query</h2>
  {% if out.within %}<p class="why">Narrowing the earlier result "{{ out.within.query }}" ({{ out.within.count }} vehicles) with "{{ q }}".
     <a href="/context">Start a new search</a></p>{% endif %}
  <div class="intent"><b>What you asked for:</b> {{ out.action.text }}</div>
  <p>{% if out.parsed.aggregate %}<span class="chip">count: {{ out.parsed.aggregate.text }}</span>{% endif %}
     {% if out.parsed.period %}<span class="chip">period: {{ out.parsed.period.start or '...' }} to {{ out.parsed.period.end or '...' }}</span>{% endif %}
     {% if out.parsed.age %}<span class="chip">owner age: {{ out.parsed.age.label }}</span>{% endif %}
     {% for o in out.parsed.origin %}<span class="chip">from: {{ o }}</span>{% endfor %}
     {% if out.mode == 'list' %}{% for p in out.parsed.places %}<span class="chip">at: {{ p }}</span>{% endfor %}
     {% for st in out.parsed.statuses %}<span class="chip">only: {{ st }}</span>{% endfor %}{% endif %}
     {% for p in out.parsed.plates %}<span class="chip">plate {{ p }}</span>{% endfor %}
     {% for c in out.parsed.colors %}<span class="chip">colour {{ c }}</span>{% endfor %}
     {% for t in out.parsed.types %}<span class="chip">type {{ t }}</span>{% endfor %}
     {% for m in out.parsed.makes %}<span class="chip">make/model {{ m }}</span>{% endfor %}
     {% if out.mode == 'context' %}{% for t in out.parsed.terms %}<span class="chip">{{ t }}</span>{% endfor %}{% endif %}
     {% for i in out.parsed.intents %}<span class="chip">about: {{ i }}</span>{% endfor %}</p>
  <p class="why">Answered in {{ out.elapsed_ms }} ms.</p>
  {% for n in out.notes or [] %}<p class="notes">{{ n }}</p>{% endfor %}
  {% if out.result_id %}<form class="refine" method="get" action="/context">
    <b>Refine these results</b> ({{ out.total if out.mode == 'list' else out.vehicles|length }} vehicles):
    <input type="text" name="q" placeholder="e.g. owners under 30 / from Maharashtra / uninsured / in the last month">
    <input type="hidden" name="within" value="{{ out.result_id }}">
    <button type="submit">Refine</button>
  </form>{% endif %}
</div>

{% if out.summary %}
<div class="card">
  <h2>Search summary</h2>
  <p><b>{{ out.summary.vehicles }}</b> vehicles
    {% if out.mode == 'context' %} in the ranked results &middot;
    <b>{{ out.summary.records }}</b> matching records &middot; <b>{{ out.summary.documents }}</b> documents
    {% elif out.summary.events is not none %} &middot; <b>{{ out.summary.events }}</b> {{ out.summary.event_label }}
    {% endif %}
  </p>
  {% if out.summary.groups %}
  <p class="why">Vehicle counts use one entry per plate. Colour and sighting location come from camera observations. Registration locations come from RTO.</p>
  <div class="summary-grid">
    {% for title, counts in out.summary.groups.items() %}
    <div><h3>{{ title }}</h3><div class="summary-group"><table>
      <tr><th scope="col">Value</th><th scope="col" class="num">Count</th></tr>
      {% for label, count in counts %}<tr><td>{{ label }}</td><td class="num">{{ count }}</td></tr>{% endfor %}
    </table></div></div>
    {% endfor %}
  </div>
  {% endif %}
</div>
{% endif %}

{% if out.mode == 'list' %}
<div class="card">
  <h2>2. Federation: what each database was asked</h2>
  <div class="wrap"><table>
  <tr><th>Step</th><th>What was sent</th><th>Answers</th></tr>
  {% for r in out.rounds %}
  <tr><td><b>{{ loop.index }}</b>. {{ r.name }}</td>
      <td class="path">{{ r.sent.path }}<br>{% for k, v in r.sent.fields.items() %}{{ k }}: {{ v }}<br>{% endfor %}
        plates: {{ 'all' if r.sent.plates is none else r.sent.plates }}</td>
      <td>{% for d in r.devices %}<b>{{ d.device }}</b>
        {% if not d.ok %}<span class="err">down</span>{% elif r.sent.path == '/docs/search' %}{{ d.found }} documents{% elif not d.supported %}<small>does not record this</small>{% else %}<span class="ok">{{ d.found }} vehicles sent</span>{% if d.counted is not none and d.counted != d.found %} <small>(of {{ d.counted }} counted there)</small>{% endif %}{% endif %}
        <small>{{ d.ms }} ms</small><br>{% endfor %}</td></tr>
  {% endfor %}
  </table></div>
</div>

<div class="card">
  {% if out.total is none %}<h2>Vehicle count unavailable</h2>{% else %}
  <h2>3. {{ out.total }} vehicles{% if out.total > out.rows|length %} (first {{ out.rows|length }} shown){% endif %}: who owns them and where they come from</h2>
  {% if out.total %}
  <div class="wrap" style="margin-top:14px"><table>
  <tr><th>Plate</th>{% if out.parsed.aggregate %}<th>Count</th><th>What and where</th>{% endif %}<th>Owner</th><th>Age</th>
      <th>Comes from (RTO office)</th><th>Owner address</th><th>Vehicle</th><th>Insurance</th><th>Theft / scrap</th></tr>
  {% for r in out.rows %}
  <tr><td><b><a href="/?plate={{ r.plate|urlencode }}">{{ r.plate }}</a></b><br><a class="why" href="/context?q={{ r.plate|urlencode }}">context</a></td>
      {% if out.parsed.aggregate %}<td class="num"><b>{{ r.count }}</b></td>
      <td class="why">{{ r.what|join(', ') }}{% if r.where %}<br>at {{ r.where|join(', ') }}{% endif %}{% if r.last %}<br>last {{ r.last }}{% endif %}</td>{% endif %}
      <td>{{ r.owner or 'not registered' }}{% if r.licence_no %}<br><small>licence {{ r.licence_no }}</small>{% endif %}</td>
      <td>{% if r.age is not none %}{{ r.age }}<br><small>{{ r.age_group }}</small>{% else %}<small>unknown</small>{% endif %}</td>
      <td>{% if r.office %}{{ r.office }} ({{ r.office_code }})<br><small>{{ r.city }}, {{ r.state }}</small>{% else %}<small>plate of {{ r.state }}</small>{% endif %}</td>
      <td>{{ r.address or '' }}</td><td>{{ r.vehicle }}</td>
      <td>{% if r.insured %}<span class="ok">insured</span><br><small>{{ r.policy }}</small>{% elif r.insured is sameas false %}<span class="err">not insured</span><br><small>last {{ r.policy }}</small>{% else %}<span class="err">no policy</span>{% endif %}</td>
      <td>{% if r.theft %}{{ 'stolen' if r.theft == 'Reported' else 'stolen, recovered' }}{% endif %}{% if r.scrapped %} scrapped {{ r.scrapped }}{% endif %}</td></tr>
  {% endfor %}
  </table></div>
  {% else %}<p>No vehicle matches.</p>{% endif %}{% endif %}
</div>
{% else %}

<div class="card">
  <h2>2. Federation: subqueries sent to each database</h2>
  <div class="wrap"><table>
  <tr><th>Round</th><th>What was sent</th>{% for d in out.rounds[0].devices %}<th>{{ d.device }}<br><small>{{ d.address }}</small></th>{% endfor %}</tr>
  {% for r in out.rounds %}
  <tr><td><b>{{ r.hop }}</b><br><small>{{ round_names[r.hop] }}</small></td>
      <td class="path">{% if r.hop == 1 %}{{ r.subquery.text }}{% elif r.hop == 2 %}{% for k, vs in r.subquery['keys'].items() if vs %}{{ k }}: {{ vs|join(', ') }}<br>{% endfor %}{% else %}{{ r.subquery.terms|length }} context terms, {{ r.subquery.plates|length }} plates, {{ r.subquery.ids|length }} numbers{% endif %}</td>
      {% for d in r.devices %}<td>{% if d.ok %}<span class="ok">{{ d.found }} found</span>{% else %}<span class="err">down</span>{% endif %}<br><small>{{ d.ms }} ms</small></td>{% endfor %}</tr>
  {% endfor %}
  </table></div>
</div>

<div class="card">
  <h2>3. Context discovered across the databases</h2>
  {% if not out.vehicles %}<p>No database holds a record related to this query.</p>{% endif %}
  {% for v in out.vehicles %}
  <div class="vehicle">
    <h3>{{ v.plate }} &nbsp;<span class="score">{{ (v.score * 100)|round|int }}% related</span>
      &nbsp;<small>found in {{ v.devices|join(', ') }}</small> &nbsp;<a href="/?plate={{ v.plate|urlencode }}">Vehicle 360 view</a></h3>
    <div class="wrap"><table>
      <tr><th>Database</th><th>Record and its FK-PK joins</th><th>Why it is in the context</th><th>Score</th></tr>
      {% for e in v.entities %}
      <tr><td><b>{{ e.device }}</b><br><small>round {{ e.hops|join(' + ') }}</small></td>
          <td>{{ e.summary }}<div class="path">{{ e.path|join(' -> ') }}</div></td>
          <td class="why">{{ e.matched|join('; ') }}</td><td>{{ (e.score * 100)|round|int }}%</td></tr>
      {% endfor %}
    </table></div>
    {% if v.documents %}<p class="why">Documents about this vehicle: {% for d in v.documents %}<a href="{{ d.url }}" target="_blank">{{ d.title }}</a> ({{ d.device }}, {{ d.how }}){% if not loop.last %}; {% endif %}{% endfor %}</p>{% endif %}
  </div>
  {% endfor %}
  <h3>Accumulated context used for the document search</h3>
  <p>{% for t, w in out.context.terms.items() %}<span class="chip">{{ t }} {{ '%.1f'|format(w) }}</span>{% endfor %}</p>
  {% if out.context.ids %}<p class="why">Numbers: {{ out.context.ids|join(', ') }}</p>{% endif %}
  {% if out.context.phrases %}<p class="why">Places and names: {{ out.context.phrases|join(', ') }}</p>{% endif %}
</div>
{% endif %}

<div class="card">
  <h2>4. Unstructured documents that match the context</h2>
  {% set documents = out.documents + (out.background_documents|default([])) %}
  {% if out.mode == "list" and out.total is none %}<p>The vehicle count is unavailable. Only background documents can be shown.</p>{% endif %}
  {% if not documents %}<p>No document matches this context.</p>{% endif %}
  {% if documents and ((out.mode == "list" and out.total == 0) or (out.mode == "context" and not out.vehicles)) %}
  <p>No vehicles matched. The documents below cover the search topic.</p>
  {% endif %}
  {% for d in documents %}
  {% if out.mode == "list" and loop.first and not d.background %}<h3>Documents about the matched vehicles</h3>{% endif %}
  {% if d.background and (loop.first or not loop.previtem.background) %}<h3>Background context</h3>{% endif %}
  <div class="doc">
    <b><a href="{{ d.url }}" target="_blank">{{ d.title }}</a></b>
    <div class="meta">{{ d.device }} &middot; {{ d.name }} &middot; relevance {{ (d.relevance * 100)|round|int }}%
      {% for l in d.plates %} &middot; mentions {{ l.plate }} as "{{ l.written }}" ({{ l.how }}){% endfor %}
      {% if d.ids %} &middot; numbers: {{ d.ids|join(', ') }}{% endif %}</div>
    <p>{{ hl(d.snippet, d.highlight, d.plates|map(attribute='written')|list) }}</p>
    <div class="meta">Context words in it: {{ d.matched_terms|join(', ') }}</div>
  </div>
  {% endfor %}
</div>
{% endif %}
</div></body></html>
"""

EXAMPLES = ["cars with more than 2 violations in the last 3 months",
            "vehicles seen at least 3 times in the last month",
            "owners under 40 with at least 2 violations since August 2026",
            "uninsured vehicles from Maharashtra with 2 or more violations in the last 90 days",
            "top 10 vehicles with most challans in 2026",
            "young owners with speeding violations in the last 3 months"]
ROUND_NAMES = {1: "user query", 2: "keys found in round 1", 3: "document search"}


@app.route("/context")
def federated_context():
    q = request.args.get("q", "").strip()
    within = request.args.get("within", "").strip() or None
    out = context_engine.run(q, within=within) if q else None
    return render_template_string(CONTEXT_PAGE, q=q, out=out, within=within, examples=EXAMPLES, hl=highlight,
                                  round_names=ROUND_NAMES)


@app.route("/api/context")
def federated_context_api():
    """The same result as JSON."""
    return jsonify(context_engine.run(request.args.get("q", "").strip(),
                                      within=request.args.get("within", "").strip() or None))


@app.route("/docs/<path:name>")
def news_document(name):
    d = context_engine.NEWS.get(name)
    if d is None:
        abort(404)
    return (f"<!doctype html><meta charset='utf-8'><title>{escape(d['title'])}</title>"
            f"<body style='font-family:Georgia,serif;max-width:720px;margin:40px auto;padding:0 16px'>"
            f"<p><a href='/context'>Federated context search</a> &middot; news/{escape(name)}</p>"
            f"<pre style='white-space:pre-wrap;font-family:inherit;font-size:16px;line-height:1.6'>{escape(d['text'])}</pre></body>")


if __name__ == "__main__":
    print("Client GUI running at http://127.0.0.1:5000")
    app.run(host="0.0.0.0", port=5000, debug=False)
