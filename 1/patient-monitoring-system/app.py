from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory, make_response
from flask_socketio import SocketIO, emit, join_room, leave_room
import threading
import time
import random
import math
import json
import os

# ── pywebpush ──────────────────────────────────────────────────────────────────
try:
    from pywebpush import webpush, WebPushException
    PUSH_AVAILABLE = True
except ImportError:
    PUSH_AVAILABLE = False
    print("[WARN] pywebpush not available – push notifications disabled")

app = Flask(__name__)
app.secret_key = 'hospital_secret_key_2024'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ─── VAPID keys (from vapid_keys.json) ───────────────────────────────────────

VAPID_EMAIL   = "mailto:medwatch@hospital.com"
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

# ─── Push subscription store (in-memory, keyed by nurse_id) ──────────────────
# Format: { nurse_id: [ { endpoint, keys:{p256dh, auth} }, ... ] }
push_subscriptions: dict[str, list[dict]] = {}
push_lock = threading.Lock()

# Rate-limit push: track last push time per (nurse_id, patient_id, vital)
last_push_time: dict[str, float] = {}
PUSH_COOLDOWN = 30  # seconds between identical alerts

# ─── Nurse & Patient Data ─────────────────────────────────────────────────────

NURSES = {
    "nurse1": {"password": "1234", "name": "Sarah Johnson",  "patients": ["P001", "P002"]},
    "nurse2": {"password": "1234", "name": "Emily Chen",     "patients": ["P003"]},
}

PATIENTS = {
    "P001": {"name": "Patient A", "full_name": "James Thornton", "age": 67, "ward": "Cardiology",  "bed": "4B", "condition": "Post-Op Recovery"},
    "P002": {"name": "Patient B", "full_name": "Maria Santos",   "age": 54, "ward": "General",     "bed": "7A", "condition": "Hypertension Watch"},
    "P003": {"name": "Patient C", "full_name": "Robert Kim",     "age": 72, "ward": "ICU",         "bed": "2C", "condition": "Respiratory Issue"},
}

# ─── Thresholds ───────────────────────────────────────────────────────────────

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

# ─── Vital State ──────────────────────────────────────────────────────────────

vital_state = {
    pid: {
        "heart_rate":     random.randint(65, 90),
        "spo2":           random.uniform(96, 99),
        "temperature":    random.uniform(36.2, 37.2),
        "blood_pressure": random.randint(115, 128),
        "phase":          random.uniform(0, 6.28),
        "crisis_mode":    False,
        "crisis_timer":   0,
        "crisis_vital":   None,
    }
    for pid in PATIENTS
}

# ─── Vital Generation ─────────────────────────────────────────────────────────

def generate_vitals(pid, state):
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

    # Heart Rate
    if state["crisis_mode"] and state["crisis_vital"] == "heart_rate":
        target_hr = random.choice([random.randint(30, 48), random.randint(122, 145)])
    else:
        target_hr = 78 + 10 * math.sin(t * 0.7) + random.gauss(0, 3)
    state["heart_rate"] = max(30, min(160, state["heart_rate"] * 0.85 + target_hr * 0.15))

    # SpO2
    if state["crisis_mode"] and state["crisis_vital"] == "spo2":
        target_spo2 = random.uniform(87, 91)
    else:
        target_spo2 = 97.5 + random.gauss(0, 0.5)
    state["spo2"] = max(80, min(100, state["spo2"] * 0.9 + target_spo2 * 0.1))

    # Temperature
    if state["crisis_mode"] and state["crisis_vital"] == "temperature":
        target_temp = random.uniform(38.2, 39.5)
    else:
        target_temp = 36.8 + random.gauss(0, 0.1)
    state["temperature"] = max(34, min(42, state["temperature"] * 0.95 + target_temp * 0.05))

    # Blood Pressure
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


def check_alerts(pid, vitals):
    alerts = []
    patient = PATIENTS[pid]
    checks = [
        ("heart_rate",     vitals["heart_rate"],     "bpm",  "Heart Rate"),
        ("spo2",           vitals["spo2"],            "%",    "SpO₂"),
        ("temperature",    vitals["temperature"],     "°C",   "Temperature"),
        ("blood_pressure", vitals["blood_pressure"],  "mmHg", "Blood Pressure"),
    ]
    for key, value, unit, label in checks:
        t = THRESHOLDS[key]
        if value < t["low"] or value > t["high"]:
            level = "critical" if (value < t["low"] * 0.9 or value > t["high"] * 1.1) else "warning"
            direction = "LOW" if value < t["low"] else "HIGH"
            alerts.append({
                "patient_id":   pid,
                "patient_name": patient["full_name"],
                "vital":        label,
                "vital_key":    key,
                "value":        f"{value} {unit}",
                "direction":    direction,
                "level":        level,
                "nurses":       [n for n, d in NURSES.items() if pid in d["patients"]],
            })
    return alerts

# ─── Web Push Delivery ────────────────────────────────────────────────────────

def should_send_push(nurse_id: str, patient_id: str, vital_key: str) -> bool:
    """Rate-limit: only send one push per (nurse, patient, vital) every PUSH_COOLDOWN s."""
    key = f"{nurse_id}:{patient_id}:{vital_key}"
    now = time.time()
    with push_lock:
        last = last_push_time.get(key, 0)
        if now - last < PUSH_COOLDOWN:
            return False
        last_push_time[key] = now
        return True


def send_push_to_nurse(nurse_id: str, alert: dict):
    """Send a Web Push notification to every subscribed device for this nurse."""
    if not PUSH_AVAILABLE or not VAPID_PRIVATE_KEY:
        return

    with push_lock:
        subs = list(push_subscriptions.get(nurse_id, []))

    if not subs:
        return

    vital  = alert["vital"]
    value  = alert["value"]
    level  = alert["level"]
    pname  = alert["patient_name"]
    dirn   = alert["direction"]
    icon   = "🚨" if level == "critical" else "⚠️"

    payload = json.dumps({
        "title":   f"{icon} {'CRITICAL' if level == 'critical' else 'WARNING'} – {pname}",
        "body":    f"{vital}: {value} ({dirn})",
        "level":   level,
        "patient": pname,
        "vital":   vital,
        "value":   value,
        "tag":     f"{alert['patient_id']}-{alert['vital_key']}",  # collapses duplicate notifications
        "url":     "/mobile",
    })

    dead_subs = []
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_EMAIL},
            )
            print(f"[PUSH] ✅ Sent to {nurse_id}: {vital} {value}")
        except WebPushException as ex:
            code = ex.response.status_code if ex.response else None
            print(f"[PUSH] ❌ Failed ({code}): {ex}")
            if code in (404, 410):  # subscription expired / gone
                dead_subs.append(sub)
        except Exception as ex:
            print(f"[PUSH] ❌ Error: {ex}")

    # Clean up expired subscriptions
    if dead_subs:
        with push_lock:
            current = push_subscriptions.get(nurse_id, [])
            push_subscriptions[nurse_id] = [s for s in current if s not in dead_subs]
            print(f"[PUSH] Removed {len(dead_subs)} expired sub(s) for {nurse_id}")


# ─── Vital Simulation Loop ────────────────────────────────────────────────────

def vital_simulation_loop():
    while True:
        for pid, state in vital_state.items():
            vitals = generate_vitals(pid, state)
            alerts = check_alerts(pid, vitals)

            payload = {"patient_id": pid, "vitals": vitals, "alerts": alerts}
            socketio.emit("vital_update", payload)

            for alert in alerts:
                socketio.emit("alert_event", alert)

                # Send native push — rate-limited per vital per nurse
                for nurse_id in alert["nurses"]:
                    if should_send_push(nurse_id, pid, alert["vital_key"]):
                        t = threading.Thread(
                            target=send_push_to_nurse,
                            args=(nurse_id, alert),
                            daemon=True,
                        )
                        t.start()

        time.sleep(2)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        if username in NURSES and NURSES[username]["password"] == password:
            session["nurse"]      = username
            session["nurse_name"] = NURSES[username]["name"]
            return redirect(url_for("dashboard"))
        else:
            error = "Invalid credentials. Please try again."
    return render_template("login.html", error=error)


@app.route("/dashboard")
def dashboard():
    if "nurse" not in session:
        return redirect(url_for("login"))
    nurse_id   = session["nurse"]
    nurse_info = NURSES[nurse_id]
    patients   = []
    for pid in nurse_info["patients"]:
        p = PATIENTS[pid].copy()
        p["id"] = pid
        patients.append(p)
    return render_template(
        "dashboard.html",
        nurse_name=nurse_info["name"],
        nurse_id=nurse_id,
        patients=patients,
        thresholds=THRESHOLDS,
        normal_ranges=NORMAL_RANGES,
    )


@app.route("/mobile")
def mobile():
    return render_template(
        "mobile.html",
        vapid_public_key=VAPID_PUBLIC_KEY,
    )


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ─── Push Subscription API ────────────────────────────────────────────────────

@app.route("/api/push/subscribe", methods=["POST"])
def push_subscribe():
    """Called by the mobile page when it receives a PushSubscription object."""
    data = request.get_json(force=True)
    nurse_id = data.get("nurse_id", "unknown")
    sub      = data.get("subscription")

    if not sub or not sub.get("endpoint"):
        return jsonify({"error": "Invalid subscription"}), 400

    with push_lock:
        if nurse_id not in push_subscriptions:
            push_subscriptions[nurse_id] = []
        # Avoid duplicate endpoints
        endpoints = [s["endpoint"] for s in push_subscriptions[nurse_id]]
        if sub["endpoint"] not in endpoints:
            push_subscriptions[nurse_id].append(sub)
            print(f"[PUSH] New subscription for {nurse_id} (total: {len(push_subscriptions[nurse_id])})")

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
    """Trigger an immediate test push to a given nurse."""
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

    # Bypass rate limiter for test
    with push_lock:
        last_push_time[f"{nurse_id}:TEST:heart_rate_test"] = 0

    t = threading.Thread(target=send_push_to_nurse, args=(nurse_id, test_alert), daemon=True)
    t.start()

    with push_lock:
        count = len(push_subscriptions.get(nurse_id, []))
    return jsonify({"status": "sent", "devices": count, "nurse_id": nurse_id})


@app.route("/sw.js")
def service_worker():
    """Serve the service worker from root scope so it can control the entire origin."""
    resp = make_response(send_from_directory("static", "sw.js"))
    resp.headers["Content-Type"]           = "application/javascript"
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"]          = "no-cache"
    return resp


@app.route("/api/patients")
def api_patients():
    if "nurse" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    nurse_id = session["nurse"]
    pids = NURSES[nurse_id]["patients"]
    return jsonify({pid: PATIENTS[pid] for pid in pids})


@app.route("/api/push/status")
def push_status():
    with push_lock:
        status = {nid: len(subs) for nid, subs in push_subscriptions.items()}
    return jsonify({"subscriptions": status, "push_available": PUSH_AVAILABLE})


# ─── Socket.IO Events ─────────────────────────────────────────────────────────

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


# ─── Startup ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sim_thread = threading.Thread(target=vital_simulation_loop, daemon=True)
    sim_thread.start()
    print("🏥  Patient Monitoring System starting on http://127.0.0.1:5000")
    print(f"🔔  Push notifications: {'ENABLED' if PUSH_AVAILABLE and VAPID_PUBLIC_KEY else 'DISABLED'}")
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
