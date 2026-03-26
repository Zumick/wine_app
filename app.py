from flask import Flask, request, redirect, flash, get_flashed_messages, session
from markupsafe import escape
from urllib.parse import urlencode
from db import get_connection
import csv
import io
import os

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-nahradit-pro-produkci")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024

SESSION_EDIT_PREFIX = "edit_deg_"
SESSION_REZIM_PREFIX = "rezim_deg_"
SESSION_KOMISE_PREFIX = "komise_deg_"
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
    s = (raw or "").strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


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
    Importuje vzorky z textu (tabulka s hlavičkou).
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

    reader = csv.DictReader(f, delimiter=delim)
    if not reader.fieldnames:
        return {"ok": False, "error": "V souboru chybí hlavička sloupců."}

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
            nazev = (row.get("Jméno") or "").strip()
            adresa = (row.get("Adresa") or "").strip()
            odruda = (row.get("Odrůda") or "").strip()
            privlastek = (row.get("Přívlastek") or "").strip()
            rocnik = (row.get("Rok") or "").strip()
            body_raw = (row.get("Body") or "").strip()

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
            "error": "Nepodařilo se naimportovat žádný řádek. Zkontrolujte sloupce (Jméno, Odrůda, …) a oddělovače.",
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
            f'<div style="padding:10px 14px;margin-bottom:12px;border-radius:6px;'
            f'border:1px solid {barva};background:{pozadí};color:#222;">{t}</div>'
        )
    return '<div style="max-width:1280px;margin:0 auto 16px;padding:0 20px;">' + "".join(bloky) + "</div>"


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
            pocet_raw = request.form.get("pocet_komisi") or "3"
            try:
                pocet_komisi = int(pocet_raw)
            except ValueError:
                pocet_komisi = 3
            if pocet_komisi < 1:
                pocet_komisi = 1
            if pocet_komisi > 10:
                pocet_komisi = 10
            conn.execute(
                "INSERT INTO degustace (nazev, datum, pocet_komisi) VALUES (?, ?, ?)",
                (request.form["nazev"], request.form["datum"], pocet_komisi)
            )
            conn.commit()
            conn.close()
            return redirect("/")

        elif action == "vyber":
            conn.close()
            return redirect(f"/degustace/{request.form['degustace_id']}")

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
        <h1>Degustace vín</h1>

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
                    Počet komisí (1–10)<br>
                    <input type="number" name="pocet_komisi" min="1" max="10" value="3" style="width: 90px;">
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
            if r not in ("seznam", "komise"):
                r = "seznam"
            session[SESSION_REZIM_PREFIX + str(id)] = r
            if r == "komise" and session.get(SESSION_EDIT_PREFIX + str(id), True):
                kk = SESSION_KOMISE_PREFIX + str(id)
                if session.get(kk, 1) == -1:
                    session[kk] = 1
            session.modified = True
            conn.close()
            return redirect(red)

        if action == "set_komise":
            k = request.form.get("komise") or "1"
            edit_now = session.get(SESSION_EDIT_PREFIX + str(id), True)
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

        if action == "porotci_uloz":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "komise":
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

        if action == "komise_uloz":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "komise":
                conn.close()
                return redirect(red)
            vid = request.form["vzorek_id"]
            bb = _parse_sc_float(request.form.get("body_barva"))
            bc = _parse_sc_float(request.form.get("body_cistota"))
            bv = _parse_sc_float(request.form.get("body_vune"))
            bch = _parse_sc_float(request.form.get("body_chut"))
            poz = (request.form.get("poznamka") or "").strip()
            parts = [x for x in (bb, bc, bv, bch) if x is not None]
            celkem = round(sum(parts), 1) if parts else None
            conn.execute("""
                UPDATE vzorky SET body_barva=?, body_cistota=?, body_vune=?, body_chut=?, body=?, poznamka=?
                WHERE id=? AND degustace_id=?
            """, (bb, bc, bv, bch, celkem, poz or None, vid, id))
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
                vid_int = int(vid)
                j = tab_ids.index(vid_int)
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

    edit_mode = session.get(SESSION_EDIT_PREFIX + str(id), True)
    rezim = session.get(SESSION_REZIM_PREFIX + str(id), "seznam")
    if rezim not in ("seznam", "komise"):
        rezim = "seznam"

    vzorky_o = list(vzorky)
    n_kom = pocet_komisi

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
    else:
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
    else:
        title_rezim_suffix = (
            "Komise · Vše" if komise_sel == -1 else f"Komise · č. {komise_sel}"
        )

    tisk_html = ""
    komise_select_html = ""
    if rezim == "komise":
        tisk_html = (
            f'<a class="btn btn-primary btn-sm" href="/tisk/{id}" target="_blank">Tisk pro komise</a>'
        )
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

    controls_sub = ""
    if rezim == "seznam":
        if edit_mode:
            controls_sub = f"""
            <div class="controls-sub">
                <div class="import-help-row">
                    <form id="form-import" method="post" enctype="multipart/form-data" class="import-row">
                        <input type="hidden" name="action" value="import">
                        {ph}
                        <input type="file" name="soubor" id="input-import-file" class="visually-hidden"
                            accept=".csv,.txt,.tsv,text/csv,text/plain"
                            onchange="if(this.files.length)this.form.submit()">
                        <label for="input-import-file" class="btn">Import dat ze souboru</label>
                    </form>
                    <button type="button" class="btn-help" id="btn-help-toggle" title="Nápověda" aria-label="Nápověda">?</button>
                </div>
                <div id="help-panel" class="help-panel">
                    <p><strong>Filtrování</strong> (režim Zobrazení): slova oddělte mezerou. Řádek musí obsahovat
                    <em>všechna</em> slova kdykoli v řádku (AND).</p>
                    <p><strong>Import:</strong> tabulka z Excelu (tabulátory), CSV nebo středníky. Sloupce: č.v. (ignoruje se),
                    Jméno, Adresa, Odrůda, Přívlastek, Rok, Body (volitelně). Číslo vzorku vždy přidělí aplikace. Stejná
                    kombinace Jméno + Odrůda + Přívlastek + Rok jako u již uloženého vzorku nebo dvakrát v souboru → řádek se přeskočí.</p>
                </div>
            </div>
            """
        else:
            zrusit_f = ""
            if q_raw:
                href_clear = _build_degustace_url(id, sort_key, sort_dir, "")
                zrusit_f = f'<a class="btn btn-ghost" href="{href_clear}">Zrušit filtr</a>'
            controls_sub = f"""
            <div class="controls-sub">
                <form method="get" action="/degustace/{id}" class="filter-row" role="search">
                    <input type="hidden" name="sort" value="{escape(sort_key)}">
                    <input type="hidden" name="dir" value="{escape(sort_dir)}">
                    <label for="filtr-q" class="filter-label">Hledat</label>
                    <input id="filtr-q" type="search" name="q" value="{escape(q_raw)}"
                        placeholder="Všechna slova musí pasovat…" autocomplete="off">
                    <button class="btn" type="submit">Použít filtr</button>
                    {zrusit_f}
                </form>
            </div>
            """

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
                min-width: 180px;
                max-width: 360px;
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
        </style>
    </head>
    <body>
        <div class="fixed-chrome" id="fixed-chrome">
            <div class="fixed-chrome-inner">
                {flash_html}
                <div class="top-grid">
                    <div class="title-block">
                        <h1 class="deg-nazev"><span class="deg-title-name">{escape(degustace['nazev'])}</span><span class="deg-title-sep">·</span><span class="deg-title-rezim">{escape(title_rezim_suffix)}</span></h1>
                        <div class="datum">{escape(datum_cz)}</div>
                        <a class="link-back" href="/">← Zpět na degustace</a>
                    </div>
                    <div class="controls-block">
                        <div class="controls-toggles">
                            <div class="mode-wrap" title="Úpravy vs. prohlížení">
                                <form method="post" style="display:inline;">
                                    <input type="hidden" name="action" value="set_edit">
                                    <input type="hidden" name="edit" value="1">
                                    {ph}
                                    <button type="submit" class="{'active' if edit_mode else ''}">Editace</button>
                                </form>
                                <form method="post" style="display:inline;">
                                    <input type="hidden" name="action" value="set_edit">
                                    <input type="hidden" name="edit" value="0">
                                    {ph}
                                    <button type="submit" class="{'active' if not edit_mode else ''}">Zobrazení</button>
                                </form>
                            </div>
                            <div class="mode-wrap" title="Seznam vzorků vs. hodnocení komise">
                                <form method="post" style="display:inline;">
                                    <input type="hidden" name="action" value="set_rezim">
                                    <input type="hidden" name="rezim" value="seznam">
                                    {ph}
                                    <button type="submit" class="{'active' if rezim == 'seznam' else ''}">Seznam vzorků</button>
                                </form>
                                <form method="post" style="display:inline;">
                                    <input type="hidden" name="action" value="set_rezim">
                                    <input type="hidden" name="rezim" value="komise">
                                    {ph}
                                    <button type="submit" class="{'active' if rezim == 'komise' else ''}">Komise</button>
                                </form>
                            </div>
                        </div>
                        <div class="komise-panel-right">
                            {('<div class="controls-row" style="justify-content:flex-end;gap:8px;">' + komise_select_html + tisk_html + '</div>') if rezim == 'komise' else ''}
                        </div>
                        {controls_sub}
                    </div>
                </div>
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

    if rezim == "komise":

        def _fmt_komise_dilci(x):
            if x is None:
                return "—"
            return format_body_hodnota(x)

        html += """
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
                    <th>Barva<br>0–2</th>
                    <th>Čistota<br>0–2</th>
                    <th>Vůně<br>0–4</th>
                    <th>Chuť<br>0–12</th>
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
                    <td class="td-celkem">{celkem_txt}</td>
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
    else:
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
        }})();
        </script>
    </body>
    </html>
    """

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
        if rozdeleni_existuje:
            conn.close()
            return f"""
            <html><head><meta charset="utf-8"><title>Tisk pro komise</title>
            <style>
              body {{ font-family: Arial, sans-serif; max-width: 980px; margin: 30px auto; padding: 0 16px; color:#222; background:#fff; }}
              .box {{ border:1px solid #ddd; border-radius:10px; padding:18px; background:#fff; }}
              .row {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }}
              a.btn {{ display:inline-block; padding:10px 14px; border:1px solid #bbb; border-radius:8px; text-decoration:none; color:#222; background:#f8f8f8; }}
              a.btn:hover {{ background:#f0f0f0; }}
              a.btn-primary {{ background:#3d5c35; color:#fff; border-color:#3d5c35; }}
              a.btn-primary:hover {{ background:#324a2c; }}
              .muted {{ color:#555; font-size:13px; }}
            </style></head><body>
              <div class="box">
                <h2 style="margin:0 0 6px 0;">Tisk pro komise</h2>
                <div class="muted">Degustace: <strong>{escape(degustace["nazev"])}</strong> ({escape(format_datum_cz(degustace["datum"]))})</div>
                <p class="muted" style="margin-top:10px;">Rozdělení vzorků do komisí už existuje. Co chceš udělat?</p>
                <div class="row">
                  <a class="btn btn-primary" href="/tisk/{id}?mode=use" target="_blank">Použít existující rozdělení</a>
                  <a class="btn" href="/tisk/{id}?mode=regen" target="_blank">Přegenerovat rozdělení a tisknout</a>
                </div>
                <p class="muted" style="margin-top:14px;">Pozn.: Přegenerování přepíše přiřazení komisí u všech vzorků.</p>
              </div>
            </body></html>
            """
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
