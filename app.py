from flask import Flask, request, redirect, flash, get_flashed_messages, session, jsonify
from markupsafe import escape
from urllib.parse import urlencode, quote
from db import get_connection
import csv
import io
import os
import json
import secrets
import hmac

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-nahradit-pro-produkci")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

SESSION_EDIT_PREFIX = "edit_deg_"
SESSION_REZIM_PREFIX = "rezim_deg_"
SESSION_KOMISE_PREFIX = "komise_deg_"
SESSION_EDIT_ROW_PREFIX = "edit_row_deg_"
_KOMISE_VELIKOST = 30

_KOMISE_EXTRA_COLS = (
    ("body_barva", "REAL"),
    ("body_cistota", "REAL"),
    ("body_vune", "REAL"),
    ("body_chut", "REAL"),
    ("poznamka", "TEXT"),
)

SORTABLE = ("cislo", "nazev", "adresa", "odruda", "privlastek", "rocnik", "body")
DEFAULT_SORT = "body"
DEFAULT_DIR = "desc"


def init_db():
    conn = get_connection()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS degustace (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazev TEXT,
            datum TEXT,
            pocet_komisi INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vzorky (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            degustace_id INTEGER,
            cislo INTEGER,
            nazev TEXT,
            adresa TEXT,
            odruda TEXT,
            privlastek TEXT,
            rocnik TEXT,
            body REAL,
            body_barva REAL,
            body_cistota REAL,
            body_vune REAL,
            body_chut REAL,
            poznamka TEXT,
            komise_cislo INTEGER
        )
    """)

    cur = conn.execute("PRAGMA table_info(degustace)")
    exist_deg = {row[1] for row in cur.fetchall()}
    if "pocet_komisi" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN pocet_komisi INTEGER")
    if "katalog_top_x" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN katalog_top_x INTEGER")
    if "katalog_format" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN katalog_format TEXT")
    if "katalog_font_pt" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN katalog_font_pt INTEGER")
    if "hodnoceni_token" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN hodnoceni_token TEXT")
    for i in range(1, 5):
        if f"hodn_b{i}_label" not in exist_deg:
            conn.execute(f"ALTER TABLE degustace ADD COLUMN hodn_b{i}_label TEXT")
        if f"hodn_b{i}_max" not in exist_deg:
            conn.execute(f"ALTER TABLE degustace ADD COLUMN hodn_b{i}_max INTEGER")

    cur = conn.execute("PRAGMA table_info(vzorky)")
    exist = {row[1] for row in cur.fetchall()}
    for col, typ in _KOMISE_EXTRA_COLS:
        if col not in exist:
            conn.execute(f"ALTER TABLE vzorky ADD COLUMN {col} {typ}")
    if "komise_cislo" not in exist:
        conn.execute("ALTER TABLE vzorky ADD COLUMN komise_cislo INTEGER")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS komise_porotci (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            degustace_id INTEGER NOT NULL,
            komise_cislo INTEGER NOT NULL,
            jmena TEXT,
            UNIQUE (degustace_id, komise_cislo)
        )
    """)

    conn.commit()
    conn.close()


def format_body_hodnota(hodnota):
    if hodnota is None:
        return ""
    return f"{float(hodnota):.1f}".replace(".", ",")


def format_datum_cz(datum_raw):
    if not datum_raw:
        return ""
    s = str(datum_raw).strip()
    parts = s.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        y, m, d = parts
        return f"{int(d):02d}.{int(m):02d}.{y}"
    return s


def _parse_sc_float(raw):
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and raw != raw:
            return None
        return float(raw)
    s = (str(raw) or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _hodnoceni_labels_maxes_from_deg(deg_row):
    lb = [
        (deg_row["hodn_b1_label"] or "").strip() or "Barva",
        (deg_row["hodn_b2_label"] or "").strip() or "Čistota",
        (deg_row["hodn_b3_label"] or "").strip() or "Vůně",
        (deg_row["hodn_b4_label"] or "").strip() or "Chuť",
    ]
    defaults = (2, 2, 4, 12)
    mx = []
    for i in range(4):
        k = f"hodn_b{i + 1}_max"
        try:
            v = deg_row[k]
            if v is None:
                mx.append(defaults[i])
            else:
                vi = int(v)
                mx.append(max(1, min(100, vi)))
        except (TypeError, ValueError):
            mx.append(defaults[i])
    return lb, mx


def _hodnoceni_token_ok(stored, given):
    if not stored or not given:
        return False
    try:
        return hmac.compare_digest(str(stored), str(given))
    except Exception:
        return False


def _validate_komise_partials(deg_row, bb, bc, bv, bch, require_all=False):
    labels, maxes = _hodnoceni_labels_maxes_from_deg(deg_row)
    vals = [bb, bc, bv, bch]
    if require_all:
        if any(x is None for x in vals):
            return False, "Vyplňte všechna čtyři kritéria."
    for i, x in enumerate(vals):
        if x is None:
            continue
        try:
            fx = float(x)
        except (TypeError, ValueError):
            return False, "Neplatná číselná hodnota."
        if fx < 0 or fx > maxes[i]:
            return False, f"Hodnota „{labels[i]}“ musí být v rozsahu 0–{maxes[i]}."
    return True, None


def _komise_update_vzorek_body(conn, degustace_id, vzorek_id, bb, bc, bv, bch, poz):
    parts = [x for x in (bb, bc, bv, bch) if x is not None]
    celkem = round(sum(parts), 1) if parts else None
    conn.execute(
        """
        UPDATE vzorky SET body_barva=?, body_cistota=?, body_vune=?, body_chut=?, body=?, poznamka=?
        WHERE id=? AND degustace_id=?
        """,
        (bb, bc, bv, bch, celkem, poz or None, vzorek_id, degustace_id),
    )


def _komise_pocet(pocet_vzorku):
    if pocet_vzorku <= 0:
        return 1
    return (pocet_vzorku + _KOMISE_VELIKOST - 1) // _KOMISE_VELIKOST


def _degustace_pocet_komisi(degustace_row, pocet_vzorku):
    pk = None
    try:
        pk = degustace_row["pocet_komisi"]
    except Exception:
        pk = None
    try:
        pk_i = int(pk) if pk is not None else 0
    except (TypeError, ValueError):
        pk_i = 0
    if pk_i <= 0:
        return _komise_pocet(pocet_vzorku)
    return pk_i


def _komise_prirazeni_existuje(vzorky):
    return any(v["komise_cislo"] is not None for v in vzorky)


def _komise_generovat_prirazeni(conn, degustace_id, pocet_komisi):
    """
    Stabilní MVP rozdělení:
    - seřadí vzorky podle (odrůda, jakost, ročník, číslo),
    - round-robin přiřadí 1..pocet_komisi,
    - uloží do `vzorky.komise_cislo`.
    """
    try:
        k = int(pocet_komisi)
    except (TypeError, ValueError):
        k = 1
    if k <= 0:
        k = 1

    vz = conn.execute(
        "SELECT id, cislo, odruda, privlastek, rocnik FROM vzorky WHERE degustace_id=?",
        (degustace_id,),
    ).fetchall()
    if not vz:
        return

    vz_sorted = sorted(
        vz,
        key=lambda r: (
            (r["odruda"] or "").casefold(),
            (r["privlastek"] or "").casefold(),
            (r["rocnik"] or "").casefold(),
            r["cislo"],
        ),
    )

    payload = []
    for idx, r in enumerate(vz_sorted):
        kom = (idx % k) + 1
        payload.append((kom, r["id"], degustace_id))

    conn.executemany(
        "UPDATE vzorky SET komise_cislo=? WHERE id=? AND degustace_id=?",
        payload,
    )
    conn.commit()


def _nacti_porotce_map(conn, degustace_id):
    rows = conn.execute(
        "SELECT komise_cislo, jmena FROM komise_porotci WHERE degustace_id=?",
        (degustace_id,),
    ).fetchall()
    return {int(r["komise_cislo"]): (r["jmena"] or "") for r in rows}


def _vzorky_pro_komisi(vzorky_seznam, komise_1based):
    i0 = (komise_1based - 1) * _KOMISE_VELIKOST
    return vzorky_seznam[i0 : i0 + _KOMISE_VELIKOST]


def _komise_celkem_zobrazit(v):
    if v["body"] is not None:
        return format_body_hodnota(v["body"])
    t = 0.0
    anyp = False
    for k in ("body_barva", "body_cistota", "body_vune", "body_chut"):
        if v[k] is not None:
            anyp = True
            t += float(v[k])
    if not anyp:
        return ""
    return f"{round(t, 1):.1f}".replace(".", ",")


def _vzorek_hodnoceni_payload(v):
    def g(k):
        if v[k] is None:
            return None
        return float(v[k])

    return {
        "id": int(v["id"]),
        "cislo": v["cislo"],
        "odruda": v["odruda"] or "",
        "privlastek": v["privlastek"] or "",
        "rocnik": v["rocnik"] or "",
        "b": [g("body_barva"), g("body_cistota"), g("body_vune"), g("body_chut")],
        "body": float(v["body"]) if v["body"] is not None else None,
        "complete": all(
            v[k] is not None for k in ("body_barva", "body_cistota", "body_vune", "body_chut")
        ),
    }


def _hodnoceni_hotovo_pocet(vzorky_rows):
    n = 0
    for v in vzorky_rows:
        if all(v[k] is not None for k in ("body_barva", "body_cistota", "body_vune", "body_chut")):
            n += 1
    return n


def _html_hodnoceni_chyba(msg):
    return f"""<!DOCTYPE html>
<html lang="cs">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hodnocení</title>
<style>
body{{font-family:Arial,sans-serif;background:#f2f4f6;margin:0;padding:24px;color:#1f2933;}}
.box{{max-width:480px;margin:40px auto;background:#fff;border:1px solid #dde2e8;border-radius:10px;padding:24px;text-align:center;}}
</style>
</head>
<body><div class="box"><p style="margin:0;">{escape(msg)}</p></div></body>
</html>"""


def _detect_delimiter(first_line):
    if not first_line or not first_line.strip():
        return "\t"
    tab = first_line.count("\t")
    comma = first_line.count(",")
    semi = first_line.count(";")
    m = max(tab, comma, semi)
    if m == 0:
        return "\t"
    if tab == m:
        return "\t"
    if semi == m:
        return ";"
    return ","


def _decode_bytes(raw):
    for enc in ("utf-8-sig", "cp1250", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def _vzorek_import_klic(nazev, odruda, privlastek, rocnik):
    return (
        (nazev or "").strip().casefold(),
        (odruda or "").strip().casefold(),
        (privlastek or "").strip().casefold(),
        (rocnik or "").strip().casefold(),
    )


def import_vzorky_z_textu(text, degustace_id):
    """
    Importuje vzorky z textu (tabulka s hlavičkou v 1. řádku).
    Názvy hlaviček se ignorují, rozhoduje jen pořadí sloupců.
    Číslo vzorku z CSV se ignoruje — přiděluje se další volné v degustaci.
    Duplicita podle (Jméno, Odrůda, Přívlastek, Rok) vůči DB i v rámci souboru → řádek přeskočen.
    Vrací slovník: ok, imported, případně error, nebo skipped (seznam stručných důvodů).
    """
    text = (text or "").lstrip("\ufeff")
    if not text.strip():
        return {"ok": False, "error": "Soubor je prázdný."}

    f = io.StringIO(text)
    first = f.readline()
    f.seek(0)
    delim = _detect_delimiter(first)

    reader = csv.reader(f, delimiter=delim)
    try:
        next(reader)  # Hlavička je povinná, ale její názvy ignorujeme.
    except StopIteration:
        return {"ok": False, "error": "Soubor je prázdný."}

    conn = get_connection()
    imported = 0
    skipped = []

    try:
        mx = conn.execute(
            "SELECT COALESCE(MAX(cislo), 0) FROM vzorky WHERE degustace_id = ?",
            (degustace_id,),
        ).fetchone()
        next_cislo = (mx[0] or 0) + 1

        existující = conn.execute(
            "SELECT nazev, odruda, privlastek, rocnik FROM vzorky WHERE degustace_id = ?",
            (degustace_id,),
        ).fetchall()
        známé_klíče = {_vzorek_import_klic(r["nazev"], r["odruda"], r["privlastek"], r["rocnik"]) for r in existující}

        for row in reader:
            if not row:
                continue
            nazev = (row[1] if len(row) > 1 else "").strip()
            adresa = (row[2] if len(row) > 2 else "").strip()
            odruda = (row[3] if len(row) > 3 else "").strip()
            privlastek = (row[4] if len(row) > 4 else "").strip()
            rocnik = (row[5] if len(row) > 5 else "").strip()
            body_raw = (row[6] if len(row) > 6 else "").strip()

            if not nazev:
                continue

            klíč = _vzorek_import_klic(nazev, odruda, privlastek, rocnik)
            if klíč in známé_klíče:
                if len(skipped) < 12:
                    skipped.append(f"duplicita: {nazev} ({odruda}, {rocnik})")
                continue

            body = None
            if body_raw:
                try:
                    body = float(body_raw.replace(",", "."))
                except ValueError:
                    body = None

            conn.execute("""
                INSERT INTO vzorky (
                    degustace_id, cislo, nazev, adresa, odruda, privlastek, rocnik, body
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                degustace_id,
                next_cislo,
                nazev,
                adresa,
                odruda,
                privlastek,
                rocnik,
                body
            ))
            známé_klíče.add(klíč)
            next_cislo += 1
            imported += 1

        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return {"ok": False, "error": f"Při importu došlo k chybě: {e}"}

    conn.close()

    if imported == 0 and not skipped:
        return {
            "ok": False,
            "error": "Nepodařilo se naimportovat žádný řádek. Zkontrolujte pořadí sloupců a oddělovače.",
        }

    out = {"ok": True, "imported": imported, "skipped": skipped}
    if imported == 0 and skipped:
        out["ok"] = False
        out["error"] = "Žádný řádek nebyl importován. " + " ".join(skipped[:3])
    return out


def _html_flash_zprávy():
    zprávy = get_flashed_messages(with_categories=True)
    if not zprávy:
        return ""
    bloky = []
    for kategorie, text in zprávy:
        t = escape(text)
        if kategorie == "error":
            barva = "#8b1538"
            pozadí = "#fde8ec"
        else:
            barva = "#1a5d1a"
            pozadí = "#e8f5e9"
        bloky.append(
            f'<div class="flash-msg" style="position:relative;padding:10px 36px 10px 14px;margin-bottom:12px;border-radius:6px;'
            f'border:1px solid {barva};background:{pozadí};color:#222;">'
            f'<button type="button" class="flash-close" aria-label="Zavřít">×</button>{t}</div>'
        )
    return '<div class="flash-wrap" style="max-width:1280px;margin:0 auto 16px;padding:0 20px;">' + "".join(bloky) + "</div>"


def _view_state():
    sort = request.values.get("sort") or DEFAULT_SORT
    if sort not in SORTABLE:
        sort = DEFAULT_SORT
    dir_ = request.values.get("dir") or DEFAULT_DIR
    if dir_ not in ("asc", "desc"):
        dir_ = DEFAULT_DIR
    q = (request.values.get("q") or "").strip()
    return {"sort": sort, "dir": dir_, "q": q}


def _build_degustace_url(deg_id, sort, dir_, q, fb=None):
    clean = {}
    if sort:
        clean["sort"] = sort
    if dir_:
        clean["dir"] = dir_
    if q:
        clean["q"] = q
    if fb is not None:
        clean["fb"] = str(int(fb))
    qs = urlencode(clean)
    return f"/degustace/{deg_id}" + ("?" + qs if qs else "")


def _sort_href(deg_id, col, cur_sort, cur_dir, q):
    if col == cur_sort:
        new_dir = "asc" if cur_dir == "desc" else "desc"
    else:
        new_dir = "desc" if col == "body" else "asc"
    return _build_degustace_url(deg_id, col, new_dir, q)


def _sort_symbol(col, cur_sort, cur_dir):
    if col != cur_sort:
        return '<span class="sort-muted" title="Řadit">↕</span>'
    if cur_dir == "desc":
        return '<span class="sort-active" title="Sestupně">▼</span>'
    return '<span class="sort-active" title="Vzestupně">▲</span>'


def _row_text_blob(v):
    b = v["body"]
    body_raw = "" if b is None else str(b)
    body_cz = format_body_hodnota(b)
    parts = [
        str(v["cislo"]),
        v["nazev"] or "",
        v["adresa"] or "",
        v["odruda"] or "",
        v["privlastek"] or "",
        v["rocnik"] or "",
        body_raw,
        body_cz,
    ]
    return " ".join(parts).lower()


def _filter_vzorky(vzorky, q_raw):
    if not q_raw or not q_raw.strip():
        return list(vzorky)
    words = [w for w in q_raw.split() if w.strip()]
    if not words:
        return list(vzorky)
    out = []
    for v in vzorky:
        blob = _row_text_blob(v)
        if all(w.lower() in blob for w in words):
            out.append(v)
    return out


def _poradi_podle_bodu(vzorky_all):
    """Pořadí podle bodů (nejvyšší první), stejné body řeší číslo vzorku. Bez bodů v žebříčku není."""
    scored = [v for v in vzorky_all if v["body"] is not None]
    scored.sort(key=lambda v: (-float(v["body"]), v["cislo"]))
    return {v["id"]: i + 1 for i, v in enumerate(scored)}


def _sort_vzorky(vzorky, sort_key, sort_dir):
    reverse = sort_dir == "desc"

    if sort_key == "cislo":
        return sorted(vzorky, key=lambda v: v["cislo"], reverse=reverse)

    if sort_key == "body":
        with_score = [v for v in vzorky if v["body"] is not None]
        without = [v for v in vzorky if v["body"] is None]
        with_score.sort(key=lambda v: v["cislo"])
        with_score.sort(key=lambda v: float(v["body"]), reverse=reverse)
        return with_score + without

    col = sort_key

    def key_text(v):
        return ((v[col] or "").casefold(), v["cislo"])

    return sorted(vzorky, key=key_text, reverse=reverse)


def _preserve_hidden(sort, dir_, q):
    h = f'<input type="hidden" name="sort" value="{escape(sort)}">'
    h += f'<input type="hidden" name="dir" value="{escape(dir_)}">'
    if q:
        h += f'<input type="hidden" name="q" value="{escape(q)}">'
    return h


@app.route("/", methods=["GET", "POST"])
def home():
    conn = get_connection()

    if request.method == "POST":
        action = request.form.get("action")

        if action == "nova_degustace":
            pocet_komisi = 3
            conn.execute(
                "INSERT INTO degustace (nazev, datum, pocet_komisi) VALUES (?, ?, ?)",
                (request.form["nazev"], request.form["datum"], pocet_komisi)
            )
            conn.commit()
            conn.close()
            return redirect("/")

        elif action == "vyber":
            conn.close()
            deg_id = str(request.form["degustace_id"])
            session[SESSION_REZIM_PREFIX + deg_id] = "seznam"
            session[SESSION_EDIT_PREFIX + deg_id] = False
            session[SESSION_KOMISE_PREFIX + deg_id] = 1
            session.modified = True
            return redirect(f"/degustace/{deg_id}")

    degustace = conn.execute(
        "SELECT * FROM degustace ORDER BY datum DESC, id DESC"
    ).fetchall()

    conn.close()

    html = """
    <html>
    <head>
        <title>Degustace vín</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 1100px;
                margin: 30px auto;
                padding: 0 20px;
                color: #222;
                background: #f7f7f7;
            }
            .box {
                border: 1px solid #d9d9d9;
                border-radius: 8px;
                padding: 18px;
                margin-bottom: 20px;
                background: white;
            }
            h1, h2 {
                margin-bottom: 10px;
            }
            input, button {
                padding: 8px 10px;
                margin: 4px 0;
                font-size: 14px;
            }
            button {
                cursor: pointer;
            }
            .menu-button {
                min-width: 320px;
                text-align: left;
            }
        </style>
    </head>
    <body>
        <h1>Degustace vín - správa a vyhodnocení bodovaných degustací</h1>

        <div class="box">
            <h2>Nová degustace</h2>
            <form method="post">
                <input type="hidden" name="action" value="nova_degustace">
                <div>
                    Název degustace<br>
                    <input name="nazev" required style="width: 320px;">
                </div>
                <div>
                    Datum<br>
                    <input type="date" name="datum" required>
                </div>
                <div>
                    <button type="submit">Vytvořit degustaci</button>
                </div>
            </form>
        </div>

        <div class="box">
            <h2>Seznam degustací</h2>
    """

    if degustace:
        for d in degustace:
            html += f"""
            <form method="post" style="margin:6px 0;">
                <input type="hidden" name="action" value="vyber">
                <input type="hidden" name="degustace_id" value="{d['id']}">
                <button class="menu-button" type="submit">{d['nazev']} ({d['datum']})</button>
            </form>
            """
    else:
        html += "<p>Zatím není založena žádná degustace.</p>"

    html += """
        </div>
    </body>
    </html>
    """

    return html


@app.route("/degustace/<int:id>", methods=["GET", "POST"])
def detail(id):
    conn = get_connection()

    if request.method == "POST":
        action = request.form.get("action")
        st = {
            "sort": request.form.get("sort") or DEFAULT_SORT,
            "dir": request.form.get("dir") or DEFAULT_DIR,
            "q": (request.form.get("q") or "").strip(),
        }
        if st["sort"] not in SORTABLE:
            st["sort"] = DEFAULT_SORT
        if st["dir"] not in ("asc", "desc"):
            st["dir"] = DEFAULT_DIR

        red = _build_degustace_url(id, st["sort"], st["dir"], st["q"])

        if action == "set_edit":
            key = SESSION_EDIT_PREFIX + str(id)
            session[key] = request.form.get("edit") == "1"
            session.modified = True
            conn.close()
            return redirect(red)

        if action == "set_rezim":
            r = request.form.get("rezim") or "seznam"
            if r not in ("seznam", "komise", "nastaveni", "katalog"):
                r = "seznam"
            session[SESSION_REZIM_PREFIX + str(id)] = r
            if r == "komise" and session.get(SESSION_EDIT_PREFIX + str(id), False):
                kk = SESSION_KOMISE_PREFIX + str(id)
                if session.get(kk, 1) == -1:
                    session[kk] = 1
            session.modified = True
            conn.close()
            return redirect(red)

        if action == "set_pocet_komisi":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            raw_pk = request.form.get("pocet_komisi") or "3"
            try:
                pk = int(raw_pk)
            except ValueError:
                pk = 3
            pk = max(1, min(10, pk))
            conn.execute(
                "UPDATE degustace SET pocet_komisi = ? WHERE id = ?",
                (pk, id),
            )
            conn.commit()
            _komise_generovat_prirazeni(conn, id, pk)
            kk = SESSION_KOMISE_PREFIX + str(id)
            raw_k = session.get(kk, 1)
            if raw_k in (-1, "-1", "vse"):
                pass
            else:
                try:
                    ki = int(raw_k)
                    if ki > pk:
                        session[kk] = pk
                except (TypeError, ValueError):
                    session[kk] = 1
            session.modified = True
            flash("Počet komisí byl uložen a přiřazení vzorků bylo přepočítáno.", "success")
            conn.close()
            return redirect(red)

        if action == "set_katalog_nastaveni":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            raw_top = (request.form.get("katalog_top_x") or "").strip()
            if raw_top:
                try:
                    top_x = int(raw_top)
                except ValueError:
                    top_x = 15
                top_x = max(1, min(200, top_x))
            else:
                top_x = 15
            fmt = (request.form.get("katalog_format") or "A4").strip().upper()
            if fmt not in ("A4", "A5"):
                fmt = "A4"
            raw_font = (request.form.get("katalog_font_pt") or "").strip()
            if raw_font:
                try:
                    font_pt = int(raw_font)
                except ValueError:
                    font_pt = 8
                font_pt = max(6, min(10, font_pt))
            else:
                font_pt = 8
            conn.execute(
                "UPDATE degustace SET katalog_top_x = ?, katalog_format = ?, katalog_font_pt = ? WHERE id = ?",
                (top_x, fmt, font_pt, id),
            )
            conn.commit()
            flash("Nastavení katalogu bylo uloženo.", "success")
            conn.close()
            return redirect(red)

        if action == "smaz_vse_vzorky":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            conn.execute(
                "DELETE FROM vzorky WHERE degustace_id = ?",
                (id,),
            )
            conn.commit()
            flash("Všechny vzorky degustace byly smazány.", "success")
            conn.close()
            return redirect(red)

        if action == "set_komise":
            k = request.form.get("komise") or "1"
            edit_now = session.get(SESSION_EDIT_PREFIX + str(id), False)
            if k == "vse" and not edit_now:
                session[SESSION_KOMISE_PREFIX + str(id)] = -1
            else:
                try:
                    session[SESSION_KOMISE_PREFIX + str(id)] = max(1, int(k))
                except ValueError:
                    session[SESSION_KOMISE_PREFIX + str(id)] = 1
            session.modified = True
            conn.close()
            return redirect(red)

        if action == "edit_row":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "seznam":
                conn.close()
                return redirect(red)
            try:
                vid = int(request.form.get("vzorek_id") or "0")
            except ValueError:
                vid = 0
            key_row = SESSION_EDIT_ROW_PREFIX + str(id)
            session[key_row] = vid if vid > 0 else None
            session.modified = True
            conn.close()
            return redirect(red)

        if action == "edit_row_cancel":
            key_row = SESSION_EDIT_ROW_PREFIX + str(id)
            session.pop(key_row, None)
            session.modified = True
            conn.close()
            return redirect(red)

        if action == "update_vzorek":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "seznam":
                conn.close()
                return redirect(red)
            try:
                vid = int(request.form.get("vzorek_id") or "0")
            except ValueError:
                vid = 0
            if vid > 0:
                nazev = (request.form.get("nazev") or "").strip()
                adresa = (request.form.get("adresa") or "").strip()
                odruda = (request.form.get("odruda") or "").strip()
                privlastek = (request.form.get("privlastek") or "").strip()
                rocnik = (request.form.get("rocnik") or "").strip()
                conn.execute(
                    """
                    UPDATE vzorky
                    SET nazev = ?, adresa = ?, odruda = ?, privlastek = ?, rocnik = ?
                    WHERE id = ? AND degustace_id = ?
                    """,
                    (nazev, adresa, odruda, privlastek, rocnik, vid, id),
                )
                conn.commit()
            key_row = SESSION_EDIT_ROW_PREFIX + str(id)
            session.pop(key_row, None)
            session.modified = True
            conn.close()
            return redirect(red)

        if action == "porotci_uloz":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") not in ("komise", "nastaveni"):
                conn.close()
                return redirect(red)
            try:
                k = int(request.form.get("komise_cislo") or "1")
            except ValueError:
                k = 1
            if k < 1:
                k = 1
            jmena = (request.form.get("jmena") or "").strip()
            conn.execute(
                "INSERT INTO komise_porotci (degustace_id, komise_cislo, jmena) "
                "VALUES (?, ?, ?) "
                "ON CONFLICT(degustace_id, komise_cislo) DO UPDATE SET jmena=excluded.jmena",
                (id, k, jmena or None),
            )
            conn.commit()
            flash("Porotci byli uloženi.", "success")
            conn.close()
            return redirect(red)

        if action == "hodnoceni_nastaveni":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                return redirect(red)
            labels = []
            maxes = []
            mx_def = (2, 2, 4, 12)
            for i in range(1, 5):
                labels.append((request.form.get(f"hodn_b{i}_label") or "").strip())
                raw_m = (request.form.get(f"hodn_b{i}_max") or "").strip()
                try:
                    m = int(raw_m) if raw_m else None
                except ValueError:
                    m = None
                if m is None:
                    m = mx_def[i - 1]
                else:
                    m = max(1, min(100, m))
                maxes.append(m)
            cur_t = conn.execute(
                "SELECT hodnoceni_token FROM degustace WHERE id=?",
                (id,),
            ).fetchone()
            tok = (cur_t["hodnoceni_token"] or "").strip() if cur_t else ""
            if not tok:
                tok = secrets.token_urlsafe(24)
            conn.execute(
                """
                UPDATE degustace SET
                    hodn_b1_label=?, hodn_b2_label=?, hodn_b3_label=?, hodn_b4_label=?,
                    hodn_b1_max=?, hodn_b2_max=?, hodn_b3_max=?, hodn_b4_max=?,
                    hodnoceni_token=?
                WHERE id=?
                """,
                (
                    labels[0] or None,
                    labels[1] or None,
                    labels[2] or None,
                    labels[3] or None,
                    maxes[0],
                    maxes[1],
                    maxes[2],
                    maxes[3],
                    tok,
                    id,
                ),
            )
            conn.commit()
            flash("Nastavení mobilního hodnocení bylo uloženo.", "success")
            conn.close()
            return redirect(red)

        if action == "hodnoceni_token_obnovit":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                return redirect(red)
            new_tok = secrets.token_urlsafe(24)
            conn.execute(
                "UPDATE degustace SET hodnoceni_token=? WHERE id=?",
                (new_tok, id),
            )
            conn.commit()
            flash("Odkaz pro mobilní hodnocení byl obnoven — staré QR kódy přestaly platit.", "success")
            conn.close()
            return redirect(red)

        if action == "komise_uloz":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "komise":
                conn.close()
                return redirect(red)
            try:
                vid = int(request.form["vzorek_id"])
            except (KeyError, ValueError, TypeError):
                conn.close()
                return redirect(red)
            bb = _parse_sc_float(request.form.get("body_barva"))
            bc = _parse_sc_float(request.form.get("body_cistota"))
            bv = _parse_sc_float(request.form.get("body_vune"))
            bch = _parse_sc_float(request.form.get("body_chut"))
            poz = (request.form.get("poznamka") or "").strip()
            deg_row = conn.execute("SELECT * FROM degustace WHERE id=?", (id,)).fetchone()
            ok, err = _validate_komise_partials(deg_row, bb, bc, bv, bch, require_all=False)
            if not ok:
                flash(err, "error")
                conn.close()
                return redirect(red)
            _komise_update_vzorek_body(conn, id, vid, bb, bc, bv, bch, poz)
            conn.commit()
            # Fokus po uložení: další vzorek v aktuální komisi (podle pořadí cislo)
            raw_k = session.get(SESSION_KOMISE_PREFIX + str(id), 1)
            try:
                komise_sel_post = int(raw_k)
            except (TypeError, ValueError):
                komise_sel_post = 1
            if komise_sel_post < 1:
                komise_sel_post = 1
            ids_rows = conn.execute(
                "SELECT id FROM vzorky WHERE degustace_id=? AND komise_cislo=? ORDER BY cislo",
                (id, komise_sel_post),
            ).fetchall()
            tab_ids = [r[0] for r in ids_rows]
            next_fb = None
            try:
                j = tab_ids.index(vid)
                if j + 1 < len(tab_ids):
                    next_fb = tab_ids[j + 1]
            except (ValueError, IndexError):
                next_fb = None
            conn.close()
            red = _build_degustace_url(id, st["sort"], st["dir"], st["q"], next_fb)
            return redirect(red)

        if action == "pridej":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "seznam":
                conn.close()
                return redirect(red)
            row = conn.execute(
                "SELECT COALESCE(MAX(cislo), 0) + 1 FROM vzorky WHERE degustace_id = ?",
                (id,)
            ).fetchone()

            cislo = row[0]

            conn.execute("""
                INSERT INTO vzorky (degustace_id, cislo, nazev, adresa, odruda, privlastek, rocnik)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                id,
                cislo,
                request.form.get("nazev", "").strip(),
                request.form.get("adresa", "").strip(),
                request.form.get("odruda", "").strip(),
                request.form.get("privlastek", "").strip(),
                request.form.get("rocnik", "").strip()
            ))
            conn.commit()
            conn.close()
            return redirect(red)

        elif action == "smaz":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "seznam":
                conn.close()
                return redirect(red)
            vid = request.form.get("vzorek_id")
            if vid:
                conn.execute(
                    "DELETE FROM vzorky WHERE id = ? AND degustace_id = ?",
                    (vid, id),
                )
                conn.commit()
                flash("Vzorek byl odstraněn.", "success")
            conn.close()
            return redirect(red)

        elif action == "body":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "seznam":
                conn.close()
                return redirect(red)
            body_raw = (request.form.get("body") or "").strip()

            if body_raw:
                body_raw = body_raw.replace(",", ".")
                try:
                    body = float(body_raw)
                except ValueError:
                    body = None
            else:
                body = None

            conn.execute(
                "UPDATE vzorky SET body = ? WHERE id = ?",
                (body, request.form["vzorek_id"])
            )
            conn.commit()
            conn.close()
            return redirect(red)

        elif action == "import":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "seznam":
                conn.close()
                return redirect(red)
            soubor = request.files.get("soubor")
            conn.close()
            if not soubor or not soubor.filename:
                flash("Vyberte prosím soubor k importu.", "error")
                return redirect(red)
            raw = soubor.read()
            text = _decode_bytes(raw)
            if text is None:
                flash(
                    "Soubor se nepodařilo přečíst. Uložte ho z Excelu znovu jako text UTF-8 nebo zkuste CSV.",
                    "error"
                )
                return redirect(red)
            result = import_vzorky_z_textu(text, id)
            if not result.get("ok"):
                flash(result.get("error", "Import se nezdařil."), "error")
            else:
                zpr = f'Importováno vzorků: {result["imported"]}.'
                skip = result.get("skipped") or []
                if skip:
                    zpr += " Přeskočeno: " + "; ".join(skip[:5])
                    if len(skip) > 5:
                        zpr += " …"
                flash(zpr, "success")
            return redirect(red)

    degustace = conn.execute(
        "SELECT * FROM degustace WHERE id = ?",
        (id,)
    ).fetchone()

    vzorky = conn.execute(
        "SELECT * FROM vzorky WHERE degustace_id = ? ORDER BY cislo",
        (id,)
    ).fetchall()

    pocet_komisi = _degustace_pocet_komisi(degustace, len(vzorky))
    rezim_for_auto = session.get(SESSION_REZIM_PREFIX + str(id), "seznam")
    if rezim_for_auto == "komise" and not _komise_prirazeni_existuje(vzorky):
        _komise_generovat_prirazeni(conn, id, pocet_komisi)
        vzorky = conn.execute(
            "SELECT * FROM vzorky WHERE degustace_id = ? ORDER BY cislo",
            (id,)
        ).fetchall()

    porotci_map = _nacti_porotce_map(conn, id)

    conn.close()

    vs = _view_state()
    sort_key = vs["sort"]
    sort_dir = vs["dir"]
    q_raw = vs["q"]

    edit_mode = session.get(SESSION_EDIT_PREFIX + str(id), False)
    rezim = session.get(SESSION_REZIM_PREFIX + str(id), "seznam")
    if rezim not in ("seznam", "komise", "nastaveni", "katalog"):
        rezim = "seznam"

    vzorky_o = list(vzorky)
    n_kom = pocet_komisi

    edit_row_id = None
    if rezim == "seznam" and edit_mode:
        key_row = SESSION_EDIT_ROW_PREFIX + str(id)
        raw_er = session.get(key_row)
        try:
            edit_row_id = int(raw_er) if raw_er is not None else None
        except (TypeError, ValueError):
            edit_row_id = None

    raw_k = session.get(SESSION_KOMISE_PREFIX + str(id), 1)
    if raw_k in (-1, "-1", "vse"):
        komise_sel = -1
    else:
        try:
            komise_sel = int(raw_k)
        except (TypeError, ValueError):
            komise_sel = 1
        komise_sel = max(1, min(n_kom, komise_sel))

    vzorky_sorted = []
    poradi_map = {}
    vzorky_komise_tab = []

    if rezim == "seznam":
        if edit_mode:
            vzorky_f = list(vzorky)
            vzorky_sorted = _sort_vzorky(vzorky_f, sort_key, sort_dir)
            poradi_map = {}
        else:
            vzorky_f = _filter_vzorky(vzorky, q_raw)
            vzorky_sorted = _sort_vzorky(vzorky_f, sort_key, sort_dir)
            poradi_map = _poradi_podle_bodu(vzorky)
    elif rezim == "komise":
        if edit_mode:
            k_eff = 1 if komise_sel == -1 else komise_sel
            k_eff = max(1, min(n_kom, k_eff))
            vzorky_komise_tab = [v for v in vzorky_o if int(v["komise_cislo"] or 0) == k_eff]
        else:
            if komise_sel == -1:
                vzorky_komise_tab = vzorky_o
            else:
                vzorky_komise_tab = [v for v in vzorky_o if int(v["komise_cislo"] or 0) == komise_sel]

    flash_html = _html_flash_zprávy()
    katalog_warning_html = ""
    if rezim == "katalog" and not any(v["body"] is not None for v in vzorky_o):
        katalog_warning_html = (
            '<div style="max-width:1280px;margin:0 auto 10px;padding:0 20px;">'
            '<div style="padding:10px 14px;border-radius:6px;border:1px solid #8b1538;background:#fde8ec;color:#222;">'
            'V katalogu zatím není pořadí, protože u vzorků nejsou zadané body.'
            '</div></div>'
        )
    ma_vzorky = len(vzorky_o) > 0

    ph = _preserve_hidden(sort_key, sort_dir, q_raw)

    def th_sort(col, label):
        href = _sort_href(id, col, sort_key, sort_dir, q_raw)
        sym = _sort_symbol(col, sort_key, sort_dir)
        return (
            f'<th class="th-sort"><a href="{href}" class="th-sort-link">{escape(label)} {sym}</a></th>'
        )

    def th_plain(label):
        return f"<th>{escape(label)}</th>"

    datum_cz = format_datum_cz(degustace["datum"])
    if rezim == "seznam":
        title_rezim_suffix = "Seznam vzorků"
    elif rezim == "katalog":
        title_rezim_suffix = "Katalog"
    elif rezim == "nastaveni":
        title_rezim_suffix = "Nastavení"
    else:
        title_rezim_suffix = (
            "Komise · Vše" if komise_sel == -1 else f"Komise · č. {komise_sel}"
        )

    katalog_top_x = degustace["katalog_top_x"]
    try:
        katalog_top_x = int(katalog_top_x) if katalog_top_x is not None else 15
    except (TypeError, ValueError):
        katalog_top_x = 15
    katalog_top_x = max(1, min(200, katalog_top_x))
    katalog_format = (degustace["katalog_format"] or "A4").strip().upper()
    if katalog_format not in ("A4", "A5"):
        katalog_format = "A4"
    katalog_font_pt = degustace["katalog_font_pt"]
    try:
        katalog_font_pt = int(katalog_font_pt) if katalog_font_pt is not None else 8
    except (TypeError, ValueError):
        katalog_font_pt = 8
    katalog_font_pt = max(6, min(10, katalog_font_pt))

    tisk_html = ""
    komise_select_html = ""
    if rezim == "komise":
        rozdeleni_tisk = _komise_prirazeni_existuje(vzorky_o)
        if rozdeleni_tisk:
            tisk_html = f"""
            <div class="tisk-panel-wrap">
                <button type="button" class="btn btn-primary btn-sm" id="btn-tisk-toggle">Tisk pro komise</button>
                <div id="tisk-panel" class="tisk-panel">
                    <button type="button" class="tisk-panel-close" id="btn-tisk-close" aria-label="Zavřít">×</button>
                    <p style="margin:0 0 8px;font-size:13px;color:var(--text-muted);">Rozdělení vzorků do komisí už existuje.</p>
                    <div class="tisk-panel-actions">
                        <a class="btn btn-primary btn-sm" href="/tisk/{id}?mode=use" target="_blank">Použít existující rozdělení</a>
                        <a class="btn btn-sm" href="/tisk/{id}?mode=regen" target="_blank">Přegenerovat a tisknout</a>
                    </div>
                </div>
            </div>
            """
        else:
            tisk_html = f'<a class="btn btn-primary btn-sm" href="/tisk/{id}" target="_blank">Tisk pro komise</a>'
        k_for_select = 1 if (edit_mode and komise_sel == -1) else komise_sel
        if edit_mode:
            k_for_select = max(1, min(n_kom, k_for_select if k_for_select != -1 else 1))
        opt_parts = []
        if not edit_mode:
            sel_vse = " selected" if komise_sel == -1 else ""
            opt_parts.append(f'<option value="vse"{sel_vse}>Vše</option>')
        for i in range(1, n_kom + 1):
            if edit_mode:
                is_sel = k_for_select == i
            else:
                is_sel = komise_sel != -1 and komise_sel == i
            sel_i = " selected" if is_sel else ""
            opt_parts.append(f'<option value="{i}"{sel_i}>Komise č.{i}</option>')
        opts_joined = "".join(opt_parts)
        komise_select_html = f"""
            <form method="post" class="form-komise-inline">
                <input type="hidden" name="action" value="set_komise">
                {ph}
                <label class="filter-label" for="sel-komise">Komise</label>
                <select name="komise" id="sel-komise" class="select-komise" onchange="this.form.submit()">{opts_joined}</select>
            </form>
        """

    katalog_tisk_html = ""
    katalog_qr_html = ""
    if rezim == "katalog":
        katalog_tisk_html = f'<a class="btn btn-primary btn-sm" href="/katalog_tisk/{id}" target="_blank">Tisk katalogu</a>'
        katalog_mobile_url = request.url_root.rstrip("/") + f"/mobile-katalog/{id}"
        katalog_mobile_qr = f"https://api.qrserver.com/v1/create-qr-code/?size=72x72&data={quote(katalog_mobile_url, safe='')}"
        katalog_qr_html = (
            f'<div class="catalog-qr-wrap catalog-qr-header">'
            f'<img class="catalog-qr-img" src="{katalog_mobile_qr}" alt="QR odkaz na mobilní e-katalog" width="72" height="72">'
            f'<a class="catalog-qr-link" href="{katalog_mobile_url}" target="_blank">Otevřít e-katalog</a>'
            f'</div>'
        )

    seznam_tools_row_html = ""
    if rezim == "seznam":
        filter_row = ""
        if not edit_mode:
            zrusit_f = ""
            if q_raw:
                href_clear = _build_degustace_url(id, sort_key, sort_dir, "")
                zrusit_f = f'<a class="btn btn-ghost" href="{href_clear}">Zrušit filtr</a>'
            filter_row = f"""
                <form method="get" action="/degustace/{id}" class="filter-row filter-row-tools" role="search">
                    <input type="hidden" name="sort" value="{escape(sort_key)}">
                    <input type="hidden" name="dir" value="{escape(sort_dir)}">
                    <label for="filtr-q" class="filter-label">Hledat</label>
                    <input id="filtr-q" type="search" name="q" value="{escape(q_raw)}"
                        placeholder="Všechna slova musí pasovat…" autocomplete="off">
                    <button class="btn" type="submit">Použít filtr</button>
                    {zrusit_f}
                </form>
            """
        seznam_tools_row_html = f"""
            <div class="chrome-row-tools">
                <div class="chrome-row-tools-left">
                    <a class="link-back tools-back-link" href="/">← Zpět na degustace</a>
                </div>
                <div class="chrome-row-tools-center">{filter_row}</div>
                <div class="chrome-row-tools-right">
                    <div class="import-help-row import-help-row-chrome">
                        <form id="form-import" method="post" enctype="multipart/form-data" class="import-row">
                            <input type="hidden" name="action" value="import">
                            {ph}
                            <input type="file" name="soubor" id="input-import-file" class="visually-hidden"
                                accept=".csv,.txt,.tsv,text/csv,text/plain"
                                onchange="if(this.files.length)window.importSouborPotvrdit(this);">
                            <label for="input-import-file" class="btn">Import dat ze souboru</label>
                        </form>
                        <button type="button" class="btn-help" id="btn-help-toggle" title="Nápověda" aria-label="Nápověda">?</button>
                    </div>
                    <div id="help-panel" class="help-panel">
                        <p><strong>Filtrování</strong> (režim Zobrazení): slova oddělte mezerou. Řádek musí obsahovat
                        <em>všechna</em> slova kdykoli v řádku (AND).</p>
                        <p><strong>Import:</strong> tabulka z Excelu (tabulátory), CSV nebo středníky. <strong>Názvy hlaviček se ignorují</strong>,
                        rozhoduje jen pořadí sloupců: 1) č.v. (ignoruje se), 2) Jméno, 3) Adresa, 4) Odrůda, 5) Přívlastek,
                        6) Rok, 7) Body (volitelně). Číslo vzorku vždy přidělí aplikace. Stejná kombinace Jméno + Odrůda +
                        Přívlastek + Rok jako u již uloženého vzorku nebo dvakrát v souboru → řádek se přeskočí.</p>
                    </div>
                </div>
            </div>
            """

    katalog_tools_row_html = ""
    if rezim == "katalog":
        filter_row_k = ""
        if not edit_mode:
            zrusit_k = ""
            if q_raw:
                href_clear_k = _build_degustace_url(id, sort_key, sort_dir, "")
                zrusit_k = f'<a class="btn btn-ghost" href="{href_clear_k}">Zrušit filtr</a>'
            filter_row_k = f"""
                <form method="get" action="/degustace/{id}" class="filter-row filter-row-tools" role="search">
                    <input type="hidden" name="sort" value="{escape(sort_key)}">
                    <input type="hidden" name="dir" value="{escape(sort_dir)}">
                    <label for="filtr-q-katalog" class="filter-label">Hledat</label>
                    <input id="filtr-q-katalog" type="search" name="q" value="{escape(q_raw)}"
                        placeholder="Všechna slova musí pasovat…" autocomplete="off">
                    <button class="btn" type="submit">Použít filtr</button>
                    {zrusit_k}
                </form>
            """
        katalog_tools_row_html = f"""
            <div class="chrome-row-tools">
                <div class="chrome-row-tools-left">
                    <a class="link-back tools-back-link" href="/">← Zpět na degustace</a>
                </div>
                <div class="chrome-row-tools-center">{filter_row_k}</div>
                <div class="chrome-row-tools-right"></div>
            </div>
        """

    opt_seznam = " selected" if rezim == "seznam" else ""
    opt_komise = " selected" if rezim == "komise" else ""
    opt_katalog = " selected" if rezim == "katalog" else ""
    opt_nastaveni = " selected" if rezim == "nastaveni" else ""

    pk_edit = degustace["pocet_komisi"]
    if pk_edit is None:
        pk_edit = n_kom
    else:
        try:
            pk_edit = int(pk_edit)
        except (TypeError, ValueError):
            pk_edit = n_kom
    pk_edit = max(1, min(10, pk_edit))

    html = f"""<!DOCTYPE html>
    <html lang="cs">
    <head>
        <meta charset="utf-8">
        <title>{escape(degustace['nazev'])}</title>
        <style>
            :root {{
                --app-bg: #f4f5f7;
                --surface: #ffffff;
                --text: #1a1d21;
                --text-muted: #5c6370;
                --border: #e1e4e8;
                --border-strong: #c5cad3;
                --accent: #3d5c35;
                --accent-hover: #324a2c;
                --accent-soft: #e8efe6;
                --focus-ring: rgba(61, 92, 53, 0.35);
                --radius: 8px;
                --radius-sm: 6px;
                --shadow-sm: 0 1px 2px rgba(0, 0, 0, 0.05);
                --shadow-md: 0 4px 14px rgba(0, 0, 0, 0.07);
            }}
            body {{
                font-family: 'Segoe UI', system-ui, -apple-system, BlinkMacSystemFont, 'Roboto', 'Helvetica Neue', Arial, sans-serif;
                max-width: 1280px;
                margin: 12px auto;
                padding: 0 20px 40px;
                color: var(--text);
                background: var(--app-bg);
                font-size: 15px;
                line-height: 1.45;
                -webkit-font-smoothing: antialiased;
            }}
            html {{
                scroll-padding-top: var(--chrome-h, 160px);
            }}
            .fixed-chrome {{
                position: fixed;
                top: 0;
                left: 0;
                right: 0;
                z-index: 200;
                background: var(--app-bg);
                border-bottom: none;
                box-shadow: 0 2px 10px rgba(0, 0, 0, 0.06);
            }}
            .fixed-chrome-inner {{
                max-width: 1280px;
                margin: 0 auto;
                padding: 10px 20px 10px;
                box-sizing: border-box;
            }}
            .page-body {{
                padding-top: var(--chrome-h, 160px);
                margin-top: 0;
            }}
            .top-grid {{
                display: grid;
                grid-template-columns: minmax(0, 1fr) auto;
                gap: 12px 24px;
                align-items: start;
            }}
            .chrome-row1 {{
                display: grid;
                grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
                gap: 12px 16px;
                align-items: flex-start;
            }}
            .title-left {{ justify-self: start; min-width: 0; }}
            .title-center {{
                justify-self: center;
                align-self: flex-start;
                text-align: center;
                font-size: 1.22rem;
                font-weight: 700;
                color: var(--accent);
                letter-spacing: -0.02em;
                padding: 0 8px;
                line-height: 1.25;
            }}
            .title-right {{
                justify-self: end;
                align-self: flex-start;
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                gap: 8px;
            }}
            .title-right-top {{
                display: flex;
                flex-wrap: wrap;
                align-items: flex-start;
                justify-content: flex-end;
                gap: 10px;
                width: 100%;
            }}
            .title-right-top .form-rezim-select {{
                margin-left: auto;
            }}
            .title-right-row2 {{
                display: flex;
                justify-content: flex-end;
                align-items: flex-start;
                gap: 10px;
                width: 100%;
                margin-top: 4px;
            }}
            .catalog-qr-wrap {{
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 4px;
                min-width: 96px;
            }}
            .catalog-qr-header {{
                flex-direction: row;
                align-items: flex-start;
                gap: 8px;
                min-width: 0;
                flex-shrink: 0;
            }}
            .catalog-qr-header .catalog-qr-img {{
                width: 72px;
                height: 72px;
                flex-shrink: 0;
            }}
            .catalog-qr-header .catalog-qr-link {{
                align-self: center;
                max-width: 9rem;
                line-height: 1.25;
            }}
            .catalog-qr-img {{
                width: 96px;
                height: 96px;
                border-radius: 6px;
                border: 1px solid var(--border);
                background: #fff;
            }}
            .catalog-qr-link {{
                font-size: 11px;
                color: var(--text-muted);
                text-decoration: none;
            }}
            .catalog-qr-link:hover {{
                color: var(--accent);
                text-decoration: underline;
            }}
            .chrome-row-tools {{
                display: grid;
                grid-template-columns: 1fr auto 1fr;
                align-items: start;
                gap: 8px 16px;
                width: 100%;
                padding: 2px 0 8px;
                box-sizing: border-box;
            }}
            .chrome-row-tools-left {{
                min-width: 0;
                display: flex;
                align-items: center;
            }}
            .tools-back-link {{
                margin: 0;
                white-space: nowrap;
            }}
            .chrome-row-tools-center {{
                justify-self: center;
                grid-column: 2;
                min-width: 0;
            }}
            .chrome-row-tools-center .filter-row-tools {{
                justify-content: center;
                margin: 0;
            }}
            .chrome-row-tools-right {{
                justify-self: end;
                grid-column: 3;
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                gap: 6px;
                min-width: 0;
            }}
            .import-help-row-chrome {{
                justify-content: flex-end;
            }}
            .form-rezim-select {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                gap: 8px;
                margin: 0;
            }}
            .edit-switch-form {{
                display: inline-flex;
                align-items: center;
                gap: 8px;
                margin: 0;
            }}
            .switch-label {{
                font-size: 13px;
                color: var(--text-muted);
                white-space: nowrap;
            }}
            .switch-track {{
                position: relative;
                width: 44px;
                height: 26px;
                border-radius: 13px;
                border: 1px solid var(--border-strong);
                background: #d8dce2;
                padding: 3px;
                cursor: pointer;
                flex-shrink: 0;
                transition: background 0.15s ease, border-color 0.15s ease;
            }}
            .switch-track.is-on {{
                background: var(--accent);
                border-color: var(--accent);
            }}
            .switch-knob {{
                display: block;
                width: 18px;
                height: 18px;
                border-radius: 50%;
                background: #fff;
                box-shadow: 0 1px 3px rgba(0, 0, 0, 0.2);
                transition: transform 0.15s ease;
            }}
            .switch-track.is-on .switch-knob {{
                transform: translateX(18px);
            }}
            .flash-close {{
                position: absolute;
                top: 6px;
                right: 8px;
                border: none;
                background: transparent;
                font-size: 20px;
                line-height: 1;
                cursor: pointer;
                color: inherit;
                opacity: 0.55;
                padding: 2px 6px;
            }}
            .flash-close:hover {{ opacity: 1; }}
            .komise-panel-inline {{
                display: flex;
                flex-wrap: wrap;
                align-items: flex-start;
                justify-content: flex-end;
                gap: 8px;
            }}
            .tisk-panel-wrap {{
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                gap: 6px;
            }}
            .tisk-panel {{
                display: none;
                position: relative;
                max-width: 360px;
                padding: 10px 12px;
                background: var(--surface);
                border: 1px solid var(--border-strong);
                border-radius: var(--radius-sm);
                font-size: 13px;
                text-align: left;
                box-shadow: var(--shadow-md);
            }}
            .tisk-panel-close {{
                position: absolute;
                top: 6px;
                right: 8px;
                border: none;
                background: transparent;
                font-size: 20px;
                line-height: 1;
                cursor: pointer;
                color: var(--text-muted);
                padding: 2px 6px;
            }}
            .tisk-panel-close:hover {{
                color: var(--text);
            }}
            .tisk-panel.is-open {{ display: block; }}
            .tisk-panel-actions {{
                display: flex;
                flex-wrap: wrap;
                gap: 8px;
                align-items: center;
            }}
            .title-block h1.deg-nazev {{
                margin: 0 0 6px 0;
                font-size: 1.35rem;
                font-weight: 600;
                letter-spacing: -0.02em;
                display: flex;
                flex-wrap: wrap;
                align-items: baseline;
                gap: 4px 8px;
            }}
            .title-block .deg-title-name {{ flex: 0 1 auto; min-width: 0; }}
            .title-block .deg-title-sep {{ color: var(--text-muted); font-weight: 400; }}
            .title-block .deg-title-rezim {{ font-size: 1.05rem; font-weight: 600; color: var(--accent); }}
            .title-block .datum {{ color: var(--text-muted); font-size: 0.9rem; margin-bottom: 4px; }}
            .title-block .link-back {{
                font-size: 12px;
                color: var(--text-muted);
                text-decoration: none;
                display: inline-block;
                margin-top: 2px;
            }}
            .title-block .link-back:hover {{ color: var(--accent); text-decoration: underline; }}
            .controls-block {{ text-align: right; min-width: 280px; }}
            .controls-toggles {{
                display: flex;
                flex-wrap: wrap;
                justify-content: flex-end;
                align-items: center;
                gap: 10px;
                margin-bottom: 8px;
            }}
            .komise-panel-right {{
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                gap: 8px;
            }}
            .form-komise-inline {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: flex-end;
                gap: 8px;
                margin: 0;
            }}
            .select-komise {{
                padding: 6px 10px;
                border-radius: var(--radius-sm);
                border: 1px solid var(--border-strong);
                font-size: 13px;
                font-family: inherit;
                background: var(--surface);
                min-width: 140px;
            }}
            .controls-row {{
                display: flex;
                flex-wrap: wrap;
                justify-content: flex-end;
                align-items: center;
                gap: 8px;
                margin-bottom: 6px;
            }}
            .controls-sub {{
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                gap: 6px;
            }}
            .import-help-row {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: flex-end;
                gap: 10px;
            }}
            .import-row {{ display: inline; }}
            .visually-hidden {{
                position: absolute;
                width: 1px;
                height: 1px;
                padding: 0;
                margin: -1px;
                overflow: hidden;
                clip: rect(0, 0, 0, 0);
                white-space: nowrap;
                border: 0;
            }}
            .btn-help {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                width: 38px;
                height: 38px;
                border-radius: 50%;
                border: 1px solid #bbb;
                background: #fff;
                cursor: pointer;
                font-weight: bold;
                font-size: 16px;
                color: #444;
                line-height: 1;
                padding: 0;
                margin: 0;
                flex-shrink: 0;
                vertical-align: middle;
                box-sizing: border-box;
            }}
            .btn-help:hover {{ background: #f5f5f5; }}
            .help-panel {{
                display: none;
                max-width: 420px;
                padding: 12px 14px;
                background: #fff;
                border: 1px solid #ccc;
                border-radius: 8px;
                font-size: 13px;
                text-align: left;
                line-height: 1.45;
                color: #333;
                box-shadow: 0 4px 14px rgba(0, 0, 0, 0.07);
            }}
            .help-panel.is-open {{ display: block; }}
            .help-panel p {{ margin: 0 0 8px 0; }}
            .help-panel p:last-child {{ margin-bottom: 0; }}
            .filter-row {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: flex-end;
                gap: 8px;
            }}
            .filter-row input[type="search"] {{
                min-width: 225px;
                max-width: 450px;
                padding: 8px 10px;
                border: 1px solid #ccc;
                border-radius: 6px;
                width: auto;
            }}
            .filter-label {{
                font-size: 13px;
                color: #444;
                white-space: nowrap;
            }}
            .btn {{
                display: inline-flex;
                align-items: center;
                justify-content: center;
                padding: 9px 16px;
                text-decoration: none;
                border: 1px solid var(--border-strong);
                border-radius: var(--radius);
                color: var(--text);
                background: var(--surface);
                cursor: pointer;
                font-size: 14px;
                font-weight: 500;
                line-height: 1.2;
                transition: background 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
                box-shadow: var(--shadow-sm);
            }}
            .btn:hover {{
                background: #f8f9fa;
                border-color: #b0b6c0;
            }}
            .btn:focus-visible {{
                outline: none;
                box-shadow: 0 0 0 3px var(--focus-ring);
            }}
            .btn-primary {{
                background: var(--accent);
                color: #fff;
                border-color: var(--accent);
            }}
            .btn-primary:hover {{
                background: var(--accent-hover);
                border-color: var(--accent-hover);
            }}
            .btn-ghost {{
                background: var(--surface);
                border-color: var(--border-strong);
                box-shadow: none;
            }}
            .btn-ghost:hover {{ background: #f0f2f4; }}
            .btn-sm {{
                padding: 7px 12px;
                font-size: 13px;
                border-radius: var(--radius-sm);
                font-weight: 500;
            }}
            label.btn {{
                margin: 0;
                cursor: pointer;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-height: 38px;
                box-sizing: border-box;
                line-height: 1.2;
            }}
            .mode-wrap {{
                display: inline-flex;
                border: 1px solid var(--border-strong);
                border-radius: var(--radius);
                overflow: hidden;
                background: var(--surface);
                box-shadow: var(--shadow-sm);
            }}
            .mode-wrap form {{ margin: 0; display: inline; }}
            .mode-wrap button {{
                border: none;
                padding: 10px 16px;
                font-size: 14px;
                font-weight: 500;
                cursor: pointer;
                background: transparent;
                color: var(--text-muted);
                transition: background 0.15s ease, color 0.15s ease;
            }}
            .mode-wrap button:hover {{ background: #f0f2f4; color: var(--text); }}
            .mode-wrap button.active {{
                background: var(--accent-soft);
                color: var(--accent);
                font-weight: 600;
            }}
            /* overflow na obalu tabulky dělá z prvku scroll kontejner → sticky se vztahuje k němu,
               ne k oknu; v Chrome/Edge to přehází vykreslení řádků nad záhlaví */
            .table-panel {{
                margin-top: 0;
                background: var(--surface);
                border: none;
                border-radius: var(--radius);
                overflow: visible;
                box-shadow: var(--shadow-md);
            }}
            table.data-grid {{
                width: 100%;
                border-collapse: separate;
                border-spacing: 0;
                background: var(--surface);
                border: none;
            }}
            table.data-grid th,
            table.data-grid td {{
                border-right: 1px solid var(--border);
                border-bottom: 1px solid var(--border);
                padding: 8px 10px;
                text-align: left;
                vertical-align: middle;
            }}
            table.data-grid.table-komise {{
                table-layout: fixed;
                width: 100%;
            }}
            table.data-grid.table-komise col.col-kom {{ width: 1.85rem; }}
            table.data-grid.table-komise col.col-cv {{ width: 1.85rem; }}
            table.data-grid.table-komise col.col-odr {{ width: 6%; }}
            table.data-grid.table-komise col.col-jak {{ width: 5%; }}
            table.data-grid.table-komise col.col-roc {{ width: 3.5%; }}
            table.data-grid.table-komise col.col-sc {{ width: 4.5%; }}
            table.data-grid.table-komise col.col-sum {{ width: 3.2%; }}
            table.data-grid.table-komise col.col-pozn {{ width: 52%; }}
            table.data-grid.table-komise th,
            table.data-grid.table-komise td {{
                padding: 8px 7px;
                font-size: 14px;
                vertical-align: middle;
            }}
            table.data-grid.table-komise thead th {{
                text-align: center;
                line-height: 1.3;
                font-size: 11px;
                text-transform: none;
            }}
            table.data-grid.table-komise .td-kom,
            table.data-grid.table-komise .col-kom-h {{
                width: 1.85rem;
                max-width: 1.85rem;
                text-align: center;
                font-weight: 600;
                color: var(--text-muted);
                font-size: 12px;
            }}
            table.data-grid.table-komise .td-cv {{ text-align: center; font-weight: 600; width: 1.85rem; max-width: 1.85rem; }}
            table.data-grid.table-komise .td-clip {{
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
                max-width: 0;
            }}
            table.data-grid.table-komise .td-celkem {{ text-align: center; font-weight: 600; white-space: nowrap; font-size: 13px; }}
            table.data-grid.table-komise .pozn-input-wrap {{
                flex: 1 1 0;
                min-width: 0;
                display: flex;
                align-items: center;
            }}
            table.data-grid.table-komise input.pozn-input {{
                width: 100%;
                min-width: 0;
                box-sizing: border-box;
                padding: 4px 8px;
                font-size: 14px;
                font-family: inherit;
                border: 1px solid var(--border-strong);
                border-radius: var(--radius-sm);
                margin: 0;
                line-height: 1.35;
                height: 2.1rem;
            }}
            table.data-grid.table-komise input.in-score {{
                width: 100%;
                max-width: 2.35rem;
                box-sizing: border-box;
                padding: 4px 4px;
                font-size: 14px;
                text-align: center;
                margin: 0 auto;
                display: block;
                border: 1px solid var(--border-strong);
                border-radius: var(--radius-sm);
                font-family: inherit;
                height: 2.1rem;
                line-height: 1.35;
            }}
            table.data-grid.table-komise .komise-form-row .btn {{
                padding: 6px 12px;
                font-size: 13px;
                flex: 0 0 auto;
                align-self: center;
            }}
            table.data-grid.table-komise .td-pozn {{
                vertical-align: middle;
                min-width: 0;
                display: flex;
                flex-direction: row;
                align-items: center;
                justify-content: flex-start;
                gap: 8px;
            }}
            table.data-grid.table-komise tbody.komise-tbody-edit tr.komise-form-row td {{
                padding-top: 4px;
                padding-bottom: 4px;
            }}
            table.data-grid.table-komise .td-pozn-read {{
                font-size: 14px;
                line-height: 1.4;
                vertical-align: top;
                white-space: normal;
                word-break: break-word;
            }}
            table.data-grid thead th:last-child,
            table.data-grid tbody td:last-child {{
                border-right: none;
            }}
            table.data-grid tbody tr:last-child td {{
                border-bottom: none;
            }}
            table.data-grid tbody tr:nth-child(even) td {{
                background: #fafbfc;
            }}
            table.data-grid thead th {{
                position: sticky;
                top: var(--chrome-h, 160px);
                z-index: 60;
                background: linear-gradient(180deg, #f0f2f5 0%, #e8eaee 100%);
                border-bottom: 1px solid var(--border);
                box-shadow: none;
                font-size: 13px;
                font-weight: 600;
                color: #3d4248;
                letter-spacing: 0.01em;
            }}
            table.data-grid.table-katalog thead th {{
                position: static;
                top: auto;
                z-index: auto;
            }}
            .btn-danger {{
                color: #9b1c1c;
                border-color: #d4a0a0;
                background: #fff8f8;
            }}
            .btn-danger:hover {{
                background: #ffecec;
                border-color: #c45c5c;
                color: #7f1010;
            }}
            table.data-grid .form-smaz {{ margin: 0; display: inline; }}
            table.data-grid .td-akce {{ white-space: nowrap; width: 5rem; }}
            table.data-grid tbody tr.row-novy-vzorek td {{
                background: #f3f7f2;
            }}
            table.data-grid tbody tr.row-novy-vzorek td.cell-novy {{
                background: var(--accent-soft) !important;
            }}
            table.data-grid td.cell-novy {{
                text-align: center;
                vertical-align: middle;
                background: var(--accent-soft) !important;
                color: var(--accent);
                font-weight: 700;
                font-size: 1.1rem;
                width: 3rem;
            }}
            table.data-grid .cell-form-body {{
                display: flex;
                align-items: center;
                gap: 10px;
                flex-wrap: wrap;
                width: 100%;
                margin: 0;
            }}
            .th-sort-link {{
                color: #2c5282;
                text-decoration: none;
                white-space: nowrap;
            }}
            .th-sort-link:hover {{ text-decoration: underline; color: #1a365d; }}
            .sort-muted {{ color: #9ca3af; font-size: 0.85em; }}
            .sort-active {{ color: var(--accent); font-weight: 700; }}
            table.data-grid td input[type="text"],
            table.data-grid td input:not([type]) {{
                width: 100%;
                box-sizing: border-box;
                padding: 9px 10px;
                margin: 0;
                display: block;
                border: 1px solid var(--border-strong);
                border-radius: var(--radius-sm);
                font-size: 14px;
                font-family: inherit;
                color: var(--text);
                background: var(--surface);
                transition: border-color 0.15s ease, box-shadow 0.15s ease;
            }}
            table.data-grid td input:hover {{
                border-color: #b8bec8;
            }}
            table.data-grid td input:focus {{
                outline: none;
                border-color: var(--accent);
                box-shadow: 0 0 0 3px var(--focus-ring);
            }}
            table.data-grid td input.body-input {{
                width: 5.5rem;
                flex: 0 0 auto;
                display: inline-block;
                text-align: center;
            }}
            /* obecné velké paddingy výše přepisují komisi — sjednotit výšku řádku s dílčími body */
            table.data-grid.table-komise td input.in-score {{
                padding: 4px 4px;
                height: 2.1rem;
                line-height: 1.35;
                font-size: 14px;
            }}
            table.data-grid.table-komise td input.pozn-input {{
                padding: 4px 8px;
                height: 2.1rem;
                line-height: 1.35;
                font-size: 14px;
            }}
            button.btn {{
                font-family: inherit;
            }}
            table.data-grid .cell-form-body .btn {{
                margin: 0;
                flex: 0 0 auto;
                box-shadow: none;
            }}
            table.data-grid .cell-form-body .btn:hover {{
                box-shadow: var(--shadow-sm);
            }}
            .poradi {{
                font-weight: bold;
                white-space: nowrap;
            }}
            .settings-panel {{
                padding: 12px 18px 18px;
                color: var(--text);
            }}
            .settings-panel h2 {{
                margin: 0 0 10px 0;
                font-size: 1rem;
                font-weight: 600;
                color: var(--text);
            }}
            .settings-block {{
                margin-bottom: 20px;
            }}
            .settings-row {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                gap: 10px;
                margin-bottom: 10px;
            }}
        </style>
    </head>
    <body data-ma-vzorky="{'1' if ma_vzorky else '0'}">
        <div class="fixed-chrome" id="fixed-chrome">
            <div class="fixed-chrome-inner">
                {flash_html}
                {katalog_warning_html}
                <div class="chrome-row1">
                    <div class="title-left title-block">
                        <h1 class="deg-nazev"><span class="deg-title-name">{escape(degustace['nazev'])}</span></h1>
                        <div class="datum">{escape(datum_cz)}</div>
                    </div>
                    <div class="title-center">{escape(title_rezim_suffix)}</div>
                    <div class="title-right">
                        <div class="title-right-top">
                            {katalog_qr_html if rezim == 'katalog' else ''}
                            {'' if rezim == 'katalog' else f'''
                            <form method="post" class="edit-switch-form">
                                <input type="hidden" name="action" value="set_edit">
                                <input type="hidden" name="edit" value="{'0' if edit_mode else '1'}">
                                {ph}
                                <span class="switch-label">{'Úpravy' if edit_mode else 'Prohlížení'}</span>
                                <button type="submit" class="switch-track{' is-on' if edit_mode else ''}" title="Přepnout režim úprav" aria-label="Přepnout režim úprav">
                                    <span class="switch-knob"></span>
                                </button>
                            </form>
                            '''}
                            <form method="post" class="form-rezim-select">
                                <input type="hidden" name="action" value="set_rezim">
                                {ph}
                                <label class="filter-label" for="sel-rezim">Sekce</label>
                                <select name="rezim" id="sel-rezim" class="select-komise" onchange="this.form.submit()">
                                    <option value="seznam"{opt_seznam}>Seznam vzorků</option>
                                    <option value="komise"{opt_komise}>Komise</option>
                                    <option value="katalog"{opt_katalog}>Katalog</option>
                                    <option value="nastaveni"{opt_nastaveni}>Nastavení</option>
                                </select>
                            </form>
                            {('<div class="komise-panel-inline">' + tisk_html + komise_select_html + '</div>') if rezim == 'komise' else ''}
                        </div>
                        {('<div class="title-right-row2">' + katalog_tisk_html + '</div>') if rezim == 'katalog' else ''}
                    </div>
                </div>
                {seznam_tools_row_html}
                {katalog_tools_row_html}
            </div>
        </div>

        <div class="page-body">
    """

    if edit_mode and rezim == "seznam":
        html += f"""
            <form id="form-pridej" method="post" hidden>
                <input type="hidden" name="action" value="pridej">
                {ph}
            </form>
        """

    html += """        <div class="table-panel">
    """

    if rezim == "nastaveni":
        html += '<div class="settings-panel">'
        html += '<div class="settings-block"><h2>Počet komisí</h2>'
        if edit_mode:
            html += f"""
            <form method="post" class="settings-row">
                <input type="hidden" name="action" value="set_pocet_komisi">
                {ph}
                <label class="filter-label" for="inp-pocet-komisi">Počet</label>
                <input id="inp-pocet-komisi" type="number" name="pocet_komisi" min="1" max="10" value="{pk_edit}"
                    style="width:5rem;padding:8px 10px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;">
                <button class="btn btn-sm btn-primary" type="submit">Uložit</button>
            </form>
            """
        else:
            html += f'<p style="margin:0;">Aktuálně <strong>{pk_edit}</strong> komisí.</p>'
        html += "</div>"
        html += '<div class="settings-block"><h2>Nastavení katalogu</h2>'
        if edit_mode:
            sel_a4 = " selected" if katalog_format == "A4" else ""
            sel_a5 = " selected" if katalog_format == "A5" else ""
            html += f"""
            <form method="post" class="settings-row">
                <input type="hidden" name="action" value="set_katalog_nastaveni">
                {ph}
                <label class="filter-label" for="inp-katalog-top">TOP počet</label>
                <input id="inp-katalog-top" type="number" name="katalog_top_x" min="1" max="200" value="{katalog_top_x}"
                    style="width:6rem;padding:8px 10px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;">
                <label class="filter-label" for="sel-katalog-format">Formát tisku</label>
                <select id="sel-katalog-format" name="katalog_format" class="select-komise">
                    <option value="A4"{sel_a4}>A4</option>
                    <option value="A5"{sel_a5}>A5</option>
                </select>
                <label class="filter-label" for="inp-katalog-font">Velikost písma (tisk)</label>
                <input id="inp-katalog-font" type="number" name="katalog_font_pt" min="6" max="10" value="{katalog_font_pt}"
                    style="width:5rem;padding:8px 10px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;">
                <button class="btn btn-sm btn-primary" type="submit">Uložit</button>
            </form>
            """
        else:
            html += f'<p style="margin:0;">TOP počet: <strong>{katalog_top_x}</strong>, formát tisku: <strong>{katalog_format}</strong>, velikost písma: <strong>{katalog_font_pt} pt</strong>.</p>'
        html += "</div>"
        h_lb, h_mx = _hodnoceni_labels_maxes_from_deg(degustace)
        h_tok = (degustace["hodnoceni_token"] or "").strip() if degustace["hodnoceni_token"] else ""
        base_h = request.url_root.rstrip("/")
        html += '<div class="settings-block"><h2>Mobilní hodnocení (komise)</h2>'
        html += (
            '<p style="margin:0 0 12px;font-size:13px;color:var(--text-muted);">'
            "Pořadí kritérií je vždy: barva → čistota → vůně → chuť (sloupce v databázi). "
            "Jeden tajný token na degustaci; v URL se volí číslo komise. "
            "Po prvním uložení kritérií se token vygeneruje automaticky.</p>"
        )
        if edit_mode:
            html += f"""
            <form method="post" style="margin-bottom:10px;">
                <input type="hidden" name="action" value="hodnoceni_nastaveni">
                {ph}
                <div style="display:grid;grid-template-columns:repeat(2,minmax(200px,1fr));gap:12px;width:100%;max-width:900px;">
            """
            map_hint = ("barva", "čistota", "vůně", "chuť")
            for i in range(4):
                bi = i + 1
                html += f"""
                    <div style="border:1px solid var(--border);border-radius:8px;padding:10px;background:#fafbfc;">
                        <div style="font-size:12px;font-weight:600;margin-bottom:6px;">Kritérium {bi} ({map_hint[i]})</div>
                        <label style="font-size:12px;">Popisek</label>
                        <input name="hodn_b{bi}_label" value="{escape(h_lb[i])}"
                            style="width:100%;box-sizing:border-box;padding:6px 8px;margin:4px 0 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;">
                        <label style="font-size:12px;">Maximum bodů (1–100)</label>
                        <input type="number" name="hodn_b{bi}_max" min="1" max="100" value="{h_mx[i]}"
                            style="width:6rem;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;">
                    </div>
                """
            html += f"""
                </div>
                <button class="btn btn-sm btn-primary" type="submit" style="margin-top:12px;">Uložit kritéria</button>
            </form>
            <form method="post" style="margin-bottom:8px;">
                <input type="hidden" name="action" value="hodnoceni_token_obnovit">
                {ph}
                <button class="btn btn-sm" type="submit" onclick="return confirm('Obnovit tajný odkaz? Staré QR přestanou platit.');">Obnovit tajný odkaz</button>
            </form>
            """
        else:
            html += (
                f'<p style="margin:0 0 8px;font-size:13px;">'
                f"{escape(h_lb[0])} (max {h_mx[0]}), {escape(h_lb[1])} ({h_mx[1]}), "
                f"{escape(h_lb[2])} ({h_mx[2]}), {escape(h_lb[3])} ({h_mx[3]})"
                f"</p>"
            )
        if h_tok:
            html += '<div style="margin-top:10px;font-weight:600;font-size:14px;">Odkazy a QR pro komise</div>'
            html += (
                '<table class="data-grid" style="margin-top:8px;font-size:13px;max-width:100%;">'
                "<thead><tr><th>Kom.</th><th>URL</th><th>QR</th></tr></thead><tbody>"
            )
            for k in range(1, n_kom + 1):
                u = f"{base_h}/hodnoceni/{id}/{k}?t={quote(h_tok, safe='')}"
                qr = f"https://api.qrserver.com/v1/create-qr-code/?size=96x96&data={quote(u, safe='')}"
                html += f"""
                <tr>
                    <td style="white-space:nowrap;">č. {k}</td>
                    <td style="word-break:break-all;"><a href="{escape(u)}" target="_blank" rel="noopener">{escape(u)}</a></td>
                    <td style="text-align:center;"><img src="{qr}" width="96" height="96" alt="QR komise {k}"></td>
                </tr>
                """
            html += "</tbody></table>"
        else:
            html += (
                '<p style="margin:10px 0 0;font-size:13px;color:var(--text-muted);">'
                "Token pro mobilní hodnocení zatím není — v režimu úprav uložte kritéria výše (token se vytvoří automaticky)."
                "</p>"
            )
        html += "</div>"
        html += '<div class="settings-block"><h2>Porotci / komisaři</h2>'
        html += '<p style="margin:0 0 12px;font-size:13px;color:var(--text-muted);">Jedno pole na komisi; jména oddělte čárkami.</p>'
        for k in range(1, n_kom + 1):
            cur_jm = porotci_map.get(k) or ""
            if edit_mode:
                html += f"""
                <form method="post" class="settings-row" style="align-items:flex-start;">
                    <input type="hidden" name="action" value="porotci_uloz">
                    <input type="hidden" name="komise_cislo" value="{k}">
                    {ph}
                    <label class="filter-label" for="inp-por-set-{k}" style="padding-top:8px;">Komise č.{k}</label>
                    <input id="inp-por-set-{k}" type="text" name="jmena" value="{escape(cur_jm)}"
                        placeholder="Např. Novák, Svobodová, …" autocomplete="off"
                        style="flex:1;min-width:220px;max-width:100%;padding:8px 10px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;">
                    <button class="btn btn-sm" type="submit">Uložit</button>
                </form>
                """
            else:
                html += f'<p style="margin:8px 0 12px;"><strong>Komise č.{k}:</strong> {escape(cur_jm) if cur_jm else "—"}</p>'
        html += "</div>"
        html += '<div class="settings-block"><h2>Výmaz dat vzorků</h2>'
        if edit_mode:
            html += f"""
            <form method="post" class="settings-row"
                onsubmit="return window.confirm('Opravdu smazat všechny vzorky této degustace?\\n\\nTato akce se nedá vrátit.');">
                <input type="hidden" name="action" value="smaz_vse_vzorky">
                {ph}
                <button class="btn btn-sm btn-danger" type="submit">Smazat všechny vzorky</button>
            </form>
            """
        else:
            html += '<p style="margin:0;font-size:13px;color:var(--text-muted);">Výmaz je dostupný pouze v režimu Úpravy.</p>'
        html += "</div></div>"
    elif rezim == "katalog":
        vzorky_k = _filter_vzorky(vzorky_o, q_raw)

        rank_all = [v for v in vzorky_o if v["body"] is not None]
        rank_all.sort(key=lambda v: (-float(v["body"]), v["cislo"]))
        poradi_katalog = {v["id"]: i + 1 for i, v in enumerate(rank_all)}

        top_scored = [v for v in vzorky_k if v["body"] is not None]
        top_scored.sort(key=lambda v: (-float(v["body"]), v["cislo"]))
        top_scored = top_scored[:katalog_top_x]

        by_odruda = {}
        for v in vzorky_k:
            odr = (v["odruda"] or "Nezařazeno").strip() or "Nezařazeno"
            by_odruda.setdefault(odr, []).append(v)
        odrudy_sorted = sorted(by_odruda.keys(), key=lambda x: x.casefold())
        for odr in odrudy_sorted:
            by_odruda[odr].sort(key=lambda v: ((v["nazev"] or "").casefold(), v["cislo"]))

        html += f"""
            <div style="padding:14px 16px 8px;">
                <h2 style="margin:0 0 10px;font-size:1.08rem;color:#223;">TOP {katalog_top_x} vzorků podle pořadí</h2>
                <div style="overflow:auto;">
                    <table class="data-grid table-katalog" style="margin-bottom:14px;">
                        <thead><tr>
                            <th>Pořadí</th><th>Číslo</th><th>Vystavovatel</th><th>Odrůda</th><th>Přívlastek</th><th>Rok</th><th>Body</th>
                        </tr></thead>
                        <tbody>
        """
        if top_scored:
            for v in top_scored:
                por = poradi_katalog.get(v["id"])
                por_txt = f"{por}." if por else "—"
                html += f"""
                        <tr>
                            <td class="poradi">{por_txt}</td>
                            <td>{v["cislo"]}</td>
                            <td>{escape(v["nazev"] or "")}</td>
                            <td>{escape(v["odruda"] or "")}</td>
                            <td>{escape(v["privlastek"] or "")}</td>
                            <td>{escape(v["rocnik"] or "")}</td>
                            <td>{format_body_hodnota(v["body"]) or "—"}</td>
                        </tr>
                """
        else:
            html += '<tr><td colspan="7" style="text-align:center;color:#666;">Zatím nejsou zadané body.</td></tr>'
        html += """
                        </tbody>
                    </table>
                </div>
            </div>
        """
        html += '<div style="padding:6px 16px 16px;"><h2 style="margin:0 0 10px;font-size:1.08rem;color:#223;">Katalog podle odrůd</h2>'
        for odr in odrudy_sorted:
            html += f"""
            <div style="margin:10px 0 14px;padding:10px 12px;border:1px solid var(--border);border-radius:8px;background:#fff;">
                <h3 style="margin:0 0 8px;font-size:1rem;color:#2a3f2a;">{escape(odr)}</h3>
                <div style="overflow:auto;">
                    <table class="data-grid table-katalog">
                        <thead><tr>
                            <th>Pořadí</th><th>Číslo</th><th>Vystavovatel</th><th>Adresa</th><th>Přívlastek</th><th>Rok</th><th>Body</th>
                        </tr></thead>
                        <tbody>
            """
            for v in by_odruda[odr]:
                por = poradi_katalog.get(v["id"])
                por_txt = f"{por}." if por else "—"
                html += f"""
                        <tr>
                            <td class="poradi">{por_txt}</td>
                            <td>{v["cislo"]}</td>
                            <td>{escape(v["nazev"] or "")}</td>
                            <td>{escape(v["adresa"] or "")}</td>
                            <td>{escape(v["privlastek"] or "")}</td>
                            <td>{escape(v["rocnik"] or "")}</td>
                            <td>{format_body_hodnota(v["body"]) or "—"}</td>
                        </tr>
                """
            html += """
                        </tbody>
                    </table>
                </div>
            </div>
            """
        html += "</div>"
    elif rezim == "komise":

        def _fmt_komise_dilci(x):
            if x is None:
                return "—"
            return format_body_hodnota(x)

        hk_lb, hk_mx = _hodnoceni_labels_maxes_from_deg(degustace)
        th_b1 = f"{escape(hk_lb[0])}<br>0–{hk_mx[0]}"
        th_b2 = f"{escape(hk_lb[1])}<br>0–{hk_mx[1]}"
        th_b3 = f"{escape(hk_lb[2])}<br>0–{hk_mx[2]}"
        th_b4 = f"{escape(hk_lb[3])}<br>0–{hk_mx[3]}"

        html += f"""
            <table class="data-grid table-komise">
                <colgroup>
                    <col class="col-kom" />
                    <col class="col-cv" />
                    <col class="col-odr" />
                    <col class="col-jak" />
                    <col class="col-rok" />
                    <col class="col-sc" />
                    <col class="col-sc" />
                    <col class="col-sc" />
                    <col class="col-sc" />
                    <col class="col-sum" />
                    <col class="col-pozn" />
                </colgroup>
                <thead>
                <tr>
                    <th class="col-kom-h">Kom.</th>
                    <th>č.v.</th>
                    <th>odrůda</th>
                    <th>jakost</th>
                    <th>ročník</th>
                    <th>{th_b1}</th>
                    <th>{th_b2}</th>
                    <th>{th_b3}</th>
                    <th>{th_b4}</th>
                    <th>celkem</th>
                    <th>poznámka</th>
                </tr>
                </thead>
        """
        html += f"""
                <tbody class="{'komise-tbody-edit' if edit_mode else ''}">
        """
        komise_forms_html = ""
        for v in vzorky_komise_tab:
            vid = v["id"]
            k_num = int(v["komise_cislo"] or 0) or 1
            celkem_txt = _komise_celkem_zobrazit(v) or "—"
            poz_txt = v["poznamka"] or ""
            if edit_mode:
                pv_ba = format_body_hodnota(v["body_barva"]) if v["body_barva"] is not None else ""
                pv_bc = format_body_hodnota(v["body_cistota"]) if v["body_cistota"] is not None else ""
                pv_bv = format_body_hodnota(v["body_vune"]) if v["body_vune"] is not None else ""
                pv_bch = format_body_hodnota(v["body_chut"]) if v["body_chut"] is not None else ""

                komise_forms_html += f"""
                <form id="ksave-{vid}" method="post" class="visually-hidden" aria-hidden="true">
                    <input type="hidden" name="action" value="komise_uloz">
                    {ph}
                    <input type="hidden" name="vzorek_id" value="{vid}">
                </form>
                """
                html += f"""
                <tr class="komise-form-row">
                    <td class="td-kom">{k_num}</td>
                    <td class="td-cv">{v["cislo"]}</td>
                    <td class="td-clip">{escape(v["odruda"] or "")}</td>
                    <td class="td-clip">{escape(v["privlastek"] or "")}</td>
                    <td class="td-clip">{escape(v["rocnik"] or "")}</td>
                    <td><input class="in-score" type="text" inputmode="decimal" name="body_barva" form="ksave-{vid}" id="barva-{vid}" value="{pv_ba}" autocomplete="off"></td>
                    <td><input class="in-score" type="text" inputmode="decimal" name="body_cistota" form="ksave-{vid}" value="{pv_bc}" autocomplete="off"></td>
                    <td><input class="in-score" type="text" inputmode="decimal" name="body_vune" form="ksave-{vid}" value="{pv_bv}" autocomplete="off"></td>
                    <td><input class="in-score" type="text" inputmode="decimal" name="body_chut" form="ksave-{vid}" value="{pv_bch}" autocomplete="off"></td>
                    <td class="td-celkem" id="kom-celkem-{vid}">{celkem_txt}</td>
                    <td class="td-pozn">
                        <div class="pozn-input-wrap">
                            <input type="text" class="pozn-input" name="poznamka" form="ksave-{vid}" value="{escape(poz_txt)}" autocomplete="off">
                        </div>
                        <button class="btn btn-sm" type="submit" form="ksave-{vid}">Uložit</button>
                    </td>
                </tr>
                """
            else:
                html += f"""
                <tr>
                    <td class="td-kom">{k_num}</td>
                    <td class="td-cv">{v["cislo"]}</td>
                    <td class="td-clip">{escape(v["odruda"] or "")}</td>
                    <td class="td-clip">{escape(v["privlastek"] or "")}</td>
                    <td class="td-clip">{escape(v["rocnik"] or "")}</td>
                    <td>{_fmt_komise_dilci(v["body_barva"])}</td>
                    <td>{_fmt_komise_dilci(v["body_cistota"])}</td>
                    <td>{_fmt_komise_dilci(v["body_vune"])}</td>
                    <td>{_fmt_komise_dilci(v["body_chut"])}</td>
                    <td class="td-celkem">{celkem_txt}</td>
                    <td class="td-pozn-read">{escape(poz_txt) if poz_txt else "—"}</td>
                </tr>
                """
        html += """
                </tbody>
            </table>
        """
        if edit_mode:
            html += komise_forms_html

        # Porotci
        if edit_mode:
            k_eff = 1 if komise_sel == -1 else komise_sel
            k_eff = max(1, min(n_kom, k_eff))
            cur_jmena = porotci_map.get(k_eff) or ""
            html += f"""
            <div style="padding:10px 12px;">
                <form method="post" class="filter-row" style="justify-content:flex-start;gap:10px;">
                    <input type="hidden" name="action" value="porotci_uloz">
                    <input type="hidden" name="komise_cislo" value="{k_eff}">
                    {ph}
                    <label class="filter-label" for="inp-porotci">Porotci komise č.{k_eff}</label>
                    <input id="inp-porotci" type="text" name="jmena" value="{escape(cur_jmena)}"
                        placeholder="Např. Novák, Svobodová, …" autocomplete="off" style="min-width:320px;max-width:680px;">
                    <button class="btn btn-sm" type="submit">Uložit porotce</button>
                </form>
            </div>
            """
        else:
            if komise_sel == -1:
                lines = []
                for k in range(1, n_kom + 1):
                    jm = porotci_map.get(k) or ""
                    lines.append(
                        f"<p style=\"margin:6px 0;\"><strong>Porotci komise č.{k}:</strong> {escape(jm) if jm else '—'}</p>"
                    )
                html += (
                    "<div style=\"padding:10px 12px;color:#333;\">"
                    + "<div style=\"font-size:13px;color:#555;margin-bottom:6px;\">Porotci</div>"
                    + "".join(lines)
                    + "</div>"
                )
            else:
                jm = porotci_map.get(komise_sel) or ""
                html += f"""
                <div style="padding:10px 12px;color:#333;">
                    <div style="font-size:13px;color:#555;margin-bottom:6px;">Porotci</div>
                    <p style="margin:0;"><strong>Porotci komise č.{komise_sel}:</strong> {escape(jm) if jm else "—"}</p>
                </div>
                """
    elif rezim == "seznam":
        html += """
            <table class="data-grid">
                <thead>
                <tr>
        """
        if edit_mode:
            html += "".join([
                th_sort("cislo", "Číslo"),
                th_sort("nazev", "Jméno"),
                th_sort("adresa", "Adresa"),
                th_sort("odruda", "Odrůda"),
                th_sort("privlastek", "Přívlastek"),
                th_sort("rocnik", "Rok"),
                th_plain("Akce"),
            ])
        else:
            html += '<th class="poradi">Pořadí</th>'
            html += th_sort("cislo", "Číslo")
            html += th_sort("nazev", "Jméno")
            html += th_sort("adresa", "Adresa")
            html += th_sort("odruda", "Odrůda")
            html += th_sort("privlastek", "Přívlastek")
            html += th_sort("rocnik", "Rok")
            html += th_sort("body", "Body")

        html += """
                </tr>
                </thead>
                <tbody>
        """

        if edit_mode:
            html += """
                <tr class="row-novy-vzorek">
                    <td class="cell-novy" title="Číslo vzorku doplní systém po uložení">+</td>
                    <td><input name="nazev" form="form-pridej" autocomplete="off" placeholder="Jméno / výrobce"></td>
                    <td><input name="adresa" form="form-pridej" autocomplete="off" placeholder="Obec"></td>
                    <td><input name="odruda" form="form-pridej" autocomplete="off" placeholder="Odrůda"></td>
                    <td><input name="privlastek" form="form-pridej" autocomplete="off" placeholder="Např. MZV"></td>
                    <td><input name="rocnik" form="form-pridej" autocomplete="off" placeholder="Ročník"></td>
                    <td><button class="btn btn-sm" type="submit" form="form-pridej">Přidat</button></td>
                </tr>
            """

        for v in vzorky_sorted:
            body_zobrazeni = format_body_hodnota(v["body"])

            if edit_mode:
                vid = v["id"]
                if edit_row_id and vid == edit_row_id:
                    html += f"""
                <form id="form-edit-{vid}" method="post" class="visually-hidden" aria-hidden="true">
                    <input type="hidden" name="action" value="update_vzorek">
                    <input type="hidden" name="vzorek_id" value="{vid}">
                    {ph}
                </form>
                <tr>
                    <td>{v["cislo"]}</td>
                    <td><input name="nazev" form="form-edit-{vid}" autocomplete="off" value="{escape(v["nazev"] or "")}"></td>
                    <td><input name="adresa" form="form-edit-{vid}" autocomplete="off" value="{escape(v["adresa"] or "")}"></td>
                    <td><input name="odruda" form="form-edit-{vid}" autocomplete="off" value="{escape(v["odruda"] or "")}"></td>
                    <td><input name="privlastek" form="form-edit-{vid}" autocomplete="off" value="{escape(v["privlastek"] or "")}"></td>
                    <td><input name="rocnik" form="form-edit-{vid}" autocomplete="off" value="{escape(v["rocnik"] or "")}"></td>
                    <td class="td-akce">
                        <button class="btn btn-sm btn-primary" type="submit" form="form-edit-{vid}">Uložit</button>
                        <form method="post" style="display:inline;">
                            <input type="hidden" name="action" value="edit_row_cancel">
                            {ph}
                            <button type="submit" class="btn btn-sm" title="Zrušit úpravy">×</button>
                        </form>
                    </td>
                </tr>
                    """
                else:
                    html += f"""
                <tr>
                    <td>{v["cislo"]}</td>
                    <td>{escape(v["nazev"] or "")}</td>
                    <td>{escape(v["adresa"] or "")}</td>
                    <td>{escape(v["odruda"] or "")}</td>
                    <td>{escape(v["privlastek"] or "")}</td>
                    <td>{escape(v["rocnik"] or "")}</td>
                    <td class="td-akce">
                        <form method="post" class="form-smaz" onsubmit="return window.confirm('Opravdu vymazat vzorek?\\n\\nOK = Ano, Zrušit = Ne.');">
                            <input type="hidden" name="action" value="smaz">
                            <input type="hidden" name="vzorek_id" value="{v["id"]}">
                            {ph}
                            <button type="submit" class="btn btn-sm btn-danger">Smazat</button>
                        </form>
                        <form method="post" style="display:inline;margin-left:6px;">
                            <input type="hidden" name="action" value="edit_row">
                            <input type="hidden" name="vzorek_id" value="{v["id"]}">
                            {ph}
                            <button type="submit" class="btn btn-sm">Editovat</button>
                        </form>
                    </td>
                </tr>
                    """
            else:
                p = poradi_map.get(v["id"])
                poradi_cell = f"{p}." if p else "—"
                html += f"""
                <tr>
                    <td class="poradi">{poradi_cell}</td>
                    <td>{v["cislo"]}</td>
                    <td>{escape(v["nazev"] or "")}</td>
                    <td>{escape(v["adresa"] or "")}</td>
                    <td>{escape(v["odruda"] or "")}</td>
                    <td>{escape(v["privlastek"] or "")}</td>
                    <td>{escape(v["rocnik"] or "")}</td>
                    <td>{body_zobrazeni if body_zobrazeni else "—"}</td>
                </tr>
                """

        html += """
                </tbody>
            </table>
        """

    html += """
        </div>
        </div>
        <script>
        (function () {{
            var chrome = document.getElementById('fixed-chrome');
            function syncChromeHeight() {{
                if (!chrome) return;
                var h = chrome.offsetHeight;
                document.documentElement.style.setProperty('--chrome-h', h + 'px');
            }}
            syncChromeHeight();
            window.addEventListener('resize', syncChromeHeight);
            window.addEventListener('load', syncChromeHeight);
            if (chrome && window.ResizeObserver) {{
                new ResizeObserver(syncChromeHeight).observe(chrome);
            }}
            var btn = document.getElementById('btn-help-toggle');
            var panel = document.getElementById('help-panel');
            if (btn && panel) {{
                btn.addEventListener('click', function () {{
                    panel.classList.toggle('is-open');
                    syncChromeHeight();
                }});
            }}
            document.querySelectorAll('.flash-close').forEach(function (btn) {{
                btn.addEventListener('click', function () {{
                    var el = btn.closest('.flash-msg');
                    if (el) el.remove();
                    syncChromeHeight();
                }});
            }});
            window.importSouborPotvrdit = function (inp) {{
                var ma = document.body.getAttribute('data-ma-vzorky') === '1';
                if (ma && !confirm('V degustaci už jsou vzorky. Pokračovat v importu?')) {{
                    inp.value = '';
                    return;
                }}
                inp.form.submit();
            }};
            var btnTisk = document.getElementById('btn-tisk-toggle');
            var panelTisk = document.getElementById('tisk-panel');
            var btnTiskClose = document.getElementById('btn-tisk-close');
            if (btnTisk && panelTisk) {{
                btnTisk.addEventListener('click', function () {{
                    panelTisk.classList.toggle('is-open');
                    syncChromeHeight();
                }});
            }}
            if (btnTiskClose && panelTisk) {{
                btnTiskClose.addEventListener('click', function () {{
                    panelTisk.classList.remove('is-open');
                    syncChromeHeight();
                }});
            }}
            if (panelTisk) {{
                panelTisk.addEventListener('click', function (ev) {{
                    var t = ev.target;
                    if (!t || !t.closest) return;
                    var link = t.closest('a');
                    if (link && link.closest('.tisk-panel-actions')) {{
                        panelTisk.classList.remove('is-open');
                        syncChromeHeight();
                    }}
                }});
            }}
            var sp = new URLSearchParams(location.search);
            var fb = sp.get('fb');
            if (fb) {{
                var inp = document.getElementById('barva-' + fb);
                if (inp) {{
                    inp.focus();
                    try {{ inp.select(); }} catch (e) {{}}
                }}
                sp.delete('fb');
                var nq = sp.toString();
                var nu = location.pathname + (nq ? '?' + nq : '') + location.hash;
                history.replaceState(null, '', nu);
            }}
            function komiseParseFloat(raw) {{
                if (raw == null) return null;
                var s = String(raw).trim().replace(',', '.');
                if (!s) return null;
                var n = parseFloat(s);
                return isNaN(n) ? null : n;
            }}
            function komiseCelkemFromForm(formId) {{
                var names = ['body_barva', 'body_cistota', 'body_vune', 'body_chut'];
                var parts = [];
                for (var i = 0; i < names.length; i++) {{
                    var el = document.querySelector('input[form="' + formId + '"][name="' + names[i] + '"]');
                    var p = el ? komiseParseFloat(el.value) : null;
                    if (p !== null) parts.push(p);
                }}
                if (!parts.length) return null;
                var t = 0;
                for (var j = 0; j < parts.length; j++) t += parts[j];
                return Math.round(t * 10) / 10;
            }}
            function komiseFmtCelkem(n) {{
                return n.toFixed(1).replace('.', ',');
            }}
            function komiseUpdateCelkem(formId, isInitial) {{
                var m = formId.match(/^ksave-(\\d+)$/);
                if (!m) return;
                var cell = document.getElementById('kom-celkem-' + m[1]);
                if (!cell) return;
                var sum = komiseCelkemFromForm(formId);
                if (sum === null && isInitial) return;
                cell.textContent = sum === null ? '—' : komiseFmtCelkem(sum);
            }}
            document.querySelectorAll('form[id^="ksave-"]').forEach(function (f) {{
                komiseUpdateCelkem(f.id, true);
                var onInp = function () {{ komiseUpdateCelkem(f.id, false); }};
                ['body_barva', 'body_cistota', 'body_vune', 'body_chut'].forEach(function (nm) {{
                    var inp = document.querySelector('input[form="' + f.id + '"][name="' + nm + '"]');
                    if (inp) {{
                        inp.addEventListener('input', onInp);
                        inp.addEventListener('change', onInp);
                    }}
                }});
            }});
        }})();
        </script>
    </body>
    </html>
    """

    return html


def _html_hodnoceni_mobilni(deg, vz_all, komise_cislo, por_txt, degustace_id):
    if not vz_all:
        return f"""<!DOCTYPE html>
<html lang="cs">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hodnocení</title>
<style>
body{{font-family:Arial,sans-serif;background:#f2f4f6;margin:0;padding:20px 14px;color:#1f2933;}}
.box{{max-width:520px;margin:24px auto;background:#fff;border:1px solid #dde2e8;border-radius:10px;padding:20px;}}
</style>
</head>
<body><div class="box">
<p style="margin:0 0 10px;"><strong>V této komisi zatím nejsou žádné vzorky.</strong></p>
<p style="margin:0;font-size:14px;color:#555;line-height:1.45;">Nejdřív na desktopu nechte aplikaci rozdělit vzorky do komisí
(Nastavení → počet komisí a uložení, případně tisk pro komise). Potom zkuste QR znovu.</p>
</div></body></html>"""

    labels, maxes = _hodnoceni_labels_maxes_from_deg(deg)
    boot = {
        "degId": degustace_id,
        "komise": komise_cislo,
        "degNazev": deg["nazev"] or "",
        "datumCz": format_datum_cz(deg["datum"]),
        "porotci": por_txt,
        "labels": labels,
        "maxes": maxes,
        "vzorky": [_vzorek_hodnoceni_payload(v) for v in vz_all],
        "x": _hodnoceni_hotovo_pocet(vz_all),
        "y": len(vz_all),
        "path": f"/hodnoceni/{degustace_id}/{komise_cislo}",
    }
    payload = json.dumps(boot, ensure_ascii=False).replace("</", "<\\/")
    title = escape(deg["nazev"] or "Hodnocení")

    html = f"""<!DOCTYPE html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Hodnocení – {title}</title>
<style>
:root {{ --bg:#f2f4f6; --card:#fff; --text:#1f2933; --muted:#667084; --accent:#2f5e2b; --border:#dde2e8; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; font-family: Arial, sans-serif; background: var(--bg); color: var(--text); }}
.app {{ max-width: 520px; margin: 0 auto; min-height: 100vh; padding-bottom: 28px; }}
.top {{ position: sticky; top: 0; z-index: 20; background: #fff; border-bottom: 1px solid var(--border);
  padding: 12px 14px 10px; box-shadow: 0 1px 0 rgba(0,0,0,0.04); }}
.top h1 {{ margin: 0 0 4px; font-size: 17px; line-height: 1.25; }}
.top .meta {{ font-size: 12px; color: var(--muted); margin: 0; line-height: 1.35; }}
.row-tools {{ display: flex; justify-content: space-between; align-items: center; margin-top: 8px; gap: 8px; flex-wrap: wrap; }}
.btn {{ border: 1px solid var(--border); background: #fff; border-radius: 8px; padding: 8px 12px; font-size: 13px; font-weight: 600; cursor: pointer; }}
.btn-primary {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
.btn:disabled {{ opacity: 0.45; cursor: not-allowed; }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; margin: 10px 12px; padding: 14px; }}
.cv {{ font-size: 28px; font-weight: 800; color: var(--accent); margin: 0 0 6px; }}
.subline {{ font-size: 15px; margin: 0 0 12px; line-height: 1.35; }}
.crit {{ margin: 12px 0; }}
.crit-lbl {{ font-size: 13px; color: var(--muted); margin-bottom: 6px; }}
.stepper {{ display: flex; align-items: center; gap: 10px; }}
.stepper button {{ width: 44px; height: 44px; border-radius: 10px; border: 1px solid var(--border); background: #fff; font-size: 22px; font-weight: 700; cursor: pointer; }}
.stepper .val {{ flex: 1; text-align: center; font-size: 22px; font-weight: 700; min-height: 44px; line-height: 44px; }}
.nav-row {{ display: flex; gap: 10px; margin: 14px 12px 0; }}
.nav-row button {{ flex: 1; }}
.hint {{ font-size: 12px; color: var(--muted); margin: 10px 12px 0; line-height: 1.35; }}
.modal-bg {{ display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.35); z-index: 100; padding: 16px; overflow: auto; }}
.modal-bg.open {{ display: block; }}
.modal {{ background: #fff; border-radius: 12px; max-width: 520px; margin: 20px auto; padding: 14px; border: 1px solid var(--border); }}
.modal h3 {{ margin: 0 0 10px; font-size: 16px; }}
.modal table {{ width: 100%; font-size: 12px; border-collapse: collapse; }}
.modal th, .modal td {{ border-bottom: 1px solid #eee; padding: 6px 4px; text-align: left; }}
.modal th {{ color: var(--muted); font-weight: 600; }}
</style>
</head>
<body>
<div class="app">
  <div class="top">
    <h1 id="hn-title">{title}</h1>
    <p class="meta" id="hn-sub"></p>
    <div class="row-tools">
      <span id="hn-count" style="font-size:14px;font-weight:600;"></span>
      <button type="button" class="btn" id="hn-check">Kontrola</button>
    </div>
  </div>
  <div id="hn-main"></div>
  <p class="hint" id="hn-hint"></p>
  <div class="nav-row">
    <button type="button" class="btn" id="hn-prev">« Předchozí</button>
    <button type="button" class="btn" id="hn-next">Další »</button>
  </div>
</div>
<div class="modal-bg" id="hn-modal-bg"><div class="modal">
  <h3>Kontrola — komise</h3>
  <div id="hn-modal-body"></div>
  <p style="margin-top:12px;"><button type="button" class="btn btn-primary" id="hn-modal-close">Zavřít</button></p>
</div></div>
<script type="application/json" id="hn-boot">{payload}</script>
"""

    html += """
<script>
(function () {
  var BOOT = JSON.parse(document.getElementById("hn-boot").textContent);
  function tok() {
    var p = new URLSearchParams(location.search);
    return p.get("t") || "";
  }
  function el(id) { return document.getElementById(id); }
  function fmtNum(n) {
    if (n == null || n !== n) return "—";
    var s = (Math.round(n * 10) / 10).toFixed(1);
    return s.replace(".", ",");
  }
  function sumB(b) {
    var p = [];
    for (var i = 0; i < 4; i++) if (b[i] != null) p.push(b[i]);
    if (p.length !== 4) return null;
    var t = 0; for (var j = 0; j < 4; j++) t += p[j];
    return Math.round(t * 10) / 10;
  }
  var VZ = JSON.parse(JSON.stringify(BOOT.vzorky));
  var ix = 0;
  var dirty = false;
  var editLocked = false;
  var overrideEdit = false;

  function cur() { return VZ[ix]; }

  function allFilledValid(b) {
    var mx = BOOT.maxes;
    for (var i = 0; i < 4; i++) {
      if (b[i] == null) return false;
      if (b[i] < 0 || b[i] > mx[i]) return false;
    }
    return true;
  }

  function syncLockState() {
    var c = cur();
    editLocked = !overrideEdit && c.complete === true;
  }

  function renderTop() {
    el("hn-sub").textContent = BOOT.datumCz + " · Komise č. " + BOOT.komise + (BOOT.porotci ? " · Členové komise: " + BOOT.porotci : "");
    el("hn-count").textContent = "Hodnoceno " + BOOT.x + " z " + BOOT.y;
  }

  function renderMain() {
    syncLockState();
    var c = cur();
    var b = c.b;
    var lockedUI = editLocked && !dirty;
    var html = '<div class="card">';
    html += '<p class="cv">č.v. ' + c.cislo + '</p>';
    html += '<p class="subline">' + (c.odruda || "—") + " · " + (c.privlastek || "—") + " · " + (c.rocnik || "—") + '</p>';
    for (var i = 0; i < 4; i++) {
      var dis = lockedUI ? " disabled" : "";
      var v = b[i];
      var show = fmtNum(v);
      html += '<div class="crit"><div class="crit-lbl">' + BOOT.labels[i] + " (max " + BOOT.maxes[i] + ')</div>';
      html += '<div class="stepper">';
      html += '<button type="button" class="hn-min"' + dis + ' data-i="' + i + '">−</button>';
      html += '<div class="val" data-vi="' + i + '">' + show + '</div>';
      html += '<button type="button" class="hn-plus"' + dis + ' data-i="' + i + '">+</button>';
      html += '</div></div>';
    }
    var sm = sumB(b);
    html += '<p style="margin:14px 0 0;font-size:16px;font-weight:700;color:var(--accent);">Celkem: ' + (sm == null ? "—" : fmtNum(sm)) + '</p>';
    html += '</div>';
    html += '<div class="nav-row" style="margin-top:16px;">';
    if (lockedUI) {
      html += '<button type="button" class="btn btn-primary" id="hn-edit" style="width:100%">Upravit</button>';
    } else {
      var canSave = allFilledValid(b);
      html += '<button type="button" class="btn btn-primary" id="hn-save" style="width:100%"' + (canSave ? "" : " disabled") + '>Uložit</button>';
    }
    html += '</div>';
    if (!lockedUI && !allFilledValid(b)) {
      html += '<p class="hint" style="margin-top:8px;">Vyplňte všechna čtyři kritéria v povoleném rozsahu.</p>';
    }
    el("hn-main").innerHTML = html;
    el("hn-hint").textContent = dirty ? "Máte neuložené změny u tohoto vzorku." : "";

    var mins = document.querySelectorAll(".hn-min");
    var pls = document.querySelectorAll(".hn-plus");
    for (var a = 0; a < mins.length; a++) {
      mins[a].onclick = function (ev) { step(parseInt(ev.target.getAttribute("data-i"), 10), -0.1); };
    }
    for (var b_ = 0; b_ < pls.length; b_++) {
      pls[b_].onclick = function (ev) { step(parseInt(ev.target.getAttribute("data-i"), 10), 0.1); };
    }
    var es = el("hn-edit");
    if (es) es.onclick = function () {
      if (!confirm("Upravit již uložené hodnocení?")) return;
      overrideEdit = true;
      dirty = false;
      renderMain();
    };
    var sv = el("hn-save");
    if (sv) sv.onclick = save;
  }

  function step(i, delta) {
    if (editLocked && !dirty) return;
    var c = cur();
    var mx = BOOT.maxes[i];
    var v = c.b[i];
    if (v == null) v = 0;
    v = Math.round((v + delta) * 10) / 10;
    if (v < 0) v = 0;
    if (v > mx) v = mx;
    c.b[i] = v;
    dirty = true;
    renderMain();
  }

  function save() {
    var c = cur();
    if (!allFilledValid(c.b)) return;
    var body = { t: tok(), vzorek_id: c.id, b1: c.b[0], b2: c.b[1], b3: c.b[2], b4: c.b[3] };
    fetch(BOOT.path + "?t=" + encodeURIComponent(tok()), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
    .then(function (o) {
      if (!o.ok || !o.j.ok) { alert(o.j.error || "Uložení se nezdařilo."); return; }
      var u = o.j.vzorek;
      for (var k = 0; k < VZ.length; k++) if (VZ[k].id === u.id) { VZ[k] = u; break; }
      BOOT.x = o.j.x;
      dirty = false;
      overrideEdit = false;
      renderTop();
      renderMain();
    }).catch(function () { alert("Chyba sítě."); });
  }

  function goPrev() {
    if (ix <= 0) return;
    if (dirty && !confirm("Opustit vzorek s neuloženými změnami?")) return;
    dirty = false;
    overrideEdit = false;
    ix--;
    renderMain();
    renderTop();
  }
  function goNext() {
    if (ix >= VZ.length - 1) return;
    if (dirty && !confirm("Opustit vzorek s neuloženými změnami?")) return;
    dirty = false;
    overrideEdit = false;
    ix++;
    renderMain();
    renderTop();
  }

  function openModal() {
    var h = '<table><thead><tr><th>č.v.</th><th>Odrůda</th><th>dílčí</th><th>celkem</th></tr></thead><tbody>';
    for (var i = 0; i < VZ.length; i++) {
      var r = VZ[i];
      var parts = [];
      for (var j = 0; j < 4; j++) parts.push(fmtNum(r.b[j]));
      var sm = sumB(r.b);
      h += "<tr><td>" + r.cislo + "</td><td>" + (r.odruda || "") + "</td><td>" + parts.join(" / ") + "</td><td>" + (sm == null ? "—" : fmtNum(sm)) + "</td></tr>";
    }
    h += "</tbody></table>";
    el("hn-modal-body").innerHTML = h;
    el("hn-modal-bg").classList.add("open");
  }

  el("hn-prev").onclick = goPrev;
  el("hn-next").onclick = goNext;
  el("hn-check").onclick = openModal;
  el("hn-modal-close").onclick = function () { el("hn-modal-bg").classList.remove("open"); };
  el("hn-modal-bg").onclick = function (e) { if (e.target === el("hn-modal-bg")) el("hn-modal-bg").classList.remove("open"); };

  renderTop();
  renderMain();
})();
</script>
</body>
</html>
"""
    return html


@app.route("/hodnoceni/<int:degustace_id>/<int:komise_cislo>", methods=["GET", "POST"])
def hodnoceni_komise(degustace_id, komise_cislo):
    conn = get_connection()
    deg = conn.execute(
        "SELECT * FROM degustace WHERE id = ?",
        (degustace_id,),
    ).fetchone()
    if not deg:
        conn.close()
        return _html_hodnoceni_chyba("Degustace neexistuje."), 404

    n_vz = conn.execute(
        "SELECT COUNT(*) FROM vzorky WHERE degustace_id = ?",
        (degustace_id,),
    ).fetchone()[0]
    n_kom = _degustace_pocet_komisi(deg, n_vz)

    if request.method == "POST":
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            conn.close()
            return jsonify(ok=False, error="Očekáváno JSON."), 400
        t = data.get("t")
        if not _hodnoceni_token_ok(deg["hodnoceni_token"], t):
            conn.close()
            return jsonify(ok=False, error="Neplatný token."), 403
        try:
            vid = int(data.get("vzorek_id"))
        except (TypeError, ValueError):
            conn.close()
            return jsonify(ok=False, error="Neplatný vzorek."), 400
        b1 = _parse_sc_float(data.get("b1"))
        b2 = _parse_sc_float(data.get("b2"))
        b3 = _parse_sc_float(data.get("b3"))
        b4 = _parse_sc_float(data.get("b4"))
        ok, err = _validate_komise_partials(deg, b1, b2, b3, b4, require_all=True)
        if not ok:
            conn.close()
            return jsonify(ok=False, error=err), 400
        row = conn.execute(
            "SELECT * FROM vzorky WHERE id = ? AND degustace_id = ?",
            (vid, degustace_id),
        ).fetchone()
        if not row:
            conn.close()
            return jsonify(ok=False, error="Vzorek nenalezen."), 404
        if int(row["komise_cislo"] or 0) != int(komise_cislo):
            conn.close()
            return jsonify(ok=False, error="Vzorek nepatří do této komise."), 403
        _komise_update_vzorek_body(
            conn,
            degustace_id,
            vid,
            b1,
            b2,
            b3,
            b4,
            row["poznamka"],
        )
        conn.commit()
        v_up = conn.execute("SELECT * FROM vzorky WHERE id = ?", (vid,)).fetchone()
        vz_all = conn.execute(
            """
            SELECT * FROM vzorky
            WHERE degustace_id = ? AND komise_cislo = ?
            ORDER BY cislo
            """,
            (degustace_id, komise_cislo),
        ).fetchall()
        conn.close()
        return jsonify(
            ok=True,
            vzorek=_vzorek_hodnoceni_payload(v_up),
            x=_hodnoceni_hotovo_pocet(vz_all),
            y=len(vz_all),
        )

    t = request.args.get("t")
    if not _hodnoceni_token_ok(deg["hodnoceni_token"], t):
        conn.close()
        return _html_hodnoceni_chyba("Neplatný nebo chybějící odkaz (token)."), 403

    if komise_cislo < 1 or komise_cislo > n_kom:
        conn.close()
        return _html_hodnoceni_chyba("Neplatné číslo komise."), 404

    vz_all = conn.execute(
        """
        SELECT * FROM vzorky
        WHERE degustace_id = ? AND komise_cislo = ?
        ORDER BY cislo
        """,
        (degustace_id, komise_cislo),
    ).fetchall()
    pr = conn.execute(
        """
        SELECT jmena FROM komise_porotci
        WHERE degustace_id = ? AND komise_cislo = ?
        """,
        (degustace_id, komise_cislo),
    ).fetchone()
    conn.close()
    por_txt = (pr["jmena"] or "").strip() if pr else ""
    return _html_hodnoceni_mobilni(deg, vz_all, komise_cislo, por_txt, degustace_id)


@app.route("/mobile-katalog/<int:id>")
def mobile_katalog(id):
    conn = get_connection()
    degustace = conn.execute(
        "SELECT * FROM degustace WHERE id = ?",
        (id,),
    ).fetchone()
    vzorky = conn.execute(
        "SELECT * FROM vzorky WHERE degustace_id = ? ORDER BY cislo",
        (id,),
    ).fetchall()
    porotci_rows = conn.execute(
        "SELECT komise_cislo, jmena FROM komise_porotci WHERE degustace_id=? ORDER BY komise_cislo",
        (id,),
    ).fetchall()
    conn.close()

    rank_all = [v for v in vzorky if v["body"] is not None]
    rank_all.sort(key=lambda v: (-float(v["body"]), v["cislo"]))
    poradi_map = {v["id"]: i + 1 for i, v in enumerate(rank_all)}

    abbr_to_full = {}
    full_to_abbr = {}
    for rel in ("input/odrudy.txt", "input/odrudy0.txt"):
        p = os.path.join(os.path.dirname(__file__), rel)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except UnicodeDecodeError:
            with open(p, "r", encoding="cp1250") as f:
                lines = f.read().splitlines()
        for ln in lines[1:]:
            parts = [x.strip() for x in ln.split("\t")]
            if len(parts) < 2:
                continue
            ab = parts[0]
            full = parts[1]
            if not ab:
                continue
            abbr_to_full[ab] = full or ab
            full_to_abbr[(full or "").casefold()] = ab

    abbr_entries = [{"abbr": a, "full": f} for a, f in abbr_to_full.items()]
    abbr_entries.sort(key=lambda x: x["abbr"].casefold())

    porotci_entries = []
    for r in porotci_rows:
        porotci_entries.append({
            "komise": int(r["komise_cislo"]),
            "jmena": (r["jmena"] or "").strip(),
        })

    data = []
    abbr_case_map = {k.casefold(): k for k in abbr_to_full.keys()}
    for v in vzorky:
        odr_full = (v["odruda"] or "").strip() or "Nezařazeno"
        ab_from_full = full_to_abbr.get(odr_full.casefold())
        ab_from_key = abbr_case_map.get(odr_full.casefold())
        odr_abbr = ab_from_full or ab_from_key or odr_full
        data.append({
            "id": int(v["id"]),
            "poradi": poradi_map.get(v["id"]),
            "cislo": v["cislo"],
            "vystavovatel": v["nazev"] or "",
            "adresa": v["adresa"] or "",
            "odruda": odr_full,
            "odruda_abbr": odr_abbr,
            "privlastek": v["privlastek"] or "",
            "rocnik": v["rocnik"] or "",
            "body": float(v["body"]) if v["body"] is not None else None,
        })

    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    payload_abbr = json.dumps(abbr_entries, ensure_ascii=False).replace("</", "<\\/")
    payload_porotci = json.dumps(porotci_entries, ensure_ascii=False).replace("</", "<\\/")
    title = escape(degustace["nazev"] or "E-katalog")

    html = f"""<!DOCTYPE html>
    <html lang="cs">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>E-katalog – {title}</title>
        <style>
            :root {{
                --bg: #f2f4f6;
                --card: #fff;
                --text: #1f2933;
                --muted: #667084;
                --accent: #2f5e2b;
                --border: #dde2e8;
            }}
            * {{ box-sizing: border-box; }}
            body {{ margin: 0; font-family: Arial, sans-serif; background: var(--bg); color: var(--text); }}
            .app {{ max-width: 720px; margin: 0 auto; min-height: 100vh; }}
            .top {{
                position: sticky; top: 0; z-index: 10; background: #fff;
                margin: 8px 12px 6px; padding: 12px 12px 10px;
                border: 1px solid var(--border); border-radius: 10px;
                box-shadow: 0 1px 0 rgba(0,0,0,0.03);
            }}
            .top-head {{ display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:8px; }}
            .title {{ font-size: 18px; font-weight: 700; margin: 0; }}
            .btn-info {{ border:1px solid var(--border); background:#fff; border-radius:8px; padding:7px 10px; font-size:12px; font-weight:600; }}
            .tabs {{ display: flex; gap: 6px; margin-bottom: 8px; }}
            .tab {{
                flex: 1; border: 1px solid var(--border); background:#fff; border-radius: 8px; padding: 8px 6px;
                font-size: 13px; font-weight: 600;
            }}
            .tab.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
            .search {{ width: 100%; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; font-size: 14px; }}
            .meta {{ color: var(--muted); font-size: 12px; margin: 6px 0 0; padding: 0 2px; display: none; }}
            .section-title {{ margin: 10px 12px 6px; font-size: 14px; color: var(--muted); font-weight: 700; }}
            /* overflow: visible — overflow:hidden na předkovi rozbíjí sticky thead v mobilních prohlížečích */
            .tbl-wrap {{ background: #fff; border: 1px solid var(--border); border-radius: 10px; margin: 0 12px 8px; overflow: visible; }}
            table.tbl {{ width: 100%; border-collapse: separate; border-spacing: 0; table-layout: fixed; font-size: 12px; }}
            .tbl thead th {{
                position: sticky; top: var(--thead-top, 200px); z-index: 9; background: #fff;
                padding: 8px 4px; text-align: left; font-weight: 700; color: #44505d;
                white-space: nowrap; border-bottom: 1px solid #e8edf2; vertical-align: middle;
            }}
            .tbl thead th:first-child {{ border-top-left-radius: 10px; }}
            .tbl thead th:last-child {{ border-top-right-radius: 10px; }}
            .tbl thead th.col-num {{ width: 44px; }}
            .tbl thead th.col-fav-star {{ width: 42px; text-align: center; }}
            .tbl thead th.col-vinar {{ width: 32%; }}
            .tbl thead th.col-odruda {{ width: 13%; }}
            .tbl thead th.col-priv {{ width: 9%; }}
            .tbl thead th.col-roc {{ width: 4.5rem; }}
            .tbl thead th.col-body {{ width: 3.25rem; }}
            .tbl thead th.col-tasted {{ width: 40px; text-align: center; }}
            .tbl thead th.col-priv, .tbl thead th.col-roc, .tbl thead th.col-body {{ text-align: center; }}
            .tbl thead th.col-priv .sort-btn, .tbl thead th.col-roc .sort-btn, .tbl thead th.col-body .sort-btn {{ width: 100%; text-align: center; }}
            .tbl tbody {{ position: relative; z-index: 0; }}
            .tbl tbody td {{ padding: 8px 4px; vertical-align: middle; border: none; }}
            .tbl tbody td.col-vinar {{ word-wrap: break-word; overflow-wrap: break-word; hyphens: auto; }}
            .tbl tbody td.col-priv, .tbl tbody td.col-roc, .tbl tbody td.col-body {{ text-align: center; }}
            .tbl tbody td.col-fav-star {{ text-align: center; width: 42px; }}
            .tbl tbody td.col-tasted {{ text-align: right; width: 40px; }}
            .tbl tbody tr.main-row td {{ border-top: 1px solid #f0f2f4; }}
            .num-btn {{
                border: 1px solid var(--border); background: #fff; border-radius: 999px; width: 34px; height: 34px;
                font-size: 12px; font-weight: 700; color: var(--accent);
            }}
            .num-btn.open {{ background: #eef6ed; border-color: #b9d4b4; }}
            .fav-inline {{
                border: none; background: transparent; padding: 4px; font-size: 20px; line-height: 1;
                color: #9a6d00; min-width: 38px; text-align: center;
            }}
            .fav-inline.on {{ color: #c77900; }}
            .tasted-btn {{
                border: none; background: transparent; padding: 4px; font-size: 18px; line-height: 1;
                color: #94a0ad; min-width: 36px; text-align: center;
            }}
            .tasted-btn.on {{ color: var(--accent); font-weight: 700; }}
            .detail-row {{ display: none; }}
            .detail-row.open {{ display: table-row; }}
            .detail-box {{ color: var(--muted); font-size: 12px; padding-top: 0; }}
            .sort-btn {{ border: none; background: transparent; padding: 0; color: inherit; font: inherit; cursor: pointer; text-align: left; }}
            .sort-btn-active {{ color: var(--accent); font-weight: 700; }}
            .sort-col-active {{ background: #f4faf3; }}
            .sort-sym {{
                display: inline-block; margin-left: 4px; font-size: 13px; font-weight: 800;
                color: #5c6b7a; min-width: 1.1em; text-align: center;
            }}
            .sort-sym-active {{
                color: var(--accent); background: #e8f2e6; border-radius: 4px; padding: 1px 5px;
            }}
            .empty {{ margin: 20px 12px; padding: 14px; border: 1px dashed var(--border); border-radius: 10px; color: var(--muted); background:#fff; }}
            .modal-bg {{ position: fixed; inset: 0; background: rgba(0,0,0,0.28); display: none; z-index: 30; }}
            .modal-bg.open {{ display: block; }}
            .modal {{
                position: absolute; left: 50%; top: 50%; transform: translate(-50%, -50%);
                width: min(92vw, 720px); max-height: 86vh; overflow: auto;
                background: #fff; border-radius: 12px; border: 1px solid var(--border); padding: 12px;
            }}
            .modal-head {{ display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }}
            .close-x {{ border:none; background:transparent; font-size:22px; line-height:1; cursor:pointer; }}
            .abbr-grid {{ display:grid; grid-template-columns: 90px 1fr; gap: 4px 10px; font-size: 12px; }}
            .abbr-grid div {{ padding: 2px 0; }}
            .kom-list p {{ margin: 4px 0; font-size: 12px; }}
        </style>
    </head>
    <body>
    <div class="app">
        <div class="top">
            <div class="top-head">
                <h1 class="title">{title}</h1>
                <button id="btn-info" class="btn-info" type="button">Info o degustaci</button>
            </div>
            <div class="tabs">
                <button class="tab active" data-mode="all">Vše</button>
                <button class="tab" data-mode="odrudy">Odrůdy</button>
                <button class="tab" data-mode="fav">Oblíbené</button>
            </div>
            <input id="q" class="search" type="search" placeholder="Hledat: vystavovatel, odrůda, ročník...">
            <div class="meta" id="count"></div>
        </div>
        <div id="list"></div>
    </div>
    <div class="modal-bg" id="modal-bg">
      <div class="modal" id="modal-card">
        <div class="modal-head">
          <strong>Info o degustaci</strong>
          <button type="button" class="close-x" id="btn-close" aria-label="Zavřít">×</button>
        </div>
        <h3 style="margin:8px 0 6px;font-size:13px;">Význam zkratek</h3>
        <div class="abbr-grid" id="abbr-grid"></div>
        <h3 style="margin:12px 0 6px;font-size:13px;">Členové komisí</h3>
        <div class="kom-list" id="kom-list"></div>
      </div>
    </div>
    <script>
    const vzorky = {payload};
    const odrudyInfo = {payload_abbr};
    const porotci = {payload_porotci};
    const favKey = "ekatalog-favorites-{id}";
    const tastedKey = "ekatalog-tasted-{id}";
    const state = {{
      mode: "all",
      query: "",
      fav: new Set(JSON.parse(localStorage.getItem(favKey) || "[]")),
      tasted: new Set(JSON.parse(localStorage.getItem(tastedKey) || "[]")),
      expanded: new Set(),
      sortKey: "body",
      sortDir: "desc"
    }};
    const listEl = document.getElementById("list");
    const countEl = document.getElementById("count");
    const qEl = document.getElementById("q");
    const modalBg = document.getElementById("modal-bg");
    const modalCard = document.getElementById("modal-card");
    function cmpVal(a, b, numeric) {{
      const aa = (a == null || a === "") ? null : a;
      const bb = (b == null || b === "") ? null : b;
      if (aa == null && bb == null) return 0;
      if (aa == null) return 1;
      if (bb == null) return -1;
      if (numeric) return Number(aa) - Number(bb);
      return String(aa).localeCompare(String(bb), "cs");
    }}
    function sortRows(arr) {{
      const key = state.sortKey;
      const dir = state.sortDir === "asc" ? 1 : -1;
      const out = [...arr];
      out.sort((a, b) => {{
        if (key === "body" || key === "poradi" || key === "cislo") {{
          const c = cmpVal(a[key], b[key], true);
          if (c !== 0) return c * dir;
          return cmpVal(a.cislo, b.cislo, true);
        }}
        const c = cmpVal(a[key], b[key], false);
        if (c !== 0) return c * dir;
        return cmpVal(a.cislo, b.cislo, true);
      }});
      return out;
    }}
    function toggleFav(id) {{
      if (state.fav.has(id)) state.fav.delete(id); else state.fav.add(id);
      localStorage.setItem(favKey, JSON.stringify([...state.fav]));
      render();
    }}
    function toggleTasted(id) {{
      if (state.tasted.has(id)) state.tasted.delete(id); else state.tasted.add(id);
      localStorage.setItem(tastedKey, JSON.stringify([...state.tasted]));
      render();
    }}
    function toggleDetail(id) {{
      if (state.expanded.has(id)) state.expanded.delete(id); else state.expanded.add(id);
      render();
    }}
    function setSort(key) {{
      if (state.sortKey === key) state.sortDir = state.sortDir === "asc" ? "desc" : "asc";
      else {{ state.sortKey = key; state.sortDir = "asc"; }}
      render();
    }}
    function sortSym(key) {{
      if (state.sortKey !== key) return "↕";
      return state.sortDir === "asc" ? "↑" : "↓";
    }}
    const SORT_COL_CLASS = {{ vystavovatel: " col-vinar", odruda: " col-odruda", privlastek: " col-priv", rocnik: " col-roc", body: " col-body" }};
    function thSort(key, label) {{
      const active = state.sortKey === key;
      const ac = active ? " sort-col-active" : "";
      const ba = active ? " sort-btn-active" : "";
      const sa = active ? " sort-sym-active" : "";
      const col = SORT_COL_CLASS[key] || "";
      return '<th class="col-sort' + ac + col + '" scope="col"><button type="button" class="sort-btn' + ba + '" data-sort="' + key + '">' + label + ' <span class="sort-sym' + sa + '">' + sortSym(key) + '</span></button></th>';
    }}
    function fmtBody(v) {{ return v == null ? "—" : String(v.toFixed ? v.toFixed(1) : v).replace(".", ","); }}
    function rowText(v) {{
      return [v.vystavovatel, v.adresa, v.odruda, v.privlastek, v.rocnik, v.cislo].join(" ").toLowerCase();
    }}
    function getBase() {{
      let out = [...vzorky];
      if (state.mode === "fav") out = out.filter(v => state.fav.has(v.id));
      if (state.query) out = out.filter(v => rowText(v).includes(state.query));
      return sortRows(out);
    }}
    function headerRow() {{
      return '<thead><tr>' +
        '<th class="col-num" scope="col">#</th>' +
        '<th class="col-fav-star" scope="col" aria-label="Oblíbené"></th>' +
        thSort("vystavovatel", "Vinař") +
        thSort("odruda", "Odrůda") +
        thSort("privlastek", "Přívl.") +
        thSort("rocnik", "Rok") +
        thSort("body", "Body") +
        '<th class="col-tasted" scope="col" aria-label="Koštováno"></th>' +
        '</tr></thead>';
    }}
    function twoRows(v, useAbbr) {{
      const por = v.poradi ? (v.poradi + ".") : "—";
      const isOpen = state.expanded.has(v.id);
      const fav = state.fav.has(v.id) ? "★" : "☆";
      const odr = useAbbr ? (v.odruda_abbr || v.odruda) : (v.odruda || "—");
      return `
      <tr class="main-row">
        <td class="col-num"><button type="button" class="num-btn ${{isOpen ? "open" : ""}}" data-exp="${{v.id}}">${{v.cislo}}</button></td>
        <td class="col-fav-star"><button type="button" class="fav-inline ${{state.fav.has(v.id) ? "on" : ""}}" data-fav="${{v.id}}" aria-label="Oblíbený vzorek">${{fav}}</button></td>
        <td class="col-vinar">${{v.vystavovatel || "—"}}</td>
        <td class="col-odruda">${{odr}}</td>
        <td class="col-priv">${{v.privlastek || "—"}}</td>
        <td class="col-roc">${{v.rocnik || "—"}}</td>
        <td class="col-body">${{fmtBody(v.body)}}</td>
        <td class="col-tasted"><button type="button" class="tasted-btn ${{state.tasted.has(v.id) ? "on" : ""}}" data-tasted="${{v.id}}" aria-pressed="${{state.tasted.has(v.id)}}" aria-label="Koštováno">${{state.tasted.has(v.id) ? "✓" : "○"}}</button></td>
      </tr>
      <tr class="detail-row ${{isOpen ? "open" : ""}}">
        <td colspan="8" class="detail-box"><strong>Pořadí:</strong> ${{por}} &nbsp;&nbsp; <strong>Adresa:</strong> ${{v.adresa || "—"}}</td>
      </tr>`;
    }}
    function renderTable(rows, useAbbr) {{
      if (!rows.length) return `<div class="empty">Žádné položky pro aktuální filtr.</div>`;
      return `<div class="tbl-wrap"><table class="tbl">${{headerRow()}}<tbody>${{rows.map(v => twoRows(v, useAbbr)).join("")}}</tbody></table></div>`;
    }}
    function renderByOdrudy(base) {{
      if (!base.length) return `<div class="empty">Žádné položky pro aktuální filtr.</div>`;
      const grp = {{}};
      for (const v of base) {{ (grp[v.odruda || "Nezařazeno"] ||= []).push(v); }}
      const keys = Object.keys(grp).sort((a,b)=>a.localeCompare(b,"cs"));
      return keys.map(k => `<div class="section-title">${{k}} (${{grp[k].length}})</div>${{renderTable(grp[k], true)}}`).join("");
    }}
    function bindActions() {{
      listEl.querySelectorAll("[data-fav]").forEach(btn => btn.addEventListener("click", (e) => {{ e.preventDefault(); e.stopPropagation(); toggleFav(Number(btn.dataset.fav)); }}));
      listEl.querySelectorAll("[data-tasted]").forEach(btn => btn.addEventListener("click", (e) => {{ e.preventDefault(); e.stopPropagation(); toggleTasted(Number(btn.dataset.tasted)); }}));
      listEl.querySelectorAll("[data-exp]").forEach(btn => btn.addEventListener("click", () => toggleDetail(Number(btn.dataset.exp))));
      listEl.querySelectorAll("[data-sort]").forEach(btn => btn.addEventListener("click", () => setSort(btn.dataset.sort)));
    }}
    function syncStickyTop() {{
      const topEl = document.querySelector(".top");
      if (!topEl) return;
      const h = Math.round(topEl.getBoundingClientRect().height);
      document.documentElement.style.setProperty("--thead-top", h + "px");
    }}
    function scheduleStickySync() {{
      syncStickyTop();
      requestAnimationFrame(() => {{
        syncStickyTop();
        requestAnimationFrame(() => {{ syncStickyTop(); }});
      }});
    }}
    function fillInfoModal() {{
      const ag = document.getElementById("abbr-grid");
      const kl = document.getElementById("kom-list");
      ag.innerHTML = odrudyInfo.map(r => `<div><strong>${{r.abbr}}</strong></div><div>${{r.full}}</div>`).join("");
      if (!porotci.length) kl.innerHTML = `<p>Členové komisí zatím nejsou vyplněni.</p>`;
      else kl.innerHTML = porotci.map(r => `<p><strong>Komise č.${{r.komise}}:</strong> ${{r.jmena || "—"}}</p>`).join("");
    }}
    function render() {{
        const allInMode = sortRows((state.mode === "fav")
            ? vzorky.filter(v => state.fav.has(v.id))
            : [...vzorky]
        );
        const base = getBase();

        const filterOn = !!state.query;
        countEl.style.display = filterOn ? "block" : "none";
        if (filterOn) {{
            countEl.textContent = `Zobrazeno položek ${{base.length}}/${{allInMode.length}}`;
        }}

        if (state.mode === "odrudy") listEl.innerHTML = renderByOdrudy(base);
        else if (state.mode === "fav") listEl.innerHTML = base.length ? renderTable(base, false) : `<div class="empty">Zatím nemáte žádné oblíbené vzorky.</div>`;
        else listEl.innerHTML = renderTable(base, false);

        bindActions();
        scheduleStickySync();
    }}


    document.querySelectorAll(".tab").forEach(btn => btn.addEventListener("click", () => {{
      document.querySelectorAll(".tab").forEach(x => x.classList.remove("active"));
      btn.classList.add("active");
      state.mode = btn.getAttribute("data-mode");
      render();
    }}));
    qEl.addEventListener("input", () => {{ state.query = qEl.value.trim().toLowerCase(); render(); }});
    window.addEventListener("resize", scheduleStickySync);
    window.addEventListener("orientationchange", scheduleStickySync);
    (function initStickyHeader() {{
      const topEl = document.querySelector(".top");
      if (!topEl) return;
      if (window.ResizeObserver) {{
        new ResizeObserver(() => scheduleStickySync()).observe(topEl);
      }}
      let scrollRaf = 0;
      window.addEventListener("scroll", () => {{
        if (scrollRaf) return;
        scrollRaf = requestAnimationFrame(() => {{ scrollRaf = 0; scheduleStickySync(); }});
      }}, {{ passive: true }});
    }})();
    document.getElementById("btn-info").addEventListener("click", () => modalBg.classList.add("open"));
    document.getElementById("btn-close").addEventListener("click", () => modalBg.classList.remove("open"));
    modalBg.addEventListener("click", (e) => {{ if (e.target === modalBg) modalBg.classList.remove("open"); }});
    fillInfoModal();
    render();
    </script>
    </body></html>
    """
    return html


@app.route("/katalog_tisk/<int:id>")
def katalog_tisk(id):
    conn = get_connection()
    degustace = conn.execute(
        "SELECT * FROM degustace WHERE id = ?",
        (id,),
    ).fetchone()
    vzorky = conn.execute(
        "SELECT * FROM vzorky WHERE degustace_id = ? ORDER BY cislo",
        (id,),
    ).fetchall()
    conn.close()

    top_x = degustace["katalog_top_x"]
    try:
        top_x = int(top_x) if top_x is not None else 15
    except (TypeError, ValueError):
        top_x = 15
    top_x = max(1, min(200, top_x))
    fmt = (degustace["katalog_format"] or "A4").strip().upper()
    if fmt not in ("A4", "A5"):
        fmt = "A4"

    top_scored = [v for v in vzorky if v["body"] is not None]
    top_scored.sort(key=lambda v: (-float(v["body"]), v["cislo"]))
    top_scored = top_scored[:top_x]

    by_odruda = {}
    for v in vzorky:
        k = (v["odruda"] or "Nezařazeno").strip() or "Nezařazeno"
        by_odruda.setdefault(k, []).append(v)
    odrudy_sorted = sorted(by_odruda.keys(), key=lambda x: x.casefold())
    for k in odrudy_sorted:
        by_odruda[k].sort(key=lambda v: ((v["nazev"] or "").casefold(), v["cislo"]))

    poradi_all = [v for v in vzorky if v["body"] is not None]
    poradi_all.sort(key=lambda v: (-float(v["body"]), v["cislo"]))
    poradi_map = {v["id"]: i + 1 for i, v in enumerate(poradi_all)}

    sheet_w = "210mm" if fmt == "A4" else "148mm"
    mobile_url = request.url_root.rstrip("/") + f"/mobile-katalog/{id}"
    mobile_qr = f"https://api.qrserver.com/v1/create-qr-code/?size=140x140&data={quote(mobile_url, safe='')}"
    font_pt = degustace["katalog_font_pt"]
    try:
        font_pt = int(font_pt) if font_pt is not None else 8
    except (TypeError, ValueError):
        font_pt = 8
    font_pt = max(6, min(10, font_pt))

    line_h = "1.1" if font_pt <= 7 else "1.2"

    html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>Katalog – {escape(degustace["nazev"] or "")}</title>
        <style>
            @page {{ size: {fmt}; margin: 7mm; }}
            body {{ margin: 0; background:#efefef; color:#222; font-family: Arial, sans-serif; }}
            .sheet {{
                width: {sheet_w};
                min-height: calc({sheet_w} * 1.414);
                margin: 10px auto;
                background:#fff;
                box-shadow: 0 0 0 1px #ddd;
                padding: 7mm;
                box-sizing: border-box;
                font-size: {font_pt}pt;
                line-height: {line_h};
            }}
            h1 {{ margin:0 0 1mm 0; font-size:1.6em; }}
            h2 {{ margin:3mm 0 1.5mm; font-size:1.25em; font-weight:700; }}
            h3 {{ margin:2mm 0 1mm; font-size:1.1em; font-weight:700; }}
            .meta {{ margin-bottom:2mm; color:#555; font-size:8pt; }}
            .top-wrap {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 4mm; margin-bottom: 2mm; }}
            .top-left {{ min-width: 0; }}
            .qr-box {{ text-align: right; }}
            .qr-box img {{ width: 24mm; height: 24mm; display: block; margin-left: auto; }}
            .qr-box a {{ color:#555; text-decoration: none; font-size: 7pt; }}
            .qr-box a:hover {{ text-decoration: underline; }}
            table {{ width:100%; border-collapse:collapse; margin:0 0 2mm 0; table-layout: fixed; }}
            th, td {{ border:none; padding:0.8mm 1.1mm; vertical-align:top; text-align:left; }}
            table, thead th, tbody td {{ font-size:{font_pt}pt; line-height:{line_h}; }}
            thead th {{ font-weight:700; }}
            .odr-block {{ margin:0 0 2mm 0; page-break-inside: avoid; }}
            @media print {{
                body {{ background:#fff; }}
                .sheet {{ margin: 0; width: auto; min-height: auto; box-shadow: none; padding: 0; }}
            }}
        </style>
    </head>
    <body>
        <div class="sheet">
        <div class="top-wrap">
            <div class="top-left">
                <h1>{escape(degustace["nazev"] or "")}</h1>
                <div class="meta">Katalog vzorků · datum {escape(format_datum_cz(degustace["datum"]))} · formát {fmt} · font {font_pt} pt</div>
            </div>
            <div class="qr-box">
                <img src="{mobile_qr}" alt="QR odkaz na mobilní e-katalog">
                <a href="{mobile_url}" target="_blank">Mobilní e-katalog</a>
            </div>
        </div>
        <h2>TOP {top_x} vzorků podle pořadí</h2>
        <table>
            <tr><th>Pořadí</th><th>Číslo</th><th>Vystavovatel</th><th>Odrůda</th><th>Přívlastek</th><th>Rok</th><th>Body</th></tr>
    """
    if top_scored:
        for v in top_scored:
            por = poradi_map.get(v["id"])
            por_txt = f"{por}." if por else "—"
            html += f"""
            <tr>
                <td>{por_txt}</td><td>{v["cislo"]}</td><td>{escape(v["nazev"] or "")}</td><td>{escape(v["odruda"] or "")}</td>
                <td>{escape(v["privlastek"] or "")}</td><td>{escape(v["rocnik"] or "")}</td><td>{format_body_hodnota(v["body"]) or "—"}</td>
            </tr>
            """
    else:
        html += '<tr><td colspan="7" style="text-align:center;color:#666;">Zatím nejsou zadané body.</td></tr>'
    html += "</table><h2>Všechny vzorky podle odrůd</h2>"
    for odr in odrudy_sorted:
        html += f'<div class="odr-block"><h3>{escape(odr)}</h3><table><tr><th>Pořadí</th><th>Číslo</th><th>Vystavovatel</th><th>Adresa</th><th>Přívlastek</th><th>Rok</th><th>Body</th></tr>'
        for v in by_odruda[odr]:
            por = poradi_map.get(v["id"])
            por_txt = f"{por}." if por else "—"
            html += f"""
            <tr>
                <td>{por_txt}</td><td>{v["cislo"]}</td><td>{escape(v["nazev"] or "")}</td><td>{escape(v["adresa"] or "")}</td>
                <td>{escape(v["privlastek"] or "")}</td><td>{escape(v["rocnik"] or "")}</td><td>{format_body_hodnota(v["body"]) or "—"}</td>
            </tr>
            """
        html += "</table></div>"
    html += "</div></body></html>"
    return html


@app.route("/tisk/<int:id>")
def tisk(id):
    conn = get_connection()

    degustace = conn.execute(
        "SELECT * FROM degustace WHERE id = ?",
        (id,)
    ).fetchone()

    vzorky = conn.execute(
        "SELECT * FROM vzorky WHERE degustace_id = ? ORDER BY cislo",
        (id,)
    ).fetchall()

    pocet_komisi = _degustace_pocet_komisi(degustace, len(vzorky))
    mode = (request.args.get("mode") or "").strip().lower()
    rozdeleni_existuje = _komise_prirazeni_existuje(vzorky)

    if mode not in ("use", "regen"):
        # Volba je na stránce degustace (panel); přímý odkaz použije stávající rozdělení.
        mode = "use"

    if mode == "regen" or not rozdeleni_existuje:
        _komise_generovat_prirazeni(conn, id, pocet_komisi)
        vzorky = conn.execute(
            "SELECT * FROM vzorky WHERE degustace_id = ? ORDER BY cislo",
            (id,)
        ).fetchall()
        rozdeleni_existuje = True

    conn.close()
    datum_tisk = format_datum_cz(degustace["datum"])

    html = """
    <html>
    <head>
        <title>Bodovací tabulka</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                max-width: 1280px;
                margin: 12px auto;
                padding: 0 20px 32px;
                color: #222;
                background: #fff;
            }
            h1 {
                text-align: center;
                margin-bottom: 10px;
            }
            .page {
                page-break-after: always;
                margin-bottom: 40px;
            }
            .page:last-child {
                page-break-after: auto;
            }
            table {
                width: 100%;
                border-collapse: collapse;
                margin-top: 10px;
            }
            th, td {
                border: 1px solid #000;
                padding: 6px;
                text-align: center;
                font-size: 12px;
            }
            th {
                background: #eee;
            }
            .header {
                display: flex;
                justify-content: space-between;
                margin-bottom: 10px;
            }
            .footer {
                margin-top: 20px;
                display: flex;
                justify-content: space-between;
            }
        </style>
    </head>
    <body>
    """

    for komise_cislo in range(1, pocet_komisi + 1):
        page = [v for v in vzorky if int(v["komise_cislo"] or 0) == komise_cislo]
        page.sort(key=lambda r: r["cislo"])
        html += f"""
        <div class="page">
            <h1>Bodovací tabulka - komise č.{komise_cislo}</h1>

            <div class="header">
                <div><strong>Datum:</strong> {datum_tisk}</div>
                <div><strong>Strana:</strong> {komise_cislo}</div>
            </div>

            <table>
                <tr>
                    <th>č.v.</th>
                    <th>odrůda</th>
                    <th>jakost</th>
                    <th>ročník</th>
                    <th>Barva<br>0 - 2</th>
                    <th>Čistota<br>0 - 2</th>
                    <th>Vůně<br>0 - 4</th>
                    <th>Chuť<br>0 - 12</th>
                    <th>celkem</th>
                    <th>poznámka</th>
                </tr>
        """

        for v in page:
            html += f"""
            <tr>
                <td>{v['cislo']}</td>
                <td>{escape(v['odruda'] or '')}</td>
                <td>{escape(v['privlastek'] or '')}</td>
                <td>{escape(v['rocnik'] or '')}</td>
                <td></td>
                <td></td>
                <td></td>
                <td></td>
                <td></td>
                <td style="text-align:left;"></td>
            </tr>
            """

        html += f"""
            </table>

            <div class="footer">
                <div><strong>Počet vzorků:</strong> {len(page)}</div>
                <div>Jméno porotce: ____________________</div>
                <div>Podpis: ____________________</div>
            </div>
        </div>
        """

    html += "</body></html>"

    return html


init_db()

if __name__ == "__main__":
    app.run(debug=True)
