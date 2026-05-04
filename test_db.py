from db_config import get_db_connection, get_last_db_error

try:
    conn = get_db_connection()
    if not conn:
        print("Database connection failed:")
        print(get_last_db_error() or "Unknown database error")
        raise SystemExit(1)

    cursor = conn.cursor()
    cursor.execute("SELECT DATABASE();")
    result = cursor.fetchone()
    print("Connected to:", result[0])

    cursor.execute("SHOW TABLES;")
    tables = cursor.fetchall()
    print("Tables:", tables)

    cursor.close()
    conn.close()
except Exception as e:
    print("Database connection failed:")
    print(e)
