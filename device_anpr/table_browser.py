"""Read-only table selection for the device home page."""

from flask import request


PAGE_SIZE = 100

CONTROLS = """
<form method="get" action="{{ url_for('admin_home') }}#database-tables" id="database-tables"
      style="display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;margin:1rem 0">
    <label for="database-table-select"><strong>Table</strong></label>
    <select id="database-table-select" name="table" onchange="this.form.submit()"
            style="font:inherit;color:inherit;background:inherit;padding:.4rem .65rem;border-radius:.35rem">
        {% for name in table_view.names %}
        <option value="{{ name }}"{% if name == table_view.name %} selected{% endif %}>
            {{ name.replace('_', ' ').title() }}
        </option>
        {% endfor %}
    </select>
    <noscript><button type="submit">Show table</button></noscript>
</form>
"""

RECORDS = """
{% if table_view.columns %}
<div class="table-wrapper" style="overflow-x:auto">
    <table>
        <thead><tr>{% for column in table_view.columns %}<th scope="col">{{ column }}</th>{% endfor %}</tr></thead>
        <tbody>
        {% for row in table_view.rows %}
        <tr>{% for value in row %}<td>{{ value if value is not none else '\u2014' }}</td>{% endfor %}</tr>
        {% else %}
        <tr><td colspan="{{ table_view.columns|length }}">No records in this table.</td></tr>
        {% endfor %}
        </tbody>
    </table>
</div>
{% if table_view.total %}
<nav aria-label="Table pages" style="display:flex;align-items:center;gap:1rem;flex-wrap:wrap;margin:1rem 0">
    {% if table_view.page > 1 %}
    <a href="{{ url_for('admin_home', table=table_view.name, table_page=table_view.page - 1, _anchor='database-tables') }}">Previous</a>
    {% endif %}
    <span>Rows {{ table_view.start }}&#8211;{{ table_view.end }} of {{ table_view.total }}</span>
    {% if table_view.end < table_view.total %}
    <a href="{{ url_for('admin_home', table=table_view.name, table_page=table_view.page + 1, _anchor='database-tables') }}">Next</a>
    {% endif %}
</nav>
{% endif %}
{% else %}
<p>This table is unavailable. Start the device with its current service code to initialize its database.</p>
{% endif %}
"""


def browse(conn, names, custom_tables=1):
    """Load one page from a known table. Custom tables use their existing views."""
    name = request.args.get("table")
    if name not in names:
        name = names[0]
    view = {"name": name, "names": names, "columns": ()}
    if name in names[:custom_tables]:
        return view

    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone():
        return view

    # The identifier above was selected from the service's fixed list of tables.
    total = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
    last_page = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(last_page, max(1, request.args.get("table_page", 1, type=int)))
    offset = (page - 1) * PAGE_SIZE
    cursor = conn.execute(
        f'SELECT * FROM "{name}" ORDER BY rowid LIMIT ? OFFSET ?', (PAGE_SIZE, offset)
    )
    rows = cursor.fetchall()
    view.update(columns=[col[0] for col in cursor.description], rows=rows,
                page=page, total=total, start=offset + 1, end=offset + len(rows))
    return view
