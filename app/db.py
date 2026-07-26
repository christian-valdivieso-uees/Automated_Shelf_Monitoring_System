"""
db.py

Capa de acceso a datos del sistema de monitoreo de góndolas. Todas las
funciones reciben la conexión (`conn`) como primer parámetro en vez de
abrirla internamente — esto es lo que permite probarlas con una base de
datos SQLite en memoria (ver tests/test_db.py) sin tocar el archivo real
en disco ni depender de rutas del Raspberry Pi.

Estructura de carpetas asumida (raíz del proyecto):
    Automated_Shelf_Monitoring_System/
        app/db.py          <- este archivo
        data/schema.sql             <- esquema aplicado por get_connection()/init_schema()
        data/shelf_monitoring.db    <- base de datos real (se crea en runtime)
        src/backend.py      <- Flask app que importará estas funciones
"""

import os
import sqlite3
from datetime import datetime, timedelta
from typing import Optional


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB_PATH = os.path.join(PROJECT_ROOT, "data", "shelf_monitoring.db")
DEFAULT_SCHEMA_PATH = os.path.join(PROJECT_ROOT, "data", "schema.sql")


# ----------------------------------------------------------------------------
# Conexión e inicialización
# ----------------------------------------------------------------------------

def get_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """
    Abre una conexión con row_factory=sqlite3.Row (permite acceder a las
    columnas por nombre, ej. row["total_objects"]) y foreign_keys activado.

    Nota de diseño: `db_path` usa None como centinela y se resuelve contra
    DEFAULT_DB_PATH DENTRO del cuerpo de la función (no como valor por
    defecto del parámetro). Si DEFAULT_DB_PATH fuera el valor por defecto
    del parámetro, Python lo fijaría una sola vez al definirse la función,
    y cambiarlo después (ej. en pruebas, apuntando a un archivo temporal)
    no tendría ningún efecto.
    """
    if db_path is None:
        db_path = DEFAULT_DB_PATH

    if db_path != ":memory:":
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection, schema_path: Optional[str] = None) -> None:
    """Aplica schema.sql sobre una conexión ya abierta (idempotente)."""
    if schema_path is None:
        schema_path = DEFAULT_SCHEMA_PATH

    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


# ----------------------------------------------------------------------------
# smtp_config y alert_recipients
# ----------------------------------------------------------------------------

def get_active_smtp_config(conn: sqlite3.Connection) -> Optional[dict]:
    """Usado por el envío de alertas (RF-19). La contraseña viaja cifrada;
    descifrarla es responsabilidad de app.smtp_service, no de esta capa."""
    row = conn.execute(
        "SELECT * FROM smtp_config WHERE active = 1 ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def save_smtp_config(conn: sqlite3.Connection, server: str, port: int, username: str,
                      encrypted_password: str, use_tls: bool = True) -> int:
    """
    Guarda una nueva configuración SMTP activa (RF-19), desactivando
    cualquier configuración previa. Se conserva el historial (no se
    borra), por si se necesita auditar cambios de configuración.
    """
    conn.execute("UPDATE smtp_config SET active = 0")
    cursor = conn.execute(
        """
        INSERT INTO smtp_config (server, port, username, encrypted_password, use_tls, active, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, datetime('now', 'localtime'))
        """,
        (server, port, username, encrypted_password, use_tls),
    )
    conn.commit()
    return cursor.lastrowid


def get_active_alert_recipient_emails(conn: sqlite3.Connection) -> list:
    """RF-21: lista simple de correos que reciben todas las alertas."""
    rows = conn.execute(
        "SELECT email FROM alert_recipients WHERE active = 1"
    ).fetchall()
    return [row["email"] for row in rows]


def add_alert_recipient(conn: sqlite3.Connection, email: str) -> int:
    cursor = conn.execute(
        "INSERT OR IGNORE INTO alert_recipients (email, created_at) VALUES (?, datetime('now', 'localtime'))", (email,)
    )
    conn.commit()
    return cursor.lastrowid


def remove_alert_recipient(conn: sqlite3.Connection, email: str) -> None:
    conn.execute("DELETE FROM alert_recipients WHERE email = ?", (email,))
    conn.commit()


# ----------------------------------------------------------------------------
# users
# ----------------------------------------------------------------------------

def create_user(conn: sqlite3.Connection, username: str, password_hash: str) -> int:
    """Crea un nuevo usuario. `password_hash` ya debe venir hasheado
    (werkzeug.security.generate_password_hash) — esta capa nunca hashea."""
    cursor = conn.execute(
        "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, datetime('now', 'localtime'))",
        (username, password_hash),
    )
    conn.commit()
    return cursor.lastrowid


def get_all_users(conn: sqlite3.Connection) -> list:
    """Lista de usuarios SIN password_hash — nunca se expone al frontend."""
    rows = conn.execute(
        "SELECT id, username, created_at FROM users ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows]


def count_users(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()
    return row["c"]


def update_user_password(conn: sqlite3.Connection, user_id: int, password_hash: str) -> None:
    conn.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id)
    )
    conn.commit()


def delete_user(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()


def get_user_by_username(conn: sqlite3.Connection, username: str) -> Optional[dict]:
    """Usado por la ruta /login para verificar credenciales (RF-01)."""
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    return dict(row) if row else None


def get_user_by_id(conn: sqlite3.Connection, user_id: int) -> Optional[dict]:
    """Usado por Flask-Login (user_loader) para restaurar la sesión."""
    row = conn.execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return dict(row) if row else None


# ----------------------------------------------------------------------------
# roi_zones
# ----------------------------------------------------------------------------

def get_active_roi_zones(conn: sqlite3.Connection) -> list:
    """Devuelve todas las zonas activas (RF-08, RF-09), como lista de dicts."""
    rows = conn.execute(
        "SELECT * FROM roi_zones WHERE active = 1 ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows]


def get_roi_zone(conn: sqlite3.Connection, roi_id: int) -> Optional[dict]:
    row = conn.execute(
        "SELECT * FROM roi_zones WHERE id = ?", (roi_id,)
    ).fetchone()
    return dict(row) if row else None


def create_roi_zone(conn: sqlite3.Connection, name: str, x1: float, y1: float,
                     x2: float, y2: float, low_stock_threshold: int = 2,
                     restocked_threshold: int = 4, confirmation_readings: int = 3) -> int:
    """Crea una zona ROI (RF-08, RF-10) y devuelve su id."""
    cursor = conn.execute(
        """
        INSERT INTO roi_zones
            (name, x1, y1, x2, y2, low_stock_threshold, restocked_threshold, confirmation_readings,
             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'), datetime('now', 'localtime'))
        """,
        (name, x1, y1, x2, y2, low_stock_threshold, restocked_threshold, confirmation_readings),
    )
    conn.commit()
    return cursor.lastrowid


def update_roi_state(conn: sqlite3.Connection, roi_id: int, current_state: str,
                      candidate_state: Optional[str], candidate_consecutive_readings: int) -> None:
    """
    Persiste el estado vigente de la máquina de histéresis (ver
    app/roi_state_machine.py) para que sobreviva a un reinicio del proceso.
    """
    conn.execute(
        """
        UPDATE roi_zones
        SET current_state = ?,
            candidate_state = ?,
            candidate_consecutive_readings = ?,
            updated_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (current_state, candidate_state, candidate_consecutive_readings, roi_id),
    )
    conn.commit()


def update_roi_zone(conn: sqlite3.Connection, roi_id: int, name: str = None,
                     low_stock_threshold: int = None, restocked_threshold: int = None,
                     confirmation_readings: int = None) -> None:
    """
    Actualiza los campos editables de una zona (RF-10) sin tocar su estado
    de histéresis vigente. Solo se actualizan los campos que no son None.
    """
    fields = {
        "name": name,
        "low_stock_threshold": low_stock_threshold,
        "restocked_threshold": restocked_threshold,
        "confirmation_readings": confirmation_readings,
    }
    updates = {k: v for k, v in fields.items() if v is not None}
    if not updates:
        return

    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [roi_id]
    conn.execute(
        f"UPDATE roi_zones SET {set_clause}, updated_at = datetime('now', 'localtime') WHERE id = ?",
        params,
    )
    conn.commit()


def delete_roi_zone(conn: sqlite3.Connection, roi_id: int) -> None:
    """Elimina una zona (RF-11). Las lecturas asociadas se eliminan en cascada;
    los eventos históricos se conservan (roi_id queda NULL, ver ON DELETE SET NULL)."""
    conn.execute("DELETE FROM roi_zones WHERE id = ?", (roi_id,))
    conn.commit()


# ----------------------------------------------------------------------------
# roi_readings
# ----------------------------------------------------------------------------

def insert_reading(conn: sqlite3.Connection, roi_id: int, total_objects: int) -> int:
    cursor = conn.execute(
        "INSERT INTO roi_readings (roi_id, total_objects, timestamp) VALUES (?, ?, datetime('now', 'localtime'))",
        (roi_id, total_objects),
    )
    conn.commit()
    return cursor.lastrowid


def get_recent_total_objects(conn: sqlite3.Connection, roi_id: int, window: int) -> list:
    """
    Últimas `window` lecturas de una zona, más reciente primero.
    Se ordena por `id DESC` (no por `timestamp DESC`): CURRENT_TIMESTAMP en
    SQLite solo tiene resolución de segundo, y dos lecturas insertadas en el
    mismo segundo empatarían en timestamp — el id autoincremental sí refleja
    el orden real de inserción sin ambigüedad.
    """
    rows = conn.execute(
        """
        SELECT total_objects FROM roi_readings
        WHERE roi_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (roi_id, window),
    ).fetchall()
    return [row["total_objects"] for row in rows]


def cleanup_old_readings(conn: sqlite3.Connection, roi_id: int, keep_n: int) -> int:
    """
    Mantiene solo las `keep_n` lecturas más recientes de una zona,
    eliminando el resto. Devuelve la cantidad de filas eliminadas.
    """
    cursor = conn.execute(
        """
        DELETE FROM roi_readings
        WHERE roi_id = ? AND id NOT IN (
            SELECT id FROM roi_readings
            WHERE roi_id = ?
            ORDER BY id DESC
            LIMIT ?
        )
        """,
        (roi_id, roi_id, keep_n),
    )
    conn.commit()
    return cursor.rowcount


# ----------------------------------------------------------------------------
# stock_events
# ----------------------------------------------------------------------------

def insert_stock_event(conn: sqlite3.Connection, roi_id: Optional[int], roi_name_snapshot: str,
                        previous_state: str, new_state: str, avg_total_objects: Optional[float] = None,
                        image_path: Optional[str] = None) -> int:
    """Registra una transición CONFIRMADA de estado (RF-12)."""
    cursor = conn.execute(
        """
        INSERT INTO stock_events
            (roi_id, roi_name_snapshot, previous_state, new_state, avg_total_objects, image_path, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        """,
        (roi_id, roi_name_snapshot, previous_state, new_state, avg_total_objects, image_path),
    )
    conn.commit()
    return cursor.lastrowid


def mark_event_email_sent(conn: sqlite3.Connection, event_id: int) -> None:
    conn.execute("UPDATE stock_events SET email_sent = 1 WHERE id = ?", (event_id,))
    conn.commit()


def get_stock_events(conn: sqlite3.Connection, roi_id: Optional[int] = None,
                      event_type: Optional[str] = None, date_from: Optional[str] = None,
                      date_to: Optional[str] = None, limit: int = 100) -> list:
    """
    Consulta filtrable del historial de eventos (RF-13, RF-14, RF-15).
    - roi_id: filtra por zona específica.
    - event_type: 'in_stock' o 'out_of_stock', filtra por new_state.
    - date_from / date_to: strings 'YYYY-MM-DD HH:MM:SS' o 'YYYY-MM-DD'.
    """
    query = "SELECT * FROM stock_events WHERE 1=1"
    params = []

    if roi_id is not None:
        query += " AND roi_id = ?"
        params.append(roi_id)
    if event_type is not None:
        query += " AND new_state = ?"
        params.append(event_type)
    if date_from is not None:
        query += " AND timestamp >= ?"
        params.append(date_from)
    if date_to is not None:
        query += " AND timestamp <= ?"
        params.append(date_to)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [dict(row) for row in rows]


# ----------------------------------------------------------------------------
# general_parameters
# ----------------------------------------------------------------------------

def get_all_general_parameters(conn: sqlite3.Connection) -> list:
    """RF-18: lista completa de parámetros para la pantalla de Configuración General."""
    rows = conn.execute(
        "SELECT key, value, description, updated_at FROM general_parameters ORDER BY key"
    ).fetchall()
    return [dict(row) for row in rows]


def get_general_parameter(conn: sqlite3.Connection, key: str, default=None, cast=str):
    row = conn.execute(
        "SELECT value FROM general_parameters WHERE key = ?", (key,)
    ).fetchone()
    if row is None:
        return default
    return cast(row["value"])


def set_general_parameter(conn: sqlite3.Connection, key: str, value, description: Optional[str] = None) -> None:
    conn.execute(
        """
        INSERT INTO general_parameters (key, value, description, updated_at)
        VALUES (?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = datetime('now', 'localtime')
        """,
        (key, str(value), description),
    )
    conn.commit()


# ----------------------------------------------------------------------------
# Limpieza de imágenes por retención (RNF-04)
# ----------------------------------------------------------------------------

def get_events_older_than(conn: sqlite3.Connection, days: int) -> list:
    """Eventos con imagen cuya antigüedad supera `days`, para limpieza (RNF-04)."""
    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        """
        SELECT id, image_path FROM stock_events
        WHERE timestamp < ? AND image_path IS NOT NULL
        """,
        (cutoff,),
    ).fetchall()
    return [dict(row) for row in rows]


def clear_event_image_path(conn: sqlite3.Connection, event_id: int) -> None:
    """Limpia la referencia a la imagen tras borrarla del filesystem, conservando el evento."""
    conn.execute("UPDATE stock_events SET image_path = NULL WHERE id = ?", (event_id,))
    conn.commit()
