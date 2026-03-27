import os
import sqlite3

DB_DIR = os.environ.get("DB_DIR", "/data")
DB_NAME = os.path.join(DB_DIR, "wine.db")

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn