"""
backend.py

Proceso Flask único del sistema de monitoreo de góndolas (ADR-01): un solo
proceso posee la cámara (hilo camera_loop en background) y expone las
rutas HTTP de login, streaming en vivo, gestión de zonas ROI e historial
de eventos. Toda la lógica de negocio reutiliza los módulos ya probados
de forma aislada: app.db, app.roi_state_machine y app.roi_counter.

Este archivo depende de hardware (cámara CSI) y librerías con soporte
ARM (picamera2, ultralytics) que solo están disponibles en el Raspberry
Pi 4 real — por eso esos imports están protegidos con try/except: el
archivo puede revisarse/importarse en cualquier entorno, pero camera_loop()
solo se ejecuta de verdad en el RPi4.
"""

import os
import sys
import threading
import time
from datetime import datetime
from io import BytesIO

from flask import (
    Flask, jsonify, request, Response, send_file, redirect, url_for,
    render_template,
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user,
)
from werkzeug.security import check_password_hash

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app import db
from app import smtp_service
from app.roi_state_machine import RoiState, evaluate_reading, moving_average
from app.roi_counter import BoundingBox, assign_detections_to_rois

# Dependencias de hardware/inferencia: solo existen en el RPi4 real.
try:
    import cv2
    from picamera2 import Picamera2
    from ultralytics import YOLO
    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False


# ----------------------------------------------------------------------------
# Configuración
# ----------------------------------------------------------------------------

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_DIR = os.path.join(PROJECT_ROOT, "data", "event_images")
MODEL_PATH = os.path.join(PROJECT_ROOT, "models", "best_ncnn_model")
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
CAPTURE_INTERVAL_SECONDS = 1.0

# template_folder/static_folder se pasan explícitamente (no se dejan al
# comportamiento por defecto de Flask): la detección automática de
# root_path depende de cómo Python importó este módulo, y falla si
# backend.py se carga dinámicamente (ej. en pruebas vía importlib) en
# vez de ejecutarse directamente como script.
app = Flask(
    __name__,
    template_folder=os.path.join(BACKEND_DIR, "templates"),
    static_folder=os.path.join(BACKEND_DIR, "static"),
)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24))

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

# Estado compartido entre camera_loop() (hilo) y la ruta /video_feed.
_latest_frame_lock = threading.Lock()
_latest_frame_jpeg = None  # bytes JPEG ya codificados, listos para streaming


# ----------------------------------------------------------------------------
# Flask-Login
# ----------------------------------------------------------------------------

class User(UserMixin):
    def __init__(self, row: dict):
        self.id = row["id"]
        self.username = row["username"]
        self.password_hash = row["password_hash"]


@login_manager.user_loader
def load_user(user_id):
    conn = db.get_connection()
    try:
        row = db.get_user_by_id(conn, int(user_id))
        return User(row) if row else None
    finally:
        conn.close()


@app.route("/login", methods=["GET", "POST"])
def login():
    """RF-01: página de login (GET) y verificación de credenciales (POST/JSON)."""
    if request.method == "GET":
        if current_user.is_authenticated:
            return redirect(url_for("live_view"))
        return render_template("login.html")

    data = request.get_json(force=True, silent=True) or {}
    username = data.get("username", "")
    password = data.get("password", "")

    conn = db.get_connection()
    try:
        user_row = db.get_user_by_username(conn, username)
    finally:
        conn.close()

    # Mensaje de error genérico (RF-01): no revela si falló el usuario o la contraseña.
    if user_row is None or not check_password_hash(user_row["password_hash"], password):
        return jsonify({"error": "Credenciales inválidas"}), 401

    login_user(User(user_row))
    return jsonify({"status": "ok", "username": user_row["username"]})


@app.route("/")
@login_required
def live_view():
    """RF-04 a RF-07: página de Vista en Vivo (contenedor del stream + estado por zona)."""
    return render_template("live.html")


@app.route("/roi-config")
@login_required
def roi_config_page():
    """RF-08 a RF-11: página de configuración de zonas ROI."""
    return render_template("roi_config.html")


@app.route("/history")
@login_required
def history_page():
    """RF-12 a RF-16: página de historial de eventos."""
    return render_template("history.html")


@app.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    return jsonify({"status": "ok"})


# ----------------------------------------------------------------------------
# RF-04 a RF-07: Vista en Vivo (streaming MJPEG)
# ----------------------------------------------------------------------------

@app.route("/video_feed")
@login_required
def video_feed():
    def generate():
        while True:
            with _latest_frame_lock:
                frame = _latest_frame_jpeg
            if frame is not None:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                )
            time.sleep(0.1)

    return Response(generate(), mimetype="multipart/x-mixed-replace; boundary=frame")


# ----------------------------------------------------------------------------
# RF-08 a RF-11: Configuración de Zonas ROI
# ----------------------------------------------------------------------------

@app.route("/api/roi_zones", methods=["GET"])
@login_required
def list_roi_zones():
    conn = db.get_connection()
    try:
        zones = db.get_active_roi_zones(conn)
        for zone in zones:
            recent = db.get_recent_total_objects(conn, zone["id"], window=1)
            zone["latest_count"] = recent[0] if recent else None
    finally:
        conn.close()
    return jsonify(zones)


@app.route("/api/roi_zones", methods=["POST"])
@login_required
def create_roi_zone_route():
    """RF-08, RF-09, RF-10: crea una zona con umbrales propios."""
    data = request.get_json(force=True, silent=True) or {}
    required = ["name", "x1", "y1", "x2", "y2"]
    missing = [f for f in required if f not in data]
    if missing:
        return jsonify({"error": f"Campos faltantes: {missing}"}), 400

    conn = db.get_connection()
    try:
        new_id = db.create_roi_zone(
            conn,
            name=data["name"],
            x1=float(data["x1"]), y1=float(data["y1"]),
            x2=float(data["x2"]), y2=float(data["y2"]),
            low_stock_threshold=int(data.get("low_stock_threshold", 2)),
            restocked_threshold=int(data.get("restocked_threshold", 4)),
            confirmation_readings=int(data.get("confirmation_readings", 3)),
        )
    except Exception as exc:
        # Cubre los CHECK constraints del esquema (ej. umbral inválido).
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()

    return jsonify({"id": new_id}), 201


@app.route("/api/roi_zones/<int:roi_id>", methods=["PUT"])
@login_required
def update_roi_zone_route(roi_id):
    """RF-10: editar nombre/umbrales de una zona existente."""
    data = request.get_json(force=True, silent=True) or {}
    conn = db.get_connection()
    try:
        db.update_roi_zone(
            conn, roi_id,
            name=data.get("name"),
            low_stock_threshold=data.get("low_stock_threshold"),
            restocked_threshold=data.get("restocked_threshold"),
            confirmation_readings=data.get("confirmation_readings"),
        )
        zone = db.get_roi_zone(conn, roi_id)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400
    finally:
        conn.close()

    if zone is None:
        return jsonify({"error": "Zona no encontrada"}), 404
    return jsonify(zone)


@app.route("/api/roi_zones/<int:roi_id>", methods=["DELETE"])
@login_required
def delete_roi_zone_route(roi_id):
    """RF-11: eliminar una zona."""
    conn = db.get_connection()
    try:
        db.delete_roi_zone(conn, roi_id)
    finally:
        conn.close()
    return jsonify({"status": "ok"})


# ----------------------------------------------------------------------------
# RF-12 a RF-16: Historial de Eventos
# ----------------------------------------------------------------------------

@app.route("/api/events", methods=["GET"])
@login_required
def list_events():
    """RF-12 a RF-15: historial filtrable por zona, tipo y rango de fechas."""
    roi_id = request.args.get("roi_id", type=int)
    event_type = request.args.get("event_type")
    date_from = request.args.get("date_from")
    date_to = request.args.get("date_to")
    limit = request.args.get("limit", default=100, type=int)

    conn = db.get_connection()
    try:
        events = db.get_stock_events(
            conn, roi_id=roi_id, event_type=event_type,
            date_from=date_from, date_to=date_to, limit=limit,
        )
    finally:
        conn.close()
    return jsonify(events)


@app.route("/api/events/<int:event_id>/image", methods=["GET"])
@login_required
def get_event_image(event_id):
    """RF-16: imagen de respaldo en tamaño completo."""
    conn = db.get_connection()
    try:
        events = db.get_stock_events(conn, limit=1000)
        event = next((e for e in events if e["id"] == event_id), None)
    finally:
        conn.close()

    if event is None or not event["image_path"]:
        return jsonify({"error": "Imagen no encontrada"}), 404

    full_path = os.path.join(PROJECT_ROOT, event["image_path"])
    if not os.path.exists(full_path):
        return jsonify({"error": "Archivo de imagen no existe en disco"}), 404

    return send_file(full_path, mimetype="image/jpeg")


# ----------------------------------------------------------------------------
# camera_loop(): hilo en background — captura, inferencia, histéresis, eventos
# ----------------------------------------------------------------------------

def _save_event_image(frame) -> str:
    """Guarda el frame anotado y devuelve la ruta RELATIVA a PROJECT_ROOT."""
    os.makedirs(IMAGE_DIR, exist_ok=True)
    filename = f"event_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    full_path = os.path.join(IMAGE_DIR, filename)
    cv2.imwrite(full_path, frame)
    return os.path.relpath(full_path, PROJECT_ROOT)


def _process_roi(conn, roi_row: dict, detections_count: int, annotated_frame) -> None:
    """
    Aplica la lógica de un ciclo completo para UNA zona: guarda la lectura,
    calcula el promedio móvil, evalúa la máquina de histéresis y, si se
    confirma una transición, guarda la imagen y el evento.
    """
    window = db.get_general_parameter(conn, "reading_average_window", default=5, cast=int)

    db.insert_reading(conn, roi_row["id"], detections_count)
    db.cleanup_old_readings(conn, roi_row["id"], keep_n=window)

    recent = db.get_recent_total_objects(conn, roi_row["id"], window=window)
    avg_count = moving_average(recent)

    roi_state = RoiState(
        low_stock_threshold=roi_row["low_stock_threshold"],
        restocked_threshold=roi_row["restocked_threshold"],
        confirmation_readings=roi_row["confirmation_readings"],
        current_state=roi_row["current_state"],
        candidate_state=roi_row["candidate_state"],
        candidate_consecutive_readings=roi_row["candidate_consecutive_readings"],
    )

    transition = evaluate_reading(avg_count, roi_state)

    # Persistir el nuevo estado de la máquina, haya habido transición o no
    # (el progreso del candidato también debe sobrevivir a un reinicio).
    db.update_roi_state(
        conn, roi_row["id"],
        current_state=roi_state.current_state,
        candidate_state=roi_state.candidate_state,
        candidate_consecutive_readings=roi_state.candidate_consecutive_readings,
    )

    if transition is not None:
        image_path = _save_event_image(annotated_frame)
        event_id = db.insert_stock_event(
            conn, roi_row["id"], roi_row["name"],
            previous_state=transition.previous_state,
            new_state=transition.new_state,
            avg_total_objects=avg_count,
            image_path=image_path,
        )
        print(f"[EVENTO] Zona '{roi_row['name']}': "
              f"{transition.previous_state} -> {transition.new_state}")
        _dispatch_alert_email(conn, event_id, roi_row, transition, avg_count, image_path)


def _dispatch_alert_email(conn, event_id: int, roi_row: dict, transition,
                           avg_count: float, image_path: str) -> None:
    """
    Envía la alerta de correo en background (RF-19 a RF-21), si hay una
    configuración SMTP activa y al menos un destinatario. Si falta
    cualquiera de las dos cosas, se omite el envío sin bloquear ni
    fallar el ciclo de inferencia — solo se deja constancia en consola.
    """
    smtp_config = db.get_active_smtp_config(conn)
    recipients = db.get_active_alert_recipient_emails(conn)

    if smtp_config is None:
        print(f"[SMTP] Sin configuración SMTP activa; alerta del evento {event_id} no enviada.")
        return
    if not recipients:
        print(f"[SMTP] Sin destinatarios activos; alerta del evento {event_id} no enviada.")
        return

    event = {
        "roi_name_snapshot": roi_row["name"],
        "previous_state": transition.previous_state,
        "new_state": transition.new_state,
        "avg_total_objects": avg_count,
        "timestamp": datetime.now().isoformat(sep=" ", timespec="seconds"),
    }
    subject, text_body, html_body = smtp_service.build_alert_message(event)
    full_image_path = os.path.join(PROJECT_ROOT, image_path) if image_path else None

    smtp_service.send_alert_email_in_background(
        event_id=event_id,
        smtp_config=smtp_config,
        recipients=recipients,
        subject=subject,
        body=text_body,
        html_body=html_body,
        attachment_path=full_image_path,
        on_error=lambda exc: print(f"[SMTP ERROR] Evento {event_id}: {exc}"),
    )


def camera_loop():
    """Hilo único que posee la cámara y ejecuta el ciclo de inferencia."""
    global _latest_frame_jpeg

    if not HARDWARE_AVAILABLE:
        print("ADVERTENCIA: picamera2/ultralytics no disponibles. "
              "camera_loop() no puede ejecutarse en este entorno.")
        return

    model = YOLO(MODEL_PATH)
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(
        main={"size": (FRAME_WIDTH, FRAME_HEIGHT), "format": "RGB888"}
    )
    picam2.configure(config)
    picam2.start()
    time.sleep(2)  # deja que ajuste exposición

    try:
        while True:
            frame = picam2.capture_array()
            results = model(frame)
            annotated = results[0].plot()

            boxes = [
                BoundingBox(x1=float(b[0]), y1=float(b[1]), x2=float(b[2]), y2=float(b[3]))
                for b in results[0].boxes.xyxy.tolist()
            ]

            conn = db.get_connection()
            try:
                roi_zones = db.get_active_roi_zones(conn)
                counts_by_roi = assign_detections_to_rois(
                    boxes, roi_zones, frame_width=FRAME_WIDTH, frame_height=FRAME_HEIGHT
                )
                for roi_row in roi_zones:
                    _process_roi(conn, roi_row, counts_by_roi.get(roi_row["id"], 0), annotated)
            finally:
                conn.close()

            ok, jpeg_bytes = cv2.imencode(".jpg", annotated)
            if ok:
                with _latest_frame_lock:
                    _latest_frame_jpeg = jpeg_bytes.tobytes()

            time.sleep(CAPTURE_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        picam2.stop()


# ----------------------------------------------------------------------------
# Arranque
# ----------------------------------------------------------------------------

def _ensure_database_ready():
    conn = db.get_connection()
    db.init_schema(conn)
    conn.close()


if __name__ == "__main__":
    _ensure_database_ready()
    camera_thread = threading.Thread(target=camera_loop, daemon=True)
    camera_thread.start()
    app.run(host="0.0.0.0", port=5000, debug=False)
