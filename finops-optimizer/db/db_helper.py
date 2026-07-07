import os
import re
import sqlite3

try:
    import psycopg2
except ImportError:
    psycopg2 = None

def init_sqlite_db(conn):
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS cloud_telemetry (
        time            TEXT       NOT NULL,
        service_id      TEXT       NOT NULL,
        cpu_utilization_pct    REAL,
        memory_utilization_pct REAL,
        request_count           INTEGER,
        network_egress_mb       REAL,
        current_instance_type   TEXT,
        hourly_cost_usd          REAL
    );
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_service_time ON cloud_telemetry (service_id, time DESC);")
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS agent_audit_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        time            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        service_id      TEXT NOT NULL,
        agent_name      TEXT NOT NULL,
        action          TEXT NOT NULL,
        details         TEXT,
        estimated_monthly_savings_usd REAL DEFAULT 0
    );
    """)
    
    cur.execute("""
    CREATE TABLE IF NOT EXISTS resize_cooldown (
        service_id      TEXT PRIMARY KEY,
        last_modified   TEXT NOT NULL
    );
    """)
    conn.commit()

def get_db_connection(db_config):
    """
    Attempts to connect to PostgreSQL. If connection fails, falls back to SQLite.
    Returns a tuple: (connection_object, db_type_string)
    """
    # Force SQLite if environment variable is set or psycopg2 is not installed
    if psycopg2 is None or os.environ.get("USE_SQLITE", "0") == "1":
        db_dir = os.path.dirname(__file__)
        sqlite_path = os.path.join(db_dir, "finops_telemetry.db")
        conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        init_sqlite_db(conn)
        return conn, "sqlite"

    try:
        conn = psycopg2.connect(**db_config)
        return conn, "postgres"
    except Exception as e:
        print(f"[db_helper] PostgreSQL connection failed ({e}). Falling back to local SQLite.")
        db_dir = os.path.dirname(__file__)
        sqlite_path = os.path.join(db_dir, "finops_telemetry.db")
        conn = sqlite3.connect(sqlite_path, check_same_thread=False)
        init_sqlite_db(conn)
        return conn, "sqlite"

def translate_sql(sql, db_type):
    """
    Translates PostgreSQL specific syntax to SQLite syntax if db_type is sqlite.
    """
    if db_type == "sqlite":
        # Replace Postgres-style named parameters %(name)s with SQLite :name
        sql = re.sub(r'%\((.*?)\)s', r':\1', sql)
        # Replace Postgres-style positional parameters %s with SQLite ?
        sql = sql.replace('%s', '?')
        # Translate now() and intervals
        sql = re.sub(r"now\(\)\s*-\s*interval\s*'2 hours'", "datetime('now', '-2 hours')", sql, flags=re.IGNORECASE)
        sql = re.sub(r"now\(\)\s*-\s*interval\s*'4 hours'", "datetime('now', '-4 hours')", sql, flags=re.IGNORECASE)
        sql = re.sub(r"now\(\)", "datetime('now')", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bcoalesce\b", "ifnull", sql, flags=re.IGNORECASE)
    return sql
