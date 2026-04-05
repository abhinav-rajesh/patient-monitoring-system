from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory, make_response
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
import threading
import time
import random
import math
import json
import os
import collections
from datetime import datetime

# Provide Gemini API key globally here
os.environ["GEMINI_API_KEY"] = "AIzaSyDBV9N9OIAlc_Cq24vtBSP8aPG3oqYI_Co"

# ── Firebase Admin FCM (For Flutter App) ──────────────────────────
try:
    import firebase_admin
    from firebase_admin import credentials, messaging
    cred = credentials.Certificate("serviceAccountKey.json")
    firebase_admin.initialize_app(cred)
    FCM_AVAILABLE = True
    print("[FCM] Firebase Admin initialized ✅")
except Exception as e:
    FCM_AVAILABLE = False
    print(f"[FCM] Firebase Admin not initialized ({e}) ⚠️")

# ── FCM Token Store ─────────────────────────────────────────────
# fcm_tokens have been migrated to the database.

# ── pywebpush (For PWA) ──────────────────────────────────────────
try:
    from pywebpush import webpush, WebPushException
    PUSH_AVAILABLE = True
except ImportError:
    PUSH_AVAILABLE = False
    print("[WARN] pywebpush not available – web push disabled")

app = Flask(__name__)
app.secret_key = 'hospital_secret_key_2024'

# ─── Database Configuration ────────────────────────────────────────────────────
# Try MySQL first, fall back to SQLite

MYSQL_USER     = os.environ.get("MYSQL_USER",     "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD",  "")
MYSQL_HOST     = os.environ.get("MYSQL_HOST",     "localhost")
MYSQL_PORT     = os.environ.get("MYSQL_PORT",     "3306")
MYSQL_DB       = os.environ.get("MYSQL_DB",       "medwatch")

USING_MYSQL = False
try:
    import pymysql
    pymysql.install_as_MySQLdb()
    test_conn = pymysql.connect(
        host=MYSQL_HOST, port=int(MYSQL_PORT),
        user=MYSQL_USER, password=MYSQL_PASSWORD,
        connect_timeout=2
    )
    # Create DB if it doesn't exist
    with test_conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{MYSQL_DB}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    test_conn.commit()
    test_conn.close()

    DB_URI = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"
    USING_MYSQL = True
    print(f"[DB] ✅ MySQL connected at {MYSQL_HOST}:{MYSQL_PORT} — database: {MYSQL_DB}")
except Exception as e:
    DB_URI = "sqlite:///hospital.db"
    print(f"[DB] MySQL not available ({e}) — using SQLite fallback")

app.config['SQLALCHEMY_DATABASE_URI'] = DB_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 280,
    'pool_pre_ping': True,
}

db      = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ─── VAPID keys ────────────────────────────────────────────────────────────────
VAPID_EMAIL     = "mailto:medwatch@hospital.com"
VAPID_KEYS_FILE = os.path.join(os.path.dirname(__file__), 'vapid_keys.json')

try:
    with open(VAPID_KEYS_FILE) as f:
        _vk = json.load(f)
    VAPID_PUBLIC_KEY  = _vk['public_key']
    VAPID_PRIVATE_KEY = _vk['private_key']
    print(f"[VAPID] Public key loaded ({len(VAPID_PUBLIC_KEY)} chars)")
except Exception as e:
    VAPID_PUBLIC_KEY  = ""
    VAPID_PRIVATE_KEY = ""
    print(f"[WARN] VAPID keys not found: {e}")

# ─── Push subscription store ──────────────────────────────────────────────────
push_subscriptions: dict[str, list[dict]] = {}
push_lock = threading.Lock()
last_push_time: dict[str, float] = {}
PUSH_COOLDOWN = 30

# ─── Database Models ───────────────────────────────────────────────────────────

class Nurse(db.Model):
    """Nurse login credentials and profile — used for authentication."""
    __tablename__ = 'nurse'
    id            = db.Column(db.String(50),  primary_key=True)          # login username
    password      = db.Column(db.String(255), nullable=False)             # login password
    name          = db.Column(db.String(100), nullable=False)             # display name
    department    = db.Column(db.String(100), nullable=True)              # e.g. ICU, Cardiology
    phone         = db.Column(db.String(30),  nullable=True)              # contact number
    email         = db.Column(db.String(150), nullable=True)              # email
    created_at    = db.Column(db.DateTime, default=datetime.utcnow)       # account creation
    is_admin      = db.Column(db.Boolean, default=False)                  # admin privilege


class Patient(db.Model):
    """Patient records for display, allotment, and login-based access control."""
    __tablename__      = 'patient'
    id                 = db.Column(db.String(50),  primary_key=True)      # e.g. P001
    name               = db.Column(db.String(100), nullable=False)        # short label
    full_name          = db.Column(db.String(100), nullable=False)        # display name
    age                = db.Column(db.Integer, nullable=False)
    gender             = db.Column(db.String(20),  nullable=True)         # Male/Female/Other
    blood_group        = db.Column(db.String(10),  nullable=True)         # e.g. A+
    ward               = db.Column(db.String(50),  nullable=False)
    bed                = db.Column(db.String(50),  nullable=False)
    condition          = db.Column(db.String(200), nullable=False)
    diagnosis          = db.Column(db.String(300), nullable=True)         # Clinical diagnosis
    admission_date     = db.Column(db.DateTime, default=datetime.utcnow)  # when admitted
    bystander_name     = db.Column(db.String(100), nullable=True)
    bystander_number   = db.Column(db.String(50),  nullable=True)
    bystander_relation = db.Column(db.String(80),  nullable=True)         # e.g. Son, Wife


class Allotment(db.Model):
    """Maps which nurse is responsible for which patient (exclusive: 1 nurse per patient)."""
    __tablename__ = 'allotment'
    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nurse_id   = db.Column(db.String(50), db.ForeignKey('nurse.id'),   nullable=False)
    patient_id = db.Column(db.String(50), db.ForeignKey('patient.id'), nullable=False)
    allotted_at= db.Column(db.DateTime, default=datetime.utcnow)


class LoginLog(db.Model):
    """Tracks every nurse login event for audit purposes."""
    __tablename__ = 'login_log'
    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nurse_id   = db.Column(db.String(50), db.ForeignKey('nurse.id'), nullable=False)
    logged_in_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    ip_address = db.Column(db.String(60), nullable=True)


class VitalRecord(db.Model):
    """Stores real-time vital readings for every patient every ~10 seconds."""
    __tablename__ = 'vital_record'
    id             = db.Column(db.Integer, primary_key=True, autoincrement=True)
    patient_id     = db.Column(db.String(50), db.ForeignKey('patient.id'), nullable=False, index=True)
    recorded_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    heart_rate     = db.Column(db.Float, nullable=True)
    spo2           = db.Column(db.Float, nullable=True)
    temperature    = db.Column(db.Float, nullable=True)
    blood_pressure = db.Column(db.Float, nullable=True)
    is_simulated   = db.Column(db.Boolean, default=True)


class FCMToken(db.Model):
    """Stores Flutter FCM device tokens persistently for push notifications."""
    __tablename__ = 'fcm_token'
    id       = db.Column(db.Integer, primary_key=True, autoincrement=True)
    nurse_id = db.Column(db.String(50), db.ForeignKey('nurse.id'), nullable=False)
    token    = db.Column(db.String(255), unique=True, nullable=False)
    registered_at = db.Column(db.DateTime, default=datetime.utcnow)


def init_db():
    with app.app_context():
        db.create_all()
        if not Nurse.query.first():
            db.session.add_all([
                Nurse(id="admin",  password="admin123", name="Admin",
                      department="Administration", phone="9000000000",
                      email="admin@medwatch.local", is_admin=True),
                Nurse(id="nurse1", password="1234", name="Sarah Johnson",
                      department="Cardiology", phone="9876543220",
                      email="sarah@medwatch.local"),
                Nurse(id="nurse2", password="1234", name="Emily Chen",
                      department="ICU", phone="9876543221",
                      email="emily@medwatch.local"),
            ])
            db.session.add_all([
                Patient(id="P001", name="Patient A", full_name="James Thornton",
                        age=67, gender="Male",   blood_group="B+",
                        ward="Cardiology", bed="4B", condition="Post-Op Recovery",
                        diagnosis="Coronary Artery Bypass",
                        bystander_name="Mary Thornton",  bystander_number="9876543210",
                        bystander_relation="Wife"),
                Patient(id="P002", name="Patient B", full_name="Maria Santos",
                        age=54, gender="Female", blood_group="O+",
                        ward="General",   bed="7A", condition="Hypertension Watch",
                        diagnosis="Essential Hypertension",
                        bystander_name="Carlos Santos", bystander_number="9876543211",
                        bystander_relation="Husband"),
                Patient(id="P003", name="Patient C", full_name="Robert Kim",
                        age=72, gender="Male",   blood_group="A-",
                        ward="ICU",       bed="2C", condition="Respiratory Issue",
                        diagnosis="Chronic Obstructive Pulmonary Disease",
                        bystander_name="Susan Kim",     bystander_number="9876543212",
                        bystander_relation="Daughter"),
            ])
            db.session.commit()
            db.session.add_all([
                Allotment(nurse_id="nurse1", patient_id="P001"),
                Allotment(nurse_id="nurse1", patient_id="P002"),
                Allotment(nurse_id="nurse2", patient_id="P003"),
            ])
            db.session.commit()
        print(f"[DB] ✅ Tables ready ({'MySQL' if USING_MYSQL else 'SQLite'})")
        print(f"[DB]    Nurses: {Nurse.query.count()} | Patients: {Patient.query.count()} "
              f"| Allotments: {Allotment.query.count()} | VitalRecords: {VitalRecord.query.count()}")

# ─── Thresholds ────────────────────────────────────────────────────────────────
THRESHOLDS = {
    "heart_rate":     {"low": 50,   "high": 120},
    "spo2":           {"low": 92,   "high": 101},
    "temperature":    {"low": 35.0, "high": 38.0},
    "blood_pressure": {"low": 70,   "high": 150},
}
NORMAL_RANGES = {
    "heart_rate":     {"low": 60,   "high": 100},
    "spo2":           {"low": 95,   "high": 100},
    "temperature":    {"low": 36.0, "high": 37.5},
    "blood_pressure": {"low": 110,  "high": 130},
}

# ─── In-Memory Vital State & History ──────────────────────────────────────────
vital_state:   dict = {}
vital_history: dict = {}

def ensure_vital_state(pid: str):
    if pid not in vital_state:
        vital_state[pid] = {
            "heart_rate":      random.randint(65, 90),
            "spo2":            random.uniform(96, 99),
            "temperature":     random.uniform(36.2, 37.2),
            "blood_pressure":  random.randint(115, 128),
            "phase":           random.uniform(0, 6.28),
            "crisis_mode":     False,
            "crisis_timer":    0,
            "crisis_vital":    None,
            "last_real_update": 0,
        }
    if pid not in vital_history:
        vital_history[pid] = {
            "heart_rate":     collections.deque(maxlen=60),
            "spo2":           collections.deque(maxlen=60),
            "temperature":    collections.deque(maxlen=60),
            "blood_pressure": collections.deque(maxlen=60),
        }

# ─── Vital Generation ──────────────────────────────────────────────────────────
def generate_vitals(pid: str, state: dict) -> dict:
    t = state["phase"]
    state["phase"] += 0.15

    if not state["crisis_mode"] and random.random() < 0.008:
        state["crisis_mode"]  = True
        state["crisis_timer"] = random.randint(5, 12)
        state["crisis_vital"] = random.choice(["heart_rate", "spo2", "temperature", "blood_pressure"])

    if state["crisis_mode"]:
        state["crisis_timer"] -= 1
        if state["crisis_timer"] <= 0:
            state["crisis_mode"]  = False
            state["crisis_vital"] = None

    if state["crisis_mode"] and state["crisis_vital"] == "heart_rate":
        target_hr = random.choice([random.randint(30, 48), random.randint(122, 145)])
    else:
        target_hr = 78 + 10 * math.sin(t * 0.7) + random.gauss(0, 3)
    state["heart_rate"] = max(30, min(160, state["heart_rate"] * 0.85 + target_hr * 0.15))

    if state["crisis_mode"] and state["crisis_vital"] == "spo2":
        target_spo2 = random.uniform(87, 91)
    else:
        target_spo2 = 97.5 + random.gauss(0, 0.5)
    state["spo2"] = max(80, min(100, state["spo2"] * 0.9 + target_spo2 * 0.1))

    if state["crisis_mode"] and state["crisis_vital"] == "temperature":
        target_temp = random.uniform(38.2, 39.5)
    else:
        target_temp = 36.8 + random.gauss(0, 0.1)
    state["temperature"] = max(34, min(42, state["temperature"] * 0.95 + target_temp * 0.05))

    if state["crisis_mode"] and state["crisis_vital"] == "blood_pressure":
        target_bp = random.randint(155, 185)
    else:
        target_bp = 120 + 5 * math.sin(t * 0.4) + random.gauss(0, 3)
    state["blood_pressure"] = max(50, min(220, state["blood_pressure"] * 0.85 + target_bp * 0.15))

    return {
        "heart_rate":     round(state["heart_rate"], 1),
        "spo2":           round(state["spo2"], 1),
        "temperature":    round(state["temperature"], 2),
        "blood_pressure": round(state["blood_pressure"], 1),
    }

# ─── Alert Checking ────────────────────────────────────────────────────────────
def check_alerts(pid: str, vitals: dict) -> list:
    alerts = []
    with app.app_context():
        patient = db.session.get(Patient, pid)
        if not patient:
            return []
        p_name = patient.full_name
        nurses = [al.nurse_id for al in Allotment.query.filter_by(patient_id=pid).all()]

    checks = [
        ("heart_rate",     vitals["heart_rate"],     "bpm",  "Heart Rate",     50,   120),
        ("spo2",           vitals["spo2"],           "%",    "SpO₂",           92,   101),
        ("temperature",    vitals["temperature"],    "°C",   "Temperature",    35.0, 38.0),
        ("blood_pressure", vitals["blood_pressure"], "mmHg", "Blood Pressure", 70,   150),
    ]

    ensure_vital_state(pid)
    for key, value, unit, label, abs_low, abs_high in checks:
        history   = vital_history[pid][key]
        level     = None
        direction = None

        if len(history) > 15:
            mean     = sum(history) / len(history)
            variance = sum((x - mean) ** 2 for x in history) / len(history)
            std_dev  = max(math.sqrt(variance), 1.5)
            z_score  = abs(value - mean) / std_dev

            if z_score > 3.5 or value < abs_low * 0.9 or value > abs_high * 1.1:
                level     = "critical"
                direction = f"SUDDEN SHIFT (avg: {mean:.1f})" if z_score > 3.5 else \
                            "CRITICALLY " + ("LOW" if value < abs_low else "HIGH")
            elif z_score > 2.5 or value < abs_low or value > abs_high:
                level     = "warning"
                direction = f"ABNORMAL TREND (baseline: {mean:.1f})" if z_score > 2.5 else \
                            ("LOW" if value < abs_low else "HIGH")
        else:
            if value < abs_low or value > abs_high:
                level     = "critical" if (value < abs_low * 0.9 or value > abs_high * 1.1) else "warning"
                direction = "LOW" if value < abs_low else "HIGH"

        history.append(value)

        if level:
            alerts.append({
                "patient_id":   pid,
                "patient_name": p_name,
                "vital":        label,
                "vital_key":    key,
                "value":        f"{value} {unit}",
                "direction":    direction,
                "level":        level,
                "nurses":       nurses,
            })

    return alerts

# ─── Web Push ──────────────────────────────────────────────────────────────────
def should_send_push(nurse_id: str, patient_id: str, vital_key: str) -> bool:
    key = f"{nurse_id}:{patient_id}:{vital_key}"
    now = time.time()
    with push_lock:
        if now - last_push_time.get(key, 0) < PUSH_COOLDOWN:
            return False
        last_push_time[key] = now
        return True

def send_push_to_nurse(nurse_id: str, alert: dict):
    icon    = "🚨" if alert["level"] == "critical" else "⚠️"
    title   = f"{icon} {'CRITICAL' if alert['level'] == 'critical' else 'WARNING'} – {alert['patient_name']}"
    body    = f"{alert['vital']}: {alert['value']} ({alert['direction']})"

    # 1) Send PWA Web Push
    if PUSH_AVAILABLE and VAPID_PRIVATE_KEY:
        with push_lock:
            subs = list(push_subscriptions.get(nurse_id, []))
        if subs:
            payload = {
                "title":   title,
                "body":    body,
                "level":   alert["level"],
                "patient": alert["patient_name"],
                "vital":   alert["vital"],
                "value":   alert["value"],
                "tag":     f"{alert['patient_id']}-{alert['vital_key']}",
                "url":     "/mobile",
            }
            dead_subs = []
            for sub in subs:
                try:
                    webpush(subscription_info=sub, data=json.dumps(payload),
                            vapid_private_key=VAPID_PRIVATE_KEY,
                            vapid_claims={"sub": VAPID_EMAIL})
                except WebPushException as ex:
                    code = ex.response.status_code if ex.response else None
                    if code in (404, 410):
                        dead_subs.append(sub)
                except Exception:
                    pass

            if dead_subs:
                with push_lock:
                    current = push_subscriptions.get(nurse_id, [])
                    push_subscriptions[nurse_id] = [s for s in current if s not in dead_subs]

    # 3) Send Native Flutter FCM Push (NEW)
    if FCM_AVAILABLE:
        try:
            with app.app_context():
                db_tokens = FCMToken.query.filter_by(nurse_id=nurse_id).all()
                tokens = [t.token for t in db_tokens]
            
            if tokens:
                for token in tokens:
                    try:
                        message = messaging.Message(
                            notification=messaging.Notification(
                                title=title,
                                body=body,
                            ),
                            data={
                                "patient_id": str(alert["patient_id"]),
                                "level": alert["level"],
                            },
                            token=token,
                            android=messaging.AndroidConfig(
                                priority='high',
                                notification=messaging.AndroidNotification(
                                    channel_id='critical_alerts',
                                    sound='default',
                                    priority='high',
                                ),
                            ),
                        )
                        messaging.send(message)
                    except Exception as token_err:
                        print(f"[FCM WARN] Failed sending to token {token}: {token_err}")
                # print(f"[FCM] Sent to {len(tokens)} devices for {nurse_id}")
        except Exception as e:
            print(f"[FCM PUSH ERROR] {e}")

# ─── DB Vital Persistence (background writer) ──────────────────────────────────
_vital_write_queue: list = []
_vital_write_lock  = threading.Lock()
VITAL_SAVE_INTERVAL = 10  # save to DB every N seconds

def _vital_db_writer():
    """Background thread that batch-writes queued vitals to the database."""
    while True:
        time.sleep(VITAL_SAVE_INTERVAL)
        with _vital_write_lock:
            batch = list(_vital_write_queue)
            _vital_write_queue.clear()
        if batch:
            try:
                with app.app_context():
                    db.session.bulk_insert_mappings(VitalRecord, batch)
                    db.session.commit()
                    print(f"[DB] Saved {len(batch)} vital records")
            except Exception as e:
                print(f"[DB WARN] Failed writing vitals: {e}")

def queue_vital_record(pid: str, vitals: dict, simulated: bool = True):
    record = {
        "patient_id":     pid,
        "recorded_at":    datetime.utcnow(),
        "heart_rate":     vitals.get("heart_rate"),
        "spo2":           vitals.get("spo2"),
        "temperature":    vitals.get("temperature"),
        "blood_pressure": vitals.get("blood_pressure"),
        "is_simulated":   simulated,
    }
    with _vital_write_lock:
        _vital_write_queue.append(record)

# ─── Simulation Loop ───────────────────────────────────────────────────────────
def vital_simulation_loop():
    while True:
        with app.app_context():
            pids = [p.id for p in Patient.query.all()]

        for pid in pids:
            ensure_vital_state(pid)
            state = vital_state[pid]
            now   = time.time()

            is_real = (now - state.get("last_real_update", 0)) < 10
            if is_real:
                vitals = {k: state[k] for k in ["heart_rate", "spo2", "temperature", "blood_pressure"]}
            else:
                vitals = generate_vitals(pid, state)

            # Queue vitals to be saved to DB
            queue_vital_record(pid, vitals, simulated=not is_real)

            alerts  = check_alerts(pid, vitals)
            payload = {"patient_id": pid, "vitals": vitals, "alerts": alerts}
            socketio.emit("vital_update", payload)

            for alert in alerts:
                socketio.emit("alert_event", alert)
                for nurse_id in alert["nurses"]:
                    if should_send_push(nurse_id, pid, alert["vital_key"]):
                        threading.Thread(target=send_push_to_nurse,
                                         args=(nurse_id, alert), daemon=True).start()

        time.sleep(2)

# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        nurse    = db.session.get(Nurse, username)
        if nurse and nurse.password == password:
            session["nurse"]      = nurse.id
            session["nurse_name"] = nurse.name
            session["is_admin"]   = nurse.is_admin
            # ── Record login in DB ────────────────────────────────────────────
            log = LoginLog(
                nurse_id   = nurse.id,
                ip_address = request.remote_addr,
            )
            db.session.add(log)
            db.session.commit()
            # Admin goes to admin panel, nurses go to dashboard
            if nurse.is_admin:
                return redirect(url_for("admin_panel"))
            return redirect(url_for("dashboard"))
        error = "Invalid credentials. Please try again."
    return render_template("login.html", error=error)


# ─── Admin Panel ────────────────────────────────────────────────────────────────

@app.route("/admin")
def admin_panel():
    if "nurse" not in session:
        return redirect(url_for("login"))
    if not session.get("is_admin"):
        return redirect(url_for("dashboard"))

    nurses   = Nurse.query.order_by(Nurse.name).all()
    patients = Patient.query.order_by(Patient.full_name).all()
    allots   = Allotment.query.all()
    logs     = (LoginLog.query
                .order_by(LoginLog.logged_in_at.desc())
                .limit(30).all())
    vital_count = VitalRecord.query.count()

    # Build allotment map: patient_id -> nurse name
    allot_map = {}
    for a in allots:
        n = db.session.get(Nurse, a.nurse_id)
        allot_map[a.patient_id] = n.name if n else a.nurse_id

    # Enrich login logs with nurse names
    log_entries = []
    for log in logs:
        n = db.session.get(Nurse, log.nurse_id)
        log_entries.append({
            "nurse_name":   n.name if n else log.nurse_id,
            "nurse_id":     log.nurse_id,
            "logged_in_at": log.logged_in_at.strftime("%Y-%m-%d %H:%M:%S"),
            "ip_address":   log.ip_address or "—",
        })

    return render_template(
        "admin.html",
        nurses=nurses,
        patients=patients,
        allot_map=allot_map,
        log_entries=log_entries,
        vital_count=vital_count,
        using_mysql=USING_MYSQL,
        db_uri=DB_URI.split("@")[-1] if "@" in DB_URI else DB_URI,
    )


@app.route("/api/admin/nurse/<nid>", methods=["DELETE"])
def admin_delete_nurse(nid):
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403
    if nid == session["nurse"]:
        return jsonify({"error": "Cannot delete yourself"}), 400
    Allotment.query.filter_by(nurse_id=nid).delete()
    LoginLog.query.filter_by(nurse_id=nid).delete()
    nurse = db.session.get(Nurse, nid)
    if nurse:
        db.session.delete(nurse)
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/nurse/<nid>/reset_password", methods=["POST"])
def admin_reset_password(nid):
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403
    data     = request.get_json(force=True)
    new_pass = data.get("password", "").strip()
    if not new_pass:
        return jsonify({"error": "Password cannot be empty"}), 400
    nurse = db.session.get(Nurse, nid)
    if not nurse:
        return jsonify({"error": "Nurse not found"}), 404
    nurse.password = new_pass
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/admin/patient/<pid>", methods=["DELETE"])
def admin_delete_patient(pid):
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403
    VitalRecord.query.filter_by(patient_id=pid).delete()
    Allotment.query.filter_by(patient_id=pid).delete()
    patient = db.session.get(Patient, pid)
    if patient:
        db.session.delete(patient)
    db.session.commit()
    vital_state.pop(pid, None)
    vital_history.pop(pid, None)
    return jsonify({"success": True})


@app.route("/api/admin/stats")
def admin_stats():
    if not session.get("is_admin"):
        return jsonify({"error": "Unauthorized"}), 403
    return jsonify({
        "nurses":        Nurse.query.count(),
        "patients":      Patient.query.count(),
        "allotments":    Allotment.query.count(),
        "vital_records": VitalRecord.query.count(),
        "login_logs":    LoginLog.query.count(),
        "using_mysql":   USING_MYSQL,
    })


@app.route("/api/register_expo_push", methods=["POST"])
def register_expo_push():
    data = request.get_json(force=True)
    nurse_id = data.get("nurse_id")
    token = data.get("token")
    if not nurse_id or not token:
        return jsonify({"error": "nurse_id and token required"}), 400
    with push_lock:
        if nurse_id not in expo_push_tokens:
            expo_push_tokens[nurse_id] = set()
        expo_push_tokens[nurse_id].add(token)
    return jsonify({"success": True})


@app.route("/dashboard")
def dashboard():
    if "nurse" not in session:
        return redirect(url_for("login"))
    nurse_id = session["nurse"]
    nurse    = db.session.get(Nurse, nurse_id)
    if not nurse:
        return redirect(url_for("logout"))

    allots   = Allotment.query.filter_by(nurse_id=nurse_id).all()
    patients = []
    for al in allots:
        p = db.session.get(Patient, al.patient_id)
        if p:
            patients.append({
                "id": p.id, "name": p.name, "full_name": p.full_name,
                "age": p.age, "ward": p.ward, "bed": p.bed,
                "condition": p.condition,
                "bystander_name":   p.bystander_name,
                "bystander_number": p.bystander_number,
            })

    return render_template(
        "dashboard.html",
        nurse_name=nurse.name,
        nurse_id=nurse_id,
        patients=patients,
        thresholds=THRESHOLDS,
        normal_ranges=NORMAL_RANGES,
        using_mysql=USING_MYSQL,
    )


@app.route("/mobile")
def mobile():
    return render_template("mobile.html", vapid_public_key=VAPID_PUBLIC_KEY)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Push API ──────────────────────────────────────────────────────────────────

@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    data     = request.get_json(force=True)
    nurse_id = data.get("nurse_id", "unknown")
    sub      = data.get("subscription")
    if not sub or not sub.get("endpoint"):
        return jsonify({"error": "Invalid subscription"}), 400
    with push_lock:
        if nurse_id not in push_subscriptions:
            push_subscriptions[nurse_id] = []
        endpoints = [s["endpoint"] for s in push_subscriptions[nurse_id]]
        if sub["endpoint"] not in endpoints:
            push_subscriptions[nurse_id].append(sub)
    return jsonify({"status": "subscribed", "nurse_id": nurse_id})


@app.route("/api/push/unsubscribe", methods=["POST"])
def push_unsubscribe():
    data     = request.get_json(force=True)
    nurse_id = data.get("nurse_id", "unknown")
    endpoint = data.get("endpoint")
    with push_lock:
        if nurse_id in push_subscriptions:
            push_subscriptions[nurse_id] = [
                s for s in push_subscriptions[nurse_id]
                if s.get("endpoint") != endpoint
            ]
    return jsonify({"status": "unsubscribed"})


@app.route("/api/push/vapid-public-key")
def vapid_public_key():
    return jsonify({"public_key": VAPID_PUBLIC_KEY})


@app.route("/api/push/send-test", methods=["POST"])
def send_test_push():
    data     = request.get_json(force=True)
    nurse_id = data.get("nurse_id", "nurse1")
    test_alert = {
        "patient_id":   "TEST",
        "patient_name": "Test Patient",
        "vital":        "Heart Rate",
        "vital_key":    "heart_rate_test",
        "value":        "135 bpm",
        "direction":    "HIGH",
        "level":        "critical",
        "nurses":       [nurse_id],
    }
    with push_lock:
        last_push_time[f"{nurse_id}:TEST:heart_rate_test"] = 0
    threading.Thread(target=send_push_to_nurse, args=(nurse_id, test_alert), daemon=True).start()
    with push_lock:
        count = len(push_subscriptions.get(nurse_id, []))
    return jsonify({"status": "sent", "devices": count, "nurse_id": nurse_id})


@app.route("/api/push/status")
def push_status():
    with push_lock:
        status = {nid: len(subs) for nid, subs in push_subscriptions.items()}
    return jsonify({"subscriptions": status, "push_available": PUSH_AVAILABLE})


@app.route("/sw.js")
def service_worker():
    resp = make_response(send_from_directory("static", "sw.js"))
    resp.headers["Content-Type"]           = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"]          = "no-cache"
    return resp


# ─── Patient & Nurse Management API ───────────────────────────────────────────

@app.route("/api/patients")
def api_patients():
    if "nurse" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    nurse_id = session["nurse"]
    allots   = Allotment.query.filter_by(nurse_id=nurse_id).all()
    p_dict   = {}
    for al in allots:
        p = db.session.get(Patient, al.patient_id)
        if p:
            p_dict[p.id] = {
                "id": p.id, "name": p.name, "full_name": p.full_name,
                "age": p.age, "ward": p.ward, "bed": p.bed,
                "condition": p.condition,
                "bystander_name":   p.bystander_name,
                "bystander_number": p.bystander_number,
            }
    return jsonify(p_dict)


@app.route("/api/nurses")
def api_nurses():
    nurses = Nurse.query.all()
    return jsonify([{"id": n.id, "name": n.name} for n in nurses])


@app.route("/api/all_patients")
def api_all_patients():
    patients = Patient.query.all()
    return jsonify([{
        "id": p.id, "name": p.name, "full_name": p.full_name,
        "age": p.age, "ward": p.ward, "bed": p.bed,
        "condition": p.condition,
        "bystander_name":   p.bystander_name,
        "bystander_number": p.bystander_number,
    } for p in patients])


@app.route("/api/nurse", methods=["POST"])
def add_nurse():
    data     = request.get_json(force=True)
    nid      = data.get("id", "").strip()
    name     = data.get("name", "").strip()
    password = data.get("password", "").strip()
    if not (nid and name and password):
        return jsonify({"error": "Missing fields"}), 400
    if db.session.get(Nurse, nid):
        return jsonify({"error": "Nurse ID already exists"}), 400
    db.session.add(Nurse(id=nid, name=name, password=password))
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/patient", methods=["POST"])
def add_patient():
    data             = request.get_json(force=True)
    pid              = data.get("id", "").strip()
    name             = data.get("name", "").strip()
    full_name        = data.get("full_name", "").strip()
    age              = data.get("age")
    ward             = data.get("ward", "").strip()
    bed              = data.get("bed", "").strip()
    condition        = data.get("condition", "").strip()
    bystander_name   = data.get("bystander_name", "").strip()
    bystander_number = data.get("bystander_number", "").strip()

    if not (pid and name and full_name and age and ward and bed and condition):
        return jsonify({"error": "Missing required fields"}), 400
    if db.session.get(Patient, pid):
        return jsonify({"error": "Patient ID already exists"}), 400

    patient = Patient(
        id=pid, name=name, full_name=full_name, age=int(age),
        ward=ward, bed=bed, condition=condition,
        bystander_name=bystander_name, bystander_number=bystander_number
    )
    db.session.add(patient)
    db.session.commit()
    ensure_vital_state(pid)
    return jsonify({"success": True})


@app.route("/api/patient/<pid>/discharge", methods=["POST"])
def discharge_patient(pid):
    # Delete vital history from DB as well
    VitalRecord.query.filter_by(patient_id=pid).delete()
    Allotment.query.filter_by(patient_id=pid).delete()
    patient = db.session.get(Patient, pid)
    if patient:
        db.session.delete(patient)
    db.session.commit()
    vital_state.pop(pid, None)
    vital_history.pop(pid, None)
    return jsonify({"success": True})


@app.route("/api/allot", methods=["POST"])
def allot_patient():
    """Exclusively allot a patient to a nurse — removes from all other nurses first."""
    data = request.get_json(force=True)
    nid  = data.get("nurse_id", "").strip()
    pid  = data.get("patient_id", "").strip()
    if not db.session.get(Nurse, nid):
        return jsonify({"error": "Nurse not found"}), 404
    if not db.session.get(Patient, pid):
        return jsonify({"error": "Patient not found"}), 404

    # ── EXCLUSIVE: Remove patient from ALL other nurses first ──────────────────
    Allotment.query.filter_by(patient_id=pid).delete()
    db.session.commit()

    # ── Then allot to the chosen nurse ────────────────────────────────────────
    db.session.add(Allotment(nurse_id=nid, patient_id=pid))
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/deallot", methods=["POST"])
def deallot_patient():
    data = request.get_json(force=True)
    nid  = data.get("nurse_id", "").strip()
    pid  = data.get("patient_id", "").strip()
    Allotment.query.filter_by(nurse_id=nid, patient_id=pid).delete()
    db.session.commit()
    return jsonify({"success": True})


# ─── Vital History API ─────────────────────────────────────────────────────────

@app.route("/api/vitals/<pid>")
def get_vital_history(pid):
    """Returns last N vital records from the database for a patient."""
    if "nurse" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    limit = min(int(request.args.get("limit", 100)), 500)
    records = (VitalRecord.query
               .filter_by(patient_id=pid)
               .order_by(VitalRecord.recorded_at.desc())
               .limit(limit)
               .all())
    return jsonify([{
        "recorded_at":    r.recorded_at.isoformat(),
        "heart_rate":     r.heart_rate,
        "spo2":           r.spo2,
        "temperature":    r.temperature,
        "blood_pressure": r.blood_pressure,
        "is_simulated":   r.is_simulated,
    } for r in reversed(records)])


# ─── Report ────────────────────────────────────────────────────────────────────

@app.route("/report/<pid>")
def generate_report(pid):
    if "nurse" not in session:
        return redirect(url_for("login"))
    patient = db.session.get(Patient, pid)
    if not patient:
        return "Patient not found", 404

    ensure_vital_state(pid)
    state    = vital_state.get(pid, {})
    baselines = {}
    for k in ["heart_rate", "spo2", "temperature", "blood_pressure"]:
        hist = vital_history.get(pid, {}).get(k, [])
        baselines[k] = round(sum(hist) / len(hist), 1) if len(hist) > 0 else "N/A"

    allots = Allotment.query.filter_by(patient_id=pid).all()
    assigned_nurses = []
    for al in allots:
        n = db.session.get(Nurse, al.nurse_id)
        if n:
            assigned_nurses.append(n.name)

    # Latest 20 DB records for the report trend
    db_records = (VitalRecord.query
                  .filter_by(patient_id=pid)
                  .order_by(VitalRecord.recorded_at.desc())
                  .limit(20).all())
    db_records = list(reversed(db_records))

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return render_template(
        "report.html",
        patient=patient,
        state=state,
        baselines=baselines,
        assigned_nurses=assigned_nurses,
        generated_at=generated_at,
        thresholds=THRESHOLDS,
        normal_ranges=NORMAL_RANGES,
        db_records=db_records,
        using_mysql=USING_MYSQL,
    )


import requests

# ─── Settings ──────────────────────────────────────────────────────────────────
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
USING_MYSQL = False


@app.route("/api/register_fcm_push", methods=["POST"])
def register_fcm_push():
    """Endpoint for native Flutter App (FCM) push token registration"""
    data     = request.get_json(force=True)
    nurse_id = data.get("nurse_id")
    token    = data.get("token")
    if not (nurse_id and token):
        return jsonify({"error": "Missing nurse_id or token"}), 400

    existing = FCMToken.query.filter_by(token=token).first()
    if existing:
        if existing.nurse_id != nurse_id:
            existing.nurse_id = nurse_id
            db.session.commit()
    else:
        new_token = FCMToken(nurse_id=nurse_id, token=token)
        db.session.add(new_token)
        db.session.commit()
    
    print(f"[FCM] Registered token via DB for nurse: {nurse_id}")
    return jsonify({"success": True})

# ─── AI Analysis ───────────────────────────────────────────────────────────────
import google.generativeai as genai

AI_CONFIGURED = False
if "GEMINI_API_KEY" in os.environ:
    genai.configure(api_key=os.environ["GEMINI_API_KEY"])
    AI_CONFIGURED = True


@app.route("/api/analyze/<pid>")
def analyze_patient_ai(pid):
    patient_obj = db.session.get(Patient, pid)
    if not patient_obj:
        return jsonify({"error": "Patient not found"}), 404

    ensure_vital_state(pid)
    vitals  = vital_state[pid]
    baselines = {}
    for k in ["heart_rate", "spo2", "temperature", "blood_pressure"]:
        hist = vital_history.get(pid, {}).get(k, [])
        baselines[k] = round(sum(hist) / len(hist), 1) if len(hist) > 0 else "N/A"

    prompt = f"""
    You are an AI Medical Assistant integrated into the 'MedWatch' Smart Alert System.
    Analyze the current vitals of the following patient based on their smart baseline data.
    Provide a brief, professional, and clinical summary (2-3 short paragraphs).

    Patient: {patient_obj.full_name} (Age: {patient_obj.age}, Condition: {patient_obj.condition}, Ward: {patient_obj.ward})

    Current Vitals vs Learned Baselines:
    - Heart Rate: {round(vitals['heart_rate'],1)} bpm (Baseline avg: {baselines['heart_rate']})
    - SpO2: {round(vitals['spo2'],1)} % (Baseline avg: {baselines['spo2']})
    - Temperature: {round(vitals['temperature'],2)} °C (Baseline avg: {baselines['temperature']})
    - Blood Pressure: {round(vitals['blood_pressure'],1)} mmHg (Baseline avg: {baselines['blood_pressure']})

    Instructions:
    1. Identify deviations from the learned baseline.
    2. Assess the risk level (Stable, Warning, Critical).
    3. Suggest one immediate nursing action if abnormal, otherwise routine observation.
    Keep formatting clean using bold text (no markdown headings).
    """

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return jsonify({
            "status": "success",
            "analysis": f"⚠️ **AI Not Configured**: No `GEMINI_API_KEY` found.\n\n**Simulated Analysis for {patient_obj.full_name}:**\nBase HR: {baselines['heart_rate']}, Current: {round(vitals['heart_rate'],1)}.\n\n**Assessment**: Patient remains stable.\n\n**Recommendation**: Continue routine monitoring."
        })

    try:
        genai.configure(api_key=api_key)
        model    = genai.GenerativeModel('gemini-2.5-flash')
        response = model.generate_content(prompt)
        return jsonify({"status": "success", "analysis": response.text})
    except Exception as e:
        return jsonify({"status": "error", "analysis": f"Failed: {str(e)}"}), 500


# ─── Sensor Data (ESP32) ───────────────────────────────────────────────────────
@app.route("/api/sensor_data", methods=["POST"])
def receive_sensor_data():
    data = request.get_json(force=True)
    if not data:
        return jsonify({"error": "No data received"}), 400

    pid = data.get("patient_id", "P001")
    ensure_vital_state(pid)
    state = vital_state[pid]
    state["last_real_update"] = time.time()

    if "temperature" in data:
        state["temperature"] = float(data["temperature"])
    if "heart_rate" in data:
        hr = float(data["heart_rate"])
        if hr > 0:
            state["heart_rate"] = hr
    if "humidity" in data:
        hum = float(data["humidity"])
        state["spo2"] = min(100.0, max(60.0, 90 + hum / 10.0))
    if "blood_pressure" in data:
        state["blood_pressure"] = float(data["blood_pressure"])

    return jsonify({"status": "success", "patient_id": pid})


# ─── Socket.IO ─────────────────────────────────────────────────────────────────
@socketio.on("connect")
def handle_connect():
    print(f"[WS] Client connected: {request.sid}")

@socketio.on("disconnect")
def handle_disconnect():
    print(f"[WS] Client disconnected: {request.sid}")

@socketio.on("subscribe_nurse")
def handle_subscribe(data):
    nurse_id = data.get("nurse_id")
    join_room(nurse_id)
    print(f"[WS] {request.sid} subscribed as nurse: {nurse_id}")


# ─── Startup ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()

    # Start vital DB writer thread
    writer_thread = threading.Thread(target=_vital_db_writer, daemon=True)
    writer_thread.start()

    # Start simulation
    sim_thread = threading.Thread(target=vital_simulation_loop, daemon=True)
    sim_thread.start()

    db_mode = "MySQL" if USING_MYSQL else "SQLite"
    print(f"🏥  Patient Monitoring System starting on http://127.0.0.1:5000")
    print(f"🗄️  Database: {db_mode}")
    print(f"🔔  Push notifications: {'ENABLED' if PUSH_AVAILABLE and VAPID_PUBLIC_KEY else 'DISABLED'}")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
