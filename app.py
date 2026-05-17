from flask import (
    Flask, request, jsonify, render_template,
    redirect, url_for, session
)
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from werkzeug.security import check_password_hash, generate_password_hash
from datetime import datetime, timedelta, date
from apscheduler.schedulers.background import BackgroundScheduler
import os
import atexit
import math
from dotenv import load_dotenv

from db_config import get_db_connection, get_last_db_error


from fcm_service import send_notification_to_topic, send_notification_to_token


# =====================================================================
# ENV
# =====================================================================
load_dotenv()


def env_flag(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# =====================================================================
# APP CONFIG
# =====================================================================
app = Flask(__name__)
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET", "capstone-waste-jolo-2026")
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=24)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "admin-panel-secret")

CORS(app)

TRUCK_NEAR_ALERT_METERS = int(os.getenv("TRUCK_NEAR_ALERT_METERS", "700"))
jwt = JWTManager(app)


# =====================================================================
# DB HELPERS
# =====================================================================
def close_quietly(resource):
    try:
        if resource:
            resource.close()
    except Exception:
        pass


def table_exists(cur, table_name: str) -> bool:
    cur.execute("""
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = %s
    """, (table_name,))
    return cur.fetchone()[0] > 0


def column_exists(cur, table_name: str, column_name: str) -> bool:
    cur.execute("""
        SELECT COUNT(*)
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND column_name = %s
    """, (table_name, column_name))
    return cur.fetchone()[0] > 0


def ensure_column(cur, table_name: str, column_name: str, column_sql: str):
    if not column_exists(cur, table_name, column_name):
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")
        print(f"[DB] Added missing column: {table_name}.{column_name}")


def ensure_index(cur, table_name: str, index_name: str, index_sql: str):
    cur.execute("""
        SELECT COUNT(*)
        FROM information_schema.statistics
        WHERE table_schema = DATABASE()
          AND table_name = %s
          AND index_name = %s
    """, (table_name, index_name))
    exists = cur.fetchone()[0] > 0

    if not exists:
        cur.execute(index_sql)
        print(f"[DB] Added missing index: {index_name} on {table_name}")


def ensure_tables():
    print("[STARTUP] ensure_tables() started")
    conn = get_db_connection()
    if not conn:
        print("[STARTUP] No DB connection")
        return

    cur = None
    try:
        cur = conn.cursor()

        # -------------------------------------------------------------
        # ADMINS
        # -------------------------------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id INT NOT NULL AUTO_INCREMENT,
                username VARCHAR(50) NOT NULL,
                password VARCHAR(255) NOT NULL,
                full_name VARCHAR(120) DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_admins_username (username)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # -------------------------------------------------------------
        # BARANGAYS
        # -------------------------------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS barangays (
                id INT NOT NULL AUTO_INCREMENT,
                name VARCHAR(100) NOT NULL,
                code VARCHAR(20) DEFAULT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        # If user previously created barangays with barangay_name instead of name,
        # add missing "name" and copy values from barangay_name.
        if column_exists(cur, "barangays", "barangay_name") and not column_exists(cur, "barangays", "name"):
            cur.execute("ALTER TABLE barangays ADD COLUMN name VARCHAR(100) DEFAULT NULL")
            cur.execute("""
                UPDATE barangays
                SET name = barangay_name
                WHERE (name IS NULL OR name = '')
                  AND barangay_name IS NOT NULL
            """)
            print("[DB] Synced barangays.barangay_name -> barangays.name")

        ensure_column(cur, "barangays", "name", "name VARCHAR(100) NOT NULL DEFAULT ''")
        ensure_column(cur, "barangays", "code", "code VARCHAR(20) DEFAULT NULL")
        ensure_column(cur, "barangays", "created_at", "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP")

        # -------------------------------------------------------------
        # RESIDENTS
        # -------------------------------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS residents (
                id INT NOT NULL AUTO_INCREMENT,
                phone VARCHAR(20) NOT NULL,
                barangay_id INT NOT NULL,
                barangay_name VARCHAR(100) NOT NULL,
                fcm_token TEXT DEFAULT NULL,
                is_active TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_residents_phone (phone)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        ensure_column(cur, "residents", "phone", "phone VARCHAR(20) NOT NULL")
        ensure_column(cur, "residents", "barangay_id", "barangay_id INT NOT NULL DEFAULT 0")
        ensure_column(cur, "residents", "barangay_name", "barangay_name VARCHAR(100) NOT NULL DEFAULT ''")
        ensure_column(cur, "residents", "fcm_token", "fcm_token TEXT DEFAULT NULL")
        ensure_column(cur, "residents", "is_active", "is_active TINYINT(1) DEFAULT 1")
        ensure_column(cur, "residents", "created_at", "created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
        ensure_index(
            cur,
            "residents",
            "uq_residents_phone",
            "ALTER TABLE residents ADD UNIQUE KEY uq_residents_phone (phone)"
        )

        # -------------------------------------------------------------
        # SCHEDULES
        # -------------------------------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schedules (
                id INT NOT NULL AUTO_INCREMENT,
                barangay_id INT NOT NULL,
                barangay_name VARCHAR(100) NOT NULL,
                collection_date DATE NOT NULL,
                collection_time TIME NOT NULL,
                waste_type VARCHAR(50) DEFAULT NULL,
                notes TEXT DEFAULT NULL,
                status VARCHAR(20) DEFAULT 'scheduled',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        ensure_column(cur, "schedules", "barangay_id", "barangay_id INT NOT NULL DEFAULT 0")
        ensure_column(cur, "schedules", "barangay_name", "barangay_name VARCHAR(100) NOT NULL DEFAULT ''")
        ensure_column(cur, "schedules", "collection_date", "collection_date DATE NOT NULL DEFAULT '2026-01-01'")
        ensure_column(cur, "schedules", "collection_time", "collection_time TIME NOT NULL DEFAULT '08:00:00'")
        ensure_column(cur, "schedules", "waste_type", "waste_type VARCHAR(50) DEFAULT NULL")
        ensure_column(cur, "schedules", "notes", "notes TEXT DEFAULT NULL")
        ensure_column(cur, "schedules", "status", "status VARCHAR(20) DEFAULT 'scheduled'")
        ensure_column(cur, "schedules", "created_at", "created_at DATETIME DEFAULT CURRENT_TIMESTAMP")

        # -------------------------------------------------------------
        # ANNOUNCEMENTS
        # -------------------------------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS announcements (
                id INT NOT NULL AUTO_INCREMENT,
                title VARCHAR(200) NOT NULL,
                message TEXT NOT NULL,
                target_barangay_id INT DEFAULT NULL,
                target_barangay_name VARCHAR(100) DEFAULT NULL,
                is_active TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        ensure_column(cur, "announcements", "title", "title VARCHAR(200) NOT NULL DEFAULT ''")
        ensure_column(cur, "announcements", "message", "message TEXT NOT NULL")
        ensure_column(cur, "announcements", "target_barangay_id", "target_barangay_id INT DEFAULT NULL")
        ensure_column(cur, "announcements", "target_barangay_name", "target_barangay_name VARCHAR(100) DEFAULT NULL")
        ensure_column(cur, "announcements", "is_active", "is_active TINYINT(1) DEFAULT 1")
        ensure_column(cur, "announcements", "created_at", "created_at DATETIME DEFAULT CURRENT_TIMESTAMP")

        # -------------------------------------------------------------
        # LOGS
        # -------------------------------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INT NOT NULL AUTO_INCREMENT,
                action VARCHAR(100) NOT NULL,
                user_type VARCHAR(30) NOT NULL,
                user_id INT DEFAULT NULL,
                details TEXT DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        ensure_column(cur, "logs", "action", "action VARCHAR(100) NOT NULL DEFAULT ''")
        ensure_column(cur, "logs", "user_type", "user_type VARCHAR(30) NOT NULL DEFAULT ''")
        ensure_column(cur, "logs", "user_id", "user_id INT DEFAULT NULL")
        ensure_column(cur, "logs", "details", "details TEXT DEFAULT NULL")
        ensure_column(cur, "logs", "created_at", "created_at DATETIME DEFAULT CURRENT_TIMESTAMP")

        # -------------------------------------------------------------
        # MESSAGES
        # -------------------------------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INT NOT NULL AUTO_INCREMENT,
                resident_phone VARCHAR(20) NOT NULL,
                resident_barangay_id INT DEFAULT NULL,
                resident_barangay_name VARCHAR(100) DEFAULT NULL,
                category VARCHAR(50) NOT NULL,
                message TEXT NOT NULL,
                admin_reply TEXT DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                replied_at DATETIME DEFAULT NULL,
                PRIMARY KEY (id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        ensure_column(cur, "messages", "resident_phone", "resident_phone VARCHAR(20) NOT NULL DEFAULT ''")
        ensure_column(cur, "messages", "resident_barangay_id", "resident_barangay_id INT DEFAULT NULL")
        ensure_column(cur, "messages", "resident_barangay_name", "resident_barangay_name VARCHAR(100) DEFAULT NULL")
        ensure_column(cur, "messages", "category", "category VARCHAR(50) NOT NULL DEFAULT 'Other'")
        ensure_column(cur, "messages", "message", "message TEXT NOT NULL")
        ensure_column(cur, "messages", "admin_reply", "admin_reply TEXT DEFAULT NULL")
        ensure_column(cur, "messages", "created_at", "created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
        ensure_column(cur, "messages", "replied_at", "replied_at DATETIME DEFAULT NULL")

        # -------------------------------------------------------------
        # TRUCK LOCATIONS
        # -------------------------------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS truck_locations (
                id INT NOT NULL AUTO_INCREMENT,
                barangay_id INT NOT NULL,
                barangay_name VARCHAR(100) NOT NULL,
                latitude DECIMAL(10,7) NOT NULL,
                longitude DECIMAL(10,7) NOT NULL,
                collection_update VARCHAR(100) DEFAULT 'Monitoring',
                notes TEXT DEFAULT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_truck_locations_barangay (barangay_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        ensure_column(cur, "truck_locations", "barangay_id", "barangay_id INT NOT NULL DEFAULT 0")
        ensure_column(cur, "truck_locations", "barangay_name", "barangay_name VARCHAR(100) NOT NULL DEFAULT ''")
        ensure_column(cur, "truck_locations", "latitude", "latitude DECIMAL(10,7) NOT NULL DEFAULT 0")
        ensure_column(cur, "truck_locations", "longitude", "longitude DECIMAL(10,7) NOT NULL DEFAULT 0")
        ensure_column(cur, "truck_locations", "collection_update", "collection_update VARCHAR(100) DEFAULT 'Monitoring'")
        ensure_column(cur, "truck_locations", "notes", "notes TEXT DEFAULT NULL")
        ensure_column(cur, "truck_locations", "updated_at", "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP")
        ensure_index(
            cur,
            "truck_locations",
            "uq_truck_locations_barangay",
            "ALTER TABLE truck_locations ADD UNIQUE KEY uq_truck_locations_barangay (barangay_id)"
        )

        # -------------------------------------------------------------
        # READY MARKERS
        # -------------------------------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ready_markers (
                id INT NOT NULL AUTO_INCREMENT,
                schedule_id INT NOT NULL,
                resident_phone VARCHAR(20) NOT NULL,
                barangay_id INT NOT NULL,
                barangay_name VARCHAR(100) NOT NULL,
                latitude DECIMAL(10,7) NOT NULL,
                longitude DECIMAL(10,7) NOT NULL,
                is_ready TINYINT(1) DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_ready_schedule_phone (schedule_id, resident_phone)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)

        ensure_column(cur, "ready_markers", "schedule_id", "schedule_id INT NOT NULL DEFAULT 0")
        ensure_column(cur, "ready_markers", "resident_phone", "resident_phone VARCHAR(20) NOT NULL DEFAULT ''")
        ensure_column(cur, "ready_markers", "barangay_id", "barangay_id INT NOT NULL DEFAULT 0")
        ensure_column(cur, "ready_markers", "barangay_name", "barangay_name VARCHAR(100) NOT NULL DEFAULT ''")
        ensure_column(cur, "ready_markers", "latitude", "latitude DECIMAL(10,7) NOT NULL DEFAULT 0")
        ensure_column(cur, "ready_markers", "longitude", "longitude DECIMAL(10,7) NOT NULL DEFAULT 0")
        ensure_column(cur, "ready_markers", "is_ready", "is_ready TINYINT(1) DEFAULT 1")
        ensure_column(cur, "ready_markers", "created_at", "created_at DATETIME DEFAULT CURRENT_TIMESTAMP")
        ensure_column(cur, "ready_markers", "updated_at", "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP")
        ensure_index(
            cur,
            "ready_markers",
            "uq_ready_schedule_phone",
            "ALTER TABLE ready_markers ADD UNIQUE KEY uq_ready_schedule_phone (schedule_id, resident_phone)"
        )

        # -------------------------------------------------------------
        # TRUCK PROXIMITY ALERTS
        # -------------------------------------------------------------
        cur.execute("""
            CREATE TABLE IF NOT EXISTS truck_proximity_alerts (
                id INT NOT NULL AUTO_INCREMENT,
                schedule_id INT NOT NULL,
                resident_phone VARCHAR(20) NOT NULL,
                alert_type VARCHAR(50) NOT NULL DEFAULT 'truck_near',
                distance_meters INT DEFAULT NULL,
                sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_truck_alert_once (schedule_id, resident_phone, alert_type)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        ensure_column(cur, "truck_proximity_alerts", "schedule_id", "schedule_id INT NOT NULL DEFAULT 0")
        ensure_column(cur, "truck_proximity_alerts", "resident_phone", "resident_phone VARCHAR(20) NOT NULL DEFAULT ''")
        ensure_column(cur, "truck_proximity_alerts", "alert_type", "alert_type VARCHAR(50) NOT NULL DEFAULT 'truck_near'")
        ensure_column(cur, "truck_proximity_alerts", "distance_meters", "distance_meters INT DEFAULT NULL")
        ensure_column(cur, "truck_proximity_alerts", "sent_at", "sent_at DATETIME DEFAULT CURRENT_TIMESTAMP")
        ensure_index(
            cur,
            "truck_proximity_alerts",
            "uq_truck_alert_once",
            "ALTER TABLE truck_proximity_alerts ADD UNIQUE KEY uq_truck_alert_once (schedule_id, resident_phone, alert_type)"
        )

        # -------------------------------------------------------------
        # OPTIONAL SEED BARANGAYS
        # -------------------------------------------------------------
        cur.execute("SELECT COUNT(*) FROM barangays")
        barangay_count = cur.fetchone()[0]

        if barangay_count == 0:
            cur.executemany("""
                INSERT INTO barangays (name, code)
                VALUES (%s, %s)
            """, [
                ("Asturias", "AST"),
                ("Bus-Bus", "BUS"),
                ("Chinese Pier", "CP"),
                ("San Raymundo", "SR"),
                ("Takut-Takut", "TT"),
                ("Tulay", "TUL"),
            ])
            print("[DB] Seeded default barangays")

        conn.commit()
        print("[STARTUP] Tables checked successfully")

    except Exception as e:
        print("[STARTUP ERROR] ensure_tables failed:", e)
        try:
            conn.rollback()
        except Exception:
            pass
    finally:
        close_quietly(cur)
        close_quietly(conn)
        print("[STARTUP] DB connection closed")


def log_action(action, user_type, user_id=None, details=None):
    conn = get_db_connection()
    if not conn:
        return

    cur = None
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO logs (action, user_type, user_id, details)
            VALUES (%s, %s, %s, %s)
        """, (action, user_type, user_id, details))
        conn.commit()
    except Exception as e:
        print("LOGGING SKIPPED:", e)
    finally:
        close_quietly(cur)
        close_quietly(conn)


def require_admin():
    return "admin_id" in session


def topic_for_barangay(barangay_id):
    return f"barangay_{barangay_id}"


def driver_topic_for_barangay(barangay_id):
    return f"drivers_barangay_{barangay_id}"


def safe_send_topic(topic: str, title: str, body: str, data=None):
    """
    Safe wrapper so FCM failures won't crash your Flask app.
    We inject title/body into data so Flutter always receives payload in message.data.
    """
    try:
        payload = dict(data or {})
        payload["title"] = str(title)
        payload["body"] = str(body)

        payload = {str(k): "" if v is None else str(v) for k, v in payload.items()}

        send_notification_to_topic(
            topic=topic,
            title=str(title),
            body=str(body),
            data=payload,
        )
        print(f"✅ FCM SENT topic={topic} type={payload.get('type')}")
    except Exception as e:
        print("❌ FCM ERROR:", e)


def format_schedule_row(s):
    if isinstance(s.get("collection_date"), (datetime, date)):
        s["collection_date"] = s["collection_date"].isoformat()
    else:
        s["collection_date"] = str(s.get("collection_date"))

    s["collection_time"] = str(s.get("collection_time"))
    s["created_at"] = str(s.get("created_at"))
    return s


def get_current_schedule_for_barangay(conn, barangay_id):
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT *
            FROM schedules
            WHERE barangay_id=%s
              AND status='scheduled'
            ORDER BY collection_date, collection_time
            LIMIT 1
        """, (barangay_id,))
        row = cur.fetchone()
        if row:
            return format_schedule_row(row)
        return None
    finally:
        close_quietly(cur)


def distance_meters(lat1, lon1, lat2, lon2):
    radius = 6371000
    phi1 = math.radians(float(lat1))
    phi2 = math.radians(float(lat2))
    delta_phi = math.radians(float(lat2) - float(lat1))
    delta_lambda = math.radians(float(lon2) - float(lon1))

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    return int(radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)))


def notify_nearby_ready_residents(conn, barangay_id, truck_latitude, truck_longitude):
    schedule = get_current_schedule_for_barangay(conn, barangay_id)
    if not schedule:
        return 0

    cur = None
    sent_count = 0
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS truck_proximity_alerts (
                id INT NOT NULL AUTO_INCREMENT,
                schedule_id INT NOT NULL,
                resident_phone VARCHAR(20) NOT NULL,
                alert_type VARCHAR(50) NOT NULL DEFAULT 'truck_near',
                distance_meters INT DEFAULT NULL,
                sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                UNIQUE KEY uq_truck_alert_once (schedule_id, resident_phone, alert_type)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        cur.execute("""
            SELECT rm.resident_phone, rm.latitude, rm.longitude, r.fcm_token
            FROM ready_markers rm
            JOIN residents r ON r.phone = rm.resident_phone
            WHERE rm.schedule_id=%s
              AND rm.barangay_id=%s
              AND rm.is_ready=1
              AND r.is_active=1
              AND r.fcm_token IS NOT NULL
              AND r.fcm_token <> ''
        """, (schedule["id"], barangay_id))

        for resident in cur.fetchall():
            distance = distance_meters(
                truck_latitude,
                truck_longitude,
                resident["latitude"],
                resident["longitude"],
            )
            if distance > TRUCK_NEAR_ALERT_METERS:
                continue

            cur.execute("""
                SELECT id
                FROM truck_proximity_alerts
                WHERE schedule_id=%s
                  AND resident_phone=%s
                  AND alert_type='truck_near'
            """, (schedule["id"], resident["resident_phone"]))
            if cur.fetchone():
                continue

            send_notification_to_token(
                token=resident["fcm_token"],
                title="Truck is nearby",
                body="The waste collection truck is almost at your location. Please prepare your waste.",
                data={
                    "type": "truck_near",
                    "schedule_id": schedule["id"],
                    "barangay_id": barangay_id,
                    "distance_meters": distance,
                },
            )

            cur.execute("""
                INSERT INTO truck_proximity_alerts
                (schedule_id, resident_phone, alert_type, distance_meters, sent_at)
                VALUES (%s, %s, 'truck_near', %s, NOW())
            """, (schedule["id"], resident["resident_phone"], distance))
            sent_count += 1

        conn.commit()
        return sent_count
    except Exception as e:
        print("Truck proximity notification skipped:", e)
        try:
            conn.rollback()
        except Exception:
            pass
        return sent_count
    finally:
        close_quietly(cur)


# =====================================================================
# BASIC ROUTES
# =====================================================================
@app.route("/")
def index():
    return redirect(url_for("admin_login"))


@app.route("/ping")
def ping():
    return "SERVER OK"


@app.route("/api/health", methods=["GET"])
def api_health():
    return jsonify({"status": "ok", "message": "Waste Collection backend is running"}), 200


# =====================================================================
# ADMIN AUTH (WEB)
# =====================================================================
@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if "admin_id" in session:
        return redirect(url_for("admin_dashboard"))

    error = None

    try:
        ensure_tables()
    except Exception as e:
        error = f"Database setup error: {e}"

    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""

        conn = get_db_connection()
        if not conn:
            error = get_last_db_error() or "Database connection failed"
        else:
            cur = None
            try:
                cur = conn.cursor(dictionary=True, buffered=True)
                cur.execute("SELECT id, username, password FROM admins WHERE username=%s", (username,))
                admin = cur.fetchone()

                if admin and check_password_hash(admin["password"], password):
                    session["admin_id"] = admin["id"]
                    session["admin_name"] = admin["username"]
                    log_action("admin_login", "admin", admin["id"])
                    return redirect(url_for("admin_dashboard"))
                else:
                    error = "Invalid username or password"
            except Exception as e:
                error = f"MySQL error: {e}"
            finally:
                close_quietly(cur)
                close_quietly(conn)

    return render_template("login.html", error=error)


@app.route("/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


# =====================================================================
# ADMIN DASHBOARD (WEB)
# =====================================================================
@app.route("/dashboard")
def admin_dashboard():
    if "admin_id" not in session:
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)

        cur.execute("SELECT COUNT(*) AS total_residents FROM residents")
        total_residents = cur.fetchone()["total_residents"]

        cur.execute("SELECT COUNT(*) AS total_barangays FROM barangays")
        total_barangays = cur.fetchone()["total_barangays"]

        cur.execute("SELECT COUNT(*) AS total_schedules FROM schedules")
        total_schedules = cur.fetchone()["total_schedules"]

        cur.execute("SELECT COUNT(*) AS total_scheduled FROM schedules WHERE status='scheduled'")
        total_scheduled = cur.fetchone()["total_scheduled"]

        cur.execute("SELECT COUNT(*) AS total_done FROM schedules WHERE status='done'")
        total_done = cur.fetchone()["total_done"]

        cur.execute("SELECT COUNT(*) AS total_announcements FROM announcements")
        total_announcements = cur.fetchone()["total_announcements"]

        cur.execute("""
            SELECT *
            FROM schedules
            ORDER BY collection_date DESC, collection_time DESC
        """)
        schedules = cur.fetchall()

        return render_template(
            "dashboard.html",
            schedules=schedules,
            admin=session.get("admin_name"),
            total_residents=total_residents,
            total_barangays=total_barangays,
            total_schedules=total_schedules,
            total_scheduled=total_scheduled,
            total_done=total_done,
            total_announcements=total_announcements,
        )
    finally:
        close_quietly(cur)
        close_quietly(conn)


# =====================================================================
# SCHEDULES (ADMIN WEB)
# =====================================================================
@app.route("/add_schedule", methods=["GET", "POST"])
def add_schedule():
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)

        if request.method == "POST":
            barangay_id = (request.form.get("barangay_id") or "").strip()
            collection_date = (request.form.get("collection_date") or "").strip()
            collection_time = (request.form.get("collection_time") or "").strip()
            waste_type = (request.form.get("waste_type") or "household").strip()
            notes = (request.form.get("notes") or "").strip()

            cur.execute("SELECT name FROM barangays WHERE id=%s", (barangay_id,))
            row = cur.fetchone()
            barangay_name = row["name"] if row else None

            if not barangay_name:
                return "Invalid barangay selected", 400

            cur.execute("""
                INSERT INTO schedules
                (barangay_id, barangay_name, collection_date, collection_time, waste_type, notes, status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'scheduled', NOW())
            """, (barangay_id, barangay_name, collection_date, collection_time, waste_type, notes))
            conn.commit()

            schedule_id = cur.lastrowid

            safe_send_topic(
                topic=topic_for_barangay(barangay_id),
                title="Waste Collection Schedule",
                body=f"{barangay_name}: {collection_date} at {collection_time} ({waste_type})",
                data={
                    "type": "schedule",
                    "schedule_id": schedule_id,
                    "barangay_id": barangay_id,
                    "barangay_name": barangay_name,
                    "collection_date": collection_date,
                    "collection_time": collection_time,
                    "waste_type": waste_type,
                },
            )
            safe_send_topic(
                topic=driver_topic_for_barangay(barangay_id),
                title="New Collection Schedule",
                body=f"{barangay_name}: {collection_date} at {collection_time} ({waste_type})",
                data={
                    "type": "driver_schedule",
                    "schedule_id": schedule_id,
                    "barangay_id": barangay_id,
                    "barangay_name": barangay_name,
                    "collection_date": collection_date,
                    "collection_time": collection_time,
                    "waste_type": waste_type,
                },
            )

            log_action("add_schedule", "admin", session.get("admin_id"), f"schedule_id={schedule_id}")
            return redirect(url_for("admin_dashboard"))

        cur.execute("SELECT id, name FROM barangays ORDER BY name")
        barangays = cur.fetchall()
        return render_template("add_schedule.html", barangays=barangays)

    finally:
        close_quietly(cur)
        close_quietly(conn)


# =====================================================================
# ANNOUNCEMENTS (ADMIN WEB)
# =====================================================================
@app.route("/announcements", methods=["GET", "POST"])
def admin_announcements():
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)

        cur.execute("SELECT id, name FROM barangays ORDER BY name")
        barangays = cur.fetchall()

        if request.method == "POST":
            title = (request.form.get("title") or "").strip()
            message = (request.form.get("message") or "").strip()
            target_barangay_id = (request.form.get("target_barangay_id") or "").strip()

            if not title or not message:
                return "Title and message required", 400

            target_barangay_name = None
            target_id_value = None

            if target_barangay_id and target_barangay_id.lower() != "all":
                try:
                    bid = int(target_barangay_id)
                    cur.execute("SELECT name FROM barangays WHERE id=%s", (bid,))
                    row = cur.fetchone()
                    target_barangay_name = row["name"] if row else None

                    if not target_barangay_name:
                        return "Invalid target barangay", 400

                    target_id_value = bid
                except Exception:
                    target_barangay_name = None
                    target_id_value = None

            cur.execute("""
                INSERT INTO announcements (title, message, target_barangay_id, target_barangay_name, is_active, created_at)
                VALUES (%s, %s, %s, %s, 1, NOW())
            """, (title, message, target_id_value, target_barangay_name))
            conn.commit()

            announcement_id = cur.lastrowid

            if target_id_value is not None:
                safe_send_topic(
                    topic=topic_for_barangay(target_id_value),
                    title=f"Announcement: {title}",
                    body=message,
                    data={
                        "type": "announcement",
                        "announcement_id": announcement_id,
                        "target_barangay_id": target_id_value,
                        "target_barangay_name": target_barangay_name,
                    },
                )
                safe_send_topic(
                    topic=driver_topic_for_barangay(target_id_value),
                    title=f"Announcement: {title}",
                    body=message,
                    data={
                        "type": "driver_announcement",
                        "announcement_id": announcement_id,
                        "target_barangay_id": target_id_value,
                        "target_barangay_name": target_barangay_name,
                    },
                )
            else:
                safe_send_topic(
                    topic="all_residents",
                    title=f"Announcement: {title}",
                    body=message,
                    data={
                        "type": "announcement",
                        "announcement_id": announcement_id,
                        "target": "all_residents",
                    },
                )
                safe_send_topic(
                    topic="all_drivers",
                    title=f"Announcement: {title}",
                    body=message,
                    data={
                        "type": "driver_announcement",
                        "announcement_id": announcement_id,
                        "target": "all_drivers",
                    },
                )

            log_action("send_announcement", "admin", session.get("admin_id"), f"announcement_id={announcement_id}")
            return redirect(url_for("admin_announcements"))

        cur.execute("SELECT * FROM announcements ORDER BY created_at DESC")
        announcements = cur.fetchall()

        return render_template(
            "announcements.html",
            announcements=announcements,
            barangays=barangays,
            admin=session.get("admin_name"),
        )

    finally:
        close_quietly(cur)
        close_quietly(conn)


# =====================================================================
# RESIDENTS (ADMIN WEB)
# =====================================================================
@app.route("/manage_residents")
def manage_residents():
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT
                r.id,
                r.phone,
                r.barangay_id,
                r.barangay_name,
                r.is_active,
                r.created_at,
                b.name AS linked_barangay_name
            FROM residents r
            LEFT JOIN barangays b ON b.id = r.barangay_id
            ORDER BY r.created_at DESC, r.id DESC
        """)
        residents = [(row, {"name": row.get("linked_barangay_name") or row.get("barangay_name") or "-"}) for row in cur.fetchall()]
        return render_template("manage_residents.html", residents=residents, title="Residents")
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/manage_residents/<int:resident_id>/edit", methods=["GET", "POST"])
def edit_resident(resident_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)

        if request.method == "POST":
            phone = (request.form.get("phone") or "").strip()
            barangay_id = (request.form.get("barangay_id") or "").strip()
            is_active = 1 if (request.form.get("is_active") or "1") == "1" else 0

            if not phone or not barangay_id:
                return "Phone and barangay are required", 400

            cur.execute("SELECT name FROM barangays WHERE id=%s", (barangay_id,))
            barangay = cur.fetchone()
            if not barangay:
                return "Invalid barangay selected", 400

            cur.execute("""
                UPDATE residents
                SET phone=%s,
                    barangay_id=%s,
                    barangay_name=%s,
                    is_active=%s
                WHERE id=%s
            """, (phone, barangay_id, barangay["name"], is_active, resident_id))
            conn.commit()

            log_action("edit_resident", "admin", session.get("admin_id"), f"resident_id={resident_id}")
            return redirect(url_for("manage_residents"))

        cur.execute("SELECT * FROM residents WHERE id=%s", (resident_id,))
        resident = cur.fetchone()
        if not resident:
            return "Resident not found", 404

        cur.execute("SELECT id, name FROM barangays ORDER BY name")
        barangays = cur.fetchall()

        return render_template(
            "edit_resident.html",
            resident=resident,
            barangays=barangays,
            title="Edit Resident",
        )
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/manage_residents/<int:resident_id>/toggle", methods=["POST"])
def toggle_resident(resident_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT is_active FROM residents WHERE id=%s", (resident_id,))
        resident = cur.fetchone()
        if not resident:
            return "Resident not found", 404

        next_status = 0 if resident["is_active"] else 1
        cur.execute("UPDATE residents SET is_active=%s WHERE id=%s", (next_status, resident_id))
        conn.commit()

        log_action("toggle_resident", "admin", session.get("admin_id"), f"resident_id={resident_id},is_active={next_status}")
        return redirect(url_for("manage_residents"))
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/manage_residents/<int:resident_id>/delete", methods=["POST"])
def delete_resident(resident_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM residents WHERE id=%s", (resident_id,))
        conn.commit()

        log_action("delete_resident", "admin", session.get("admin_id"), f"resident_id={resident_id}")
        return redirect(url_for("manage_residents"))
    finally:
        close_quietly(cur)
        close_quietly(conn)


# =====================================================================
# ADMINS (ADMIN WEB)
# =====================================================================
@app.route("/manage_admin")
def manage_admin_alias():
    return redirect(url_for("manage_admins"))


@app.route("/manage_admins")
def manage_admins():
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, username, full_name, created_at FROM admins ORDER BY created_at DESC, id DESC")
        admins = cur.fetchall()
        return render_template("manage_admin.html", admins=admins, title="Admins")
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/manage_admins/add", methods=["GET", "POST"])
def add_admin():
    if not require_admin():
        return redirect(url_for("admin_login"))

    error = None
    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)

        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            full_name = (request.form.get("full_name") or "").strip()
            password = request.form.get("password") or ""

            if not username or not password:
                error = "Username and password are required."
            else:
                cur.execute("SELECT id FROM admins WHERE username=%s", (username,))
                exists = cur.fetchone()
                if exists:
                    error = "Username already exists."
                else:
                    cur.execute("""
                        INSERT INTO admins (username, password, full_name)
                        VALUES (%s, %s, %s)
                    """, (username, generate_password_hash(password), full_name or None))
                    conn.commit()
                    log_action("add_admin", "admin", session.get("admin_id"), f"username={username}")
                    return redirect(url_for("manage_admins"))

        return render_template("add_admin.html", error=error, title="Add Admin")
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/manage_admins/<int:admin_id>/reset", methods=["POST"])
def reset_admin_password(admin_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    new_password = request.form.get("new_password") or ""
    if not new_password.strip():
        return redirect(url_for("manage_admins"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor()
        cur.execute("UPDATE admins SET password=%s WHERE id=%s", (generate_password_hash(new_password), admin_id))
        conn.commit()
        log_action("reset_admin_password", "admin", session.get("admin_id"), f"admin_id={admin_id}")
        return redirect(url_for("manage_admins"))
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/manage_admins/<int:admin_id>/delete", methods=["POST"])
def delete_admin(admin_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    if admin_id == session.get("admin_id"):
        return redirect(url_for("manage_admins"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT COUNT(*) AS total_admins FROM admins")
        total_admins = cur.fetchone()["total_admins"]
        if total_admins <= 1:
            return redirect(url_for("manage_admins"))

        cur.execute("DELETE FROM admins WHERE id=%s", (admin_id,))
        conn.commit()
        log_action("delete_admin", "admin", session.get("admin_id"), f"admin_id={admin_id}")
        return redirect(url_for("manage_admins"))
    finally:
        close_quietly(cur)
        close_quietly(conn)


# =====================================================================
# SCHEDULE MANAGEMENT (ADMIN WEB)
# =====================================================================
@app.route("/schedule/<int:schedule_id>/edit", methods=["GET", "POST"])
def edit_schedule(schedule_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)

        if request.method == "POST":
            barangay_id = (request.form.get("barangay_id") or "").strip()
            collection_date = (request.form.get("collection_date") or "").strip()
            collection_time = (request.form.get("collection_time") or "").strip()
            waste_type = (request.form.get("waste_type") or "").strip()
            notes = (request.form.get("notes") or "").strip()
            status = (request.form.get("status") or "scheduled").strip()

            cur.execute("SELECT name FROM barangays WHERE id=%s", (barangay_id,))
            barangay = cur.fetchone()
            if not barangay:
                return "Invalid barangay selected", 400

            cur.execute("""
                UPDATE schedules
                SET barangay_id=%s,
                    barangay_name=%s,
                    collection_date=%s,
                    collection_time=%s,
                    waste_type=%s,
                    notes=%s,
                    status=%s
                WHERE id=%s
            """, (
                barangay_id,
                barangay["name"],
                collection_date,
                collection_time,
                waste_type or None,
                notes or None,
                status,
                schedule_id,
            ))
            conn.commit()

            log_action("edit_schedule", "admin", session.get("admin_id"), f"schedule_id={schedule_id}")
            return redirect(url_for("admin_dashboard"))

        cur.execute("SELECT * FROM schedules WHERE id=%s", (schedule_id,))
        schedule = cur.fetchone()
        if not schedule:
            return "Schedule not found", 404

        if isinstance(schedule.get("collection_date"), (datetime, date)):
            schedule["collection_date"] = schedule["collection_date"].isoformat()
        schedule["collection_time"] = str(schedule.get("collection_time"))[:5]

        cur.execute("SELECT id, name FROM barangays ORDER BY name")
        barangays = cur.fetchall()

        return render_template(
            "edit_schedule.html",
            schedule=schedule,
            barangays=barangays,
            title="Edit Schedule",
        )
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/schedule/<int:schedule_id>/done", methods=["POST"])
def mark_schedule_done(schedule_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor()
        cur.execute("UPDATE schedules SET status='done' WHERE id=%s", (schedule_id,))
        conn.commit()
        log_action("mark_schedule_done", "admin", session.get("admin_id"), f"schedule_id={schedule_id}")
        return redirect(url_for("admin_dashboard"))
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/schedule/<int:schedule_id>/delete", methods=["POST"])
def delete_schedule(schedule_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM schedules WHERE id=%s", (schedule_id,))
        conn.commit()
        log_action("delete_schedule", "admin", session.get("admin_id"), f"schedule_id={schedule_id}")
        return redirect(url_for("admin_dashboard"))
    finally:
        close_quietly(cur)
        close_quietly(conn)


# =====================================================================
# MESSAGES (ADMIN WEB)
# =====================================================================
@app.route("/messages", methods=["GET"])
def manage_messages():
    if not require_admin():
        return redirect(url_for("admin_login"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT *
            FROM messages
            ORDER BY created_at DESC, id DESC
        """)
        messages = cur.fetchall()
        return render_template("manage_messages.html", messages=messages, title="Messages")
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/messages/<int:message_id>/reply", methods=["POST"])
def reply_message(message_id):
    if not require_admin():
        return redirect(url_for("admin_login"))

    admin_reply = (request.form.get("admin_reply") or "").strip()
    if not admin_reply:
        return redirect(url_for("manage_messages"))

    conn = get_db_connection()
    if not conn:
        return "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("""
            SELECT m.resident_phone, m.resident_barangay_id, r.fcm_token
            FROM messages m
            LEFT JOIN residents r ON r.phone = m.resident_phone
            WHERE m.id=%s
        """, (message_id,))
        message_row = cur.fetchone()

        cur.execute("""
            UPDATE messages
            SET admin_reply=%s, replied_at=NOW()
            WHERE id=%s
        """, (admin_reply, message_id))
        conn.commit()

        if message_row and message_row.get("fcm_token"):
            send_notification_to_token(
                token=message_row["fcm_token"],
                title="Admin replied to your message",
                body=admin_reply,
                data={
                    "type": "admin_reply",
                    "message_id": message_id,
                    "barangay_id": message_row.get("resident_barangay_id"),
                },
            )

        log_action("reply_message", "admin", session.get("admin_id"), f"message_id={message_id}")
        return redirect(url_for("manage_messages"))
    finally:
        close_quietly(cur)
        close_quietly(conn)


# =====================================================================
# FLUTTER API
# =====================================================================
@app.route("/api/barangays", methods=["GET"])
def api_barangays():
    conn = get_db_connection()
    if not conn:
        return jsonify([]), 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, name, code, created_at FROM barangays ORDER BY name")
        rows = cur.fetchall()

        for row in rows:
            row["created_at"] = str(row.get("created_at"))

        return jsonify(rows), 200
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/schedules", methods=["GET"])
def api_schedules():
    conn = get_db_connection()
    if not conn:
        return jsonify([]), 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT *
            FROM schedules
            WHERE status='scheduled'
            ORDER BY collection_date, collection_time
        """)
        schedules = cur.fetchall()
        schedules = [format_schedule_row(s) for s in schedules]
        return jsonify(schedules), 200
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/announcements", methods=["GET"])
def api_announcements():
    conn = get_db_connection()
    if not conn:
        return jsonify([]), 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, title, message, target_barangay_id, target_barangay_name, created_at
            FROM announcements
            ORDER BY created_at DESC
        """)
        rows = cur.fetchall()

        for a in rows:
            a["created_at"] = str(a.get("created_at"))

        return jsonify(rows), 200
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/residents/register", methods=["POST"])
def api_register_resident():
    data = request.get_json() or {}
    phone = (data.get("phone") or "").strip()
    barangay_id = data.get("barangay_id")
    fcm_token = data.get("fcm_token")

    if not phone or not barangay_id:
        return jsonify({"error": "Invalid data"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB error"}), 500

    cur = None
    cur2 = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("SELECT name FROM barangays WHERE id=%s", (barangay_id,))
        row = cur.fetchone()
        barangay_name = row["name"] if row else None

        if not barangay_name:
            return jsonify({"error": "Invalid barangay_id"}), 400

        cur2 = conn.cursor()
        cur2.execute("""
            INSERT INTO residents (phone, barangay_id, barangay_name, fcm_token)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                barangay_id = VALUES(barangay_id),
                barangay_name = VALUES(barangay_name),
                fcm_token = VALUES(fcm_token)
        """, (phone, barangay_id, barangay_name, fcm_token))
        conn.commit()

        return jsonify({"message": "Registered"}), 201
    finally:
        close_quietly(cur2)
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/messages", methods=["GET"])
def api_get_messages():
    phone = (request.args.get("phone") or "").strip()
    if not phone:
        return jsonify({"error": "phone is required"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB error"}), 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT id, resident_phone, resident_barangay_id, resident_barangay_name,
                   category, message, admin_reply, created_at, replied_at
            FROM messages
            WHERE resident_phone=%s
            ORDER BY created_at DESC, id DESC
        """, (phone,))
        rows = cur.fetchall()

        for row in rows:
            row["created_at"] = str(row.get("created_at"))
            row["replied_at"] = str(row.get("replied_at")) if row.get("replied_at") else None

        return jsonify(rows), 200
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/messages", methods=["POST"])
def api_send_message():
    data = request.get_json() or {}
    phone = (data.get("phone") or "").strip()
    barangay_id = data.get("barangay_id")
    category = (data.get("category") or "").strip()
    message = (data.get("message") or "").strip()

    if not phone or not barangay_id or not category or not message:
        return jsonify({"error": "Invalid data"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB error"}), 500

    cur = None
    cur2 = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("SELECT name FROM barangays WHERE id=%s", (barangay_id,))
        row = cur.fetchone()
        barangay_name = row["name"] if row else None

        if not barangay_name:
            return jsonify({"error": "Invalid barangay_id"}), 400

        cur2 = conn.cursor()
        cur2.execute("""
            INSERT INTO messages (
                resident_phone, resident_barangay_id, resident_barangay_name,
                category, message, created_at
            )
            VALUES (%s, %s, %s, %s, %s, NOW())
        """, (phone, barangay_id, barangay_name, category, message))
        conn.commit()

        safe_send_topic(
            topic=driver_topic_for_barangay(barangay_id),
            title=f"New {category} message",
            body=f"{barangay_name}: {message}",
            data={
                "type": "resident_message",
                "phone": phone,
                "barangay_id": barangay_id,
                "barangay_name": barangay_name,
                "category": category,
            },
        )

        return jsonify({"message": "Sent"}), 201
    finally:
        close_quietly(cur2)
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/residents/ready-state", methods=["GET"])
def api_ready_state():
    phone = (request.args.get("phone") or "").strip()
    barangay_id = (request.args.get("barangay_id") or "").strip()

    if not phone or not barangay_id:
        return jsonify({"error": "phone and barangay_id are required"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB error"}), 500

    cur = None
    try:
        schedule = get_current_schedule_for_barangay(conn, barangay_id)
        if not schedule:
            return jsonify({"is_ready": False, "schedule_id": None}), 200

        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT is_ready
            FROM ready_markers
            WHERE schedule_id=%s AND resident_phone=%s
        """, (schedule["id"], phone))
        row = cur.fetchone()

        return jsonify({
            "is_ready": bool(row["is_ready"]) if row else False,
            "schedule_id": schedule["id"],
        }), 200
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/residents/ready-toggle", methods=["POST"])
def api_ready_toggle():
    data = request.get_json() or {}
    phone = (data.get("phone") or "").strip()
    barangay_id = data.get("barangay_id")
    latitude = data.get("latitude")
    longitude = data.get("longitude")

    if not phone or not barangay_id or latitude is None or longitude is None:
        return jsonify({"error": "Invalid data"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB error"}), 500

    cur = None
    cur2 = None
    try:
        schedule = get_current_schedule_for_barangay(conn, barangay_id)
        if not schedule:
            return jsonify({"error": "No scheduled collection for this barangay"}), 400

        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("SELECT name FROM barangays WHERE id=%s", (barangay_id,))
        barangay = cur.fetchone()
        if not barangay:
            return jsonify({"error": "Invalid barangay_id"}), 400

        cur.execute("""
            SELECT id, is_ready
            FROM ready_markers
            WHERE schedule_id=%s AND resident_phone=%s
        """, (schedule["id"], phone))
        existing = cur.fetchone()

        next_is_ready = 0 if existing and existing["is_ready"] else 1

        cur2 = conn.cursor()
        cur2.execute("""
            INSERT INTO ready_markers (
                schedule_id, resident_phone, barangay_id, barangay_name,
                latitude, longitude, is_ready, created_at, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
            ON DUPLICATE KEY UPDATE
                barangay_id = VALUES(barangay_id),
                barangay_name = VALUES(barangay_name),
                latitude = VALUES(latitude),
                longitude = VALUES(longitude),
                is_ready = VALUES(is_ready),
                updated_at = NOW()
        """, (
            schedule["id"],
            phone,
            barangay_id,
            barangay["name"],
            latitude,
            longitude,
            next_is_ready,
        ))
        conn.commit()

        return jsonify({
            "is_ready": bool(next_is_ready),
            "schedule_id": schedule["id"],
        }), 200
    finally:
        close_quietly(cur2)
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/truck/location", methods=["GET"])
def api_get_truck_location():
    barangay_id = (request.args.get("barangay_id") or "").strip()
    if not barangay_id:
        return jsonify({"error": "barangay_id is required"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB error"}), 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT *
            FROM truck_locations
            WHERE barangay_id=%s
        """, (barangay_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({}), 200

        row["updated_at"] = str(row.get("updated_at"))
        return jsonify(row), 200
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/truck/location", methods=["POST"])
def api_update_truck_location():
    data = request.get_json() or {}
    barangay_id = data.get("barangay_id")
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    collection_update = (data.get("collection_update") or "Monitoring").strip()
    notes = (data.get("notes") or "").strip()

    if not barangay_id or latitude is None or longitude is None:
        return jsonify({"error": "Invalid data"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB error"}), 500

    cur = None
    cur2 = None
    try:
        cur = conn.cursor(dictionary=True, buffered=True)
        cur.execute("SELECT name FROM barangays WHERE id=%s", (barangay_id,))
        barangay = cur.fetchone()
        if not barangay:
            return jsonify({"error": "Invalid barangay_id"}), 400

        cur2 = conn.cursor()
        cur2.execute("""
            INSERT INTO truck_locations (
                barangay_id, barangay_name, latitude, longitude,
                collection_update, notes, updated_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE
                barangay_name = VALUES(barangay_name),
                latitude = VALUES(latitude),
                longitude = VALUES(longitude),
                collection_update = VALUES(collection_update),
                notes = VALUES(notes),
                updated_at = NOW()
        """, (
            barangay_id,
            barangay["name"],
            latitude,
            longitude,
            collection_update,
            notes or None,
        ))
        conn.commit()

        alerted = notify_nearby_ready_residents(conn, barangay_id, latitude, longitude)

        return jsonify({"message": "Updated", "nearby_alerts_sent": alerted}), 200
    finally:
        close_quietly(cur2)
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/truck/ready-residents", methods=["GET"])
def api_ready_residents():
    barangay_id = (request.args.get("barangay_id") or "").strip()
    if not barangay_id:
        return jsonify({"error": "barangay_id is required"}), 400

    conn = get_db_connection()
    if not conn:
        return jsonify({"error": "DB error"}), 500

    cur = None
    try:
        schedule = get_current_schedule_for_barangay(conn, barangay_id)
        if not schedule:
            return jsonify([]), 200

        cur = conn.cursor(dictionary=True)
        cur.execute("""
            SELECT resident_phone, barangay_name, latitude, longitude, updated_at
            FROM ready_markers
            WHERE schedule_id=%s AND is_ready=1
            ORDER BY updated_at DESC
        """, (schedule["id"],))
        rows = cur.fetchall()

        for row in rows:
            row["updated_at"] = str(row.get("updated_at"))
            row["schedule_id"] = schedule["id"]

        return jsonify(rows), 200
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/api/driver/notifications", methods=["GET"])
def api_driver_notifications():
    barangay_id = (request.args.get("barangay_id") or "").strip()
    if not barangay_id:
        return jsonify([]), 400

    conn = get_db_connection()
    if not conn:
        return jsonify([]), 500

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        notifications = []

        schedule = get_current_schedule_for_barangay(conn, barangay_id)
        if schedule:
            notifications.append({
                "type": "schedule",
                "title": "Current Collection Schedule",
                "body": f"{schedule['barangay_name']}: {schedule['collection_date']} at {schedule['collection_time']}",
                "created_at": schedule.get("created_at"),
            })

            cur.execute("""
                SELECT COUNT(*) AS ready_count
                FROM ready_markers
                WHERE schedule_id=%s AND is_ready=1
            """, (schedule["id"],))
            ready_count = cur.fetchone()["ready_count"]
            notifications.append({
                "type": "ready",
                "title": "Residents Ready",
                "body": f"{ready_count} residents have marked ready for pickup.",
                "created_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
            })

        cur.execute("""
            SELECT category, message, created_at
            FROM messages
            WHERE resident_barangay_id=%s
            ORDER BY created_at DESC
            LIMIT 5
        """, (barangay_id,))
        for row in cur.fetchall():
            notifications.append({
                "type": "message",
                "title": row["category"],
                "body": row["message"],
                "created_at": str(row["created_at"]),
            })

        notifications.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return jsonify(notifications), 200
    finally:
        close_quietly(cur)
        close_quietly(conn)


# =====================================================================
# DEBUG ROUTES
# =====================================================================
@app.route("/debug/create-admin")
def create_admin():
    """
    Creates admin / admin123 if not exists.
    """
    if not env_flag("ALLOW_DEBUG_ROUTES"):
        return "Debug routes are disabled", 404

    ensure_tables()

    conn = get_db_connection()
    if not conn:
        return get_last_db_error() or "Database connection failed", 500

    cur = None
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT IGNORE INTO admins (username, password, full_name)
            VALUES (%s, %s, %s)
        """, ("admin", generate_password_hash("admin123"), "System Administrator"))
        conn.commit()
        return "ADMIN CREATED (admin / admin123)"
    finally:
        close_quietly(cur)
        close_quietly(conn)


@app.route("/debug/ensure-tables")
def debug_ensure_tables():
    if not env_flag("ALLOW_DEBUG_ROUTES"):
        return "Debug routes are disabled", 404

    ensure_tables()
    return jsonify({"ok": True, "message": "Tables checked and default barangays seeded when empty"})


@app.route("/debug/fcm-test")
def fcm_test():
    if not env_flag("ALLOW_DEBUG_ROUTES"):
        return "Debug routes are disabled", 404

    safe_send_topic(
        topic="all_residents",
        title="FCM Test",
        body="If you see this, FCM is working ✅",
        data={"type": "test"},
    )
    return jsonify({"sent": True})


# =====================================================================
# SCHEDULER
# =====================================================================
def scheduler_task():
    log_action("scheduler_run", "system")


scheduler = BackgroundScheduler()
scheduler.add_job(scheduler_task, "interval", hours=1)

if os.environ.get("WERKZEUG_RUN_MAIN") != "true":
    scheduler.start()

atexit.register(lambda: scheduler.shutdown())


# =====================================================================
# START
# =====================================================================
if __name__ == "__main__":
    print("Starting Waste Collection backend...")

    try:
        ensure_tables()
        print("[STARTUP] Database tables checked successfully")
    except Exception as e:
        print(f"[STARTUP ERROR] ensure_tables failed: {e}")

    port = int(os.getenv("PORT", "5000"))
    debug = env_flag("FLASK_DEBUG", default=False)
    app.run(host="0.0.0.0", port=port, debug=debug)
