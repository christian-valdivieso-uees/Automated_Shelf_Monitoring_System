"""
init_db.py — Database initialization for the retail shelf monitoring system.
Run once (or idempotently) when deploying to the Raspberry Pi 4.

Requires:
    pip install werkzeug cryptography --break-system-packages
"""

import os
import sqlite3
from werkzeug.security import generate_password_hash
from cryptography.fernet import Fernet

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "shelf_monitoring.db")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "data", "schema.sql")


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_schema():
    data_dir = os.path.dirname(DB_PATH)
    os.makedirs(data_dir, exist_ok=True)

    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema_sql = f.read()

    conn = get_connection()
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()
    print(f"Schema applied at {DB_PATH}")


def create_admin_user(username="admin", password="admin"):
    """Creates the admin user with a hashed password (never plain text)."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
    if cursor.fetchone():
        print(f"User '{username}' already exists, skipping.")
        conn.close()
        return

    password_hash = generate_password_hash(password)
    cursor.execute(
        "INSERT INTO users (username, password_hash) VALUES (?, ?)",
        (username, password_hash),
    )
    conn.commit()
    conn.close()
    print(f"User '{username}' created with hashed password.")
    print("IMPORTANT: change the default password after first login.")


def generate_encryption_key_if_missing():
    """
    Generates the Fernet key used to encrypt the SMTP password.
    Must live in an environment variable (SMTP_ENCRYPTION_KEY), never in the
    database or in the code repository.
    """
    key_path = os.path.join(os.path.dirname(__file__), ".smtp_key")
    if os.path.exists(key_path):
        print(f"SMTP encryption key already exists at {key_path}")
        return

    key = Fernet.generate_key()
    with open(key_path, "wb") as f:
        f.write(key)
    os.chmod(key_path, 0o600)
    print(f"SMTP encryption key generated at {key_path} (permissions 600).")
    print("Export it as an environment variable before starting Flask, e.g.:")
    print(f'  export SMTP_ENCRYPTION_KEY="$(cat {key_path})"')


def create_sample_roi():
    """
    Creates a sample ROI zone covering the full frame, so the system has
    at least one active zone from the first boot. Adjust real coordinates
    from the ROI Configuration screen.
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM roi_zones LIMIT 1")
    if cursor.fetchone():
        print("At least one ROI zone already exists, skipping sample zone.")
        conn.close()
        return

    cursor.execute(
        """
        INSERT INTO roi_zones
            (name, x1, y1, x2, y2, low_stock_threshold, restocked_threshold, confirmation_readings)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("Shelf - Level 1", 0.05, 0.05, 0.95, 0.95, 2, 4, 3),
    )
    conn.commit()
    conn.close()
    print("Sample ROI zone 'Shelf - Level 1' created.")

def create_alert_recipients():
    """
    Creates a sample alert recipient email address.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT INTO alert_recipients
            (email, active)
        VALUES (?, ?)
        """,
        ("christian.valdivieso@uees.edu.ec", 1),
    )

    cursor.execute(
        """
        INSERT INTO alert_recipients
            (email, active)
        VALUES (?, ?)
        """,
        ("dperugachi@onlycontrol.com", 1),
    )
    conn.commit()
    conn.close()

if __name__ == "__main__":
    create_schema()
    create_admin_user()
    generate_encryption_key_if_missing()
    create_sample_roi()
    print("\nInitialization complete.")
