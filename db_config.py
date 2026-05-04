import os

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error

# Load environment variables
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_last_db_error = None


def _set_last_db_error(message):
    global _last_db_error
    _last_db_error = message


def get_last_db_error():
    return _last_db_error


def _resolve_ssl_ca():
    ssl_ca = os.getenv("DB_SSL_CA")
    if not ssl_ca:
        return None

    if os.path.isabs(ssl_ca):
        return ssl_ca

    return os.path.join(BASE_DIR, ssl_ca)


def _base_connection_kwargs(include_database=True):
    kwargs = {
        "host": os.getenv("DB_HOST"),
        "port": int(os.getenv("DB_PORT", "3306")),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "autocommit": True,
        "connection_timeout": 10,
    }

    ssl_ca = _resolve_ssl_ca()
    if ssl_ca:
        kwargs["ssl_ca"] = ssl_ca

    if include_database:
        kwargs["database"] = os.getenv("DB_NAME")

    return kwargs


def _is_local_mysql():
    host = (os.getenv("DB_HOST") or "").strip().lower()
    return host in {"127.0.0.1", "localhost"}


def _connect(include_database=True):
    return mysql.connector.connect(**_base_connection_kwargs(include_database=include_database))


def _ensure_local_database_exists():
    db_name = (os.getenv("DB_NAME") or "").strip()
    if not db_name:
        raise Error("DB_NAME is not configured")

    admin_conn = None
    cur = None
    try:
        admin_conn = _connect(include_database=False)
        cur = admin_conn.cursor()
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        )
        print(f"[DB] Ensured local database exists: {db_name}")
    finally:
        try:
            if cur:
                cur.close()
        finally:
            if admin_conn:
                admin_conn.close()


def get_db_connection():
    """
    Creates a MySQL connection to the configured database.
    For local MySQL only, auto-creates the configured database when missing.
    """

    _set_last_db_error(None)

    try:
        conn = _connect(include_database=True)
        if conn.is_connected():
            print("[DB] Connected successfully")
        return conn
    except Error as e:
        db_name = os.getenv("DB_NAME")
        missing_database = getattr(e, "errno", None) == 1049 or f"Unknown database '{db_name}'" in str(e)

        if _is_local_mysql() and missing_database:
            try:
                print(f"[DB] Local database missing. Attempting to create: {db_name}")
                _ensure_local_database_exists()
                conn = _connect(include_database=True)
                if conn.is_connected():
                    print("[DB] Connected successfully after creating local database")
                return conn
            except Error as bootstrap_error:
                e = bootstrap_error

        message = (
            f"Database connection failed: {e}. "
            f"Host={os.getenv('DB_HOST')} Port={os.getenv('DB_PORT')} "
            f"Database={os.getenv('DB_NAME')}"
        )
        _set_last_db_error(message)
        print("[DB] Database connection failed")
        print("Error:", e)
        print("Host:", os.getenv("DB_HOST"))
        print("Port:", os.getenv("DB_PORT"))
        print("Database:", os.getenv("DB_NAME"))
        print("SSL CA:", _resolve_ssl_ca())
        return None
