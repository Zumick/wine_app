import os
import sqlite3

# pokud je nastaveno DB_DIR (Render), použij ho
# jinak použij vývojovou složku (lokální vývoj)
DB_DIR = os.environ.get("DB_DIR")

if DB_DIR:
    DB_NAME = os.path.join(DB_DIR, "wine.db")
else:
    DB_NAME = "wine.db"

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn