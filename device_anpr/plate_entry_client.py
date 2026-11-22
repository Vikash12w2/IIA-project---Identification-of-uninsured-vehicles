"""Plate dictionary of a source service, kept in its own file next to the database (plate_book.py).

Each service (ANPR, Insurance, RTO, Theft/Shredding) creates one PlateDesk for its own database(s). At the first
service start the existing plates seed the dictionary with their generated noisy variants (PlateBook.build).
Records are saved as typed: there is no plate check at entry. Automatic data cleansing (cleansing.py) uses the
dictionary as one piece of supporting evidence and adds every misread it corrects.
"""
import os
import sqlite3
import sys
from html import escape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plate_book import PlateBook  # noqa: E402


class Source:
    """One database of a service. `db_path` is a callable so the service's DB_PATH is read at call time.

    context(row) -> {color, vehicle_type, make_model} for databases that know the vehicle (ANPR, RTO), else None.
    image_url(row) -> URL of the plate image kept with a record, or None (ANPR only).
    """
    def __init__(self, name, db_path, table, id_column, plate_column, record_type, context=None, image_url=None):
        self.name, self.db_path, self.table = name, db_path, table
        self.id_column, self.plate_column, self.record_type, self.context = id_column, plate_column, record_type, context
        self.image_url = image_url


class PlateDesk:
    def __init__(self, book_path, sources):
        self.book = PlateBook(book_path)
        self.sources = {s.name: s for s in sources}

    def rows(self, source):
        src = self.sources[source]
        conn = sqlite3.connect(src.db_path())
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {src.table} ORDER BY {src.id_column}")]
        finally:
            conn.close()

    def start(self):
        """Service start: the first build seeds the existing plates with their noisy variants (see PlateBook.build)."""
        return {name: self.book.build(name, [r[src.plate_column] for r in self.rows(name)])
                for name, src in self.sources.items()}

    def register(self, app):
        """Add /plates/dictionary to the service's Flask app."""
        from flask import request

        def dictionary_page():
            q = request.args.get("q", "").strip()
            return _dictionary_html({name: self.book.grouped(name, q) for name in self.sources}, q)

        app.add_url_rule("/plates/dictionary", "plates_dictionary", dictionary_page)


STYLE = """<style>body{font-family:Arial;max-width:1100px;margin:40px auto;padding:0 20px;color:#222}
button{padding:7px 14px;margin:4px 2px}input[type=text]{padding:6px}table{border-collapse:collapse;width:100%}
td,th{border:1px solid #ddd;padding:6px;text-align:left;font-size:14px;vertical-align:top}
th{background:#1a3d6d;color:#fff}.muted{color:#777}
code{background:#eef5ff;padding:1px 5px;border-radius:3px;margin:0 4px 4px 0;display:inline-block}
code.learned{background:#eafaf1;font-weight:bold}h3{color:#1a3d6d;margin:26px 0 4px}</style>"""


def _dictionary_html(dictionaries, q):
    sections = []
    for source, entries in dictionaries.items():
        learned = sum(1 for e in entries for _, o in e["variants"] if o != "generated")
        rows = []
        for e in entries:
            mine = "".join(f'<code class="learned">{escape(v)}</code>' for v, o in e["variants"] if o != "generated")
            generated = [v for v, o in e["variants"] if o == "generated"]
            gen = (f'<details><summary class="muted">{len(generated)} generated when the dictionary was first built</summary>'
                   + "".join(f"<code>{escape(v)}</code>" for v in generated) + "</details>") if generated else ""
            rows.append(f'<tr><td><b>{escape(e["plate"])}</b><br><small class="muted">{escape(e["origin"])}</small></td>'
                        f'<td>{mine or ("" if gen else "<span class=muted>none yet</span>")}{gen}</td></tr>')
        sections.append(f'<h3>{escape(source)}</h3><p><b>{len(entries)}</b> correct plates, <b>{learned}</b> noisy versions '
                        f'learned.</p><table><tr><th>Correct plate</th><th>Noisy versions mapped to it</th></tr>'
                        f'{"".join(rows) or "<tr><td colspan=2>No plates.</td></tr>"}</table>')
    return f"""<!doctype html><html><head><title>Plate dictionary</title>{STYLE}</head><body>
<h2>Plate dictionary</h2><p><a href="/">&larr; Back</a> &nbsp;|&nbsp; <a href="/plates/corrected">Corrected number plates</a>
&nbsp;|&nbsp; <a href="/cleansing-report">Cleansing report</a></p>
<p>This database's own dictionary (kept in <code>plate_dictionary.db</code> next to it). The plates stored when it was first built
come with generated noisy versions; later ones are learned from the misreads that automatic data cleansing corrected
(<code class="learned">bold</code>). Cleansing uses the dictionary only as supporting evidence, never on its own.</p>
<form method="get"><input type="text" name="q" value="{escape(q)}" placeholder="Filter by plate"><button type="submit">Filter</button></form>
{"".join(sections)}</body></html>"""
