from flask import (
    Flask,
    request,
    has_request_context,
    redirect,
    flash,
    get_flashed_messages,
    session,
    jsonify,
    url_for,
    send_from_directory,
    abort,
    Response,
)
from markupsafe import escape
from urllib.parse import urlencode, quote, urlparse, urlunparse
from db import get_connection
import csv
import io
import os
import json
import secrets
import time
import hmac


def _public_url_scheme_early():
    return (os.environ.get("PUBLIC_URL_SCHEME") or "https").strip() or "https"


app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "dev-nahradit-pro-produkci")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["PREFERRED_URL_SCHEME"] = _public_url_scheme_early()


@app.before_request
def _apply_legacy_host_redirect():
    return _legacy_host_redirect_response()


@app.route("/assets/degus_logo.png")
def degus_logo():
    base = os.path.dirname(os.path.abspath(__file__))
    for sub in ("static", "assets"):
        path = os.path.join(base, sub, "degus_logo.png")
        if os.path.isfile(path):
            return send_from_directory(
                os.path.join(base, sub),
                "degus_logo.png",
                mimetype="image/png",
            )
    abort(404)


SESSION_EDIT_PREFIX = "edit_deg_"
SESSION_REZIM_PREFIX = "rezim_deg_"
SESSION_KOMISE_PREFIX = "komise_deg_"
SESSION_EDIT_ROW_PREFIX = "edit_row_deg_"
SESSION_SETTINGS_TAB_PREFIX = "settings_tab_deg_"
SETTINGS_TAB_IDS = ("deg", "hodn", "kom", "kat", "vys", "odr")
_KOMISE_VELIKOST = 30

TYP_AKCE_BODOVANA = "bodovana"
TYP_AKCE_PRUVODCE = "pruvodce"

URL_HOME = "/"
URL_SCORE = "/score"
URL_GUIDE = "/guide"
URL_GUIDE_APP_PREFIX = "/guide"


def _env_trim(key, default=""):
    return (os.environ.get(key) or default).strip()


def _env_truthy(key, default=False):
    v = _env_trim(key).lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    return default


def _public_url_scheme():
    return _public_url_scheme_early()


def primary_domain():
    """Hlavní značková doména (např. scoretaste.com). Bez schématu."""
    return _env_trim("PRIMARY_DOMAIN")


def czech_domain():
    """Česká doména (např. scoretaste.cz). Bez schématu."""
    return _env_trim("CZECH_DOMAIN")


def guide_demo_domain():
    """Volitelná demo subdoména / host pro průvodce — pro budoucí redirecty a dokumentaci."""
    return _env_trim("GUIDE_DEMO_DOMAIN")


def score_demo_domain():
    """Volitelná demo subdoména / host pro bodovačku — pro budoucí redirecty a dokumentaci."""
    return _env_trim("SCORE_DEMO_DOMAIN")


def _public_base_url_from_config():
    """
    Kanonický veřejný základ URL (bez koncového lomítka), výhradně z konfigurace.
    Pořadí: PUBLIC_BASE_URL → CZECH_DOMAIN → PRIMARY_DOMAIN.
    """
    explicit = _env_trim("PUBLIC_BASE_URL")
    if explicit:
        return explicit.rstrip("/")
    host = czech_domain() or primary_domain()
    if host:
        return f"{_public_url_scheme()}://{host.split('/')[0]}"
    return ""


def app_public_base_url():
    """
    Základ pro generování absolutních URL této aplikace (QR, odkazy pro vinaře, e-katalog).
    Preferuje PUBLIC_BASE_URL / domény z env; jinak aktuální požadavek (Host).
    """
    cfg = _public_base_url_from_config()
    if cfg:
        return cfg
    if has_request_context():
        return (request.url_root or "").rstrip("/")
    return ""


def absolute_public_url(path):
    """path začíná / (může obsahovat ?query)."""
    base = app_public_base_url()
    if not path.startswith("/"):
        path = "/" + path
    return base + path if base else path


def marketing_site_url():
    """Externí odkaz z loga (veřejný web); pokud není doména v env, relativní úvod /."""
    d = primary_domain() or czech_domain()
    if d:
        host = d.split("/")[0]
        return f"{_public_url_scheme()}://{host}/"
    return URL_HOME


def _legacy_redirect_source_hosts():
    hosts = []
    raw = _env_trim("LEGACY_REDIRECT_HOSTS")
    if raw:
        for part in raw.split(","):
            p = part.strip().lower()
            if p:
                hosts.append(p.split(":")[0])
    if _env_truthy("REDIRECT_DEMO_HOSTS_TO_CANONICAL"):
        for d in (guide_demo_domain(), score_demo_domain()):
            if d:
                hosts.append(d.split(":")[0].lower())
    return frozenset(hosts)


def _legacy_host_redirect_response():
    """
    Volitelný 301 z legacy hostů (např. degus.cz) na kanonický PUBLIC_BASE_URL / CZECH_DOMAIN.
    Zapnuto: ENABLE_LEGACY_HOST_REDIRECT=1 a neprázdný cíl z _public_base_url_from_config().
    """
    if not _env_truthy("ENABLE_LEGACY_HOST_REDIRECT"):
        return None
    target_base = _public_base_url_from_config()
    if not target_base:
        return None
    allowed = _legacy_redirect_source_hosts()
    if not allowed:
        return None
    host = (request.host or "").split(":")[0].lower()
    if host not in allowed:
        return None
    parts = urlparse(request.url)
    t = urlparse(target_base + "/")
    new_url = urlunparse(
        (t.scheme, t.netloc, parts.path or "/", parts.params, parts.query, parts.fragment)
    )
    return redirect(new_url, code=301)


# Možné zápisy v DB pro stejný význam (canonical je TYP_AKCE_PRUVODCE)
_TYP_AKCE_PRUVODCE_DB_VALUES = (TYP_AKCE_PRUVODCE, "průvodce")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SCORETASTE_DIST_DIR = os.path.join(_BASE_DIR, "scoretaste", "dist")
SCORETASTE_ASSETS_DIR = os.path.join(SCORETASTE_DIST_DIR, "assets")
SCORETASTE_EVENTS_DIR = os.path.join(
    SCORETASTE_DIST_DIR, "guide", "data", "events"
)
SCORETASTE_PUBLIC_EVENTS_DIR = os.path.join(
    _BASE_DIR, "scoretaste", "public", "guide", "data", "events"
)


def _scoretaste_deep_link(event_id):
    return f"{URL_GUIDE_APP_PREFIX}/e/{event_id}"


def _scoretaste_index():
    index_path = os.path.join(SCORETASTE_DIST_DIR, "index.html")
    if not os.path.isfile(index_path):
        abort(404)
    return send_from_directory(SCORETASTE_DIST_DIR, "index.html")


def _write_scoretaste_event_skeleton_if_absent(new_id, nazev, datum):
    """Katalog je v DB; žádný zápis JSON při založení akce."""
    return


def _delete_scoretaste_event_json_files(deg_id):
    """Smaže řádky ScoreTaste katalogu v DB a volitelně staré JSON soubory."""
    eid = int(deg_id)
    conn = get_connection()
    try:
        conn.execute("DELETE FROM scoretaste_wineries WHERE event_id = ?", (eid,))
        conn.commit()
    finally:
        conn.close()
    fn = f"{eid}.json"
    for base in (SCORETASTE_EVENTS_DIR, SCORETASTE_PUBLIC_EVENTS_DIR):
        p = os.path.join(base, fn)
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def _scoretaste_event_catalog_public_path(event_id):
    return os.path.join(
        SCORETASTE_PUBLIC_EVENTS_DIR, f"{int(event_id)}.json"
    )


def _scoretaste_event_catalog_dist_path(event_id):
    return os.path.join(
        SCORETASTE_EVENTS_DIR, f"{int(event_id)}.json"
    )


def _load_scoretaste_event_catalog_json_file(event_id):
    """Best-effort načtení starého JSON (migrace); při chybě souboru vrátí None."""
    path = _scoretaste_event_catalog_public_path(event_id)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    path2 = _scoretaste_event_catalog_dist_path(event_id)
    if os.path.isfile(path2):
        with open(path2, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


_SCORETASTE_WINE_COLORS = frozenset(("white", "red", "rose", "orange"))


def _norm_scoretaste_wine_color(raw):
    c = (raw or "").strip().lower()
    return c if c in _SCORETASTE_WINE_COLORS else "white"


def _contrib_variety_from_label(variety, label):
    v = (variety or "").strip()
    if v:
        return v
    return (label or "").strip()


def _contributor_form_row_indices(form):
    indices = set()
    for k in form.keys():
        if k.startswith("row_") and k.endswith("_label"):
            try:
                indices.add(int(k[4:-6]))
            except ValueError:
                pass
    return sorted(indices)


def _contrib_wine_color_options_html(selected):
    opts = []
    for val, lab in (
        ("white", "Bílé"),
        ("rose", "Růžové"),
        ("red", "Červené"),
        ("orange", "Oranžové"),
    ):
        opts.append(
            f'<option value="{val}"{" selected" if selected == val else ""}>{lab}</option>'
        )
    return "".join(opts)


def _contrib_wine_row_html(wi, wine_id, label, vintage, wcol, variety, predicate, description):
    """Jeden řádek vína; wi je index (např. „0“) nebo „ROWIDX“ v šabloně pro JS."""
    hid = escape(wine_id) if wine_id else ""
    return (
        f'<div class="c-wine-row">'
        f'<input type="hidden" name="row_{wi}_wine_id" value="{hid}">'
        '<div class="c-wine-line1">'
        f'<input type="text" class="c-inp c-inp-label" name="row_{wi}_label" '
        f'value="{escape(label)}" placeholder="Název vína" autocomplete="off">'
        f'<select class="c-inp c-sel" name="row_{wi}_color" aria-label="Barva">'
        f"{_contrib_wine_color_options_html(wcol)}"
        "</select>"
        f'<input type="text" class="c-inp c-inp-vint" name="row_{wi}_vintage" '
        f'value="{escape(vintage)}" placeholder="Ročník" autocomplete="off" inputmode="numeric">'
        '<button type="button" class="c-del" aria-label="Odebrat řádek">🗑️</button>'
        "</div>"
        f'<details class="c-more"><summary>Více</summary>'
        f'<input type="text" class="c-inp" name="row_{wi}_variety" value="{escape(variety)}" '
        f'placeholder="Odrůda" autocomplete="off">'
        f'<input type="text" class="c-inp" name="row_{wi}_predicate" value="{escape(predicate)}" '
        f'placeholder="Přívlastek" autocomplete="off">'
        f'<textarea class="c-ta" name="row_{wi}_description" placeholder="Popis" rows="2">'
        f"{escape(description)}</textarea>"
        "</details></div>"
    )


def _normalize_event_catalog(catalog, event_id_str):
    """Zajistí `event`, `wineries`, `wines` a stringové `event.id`."""
    if not isinstance(catalog, dict):
        catalog = {}
    ev = catalog.get("event")
    if not isinstance(ev, dict):
        ev = {}
    ev["id"] = str(event_id_str)
    ev.setdefault("name", "")
    ev.setdefault("date", "")
    catalog["event"] = ev
    w = catalog.get("wineries")
    catalog["wineries"] = w if isinstance(w, list) else []
    w2 = catalog.get("wines")
    catalog["wines"] = w2 if isinstance(w2, list) else []
    return catalog


def _scoretaste_catalog_from_db(conn, event_id):
    """Sestaví katalog ve tvaru JSON pro frontend; None pokud akce neexistuje nebo není průvodce."""
    eid = int(event_id)
    deg = conn.execute("SELECT * FROM degustace WHERE id = ?", (eid,)).fetchone()
    if not deg or _deg_row_typ_akce(deg) != TYP_AKCE_PRUVODCE:
        return None
    sid = str(eid)
    event = {
        "id": sid,
        "name": (deg["nazev"] or "").strip(),
        "date": (deg["datum"] or "").strip(),
    }
    wineries_rows = conn.execute(
        """
        SELECT id, name, location_number, token, note, web
        FROM scoretaste_wineries
        WHERE event_id = ?
        ORDER BY location_number IS NULL, location_number COLLATE NOCASE
        """,
        (eid,),
    ).fetchall()
    wineries = []
    for r in wineries_rows:
        ln = r["location_number"]
        item = {
            "id": str(r["id"]),
            "eventId": sid,
            "name": r["name"],
            "locationNumber": (ln if ln is not None else ""),
        }
        tok = (r["token"] or "").strip()
        if tok:
            item["token"] = tok
        note = (r["note"] or "").strip() if r["note"] is not None else ""
        if note:
            item["note"] = note
        web = (r["web"] or "").strip() if r["web"] is not None else ""
        if web:
            item["web"] = web
        wineries.append(item)
    wines_rows = conn.execute(
        """
        SELECT w.id, w.winery_id, w.label, w.variety, w.predicate, w.vintage, w.description, w.color
        FROM scoretaste_wines w
        JOIN scoretaste_wineries y ON w.winery_id = y.id
        WHERE y.event_id = ?
        ORDER BY w.id
        """,
        (eid,),
    ).fetchall()
    wines = []
    for r in wines_rows:
        w = {
            "id": str(r["id"]),
            "wineryId": str(r["winery_id"]),
            "label": r["label"],
            "variety": r["variety"],
            "predicate": (r["predicate"] or "").strip(),
            "vintage": r["vintage"],
            "color": _norm_scoretaste_wine_color(r["color"]),
        }
        desc = r["description"]
        if desc is not None and str(desc).strip():
            w["description"] = str(desc).strip()
        wines.append(w)
    return {"event": event, "wineries": wineries, "wines": wines}


def _scoretaste_import_json_catalog_to_db(conn, event_id, catalog_dict):
    """Jednorázový import starého JSON do DB; mapuje stará winery id na nová PK."""
    eid = int(event_id)
    normalized = _normalize_event_catalog(catalog_dict, str(eid))
    id_map = {}
    for wy in normalized["wineries"]:
        old_id = str(wy.get("id") or "").strip()
        name = (wy.get("name") or "").strip()
        loc = (wy.get("locationNumber") or "").strip()
        if not name:
            continue
        loc_val = loc or None
        token = (wy.get("token") or "").strip() or _new_contributor_token()
        note = (wy.get("note") or "").strip() or None
        web = (wy.get("web") or "").strip() or None
        try:
            cur = conn.execute(
                """
                INSERT INTO scoretaste_wineries (event_id, name, location_number, token, note, web)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (eid, name, loc_val, token, note, web),
            )
        except Exception:
            continue
        new_row_id = cur.lastrowid
        if old_id:
            id_map[old_id] = new_row_id
        id_map[str(new_row_id)] = new_row_id
    for win in normalized["wines"]:
        old_wy = str(win.get("wineryId") or "").strip()
        wid = id_map.get(old_wy)
        if wid is None:
            continue
        label = (win.get("label") or "").strip()
        variety = (win.get("variety") or "").strip()
        predicate = (win.get("predicate") or "").strip()
        vintage = (win.get("vintage") or "").strip()
        desc = (win.get("description") or "").strip()
        color = _norm_scoretaste_wine_color(win.get("color"))
        if not label or not variety or not vintage:
            continue
        conn.execute(
            """
            INSERT INTO scoretaste_wines (winery_id, label, variety, predicate, vintage, description, color)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (wid, label, variety, predicate, vintage, desc or None, color),
        )


def _maybe_migrate_json_to_db(conn, event_id):
    """Pokud DB pro event nemá vinařství, zkus import ze starého JSON souboru."""
    eid = int(event_id)
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM scoretaste_wineries WHERE event_id = ?",
        (eid,),
    ).fetchone()["c"]
    if n > 0:
        return
    raw = _load_scoretaste_event_catalog_json_file(event_id)
    if not raw:
        return
    _scoretaste_import_json_catalog_to_db(conn, event_id, raw)


def _scoretaste_ensure_tokens_in_db(conn, event_id):
    """Doplní chybějící contributor token u vinařství."""
    eid = int(event_id)
    rows = conn.execute(
        """
        SELECT id FROM scoretaste_wineries
        WHERE event_id = ? AND (token IS NULL OR TRIM(token) = '')
        """,
        (eid,),
    ).fetchall()
    for r in rows:
        tok = _new_contributor_token()
        conn.execute(
            "UPDATE scoretaste_wineries SET token = ? WHERE id = ?",
            (tok, r["id"]),
        )


def _ensure_scoretaste_catalog_for_deg_row(event_id, deg_row):
    """
    Načte katalog z DB; případně jednorázově importuje starý JSON.
    Vrací normalizovaný dict pro admin/contributor HTML.
    """
    sid = str(int(event_id))
    conn = get_connection()
    try:
        _maybe_migrate_json_to_db(conn, event_id)
        _scoretaste_ensure_tokens_in_db(conn, event_id)
        catalog = _scoretaste_catalog_from_db(conn, event_id)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    if catalog is None:
        nazev = (deg_row["nazev"] or "").strip()
        datum = (deg_row["datum"] or "").strip()
        catalog = {
            "event": {"id": sid, "name": nazev, "date": datum},
            "wineries": [],
            "wines": [],
        }
    return _normalize_event_catalog(catalog, sid)


def _winery_location_number_taken(catalog, location_number, exclude_winery_id=None):
    """True, pokud jiné vinařství v katalogu už má stejné číslo sklepu (po strip)."""
    loc = (location_number or "").strip()
    if not loc:
        return False
    ex = None if exclude_winery_id is None else str(exclude_winery_id).strip()
    for w in catalog.get("wineries") or []:
        wid = str(w.get("id") or "").strip()
        if ex is not None and wid == ex:
            continue
        if str(w.get("locationNumber") or "").strip() == loc:
            return True
    return False


def _new_contributor_token():
    return secrets.token_urlsafe(24)


def _scoretaste_winery_location_taken_db(
    conn, event_id, location_number, exclude_winery_id=None
):
    loc = (location_number or "").strip()
    if not loc:
        return False
    eid = int(event_id)
    if exclude_winery_id is not None:
        row = conn.execute(
            """
            SELECT id FROM scoretaste_wineries
            WHERE event_id = ? AND TRIM(location_number) = TRIM(?) AND id != ?
            """,
            (eid, loc, int(exclude_winery_id)),
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT id FROM scoretaste_wineries
            WHERE event_id = ? AND TRIM(location_number) = TRIM(?)
            """,
            (eid, loc),
        ).fetchone()
    return row is not None


_LEN_MISTO = 200


def _norm_typ_akce_uloz(raw):
    """Povolené hodnoty typu akce při zápisu do DB."""
    t = (raw or "").strip().lower()
    if t == TYP_AKCE_PRUVODCE or t == "průvodce":
        return TYP_AKCE_PRUVODCE
    return TYP_AKCE_BODOVANA


def _typ_akce_insert_from_request_path():
    """POST z `/guide` resp. `/score` — typ podle URL (nezávislé na volané view funkci)."""
    p = (request.path or "").rstrip("/") or "/"
    if p == URL_GUIDE:
        return TYP_AKCE_PRUVODCE
    if p == URL_SCORE:
        return TYP_AKCE_BODOVANA
    return None


def _deg_row_typ_akce(deg_row):
    """Typ akce z řádku degustace (výchozí bodovaná)."""
    if deg_row is None:
        return TYP_AKCE_BODOVANA
    try:
        return _norm_typ_akce_uloz(deg_row["typ_akce"])
    except (KeyError, TypeError):
        return TYP_AKCE_BODOVANA


def _deg_misto_text(deg_row):
    try:
        return (deg_row["misto"] or "").strip()
    except (KeyError, TypeError, IndexError):
        return ""


def _misto_uloz(raw):
    return _limit_str(raw, _LEN_MISTO)


# Limity délky textů (aplikace/UI)
_LEN_WEB = 500
_LEN_POZN_VZOREK = 500
_LEN_WEB_VYST = 500


def _limit_str(raw, max_len):
    """Ořezání textu po .strip(); prázdný vstup -> None."""
    t = (raw or "").strip()
    if not t:
        return None
    return t[:max_len] if max_len is not None else t


_KOMISE_EXTRA_COLS = (
    ("body_barva", "REAL"),
    ("body_cistota", "REAL"),
    ("body_vune", "REAL"),
    ("body_chut", "REAL"),
    ("poznamka_komise", "TEXT"),
)

SORTABLE = ("cislo", "nazev", "adresa", "odruda", "privlastek", "rocnik", "body")
DEFAULT_SORT = "body"
DEFAULT_DIR = "desc"

VZORKY_SELECT_JOIN = """
SELECT v.*, o.odruda_short AS odruda_join_short, o.odruda_long AS odruda_join_long
FROM vzorky v
LEFT JOIN odrudy o ON v.odruda_id = o.id
WHERE v.degustace_id = ?
ORDER BY v.cislo
"""


def _migrate_scoretaste_wineries_location_nullable(conn):
    """Starší DB měly location_number NOT NULL; uvolníme NULL pro nepřiřazené číslo sklepu.

    Nelze spoléhat na RENAME původní tabulky + DROP: SQLite ponechá FK v ``scoretaste_wines``
    odkazující na přejmenovanou tabulku (např. ``scoretaste_wineries_loc_mig_old``), takže po DROP
    vznikne rozbité schéma a INSERT do ``scoretaste_wines`` selže.
    """
    try:
        rows = conn.execute("PRAGMA table_info(scoretaste_wineries)").fetchall()
    except Exception:
        return
    if not rows:
        return
    loc_notnull = None
    for row in rows:
        if row[1] == "location_number":
            loc_notnull = row[3]
            break
    if loc_notnull != 1:
        return
    old_cols = {r[1] for r in rows}

    has_vwf = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='scoretaste_visitor_wine_flag'"
    ).fetchone()

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("CREATE TEMP TABLE _st_mig_w AS SELECT * FROM scoretaste_wines")
    conn.execute("CREATE TEMP TABLE _st_mig_y AS SELECT * FROM scoretaste_wineries")
    if has_vwf:
        conn.execute("CREATE TEMP TABLE _st_mig_vwf AS SELECT * FROM scoretaste_visitor_wine_flag")

    conn.execute("DROP TABLE IF EXISTS scoretaste_visitor_wine_flag")
    conn.execute("DROP TABLE scoretaste_wines")
    conn.execute("DROP TABLE scoretaste_wineries")

    conn.execute(
        """
        CREATE TABLE scoretaste_wineries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            location_number TEXT,
            token TEXT,
            note TEXT,
            web TEXT,
            email TEXT,
            UNIQUE (event_id, location_number),
            FOREIGN KEY (event_id) REFERENCES degustace(id) ON DELETE CASCADE
        )
        """
    )
    if "email" in old_cols:
        conn.execute(
            """
            INSERT INTO scoretaste_wineries (id, event_id, name, location_number, token, note, web, email)
            SELECT id, event_id, name, NULLIF(TRIM(location_number), ''), token, note, web, email
            FROM _st_mig_y
            """
        )
    else:
        conn.execute(
            """
            INSERT INTO scoretaste_wineries (id, event_id, name, location_number, token, note, web, email)
            SELECT id, event_id, name, NULLIF(TRIM(location_number), ''), token, note, web, NULL
            FROM _st_mig_y
            """
        )

    conn.execute(
        """
        CREATE TABLE scoretaste_wines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            winery_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            variety TEXT NOT NULL,
            predicate TEXT,
            vintage TEXT NOT NULL,
            description TEXT,
            color TEXT NOT NULL DEFAULT 'white',
            FOREIGN KEY (winery_id) REFERENCES scoretaste_wineries(id) ON DELETE CASCADE
        )
        """
    )
    conn.execute("INSERT INTO scoretaste_wines SELECT * FROM _st_mig_w")

    if has_vwf:
        conn.execute(
            """
            CREATE TABLE scoretaste_visitor_wine_flag (
                event_id INTEGER NOT NULL,
                wine_id INTEGER NOT NULL,
                session_key TEXT NOT NULL,
                liked INTEGER NOT NULL DEFAULT 0,
                want_to_buy INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT,
                PRIMARY KEY (event_id, wine_id, session_key),
                FOREIGN KEY (wine_id) REFERENCES scoretaste_wines(id) ON DELETE CASCADE,
                FOREIGN KEY (event_id) REFERENCES degustace(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("INSERT INTO scoretaste_visitor_wine_flag SELECT * FROM _st_mig_vwf")

    conn.execute("DROP TABLE IF EXISTS _st_mig_w")
    conn.execute("DROP TABLE IF EXISTS _st_mig_y")
    conn.execute("DROP TABLE IF EXISTS _st_mig_vwf")

    conn.execute("PRAGMA foreign_keys=ON")


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
            poznamka_komise TEXT,
            web TEXT,
            poznamka_vzorek TEXT,
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
    if "odruda_zobrazeni" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN odruda_zobrazeni TEXT")
    if "odruda_zob_katalog" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN odruda_zob_katalog TEXT")
    if "odruda_zob_tisk" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN odruda_zob_tisk TEXT")
    if "odruda_zob_ekatalog" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN odruda_zob_ekatalog TEXT")
    if "typ_akce" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN typ_akce TEXT DEFAULT 'bodovana'")
        conn.execute(
            "UPDATE degustace SET typ_akce = 'bodovana' WHERE typ_akce IS NULL OR TRIM(COALESCE(typ_akce,'')) = ''"
        )
    if "misto" not in exist_deg:
        conn.execute("ALTER TABLE degustace ADD COLUMN misto TEXT")
    conn.execute(
        """
        UPDATE degustace SET
            odruda_zob_katalog = COALESCE(odruda_zob_katalog, odruda_zobrazeni, 'short'),
            odruda_zob_tisk = COALESCE(odruda_zob_tisk, odruda_zobrazeni, 'short'),
            odruda_zob_ekatalog = COALESCE(odruda_zob_ekatalog, odruda_zobrazeni, 'short')
        WHERE odruda_zob_katalog IS NULL OR odruda_zob_tisk IS NULL OR odruda_zob_ekatalog IS NULL
        """
    )

    cur = conn.execute("PRAGMA table_info(vzorky)")
    exist = {row[1] for row in cur.fetchall()}
    if "poznamka_komise" not in exist and "poznamka" in exist:
        try:
            conn.execute("ALTER TABLE vzorky RENAME COLUMN poznamka TO poznamka_komise")
        except Exception:
            pass
        exist = {row[1] for row in conn.execute("PRAGMA table_info(vzorky)").fetchall()}
    for col, typ in _KOMISE_EXTRA_COLS:
        if col not in exist:
            conn.execute(f"ALTER TABLE vzorky ADD COLUMN {col} {typ}")
            exist.add(col)
    if "web" not in exist:
        conn.execute("ALTER TABLE vzorky ADD COLUMN web TEXT")
        exist.add("web")
    if "poznamka_vzorek" not in exist:
        conn.execute("ALTER TABLE vzorky ADD COLUMN poznamka_vzorek TEXT")
        exist.add("poznamka_vzorek")
    if "komise_cislo" not in exist:
        conn.execute("ALTER TABLE vzorky ADD COLUMN komise_cislo INTEGER")
    if "odruda_id" not in exist:
        conn.execute("ALTER TABLE vzorky ADD COLUMN odruda_id INTEGER")
        exist.add("odruda_id")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS komise_porotci (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            degustace_id INTEGER NOT NULL,
            komise_cislo INTEGER NOT NULL,
            jmena TEXT,
            UNIQUE (degustace_id, komise_cislo)
        )
    """)
    kp_cols = {row[1] for row in conn.execute("PRAGMA table_info(komise_porotci)").fetchall()}
    if "hlavni_komisar" not in kp_cols:
        conn.execute("ALTER TABLE komise_porotci ADD COLUMN hlavni_komisar TEXT")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS vystavovatele (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nazev TEXT NOT NULL,
            adresa TEXT,
            web TEXT,
            mobil TEXT,
            mail TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS odrudy (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odruda_short TEXT NOT NULL,
            odruda_long TEXT
        )
    """)
    conn.execute(
        """
        UPDATE odrudy SET odruda_short = UPPER(TRIM(odruda_short))
        WHERE odruda_short IS NOT NULL AND odruda_short != UPPER(TRIM(odruda_short))
        """
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS scoretaste_wineries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            location_number TEXT,
            token TEXT,
            note TEXT,
            web TEXT,
            UNIQUE (event_id, location_number),
            FOREIGN KEY (event_id) REFERENCES degustace(id) ON DELETE CASCADE
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scoretaste_wines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            winery_id INTEGER NOT NULL,
            label TEXT NOT NULL,
            variety TEXT NOT NULL,
            predicate TEXT,
            vintage TEXT NOT NULL,
            description TEXT,
            color TEXT NOT NULL DEFAULT 'white',
            FOREIGN KEY (winery_id) REFERENCES scoretaste_wineries(id) ON DELETE CASCADE
        )
    """)

    st_w_cols = {row[1] for row in conn.execute("PRAGMA table_info(scoretaste_wineries)").fetchall()}
    if "note" not in st_w_cols:
        conn.execute("ALTER TABLE scoretaste_wineries ADD COLUMN note TEXT")
    if "web" not in st_w_cols:
        conn.execute("ALTER TABLE scoretaste_wineries ADD COLUMN web TEXT")
    st_wo_cols = {row[1] for row in conn.execute("PRAGMA table_info(scoretaste_wines)").fetchall()}
    if "color" not in st_wo_cols:
        conn.execute(
            "ALTER TABLE scoretaste_wines ADD COLUMN color TEXT NOT NULL DEFAULT 'white'"
        )
    if "email" not in st_w_cols:
        conn.execute("ALTER TABLE scoretaste_wineries ADD COLUMN email TEXT")

    _migrate_scoretaste_wineries_location_nullable(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS scoretaste_visitor_wine_flag (
            event_id INTEGER NOT NULL,
            wine_id INTEGER NOT NULL,
            session_key TEXT NOT NULL,
            liked INTEGER NOT NULL DEFAULT 0,
            want_to_buy INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT,
            PRIMARY KEY (event_id, wine_id, session_key),
            FOREIGN KEY (wine_id) REFERENCES scoretaste_wines(id) ON DELETE CASCADE,
            FOREIGN KEY (event_id) REFERENCES degustace(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


def format_body_hodnota(hodnota):
    if hodnota is None:
        return ""
    return f"{float(hodnota):.1f}".replace(".", ",")


def _fmt_web_link_html(w_raw):
    w = (w_raw or "").strip()
    if not w:
        return "—"
    href = w if w.lower().startswith(("http://", "https://")) else "https://" + w.lstrip("/")
    return (
        f'<a href="{escape(href)}" target="_blank" rel="noopener noreferrer">{escape(w)}</a>'
    )


def _norm_oz_mode(raw):
    m = (raw or "short").strip().lower()
    return m if m in ("short", "long") else "short"


def _deg_oz_field(deg_row, col):
    try:
        v = deg_row[col]
    except (KeyError, IndexError, TypeError):
        v = None
    if v is None:
        try:
            v = deg_row["odruda_zobrazeni"]
        except (KeyError, IndexError, TypeError):
            v = None
    return _norm_oz_mode(v)


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
        UPDATE vzorky SET body_barva=?, body_cistota=?, body_vune=?, body_chut=?, body=?, poznamka_komise=?
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


def _komise_nezarazene_vzorky_existuji(vzorky):
    return any(v["komise_cislo"] is None for v in vzorky)


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

    vz = conn.execute(VZORKY_SELECT_JOIN, (degustace_id,)).fetchall()
    if not vz:
        return

    vz_sorted = sorted(
        vz,
        key=lambda r: (
            _odruda_sort_key_text(r),
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


def _nacti_hlavni_komisar_map(conn, degustace_id):
    rows = conn.execute(
        "SELECT komise_cislo, hlavni_komisar FROM komise_porotci WHERE degustace_id=?",
        (degustace_id,),
    ).fetchall()
    return {int(r["komise_cislo"]): (r["hlavni_komisar"] or "").strip() for r in rows}


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


def _vzorek_hodnoceni_payload(v, deg=None):
    def g(k):
        if v[k] is None:
            return None
        return float(v[k])

    oz_mode = _deg_oz_field(deg, "odruda_zob_katalog") if deg is not None else None
    odr_txt = _odruda_display(v, oz_mode)

    return {
        "id": int(v["id"]),
        "cislo": v["cislo"],
        "odruda": odr_txt,
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


def _vystavovatele_import_z_textu(conn, text_v):
    """Import vystavovatelů z CSV textu; stejná logika jako vystavovatel_import_csv. Vrací (n_ins, n_up)."""
    text_v = (text_v or "").lstrip("\ufeff")
    if not text_v.strip():
        return 0, 0
    fio = io.StringIO(text_v)
    first_v = fio.readline()
    fio.seek(0)
    delim_v = _detect_delimiter(first_v)
    reader_v = csv.reader(fio, delimiter=delim_v)
    rows_v = list(reader_v)
    if not rows_v:
        return 0, 0
    h0 = [(c or "").strip().casefold() for c in rows_v[0]]
    start_i = 1 if (h0 and h0[0] in ("nazev", "název", "jméno", "jmeno", "vystavovatel")) else 0
    exist_rows = conn.execute(
        "SELECT id, nazev FROM vystavovatele",
    ).fetchall()
    by_key = {}
    for er in exist_rows:
        k = (er["nazev"] or "").strip().casefold()
        if k:
            by_key[k] = int(er["id"])
    n_ins = 0
    n_up = 0
    for rv in rows_v[start_i:]:
        if not rv:
            continue
        nz = (rv[0] if len(rv) > 0 else "").strip()
        if not nz:
            continue
        ad = (rv[1] if len(rv) > 1 else "").strip() or None
        wb = _limit_str(rv[2] if len(rv) > 2 else None, _LEN_WEB_VYST)
        mb = (rv[3] if len(rv) > 3 else "").strip() or None
        em = (rv[4] if len(rv) > 4 else "").strip() or None
        k = nz.casefold()
        if k in by_key:
            conn.execute(
                """
                UPDATE vystavovatele
                SET nazev = ?, adresa = ?, web = ?, mobil = ?, mail = ?
                WHERE id = ?
                """,
                (nz, ad, wb, mb, em, by_key[k]),
            )
            n_up += 1
        else:
            conn.execute(
                "INSERT INTO vystavovatele (nazev, adresa, web, mobil, mail) VALUES (?, ?, ?, ?, ?)",
                (nz, ad, wb, mb, em),
            )
            by_key[k] = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            n_ins += 1
    return n_ins, n_up


def _odrudy_import_z_textu(conn, text_o):
    """Import odrůd z CSV textu; stejná logika jako odruda_import_csv. Vrací (n_ins, n_up)."""
    text_o = (text_o or "").lstrip("\ufeff")
    if not text_o.strip():
        return 0, 0
    fio_o = io.StringIO(text_o)
    first_o = fio_o.readline()
    fio_o.seek(0)
    delim_o = _detect_delimiter(first_o)
    reader_o = csv.reader(fio_o, delimiter=delim_o)
    rows_o = list(reader_o)
    if not rows_o:
        return 0, 0
    h0o = [(c or "").strip().casefold() for c in rows_o[0]]
    start_io = 1 if (
        h0o
        and h0o[0]
        in (
            "odruda_short",
            "krátký",
            "short",
            "odrůda",
            "odruda",
        )
    ) else 0
    n_ins = 0
    n_up = 0
    for ro in rows_o[start_io:]:
        if not ro:
            continue
        sh = (ro[0] if len(ro) > 0 else "").strip().upper()
        if not sh:
            continue
        lg = (ro[1] if len(ro) > 1 else "").strip() or None
        ex = conn.execute(
            "SELECT id FROM odrudy WHERE odruda_short = ?",
            (sh,),
        ).fetchone()
        if ex:
            conn.execute(
                "UPDATE odrudy SET odruda_long = ? WHERE id = ?",
                (lg, int(ex["id"])),
            )
            n_up += 1
        else:
            conn.execute(
                "INSERT INTO odrudy (odruda_short, odruda_long) VALUES (?, ?)",
                (sh, lg),
            )
            n_ins += 1
    return n_ins, n_up


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


def _sqlite_row_has(row, key):
    try:
        return key in row.keys()
    except Exception:
        return False


def _odruda_display(v, odruda_zobrazeni=None):
    mode = (odruda_zobrazeni or "short").strip().lower()
    if mode not in ("short", "long"):
        mode = "short"
    oid = v["odruda_id"]
    if oid is not None:
        short = ""
        long_ = ""
        if _sqlite_row_has(v, "odruda_join_short") and v["odruda_join_short"] is not None:
            short = str(v["odruda_join_short"]).strip()
        if _sqlite_row_has(v, "odruda_join_long") and v["odruda_join_long"] is not None:
            long_ = str(v["odruda_join_long"]).strip()
        legacy = (v["odruda"] or "").strip()
        if not short and not long_:
            return legacy
        if mode == "long":
            return long_ or short or legacy
        return short.upper() if short else legacy
    return (v["odruda"] or "").strip()


def _odruda_sort_key_text(v):
    if v["odruda_id"] is not None and _sqlite_row_has(v, "odruda_join_short"):
        s = (v["odruda_join_short"] or "").strip().upper()
        if s:
            return s.casefold()
    return (v["odruda"] or "").strip().casefold()


def _row_text_blob(v):
    b = v["body"]
    body_raw = "" if b is None else str(b)
    body_cz = format_body_hodnota(b)
    ds = _odruda_display(v, "short")
    dl = _odruda_display(v, "long")
    parts = [
        str(v["cislo"]),
        v["nazev"] or "",
        v["adresa"] or "",
        v["odruda"] or "",
        ds,
        dl,
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

    if col == "odruda":
        def key_odr(v):
            return (_odruda_sort_key_text(v), v["cislo"])

        return sorted(vzorky, key=key_odr, reverse=reverse)

    def key_text(v):
        return ((v[col] or "").casefold(), v["cislo"])

    return sorted(vzorky, key=key_text, reverse=reverse)


def _preserve_hidden(sort, dir_, q):
    h = f'<input type="hidden" name="sort" value="{escape(sort)}">'
    h += f'<input type="hidden" name="dir" value="{escape(dir_)}">'
    if q:
        h += f'<input type="hidden" name="q" value="{escape(q)}">'
    return h


def _vystav_polozky_z_formu():
    return (
        (request.form.get("nazev") or "").strip(),
        (request.form.get("adresa") or "").strip() or None,
        _limit_str(request.form.get("web"), _LEN_WEB_VYST),
        (request.form.get("mobil") or "").strip() or None,
        (request.form.get("mail") or "").strip() or None,
    )


def _odruda_z_vzorek_formu(conn):
    raw_oid = (request.form.get("odruda_id") or "").strip()
    custom = (request.form.get("odruda") or "").strip()
    if raw_oid and raw_oid.isdigit():
        oid = int(raw_oid)
        r = conn.execute("SELECT odruda_short FROM odrudy WHERE id = ?", (oid,)).fetchone()
        if r:
            return oid, (r["odruda_short"] or "").strip().upper()
    return None, custom


_HOME_TYP_META = {
    TYP_AKCE_BODOVANA: {
        "page_title": "Správa a vyhodnocení bodovaných degustací",
        "h1": "Správa a vyhodnocení bodovaných degustací",
        "help_inner": """
            <h2>Rychlý průvodce</h2>
            <p><strong>Nová degustace</strong> — založíte akci zadáním názvu a data.</p>
            <p><strong>Seznam degustací</strong> — otevřete existující degustaci kliknutím na řádek a pokračujete ve správě vzorků a hodnocení.</p>
            <p><strong>Doporučený postup:</strong></p>
            <ul>
                <li>Vytvořte degustaci (název a datum).</li>
                <li>Otevřete ji ze seznamu a doplňte vzorky a nastavení.</li>
                <li>Nechte zadat body komisím a zkontrolujte výsledky.</li>
            </ul>
            <p>V dalších částech aplikace bude u nadpisu k dispozici ikona <strong>?</strong> — zobrazí stručnou nápovědu k dané sekci.</p>
        """,
        "empty_msg": "Zatím není založena žádná degustace tohoto typu.",
        "nova_btn": "Nová bodovaná degustace",
    },
    TYP_AKCE_PRUVODCE: {
        "page_title": "Průvodce degustací",
        "h1": "Průvodce degustací",
        "help_inner": """
            <h2>Rychlý průvodce</h2>
            <p><strong>Nová degustace</strong> — založíte akci zadáním názvu a data (otevřené sklepy, veřejná degustace).</p>
            <p><strong>Seznam</strong> — otevřete akci kliknutím na řádek. Samostatná aplikace pro průvodce se postupně doplní.</p>
            <p><strong>Doporučený postup:</strong> vytvořte akci, vyplňte údaje podle pokynů v jednotlivých částech aplikace.</p>
        """,
        "empty_msg": "Zatím není založena žádná akce tohoto typu.",
        "nova_btn": "Nová degustace průvodce",
    },
}


def _html_deg_list_button_inner(d):
    nazev = (d["nazev"] or "").strip() or "Bez názvu"
    misto_t = _deg_misto_text(d)
    misto_disp = misto_t if misto_t else "—"
    datum = (d["datum"] or "").strip()
    return (
        f'<span class="deg-btn-title">{escape(nazev)}</span>'
        f'<span class="deg-btn-meta">{escape(misto_disp)} · {escape(datum)}</span>'
    )


def _html_deg_list_section(
    degustace, post_url, empty_msg, include_pruvodce_delete=False
):
    if not degustace:
        return f"<p>{escape(empty_msg)}</p>"
    parts = ['<div class="degustace-grid">']
    for d in degustace:
        inner = _html_deg_list_button_inner(d)
        vyber = f"""
        <form method="post" action="{escape(post_url)}">
            <input type="hidden" name="action" value="vyber">
            <input type="hidden" name="degustace_id" value="{int(d['id'])}">
            <button class="menu-button menu-button-deg" type="submit">{inner}</button>
        </form>"""
        if include_pruvodce_delete:
            eid = int(d["id"])
            admin_href = url_for("guide_admin_catalog", event_id=eid)
            parts.append(
                f"""
        <div class="deg-row-guide">
            {vyber}
            <a class="guide-admin-cat-link" href="{escape(admin_href)}">Správa akce</a>
            <form method="post" action="{escape(post_url)}" class="deg-del-form">
                <input type="hidden" name="action" value="smazat">
                <input type="hidden" name="degustace_id" value="{eid}">
                <button type="submit" class="btn-del-debug">Smazat</button>
            </form>
        </div>"""
            )
        else:
            parts.append(vyber)
    parts.append("</div>")
    return "".join(parts)


def _html_nova_degustace_dialog(dialog_id, post_url, typ_akce_value, open_button_label, include_typ_hidden):
    th = ""
    if include_typ_hidden:
        th = f'<input type="hidden" name="typ_akce" value="{escape(typ_akce_value)}">'
    return f"""
        <button type="button" class="btn-new-deg" onclick="document.getElementById('{dialog_id}').showModal()">{escape(open_button_label)}</button>
        <dialog id="{dialog_id}" class="dlg-nova-deg">
            <form method="post" action="{escape(post_url)}">
                <input type="hidden" name="action" value="nova_degustace">
                {th}
                <h3 class="dlg-title">Nová degustace</h3>
                <label class="dlg-field"><span>Název degustace</span><input name="nazev" required autocomplete="off"></label>
                <label class="dlg-field"><span>Místo</span><input name="misto" autocomplete="off" placeholder="volitelně"></label>
                <label class="dlg-field"><span>Datum</span><input type="date" name="datum" required></label>
                <div class="dlg-actions">
                    <button type="submit">Založit</button>
                    <button type="button" class="dlg-cancel" onclick="document.getElementById('{dialog_id}').close()">Zrušit</button>
                </div>
            </form>
        </dialog>
        """


def _html_shared_home_css():
    return """
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
            h1, h2 { margin-bottom: 10px; }
            .home-title-row {
                display: flex;
                align-items: center;
                gap: 12px;
                flex-wrap: wrap;
            }
            .home-title-row .app-logo {
                height: 6.4453125rem;
                width: auto;
                max-height: 7.03125rem;
                object-fit: contain;
                flex-shrink: 0;
            }
            .home-title-row span { font-size: 1.5rem; font-weight: bold; }
            .score-sub { margin: 0 0 18px 0; font-size: 1.05rem; color: #444; }
            .deg-block-head {
                display: flex;
                justify-content: space-between;
                align-items: flex-start;
                gap: 12px;
                flex-wrap: wrap;
                margin-bottom: 14px;
            }
            .deg-block-head h2 { margin: 0; font-size: 1.2rem; }
            .btn-new-deg {
                padding: 10px 14px;
                font-size: 14px;
                cursor: pointer;
                white-space: nowrap;
            }
            input {
                padding: 8px 10px;
                margin: 4px 0;
                font-size: 14px;
            }
            button:not(.btn-new-deg):not(.dlg-cancel) {
                padding: 10px 10px;
                margin: 4px 0;
                font-size: 14px;
                cursor: pointer;
            }
            .degustace-grid {
                display: grid;
                grid-template-columns: repeat(3, 1fr);
                gap: 10px 12px;
            }
            @media (max-width: 900px) {
                .degustace-grid { grid-template-columns: repeat(2, 1fr); }
            }
            @media (max-width: 520px) {
                .degustace-grid { grid-template-columns: 1fr; }
            }
            .degustace-grid form { margin: 0; min-width: 0; }
            .deg-row-guide {
                display: flex;
                align-items: stretch;
                gap: 8px;
                min-width: 0;
            }
            .deg-row-guide > form:first-child { flex: 1; min-width: 0; }
            .guide-admin-cat-link {
                align-self: center;
                font-size: 13px;
                white-space: nowrap;
            }
            .btn-del-debug {
                padding: 10px 12px;
                font-size: 13px;
                cursor: pointer;
                white-space: nowrap;
                background: #fee2e2;
                border: 1px solid #fca5a5;
                color: #991b1b;
            }
            .menu-button {
                width: 100%;
                min-width: 0;
                box-sizing: border-box;
                text-align: left;
            }
            .menu-button-deg {
                display: flex;
                flex-direction: column;
                align-items: flex-start;
                gap: 4px;
                line-height: 1.3;
            }
            .deg-btn-title { font-weight: 600; }
            .deg-btn-meta { font-size: 13px; color: #555; }
            .box-help {
                border: 1px solid #cdd2d8;
                border-radius: 8px;
                padding: 16px 18px;
                margin-bottom: 20px;
                background: #e8ebef;
                color: #1f2933;
            }
            .box-help h2 { margin: 0 0 10px 0; font-size: 1.1rem; }
            .box-help p { margin: 0 0 8px 0; font-size: 14px; line-height: 1.45; }
            .box-help ul { margin: 6px 0 10px 1.1em; padding: 0; font-size: 14px; line-height: 1.45; }
            .box-help li { margin-bottom: 4px; }
            .section-help-btn {
                display: inline-flex;
                align-items: center;
                justify-content: center;
                min-width: 1.5rem;
                height: 1.5rem;
                padding: 0 0.35rem;
                margin-left: 6px;
                font-size: 13px;
                font-weight: bold;
                line-height: 1;
                color: #374151;
                background: #e5e7eb;
                border: 1px solid #cbd5e1;
                border-radius: 4px;
                cursor: help;
                vertical-align: middle;
            }
            .section-help-btn:hover {
                background: #dbeafe;
                border-color: #93c5fd;
            }
            .dlg-nova-deg {
                border: 1px solid #c5cad3;
                border-radius: 8px;
                padding: 0;
                max-width: min(420px, 96vw);
            }
            .dlg-nova-deg::backdrop { background: rgba(0,0,0,0.35); }
            .dlg-nova-deg .dlg-title { margin: 0 0 12px 0; font-size: 1.1rem; }
            .dlg-nova-deg .dlg-field {
                display: flex;
                flex-direction: column;
                gap: 4px;
                margin-bottom: 10px;
            }
            .dlg-nova-deg .dlg-field > span:first-child { font-size: 13px; color: #444; }
            .dlg-nova-deg form { padding: 16px 18px; }
            .dlg-actions { display: flex; gap: 10px; flex-wrap: wrap; margin-top: 12px; }
            .dlg-cancel { background: #f3f4f6; border: 1px solid #d1d5db; }
    """


def _html_home_dashboard(deg_bod, deg_pruv, logo_url):
    css = _html_shared_home_css()
    dlg_b = _html_nova_degustace_dialog(
        "dlg-home-bodo",
        URL_HOME,
        TYP_AKCE_BODOVANA,
        "Nová bodovaná degustace",
        True,
    )
    dlg_p = _html_nova_degustace_dialog(
        "dlg-home-pruv",
        URL_HOME,
        TYP_AKCE_PRUVODCE,
        "Nová degustace průvodce",
        True,
    )
    list_b = _html_deg_list_section(
        deg_bod,
        URL_HOME,
        "Zatím žádná bodovaná degustace.",
    )
    list_p = _html_deg_list_section(
        deg_pruv,
        URL_HOME,
        "Zatím žádná akce průvodce.",
    )
    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Score Taste</title>
    <style>{css}</style>
</head>
<body>
    <h1 class="home-title-row">
        <a href="{escape(marketing_site_url())}">
        <img src="{escape(logo_url)}" class="app-logo" alt="Logo degustace vín" width="282" height="113" decoding="async">
        </a>
        <span>Score Taste</span>
    </h1>
    <p class="score-sub">Kompletní správa degustací</p>

    <div class="box deg-block">
        <div class="deg-block-head">
            <h2>Bodované degustace</h2>
            {dlg_b}
        </div>
        {list_b}
    </div>

    <div class="box deg-block">
        <div class="deg-block-head">
            <h2>Průvodce degustací</h2>
            {dlg_p}
        </div>
        {list_p}
    </div>
</body>
</html>"""


def _html_home_typ_page(typ_akce, degustace, logo_url):
    m = _HOME_TYP_META.get(typ_akce) or _HOME_TYP_META[TYP_AKCE_BODOVANA]
    help_inner = m["help_inner"]
    empty_msg = m["empty_msg"]
    page_title = escape(m["page_title"])
    h1 = escape(m["h1"])
    post_url = URL_SCORE if typ_akce == TYP_AKCE_BODOVANA else URL_GUIDE
    dlg_id = "dlg-score" if typ_akce == TYP_AKCE_BODOVANA else "dlg-guide"
    css = _html_shared_home_css()
    dlg = _html_nova_degustace_dialog(
        dlg_id,
        post_url,
        typ_akce,
        m["nova_btn"],
        False,
    )
    list_html = _html_deg_list_section(
        degustace,
        post_url,
        empty_msg,
        include_pruvodce_delete=(typ_akce == TYP_AKCE_PRUVODCE),
    )

    return f"""<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{page_title}</title>
    <style>{css}</style>
</head>
<body>
    <h1 class="home-title-row">
        <a href="{URL_HOME}" title="Úvodní stránka — Score Taste">
        <img src="{escape(logo_url)}" class="app-logo" alt="Logo degustace vín" width="282" height="113" decoding="async">
        </a>
        <span>{h1}</span>
    </h1>

    <div class="box deg-block">
        <div class="deg-block-head">
            <h2>Seznam degustací</h2>
            {dlg}
        </div>
        {list_html}
    </div>

    <div class="box-help" role="region" aria-label="Nápověda k hlavní stránce">
        {help_inner}
    </div>
</body>
</html>"""


def _handle_home_typ(typ_akce_route):
    tn = _norm_typ_akce_uloz(typ_akce_route)
    conn = get_connection()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "smazat":
            if ((request.path or "").rstrip("/") or "/") != URL_GUIDE:
                conn.close()
                abort(400)
            raw_id = request.form.get("degustace_id")
            if not raw_id or not str(raw_id).isdigit():
                conn.close()
                abort(400)
            deg_id = int(raw_id)
            deg_row = conn.execute(
                "SELECT typ_akce FROM degustace WHERE id = ?",
                (deg_id,),
            ).fetchone()
            if not deg_row:
                conn.close()
                abort(404)
            if _deg_row_typ_akce(deg_row) != TYP_AKCE_PRUVODCE:
                conn.close()
                return redirect(URL_GUIDE)
            conn.execute("DELETE FROM degustace WHERE id = ?", (deg_id,))
            conn.commit()
            conn.close()
            _delete_scoretaste_event_json_files(deg_id)
            return redirect(URL_GUIDE)
        if action == "nova_degustace":
            pocet_komisi = 3
            misto = _misto_uloz(request.form.get("misto"))
            insert_typ = _typ_akce_insert_from_request_path() or tn
            cur = conn.execute(
                "INSERT INTO degustace (nazev, datum, pocet_komisi, typ_akce, misto) VALUES (?, ?, ?, ?, ?)",
                (request.form["nazev"], request.form["datum"], pocet_komisi, insert_typ, misto),
            )
            conn.commit()
            new_id = cur.lastrowid
            conn.close()
            if insert_typ == TYP_AKCE_PRUVODCE:
                _write_scoretaste_event_skeleton_if_absent(
                    new_id,
                    (request.form.get("nazev") or "").strip(),
                    (request.form.get("datum") or "").strip(),
                )
                return redirect(_scoretaste_deep_link(str(new_id)))
            return redirect(request.path or URL_SCORE)
        if action == "vyber":
            deg_id = str(request.form["degustace_id"])
            deg_row = conn.execute(
                "SELECT typ_akce FROM degustace WHERE id = ?",
                (deg_id,),
            ).fetchone()
            conn.close()
            if not deg_row:
                abort(404)
            row_typ = _deg_row_typ_akce(deg_row)
            on_guide = ((request.path or "").rstrip("/") or "/") == URL_GUIDE
            if on_guide or tn == TYP_AKCE_PRUVODCE:
                if row_typ != TYP_AKCE_PRUVODCE:
                    return redirect(URL_GUIDE)
                return redirect(_scoretaste_deep_link(deg_id))
            if row_typ != TYP_AKCE_BODOVANA:
                return redirect(URL_SCORE)
            session[SESSION_REZIM_PREFIX + deg_id] = "seznam"
            session[SESSION_EDIT_PREFIX + deg_id] = False
            session[SESSION_KOMISE_PREFIX + deg_id] = 1
            session.modified = True
            return redirect(f"/degustace/{deg_id}")
    if tn == TYP_AKCE_PRUVODCE:
        degustace = conn.execute(
            """
            SELECT * FROM degustace
            WHERE TRIM(COALESCE(typ_akce, '')) IN (?, ?)
            ORDER BY datum DESC, id DESC
            """,
            _TYP_AKCE_PRUVODCE_DB_VALUES,
        ).fetchall()
    else:
        degustace = conn.execute(
            "SELECT * FROM degustace WHERE typ_akce = ? ORDER BY datum DESC, id DESC",
            (tn,),
        ).fetchall()
    conn.close()
    logo_url = url_for("degus_logo")
    return _html_home_typ_page(tn, degustace, logo_url)


def _handle_home_dashboard():
    conn = get_connection()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "nova_degustace":
            tn = _norm_typ_akce_uloz(request.form.get("typ_akce"))
            pocet_komisi = 3
            misto = _misto_uloz(request.form.get("misto"))
            cur = conn.execute(
                "INSERT INTO degustace (nazev, datum, pocet_komisi, typ_akce, misto) VALUES (?, ?, ?, ?, ?)",
                (request.form["nazev"], request.form["datum"], pocet_komisi, tn, misto),
            )
            conn.commit()
            new_id = cur.lastrowid
            conn.close()
            if tn == TYP_AKCE_PRUVODCE:
                return redirect(_scoretaste_deep_link(str(new_id)))
            return redirect(URL_HOME)
        if action == "vyber":
            deg_id = str(request.form["degustace_id"])
            deg_row = conn.execute(
                "SELECT typ_akce FROM degustace WHERE id = ?",
                (deg_id,),
            ).fetchone()
            conn.close()
            if _deg_row_typ_akce(deg_row) == TYP_AKCE_PRUVODCE:
                return redirect(_scoretaste_deep_link(deg_id))
            session[SESSION_REZIM_PREFIX + deg_id] = "seznam"
            session[SESSION_EDIT_PREFIX + deg_id] = False
            session[SESSION_KOMISE_PREFIX + deg_id] = 1
            session.modified = True
            return redirect(f"/degustace/{deg_id}")
    deg_bod = conn.execute(
        "SELECT * FROM degustace WHERE typ_akce = ? ORDER BY datum DESC, id DESC",
        (TYP_AKCE_BODOVANA,),
    ).fetchall()
    deg_pruv = conn.execute(
        """
        SELECT * FROM degustace
        WHERE TRIM(COALESCE(typ_akce, '')) IN (?, ?)
        ORDER BY datum DESC, id DESC
        """,
        _TYP_AKCE_PRUVODCE_DB_VALUES,
    ).fetchall()
    conn.close()
    logo_url = url_for("degus_logo")
    return _html_home_dashboard(deg_bod, deg_pruv, logo_url)


@app.route("/", methods=["GET", "POST"])
def home_dashboard():
    return _handle_home_dashboard()


@app.route("/score", methods=["GET", "POST"])
def home_score():
    return _handle_home_typ(TYP_AKCE_BODOVANA)


@app.route("/guide", methods=["GET", "POST"])
def home_guide():
    return _handle_home_typ(TYP_AKCE_PRUVODCE)


def _guide_admin_redirect(event_id, winery_id=None, tab=None):
    base = url_for("guide_admin_catalog", event_id=event_id)
    q = {}
    t = (tab or "").strip().lower()
    if t in ("catalog", "stats", "import"):
        q["tab"] = t
    if winery_id is not None:
        try:
            wid = int(winery_id)
            q["winery"] = str(wid)
        except (TypeError, ValueError):
            pass
    if q:
        return redirect(base + "?" + urlencode(q))
    return redirect(base)


def _norm_csv_wine_color_cell(raw):
    s = (raw or "").strip().lower()
    s = s.replace("ě", "e").replace("ř", "r").replace("ů", "u")
    if s in ("bile", "bílé", "white"):
        return "white"
    if s in ("cervene", "červené", "red"):
        return "red"
    if s in ("ruzove", "růžové", "rose"):
        return "rose"
    if s in ("oranzove", "oranžové", "orange"):
        return "orange"
    return _norm_scoretaste_wine_color(raw)


def _find_winery_id_by_name_ci(conn, event_id, name):
    row = conn.execute(
        """
        SELECT id FROM scoretaste_wineries
        WHERE event_id = ? AND LOWER(TRIM(name)) = LOWER(TRIM(?))
        """,
        (int(event_id), name),
    ).fetchone()
    return int(row[0]) if row else None


def _import_csv_delimiter(text):
    """Vrátí ',' nebo '\\t' podle prvního neprázdného řádku (hlavička)."""
    first = next((ln for ln in text.splitlines() if (ln or "").strip()), "")
    if not first:
        return ","
    if "\t" in first and first.count("\t") >= first.count(","):
        return "\t"
    return ","


def _import_scoretaste_csv_rows(conn, event_id, text):
    """Vrátí (počet_importovaných_řádků, chybová_zpráva_nebo_None).

    Podporuje TSV i CSV; název vína: sloupce ``label`` nebo ``Vzorek`` (case-insensitive).
    Prázdná odrůda / ročník → výchozí ``none`` / ``1000``.
    """
    eid = int(event_id)
    if not (text or "").strip():
        return 0, "Soubor je prázdný."
    delim = _import_csv_delimiter(text)
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    if not reader.fieldnames:
        return 0, "Soubor nemá hlavičku."
    fnorm = {((k or "").strip().lower()): k for k in reader.fieldnames}
    if "nazev_vinarstvi" not in fnorm:
        return 0, "Chybí sloupec: nazev_vinarstvi"
    if "label" not in fnorm and "vzorek" not in fnorm:
        return 0, "Chybí sloupec: label nebo Vzorek"
    count = 0
    winery_cache = {}
    for row in reader:
        def col(name):
            k = fnorm.get(name)
            return (row.get(k) if k else None) or ""

        nv = col("nazev_vinarstvi").strip()
        lab = col("label").strip() or col("vzorek").strip()
        odr = col("odruda").strip() or "none"
        roc = col("rocnik").strip() or "1000"
        if not nv or not lab:
            return (
                count,
                "Každý řádek musí mít nazev_vinarstvi a název vína (label nebo Vzorek).",
            )
        key = nv.lower()
        if key not in winery_cache:
            wid = _find_winery_id_by_name_ci(conn, eid, nv)
            if wid is None:
                web = col("web").strip() or None
                em = col("email").strip() or None
                loc_cell = ""
                for lk in (
                    "id_sklep",
                    "cislo_sklepu",
                    "location_number",
                    "locationnumber",
                ):
                    if lk in fnorm:
                        loc_cell = col(lk).strip()
                        break
                loc_val = loc_cell or None
                cur = conn.execute(
                    """
                    INSERT INTO scoretaste_wineries (event_id, name, location_number, token, note, web, email)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (eid, nv, loc_val, _new_contributor_token(), None, web, em),
                )
                wid = cur.lastrowid
            winery_cache[key] = wid
        wid = winery_cache[key]
        pred = col("privlastek").strip()
        poz = col("poznamka").strip()
        desc = poz or None
        color = _norm_csv_wine_color_cell(col("barva"))
        conn.execute(
            """
            INSERT INTO scoretaste_wines (winery_id, label, variety, predicate, vintage, description, color)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (wid, lab, odr, pred, roc, desc, color),
        )
        count += 1
    return count, None


def _admin_stats_active_users(conn, event_id):
    eid = int(event_id)
    row = conn.execute(
        """
        SELECT COUNT(DISTINCT session_key) FROM scoretaste_visitor_wine_flag
        WHERE event_id = ? AND (liked = 1 OR want_to_buy = 1)
        """,
        (eid,),
    ).fetchone()
    return int(row[0] or 0) if row else 0


def _admin_stats_wine_rows(conn, event_id):
    eid = int(event_id)
    rows = conn.execute(
        """
        SELECT w.id, w.label, y.name AS winery_name,
          COALESCE(SUM(CASE WHEN f.liked = 1 THEN 1 ELSE 0 END), 0) AS likes,
          COALESCE(SUM(CASE WHEN f.want_to_buy = 1 THEN 1 ELSE 0 END), 0) AS want_buy
        FROM scoretaste_wines w
        JOIN scoretaste_wineries y ON w.winery_id = y.id
        LEFT JOIN scoretaste_visitor_wine_flag f
          ON f.wine_id = w.id AND f.event_id = y.event_id
        WHERE y.event_id = ?
        GROUP BY w.id
        ORDER BY likes DESC, want_buy DESC, w.label COLLATE NOCASE
        """,
        (eid,),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "label": r["label"],
                "winery_name": r["winery_name"],
                "likes": int(r["likes"] or 0),
                "want_buy": int(r["want_buy"] or 0),
            }
        )
    return out


def _admin_tab_url(admin_base, tab, winery_id=None):
    q = {"tab": tab}
    if tab == "catalog" and winery_id:
        q["winery"] = str(winery_id)
    return admin_base + "?" + urlencode(q)


def _admin_tab_from_form():
    t = (request.form.get("redirect_tab") or "").strip().lower()
    return t if t in ("catalog", "stats", "import") else "catalog"


def _admin_event_readiness(catalog, wines_by_wid):
    wineries = catalog.get("wineries") or []
    n_total = len(wineries)
    n_with_wines = 0
    n_with_loc = 0
    n_ready = 0
    any_missing_loc = False
    for w in wineries:
        wid = str(w.get("id") or "").strip()
        wc = len(wines_by_wid.get(wid, []))
        has_loc = bool(str(w.get("locationNumber") or "").strip())
        if not has_loc:
            any_missing_loc = True
        if wc > 0:
            n_with_wines += 1
        if has_loc:
            n_with_loc += 1
        if wc > 0 and has_loc:
            n_ready += 1
    return {
        "n_total": n_total,
        "n_with_wines": n_with_wines,
        "n_with_loc": n_with_loc,
        "n_ready": n_ready,
        "n_not_ready": n_total - n_ready,
        "any_missing_loc": any_missing_loc,
    }


def _admin_winery_status_badges_html(wy, wine_count):
    has_loc = bool(str(wy.get("locationNumber") or "").strip())
    parts = []
    if not has_loc:
        parts.append(
            '<span class="fill-badge fill-badge-warn">Chybí číslo sklepu</span>'
        )
    if wine_count == 0:
        parts.append('<span class="fill-badge fill-badge-empty">Bez vín</span>')
    if has_loc and wine_count > 0:
        parts.append('<span class="fill-badge fill-badge-done">Připraveno</span>')
    return " ".join(parts)


def _admin_wine_color_options(selected):
    opts = []
    for val, lab in (
        ("white", "Bílé"),
        ("rose", "Růžové"),
        ("red", "Červené"),
        ("orange", "Oranžové"),
    ):
        opts.append(
            f'<option value="{val}"{" selected" if selected == val else ""}>{lab}</option>'
        )
    return "".join(opts)


def _html_guide_admin_page(
    event_id, deg_row, catalog, selected_winery_id=None, active_tab="catalog"
):
    ev = catalog["event"]
    title = escape((ev.get("name") or deg_row["nazev"] or "Akce").strip() or "Akce")
    preview_href = f"{URL_GUIDE_APP_PREFIX}/e/{int(event_id)}/wineries"
    visitor_abs = absolute_public_url(preview_href)
    qr_src = (
        "https://api.qrserver.com/v1/create-qr-code/?size=120x120&data="
        + quote(visitor_abs, safe="")
    )
    guide_h = escape(URL_GUIDE)
    prev_h = escape(preview_href)
    admin_base = url_for("guide_admin_catalog", event_id=event_id)
    flash_html = ""
    for cat, msg in get_flashed_messages(with_categories=True):
        flash_html += f'<p class="admin-flash admin-flash-{escape(cat)}">{escape(msg)}</p>\n'

    wines_by_wid = {}
    for w in catalog["wines"]:
        wid = str(w.get("wineryId") or "").strip()
        wines_by_wid.setdefault(wid, []).append(w)

    readiness = _admin_event_readiness(catalog, wines_by_wid)

    wineries_list = list(catalog.get("wineries") or [])
    wineries_sorted = sorted(
        wineries_list,
        key=lambda x: (
            0 if str(x.get("locationNumber") or "").strip() else 1,
            str(x.get("locationNumber") or "").lower(),
            str(x.get("name") or "").lower(),
        ),
    )
    wid_set = {str(w.get("id") or "").strip() for w in wineries_list}
    sel = (selected_winery_id or "").strip()
    if sel not in wid_set and wineries_sorted:
        sel = str(wineries_sorted[0].get("id") or "").strip()
    elif sel not in wid_set:
        sel = ""

    selected_wy = next(
        (w for w in wineries_sorted if str(w.get("id") or "").strip() == sel),
        None,
    )

    try:
        deg_datum_fallback = str(deg_row["datum"] or "").strip()
    except (KeyError, IndexError, TypeError):
        deg_datum_fallback = ""
    datum_raw = (ev.get("date") or "").strip() or deg_datum_fallback
    event_date_html = (
        f'<p class="admin-header-date">{escape(format_datum_cz(datum_raw))}</p>\n'
        if datum_raw
        else ""
    )

    head = f"""<!DOCTYPE html>
<html lang="cs">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Správa akce — {title}</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ font-family: Segoe UI, Arial, sans-serif; margin: 0; color: #1a1a1a; background: #f3f4f6; }}
        .admin-wrap {{ max-width: 1400px; margin: 0 auto; padding: 16px 20px 32px; }}
        h1 {{ font-size: 1.35rem; margin: 0 0 12px; }}
        .nav {{ margin-bottom: 12px; font-size: 14px; }}
        .nav a {{ color: #1d4ed8; }}
        .admin-header-table {{ width: 100%; border-collapse: collapse; margin-bottom: 12px; table-layout: fixed; }}
        .admin-header-main {{ vertical-align: top; }}
        .admin-header-main .nav {{ margin-bottom: 0; }}
        .admin-header-main h1 {{ margin: 0 0 4px; font-size: 1.35rem; }}
        .admin-header-date {{ margin: 0 0 8px; font-size: 0.95rem; color: #4b5563; }}
        .admin-header-flash {{ vertical-align: top; }}
        .admin-header-qr {{ vertical-align: top; text-align: right; width: 132px; padding-left: 12px; }}
        .admin-header-qr-stack {{ display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }}
        .admin-header-qr img {{ display: inline-block; max-width: 120px; height: auto; vertical-align: top; }}
        .admin-header-host-link {{ font-size: 13px; color: #1d4ed8; text-decoration: none; }}
        .admin-header-host-link:hover {{ text-decoration: underline; }}
        .box {{ border: 1px solid #d1d5db; border-radius: 8px; padding: 12px 14px; margin-bottom: 14px; background: #fff; }}
        .box-tight h2 {{ margin: 0 0 10px; font-size: 1.05rem; }}
        .add-winery-row {{ display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: flex-end; }}
        .add-winery-row label {{ font-size: 13px; display: flex; flex-direction: column; gap: 4px; min-width: 140px; flex: 1; }}
        .add-winery-row input[type=text] {{ padding: 6px 8px; border: 1px solid #ccc; border-radius: 4px; width: 100%; max-width: 280px; }}
        .btn {{ padding: 7px 14px; cursor: pointer; border-radius: 6px; border: 1px solid #d1d5db; background: #fff; font-size: 13px; }}
        .btn-primary {{ background: #2563eb; color: #fff; border-color: #1d4ed8; }}
        .btn-danger {{ color: #b91c1c; border-color: #fecaca; background: #fef2f2; }}
        .btn-sm {{ padding: 4px 10px; font-size: 12px; }}
        .admin-flash {{ margin: 8px 0; }}
        .admin-flash-error {{ color: #b91c1c; font-weight: 600; }}
        .admin-grid {{ display: flex; gap: 16px; align-items: stretch; min-height: 480px; }}
        .admin-left {{ flex: 0 0 300px; max-width: 100%; border: 1px solid #d1d5db; border-radius: 8px; background: #fff; overflow: auto; max-height: calc(100vh - 220px); }}
        .admin-right {{ flex: 1; min-width: 0; border: 1px solid #d1d5db; border-radius: 8px; background: #fff; padding: 14px 16px; overflow: auto; max-height: calc(100vh - 220px); }}
        .admin-winery-item {{ border-bottom: 1px solid #e5e7eb; }}
        .admin-winery-item-link {{
            display: block; padding: 10px 12px; text-decoration: none; color: inherit; cursor: pointer;
        }}
        .admin-winery-item:hover .admin-winery-item-link {{ background: #f9fafb; }}
        .admin-winery-item-active .admin-winery-item-link {{
            background: #eef2ff; border-left: 3px solid #4f46e5; padding-left: 9px;
        }}
        .admin-winery-item-link:focus-visible {{ outline: 2px solid #4f46e5; outline-offset: -2px; }}
        .admin-winery-item .loc {{ display: inline-block; min-width: 2rem; padding: 2px 8px; border-radius: 6px; background: #eef2ff; color: #1e3a8a; font-weight: 800; font-size: 12px; margin-right: 8px; vertical-align: middle; }}
        .admin-winery-item .nm {{ font-weight: 600; font-size: 14px; }}
        .admin-winery-meta {{ font-size: 12px; color: #6b7280; margin-top: 6px; display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
        .fill-badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; font-weight: 700; }}
        .fill-badge-done {{ background: #dcfce7; color: #166534; border: 1px solid #86efac; }}
        .fill-badge-empty {{ background: #fee2e2; color: #991b1b; border: 1px solid #fca5a5; }}
        .fill-badge-warn {{ background: #fef3c7; color: #92400e; border: 1px solid #fcd34d; }}
        .admin-readiness-summary {{ margin-bottom: 14px; }}
        .admin-readiness-summary-title {{ margin: 0 0 8px; font-size: 1.05rem; font-weight: 700; }}
        .admin-readiness-summary-list {{ margin: 0; padding-left: 1.25rem; font-size: 14px; line-height: 1.5; }}
        .admin-publish-warn {{ border-color: #f59e0b; background: #fffbeb; color: #78350f; margin-bottom: 14px; }}
        .btn-copy-link {{ margin-top: 4px; }}
        .copy-link-msg {{ display: none; color: #065f46; font-size: 12px; margin: 4px 0 0; }}
        .admin-right-head {{ display: flex; flex-wrap: wrap; justify-content: space-between; gap: 12px; align-items: flex-start; margin-bottom: 14px; padding-bottom: 12px; border-bottom: 1px solid #e5e7eb; }}
        .admin-copy-block {{ max-width: 22rem; font-size: 12px; color: #4b5563; }}
        .copy-link-hint {{ margin: 6px 0 0; line-height: 1.45; }}
        .admin-meta-block {{ border: 1px solid #e5e7eb; border-radius: 8px; padding: 12px 14px; margin-bottom: 16px; background: #fafafa; }}
        .admin-meta-form {{ margin: 0; }}
        .admin-meta-row1 {{ display: flex; flex-wrap: wrap; gap: 10px 16px; align-items: flex-end; margin-bottom: 10px; }}
        .admin-meta-lab-loc {{ flex: 0 0 auto; width: 5rem; font-size: 13px; display: flex; flex-direction: column; gap: 4px; }}
        .admin-meta-inp-loc {{ width: 100%; max-width: 4.25rem; padding: 6px 8px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }}
        .admin-meta-lab-name {{ flex: 2 1 200px; min-width: 160px; font-size: 13px; display: flex; flex-direction: column; gap: 4px; }}
        .admin-meta-lab-web {{ flex: 2 1 220px; min-width: 180px; font-size: 13px; display: flex; flex-direction: column; gap: 4px; }}
        .admin-meta-lab-name input, .admin-meta-lab-web input {{ width: 100%; padding: 6px 8px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }}
        .admin-meta-row2 {{ display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; justify-content: space-between; }}
        .admin-meta-lab-note {{ flex: 1 1 240px; min-width: 200px; font-size: 13px; display: flex; flex-direction: column; gap: 4px; }}
        .admin-meta-lab-note input {{ width: 100%; padding: 6px 8px; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }}
        .admin-meta-row2-btns {{ display: flex; gap: 8px; flex-shrink: 0; align-items: center; flex-wrap: wrap; }}
        .admin-meta-actions {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
        .wine-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
        .wine-table th, .wine-table td {{ border: 1px solid #e5e7eb; padding: 6px 8px; vertical-align: middle; }}
        .wine-table th {{ background: #f9fafb; font-weight: 700; text-align: left; color: #374151; }}
        .wine-table input[type=text], .wine-table select {{ width: 100%; min-width: 0; padding: 5px 6px; border: 1px solid #d1d5db; border-radius: 4px; font-size: 12px; }}
        .wine-table-wrap {{ overflow-x: auto; }}
        .wine-add-row {{ background: #f0fdf4; }}
        .wine-add-row td {{ border-color: #bbf7d0 !important; }}
        .wine-add-na {{ color: #9ca3af; font-size: 12px; }}
        .admin-empty {{ color: #6b7280; padding: 24px; text-align: center; }}
        .admin-section-title {{ font-size: 1rem; margin: 0 0 10px; font-weight: 700; }}
        .admin-tabs {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; border-bottom: 1px solid #e5e7eb; padding-bottom: 0; }}
        .admin-tab {{ display: inline-block; padding: 10px 16px; text-decoration: none; color: #374151; border-radius: 8px 8px 0 0; margin-bottom: -1px; border: 1px solid transparent; border-bottom: none; font-weight: 600; font-size: 14px; }}
        .admin-tab:hover {{ background: #f3f4f6; color: #111; }}
        .admin-tab-active {{ background: #fff; color: #1d4ed8; border-color: #e5e7eb; border-bottom: 1px solid #fff; }}
        .stats-summary {{ font-size: 1.05rem; margin: 0 0 14px; font-weight: 600; }}
        .stats-table {{ width: 100%; border-collapse: collapse; font-size: 13px; background: #fff; }}
        .stats-table th, .stats-table td {{ border: 1px solid #e5e7eb; padding: 8px 10px; text-align: left; }}
        .stats-table th {{ background: #f9fafb; font-weight: 700; }}
        .stats-table tr:nth-child(even) {{ background: #fafafa; }}
        .import-hint {{ font-size: 13px; color: #4b5563; line-height: 1.55; margin: 0 0 12px; max-width: 52rem; }}
    </style>
</head>
<body>
    <div class="admin-wrap">
    <table class="admin-header-table" role="presentation">
    <tr>
      <td class="admin-header-main">
        <p class="nav"><a href="{guide_h}">Přihlášení</a></p>
      </td>
      <td class="admin-header-qr" rowspan="3">
        <div class="admin-header-qr-stack">
        <img src="{escape(qr_src)}" alt="QR k odkazu Host" width="120" height="120" loading="lazy">
        <a class="admin-header-host-link" href="{prev_h}">Host</a>
        </div>
      </td>
    </tr>
    <tr>
      <td class="admin-header-main">
        <h1>Správa akce — {title}</h1>
{event_date_html}
      </td>
    </tr>
    <tr>
      <td class="admin-header-main admin-header-flash">
{flash_html}
      </td>
    </tr>
    </table>
"""
    cat_href = escape(_admin_tab_url(admin_base, "catalog", sel or None))
    stats_href = escape(_admin_tab_url(admin_base, "stats"))
    imp_href = escape(_admin_tab_url(admin_base, "import"))
    tc = " admin-tab-active" if active_tab == "catalog" else ""
    ts = " admin-tab-active" if active_tab == "stats" else ""
    ti = " admin-tab-active" if active_tab == "import" else ""
    tabs_html = f"""
    <nav class="admin-tabs" aria-label="Sekce správy">
      <a class="admin-tab{tc}" href="{cat_href}">Katalog</a>
      <a class="admin-tab{ts}" href="{stats_href}">Statistiky</a>
      <a class="admin-tab{ti}" href="{imp_href}">Import CSV</a>
    </nav>
"""
    rt = escape(active_tab)
    head_prefix = (
        head
        + tabs_html
    )
    add_winery_html = f"""    <div class="box box-tight">
        <h2>Přidat vinařství</h2>
        <form method="post" class="add-winery-row">
            <input type="hidden" name="action" value="add_winery">
            <input type="hidden" name="redirect_tab" value="{rt}">
            <label>Název *<input type="text" name="winery_name" required autocomplete="off"></label>
            <label>Číslo sklepu <input type="text" name="winery_location_number" autocomplete="off" placeholder="doplníte před publikací"></label>
            <label>Poznámka<input type="text" name="winery_note" autocomplete="off"></label>
            <label>Web<input type="text" name="winery_web" autocomplete="off" placeholder="https://…"></label>
            <button type="submit" class="btn btn-primary">Přidat</button>
        </form>
    </div>
"""
    warn_html = ""
    if readiness["any_missing_loc"]:
        warn_html = """    <div class="admin-publish-warn box" role="alert">
      <strong>Upozornění:</strong> Některá vinařství nemají přiřazené číslo sklepu. Akce není plně připravená pro návštěvnické použití, dokud čísla nedoplníte.
    </div>
"""
    summary_html = f"""    <div class="admin-readiness-summary box">
      <p class="admin-readiness-summary-title">Připravenost akce</p>
      <ul class="admin-readiness-summary-list">
        <li>Vinařství celkem: <strong>{readiness["n_total"]}</strong></li>
        <li>S alespoň jedním vínem: <strong>{readiness["n_with_wines"]}</strong></li>
        <li>S vyplněným číslem sklepu: <strong>{readiness["n_with_loc"]}</strong></li>
        <li>Nepřipraveno k publikaci (chybí číslo sklepu nebo vína): <strong>{readiness["n_not_ready"]}</strong></li>
      </ul>
    </div>
"""
    parts = [head_prefix]
    if readiness["any_missing_loc"]:
        parts.append(warn_html)
    if active_tab == "catalog":
        parts.append(summary_html)
        parts.append(add_winery_html)
        parts.append('<div class="admin-grid">')
        parts.append('<aside class="admin-left" aria-label="Seznam vinařství">')
        if not wineries_sorted:
            parts.append('<p class="admin-empty" style="padding:16px;">Zatím žádná vinařství.</p>')
        for wy in wineries_sorted:
            wy_id = str(wy.get("id") or "").strip()
            wname = escape(str(wy.get("name") or ""))
            loc_num = str(wy.get("locationNumber") or "").strip()
            wine_count = len(wines_by_wid.get(wy_id, []))
            status_badges = _admin_winery_status_badges_html(wy, wine_count)
            active = " admin-winery-item-active" if wy_id == sel else ""
            list_href = _admin_tab_url(admin_base, "catalog", wy_id)
            aria_cur = ' aria-current="true"' if wy_id == sel else ""
            row_label = escape(
                f"{loc_num or '—'} — {str(wy.get('name') or '').strip() or 'Vinařství'}"
            )
            parts.append(
                f'<div class="admin-winery-item{active}">'
                f'<a class="admin-winery-item-link" href="{escape(list_href)}"'
                f'{aria_cur} aria-label="Upravit katalog: {row_label}">'
                f'<span class="loc">{escape(loc_num or "—")}</span>'
                f'<span class="nm">{wname}</span>'
                f'<div class="admin-winery-meta">'
                f"{status_badges}"
                f"<span>{wine_count} vín</span></div></a>"
                f"</div>"
            )
        parts.append("</aside>")

        parts.append('<main class="admin-right">')
        if not selected_wy:
            parts.append(
                '<p class="admin-empty">Vyberte vinařství vlevo nebo přidejte první vinařství.</p>'
            )
        else:
            wy_id = str(selected_wy.get("id") or "").strip()
            raw_wy_name = str(selected_wy.get("name") or "")
            loc_num = str(selected_wy.get("locationNumber") or "").strip()
            wy_note = str(selected_wy.get("note") or "").strip()
            wy_web = str(selected_wy.get("web") or "").strip()
            token = str(selected_wy.get("token") or "").strip() or _new_contributor_token()
            contrib_rel = url_for(
                "guide_contributor_catalog",
                event_id=event_id,
                winery_id=wy_id,
            )
            contrib_href = f"{absolute_public_url(contrib_rel)}?{urlencode({'t': token})}"
            copy_hint = (
                "Pošlete vinaři odkaz e-mailem nebo zprávou. Otevře mu stránku pro doplnění vín; "
                "v&nbsp;URL je tajný token — nesdílejte ho veřejně."
            )
            parts.append('<div class="admin-right-head">')
            parts.append(
                f'<div><span class="loc" style="margin-right:8px;">{escape(loc_num or "—")}</span>'
                f'<strong style="font-size:1.1rem;">{escape(raw_wy_name)}</strong></div>'
                f'<div class="admin-copy-block">'
                f'<button type="button" class="btn btn-sm btn-copy-link" data-link="{escape(contrib_href)}">'
                f"Kopírovat odkaz pro vinaře</button>"
                f'<p class="copy-link-msg">Odkaz zkopírován.</p>'
                f'<p class="copy-link-hint">{copy_hint}</p>'
                f"</div></div>"
            )
            parts.append(
                f"""
            <div class="admin-meta-block">
            <form id="form-edit-winery" method="post" class="admin-meta-form">
                <input type="hidden" name="action" value="edit_winery">
                <input type="hidden" name="edit_winery_id" value="{escape(wy_id)}">
                <input type="hidden" name="redirect_winery_id" value="{escape(wy_id)}">
                <input type="hidden" name="redirect_tab" value="{rt}">
            </form>
            <form id="form-delete-winery" method="post" onsubmit="return confirm('Opravdu smazat celé vinařství a všechna jeho vína?');">
                <input type="hidden" name="action" value="delete_winery">
                <input type="hidden" name="delete_winery_id" value="{escape(wy_id)}">
                <input type="hidden" name="redirect_tab" value="{rt}">
            </form>
            <div class="admin-meta-row1">
                <label class="admin-meta-lab-loc">Číslo sklepu
                <input class="admin-meta-inp-loc" form="form-edit-winery" type="text" name="edit_winery_location_number"
                  value="{escape(loc_num)}" autocomplete="off" placeholder="volitelné"></label>
                <label class="admin-meta-lab-name">Název vinařství *
                <input type="text" form="form-edit-winery" name="edit_winery_name" value="{escape(raw_wy_name)}"
                  required autocomplete="off"></label>
                <label class="admin-meta-lab-web">Web
                <input type="text" form="form-edit-winery" name="edit_winery_web" value="{escape(wy_web)}"
                  autocomplete="off" placeholder="https://…"></label>
            </div>
            <div class="admin-meta-row2">
                <label class="admin-meta-lab-note">Poznámka (návštěvníci)
                <input type="text" form="form-edit-winery" name="edit_winery_note" value="{escape(wy_note)}"
                  autocomplete="off"></label>
                <div class="admin-meta-row2-btns">
                  <button type="submit" class="btn btn-primary" form="form-edit-winery">Uložit údaje vinařství</button>
                  <button type="submit" class="btn btn-danger btn-sm" form="form-delete-winery">Smazat vinařství</button>
                </div>
            </div>
            </div>
            """
            )

            parts.append('<h3 class="admin-section-title">Vína</h3>')
            wy_wines = wines_by_wid.get(wy_id, [])
            parts.append('<div hidden aria-hidden="true">')
            for wine in wy_wines:
                wid_wine = str(wine.get("id") or "").strip()
                parts.append(
                    f'<form id="wine-edit-{escape(wid_wine)}" method="post"></form>'
                )
            parts.append('<form id="wine-add-form" method="post"></form>')
            parts.append("</div>")
            parts.append('<div class="wine-table-wrap"><table class="wine-table">')
            parts.append(
                "<thead><tr>"
                "<th>Barva</th><th>Label</th><th>Odrůda</th><th>Přívlastek</th>"
                "<th>Ročník</th><th>Popis</th><th>Uložit</th><th>Smazat</th>"
                "</tr></thead><tbody>"
            )

            for wine in wy_wines:
                wid_wine = str(wine.get("id") or "").strip()
                wl = str(wine.get("label") or "").strip()
                wv = str(wine.get("variety") or "").strip()
                wp = str(wine.get("predicate") or "").strip()
                wvin = str(wine.get("vintage") or "").strip()
                wd = (wine.get("description") or "").strip()
                wcol = _norm_scoretaste_wine_color(wine.get("color"))
                fid = escape(wid_wine)
                parts.append(
                    f"<tr>"
                    f'<td><select form="wine-edit-{fid}" name="edit_wine_color" aria-label="Barva">'
                    f"{_admin_wine_color_options(wcol)}</select></td>"
                    f'<td><input form="wine-edit-{fid}" type="text" name="edit_wine_label" value="{escape(wl)}" required autocomplete="off"></td>'
                    f'<td><input form="wine-edit-{fid}" type="text" name="edit_wine_variety" value="{escape(wv)}" required autocomplete="off"></td>'
                    f'<td><input form="wine-edit-{fid}" type="text" name="edit_wine_predicate" value="{escape(wp)}" autocomplete="off"></td>'
                    f'<td><input form="wine-edit-{fid}" type="text" name="edit_wine_vintage" value="{escape(wvin)}" required autocomplete="off"></td>'
                    f'<td><input form="wine-edit-{fid}" type="text" name="edit_wine_description" value="{escape(wd)}" autocomplete="off"></td>'
                    f'<td>'
                    f'<input form="wine-edit-{fid}" type="hidden" name="action" value="edit_wine">'
                    f'<input form="wine-edit-{fid}" type="hidden" name="edit_wine_id" value="{escape(wid_wine)}">'
                    f'<input form="wine-edit-{fid}" type="hidden" name="redirect_winery_id" value="{escape(wy_id)}">'
                    f'<input form="wine-edit-{fid}" type="hidden" name="redirect_tab" value="{rt}">'
                    f'<button form="wine-edit-{fid}" type="submit" class="btn btn-primary btn-sm">Uložit</button>'
                    f"</td>"
                    f'<td><form method="post" style="margin:0;" onsubmit="return confirm(\'Smazat toto víno?\');">'
                    f'<input type="hidden" name="action" value="delete_wine">'
                    f'<input type="hidden" name="delete_wine_id" value="{escape(wid_wine)}">'
                    f'<input type="hidden" name="redirect_winery_id" value="{escape(wy_id)}">'
                    f'<input type="hidden" name="redirect_tab" value="{rt}">'
                    f'<button type="submit" class="btn btn-danger btn-sm">Smazat</button>'
                    f"</form></td>"
                    f"</tr>"
                )

            parts.append(
                f"""
    <tr class="wine-add-row">
      <td><select form="wine-add-form" name="wine_color" aria-label="Barva">{_admin_wine_color_options("white")}</select></td>
      <td><input form="wine-add-form" type="text" name="wine_label" required autocomplete="off" aria-label="Label"></td>
      <td><input form="wine-add-form" type="text" name="wine_variety" required autocomplete="off" aria-label="Odrůda"></td>
      <td><input form="wine-add-form" type="text" name="wine_predicate" autocomplete="off" aria-label="Přívlastek"></td>
      <td><input form="wine-add-form" type="text" name="wine_vintage" required autocomplete="off" aria-label="Ročník"></td>
      <td><input form="wine-add-form" type="text" name="wine_description" autocomplete="off" aria-label="Popis"></td>
      <td>
        <input form="wine-add-form" type="hidden" name="action" value="add_wine">
        <input form="wine-add-form" type="hidden" name="target_winery_id" value="{escape(wy_id)}">
        <input form="wine-add-form" type="hidden" name="redirect_tab" value="{rt}">
        <button form="wine-add-form" type="submit" class="btn btn-primary btn-sm">Přidat víno</button>
      </td>
      <td class="wine-add-na">—</td>
    </tr>
    """
            )
            parts.append("</tbody></table></div>")

        parts.append("</main></div>")
    elif active_tab == "stats":
        conn = get_connection()
        try:
            au = _admin_stats_active_users(conn, event_id)
            wine_rows = _admin_stats_wine_rows(conn, event_id)
        finally:
            conn.close()
        liked_wines = [r for r in wine_rows if r["likes"] > 0]
        liked_lines = "".join(
            f"<li>{escape(r['label'])} — {escape(r['winery_name'])} ({r['likes']}×)</li>"
            for r in liked_wines
        )
        stat_body = "".join(
            f"<tr><td>{escape(r['label'])}</td><td>{escape(r['winery_name'])}</td>"
            f"<td>{r['likes']}</td><td>{r['want_buy']}</td></tr>"
            for r in wine_rows
        )
        parts.append(
            f"""
    <div class="box">
      <p class="stats-summary">Aktivní uživatelé: {au}</p>
      <p class="import-hint" style="margin-top:0;">
        Počítají se návštěvníci (prohlížeč), kteří u alespoň jednoho vína zapnuli <strong>lajk</strong>
        nebo <strong>chtěl bych koupit</strong> (data se synchronizují z aplikace návštěvníka).
      </p>
      <h3 class="admin-section-title">Vína s alespoň jedním lajkem</h3>
      {"<ul>" + liked_lines + "</ul>" if liked_lines else "<p class=\"admin-empty\" style=\"padding:8px 0;\">Zatím žádné lajky.</p>"}
      <h3 class="admin-section-title" style="margin-top:18px;">Všechna vína (řazení: lajky ↓, zájem o koupi ↓)</h3>
      <div style="overflow-x:auto;">
        <table class="stats-table">
          <thead><tr><th>Label</th><th>Vinařství</th><th>Počet like</th><th>Počet chtěl koupit</th></tr></thead>
          <tbody>{stat_body}</tbody>
        </table>
      </div>
    </div>
"""
        )
    elif active_tab == "import":
        parts.append(
            f"""
    <div class="box box-tight">
      <h2 class="admin-section-title">Import CSV</h2>
      <p class="import-hint">
        Soubor může být <strong>TSV</strong> (tabulátor) nebo <strong>CSV</strong> (čárka) — oddělovač se pozná z hlavičky.
        Povinné: <code>nazev_vinarstvi</code> a název vína jako <code>label</code> <em>nebo</em> <code>Vzorek</code>.
        Volitelné: <code>ID_sklep</code> / <code>cislo_sklepu</code> / <code>location_number</code> (číslo sklepu u nového vinařství),
        <code>web</code>, <code>email</code>, <code>odruda</code>, <code>rocnik</code> (prázdné → výchozí <code>none</code> / <code>1000</code>),
        <code>privlastek</code>, <code>poznamka</code> → popis vína, <code>barva</code>.
        Stejné <code>nazev_vinarstvi</code> na více řádcích = jedno vinařství, více vín.
        Před větším importem doporučujeme zálohu databáze (postup: <code>docs/db-backup-before-migration.md</code> v projektu).
      </p>
      <form method="post" enctype="multipart/form-data" class="add-winery-row">
        <input type="hidden" name="action" value="import_csv">
        <input type="hidden" name="redirect_tab" value="import">
        <label>Soubor CSV/TSV (UTF-8)<input type="file" name="csv_file" accept=".csv,.txt,text/csv,text/tab-separated-values" required></label>
        <button type="submit" class="btn btn-primary">Nahrát a importovat</button>
      </form>
    </div>
"""
        )
    parts.append(
        """
<script>
(() => {
  document.querySelectorAll(".btn-copy-link").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const url = btn.getAttribute("data-link") || "";
      const wrap = btn.closest(".admin-winery-item, .admin-copy-block, .admin-right-head") || btn.parentElement;
      const msg = wrap ? wrap.querySelector(".copy-link-msg") : null;
      if (!url) return;
      try {
        await navigator.clipboard.writeText(url);
        if (msg) { msg.style.display = "block"; setTimeout(() => { msg.style.display = "none"; }, 2200); }
      } catch (_) { window.prompt("Zkopírujte odkaz ručně:", url); }
    });
  });
})();
</script>
</div>
</body></html>
"""
    )
    return "".join(parts)


@app.route("/guide/admin/<int:event_id>", methods=["GET", "POST"])
def guide_admin_catalog(event_id):
    conn = get_connection()
    deg_row = conn.execute(
        "SELECT * FROM degustace WHERE id = ?",
        (event_id,),
    ).fetchone()
    conn.close()
    if not deg_row or _deg_row_typ_akce(deg_row) != TYP_AKCE_PRUVODCE:
        abort(404)

    if request.method == "POST":
        action = request.form.get("action")
        eid = int(event_id)
        conn = get_connection()
        try:
            if action == "add_winery":
                name = (request.form.get("winery_name") or "").strip()
                loc = (request.form.get("winery_location_number") or "").strip()
                wnote = (request.form.get("winery_note") or "").strip() or None
                wweb = (request.form.get("winery_web") or "").strip() or None
                if not name:
                    flash("Název vinařství je povinný.", "error")
                    return _guide_admin_redirect(eid, None, tab=_admin_tab_from_form())
                loc_val = loc or None
                if loc and _scoretaste_winery_location_taken_db(conn, event_id, loc):
                    flash("Číslo sklepu už existuje.", "error")
                    return _guide_admin_redirect(eid, None, tab=_admin_tab_from_form())
                cur = conn.execute(
                    """
                    INSERT INTO scoretaste_wineries (event_id, name, location_number, token, note, web)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (eid, name, loc_val, _new_contributor_token(), wnote, wweb),
                )
                new_wid = cur.lastrowid
                conn.commit()
                return _guide_admin_redirect(eid, new_wid, tab=_admin_tab_from_form())
            if action == "add_wine":
                winery_id = (request.form.get("target_winery_id") or "").strip()
                if not winery_id or not winery_id.isdigit():
                    abort(400)
                row = conn.execute(
                    "SELECT id FROM scoretaste_wineries WHERE event_id = ? AND id = ?",
                    (eid, int(winery_id)),
                ).fetchone()
                if not row:
                    abort(400)
                label = (request.form.get("wine_label") or "").strip()
                variety = (request.form.get("wine_variety") or "").strip()
                predicate = (request.form.get("wine_predicate") or "").strip()
                vintage = (request.form.get("wine_vintage") or "").strip()
                if not label or not variety or not vintage:
                    abort(400)
                desc = (request.form.get("wine_description") or "").strip()
                color = _norm_scoretaste_wine_color(request.form.get("wine_color"))
                conn.execute(
                    """
                    INSERT INTO scoretaste_wines (winery_id, label, variety, predicate, vintage, description, color)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (int(winery_id), label, variety, predicate, vintage, desc or None, color),
                )
                conn.commit()
                return _guide_admin_redirect(eid, int(winery_id), tab=_admin_tab_from_form())
            if action == "edit_winery":
                wid = (request.form.get("edit_winery_id") or "").strip()
                if not wid or not wid.isdigit():
                    abort(400)
                name = (request.form.get("edit_winery_name") or "").strip()
                loc = (request.form.get("edit_winery_location_number") or "").strip()
                wnote = (request.form.get("edit_winery_note") or "").strip() or None
                wweb = (request.form.get("edit_winery_web") or "").strip() or None
                rw_flash = (request.form.get("redirect_winery_id") or "").strip()
                rw_i = int(rw_flash) if rw_flash.isdigit() else None
                if not name:
                    flash("Název vinařství je povinný.", "error")
                    return _guide_admin_redirect(eid, rw_i, tab=_admin_tab_from_form())
                loc_val = loc or None
                if loc and _scoretaste_winery_location_taken_db(conn, event_id, loc, wid):
                    flash("Číslo sklepu už existuje.", "error")
                    return _guide_admin_redirect(eid, rw_i, tab=_admin_tab_from_form())
                cur = conn.execute(
                    """
                    UPDATE scoretaste_wineries
                    SET name = ?, location_number = ?, note = ?, web = ?
                    WHERE id = ? AND event_id = ?
                    """,
                    (name, loc_val, wnote, wweb, int(wid), eid),
                )
                if cur.rowcount == 0:
                    abort(400)
                conn.commit()
                return _guide_admin_redirect(eid, int(wid), tab=_admin_tab_from_form())
            if action == "delete_winery":
                wid = (request.form.get("delete_winery_id") or "").strip()
                if not wid or not wid.isdigit():
                    abort(400)
                cur = conn.execute(
                    "DELETE FROM scoretaste_wineries WHERE id = ? AND event_id = ?",
                    (int(wid), eid),
                )
                if cur.rowcount == 0:
                    abort(400)
                row_next = conn.execute(
                    """
                    SELECT id FROM scoretaste_wineries
                    WHERE event_id = ?
                    ORDER BY location_number IS NULL, location_number COLLATE NOCASE
                    LIMIT 1
                    """,
                    (eid,),
                ).fetchone()
                next_wid = int(row_next[0]) if row_next else None
                conn.commit()
                return _guide_admin_redirect(eid, next_wid, tab=_admin_tab_from_form())
            if action == "edit_wine":
                wine_id = (request.form.get("edit_wine_id") or "").strip()
                if not wine_id or not wine_id.isdigit():
                    abort(400)
                label = (request.form.get("edit_wine_label") or "").strip()
                variety = (request.form.get("edit_wine_variety") or "").strip()
                predicate = (request.form.get("edit_wine_predicate") or "").strip()
                vintage = (request.form.get("edit_wine_vintage") or "").strip()
                desc = (request.form.get("edit_wine_description") or "").strip()
                color = _norm_scoretaste_wine_color(request.form.get("edit_wine_color"))
                if not label or not variety or not vintage:
                    abort(400)
                cur = conn.execute(
                    """
                    UPDATE scoretaste_wines SET label=?, variety=?, predicate=?, vintage=?, description=?, color=?
                    WHERE id=? AND winery_id IN (SELECT id FROM scoretaste_wineries WHERE event_id=?)
                    """,
                    (label, variety, predicate, vintage, desc or None, color, int(wine_id), eid),
                )
                if cur.rowcount == 0:
                    abort(400)
                conn.commit()
                rw = (request.form.get("redirect_winery_id") or "").strip()
                rw_i = int(rw) if rw.isdigit() else None
                return _guide_admin_redirect(eid, rw_i, tab=_admin_tab_from_form())
            if action == "delete_wine":
                wine_id = (request.form.get("delete_wine_id") or "").strip()
                if not wine_id or not wine_id.isdigit():
                    abort(400)
                cur = conn.execute(
                    """
                    DELETE FROM scoretaste_wines WHERE id=? AND winery_id IN
                    (SELECT id FROM scoretaste_wineries WHERE event_id=?)
                    """,
                    (int(wine_id), eid),
                )
                if cur.rowcount == 0:
                    abort(400)
                conn.commit()
                rw = (request.form.get("redirect_winery_id") or "").strip()
                rw_i = int(rw) if rw.isdigit() else None
                return _guide_admin_redirect(eid, rw_i, tab=_admin_tab_from_form())
            if action == "import_csv":
                f = request.files.get("csv_file")
                if not f or not (getattr(f, "filename", None) or "").strip():
                    flash("Vyberte CSV soubor.", "error")
                    return _guide_admin_redirect(eid, None, tab="import")
                raw = f.read()
                if not raw:
                    flash("Soubor je prázdný.", "error")
                    return _guide_admin_redirect(eid, None, tab="import")
                try:
                    text = raw.decode("utf-8-sig")
                except UnicodeDecodeError:
                    flash("Soubor musí být UTF-8.", "error")
                    return _guide_admin_redirect(eid, None, tab="import")
                n, err = _import_scoretaste_csv_rows(conn, eid, text)
                if err:
                    conn.rollback()
                    flash(err, "error")
                else:
                    conn.commit()
                    flash(f"Importováno {n} vín.", "success")
                return _guide_admin_redirect(eid, None, tab="import")
            abort(400)
        finally:
            conn.close()

    catalog = _ensure_scoretaste_catalog_for_deg_row(event_id, deg_row)
    req_w = (request.args.get("winery") or "").strip()
    selected_winery_id = req_w if req_w.isdigit() else None
    tab = (request.args.get("tab") or "catalog").strip().lower()
    if tab not in ("catalog", "stats", "import"):
        tab = "catalog"
    return _html_guide_admin_page(
        event_id,
        deg_row,
        catalog,
        selected_winery_id=selected_winery_id,
        active_tab=tab,
    )


@app.route("/guide/assets/<path:filename>")
def guide_assets(filename):
    if not os.path.isdir(SCORETASTE_ASSETS_DIR):
        abort(404)
    return send_from_directory(SCORETASTE_ASSETS_DIR, filename)


@app.route("/guide/data/events/<event_id>.json")
def guide_event_data(event_id):
    try:
        eid = int(str(event_id).strip())
    except (ValueError, TypeError):
        abort(404)
    conn = get_connection()
    try:
        deg = conn.execute("SELECT * FROM degustace WHERE id = ?", (eid,)).fetchone()
        if not deg or _deg_row_typ_akce(deg) != TYP_AKCE_PRUVODCE:
            abort(404)
        _maybe_migrate_json_to_db(conn, eid)
        _scoretaste_ensure_tokens_in_db(conn, eid)
        catalog = _scoretaste_catalog_from_db(conn, eid)
        if catalog is None:
            abort(404)
        conn.commit()
    finally:
        conn.close()
    return Response(
        json.dumps(catalog, ensure_ascii=False, indent=2),
        mimetype="application/json; charset=utf-8",
    )


@app.route("/guide/data/events/<int:event_id>/visitor-sync", methods=["POST"])
def guide_visitor_sync(event_id):
    eid = int(event_id)
    data = request.get_json(silent=True) or {}
    sk = (data.get("sessionKey") or "").strip()
    if len(sk) < 8 or len(sk) > 256:
        return jsonify(ok=False, error="sessionKey"), 400
    wines = data.get("wines")
    if not isinstance(wines, dict):
        return jsonify(ok=False, error="wines"), 400
    conn = get_connection()
    try:
        deg = conn.execute("SELECT * FROM degustace WHERE id = ?", (eid,)).fetchone()
        if not deg or _deg_row_typ_akce(deg) != TYP_AKCE_PRUVODCE:
            abort(404)
        conn.execute(
            "DELETE FROM scoretaste_visitor_wine_flag WHERE event_id = ? AND session_key = ?",
            (eid, sk),
        )
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for wid_str, rec in wines.items():
            ws = str(wid_str).strip()
            if not ws.isdigit():
                continue
            wid = int(ws)
            row = conn.execute(
                """
                SELECT w.id FROM scoretaste_wines w
                JOIN scoretaste_wineries y ON w.winery_id = y.id
                WHERE y.event_id = ? AND w.id = ?
                """,
                (eid, wid),
            ).fetchone()
            if not row:
                continue
            if not isinstance(rec, dict):
                continue
            liked = bool(rec.get("liked"))
            want = bool(rec.get("wantToBuy"))
            if not liked and not want:
                continue
            conn.execute(
                """
                INSERT INTO scoretaste_visitor_wine_flag
                (event_id, wine_id, session_key, liked, want_to_buy, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (eid, wid, sk, 1 if liked else 0, 1 if want else 0, now),
            )
        conn.commit()
        return jsonify(ok=True)
    finally:
        conn.close()


def _html_guide_contributor_page(event_id, winery, catalog):
    ev = catalog["event"]
    event_name = escape((ev.get("name") or "").strip() or f"Akce {event_id}")
    winery_name = escape(str(winery.get("name") or "").strip() or "Vinařství")
    winery_id = str(winery.get("id") or "").strip()
    token = str(winery.get("token") or "").strip()
    back_to_self = (
        f"{url_for('guide_contributor_catalog', event_id=event_id, winery_id=winery_id)}"
        f"?{urlencode({'t': token})}"
    )
    flash_html = ""
    for cat, msg in get_flashed_messages(with_categories=True):
        flash_html += f'<p class="c-flash c-flash-{escape(cat)}">{escape(msg)}</p>\n'
    wines = [
        w for w in catalog["wines"]
        if str(w.get("wineryId") or "").strip() == winery_id
    ]
    wines = sorted(wines, key=lambda x: str(x.get("label") or "").lower())

    row_blocks = []
    if wines:
        for i, wine in enumerate(wines):
            wine_id = str(wine.get("id") or "").strip()
            label = str(wine.get("label") or "").strip()
            variety = str(wine.get("variety") or "").strip()
            predicate = str(wine.get("predicate") or "").strip()
            vintage = str(wine.get("vintage") or "").strip()
            description = str(wine.get("description") or "").strip()
            wcol = _norm_scoretaste_wine_color(wine.get("color"))
            row_blocks.append(
                _contrib_wine_row_html(
                    str(i),
                    wine_id,
                    label,
                    vintage,
                    wcol,
                    variety,
                    predicate,
                    description,
                )
            )
        next_idx = len(wines)
    else:
        row_blocks.append(
            _contrib_wine_row_html(
                "0", "", "", "", "white", "", "", ""
            )
        )
        next_idx = 1

    tpl_row = _contrib_wine_row_html(
        "ROWIDX", "", "", "", "white", "", "", ""
    )
    rows_joined = "\n".join(row_blocks)

    out = f"""<!DOCTYPE html>
<html lang="cs">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Vinař — {event_name} / {winery_name}</title>
  <style>
    body {{ font-family: system-ui, Arial, sans-serif; margin: 0; color: #1a1a1a; background: #f3f4f6;
      padding: 0.65rem 0.75rem 5.25rem; max-width: 36rem; margin-left: auto; margin-right: auto;
      -webkit-text-size-adjust: 100%; }}
    h1 {{ font-size: 1.15rem; margin: 0 0 0.35rem; font-weight: 700; }}
    .meta {{ color: #555; margin: 0 0 0.75rem; font-size: 0.88rem; line-height: 1.4; }}
    .c-flash {{ margin: 0.35rem 0 0.65rem; font-weight: 600; font-size: 0.9rem; }}
    .c-flash-error {{ color: #b91c1c; }}
    .c-flash-success {{ color: #065f46; }}
    #c-main-form {{ display: block; }}
    .c-wine-list {{ display: flex; flex-direction: column; gap: 0.55rem; }}
    .c-wine-row {{ border: 1px solid #e5e7eb; border-radius: 10px; padding: 0.45rem 0.5rem;
      background: #fff; box-shadow: 0 1px 2px rgba(0,0,0,0.04); }}
    .c-wine-line1 {{ display: flex; flex-wrap: wrap; align-items: center; gap: 0.35rem; }}
    .c-inp, .c-sel, .c-ta {{ font-size: 16px; padding: 0.45rem 0.5rem; border: 1px solid #d1d5db;
      border-radius: 8px; background: #fff; }}
    .c-inp-label {{ flex: 1 1 9rem; min-width: 0; }}
    .c-sel {{ flex: 0 0 auto; min-width: 5.5rem; }}
    .c-inp-vint {{ flex: 0 0 4.25rem; width: 4.25rem; }}
    .c-del {{ flex: 0 0 auto; width: 2.5rem; height: 2.5rem; padding: 0; border: 1px solid #e5e7eb;
      border-radius: 8px; background: #fafafa; font-size: 1.1rem; line-height: 1; cursor: pointer; }}
    .c-more {{ margin-top: 0.35rem; }}
    .c-more summary {{ cursor: pointer; font-size: 0.82rem; font-weight: 600; color: #374151;
      list-style: none; padding: 0.2rem 0; }}
    .c-more summary::-webkit-details-marker {{ display: none; }}
    .c-more[open] summary {{ margin-bottom: 0.35rem; }}
    .c-more .c-inp, .c-more .c-ta {{ display: block; width: 100%; box-sizing: border-box; margin-top: 0.35rem; }}
    .c-ta {{ resize: vertical; min-height: 2.5rem; }}
    .c-add {{ width: 100%; margin-top: 0.35rem; padding: 0.55rem 0.75rem; font-size: 0.95rem;
      font-weight: 600; border: 1px dashed #9ca3af; border-radius: 10px; background: #fff;
      color: #1e3a8a; cursor: pointer; }}
    .c-savebar {{ position: fixed; left: 0; right: 0; bottom: 0; z-index: 50; padding: 0.5rem 0.75rem;
      background: linear-gradient(180deg, transparent, #f3f4f6 28%); border-top: 1px solid #e5e7eb;
      display: flex; justify-content: center; }}
    .c-savebar button {{ min-width: 12rem; padding: 0.65rem 1rem; font-size: 1rem; font-weight: 700;
      border: none; border-radius: 10px; background: #2563eb; color: #fff; cursor: pointer; }}
    #wine-row-tpl {{ display: none; }}
  </style>
</head>
<body>
  <h1>Doplňte vína</h1>
  <p class="meta"><strong>Akce:</strong> {event_name}<br><strong>Vinařství:</strong> {winery_name}</p>
  {flash_html}
  <form id="c-main-form" method="post" action="{escape(back_to_self)}">
    <input type="hidden" name="action" value="contributor_save_all">
    <input type="hidden" name="t" value="{escape(token)}">
    <div id="c-wine-list" class="c-wine-list">
{rows_joined}
    </div>
    <button type="button" class="c-add" id="c-add-wine">+ Přidat víno</button>
  </form>
  <template id="wine-row-tpl">{tpl_row}</template>
  <div class="c-savebar">
    <button type="submit" form="c-main-form">Uložit vše</button>
  </div>
  <script>
(function() {{
  const list = document.getElementById("c-wine-list");
  const tpl = document.getElementById("wine-row-tpl");
  let nextIdx = {next_idx};
  function bindRow(row) {{
    const del = row.querySelector(".c-del");
    if (del) del.addEventListener("click", function() {{ row.remove(); }});
  }}
  list.querySelectorAll(".c-wine-row").forEach(bindRow);
  document.getElementById("c-add-wine").addEventListener("click", function() {{
    const html = tpl.innerHTML.replace(/ROWIDX/g, String(nextIdx));
    nextIdx += 1;
    list.insertAdjacentHTML("beforeend", html);
    bindRow(list.lastElementChild);
  }});
}})();
  </script>
</body>
</html>"""
    return out


@app.route("/guide/contribute/<event_id>/<winery_id>", methods=["GET", "POST"])
def guide_contributor_catalog(event_id, winery_id):
    try:
        eid = int(event_id)
    except (TypeError, ValueError):
        abort(404)
    conn = get_connection()
    deg_row = conn.execute(
        "SELECT * FROM degustace WHERE id = ?",
        (eid,),
    ).fetchone()
    conn.close()
    if not deg_row or _deg_row_typ_akce(deg_row) != TYP_AKCE_PRUVODCE:
        abort(404)

    catalog = _ensure_scoretaste_catalog_for_deg_row(event_id, deg_row)
    winery_id_s = str(winery_id).strip()
    winery = next(
        (w for w in catalog["wineries"] if str(w.get("id") or "").strip() == winery_id_s),
        None,
    )
    if not winery:
        abort(404)

    tok_qs = (request.args.get("t") or request.form.get("t") or "").strip()
    tok_expected = str(winery.get("token") or "").strip()
    if not tok_qs or not tok_expected or not hmac.compare_digest(tok_expected, tok_qs):
        abort(403)

    if request.method == "POST":
        action = (request.form.get("action") or "").strip()
        label = (request.form.get("label") or "").strip()
        variety = _contrib_variety_from_label(request.form.get("variety"), label)
        predicate = (request.form.get("predicate") or "").strip()
        vintage = (request.form.get("vintage") or "").strip()
        desc = (request.form.get("description") or "").strip()
        winery_db_id = int(winery_id_s)
        conn = get_connection()
        try:
            if action == "contributor_save_all":
                row_ix = _contributor_form_row_indices(request.form)
                row = conn.execute(
                    "SELECT id FROM scoretaste_wineries WHERE id = ? AND event_id = ?",
                    (winery_db_id, eid),
                ).fetchone()
                if not row:
                    abort(400)
                keep_ids = []
                for i in row_ix:
                    label_i = (request.form.get(f"row_{i}_label") or "").strip()
                    vintage_i = (request.form.get(f"row_{i}_vintage") or "").strip()
                    if not label_i or not vintage_i:
                        continue
                    variety_i = _contrib_variety_from_label(
                        request.form.get(f"row_{i}_variety"), label_i
                    )
                    predicate_i = (request.form.get(f"row_{i}_predicate") or "").strip()
                    desc_i = (request.form.get(f"row_{i}_description") or "").strip()
                    color_i = _norm_scoretaste_wine_color(
                        request.form.get(f"row_{i}_color")
                    )
                    wine_id_i = (request.form.get(f"row_{i}_wine_id") or "").strip()
                    if wine_id_i and wine_id_i.isdigit():
                        cur = conn.execute(
                            """
                            UPDATE scoretaste_wines SET label=?, variety=?, predicate=?, vintage=?, description=?, color=?
                            WHERE id=? AND winery_id=?
                            """,
                            (
                                label_i,
                                variety_i,
                                predicate_i,
                                vintage_i,
                                desc_i or None,
                                color_i,
                                int(wine_id_i),
                                winery_db_id,
                            ),
                        )
                        if cur.rowcount == 0:
                            abort(400)
                        keep_ids.append(int(wine_id_i))
                    else:
                        cur = conn.execute(
                            """
                            INSERT INTO scoretaste_wines (winery_id, label, variety, predicate, vintage, description, color)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                winery_db_id,
                                label_i,
                                variety_i,
                                predicate_i,
                                vintage_i,
                                desc_i or None,
                                color_i,
                            ),
                        )
                        keep_ids.append(cur.lastrowid)
                if keep_ids:
                    placeholders = ",".join("?" * len(keep_ids))
                    conn.execute(
                        f"""
                        DELETE FROM scoretaste_wines
                        WHERE winery_id=? AND id NOT IN ({placeholders})
                        """,
                        (winery_db_id, *keep_ids),
                    )
                else:
                    conn.execute(
                        "DELETE FROM scoretaste_wines WHERE winery_id=?",
                        (winery_db_id,),
                    )
                conn.commit()
                flash("Uloženo.", "success")
                return redirect(
                    f"{url_for('guide_contributor_catalog', event_id=event_id, winery_id=winery_id_s)}?{urlencode({'t': tok_qs})}"
                )
            if action == "add_wine":
                if not label or not vintage:
                    flash("Název a ročník jsou povinné.", "error")
                    return redirect(
                        f"{url_for('guide_contributor_catalog', event_id=event_id, winery_id=winery_id_s)}?{urlencode({'t': tok_qs})}"
                    )
                row = conn.execute(
                    "SELECT id FROM scoretaste_wineries WHERE id = ? AND event_id = ?",
                    (winery_db_id, eid),
                ).fetchone()
                if not row:
                    abort(400)
                color = _norm_scoretaste_wine_color(request.form.get("color"))
                conn.execute(
                    """
                    INSERT INTO scoretaste_wines (winery_id, label, variety, predicate, vintage, description, color)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (winery_db_id, label, variety, predicate, vintage, desc or None, color),
                )
                conn.commit()
                flash("Uloženo.", "success")
                return redirect(
                    f"{url_for('guide_contributor_catalog', event_id=event_id, winery_id=winery_id_s)}?{urlencode({'t': tok_qs})}"
                )
            if action == "edit_wine":
                wine_id = (request.form.get("wine_id") or "").strip()
                if not wine_id or not wine_id.isdigit():
                    abort(400)
                if not label or not vintage:
                    flash("Název a ročník jsou povinné.", "error")
                    return redirect(
                        f"{url_for('guide_contributor_catalog', event_id=event_id, winery_id=winery_id_s)}?{urlencode({'t': tok_qs})}"
                    )
                color = _norm_scoretaste_wine_color(request.form.get("color"))
                cur = conn.execute(
                    """
                    UPDATE scoretaste_wines SET label=?, variety=?, predicate=?, vintage=?, description=?, color=?
                    WHERE id=? AND winery_id=?
                    """,
                    (label, variety, predicate, vintage, desc or None, color, int(wine_id), winery_db_id),
                )
                if cur.rowcount == 0:
                    abort(400)
                conn.commit()
                flash("Uloženo.", "success")
                return redirect(
                    f"{url_for('guide_contributor_catalog', event_id=event_id, winery_id=winery_id_s)}?{urlencode({'t': tok_qs})}"
                )
            if action == "delete_wine":
                wine_id = (request.form.get("wine_id") or "").strip()
                if not wine_id or not wine_id.isdigit():
                    abort(400)
                cur = conn.execute(
                    "DELETE FROM scoretaste_wines WHERE id=? AND winery_id=?",
                    (int(wine_id), winery_db_id),
                )
                if cur.rowcount == 0:
                    abort(400)
                conn.commit()
                flash("Smazáno.", "success")
                return redirect(
                    f"{url_for('guide_contributor_catalog', event_id=event_id, winery_id=winery_id_s)}?{urlencode({'t': tok_qs})}"
                )
            abort(400)
        finally:
            conn.close()

    return _html_guide_contributor_page(event_id, winery, catalog)


@app.route("/guide/e/<event_id>")
@app.route("/guide/e/<event_id>/wineries")
@app.route("/guide/e/<event_id>/wineries/<winery_id>")
@app.route("/guide/e/<event_id>/my")
def guide_scoretaste_app(event_id, winery_id=None):
    return _scoretaste_index()


@app.route("/bodovana", methods=["GET", "POST"])
def legacy_bodovana():
    return redirect(URL_SCORE, code=302)


@app.route("/pruvodce", methods=["GET", "POST"])
def legacy_pruvodce():
    return redirect(URL_GUIDE, code=302)


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

        tab_in = request.form.get("settings_tab")
        if tab_in in SETTINGS_TAB_IDS:
            session[SESSION_SETTINGS_TAB_PREFIX + str(id)] = tab_in
            session.modified = True

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

        if action == "set_odruda_zobrazeni":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            ozk = _norm_oz_mode(request.form.get("odruda_zob_katalog"))
            ozt = _norm_oz_mode(request.form.get("odruda_zob_tisk"))
            oze = _norm_oz_mode(request.form.get("odruda_zob_ekatalog"))
            conn.execute(
                """
                UPDATE degustace SET
                    odruda_zob_katalog = ?, odruda_zob_tisk = ?, odruda_zob_ekatalog = ?,
                    odruda_zobrazeni = ?
                WHERE id = ?
                """,
                (ozk, ozt, oze, ozk, id),
            )
            conn.commit()
            flash("Zobrazení názvu odrůd bylo uloženo.", "success")
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

        if action == "import_demo":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Import DEMO je dostupný v režimu Úpravy.", "error")
                return redirect(red)
            _app_dir = os.path.dirname(os.path.abspath(__file__))
            demo_path = os.path.join(_app_dir, "assets", "demo.csv")
            vin_path = os.path.join(_app_dir, "assets", "demo_vin.csv")
            odr_path = os.path.join(_app_dir, "assets", "demo_odr.csv")
            if not os.path.isfile(demo_path):
                conn.close()
                flash("Soubor assets/demo.csv nebyl nalezen.", "error")
                return redirect(red)
            if not os.path.isfile(vin_path):
                conn.close()
                flash("Soubor assets/demo_vin.csv nebyl nalezen.", "error")
                return redirect(red)
            if not os.path.isfile(odr_path):
                conn.close()
                flash("Soubor assets/demo_odr.csv nebyl nalezen.", "error")
                return redirect(red)
            with open(demo_path, "r", encoding="utf-8-sig") as f:
                demo_text = f.read()
            row_d = conn.execute(
                "SELECT id FROM degustace WHERE LOWER(TRIM(COALESCE(nazev, ''))) = ?",
                ("demo",),
            ).fetchone()
            if row_d:
                demo_id = int(row_d[0])
            else:
                conn.execute(
                    "INSERT INTO degustace (nazev, datum, pocet_komisi, typ_akce, misto) VALUES (?, ?, ?, ?, ?)",
                    ("DEMO", "2027-07-02", 3, TYP_AKCE_BODOVANA, None),
                )
                conn.commit()
                demo_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
            conn.execute("DELETE FROM vzorky WHERE degustace_id = ?", (demo_id,))
            conn.commit()
            conn.close()
            result = import_vzorky_z_textu(demo_text, demo_id)
            if result.get("ok"):
                conn = get_connection()
                deg_demo = conn.execute("SELECT * FROM degustace WHERE id = ?", (demo_id,)).fetchone()
                vz_n = conn.execute(
                    "SELECT COUNT(*) AS c FROM vzorky WHERE degustace_id = ?",
                    (demo_id,),
                ).fetchone()["c"]
                pk_d = _degustace_pocet_komisi(deg_demo, int(vz_n or 0))
                _komise_generovat_prirazeni(conn, demo_id, pk_d)
                with open(vin_path, "r", encoding="utf-8-sig") as f:
                    vin_text = f.read()
                with open(odr_path, "r", encoding="utf-8-sig") as f:
                    odr_text = f.read()
                n_vi, n_vu = _vystavovatele_import_z_textu(conn, vin_text)
                n_oi, n_ou = _odrudy_import_z_textu(conn, odr_text)
                conn.commit()
                conn.close()
                zpr = f'Import DEMO: načteno vzorků {result["imported"]}.'
                zpr += f" Vystavovatelé: vloženo {n_vi}, aktualizováno {n_vu}."
                zpr += f" Odrůdy: vloženo {n_oi}, aktualizováno {n_ou}."
                skip = result.get("skipped") or []
                if skip:
                    zpr += " Přeskočeno: " + "; ".join(skip[:5])
                    if len(skip) > 5:
                        zpr += " …"
                flash(zpr, "success")
            else:
                flash(result.get("error", "Import DEMO se nezdařil."), "error")
            session[SESSION_REZIM_PREFIX + str(demo_id)] = "nastaveni"
            session.modified = True
            red_demo = _build_degustace_url(demo_id, st["sort"], st["dir"], st["q"])
            return redirect(red_demo)

        if action == "vystavovatel_pridat":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Přidání vystavovatele je dostupné v režimu Úpravy.", "error")
                return redirect(red)
            nazev, adresa, web, mobil, mail = _vystav_polozky_z_formu()
            if not nazev:
                flash("Název vystavovatele je povinný.", "error")
            else:
                conn.execute(
                    "INSERT INTO vystavovatele (nazev, adresa, web, mobil, mail) VALUES (?, ?, ?, ?, ?)",
                    (nazev, adresa, web, mobil, mail),
                )
                conn.commit()
                flash("Vystavovatel byl přidán.", "success")
            conn.close()
            return redirect(red)

        if action == "vystavovatel_uloz":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Úpravy vystavovatelů jsou dostupné v režimu Úpravy.", "error")
                return redirect(red)
            try:
                vid = int(request.form.get("vystavovatel_id") or "0")
            except ValueError:
                vid = 0
            nazev, adresa, web, mobil, mail = _vystav_polozky_z_formu()
            if vid <= 0:
                flash("Neplatný záznam.", "error")
            elif not nazev:
                flash("Název vystavovatele je povinný.", "error")
            else:
                conn.execute(
                    """
                    UPDATE vystavovatele
                    SET nazev = ?, adresa = ?, web = ?, mobil = ?, mail = ?
                    WHERE id = ?
                    """,
                    (nazev, adresa, web, mobil, mail, vid),
                )
                conn.commit()
                flash("Údaje vystavovatele byly uloženy.", "success")
            conn.close()
            return redirect(red)

        if action == "vystavovatel_smaz":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Smazání je dostupné v režimu Úpravy.", "error")
                return redirect(red)
            try:
                vid = int(request.form.get("vystavovatel_id") or "0")
            except ValueError:
                vid = 0
            if vid > 0:
                conn.execute("DELETE FROM vystavovatele WHERE id = ?", (vid,))
                conn.commit()
                flash("Vystavovatel byl odstraněn.", "success")
            conn.close()
            return redirect(red)

        if action == "vystavovatele_smaz_vse":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Hromadné mazání je dostupné v režimu Úpravy.", "error")
                return redirect(red)
            conn.execute("DELETE FROM vystavovatele")
            conn.commit()
            flash("Všichni vystavovatelé byli smazáni.", "success")
            conn.close()
            return redirect(red)

        if action == "vystavovatel_import_csv":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Import je dostupný v režimu Úpravy.", "error")
                return redirect(red)
            soubor_v = request.files.get("soubor")
            if not soubor_v or not soubor_v.filename:
                conn.close()
                flash("Vyberte CSV soubor.", "error")
                return redirect(red)
            raw_v = soubor_v.read()
            text_v = _decode_bytes(raw_v)
            if text_v is None:
                conn.close()
                flash("Soubor se nepodařilo přečíst (kódování).", "error")
                return redirect(red)
            n_ins, n_up = _vystavovatele_import_z_textu(conn, text_v)
            conn.commit()
            conn.close()
            if n_ins or n_up:
                flash(
                    f"Import vystavovatelů: vloženo {n_ins}, aktualizováno {n_up}.",
                    "success",
                )
            else:
                flash(
                    "Žádný řádek nebyl importován (sloupce: název, adresa, web, mobil, e-mail).",
                    "error",
                )
            return redirect(red)

        def _odruda_polozky_z_formu():
            return (
                (request.form.get("odruda_short") or "").strip().upper(),
                (request.form.get("odruda_long") or "").strip() or None,
            )

        if action == "odruda_pridat":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Přidání odrůdy je dostupné v režimu Úpravy.", "error")
                return redirect(red)
            short, long_ = _odruda_polozky_z_formu()
            if not short:
                flash("Krátký název odrůdy je povinný.", "error")
            else:
                conn.execute(
                    "INSERT INTO odrudy (odruda_short, odruda_long) VALUES (?, ?)",
                    (short, long_),
                )
                conn.commit()
                flash("Odrůda byla přidána.", "success")
            conn.close()
            return redirect(red)

        if action == "odruda_uloz":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Úpravy odrůd jsou dostupné v režimu Úpravy.", "error")
                return redirect(red)
            try:
                oid = int(request.form.get("odruda_row_id") or "0")
            except ValueError:
                oid = 0
            short, long_ = _odruda_polozky_z_formu()
            if oid <= 0:
                flash("Neplatný záznam.", "error")
            elif not short:
                flash("Krátký název odrůdy je povinný.", "error")
            else:
                conn.execute(
                    "UPDATE odrudy SET odruda_short = ?, odruda_long = ? WHERE id = ?",
                    (short, long_, oid),
                )
                conn.commit()
                flash("Odrůda byla uložena.", "success")
            conn.close()
            return redirect(red)

        if action == "odruda_smaz":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Smazání je dostupné v režimu Úpravy.", "error")
                return redirect(red)
            try:
                oid = int(request.form.get("odruda_row_id") or "0")
            except ValueError:
                oid = 0
            if oid > 0:
                conn.execute("UPDATE vzorky SET odruda_id = NULL WHERE odruda_id = ?", (oid,))
                conn.execute("DELETE FROM odrudy WHERE id = ?", (oid,))
                conn.commit()
                flash("Odrůda byla odstraněna (vazby u vzorků zrušeny).", "success")
            conn.close()
            return redirect(red)

        if action == "odrudy_smaz_vse":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Hromadné mazání je dostupné v režimu Úpravy.", "error")
                return redirect(red)
            conn.execute("UPDATE vzorky SET odruda_id = NULL WHERE odruda_id IS NOT NULL")
            conn.execute("DELETE FROM odrudy")
            conn.commit()
            flash("Všechny odrůdy byly smazány (vazby u vzorků zrušeny).", "success")
            conn.close()
            return redirect(red)

        if action == "odruda_import_csv":
            if session.get(SESSION_REZIM_PREFIX + str(id), "seznam") != "nastaveni":
                conn.close()
                return redirect(red)
            if not session.get(SESSION_EDIT_PREFIX + str(id), False):
                conn.close()
                flash("Import je dostupný v režimu Úpravy.", "error")
                return redirect(red)
            soubor_o = request.files.get("soubor")
            if not soubor_o or not soubor_o.filename:
                conn.close()
                flash("Vyberte CSV soubor.", "error")
                return redirect(red)
            raw_o = soubor_o.read()
            text_o = _decode_bytes(raw_o)
            if text_o is None:
                conn.close()
                flash("Soubor se nepodařilo přečíst (kódování).", "error")
                return redirect(red)
            n_ins, n_up = _odrudy_import_z_textu(conn, text_o)
            conn.commit()
            conn.close()
            if n_ins or n_up:
                flash(
                    f"Import odrůd: vloženo {n_ins}, aktualizováno {n_up}.",
                    "success",
                )
            else:
                flash(
                    "Žádný řádek nebyl importován (sloupce: krátký název, dlouhý název).",
                    "error",
                )
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
                odruda_id, odruda = _odruda_z_vzorek_formu(conn)
                privlastek = (request.form.get("privlastek") or "").strip()
                rocnik = (request.form.get("rocnik") or "").strip()
                web_v = _limit_str(request.form.get("web"), _LEN_WEB)
                pzv = _limit_str(request.form.get("poznamka_vzorek"), _LEN_POZN_VZOREK)
                conn.execute(
                    """
                    UPDATE vzorky
                    SET nazev = ?, adresa = ?, odruda = ?, odruda_id = ?, privlastek = ?, rocnik = ?,
                        web = ?, poznamka_vzorek = ?
                    WHERE id = ? AND degustace_id = ?
                    """,
                    (nazev, adresa, odruda, odruda_id, privlastek, rocnik, web_v, pzv, vid, id),
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
            hlavni_k = (request.form.get("hlavni_komisar") or "").strip()
            conn.execute(
                "INSERT INTO komise_porotci (degustace_id, komise_cislo, jmena, hlavni_komisar) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(degustace_id, komise_cislo) DO UPDATE SET jmena=excluded.jmena, "
                "hlavni_komisar=excluded.hlavni_komisar",
                (id, k, jmena or None, hlavni_k or None),
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
            poz = (request.form.get("poznamka_komise") or "").strip()
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

            odruda_id, odruda_txt = _odruda_z_vzorek_formu(conn)

            conn.execute("""
                INSERT INTO vzorky (
                    degustace_id, cislo, nazev, adresa, odruda, odruda_id, privlastek, rocnik, web, poznamka_vzorek
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                id,
                cislo,
                request.form.get("nazev", "").strip(),
                request.form.get("adresa", "").strip(),
                odruda_txt,
                odruda_id,
                request.form.get("privlastek", "").strip(),
                request.form.get("rocnik", "").strip(),
                _limit_str(request.form.get("web"), _LEN_WEB),
                _limit_str(request.form.get("poznamka_vzorek"), _LEN_POZN_VZOREK),
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
    if not degustace:
        conn.close()
        abort(404)

    vzorky = conn.execute(VZORKY_SELECT_JOIN, (id,)).fetchall()

    pocet_komisi = _degustace_pocet_komisi(degustace, len(vzorky))
    rezim_for_auto = session.get(SESSION_REZIM_PREFIX + str(id), "seznam")
    if rezim_for_auto == "komise" and not _komise_prirazeni_existuje(vzorky):
        _komise_generovat_prirazeni(conn, id, pocet_komisi)
        vzorky = conn.execute(VZORKY_SELECT_JOIN, (id,)).fetchall()

    porotci_map = _nacti_porotce_map(conn, id)
    hlavni_komisar_map = _nacti_hlavni_komisar_map(conn, id)

    conn.close()

    vs = _view_state()
    sort_key = vs["sort"]
    sort_dir = vs["dir"]
    q_raw = vs["q"]

    edit_mode = session.get(SESSION_EDIT_PREFIX + str(id), False)
    rezim = session.get(SESSION_REZIM_PREFIX + str(id), "seznam")
    if rezim not in ("seznam", "komise", "nastaveni", "katalog"):
        rezim = "seznam"

    vystavovatele_rows = []
    odrudy_select_rows = []
    if rezim == "nastaveni":
        conn_v = get_connection()
        vystavovatele_rows = conn_v.execute(
            "SELECT id, nazev, adresa, web, mobil, mail FROM vystavovatele ORDER BY nazev COLLATE NOCASE"
        ).fetchall()
        odrudy_select_rows = conn_v.execute(
            "SELECT id, odruda_short, odruda_long FROM odrudy ORDER BY odruda_short COLLATE NOCASE"
        ).fetchall()
        conn_v.close()
    elif rezim == "seznam":
        conn_o = get_connection()
        odrudy_select_rows = conn_o.execute(
            "SELECT id, odruda_short, odruda_long FROM odrudy ORDER BY odruda_short COLLATE NOCASE"
        ).fetchall()
        conn_o.close()

    def _odruda_select_options(selected_id=None):
        lines = ['<option value="">Vlastní text</option>']
        for r in odrudy_select_rows:
            oid = int(r["id"])
            lab = escape((r["odruda_short"] or "").upper())
            tl = escape((r["odruda_long"] or r["odruda_short"] or "").strip())
            s = ""
            if selected_id is not None and int(selected_id) == oid:
                s = " selected"
            lines.append(f'<option value="{oid}" title="{tl}"{s}>{lab}</option>')
        return "\n".join(lines)

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

    if rezim == "komise" and komise_sel == -1:
        komise_sel = 1
        session[SESSION_KOMISE_PREFIX + str(id)] = 1

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
        k_eff = max(1, min(n_kom, komise_sel))
        vzorky_komise_tab = [v for v in vzorky_o if int(v["komise_cislo"] or 0) == k_eff]

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

    settings_tab_cur = session.get(SESSION_SETTINGS_TAB_PREFIX + str(id), "deg")
    if settings_tab_cur not in SETTINGS_TAB_IDS:
        settings_tab_cur = "deg"
    st_hidden = f'<input type="hidden" name="settings_tab" value="{escape(settings_tab_cur)}">'
    ph_set = ph + st_hidden

    def th_sort(col, label):
        href = _sort_href(id, col, sort_key, sort_dir, q_raw)
        sym = _sort_symbol(col, sort_key, sort_dir)
        return (
            f'<th class="th-sort"><a href="{href}" class="th-sort-link">{escape(label)} {sym}</a></th>'
        )

    def th_plain(label):
        return f"<th>{escape(label)}</th>"

    datum_cz = format_datum_cz(degustace["datum"])

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

    # odruda_zob_katalog: seznam vzorků, komise, desktop katalog, bodovací tisk / mobilní hodnocení JSON
    oz_katalog = _deg_oz_field(degustace, "odruda_zob_katalog")
    oz_tisk_katalog = _deg_oz_field(degustace, "odruda_zob_tisk")
    oz_ekatalog = _deg_oz_field(degustace, "odruda_zob_ekatalog")

    tisk_html = ""
    komise_select_html = ""
    if rezim == "komise":
        rozdeleni_tisk = _komise_prirazeni_existuje(vzorky_o)
        nezarazene = _komise_nezarazene_vzorky_existuji(vzorky_o)
        if rozdeleni_tisk and nezarazene:
            tisk_html = f"""
            <div class="tisk-panel-wrap">
                <button type="button" class="btn btn-primary btn-sm" id="btn-tisk-toggle" data-tisk-confirm="1">Tisk pro komise</button>
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
        elif rozdeleni_tisk:
            tisk_html = f'<a class="btn btn-primary btn-sm" href="/tisk/{id}?mode=use" target="_blank">Tisk pro komise</a>'
        else:
            tisk_html = f'<a class="btn btn-primary btn-sm" href="/tisk/{id}" target="_blank">Tisk pro komise</a>'
        k_for_select = max(1, min(n_kom, komise_sel if komise_sel != -1 else 1))
        opt_parts = []
        for i in range(1, n_kom + 1):
            sel_i = " selected" if k_for_select == i else ""
            hk_i = (hlavni_komisar_map.get(i) or "").strip()
            opt_lab = f"Komise č.{i}" + (f" — {hk_i}" if hk_i else "")
            opt_parts.append(f'<option value="{i}"{sel_i}>{escape(opt_lab)}</option>')
        opts_joined = "".join(opt_parts)
        komise_select_html = f"""
            <form method="post" class="form-komise-inline form-komise-body">
                <input type="hidden" name="action" value="set_komise">
                {ph}
                <label class="filter-label" for="sel-komise">Komise</label>
                <select name="komise" id="sel-komise" class="select-komise" onchange="this.form.submit()">{opts_joined}</select>
            </form>
        """

    katalog_tisk_html = ""
    katalog_ekatalog_html = ""
    katalog_mobile_url = ""
    katalog_top_qr_src = ""
    if rezim == "katalog":
        katalog_mobile_url = app_public_base_url() + f"/mobile-katalog/{id}"
        katalog_tisk_html = f'<a class="btn btn-primary btn-sm" href="/katalog_tisk/{id}" target="_blank">Tisk katalogu</a>'
        katalog_ekatalog_html = (
            f'<a class="btn btn-sm" href="{escape(katalog_mobile_url)}" target="_blank" rel="noopener">E-katalog</a>'
        )
        katalog_top_qr_src = (
            "https://api.qrserver.com/v1/create-qr-code/?size=128x128&data="
            + quote(katalog_mobile_url, safe="")
        )

    chrome_b_center_html = ""
    chrome_b_right_html = ""
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
        chrome_b_center_html = filter_row
        chrome_b_right_html = f"""
                    <div class="chrome-tools-row import-help-row-chrome">
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
            """
    elif rezim == "katalog":
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
        chrome_b_center_html = filter_row_k
        chrome_b_right_html = f"""
                    <div class="chrome-tools-row import-help-row-chrome">
                        <div class="title-right-katalog-tools chrome-b-context">{katalog_tisk_html}{katalog_ekatalog_html}</div>
                        <button type="button" class="btn-help" id="btn-help-toggle" title="Nápověda" aria-label="Nápověda">?</button>
                    </div>
            """
    elif rezim == "komise":
        chrome_b_right_html = f"""
                    <div class="chrome-tools-row import-help-row-chrome">
                        <div class="komise-panel-inline chrome-b-context">{tisk_html}</div>
                        <button type="button" class="btn-help" id="btn-help-toggle" title="Nápověda" aria-label="Nápověda">?</button>
                    </div>
            """
    elif rezim == "nastaveni":
        chrome_b_right_html = """
                    <div class="chrome-tools-row import-help-row-chrome">
                        <button type="button" class="btn-help" id="btn-help-toggle" title="Nápověda" aria-label="Nápověda">?</button>
                    </div>
            """

    if rezim == "seznam":
        help_panel_html = """
                <div id="help-panel" class="help-panel help-panel-chrome" role="region" aria-label="Nápověda k sekci">
                    <p><strong>Co je tahle stránka</strong> — jako jedna velká tabulka se všemi víny v degustaci. Řádky seřadíte kliknutím na název sloupce. Když přepnete na <strong>Úpravy</strong>, můžete řádky měnit nebo přidávat nové, podobně jako v Excelu.</p>
                    <p><strong>Co kde najdete</strong> — uprostřed nahoře je <strong>hledání</strong> (jen když je zapnuté <strong>Prohlížení</strong>). Vpravo nahoře můžete <strong>nahrát soubor z Excelu</strong> a vedle toho je tlačítko <strong>?</strong>. Pod názvem degustace jsou <strong>záložky</strong> (kam v aplikaci jste) a přepínač <strong>Prohlížení / Úpravy</strong> (jen číst × můžu měnit).</p>
                    <p><strong>Hledání</strong> — napište slova oddělená mezerou. Zobrazí se jen řádky, které obsahují <em>všechna</em> ta slova najednou (v celém řádku).</p>
                    <p><strong>Nahrání z Excelu</strong> — připravte tabulku (můžete zkopírovat z Excelu a uložit jako CSV). <strong>První řádek s názvy sloupců aplikace nepoužívá</strong> — záleží jen na <strong>pořadí</strong> sloupců zleva: číslo vzorku (můžete vynechat, doplní se samo), název vína, adresa, odrůda, přívlastek, ročník, případně body. Stejný vzorek jako už v seznamu nebo stejný řádek dvakrát v souboru se <strong>nepřidá znovu</strong>.</p>
                    <p><strong>Doporučení</strong> — nejdřív mít v tabulce všechny vzorky (nahrát soubor nebo doplnit ručně), zkontrolovat pořadí a hledání, pak přejít na zadávání bodů u komisí.</p>
                </div>
        """
    elif rezim == "komise":
        help_panel_html = """
                <div id="help-panel" class="help-panel help-panel-chrome" role="region" aria-label="Nápověda k sekci">
                    <p><strong>Co je tahle stránka</strong> — zde se zadávají <strong>body</strong> pro vzorky vybrané komise, podobně jako na papíře v tabulce, jen v počítači.</p>
                    <p><strong>Výběr komise</strong> — v rozbalovacím seznamu je <strong>číslo komise</strong> a pokud to máte vyplněné v <strong>Nastavení → Komise</strong>, i <strong>jméno hlavního komisaře</strong>. Velký nadpis nad tabulkou ukazuje totéž (číslo a případně hlavního komisaře).</p>
                    <p><strong>Členové komise</strong> — pod nadpisem je řádek se jmény všech členů (jak jste je zapsali v nastavení, oddělená čárkami).</p>
                    <p><strong>QR kód pod nadpisem</strong> — když už má degustace v nastavení hodnocení vygenerovaný <strong>tajný odkaz</strong> (token), zobrazí se <strong>čtvercový QR</strong> pro <strong>mobilní zadávání bodů právě této komise</strong>. Komise ho naskenuje telefonem; je to stejná adresa jako u odkazů v nastavení, jen bez klikacího odkazu. Bez tokenu se QR neukáže.</p>
                    <p><strong>Tisk</strong> — vpravo nahoře je tlačítko pro <strong>tisk podkladů</strong> pro komise (podle toho, jestli už máte vzorky rozdělené). Vedle je <strong>?</strong> — tato nápověda.</p>
                    <p><strong>Na mobilu</strong> — po naskenování QR uvidí komise degustaci, číslo komise, jméno hlavního komisaře a tlačítkem <strong>Kontrola</strong> souhrn zadaných bodů.</p>
                    <p><strong>Doporučení</strong> — ověřit rozdělení vzorků do komisí, případně vytisknout podklady, pak zadat body a případné poznámky u komise.</p>
                </div>
        """
    elif rezim == "katalog":
        help_panel_html = """
                <div id="help-panel" class="help-panel help-panel-chrome" role="region" aria-label="Nápověda k sekci">
                    <p><strong>Co je tahle stránka</strong> — náhled toho, jak může vypadat <strong>výpis pro návštěvníky</strong> (pořadí a údaje o víně). Hledání funguje stejně jako u seznamu vzorků, když máte zapnuté <strong>Prohlížení</strong>.</p>
                    <p><strong>Co kde najdete</strong> — <strong>Tisk katalogu</strong> vytiskne dokument. <strong>E-katalog</strong> otevře verzi v prohlížeči (v telefonu nebo počítači; můžete použít QR kód na stránce, pokud ho vidíte). <strong>?</strong> je vpravo nahoře.</p>
                    <p><strong>Doporučení</strong> — projděte pořadí a texty, pak buď vytiskněte, nebo pošlete odkaz na e-katalog.</p>
                </div>
        """
    elif rezim == "nastaveni":
        help_panel_html = """
                <div id="help-panel" class="help-panel help-panel-chrome" role="region" aria-label="Nápověda k sekci">
                    <p><strong>Co je tahle stránka</strong> — „kancelář“ degustace: kolik bude komisí, jak se hodnotí, údaje o vystavovatelích a odrůdách, nastavení katalogu. Citlivější věci (smazání vzorků, ukázkový import) jsou tady zvlášť.</p>
                    <p><strong>Co kde najdete</strong> — vpravo nahoře jen <strong>?</strong>. Přepínač <strong>Prohlížení / Úpravy</strong> je u záložek pod názvem — <strong>měnit a ukládat</strong> můžete až když je <strong>Úpravy</strong>. Uvnitř stránky jsou další záložky (Degustace, Hodnocení, …).</p>
                    <p><strong>Doporučení</strong> — nejdřív si projděte záložky v <strong>Prohlížení</strong>, pak přepněte na <strong>Úpravy</strong> a měňte, co potřebujete; u mazání a importu si vždy přečtěte potvrzovací hlášku.</p>
                </div>
        """
    else:
        help_panel_html = ""

    pk_edit = degustace["pocet_komisi"]
    if pk_edit is None:
        pk_edit = n_kom
    else:
        try:
            pk_edit = int(pk_edit)
        except (TypeError, ValueError):
            pk_edit = n_kom
    pk_edit = max(1, min(10, pk_edit))

    typ_deg = _deg_row_typ_akce(degustace)
    typ_akce_banner_html = ""
    if typ_deg == TYP_AKCE_PRUVODCE:
        typ_akce_banner_html = """
                <div class="typ-akce-banner" role="status">
                    <strong>Průvodce degustací</strong> — samostatné obrazovky pro průvodce se postupně doplní; prozatím používáte stejné rozhraní jako u bodované degustace.
                </div>
        """

    logo_url = url_for("degus_logo")
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
            .typ-akce-banner {{
                margin: 0 0 12px 0;
                padding: 10px 14px;
                border: 1px solid var(--border-strong);
                border-radius: var(--radius-sm);
                background: #f0f4f8;
                color: var(--text);
                font-size: 14px;
                line-height: 1.45;
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
                padding: 4px 16px 4px;
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
            .chrome-head {{
                display: flex;
                flex-direction: column;
                gap: 2px;
                width: 100%;
            }}
            .chrome-row-b {{
                display: grid;
                grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
                gap: 6px 10px;
                align-items: center;
            }}
            .chrome-b-left.title-block {{
                justify-self: start;
                min-width: 0;
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                gap: 10px 12px;
            }}
            .chrome-logo-link {{
                flex-shrink: 0;
                line-height: 0;
                display: flex;
                align-items: center;
            }}
            .chrome-logo-link .app-logo-chrome {{
                height: 5.078125rem;
                width: auto;
                max-height: 5.859375rem;
                object-fit: contain;
                display: block;
            }}
            .chrome-title-stack {{
                min-width: 0;
                display: flex;
                flex-direction: column;
                align-items: flex-start;
                gap: 2px;
            }}
            .chrome-b-left h1.deg-nazev {{
                margin: 0;
                font-size: 1.35rem;
                font-weight: 600;
                letter-spacing: -0.02em;
            }}
            .chrome-b-left .datum {{
                color: var(--text-muted);
                font-size: 0.88rem;
                margin: 0;
            }}
            .chrome-b-center {{
                justify-self: center;
                grid-column: 2;
                min-width: 0;
            }}
            .chrome-b-center .filter-row-tools {{
                justify-content: center;
                margin: 0;
                gap: 6px;
            }}
            .chrome-b-center .filter-row input[type="search"] {{
                padding: 6px 10px;
            }}
            .chrome-b-right {{
                justify-self: end;
                grid-column: 3;
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                justify-content: center;
                gap: 4px;
                min-width: 0;
                position: relative;
            }}
            .chrome-b-right .chrome-b-context {{
                display: inline-flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: flex-end;
                gap: 6px;
            }}
            .chrome-row-nav {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                gap: 6px;
                width: 100%;
                box-sizing: border-box;
                padding: 6px 0 8px;
                margin: 2px 0 0;
                border-top: 1px solid var(--border);
            }}
            .chrome-nav-primary {{
                display: flex;
                flex-wrap: wrap;
                align-items: stretch;
                gap: 6px;
                min-width: 0;
            }}
            .chrome-nav-admin {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                gap: 8px;
                margin-left: auto;
                padding-left: 12px;
                border-left: 1px solid var(--border-strong);
                min-width: 0;
            }}
            .chrome-nav-admin .edit-switch-form {{
                flex-shrink: 0;
            }}
            .form-rezim-tab {{
                display: inline;
                margin: 0;
            }}
            .chrome-nav-primary .form-rezim-tab .settings-tab {{
                width: 100%;
            }}
            .chrome-nav-admin .form-rezim-tab .settings-tab {{
                width: auto;
            }}
            @media (min-width: 420px) {{
                .chrome-nav-primary .form-rezim-tab .settings-tab {{
                    width: auto;
                }}
            }}
            .komise-body-toolbar {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                justify-content: space-between;
                gap: 10px 16px;
                margin: 12px 8px 8px;
                padding: 0 8px;
                box-sizing: border-box;
            }}
            .komise-mobilni-qr {{
                margin: 0 8px 10px;
                padding: 0 8px;
            }}
            .komise-mobilni-qr img {{
                display: block;
                width: 120px;
                height: 120px;
            }}
            .komise-body-title {{
                margin: 0;
                font-size: 1.08rem;
                font-weight: 700;
                color: #223;
                flex: 1;
                min-width: 0;
            }}
            .form-komise-body {{
                margin: 4px 0 0;
                flex-shrink: 0;
            }}
            @media (max-width: 640px) {{
                .form-komise-body {{
                    margin-top: 6px;
                }}
            }}
            .komise-porotci {{
                margin: 0 0 12px;
                font-size: 14px;
                color: var(--text-muted);
                line-height: 1.4;
            }}
            .katalog-top-head {{
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 12px;
                margin: 0 0 10px;
            }}
            .katalog-top-title {{
                margin: 0;
                font-size: 1.08rem;
                color: #223;
                flex: 1;
                min-width: 0;
                line-height: 1.25;
            }}
            .catalog-top-qr-btn {{
                flex-shrink: 0;
                padding: 0;
                border: none;
                background: transparent;
                cursor: pointer;
                line-height: 0;
                font-size: 1.08rem;
            }}
            .catalog-top-qr-img {{
                display: block;
                height: 4em;
                width: 4em;
                object-fit: contain;
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
                align-items: center;
                gap: 4px 10px;
                width: 100%;
                padding: 2px 0 2px;
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
                gap: 6px;
            }}
            .chrome-row-tools .filter-row input[type="search"] {{
                padding: 6px 10px;
            }}
            .chrome-row-tools-right {{
                justify-self: end;
                grid-column: 3;
                display: flex;
                flex-direction: column;
                align-items: flex-end;
                gap: 4px;
                min-width: 0;
            }}
            .import-help-row-chrome {{
                justify-content: flex-end;
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
                align-items: center;
                justify-content: flex-end;
                gap: 6px;
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
            .chrome-b-left.title-block h1.deg-nazev {{
                margin: 0;
                display: block;
            }}
            .title-block .deg-title-name {{ flex: 0 1 auto; min-width: 0; }}
            .title-block .deg-title-sep {{ color: var(--text-muted); font-weight: 400; }}
            .title-block .deg-title-rezim {{ font-size: 1.05rem; font-weight: 600; color: var(--accent); }}
            .title-block .datum {{ color: var(--text-muted); font-size: 0.88rem; margin: 2px 0 0; }}
            .chrome-b-left.title-block .datum {{ margin: 0; }}
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
            .import-help-row,
            .chrome-tools-row {{
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
            .help-panel.help-panel-chrome {{
                width: 100%;
                max-width: none;
                box-sizing: border-box;
                margin-top: 6px;
            }}
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
                padding: 0 16px 16px;
                box-sizing: border-box;
                background: var(--surface);
                border: none;
                border-radius: var(--radius);
                overflow: visible;
                box-shadow: var(--shadow-md);
            }}
            .title-right-katalog-tools {{
                display: inline-flex;
                flex-wrap: wrap;
                align-items: center;
                gap: 6px;
                justify-content: flex-end;
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
            .row-vz-main {{ cursor: pointer; }}
            .cell-vz-detail {{
                background: #f4f6f8 !important;
                font-size: 13px;
                color: var(--text-muted);
                border-top: none !important;
                padding-top: 6px !important;
                padding-bottom: 8px !important;
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
            .settings-tablist {{
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
                margin-bottom: 14px;
            }}
            .settings-tab {{
                font: inherit;
                cursor: pointer;
                padding: 8px 12px;
                border-radius: 6px;
                border: 1px solid var(--border-strong);
                background: var(--surface);
                color: var(--text);
            }}
            .settings-tab:hover {{
                border-color: #b8bec8;
            }}
            .settings-tab.is-active {{
                border-color: var(--accent);
                background: var(--accent-soft);
                color: var(--text);
                font-weight: 600;
            }}
            .settings-panel-tab {{
                display: none;
            }}
            .settings-panel-tab.is-active {{
                display: block;
            }}
            .table-vystav {{
                font-size: 13px;
                width: 100%;
                max-width: 100%;
            }}
            .table-vystav input[type="text"] {{
                width: 100%;
                box-sizing: border-box;
                padding: 6px 8px;
                border: 1px solid var(--border-strong);
                border-radius: 6px;
                font: inherit;
            }}
            .th-actions, .td-actions {{
                width: 7.5rem;
                max-width: 11rem;
                white-space: nowrap;
                text-align: right;
                vertical-align: middle;
            }}
            .col-odruda-short {{
                text-transform: uppercase;
            }}
            select.select-odruda-upper {{
                text-transform: uppercase;
            }}
            .vzorek-odruda-flex {{
                display: flex;
                flex-wrap: wrap;
                align-items: center;
                gap: 6px;
                min-width: 0;
            }}
            table.data-grid td .vzorek-odruda-flex input[type="text"] {{
                width: auto;
                display: inline-block;
                flex: 1 1 5.5rem;
                min-width: 0;
                max-width: 12rem;
                box-sizing: border-box;
            }}
            table.data-grid td .vzorek-odruda-flex select.select-komise {{
                flex: 1 1 5.5rem;
                min-width: 0;
                max-width: 12rem;
                margin-bottom: 0;
                width: auto;
                display: inline-block;
            }}
            tr.row-novy-extra td.td-vzorek-extra,
            tr.row-edit-extra td.td-vzorek-extra {{
                background: var(--accent-soft);
                padding: 8px 10px;
                vertical-align: middle;
            }}
            .vzorek-extra-web,
            .vzorek-extra-pozn {{
                display: flex;
                flex-direction: column;
                align-items: stretch;
                gap: 4px;
                font-size: 13px;
                margin: 0;
            }}
            table.data-grid td .vzorek-extra-web input,
            table.data-grid td .vzorek-extra-pozn input {{
                width: 100%;
                box-sizing: border-box;
                padding: 6px 8px;
                border: 1px solid var(--border-strong);
                border-radius: 6px;
                font: inherit;
                display: block;
            }}
            table.data-grid td .vzorek-extra-web input {{
                min-width: 0;
            }}
            .visually-hidden {{
                position: absolute;
                width: 1px;
                height: 1px;
                padding: 0;
                margin: -1px;
                overflow: hidden;
                clip: rect(0,0,0,0);
                white-space: nowrap;
                border: 0;
            }}
        </style>
    </head>
    <body data-ma-vzorky="{'1' if ma_vzorky else '0'}">
        <div class="fixed-chrome" id="fixed-chrome">
            <div class="fixed-chrome-inner">
                {flash_html}
                {typ_akce_banner_html}
                {katalog_warning_html}
                <div class="chrome-head">
                <div class="chrome-row-b">
                    <div class="chrome-b-left title-block">
                        <a href="/" class="chrome-logo-link" title="Úvodní stránka — Score Taste"><img src="{escape(logo_url)}" class="app-logo app-logo-chrome" alt="Logo" width="235" height="94" decoding="async"></a>
                        <div class="chrome-title-stack">
                            <h1 class="deg-nazev"><span class="deg-title-name">{escape(degustace['nazev'])}</span></h1>
                            <span class="datum">{escape(datum_cz)}</span>
                        </div>
                    </div>
                    <div class="chrome-b-center">{chrome_b_center_html}</div>
                    <div class="chrome-b-right">{chrome_b_right_html}</div>
                </div>
                <div class="chrome-row-nav" role="navigation" aria-label="Sekce degustace">
                    <div class="chrome-nav-primary">
                    <form method="post" class="form-rezim-tab">
                        <input type="hidden" name="action" value="set_rezim">
                        <input type="hidden" name="rezim" value="seznam">
                        {ph}
                        <button type="submit" class="settings-tab{(' is-active' if rezim == 'seznam' else '')}"{' aria-current="page"' if rezim == 'seznam' else ''}>Seznam vzorků</button>
                    </form>
                    <form method="post" class="form-rezim-tab">
                        <input type="hidden" name="action" value="set_rezim">
                        <input type="hidden" name="rezim" value="komise">
                        {ph}
                        <button type="submit" class="settings-tab{(' is-active' if rezim == 'komise' else '')}"{' aria-current="page"' if rezim == 'komise' else ''}>Bodové hodnocení</button>
                    </form>
                    <form method="post" class="form-rezim-tab">
                        <input type="hidden" name="action" value="set_rezim">
                        <input type="hidden" name="rezim" value="katalog">
                        {ph}
                        <button type="submit" class="settings-tab{(' is-active' if rezim == 'katalog' else '')}"{' aria-current="page"' if rezim == 'katalog' else ''}>Katalog</button>
                    </form>
                    </div>
                    <div class="chrome-nav-admin" aria-label="Správa a nastavení">
                            <!-- V katalogu není úprava řádků tabulkou: přepínač Úpravy skrytý, aby nevznikala falešná očekávání. -->
                            {'' if rezim == 'katalog' else f'''
                            <form method="post" class="edit-switch-form">
                                <input type="hidden" name="action" value="set_edit">
                                <input type="hidden" name="edit" value="{'0' if edit_mode else '1'}">
                                {ph}{st_hidden if rezim == 'nastaveni' else ''}
                                <span class="switch-label">{'Úpravy' if edit_mode else 'Prohlížení'}</span>
                                <button type="submit" class="switch-track{' is-on' if edit_mode else ''}" title="Přepnout režim úprav" aria-label="Přepnout režim úprav">
                                    <span class="switch-knob"></span>
                                </button>
                            </form>
                            '''}
                    <form method="post" class="form-rezim-tab">
                        <input type="hidden" name="action" value="set_rezim">
                        <input type="hidden" name="rezim" value="nastaveni">
                        {ph}
                        <button type="submit" class="settings-tab{(' is-active' if rezim == 'nastaveni' else '')}"{' aria-current="page"' if rezim == 'nastaveni' else ''}>Nastavení</button>
                    </form>
                    </div>
                </div>
                {help_panel_html}
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

    if rezim == "nastaveni":
        h_lb, h_mx = _hodnoceni_labels_maxes_from_deg(degustace)
        h_tok = (degustace["hodnoceni_token"] or "").strip() if degustace["hodnoceni_token"] else ""
        base_h = app_public_base_url()
        html += '<div class="settings-panel" id="settings-tabs-root">'
        html += (
            '<div class="settings-tablist" role="tablist">'
            f'<button type="button" class="settings-tab{" is-active" if settings_tab_cur == "deg" else ""}" data-set-tab="deg" role="tab">Degustace</button>'
            f'<button type="button" class="settings-tab{" is-active" if settings_tab_cur == "hodn" else ""}" data-set-tab="hodn" role="tab">Hodnocení</button>'
            f'<button type="button" class="settings-tab{" is-active" if settings_tab_cur == "kom" else ""}" data-set-tab="kom" role="tab">Komise</button>'
            f'<button type="button" class="settings-tab{" is-active" if settings_tab_cur == "kat" else ""}" data-set-tab="kat" role="tab">Katalog</button>'
            f'<button type="button" class="settings-tab{" is-active" if settings_tab_cur == "vys" else ""}" data-set-tab="vys" role="tab">Vystavovatelé</button>'
            f'<button type="button" class="settings-tab{" is-active" if settings_tab_cur == "odr" else ""}" data-set-tab="odr" role="tab">Odrůdy</button>'
            '</div>'
        )
        html += f'<div id="set-tab-deg" class="settings-panel-tab{" is-active" if settings_tab_cur == "deg" else ""}" role="tabpanel">'
        html += '<div class="settings-block"><h2>Počet komisí</h2>'
        if edit_mode:
            html += f"""
            <form method="post" class="settings-row">
                <input type="hidden" name="action" value="set_pocet_komisi">
                {ph_set}
                <label class="filter-label" for="inp-pocet-komisi">Počet</label>
                <input id="inp-pocet-komisi" type="number" name="pocet_komisi" min="1" max="10" value="{pk_edit}"
                    style="width:5rem;padding:8px 10px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;">
                <button class="btn btn-sm btn-primary" type="submit">Uložit</button>
            </form>
            """
        else:
            html += f'<p style="margin:0;">Aktuálně <strong>{pk_edit}</strong> komisí.</p>'
        html += "</div>"
        html += '<div class="settings-block"><h2>Výmaz dat vzorků</h2>'
        if edit_mode:
            html += f"""
            <form method="post" class="settings-row"
                onsubmit="return window.confirm('Opravdu smazat všechny vzorky této degustace?\\n\\nTato akce se nedá vrátit.');">
                <input type="hidden" name="action" value="smaz_vse_vzorky">
                {ph_set}
                <button class="btn btn-sm btn-danger" type="submit">Smazat všechny vzorky</button>
            </form>
            """
        else:
            html += '<p style="margin:0;font-size:13px;color:var(--text-muted);">Výmaz je dostupný pouze v režimu Úpravy.</p>'
        html += "</div>"
        html += '<div class="settings-block"><h2>Import vzorků DEMO</h2>'
        html += (
            '<p style="margin:0 0 10px;font-size:13px;color:var(--text-muted);">'
            "Pokud degustace DEMO neexistuje, založí se s datem 2. 7. 2027. "
            "Poté se smažou všechny vzorky DEMO a načtou se soubory "
            "<code>assets/demo.csv</code> (vzorky), <code>assets/demo_vin.csv</code> (vystavovatelé), "
            "<code>assets/demo_odr.csv</code> (odrůdy).</p>"
        )
        if edit_mode:
            html += f"""
            <form method="post" class="settings-row"
                onsubmit="return window.confirm('Načíst DEMO? Přepíšou se vzorky degustace DEMO a aktualizují se vystavovatelé a odrůdy podle assets/*.csv.');">
                <input type="hidden" name="action" value="import_demo">
                {ph_set}
                <button class="btn btn-sm btn-primary" type="submit">Importovat DEMO</button>
            </form>
            """
        else:
            html += '<p style="margin:0;font-size:13px;color:var(--text-muted);">Import DEMO je dostupný v režimu Úpravy.</p>'
        html += "</div></div>"
        html += f'<div id="set-tab-hodn" class="settings-panel-tab{" is-active" if settings_tab_cur == "hodn" else ""}" role="tabpanel">'
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
                {ph_set}
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
                {ph_set}
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
        html += "</div></div>"
        html += f'<div id="set-tab-kom" class="settings-panel-tab{" is-active" if settings_tab_cur == "kom" else ""}" role="tabpanel">'
        html += '<div class="settings-block"><h2>Porotci / komisaři</h2>'
        html += (
            '<p style="margin:0 0 12px;font-size:13px;color:var(--text-muted);">'
            "U každé komise zadejte hlavního komisaře a případně ostatní členy (jména oddělte čárkami).</p>"
        )
        for k in range(1, n_kom + 1):
            cur_jm = porotci_map.get(k) or ""
            cur_hk = hlavni_komisar_map.get(k) or ""
            if edit_mode:
                html += f"""
                <form method="post" class="settings-row" style="align-items:flex-start;flex-wrap:wrap;">
                    <input type="hidden" name="action" value="porotci_uloz">
                    <input type="hidden" name="komise_cislo" value="{k}">
                    {ph_set}
                    <div style="display:flex;flex-direction:column;gap:8px;flex:1;min-width:220px;max-width:100%;">
                        <label class="filter-label" for="inp-hlavni-set-{k}">Komise č.{k} — hlavní komisař</label>
                        <input id="inp-hlavni-set-{k}" type="text" name="hlavni_komisar" value="{escape(cur_hk)}"
                            placeholder="Např. Ing. Novák" autocomplete="off"
                            style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;">
                        <label class="filter-label" for="inp-por-set-{k}">Členové komise</label>
                        <input id="inp-por-set-{k}" type="text" name="jmena" value="{escape(cur_jm)}"
                            placeholder="Např. Novák, Svobodová, …" autocomplete="off"
                            style="width:100%;box-sizing:border-box;padding:8px 10px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;">
                    </div>
                    <button class="btn btn-sm" type="submit" style="align-self:flex-end;margin-top:22px;">Uložit</button>
                </form>
                """
            else:
                html += f'<p style="margin:8px 0 12px;"><strong>Komise č.{k}</strong> — hlavní komisař: {escape(cur_hk) if cur_hk else "—"}<br>'
                html += f'<span style="font-size:13px;color:var(--text-muted);">Členové:</span> {escape(cur_jm) if cur_jm else "—"}</p>'
        html += "</div>"
        html += f"""<div class="settings-block"><h2>Rozdělení vzorků do komisí (tisk)</h2>
        <p style="margin:0 0 12px;font-size:13px;color:var(--text-muted);">
        Znovu vypočítat přiřazení vzorků ke komisím a otevřít náhled tisku (stejná akce jako „Přegenerovat a tisknout“ u tisku komisí).
        </p>
        <p style="margin:0;"><a class="btn btn-sm btn-primary" href="/tisk/{id}?mode=regen" target="_blank" rel="noopener">Přegenerovat a tisknout</a></p>
        </div></div>"""
        html += f'<div id="set-tab-kat" class="settings-panel-tab{" is-active" if settings_tab_cur == "kat" else ""}" role="tabpanel">'
        html += '<div class="settings-block"><h2>Nastavení katalogu</h2>'
        if edit_mode:
            sel_a4 = " selected" if katalog_format == "A4" else ""
            sel_a5 = " selected" if katalog_format == "A5" else ""
            html += f"""
            <form method="post" class="settings-row">
                <input type="hidden" name="action" value="set_katalog_nastaveni">
                {ph_set}
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
        html += "</div></div>"
        html += f'<div id="set-tab-vys" class="settings-panel-tab{" is-active" if settings_tab_cur == "vys" else ""}" role="tabpanel">'
        html += '<div class="settings-block"><h2>Vystavovatelé</h2>'
        html += (
            '<p style="margin:0 0 12px;font-size:13px;color:var(--text-muted);">'
            "Dlouhodobý seznam vystavovatelů (nezávislý na vzorcích v jednotlivých degustacích).</p>"
        )
        if vystavovatele_rows:
            if edit_mode:
                html += (
                    '<table class="data-grid table-vystav table-settings-rows" style="margin-bottom:12px;">'
                    "<thead><tr><th>Název</th><th>Adresa</th><th>Web</th><th>Mobil</th><th>E-mail</th>"
                    '<th class="th-actions">Akce</th></tr></thead><tbody>'
                )
                for vr in vystavovatele_rows:
                    vid = int(vr["id"])
                    html += f"""
                    <tr>
                        <td><label class="visually-hidden" for="vys-n-{vid}">Název</label>
                        <input id="vys-n-{vid}" type="text" name="nazev" form="vys-u-{vid}" value="{escape(vr['nazev'] or '')}" required
                            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></td>
                        <td><label class="visually-hidden" for="vys-a-{vid}">Adresa</label>
                        <input id="vys-a-{vid}" type="text" name="adresa" form="vys-u-{vid}" value="{escape(vr['adresa'] or '')}"
                            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></td>
                        <td><label class="visually-hidden" for="vys-w-{vid}">Web</label>
                        <input id="vys-w-{vid}" type="text" name="web" form="vys-u-{vid}" value="{escape(vr['web'] or '')}" maxlength="500"
                            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></td>
                        <td><label class="visually-hidden" for="vys-m-{vid}">Mobil</label>
                        <input id="vys-m-{vid}" type="text" name="mobil" form="vys-u-{vid}" value="{escape(vr['mobil'] or '')}"
                            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></td>
                        <td><label class="visually-hidden" for="vys-e-{vid}">E-mail</label>
                        <input id="vys-e-{vid}" type="text" name="mail" form="vys-u-{vid}" value="{escape(vr['mail'] or '')}"
                            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></td>
                        <td class="td-actions">
                            <form id="vys-u-{vid}" method="post" style="display:inline-block;margin:0 4px 0 0;">
                                <input type="hidden" name="action" value="vystavovatel_uloz">
                                <input type="hidden" name="vystavovatel_id" value="{vid}">
                                {ph_set}
                                <button class="btn btn-sm btn-primary" type="submit">Uložit</button>
                            </form>
                            <form method="post" style="display:inline-block;margin:0;"
                                onsubmit="return window.confirm('Smazat tohoto vystavovatele?');">
                                <input type="hidden" name="action" value="vystavovatel_smaz">
                                <input type="hidden" name="vystavovatel_id" value="{vid}">
                                {ph_set}
                                <button class="btn btn-sm btn-danger" type="submit">Smazat</button>
                            </form>
                        </td>
                    </tr>
                    """
                html += "</tbody></table>"
            else:
                html += (
                    '<table class="data-grid table-vystav" style="margin-bottom:12px;">'
                    "<thead><tr><th>Název</th><th>Adresa</th><th>Web</th><th>Mobil</th><th>E-mail</th></tr></thead><tbody>"
                )
                for vr in vystavovatele_rows:
                    html += f"""
                    <tr>
                        <td>{escape(vr["nazev"] or "")}</td>
                        <td>{escape(vr["adresa"] or "")}</td>
                        <td>{escape(vr["web"] or "")}</td>
                        <td>{escape(vr["mobil"] or "")}</td>
                        <td>{escape(vr["mail"] or "")}</td>
                    </tr>
                    """
                html += "</tbody></table>"
        else:
            html += '<p style="margin:0 0 12px;font-size:13px;color:var(--text-muted);">Zatím žádní vystavovatelé.</p>'
        html += "</div>"
        html += '<div class="settings-block" style="margin-top:8px;"><h3 style="font-size:0.95rem;margin:0 0 8px 0;">Nový vystavovatel</h3>'
        if edit_mode:
            html += f"""
            <form method="post" class="settings-row" style="align-items:flex-end;">
                <input type="hidden" name="action" value="vystavovatel_pridat">
                {ph_set}
                <div style="flex:1;min-width:140px;"><label class="filter-label" for="vys-new-n">Název</label>
                <input id="vys-new-n" type="text" name="nazev" required placeholder="Povinné"
                    style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></div>
                <div style="flex:1;min-width:120px;"><label class="filter-label" for="vys-new-a">Adresa</label>
                <input id="vys-new-a" type="text" name="adresa"
                    style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></div>
                <div style="flex:1;min-width:100px;"><label class="filter-label" for="vys-new-w">Web</label>
                <input id="vys-new-w" type="text" name="web" maxlength="500"
                    style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></div>
                <div style="flex:0 0 110px;"><label class="filter-label" for="vys-new-m">Mobil</label>
                <input id="vys-new-m" type="text" name="mobil"
                    style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></div>
                <div style="flex:0 0 140px;"><label class="filter-label" for="vys-new-e">E-mail</label>
                <input id="vys-new-e" type="text" name="mail"
                    style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></div>
                <button class="btn btn-sm btn-primary" type="submit">Přidat</button>
            </form>
            """
        else:
            html += '<p style="margin:0;font-size:13px;color:var(--text-muted);">Přidávání je dostupné v režimu Úpravy.</p>'
        html += "</div>"
        html += '<div class="settings-block"><h3 style="font-size:0.95rem;margin:0 0 8px 0;">Import CSV</h3>'
        html += '<p style="margin:0 0 8px;font-size:12px;color:var(--text-muted);">Sloupce: název, adresa, web, mobil, e-mail (první řádek může být hlavička).</p>'
        if edit_mode:
            html += f"""
            <form method="post" enctype="multipart/form-data" class="settings-row">
                <input type="hidden" name="action" value="vystavovatel_import_csv">
                {ph_set}
                <input type="file" name="soubor" accept=".csv,text/csv">
                <button class="btn btn-sm" type="submit">Importovat</button>
            </form>
            """
        else:
            html += '<p style="margin:0;font-size:13px;color:var(--text-muted);">Import je dostupný v režimu Úpravy.</p>'
        html += "</div>"
        if edit_mode:
            html += f"""
            <div class="settings-block" style="margin-top:10px;">
                <h3 style="font-size:0.95rem;margin:0 0 8px 0;">Smazat všechny vystavovatele</h3>
                <p style="margin:0 0 8px;font-size:12px;color:var(--text-muted);">Odstraní celý seznam vystavovatelů (nezasahuje do vzorků).</p>
                <form method="post" class="settings-row"
                    onsubmit="return window.confirm('Opravdu smazat všechny vystavovatele?');">
                    <input type="hidden" name="action" value="vystavovatele_smaz_vse">
                    {ph_set}
                    <button class="btn btn-sm btn-danger" type="submit">Smazat všechny vystavovatele</button>
                </form>
            </div>
            """
        html += "</div>"
        html += f'<div id="set-tab-odr" class="settings-panel-tab{" is-active" if settings_tab_cur == "odr" else ""}" role="tabpanel">'
        html += '<div class="settings-block"><h2>Zobrazení názvu odrůdy</h2>'
        html += (
            '<p style="margin:0 0 10px;font-size:13px;color:var(--text-muted);">'
            "Desktop katalog (včetně seznamu vzorků, komisí a bodovacího tisku), tisk katalogu (PDF/HTML), mobilní e-katalog — "
            "lze zvlášť zvolit krátký nebo dlouhý název z číselníku.</p>"
        )
        sel_ozk_s = " selected" if oz_katalog == "short" else ""
        sel_ozk_l = " selected" if oz_katalog == "long" else ""
        sel_ozt_s = " selected" if oz_tisk_katalog == "short" else ""
        sel_ozt_l = " selected" if oz_tisk_katalog == "long" else ""
        sel_oze_s = " selected" if oz_ekatalog == "short" else ""
        sel_oze_l = " selected" if oz_ekatalog == "long" else ""
        if edit_mode:
            html += f"""
            <form method="post" class="settings-row" style="flex-wrap:wrap;gap:10px;">
                <input type="hidden" name="action" value="set_odruda_zobrazeni">
                {ph_set}
                <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;">
                    <label class="filter-label" for="sel-oz-kat">Desktop katalog</label>
                    <select id="sel-oz-kat" name="odruda_zob_katalog" class="select-komise">
                        <option value="short"{sel_ozk_s}>Krátký</option>
                        <option value="long"{sel_ozk_l}>Dlouhý</option>
                    </select>
                </div>
                <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;">
                    <label class="filter-label" for="sel-oz-tisk">Tisk katalogu</label>
                    <select id="sel-oz-tisk" name="odruda_zob_tisk" class="select-komise">
                        <option value="short"{sel_ozt_s}>Krátký</option>
                        <option value="long"{sel_ozt_l}>Dlouhý</option>
                    </select>
                </div>
                <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px;">
                    <label class="filter-label" for="sel-oz-eko">E-katalog (mobil)</label>
                    <select id="sel-oz-eko" name="odruda_zob_ekatalog" class="select-komise">
                        <option value="short"{sel_oze_s}>Krátký</option>
                        <option value="long"{sel_oze_l}>Dlouhý</option>
                    </select>
                </div>
                <button class="btn btn-sm btn-primary" type="submit">Uložit</button>
            </form>
            """
        else:
            def _oz_lbl(z):
                return "krátký" if z == "short" else "dlouhý"
            html += (
                f'<p style="margin:0;font-size:13px;">Desktop katalog: <strong>{_oz_lbl(oz_katalog)}</strong>, '
                f'tisk katalogu: <strong>{_oz_lbl(oz_tisk_katalog)}</strong>, e-katalog: <strong>{_oz_lbl(oz_ekatalog)}</strong>.</p>'
            )
        html += "</div>"
        html += '<div class="settings-block"><h2>Číselník odrůd</h2>'
        html += '<p style="margin:0 0 12px;font-size:13px;color:var(--text-muted);">Krátký název se ukládá do vzorku; u výběru z číselníku se synchronizuje s tabulkou.</p>'
        if odrudy_select_rows:
            if edit_mode:
                html += (
                    '<table class="data-grid table-vystav table-settings-rows" style="margin-bottom:12px;">'
                    "<thead><tr><th>Krátký</th><th>Dlouhý</th>"
                    '<th class="th-actions">Akce</th></tr></thead><tbody>'
                )
                for orow in odrudy_select_rows:
                    oid = int(orow["id"])
                    html += f"""
                    <tr>
                        <td class="col-odruda-short"><label class="visually-hidden" for="odr-s-{oid}">Krátký název</label>
                        <input id="odr-s-{oid}" type="text" name="odruda_short" form="odr-u-{oid}" required value="{escape(orow['odruda_short'] or '')}"
                            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;text-transform:uppercase;"></td>
                        <td><label class="visually-hidden" for="odr-l-{oid}">Dlouhý název</label>
                        <input id="odr-l-{oid}" type="text" name="odruda_long" form="odr-u-{oid}" value="{escape(orow['odruda_long'] or '')}"
                            style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></td>
                        <td class="td-actions">
                            <form id="odr-u-{oid}" method="post" style="display:inline-block;margin:0 4px 0 0;">
                                <input type="hidden" name="action" value="odruda_uloz">
                                <input type="hidden" name="odruda_row_id" value="{oid}">
                                {ph_set}
                                <button class="btn btn-sm btn-primary" type="submit">Uložit</button>
                            </form>
                            <form method="post" style="display:inline-block;margin:0;"
                                onsubmit="return window.confirm('Smazat tuto odrůdu z číselníku? Vzorky přejdou na vlastní text.');">
                                <input type="hidden" name="action" value="odruda_smaz">
                                <input type="hidden" name="odruda_row_id" value="{oid}">
                                {ph_set}
                                <button class="btn btn-sm btn-danger" type="submit">Smazat</button>
                            </form>
                        </td>
                    </tr>
                    """
                html += "</tbody></table>"
            else:
                html += (
                    '<table class="data-grid table-vystav" style="margin-bottom:12px;">'
                    "<thead><tr><th>Krátký</th><th>Dlouhý</th></tr></thead><tbody>"
                )
                for orow in odrudy_select_rows:
                    html += f"""
                    <tr>
                        <td class="col-odruda-short">{escape((orow["odruda_short"] or "").upper())}</td>
                        <td>{escape(orow["odruda_long"] or "")}</td>
                    </tr>
                    """
                html += "</tbody></table>"
        else:
            html += '<p style="margin:0 0 12px;font-size:13px;color:var(--text-muted);">Zatím žádné odrůdy v číselníku.</p>'
        html += '<div class="settings-block" style="margin-top:8px;"><h3 style="font-size:0.95rem;margin:0 0 8px 0;">Nová odrůda</h3>'
        if edit_mode:
            html += f"""
            <form method="post" class="settings-row" style="align-items:flex-end;flex-wrap:wrap;">
                <input type="hidden" name="action" value="odruda_pridat">
                {ph_set}
                <div style="flex:1;min-width:140px;"><label class="filter-label" for="odr-new-s">Krátký</label>
                <input id="odr-new-s" type="text" name="odruda_short" required placeholder="např. MT"
                    style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;text-transform:uppercase;"></div>
                <div style="flex:1;min-width:180px;"><label class="filter-label" for="odr-new-l">Dlouhý</label>
                <input id="odr-new-l" type="text" name="odruda_long" placeholder="volitelně"
                    style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid var(--border-strong);border-radius:6px;font:inherit;"></div>
                <button class="btn btn-sm btn-primary" type="submit">Přidat</button>
            </form>
            """
        else:
            html += '<p style="margin:0;font-size:13px;color:var(--text-muted);">Přidávání je dostupné v režimu Úpravy.</p>'
        html += "</div>"
        html += '<div class="settings-block"><h3 style="font-size:0.95rem;margin:0 0 8px 0;">Import CSV</h3>'
        html += '<p style="margin:0 0 8px;font-size:12px;color:var(--text-muted);">Sloupce: krátký název, dlouhý název (první řádek může být hlavička).</p>'
        if edit_mode:
            html += f"""
            <form method="post" enctype="multipart/form-data" class="settings-row">
                <input type="hidden" name="action" value="odruda_import_csv">
                {ph_set}
                <input type="file" name="soubor" accept=".csv,text/csv">
                <button class="btn btn-sm" type="submit">Importovat</button>
            </form>
            """
        else:
            html += '<p style="margin:0;font-size:13px;color:var(--text-muted);">Import je dostupný v režimu Úpravy.</p>'
        html += "</div>"
        if edit_mode:
            html += f"""
            <div class="settings-block" style="margin-top:10px;">
                <h3 style="font-size:0.95rem;margin:0 0 8px 0;">Smazat všechny odrůdy</h3>
                <p style="margin:0 0 8px;font-size:12px;color:var(--text-muted);">Odstraní celý číselník; u vzorků se zruší vazba na odrůdu (zůstane vlastní text).</p>
                <form method="post" class="settings-row"
                    onsubmit="return window.confirm('Opravdu smazat všechny odrůdy? Vzorky ztratí výběr z číselníku.');">
                    <input type="hidden" name="action" value="odrudy_smaz_vse">
                    {ph_set}
                    <button class="btn btn-sm btn-danger" type="submit">Smazat všechny odrůdy</button>
                </form>
            </div>
            """
        html += "</div></div>"
        html += """
        <script>
        (function(){
            var root = document.getElementById("settings-tabs-root");
            if (!root) return;
            root.querySelectorAll(".settings-tab").forEach(function(btn){
                btn.addEventListener("click", function(){
                    var name = btn.getAttribute("data-set-tab");
                    root.querySelectorAll(".settings-tab").forEach(function(x){
                        x.classList.toggle("is-active", x === btn);
                    });
                    root.querySelectorAll(".settings-panel-tab").forEach(function(p){
                        p.classList.toggle("is-active", p.id === "set-tab-" + name);
                    });
                    document.querySelectorAll('input[name="settings_tab"]').forEach(function(inp){
                        inp.value = name;
                    });
                });
            });
        })();
        </script>
        """
        html += "</div>"
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
            odr = (_odruda_display(v, oz_katalog) or "Nezařazeno").strip() or "Nezařazeno"
            by_odruda.setdefault(odr, []).append(v)
        odrudy_sorted = sorted(by_odruda.keys(), key=lambda x: x.casefold())
        for odr in odrudy_sorted:
            by_odruda[odr].sort(key=lambda v: ((v["nazev"] or "").casefold(), v["cislo"]))

        html += f"""
            <div style="padding:14px 0 8px;">
                <div class="katalog-top-head">
                    <h2 class="katalog-top-title">TOP {katalog_top_x} vzorků podle pořadí</h2>
                    <button type="button" class="catalog-top-qr-btn" onclick="window.open(this.getAttribute('data-url'), '_blank')"
                        data-url="{escape(katalog_mobile_url)}" aria-label="Otevřít mobilní e-katalog" title="Mobilní e-katalog">
                        <img class="catalog-top-qr-img" src="{escape(katalog_top_qr_src)}" alt="" width="128" height="128">
                    </button>
                </div>
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
                            <td>{escape(_odruda_display(v, oz_katalog))}</td>
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
        html += '<div style="padding:6px 0 16px;"><h2 style="margin:0 0 10px;font-size:1.08rem;color:#223;">Katalog podle odrůd</h2>'
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

        por_k = (porotci_map.get(komise_sel) or "").strip()
        por_line = escape(por_k) if por_k else "—"

        hk_sel = (hlavni_komisar_map.get(komise_sel) or "").strip()
        tit_kom = f"Bodové hodnocení — Komise č. {komise_sel}"
        if hk_sel:
            tit_kom += f" — {hk_sel}"

        komise_qr_block = ""
        h_tok_k = (degustace["hodnoceni_token"] or "").strip()
        if h_tok_k:
            base_h_q = app_public_base_url()
            k_eff_q = max(1, min(n_kom, komise_sel))
            mob_u_q = f"{base_h_q}/hodnoceni/{id}/{k_eff_q}?t={quote(h_tok_k, safe='')}"
            qr_u_q = f"https://api.qrserver.com/v1/create-qr-code/?size=120x120&data={quote(mob_u_q, safe='')}"
            komise_qr_block = f'<div class="komise-mobilni-qr"><img src="{escape(qr_u_q)}" width="120" height="120" alt=""></div>'

        html += f"""
            <div class="komise-body-toolbar">
                <h2 class="komise-body-title">{escape(tit_kom)}</h2>
                {komise_select_html}
            </div>
            {komise_qr_block}
            <p class="komise-porotci"><strong>Členové komise:</strong> {por_line}</p>
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
            poz_txt = v["poznamka_komise"] or ""
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
                    <td class="td-clip">{escape(_odruda_display(v, oz_katalog))}</td>
                    <td class="td-clip">{escape(v["privlastek"] or "")}</td>
                    <td class="td-clip">{escape(v["rocnik"] or "")}</td>
                    <td><input class="in-score" type="text" inputmode="decimal" name="body_barva" form="ksave-{vid}" id="barva-{vid}" value="{pv_ba}" autocomplete="off"></td>
                    <td><input class="in-score" type="text" inputmode="decimal" name="body_cistota" form="ksave-{vid}" value="{pv_bc}" autocomplete="off"></td>
                    <td><input class="in-score" type="text" inputmode="decimal" name="body_vune" form="ksave-{vid}" value="{pv_bv}" autocomplete="off"></td>
                    <td><input class="in-score" type="text" inputmode="decimal" name="body_chut" form="ksave-{vid}" value="{pv_bch}" autocomplete="off"></td>
                    <td class="td-celkem" id="kom-celkem-{vid}">{celkem_txt}</td>
                    <td class="td-pozn">
                        <div class="pozn-input-wrap">
                            <input type="text" class="pozn-input" name="poznamka_komise" form="ksave-{vid}" value="{escape(poz_txt)}" autocomplete="off">
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
                    <td class="td-clip">{escape(_odruda_display(v, oz_katalog))}</td>
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
            html += f"""
                <tr class="row-novy-vzorek">
                    <td class="cell-novy" title="Číslo vzorku doplní systém po uložení">+</td>
                    <td><input name="nazev" form="form-pridej" autocomplete="off" placeholder="Jméno / výrobce"></td>
                    <td><input name="adresa" form="form-pridej" autocomplete="off" placeholder="Obec"></td>
                    <td style="min-width:8rem;"><div class="vzorek-odruda-flex"><select name="odruda_id" form="form-pridej" class="select-komise select-odruda-upper">{_odruda_select_options()}</select>
                    <input name="odruda" form="form-pridej" autocomplete="off" placeholder="Vlastní odrůda" type="text"></div></td>
                    <td><input name="privlastek" form="form-pridej" autocomplete="off" placeholder="Např. MZV"></td>
                    <td><input name="rocnik" form="form-pridej" autocomplete="off" placeholder="Ročník"></td>
                    <td><button class="btn btn-sm" type="submit" form="form-pridej">Přidat</button></td>
                </tr>
                <tr class="row-novy-extra">
                    <td class="td-vzorek-extra"></td>
                    <td colspan="2" class="td-vzorek-extra">
                        <label class="vzorek-extra-web">Web
                            <input name="web" form="form-pridej" type="url" inputmode="url" autocomplete="off" placeholder="https://…" maxlength="500"></label>
                    </td>
                    <td colspan="3" class="td-vzorek-extra">
                        <label class="vzorek-extra-pozn">Poznámka
                            <input name="poznamka_vzorek" form="form-pridej" type="text" autocomplete="off" placeholder="Volitelná poznámka ke vzorku" maxlength="500"></label>
                    </td>
                    <td class="td-vzorek-extra"></td>
                </tr>
            """

        for v in vzorky_sorted:
            body_zobrazeni = format_body_hodnota(v["body"])

            if edit_mode:
                vid = v["id"]
                if edit_row_id and vid == edit_row_id:
                    w_e = escape(v["web"] or "")
                    pz_e = escape(v["poznamka_vzorek"] or "")
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
                    <td style="min-width:8rem;"><div class="vzorek-odruda-flex"><select name="odruda_id" form="form-edit-{vid}" class="select-komise select-odruda-upper">{_odruda_select_options(v["odruda_id"])}</select>
                    <input name="odruda" form="form-edit-{vid}" autocomplete="off" value="{escape(v["odruda"] or "")}" placeholder="Vlastní" type="text"></div></td>
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
                <tr class="row-edit-extra">
                    <td class="td-vzorek-extra"></td>
                    <td colspan="2" class="td-vzorek-extra">
                        <label class="vzorek-extra-web">Web
                            <input name="web" form="form-edit-{vid}" type="url" inputmode="url" autocomplete="off" placeholder="https://…" value="{w_e}" maxlength="500"></label>
                    </td>
                    <td colspan="3" class="td-vzorek-extra">
                        <label class="vzorek-extra-pozn">Poznámka
                            <input name="poznamka_vzorek" form="form-edit-{vid}" type="text" autocomplete="off" placeholder="Poznámka ke vzorku" value="{pz_e}" maxlength="500"></label>
                    </td>
                    <td class="td-vzorek-extra"></td>
                </tr>
                    """
                else:
                    html += f"""
                <tr>
                    <td>{v["cislo"]}</td>
                    <td>{escape(v["nazev"] or "")}</td>
                    <td>{escape(v["adresa"] or "")}</td>
                    <td>{escape(_odruda_display(v, oz_katalog))}</td>
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
                web_cell = _fmt_web_link_html(v["web"])
                pz_plain = (v["poznamka_vzorek"] or "").strip()
                pz_cell = escape(pz_plain) if pz_plain else "—"
                vid = int(v["id"])
                html += f"""
                <tr class="row-vz-main" data-vid="{vid}" title="Dvojklikem rozbalit Web a poznámku">
                    <td class="poradi">{poradi_cell}</td>
                    <td>{v["cislo"]}</td>
                    <td>{escape(v["nazev"] or "")}</td>
                    <td>{escape(v["adresa"] or "")}</td>
                    <td>{escape(_odruda_display(v, oz_katalog))}</td>
                    <td>{escape(v["privlastek"] or "")}</td>
                    <td>{escape(v["rocnik"] or "")}</td>
                    <td>{body_zobrazeni if body_zobrazeni else "—"}</td>
                </tr>
                <tr class="row-vz-detail" id="vz-det-{vid}" style="display:none;">
                    <td colspan="8" class="cell-vz-detail"><strong>Web:</strong> {web_cell}
                        &nbsp;&nbsp; <strong>Poznámka:</strong> {pz_cell}</td>
                </tr>
                """

        html += """
                </tbody>
            </table>
        """
        if not edit_mode:
            html += """
        <script>
        (function () {
          document.querySelectorAll(".row-vz-main").forEach(function (tr) {
            tr.addEventListener("dblclick", function () {
              var id = tr.getAttribute("data-vid");
              var d = document.getElementById("vz-det-" + id);
              if (!d) return;
              d.style.display = d.style.display === "table-row" ? "none" : "table-row";
            });
          });
        })();
        </script>
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
                    var willOpen = !panelTisk.classList.contains('is-open');
                    if (willOpen && btnTisk.getAttribute('data-tisk-confirm') === '1') {{
                        var ok = window.confirm(
                            'Rozdělení vzorků do komisí už existuje a některé vzorky zatím nemají přiřazenou komisi. ' +
                            'Chcete pokračovat k výběru tisku?'
                        );
                        if (!ok) return;
                    }}
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


def _html_hodnoceni_mobilni(deg, vz_all, komise_cislo, por_txt, degustace_id, hlavni_komisar=""):
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
        "hlavniKomisar": (hlavni_komisar or "").strip(),
        "labels": labels,
        "maxes": maxes,
        "vzorky": [_vzorek_hodnoceni_payload(v, deg) for v in vz_all],
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
.app {{ max-width: 520px; margin: 0 auto; min-height: 100vh; padding-bottom: calc(88px + env(safe-area-inset-bottom, 0px)); }}
.top {{ position: sticky; top: 0; z-index: 20; background: #fff; border-bottom: 1px solid var(--border);
  padding: 10px 12px 8px; box-shadow: 0 1px 0 rgba(0,0,0,0.04); }}
.top-row1 {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
.top-row1 h1 {{ margin: 0; font-size: 17px; line-height: 1.25; flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
.btn-kontrola {{ flex-shrink: 0; padding: 6px 10px; font-size: 12px; font-weight: 600; white-space: nowrap; }}
.top-row2 {{ display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-top: 6px; font-size: 12px; color: var(--muted); line-height: 1.3; }}
.top-row2-right {{ font-weight: 600; color: var(--text); flex-shrink: 0; }}
.btn {{ border: 1px solid var(--border); background: #fff; border-radius: 8px; padding: 8px 12px; font-size: 13px; font-weight: 600; cursor: pointer; }}
.btn-primary {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
.btn:disabled {{ opacity: 0.45; cursor: not-allowed; }}
.card {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; margin: 8px 12px; padding: 12px; }}
.sample-row {{ display: flex; justify-content: space-between; align-items: baseline; gap: 8px; margin-bottom: 10px; }}
.cv {{ font-size: 28px; font-weight: 800; color: var(--accent); margin: 0; flex-shrink: 0; }}
.sample-meta {{ font-weight: 700; font-size: 15px; text-align: right; flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
.crit {{ margin: 10px 0 14px; }}
.crit-sl {{
  display: grid;
  grid-template-columns: 44px 1fr 44px;
  gap: 8px;
  align-items: center;
  grid-template-areas:
    ". head ."
    "l track r";
}}
.crit-head {{
  grid-area: head;
  display: flex; justify-content: space-between; align-items: center; gap: 8px;
  min-width: 0;
}}
.crit-lbl-text {{ font-size: 13px; color: var(--text); flex: 1; min-width: 0; line-height: 1.3; }}
.crit-inp {{
  width: 4.25rem; flex-shrink: 0; text-align: right; font-size: 15px; font-weight: 700;
  padding: 5px 8px; border: 1px solid var(--border); border-radius: 8px; background: #fff; font-family: inherit;
}}
.crit-sl .sl-arr-l {{ grid-area: l; }}
.crit-sl .sl-track-wrap {{ grid-area: track; }}
.crit-sl .sl-arr-r {{ grid-area: r; }}
.sl-arr {{
  flex-shrink: 0; width: 44px; height: 44px; min-width: 44px; min-height: 44px;
  border-radius: 50%; border: 1px solid #d0d5dc; background: #fff; color: #8892a0;
  font-size: 22px; font-weight: 700; line-height: 1; cursor: pointer; padding: 0; display: flex;
  align-items: center; justify-content: center; box-shadow: 0 1px 2px rgba(0,0,0,0.06);
}}
.sl-arr:active {{ background: #f0f2f5; }}
.sl-track-wrap {{
  flex: 1; min-width: 0; height: 44px; display: flex; align-items: center; position: relative;
  touch-action: none; cursor: pointer;
}}
.sl-track {{
  position: relative; width: 100%; height: 10px; border-radius: 999px; background: #dde2e8;
}}
.sl-fill {{
  position: absolute; left: 0; top: 0; bottom: 0; border-radius: 999px; background: linear-gradient(90deg, #c5ccd6, #aeb6c2);
  pointer-events: none;
}}
.sl-thumb {{
  position: absolute; top: 50%; width: 36px; height: 24px; margin-left: 0;
  transform: translate(-50%, -50%); border-radius: 10px; background: #4a5568;
  box-shadow: 0 2px 6px rgba(0,0,0,0.18); cursor: grab; touch-action: none; pointer-events: auto;
  display: flex; align-items: center; justify-content: center;
}}
.sl-thumb:active {{ cursor: grabbing; }}
.sl-grip {{
  width: 14px; height: 12px;
  background: repeating-linear-gradient(90deg, rgba(255,255,255,0.95) 0 2px, transparent 2px 4px);
  opacity: 0.95; border-radius: 1px;
}}
.sum-line {{ margin: 12px 0 0; font-size: 16px; font-weight: 700; color: var(--accent); text-align: center; }}
.hint {{ font-size: 12px; color: var(--muted); margin: 8px 12px 0; line-height: 1.35; }}
.foot-bar {{
  position: sticky; bottom: 0; z-index: 30; display: flex; align-items: stretch; justify-content: space-between;
  gap: 8px; padding: 10px 12px; padding-bottom: calc(10px + env(safe-area-inset-bottom, 0px));
  background: linear-gradient(180deg, rgba(242,244,246,0.95) 0%, var(--bg) 12%); border-top: 1px solid var(--border);
}}
.foot-bar .foot-nav {{
  min-width: 48px; width: 48px; min-height: 48px; border-radius: 10px; border: 1px solid var(--border);
  background: #fff; font-size: 26px; font-weight: 700; line-height: 1; cursor: pointer; padding: 0; color: var(--text);
}}
.foot-mid {{ flex: 1; display: flex; justify-content: center; align-items: stretch; min-width: 0; }}
.foot-bar .foot-save {{
  width: 100%; max-width: 100%; min-height: 48px; border-radius: 10px; font-size: 16px; font-weight: 700;
}}
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
    <div class="top-row1">
      <h1 id="hn-title">{title}</h1>
      <button type="button" class="btn btn-kontrola" id="hn-check" aria-label="Kontrola hodnocení komise">Kontrola</button>
    </div>
    <div class="top-row2">
      <span id="hn-row2-left"></span>
      <span id="hn-count" class="top-row2-right"></span>
    </div>
  </div>
  <div id="hn-main"></div>
  <p class="hint" id="hn-hint"></p>
  <div class="foot-bar" id="hn-foot">
    <button type="button" class="foot-nav" id="hn-prev" aria-label="Předchozí vzorek">‹</button>
    <div class="foot-mid">
      <button type="button" class="btn btn-primary foot-save" id="hn-save">Uložit</button>
      <button type="button" class="btn btn-primary foot-save" id="hn-edit" style="display:none">Upravit</button>
    </div>
    <button type="button" class="foot-nav" id="hn-next" aria-label="Další vzorek">›</button>
  </div>
</div>
<div class="modal-bg" id="hn-modal-bg"><div class="modal">
  <h3 id="hn-modal-title">Kontrola</h3>
  <p id="hn-modal-kom" style="margin:0 0 10px;font-size:13px;color:#667084;line-height:1.35;"></p>
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
  function esc(s) {
    if (s == null) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
  }
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
  function snap01Val(v, mx) {
    v = Math.round(v * 10) / 10;
    if (v < 0) v = 0;
    if (v > mx) v = mx;
    return v;
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

  function komiseSubline() {
    var k = BOOT.komise;
    var h = (BOOT.hlavniKomisar || "").trim();
    return h ? ("Komise č. " + k + " — " + h) : ("Komise č. " + k);
  }
  function renderTop() {
    el("hn-row2-left").textContent = BOOT.datumCz + " · " + komiseSubline();
    el("hn-count").textContent = "Hodnoceno " + BOOT.x + " z " + BOOT.y;
    var mk = el("hn-modal-kom");
    if (mk) mk.textContent = komiseSubline();
  }

  function renderFooter() {
    var c = cur();
    var b = c.b;
    var lockedUI = editLocked && !dirty;
    var saveBtn = el("hn-save");
    var editBtn = el("hn-edit");
    if (lockedUI) {
      saveBtn.style.display = "none";
      editBtn.style.display = "block";
    } else {
      saveBtn.style.display = "block";
      editBtn.style.display = "none";
      saveBtn.disabled = !allFilledValid(b);
    }
  }

  function stepFine(i, delta) {
    if (editLocked && !dirty) return;
    var c = cur();
    var mx = BOOT.maxes[i];
    var v = c.b[i];
    if (v == null) v = 0;
    c.b[i] = snap01Val(v + delta, mx);
    dirty = true;
    applyCritVisual(i);
    updateSumOnly();
    updateFillHint();
    renderFooter();
  }

  function applyCritVisual(i) {
    var main = el("hn-main");
    if (!main) return;
    var mx = BOOT.maxes[i];
    var v = cur().b[i];
    var inp = main.querySelector('.crit-inp[data-i="' + i + '"]');
    var fill = main.querySelector('.sl-fill[data-i="' + i + '"]');
    var thumb = main.querySelector('.sl-thumb[data-i="' + i + '"]');
    if (inp) inp.value = v == null ? "" : fmtNum(v);
    var rv = v == null ? 0 : v;
    if (rv < 0) rv = 0;
    if (rv > mx) rv = mx;
    var pct = mx > 0 ? (rv / mx) * 100 : 0;
    if (fill) fill.style.width = pct + "%";
    if (thumb) thumb.style.left = pct + "%";
  }

  function updateSumOnly() {
    var sl = el("hn-sum-line");
    if (!sl) return;
    var sm = sumB(cur().b);
    sl.textContent = "Celkem: " + (sm == null ? "—" : fmtNum(sm));
  }

  function updateFillHint() {
    var main = el("hn-main");
    if (!main) return;
    var lockedUI = editLocked && !dirty;
    var need = !lockedUI && !allFilledValid(cur().b);
    var ex = main.querySelector(".hn-fill-hint");
    if (need && !ex) {
      var p = document.createElement("p");
      p.className = "hint hn-fill-hint";
      p.style.margin = "8px 12px 0";
      p.textContent = "Vyplňte všechna čtyři kritéria v povoleném rozsahu.";
      main.appendChild(p);
    } else if (!need && ex) {
      ex.parentNode.removeChild(ex);
    }
  }

  function onCritInputBlur(ev) {
    if (editLocked && !dirty) return;
    var inp = ev.target;
    var i = parseInt(inp.getAttribute("data-i"), 10);
    var mx = BOOT.maxes[i];
    var raw = (inp.value || "").trim();
    if (raw === "") {
      cur().b[i] = null;
    } else {
      var n = parseFloat(raw.replace(",", "."));
      if (n !== n) {
        applyCritVisual(i);
        return;
      }
      cur().b[i] = snap01Val(n, mx);
    }
    dirty = true;
    applyCritVisual(i);
    updateSumOnly();
    updateFillHint();
    renderFooter();
  }

  function onTrackPointerDown(ev) {
    if (editLocked && !dirty) return;
    var wrap = ev.currentTarget;
    if (!wrap.getAttribute("data-drag")) return;
    var i = parseInt(wrap.getAttribute("data-i"), 10);
    var track = wrap.querySelector(".sl-track");
    if (!track) return;
    ev.preventDefault();
    function move(ev2) {
      if (editLocked && !dirty) return;
      var rect = track.getBoundingClientRect();
      var mx = BOOT.maxes[i];
      var x = Math.max(0, Math.min(rect.width, ev2.clientX - rect.left));
      var ratio = rect.width > 0 ? x / rect.width : 0;
      cur().b[i] = snap01Val(ratio * mx, mx);
      dirty = true;
      applyCritVisual(i);
      updateSumOnly();
      updateFillHint();
      renderFooter();
    }
    move(ev);
    function up() {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      window.removeEventListener("pointercancel", up);
    }
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    window.addEventListener("pointercancel", up);
  }

  function setupCardHandlers() {
    var main = el("hn-main");
    if (!main) return;
    var al = main.querySelectorAll(".sl-arr-l");
    var ar = main.querySelectorAll(".sl-arr-r");
    var ai;
    for (ai = 0; ai < al.length; ai++) {
      al[ai].onclick = function (ev) {
        stepFine(parseInt(ev.currentTarget.getAttribute("data-i"), 10), -0.1);
      };
    }
    for (ai = 0; ai < ar.length; ai++) {
      ar[ai].onclick = function (ev) {
        stepFine(parseInt(ev.currentTarget.getAttribute("data-i"), 10), 0.1);
      };
    }
    var inps = main.querySelectorAll(".crit-inp");
    for (ai = 0; ai < inps.length; ai++) {
      inps[ai].onblur = onCritInputBlur;
      inps[ai].onkeydown = function (e) {
        if (e.key === "Enter") e.target.blur();
      };
    }
    var wraps = main.querySelectorAll(".sl-track-wrap[data-drag]");
    for (ai = 0; ai < wraps.length; ai++) {
      wraps[ai].onpointerdown = onTrackPointerDown;
    }
  }

  function renderMain() {
    syncLockState();
    var c = cur();
    var b = c.b;
    var lockedUI = editLocked && !dirty;
    var html = '<div class="card">';
    html += '<div class="sample-row">';
    html += '<span class="cv">č.v. ' + c.cislo + '</span>';
    var meta = esc(c.odruda || "—") + " · " + esc(c.privlastek || "—") + " · " + esc(c.rocnik || "—");
    var metaTitle = esc((c.odruda || "") + " · " + (c.privlastek || "") + " · " + (c.rocnik || ""));
    html += '<span class="sample-meta" title="' + metaTitle + '">' + meta + '</span>';
    html += '</div>';
    for (var i = 0; i < 4; i++) {
      var mx = BOOT.maxes[i];
      var v = b[i];
      var rv = (v == null) ? 0 : v;
      if (rv < 0) rv = 0;
      if (rv > mx) rv = mx;
      var pct = mx > 0 ? (rv / mx) * 100 : 0;
      var dis = lockedUI ? " disabled" : "";
      var dragAttr = lockedUI ? "" : ' data-drag="1"';
      html += '<div class="crit">';
      html += '<div class="crit-sl">';
      html += '<div class="crit-head">';
      html += '<span class="crit-lbl-text">' + esc(BOOT.labels[i]) + " (max " + mx + ')</span>';
      html += '<input type="text" class="crit-inp" data-i="' + i + '" inputmode="decimal" autocomplete="off"' + dis;
      html += ' value="' + ((b[i] == null) ? "" : fmtNum(b[i])) + '" />';
      html += '</div>';
      html += '<button type="button" class="sl-arr sl-arr-l" data-i="' + i + '"' + dis + ' aria-label="Odečíst 0,1 bodu">‹</button>';
      html += '<div class="sl-track-wrap" data-i="' + i + '"' + dragAttr + '>';
      html += '<div class="sl-track">';
      html += '<div class="sl-fill" data-i="' + i + '" style="width:' + pct + '%;"></div>';
      html += '<div class="sl-thumb" data-i="' + i + '" style="left:' + pct + '%;"><span class="sl-grip"></span></div>';
      html += '</div></div>';
      html += '<button type="button" class="sl-arr sl-arr-r" data-i="' + i + '"' + dis + ' aria-label="Přičíst 0,1 bodu">›</button>';
      html += '</div></div>';
    }
    var sm = sumB(b);
    html += '<p class="sum-line" id="hn-sum-line">Celkem: ' + (sm == null ? "—" : fmtNum(sm)) + '</p>';
    html += '</div>';
    if (!lockedUI && !allFilledValid(b)) {
      html += '<p class="hint hn-fill-hint" style="margin:8px 12px 0;">Vyplňte všechna čtyři kritéria v povoleném rozsahu.</p>';
    }
    el("hn-main").innerHTML = html;
    el("hn-hint").textContent = dirty ? "Máte neuložené změny u tohoto vzorku." : "";
    setupCardHandlers();
    renderFooter();
  }

  el("hn-edit").onclick = function () {
    if (!confirm("Upravit již uložené hodnocení?")) return;
    overrideEdit = true;
    dirty = false;
    renderMain();
  };
  el("hn-save").onclick = save;

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
            """
            SELECT v.*, o.odruda_short AS odruda_join_short, o.odruda_long AS odruda_join_long
            FROM vzorky v
            LEFT JOIN odrudy o ON v.odruda_id = o.id
            WHERE v.id = ? AND v.degustace_id = ?
            """,
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
            row["poznamka_komise"],
        )
        conn.commit()
        v_up = conn.execute(
            """
            SELECT v.*, o.odruda_short AS odruda_join_short, o.odruda_long AS odruda_join_long
            FROM vzorky v
            LEFT JOIN odrudy o ON v.odruda_id = o.id
            WHERE v.id = ?
            """,
            (vid,),
        ).fetchone()
        vz_all = conn.execute(
            """
            SELECT v.*, o.odruda_short AS odruda_join_short, o.odruda_long AS odruda_join_long
            FROM vzorky v
            LEFT JOIN odrudy o ON v.odruda_id = o.id
            WHERE v.degustace_id = ? AND v.komise_cislo = ?
            ORDER BY v.cislo
            """,
            (degustace_id, komise_cislo),
        ).fetchall()
        conn.close()
        return jsonify(
            ok=True,
            vzorek=_vzorek_hodnoceni_payload(v_up, deg),
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
        SELECT v.*, o.odruda_short AS odruda_join_short, o.odruda_long AS odruda_join_long
        FROM vzorky v
        LEFT JOIN odrudy o ON v.odruda_id = o.id
        WHERE v.degustace_id = ? AND v.komise_cislo = ?
        ORDER BY v.cislo
        """,
        (degustace_id, komise_cislo),
    ).fetchall()
    pr = conn.execute(
        """
        SELECT jmena, hlavni_komisar FROM komise_porotci
        WHERE degustace_id = ? AND komise_cislo = ?
        """,
        (degustace_id, komise_cislo),
    ).fetchone()
    conn.close()
    por_txt = (pr["jmena"] or "").strip() if pr else ""
    hlavni_txt = (pr["hlavni_komisar"] or "").strip() if pr else ""
    return _html_hodnoceni_mobilni(deg, vz_all, komise_cislo, por_txt, degustace_id, hlavni_txt)


@app.route("/mobile-katalog/<int:id>")
def mobile_katalog(id):
    conn = get_connection()
    degustace = conn.execute(
        "SELECT * FROM degustace WHERE id = ?",
        (id,),
    ).fetchone()
    vzorky = conn.execute(VZORKY_SELECT_JOIN, (id,)).fetchall()
    porotci_rows = conn.execute(
        "SELECT komise_cislo, jmena FROM komise_porotci WHERE degustace_id=? ORDER BY komise_cislo",
        (id,),
    ).fetchall()
    odrudy_cat = conn.execute(
        "SELECT odruda_short, odruda_long FROM odrudy ORDER BY odruda_short COLLATE NOCASE"
    ).fetchall()
    conn.close()

    rank_all = [v for v in vzorky if v["body"] is not None]
    rank_all.sort(key=lambda v: (-float(v["body"]), v["cislo"]))
    poradi_map = {v["id"]: i + 1 for i, v in enumerate(rank_all)}

    oz_mob = _deg_oz_field(degustace, "odruda_zob_ekatalog")

    abbr_entries = []
    for r in odrudy_cat:
        abbr_entries.append({
            "abbr": (r["odruda_short"] or "").strip(),
            "full": (r["odruda_long"] or r["odruda_short"] or "").strip(),
        })
    abbr_entries.sort(key=lambda x: x["abbr"].casefold())

    porotci_entries = []
    for r in porotci_rows:
        porotci_entries.append({
            "komise": int(r["komise_cislo"]),
            "jmena": (r["jmena"] or "").strip(),
        })

    data = []
    for v in vzorky:
        odr_full = _odruda_display(v, oz_mob).strip() or "Nezařazeno"
        odr_abbr = (
            (v["odruda_join_short"] or "").strip().upper()
            if v["odruda_id"]
            else (v["odruda"] or "").strip()
        )
        if not odr_abbr:
            odr_abbr = odr_full
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
            "web": (v["web"] or "").strip(),
            "poznamka_vzorek": (v["poznamka_vzorek"] or "").strip(),
        })

    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    payload_abbr = json.dumps(abbr_entries, ensure_ascii=False).replace("</", "<\\/")
    payload_porotci = json.dumps(porotci_entries, ensure_ascii=False).replace("</", "<\\/")
    title = escape(degustace["nazev"] or "E-katalog")
    ek_logo_url = url_for("degus_logo")

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
            .top-head {{ display:flex; justify-content:space-between; align-items:center; gap:8px; margin-bottom:8px; min-width: 0; }}
            .top-head-main {{ display: flex; align-items: center; gap: 8px; min-width: 0; flex: 1; }}
            .ek-logo-link {{ flex-shrink: 0; line-height: 0; display: flex; align-items: center; }}
            .ek-logo {{ height: 2.25rem; width: auto; max-height: 2.6rem; object-fit: contain; display: block; }}
            .title {{ font-size: 18px; font-weight: 700; margin: 0; min-width: 0; flex: 1; line-height: 1.25; }}
            .btn-info {{ border:1px solid var(--border); background:#fff; border-radius:6px; padding:4px 8px; font-size:11px; font-weight:600; flex-shrink: 0; white-space: nowrap; }}
            .tabs {{ display: flex; gap: 4px; margin-bottom: 8px; flex-wrap: wrap; }}
            .tab {{
                flex: 1; min-width: 4.5rem; border: 1px solid var(--border); background:#fff; border-radius: 8px; padding: 8px 4px;
                font-size: 12px; font-weight: 600;
            }}
            .tab.active {{ background: var(--accent); color: #fff; border-color: var(--accent); }}
            .search {{ width: 100%; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; font-size: 14px; }}
            .meta {{ color: var(--muted); font-size: 12px; margin: 6px 0 0; padding: 0 2px; display: none; }}
            .section-title {{ margin: 10px 12px 6px; font-size: 14px; color: var(--muted); font-weight: 700; }}
            /* overflow: visible — overflow:hidden na předkovi rozbíjí sticky thead v mobilních prohlížečích */
            .tbl-wrap {{ background: #fff; border: 1px solid var(--border); border-radius: 10px; margin: 0 12px 8px; overflow: visible; }}
            table.tbl {{
                width: 100%; border-collapse: separate; border-spacing: 0; table-layout: fixed; font-size: 12px;
            }}
            .tbl col.colg-num {{ width: 2.75rem; max-width: 2.75rem; }}
            .tbl col.colg-fav {{ width: 2.25rem; max-width: 2.25rem; }}
            .tbl col.colg-odruda {{ width: auto; min-width: 0; }}
            .tbl col.colg-priv {{ width: 2.85rem; max-width: 3.25rem; }}
            .tbl col.colg-roc {{ width: 2.75rem; max-width: 3rem; }}
            .tbl col.colg-body {{ width: 3.1rem; max-width: 3.35rem; }}
            .tbl col.colg-tasted {{ width: 2.25rem; max-width: 2.25rem; }}
            @media (max-width: 720px) {{
                .tbl col.colg-num {{ width: 2.5rem; max-width: 2.6rem; }}
                .tbl col.colg-fav {{ width: 2rem; max-width: 2.1rem; }}
                .tbl col.colg-priv {{ width: 2.6rem; max-width: 2.85rem; }}
                .tbl col.colg-roc {{ width: 2.5rem; max-width: 2.75rem; }}
                .tbl col.colg-body {{ width: 2.85rem; max-width: 3.1rem; }}
                .tbl col.colg-tasted {{ width: 2rem; max-width: 2.1rem; }}
                .tbl tbody td.cell-spacer {{ padding: 0 4px 1px; }}
                .tbl tbody tr.main-row-sub td.col-vinar-full {{
                    padding: 0 4px 2px;
                    line-height: 1.22;
                }}
                .detail-row .detail-box {{ padding: 12px 4px 5px; }}
                .detail-box-inner {{ line-height: 1.3; }}
                .detail-line {{ margin: 1px 0; }}
            }}
            .tbl thead th {{
                position: sticky; top: var(--thead-top, 200px); z-index: 9; background: #fff;
                padding: 8px 4px; text-align: left; font-weight: 700; color: #44505d;
                white-space: nowrap; border-bottom: 1px solid #e8edf2; vertical-align: middle;
            }}
            .tbl thead th:first-child {{ border-top-left-radius: 10px; }}
            .tbl thead th:last-child {{ border-top-right-radius: 10px; }}
            .tbl thead th.col-num {{ width: auto; min-width: 0; text-align: center; }}
            .tbl thead th.col-fav-star {{ text-align: center; }}
            .tbl thead th.col-odruda {{ min-width: 0; }}
            .tbl thead th.col-priv, .tbl thead th.col-roc, .tbl thead th.col-body {{ min-width: 0; }}
            .tbl thead th.col-tasted {{ text-align: center; }}
            .tbl thead th.col-priv, .tbl thead th.col-roc, .tbl thead th.col-body {{ text-align: center; }}
            .tbl thead th.col-priv .sort-btn, .tbl thead th.col-roc .sort-btn, .tbl thead th.col-body .sort-btn {{ width: 100%; text-align: center; }}
            .tbl tbody {{ position: relative; z-index: 0; }}
            .tbl tbody td {{ padding: 8px 4px; vertical-align: middle; border: none; }}
            .tbl tbody td.col-priv, .tbl tbody td.col-roc, .tbl tbody td.col-body {{ text-align: center; }}
            .tbl tbody td.col-fav-star {{ text-align: center; }}
            .tbl tbody td.col-tasted {{ text-align: center; }}
            .tbl tbody td.cell-spacer {{ padding: 2px 4px; border: none; vertical-align: top; }}
            .tbl tbody tr.main-row-top td {{ border-top: 1px solid #f0f2f4; padding-bottom: 2px; }}
            .tbl tbody tr.main-row-top:first-child td {{ border-top: none; }}
            .tbl tbody tr.main-row-sub td.col-vinar-full {{
                padding: 0 4px 3px;
                font-size: 12px;
                font-weight: 600;
                color: #334155;
                line-height: 1.26;
                word-wrap: break-word;
                overflow-wrap: anywhere;
                border-top: none;
            }}
            .detail-row .detail-box {{
                padding: 10px 4px 7px;
                vertical-align: top;
                box-sizing: border-box;
            }}
            .detail-box-inner {{ padding: 0; font-size: 12px; line-height: 1.34; color: var(--muted); box-sizing: border-box; }}
            .detail-line {{ margin: 2px 0; }}
            .detail-line:first-child {{ margin-top: 0; }}
            .detail-line-addr {{ display: block; word-wrap: break-word; overflow-wrap: anywhere; }}
            .detail-addr-sep {{ color: var(--muted); }}
            .detail-web-link {{ font-weight: 600; color: var(--accent); }}
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
            @media (max-width: 720px) {{
                .tbl tbody tr.main-row-top td {{ padding-top: 6px; padding-bottom: 0; }}
            }}
        </style>
    </head>
    <body>
    <div class="app">
        <div class="top">
            <div class="top-head">
                <div class="top-head-main">
                    <a href="/" class="ek-logo-link" title="Úvodní stránka — Score Taste"><img src="{escape(ek_logo_url)}" class="ek-logo" alt="" width="120" height="48" decoding="async"></a>
                    <h1 class="title">{title}</h1>
                </div>
                <button id="btn-info" class="btn-info" type="button" title="Info o degustaci">Info</button>
            </div>
            <div class="tabs">
                <button class="tab active" data-mode="all">Vše</button>
                <button class="tab" data-mode="odrudy">Odrůdy</button>
                <button class="tab" data-mode="vinarstvi">Vinařství</button>
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
    function escHtml(s) {{
      return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
    }}
    function fmtWebLabel(w) {{
      w = (w || "").trim();
      if (!w) return "—";
      const href = new RegExp("^https?://", "i").test(w) ? w : "https://" + w;
      return '<a href="' + escHtml(href) + '" target="_blank" rel="noopener" class="detail-web-link">Web</a>';
    }}
    function fmtBody(v) {{ return v == null ? "—" : String(v.toFixed ? v.toFixed(1) : v).replace(".", ","); }}
    function rowText(v) {{
      return [v.vystavovatel, v.adresa, v.odruda, v.privlastek, v.rocnik, v.cislo, v.web, v.poznamka_vzorek].join(" ").toLowerCase();
    }}
    function getBase() {{
      let out = [...vzorky];
      if (state.mode === "fav") out = out.filter(v => state.fav.has(v.id));
      if (state.query) out = out.filter(v => rowText(v).includes(state.query));
      return sortRows(out);
    }}
    function colgroupHtml() {{
      return '<colgroup>' +
        '<col class="colg-num" />' +
        '<col class="colg-fav" />' +
        '<col class="colg-odruda" />' +
        '<col class="colg-priv" />' +
        '<col class="colg-roc" />' +
        '<col class="colg-body" />' +
        '<col class="colg-tasted" />' +
        '</colgroup>';
    }}
    function headerRow() {{
      return '<thead><tr>' +
        '<th class="col-num" scope="col" aria-label="Číslo vzorku"></th>' +
        '<th class="col-fav-star" scope="col" aria-label="Oblíbené"></th>' +
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
      const pzRaw = (v.poznamka_vzorek || "").trim();
      const pz = pzRaw ? escHtml(pzRaw) : "—";
      const vin = escHtml(v.vystavovatel || "—");
      const addr = escHtml(v.adresa || "—");
      const webL = fmtWebLabel(v.web);
      return `
      <tr class="main-row main-row-top">
        <td class="col-num"><button type="button" class="num-btn ${{isOpen ? "open" : ""}}" data-exp="${{v.id}}">${{v.cislo}}</button></td>
        <td class="col-fav-star"><button type="button" class="fav-inline ${{state.fav.has(v.id) ? "on" : ""}}" data-fav="${{v.id}}" aria-label="Oblíbený vzorek">${{fav}}</button></td>
        <td class="col-odruda">${{odr}}</td>
        <td class="col-priv">${{v.privlastek || "—"}}</td>
        <td class="col-roc">${{v.rocnik || "—"}}</td>
        <td class="col-body">${{fmtBody(v.body)}}</td>
        <td class="col-tasted"><button type="button" class="tasted-btn ${{state.tasted.has(v.id) ? "on" : ""}}" data-tasted="${{v.id}}" aria-pressed="${{state.tasted.has(v.id)}}" aria-label="Koštováno">${{state.tasted.has(v.id) ? "✓" : "○"}}</button></td>
      </tr>
      <tr class="main-row main-row-sub">
        <td class="col-num cell-spacer" aria-hidden="true"></td>
        <td class="col-fav-star cell-spacer" aria-hidden="true"></td>
        <td colspan="5" class="col-vinar-full">${{vin}}</td>
      </tr>
      <tr class="detail-row ${{isOpen ? "open" : ""}}">
        <td class="col-num cell-spacer" aria-hidden="true"></td>
        <td class="col-fav-star cell-spacer" aria-hidden="true"></td>
        <td colspan="5" class="detail-box">
            <div class="detail-box-inner">
            <div class="detail-line detail-line-addr"><strong>Adresa:</strong> ${{addr}}<span class="detail-addr-sep"> · </span><strong>Web:</strong> ${{webL}}</div>
            <div class="detail-line"><strong>Pořadí:</strong> ${{por}}</div>
            <div class="detail-line"><strong>Poznámka:</strong> ${{pz}}</div>
          </div>
        </td>
      </tr>`;
    }}
    function renderTable(rows, useAbbr) {{
      if (!rows.length) return `<div class="empty">Žádné položky pro aktuální filtr.</div>`;
      return `<div class="tbl-wrap"><table class="tbl">${{colgroupHtml()}}${{headerRow()}}<tbody>${{rows.map(v => twoRows(v, useAbbr)).join("")}}</tbody></table></div>`;
    }}
    function renderByOdrudy(base) {{
      if (!base.length) return `<div class="empty">Žádné položky pro aktuální filtr.</div>`;
      const grp = {{}};
      for (const v of base) {{ (grp[v.odruda || "Nezařazeno"] ||= []).push(v); }}
      const keys = Object.keys(grp).sort((a,b)=>a.localeCompare(b,"cs"));
      return keys.map(k => `<div class="section-title">${{escHtml(k)}} (${{grp[k].length}})</div>${{renderTable(grp[k], true)}}`).join("");
    }}
    function renderByVinarstvi(base) {{
      if (!base.length) return `<div class="empty">Žádné položky pro aktuální filtr.</div>`;
      const grp = {{}};
      for (const v of base) {{ (grp[v.vystavovatel || "Nezařazeno"] ||= []).push(v); }}
      const keys = Object.keys(grp).sort((a,b)=>a.localeCompare(b,"cs"));
      return keys.map(k => `<div class="section-title">${{escHtml(k)}} (${{grp[k].length}})</div>${{renderTable(grp[k], true)}}`).join("");
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
        else if (state.mode === "vinarstvi") listEl.innerHTML = renderByVinarstvi(base);
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
    vzorky = conn.execute(VZORKY_SELECT_JOIN, (id,)).fetchall()
    conn.close()

    oz_kt = _deg_oz_field(degustace, "odruda_zob_tisk")

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
        k = (_odruda_display(v, oz_kt) or "Nezařazeno").strip() or "Nezařazeno"
        by_odruda.setdefault(k, []).append(v)
    odrudy_sorted = sorted(by_odruda.keys(), key=lambda x: x.casefold())
    for k in odrudy_sorted:
        by_odruda[k].sort(key=lambda v: ((v["nazev"] or "").casefold(), v["cislo"]))

    poradi_all = [v for v in vzorky if v["body"] is not None]
    poradi_all.sort(key=lambda v: (-float(v["body"]), v["cislo"]))
    poradi_map = {v["id"]: i + 1 for i, v in enumerate(poradi_all)}

    sheet_w = "210mm" if fmt == "A4" else "148mm"
    mobile_url = app_public_base_url() + f"/mobile-katalog/{id}"
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
                <td>{por_txt}</td><td>{v["cislo"]}</td><td>{escape(v["nazev"] or "")}</td><td>{escape(_odruda_display(v, oz_kt))}</td>
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

    vzorky = conn.execute(VZORKY_SELECT_JOIN, (id,)).fetchall()

    # Stejné zobrazení odrůdy jako desktop katalog (seznam / komise).
    oz_tisk = _deg_oz_field(degustace, "odruda_zob_katalog")

    pocet_komisi = _degustace_pocet_komisi(degustace, len(vzorky))
    mode = (request.args.get("mode") or "").strip().lower()
    rozdeleni_existuje = _komise_prirazeni_existuje(vzorky)

    if mode not in ("use", "regen"):
        # Volba je na stránce degustace (panel); přímý odkaz použije stávající rozdělení.
        mode = "use"

    if mode == "regen" or not rozdeleni_existuje:
        _komise_generovat_prirazeni(conn, id, pocet_komisi)
        vzorky = conn.execute(VZORKY_SELECT_JOIN, (id,)).fetchall()
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
                <td>{escape(_odruda_display(v, oz_tisk))}</td>
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
